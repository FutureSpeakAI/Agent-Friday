"""
Agent Friday — the Arbiter: runtime owner of the GPUs.

The policy decides what SHOULD be resident. The Arbiter is what makes it true
and keeps it true: it boots the default plan, grants capability leases, and
executes transitions serially with timeouts and rollback.

Why it owns processes rather than asking a daemon nicely (rule R9). Measured on
the reference instance 2026-08-14: loading gemma4:12b (8001 MiB) and then
gemma4:e2b (1763 MiB) against a 9997 MiB budget leaves ONLY the e2b resident.
Ollama evicted the model the policy considers pinned, at every num_ctx tried,
with no model-count limit reached and no report anywhere. A residency layer
cannot delegate placement to a scheduler that makes its own eviction decisions
on different criteria. So:

  * **pinned** seats run as llama-server processes the Arbiter spawns, health-
    checks, and terminates. Nothing else can evict them.
  * **leased** seats may use Ollama, because a lease is precisely the moment
    eviction is wanted.

Concurrency is deliberately absent. Transitions are serial under one lock: a
residency layer that races itself produces exactly the un-debuggable VRAM
exhaustion it exists to prevent.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

from agent_friday.core import runtime_dir
from agent_friday.services import hardware_profile as hwp
from agent_friday.services import residency_catalog as rc
from agent_friday.services import residency_policy as rp

STATE_DEFAULT = "DEFAULT"
STATE_TRANSITIONING = "TRANSITIONING"
STATE_LEASED = "LEASED"
STATE_ROLLING_BACK = "ROLLING_BACK"
STATE_DEGRADED = "DEGRADED"

OLLAMA_URL = "http://localhost:11434"
PORT_BASE = 8090          # one loopback port per pinned llama-server seat

# Transition timeouts are derived from the load-time estimator, never fixed: a
# measured 55 s cold load must not share a budget with a 21 s one.
TIMEOUT_FLOOR_S = 45.0
TIMEOUT_MULTIPLE = 3.0


class TransitionError(RuntimeError):
    pass


# The process-wide Arbiter, set by server._residency_boot. None means the
# residency layer is not governing this process (tests, FRIDAY_NO_ARBITER=1,
# or a failed import) — callers must treat that as "no arbiter", never as an
# error, so nothing depends on residency being present to function.
ARBITER = None


def exclusive_lease() -> dict | None:
    """The lease currently holding the card, or None.

    Acquiring an exclusive lease evicts every seat but the R10-retained ones,
    precisely so an image job gets the whole card. Nothing on the dispatch path
    ever asked whether that lease existed, so the exclusivity was real on the
    way IN and unenforced afterwards: any timer that woke up and called a local
    model loaded ~7 GB straight back onto a card an image job believed it owned.

    Stephen, 2026-08-18: "An hourly heartbeat launched while I was running my
    last image job and the whole computer slowed to a crawl." The heartbeat was
    harmless while background work went to the cloud; moving it to a local
    model (correctly — it was costing a million tokens a day) turned it into a
    GPU competitor, and nothing taught the scheduler about leases.
    """
    try:
        arb = get_arbiter()
    except Exception:
        return None
    if arb is None:
        return None
    lease = getattr(arb, "lease", None)
    return dict(lease) if lease else None


def get_arbiter():
    return ARBITER


def endpoints_path() -> Path:
    return runtime_dir() / "residency" / "endpoints.json"


def _publish_endpoints(procs: dict) -> None:
    """Write the live seat->port map where OTHER processes can read it.

    An in-memory map serves the server and nothing else. Any other process —
    a measurement probe, a worker, a script — has no Arbiter, so it asks the
    Ollama daemon, and with the daemon stopped it raises "Ollama is not
    running" about a model that is loaded and healthy two ports away.
    Measured 2026-08-15: the tool-chain probe scored 0/5 with that error while
    the same seat answered a real turn inside the server in 4.5 s.

    Best-effort and never fatal: a seat that cannot publish its port is still
    a working seat for the process that owns it.
    """
    try:
        p = endpoints_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {"pid": os.getpid(), "updated_at": time.time(),
                "endpoints": {m: "http://127.0.0.1:%d/v1" % port
                              for m, (_proc, port) in procs.items()}}
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except Exception:
        pass


def _published_endpoint(model_id: str) -> str | None:
    """A seat's port from the file, verified live before it is trusted.

    The file can outlive the process that wrote it, so a stale entry must not
    send a caller to a port nobody is listening on. Health-checked first.
    """
    try:
        data = json.loads(endpoints_path().read_text(encoding="utf-8"))
        base = (data.get("endpoints") or {}).get(model_id)
        if not base:
            return None
        port = int(base.rsplit(":", 1)[1].split("/")[0])
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/health" % port, timeout=2) as r:
            return base if r.status == 200 else None
    except Exception:
        return None


def owned_endpoint(model_id: str) -> str | None:
    """The OpenAI-compatible base URL of a seat WE are serving, or None.

    This is the other half of owning the runtime, and forgetting it made things
    briefly worse rather than better. Extracting the brain's GGUF moved it off
    the Ollama daemon into a process the Arbiter runs on a loopback port — at
    which point dispatch, which still asked the daemon on :11434, could not
    find it. Measured 2026-08-15: the brain sat resident and unreachable while
    an ordinary "reply with one word" turn fell through to the cloud and took
    2m05s, against 16s when the same model was served by the daemon.

    A seat that is resident and unreachable is worse than one that is neither.
    """
    if not model_id:
        return None
    arb = ARBITER
    if arb is not None:
        entry = getattr(arb.llama, "procs", {}).get(model_id)
        if entry:
            _proc, port = entry
            return "http://127.0.0.1:%d/v1" % port
    # No Arbiter in THIS process. The seat may still be running in another one.
    return _published_endpoint(model_id)


def owned_provider(model_id: str) -> dict | None:
    """An ad-hoc provider descriptor for a seat we serve ourselves.

    Built at call time from the live port rather than registered in the
    provider registry: the port is assigned when the process starts and a
    stale registry entry would point dispatch at nothing. `classification` and
    the loopback host are what keep `_call_openai`'s call-time local check
    satisfied, so vault-tier material stays permitted — this is on-device by
    construction, not by configuration.
    """
    base = owned_endpoint(model_id)
    if not base:
        return None
    return {
        "name": "arbiter-local",
        "classification": "local",
        "type": "openai-compatible",
        # MUST be "openai-compatible", not "openai".
        #
        # `adapter_of()` reads `adapter` before `type`, and only the adapters in
        # LOCAL_CAPABLE_ADAPTERS earn local classification. "openai" is not one
        # of them, so this single wrong word made `classification_of()` return
        # "cloud" for a model running on 127.0.0.1 in a process we own, and
        # `local_bypass` came out False. Two consequences, both measured
        # 2026-08-16 in Stephen's activity ledger:
        #
        #   * the egress gate SEALED payloads on their way to Friday's own
        #     local seat — vault-tier spans redacted before reaching a model
        #     that is allowed to see them, which defeats the entire reason the
        #     local tier exists;
        #   * the ledger labelled those turns `gemma4:12b · openai`, so a local
        #     model read as a cloud one.
        "adapter": "openai-compatible",
        "base_url": base,
        "models": [model_id],
        "auth": {"type": "none"},
        "network": {"timeout_s": 600},
    }


def _post(url, body, timeout=600):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _get(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


# ─────────────────────────────────────────────────────────────────────────────
#  Backend drivers
# ─────────────────────────────────────────────────────────────────────────────

class OllamaBackend:
    """Leased seats only. Its scheduler evicts, which is fine during a lease."""

    name = "ollama"

    def __init__(self, base_url=OLLAMA_URL):
        self.base_url = base_url

    def resident(self):
        try:
            return {m["name"]: round((m.get("size_vram") or 0) / 1048576)
                    for m in (_get(self.base_url + "/api/ps") or {})
                    .get("models", [])}
        except Exception:
            return {}

    def load(self, model_id, num_ctx, keep_alive="15m", think=False):
        body = {"model": model_id, "prompt": "hi", "stream": False,
                "keep_alive": keep_alive,
                "options": {"num_ctx": num_ctx, "num_predict": 8}}
        if think is False:
            body["think"] = False
        _post(self.base_url + "/api/generate", body)

    def evict(self, model_id):
        try:
            _post(self.base_url + "/api/generate",
                  {"model": model_id, "prompt": "", "keep_alive": 0},
                  timeout=120)
        except Exception:
            pass

    def evict_all(self):
        for name in list(self.resident()):
            self.evict(name)


def ollama_engine_path() -> Path:
    """Ollama's own llama-server, which we run as a PROCESS WE OWN.

    Not the daemon. The daemon is what R9 exists because of; this is the engine
    binary that ships beside it, started and killed by the Arbiter like any
    other seat process. Nothing schedules it but us.
    """
    return (Path.home() / "AppData" / "Local" / "Programs" / "Ollama" /
            "lib" / "ollama" / "llama-server.exe")


# Which engine successfully loaded which model, learned at runtime and kept, so
# a model that upstream cannot parse does not pay a failed load on every boot.
_ENGINE_MEMO: dict = {}

# Models we deliberately leave on the Ollama daemon. EMPTY as of 2026-08-15.
#
# It briefly held gemma4:e2b and e4b. Those models emit tool calls in a channel
# format --  <|tool_call>call:get_weather{city:Oslo}<tool_call|>  -- which only
# Ollama's DAEMON parsed, so serving them as processes we owned cost tool
# calling outright: the model called correctly and `tool_calls` came back None.
#
# services/channel_toolcalls.py now parses that format directly, and the seat
# was verified as OUR process completing a five-call dependent chain with the
# right arguments and the right answer. The exception no longer has a reason to
# exist.
#
# The mechanism stays. The next model with a private wire format belongs here,
# with its measurement, rather than in a silent special case somewhere.
DAEMON_SERVED: dict = {}



# ── Seats that outlived the process that spawned them ───────────────────────
#
# `procs` lives in memory, so a restart forgets every llama-server it started
# while the processes themselves keep running and keep their VRAM. Measured
# 2026-08-18: EIGHT of them, 12:06 to 22:36, one per restart that day, holding
# ~4.1 GB between them, none known to the Arbiter that had just booted. That
# is the same defect as a daemon seating a model behind our back -- the rule
# is that nothing occupies that GPU without the Arbiter knowing, and it does
# not care who started it.
#
# So boot LOOKS first: whatever is serving a model the plan wants is adopted,
# and everything else on our ports is reaped.


class AdoptedProc:
    """A llama-server this process did not spawn but now owns.

    Quacks like `subprocess.Popen` for the four calls `evict` makes, so an
    adopted seat is evicted, leased against and republished exactly like one
    we started. Anything less would make adoption a second class of seat.
    """

    def __init__(self, pid: int):
        self.pid = int(pid)
        self._rc = None

    def _alive(self) -> bool:
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"if (Get-Process -Id {self.pid} -ErrorAction SilentlyContinue)"
                 f" {{'1'}} else {{'0'}}"],
                capture_output=True, text=True, timeout=10)
            return out.stdout.strip().endswith("1")
        except Exception:
            return False

    def poll(self):
        if self._rc is not None:
            return self._rc
        return None if self._alive() else 0

    def terminate(self):
        try:
            subprocess.run(["taskkill", "/PID", str(self.pid)],
                           capture_output=True, timeout=20)
        except Exception:
            pass

    def kill(self):
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(self.pid)],
                           capture_output=True, timeout=20)
            self._rc = 0
        except Exception:
            pass

    def wait(self, timeout=None):
        deadline = time.time() + (timeout or 30)
        while time.time() < deadline:
            if not self._alive():
                self._rc = 0
                return 0
            time.sleep(0.5)
        raise TimeoutError(f"pid {self.pid} did not exit")


def _listening_ports() -> dict:
    """`port -> owning pid` for everything listening, in one call."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-NetTCPConnection -State Listen | "
             "Select-Object -Property LocalPort,OwningProcess | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=30)
        rows = json.loads(out.stdout or "[]")
        if isinstance(rows, dict):
            rows = [rows]
        return {int(r["LocalPort"]): int(r["OwningProcess"]) for r in rows}
    except Exception:
        return {}


def _llama_server_pids() -> set:
    """llama-server processes serving OUR models. Not the daemon's.

    The discriminator is the MODEL PATH, not the binary. Ollama's daemon
    spawns the same `llama-server.exe` for its own models, and the Arbiter
    deliberately uses Ollama's binary for the e-series that upstream cannot
    parse — so "is this Ollama's exe" answers the wrong question in both
    directions.

    Ours load from `~/.friday/runtime/models`; the daemon's load from
    `~/.ollama/models/blobs`. Measured 2026-08-18, the first version of this
    matched on the binary name and reaped Ollama's live runner (pid 17104,
    port 56662) along with the real orphans, forcing the daemon to reload a
    model mid-session. Killing another scheduler's process is exactly the
    discourtesy this module exists to stop, and it does not stop being one
    when we are the ones doing it.
    """
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='llama-server.exe'\" | "
             "Where-Object { $_.CommandLine -like '*.friday*runtime*models*' } | "
             "Select-Object -ExpandProperty ProcessId"],
            capture_output=True, text=True, timeout=30)
        return {int(x) for x in out.stdout.split() if x.strip().isdigit()}
    except Exception:
        return set()


def _model_on_port(port: int) -> str | None:
    """What a llama-server is actually holding. The server, not a record."""
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/v1/models" % port, timeout=2) as r:
            data = json.loads(r.read().decode()) or {}
        rows = data.get("models") or data.get("data") or []
        for m in rows:
            mid = m.get("id") or m.get("name")
            if mid:
                return str(mid)
    except Exception:
        pass
    return None


def _seat_num_ctx(pid: int) -> int | None:
    """The `-c` a running llama-server was started with, or None."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}' | "
             f"Select-Object -ExpandProperty CommandLine"],
            capture_output=True, text=True, timeout=20)
        parts = (out.stdout or "").split()
        for i, tok in enumerate(parts):
            if tok == "-c" and i + 1 < len(parts) and parts[i + 1].isdigit():
                return int(parts[i + 1])
    except Exception:
        pass
    return None


def survey_live_seats(port_lo: int = PORT_BASE, port_hi: int = PORT_BASE + 40) -> dict:
    """`model_id -> (pid, port)` for llama-servers answering on our ports."""
    listening = _listening_ports()
    found = {}
    for port in range(port_lo, port_hi + 1):
        if port not in listening:
            continue
        model = _model_on_port(port)
        if model:
            found[model] = (listening[port], port)
    return found


class LlamaServerBackend:
    """Pinned seats. One process per seat, owned end to end (R9).

    Two engines, and the reason is measured rather than defensive. Upstream
    llama.cpp (build 10415) loads `gemma4:12b` from the extracted GGUF fine and
    refuses `gemma4:e2b` from a file that is provably intact:

        done_getting_tensors: wrong number of tensors; expected 2012, got 601

    That error was read for a day as "Ollama shards its models and llama.cpp
    cannot reassemble them". It is not. The manifest has exactly one model
    layer, the extracted file passes the GGUF magic check, and **Ollama's own
    engine binary loads that same file and generates from it** — verified
    2026-08-15, "ready." in 0.90 s. The gemma4 e-series tensor layout simply
    is not what upstream's gemma3n reader expects.

    So we try upstream first and fall back to the engine that ships with
    Ollama. Either way the process is ours: we spawn it, health-check it, and
    kill it. The daemon is not in the path.
    """

    name = "llama-server"

    def __init__(self, binary: Path | None = None, fallback: Path | None = None):
        self.binary = binary or (runtime_dir() / "llama.cpp" /
                                 "llama-server.exe")
        self.fallback = fallback if fallback is not None else \
            ollama_engine_path()
        self.procs: dict = {}          # model_id -> (Popen, port)

    def resident(self):
        return {m: 0 for m in self.procs}

    def engines_for(self, model_id):
        """Engines to try, best first, with anything already learned first."""
        known = _ENGINE_MEMO.get(model_id)
        order = [self.binary]
        if self.fallback and Path(self.fallback).exists():
            order.append(Path(self.fallback))
        if known:
            order = [Path(known)] + [b for b in order if str(b) != str(known)]
        return [b for b in order if Path(b).exists()]

    def load(self, model_id, num_ctx, *, gguf_path, port,
             n_cpu_moe=None, timeout=300):
        engines = self.engines_for(model_id)
        if not engines:
            raise TransitionError("no llama-server binary found (looked at %s "
                                  "and %s)" % (self.binary, self.fallback))
        last = None
        for i, binary in enumerate(engines):
            try:
                took = self._spawn(binary, model_id, num_ctx,
                                   gguf_path=gguf_path, port=port + i,
                                   n_cpu_moe=n_cpu_moe, timeout=timeout)
                _ENGINE_MEMO[model_id] = str(binary)
                return took
            except TransitionError as e:
                last = e
        raise TransitionError("no engine could load %s: %s" % (model_id, last))

    # A seat's window is the Arbiter's decision, and it is bounded.
    #
    # MEASURED on this card: the 12b at 131072 took 11,351 MiB of a 12,282 MiB
    # GPU on ONE request. At 262144 -- the model's architectural maximum, which
    # is what `models.json` reports and what a seat gets when nothing clamps it
    # -- boot alone left 448 MiB free, under the 1024 MiB display reserve, in
    # the exact state that has already dropped Stephen's second monitor twice
    # today. The daemon was blamed for it the first time.
    #
    # MEASURED ON THIS CARD, 2026-08-19, and the answer was not the expected
    # one. The roles contract (docs/contracts/roles-and-model-identity.md
    # 6a) sets TOOL_SEAT_NUM_CTX to 65,536 on the reasoning that over-
    # reserving is nearly free because the KV curve is flat -- about 32 MiB --
    # while under-reserving truncates silently. On gemma4:12b on this RTX 4070
    # the curve is not flat:
    #
    #     32,768 -> 1,936 MiB free of 12,282
    #     65,536 ->   551 MiB free      <-- under the 1024 MiB display reserve
    #
    # A 1,385 MiB difference, not 32. The compute buffer scales with context
    # and dwarfs the KV cache at long windows, which is the same effect
    # already recorded on the batch-size flag below. 65,536 costs Stephen his
    # second monitor on this hardware.
    #
    # The silent-truncation worry also does not apply to these seats:
    # llama-server REJECTS an over-length request with a 400 naming the token
    # count -- observed, "request (47448 tokens) exceeds the available context
    # size" -- rather than dropping the oldest spans. Loud beats quiet, and
    # the fallback is reported to the caller.
    #
    # So: 32,768 here, and the contract's 65,536 wants re-measuring on this
    # card before it is applied to it.
    #
    # Superseded reasoning, kept because the shape of the trade is right:
    # (docs/contracts/roles-and-model-identity.md §6a, branch
    # model-suite-determination) showed 32,768 to be the WRONG side of this
    # trade: a real turn does not fit in it, and the overflow is silent --
    # the oldest spans fall out the front and the model answers from a
    # truncated view without anyone being told. Over-reserving is nearly free
    # on this model family because the KV curve is flat; under-reserving
    # corrupts the answer quietly. Between a cost measured in tens of MiB and
    # a failure nobody can see, take the MiB.
    #
    # The ceiling stays, because the thing it was built to stop is real: this
    # seat spawned at its architectural 262,144 left 448 MiB of 12,282 and
    # took a monitor off the desktop. Measured on this card at 65,536 before
    # committing to it -- see the report; a claim about a flat curve is not
    # the same as this card.
    MAX_SEAT_NUM_CTX = 32768

    def _spawn(self, binary, model_id, num_ctx, *, gguf_path, port,
               n_cpu_moe=None, timeout=300):
        try:
            asked = int(num_ctx or 0)
        except Exception:
            asked = 0
        if asked > self.MAX_SEAT_NUM_CTX:
            print(f"  [arbiter] {model_id}: capping context {asked:,} -> "
                  f"{self.MAX_SEAT_NUM_CTX:,} to keep the display reserve")
            num_ctx = self.MAX_SEAT_NUM_CTX
        cmd = [str(binary), "-m", str(gguf_path), "--alias", model_id,
               "--host", "127.0.0.1", "--port", str(port),
               "-ngl", "99", "--flash-attn", "on", "-c", str(num_ctx),
               "--jinja", "--no-webui",
               # Batch size caps the COMPUTE buffer, which scales with context
               # and dwarfs the KV cache at long windows. Measured: the 12b at
               # 131072 took 11351 MiB of GPU at the default batch against 7813
               # under Ollama, which runs -b 512 -ub 512. The extra ~3.5 GB is
               # the compute buffer, not the model, and it is the difference
               # between the pinned pair fitting and not.
               "-b", "512", "-ub", "512"]
        # A seat with no chat template silently falls back to ChatML, which
        # leaks `<|im_end|>` into replies and — the part that matters — hands
        # the seat a template with NO TOOL DEFINITIONS. gemma4:e2b and e4b ship
        # without an embedded template; gguf_extract borrows the 12b's (same
        # architecture, same tokenizer) and writes it beside the weights.
        # Without this the sidekick would look like a model that cannot call
        # tools, which is the misdiagnosis this codebase has already made twice.
        try:
            from agent_friday.services import gguf_extract as _gx
            tmpl = _gx.chat_template_path(model_id)
            if tmpl.exists():
                cmd += ["--chat-template-file", str(tmpl)]
        except Exception:
            pass
        if n_cpu_moe is not None:
            cmd += ["--n-cpu-moe", str(n_cpu_moe)]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                cwd=str(Path(binary).parent))
        t0 = time.time()
        while time.time() - t0 < timeout:
            if proc.poll() is not None:
                raise TransitionError(
                    "%s exited %s loading %s"
                    % (Path(binary).parent.name, proc.returncode, model_id))
            try:
                with urllib.request.urlopen(
                        "http://127.0.0.1:%d/health" % port, timeout=3) as r:
                    if r.status == 200:
                        self.procs[model_id] = (proc, port)
                        _publish_endpoints(self.procs)
                        return round(time.time() - t0, 2)
            except Exception:
                time.sleep(1.5)
        proc.terminate()
        raise TransitionError("%s never became ready in %ss for %s"
                              % (Path(binary).name, timeout, model_id))

    def evict(self, model_id):
        entry = self.procs.pop(model_id, None)
        _publish_endpoints(self.procs)
        if not entry:
            return
        proc, _ = entry
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=60)
            except Exception:
                proc.kill()

    def evict_all(self):
        for m in list(self.procs):
            self.evict(m)

    def adopt_or_reap(self, wanted: set) -> dict:
        """Take ownership of what the plan wants; kill what it does not.

        Called at boot, before anything is loaded. `wanted` is the set of
        model ids the computed plan intends to keep pinned.

        Adoption is not politeness — it is the only way the invariant holds
        across a restart. An orphan we cannot see is an orphan we cannot
        evict when a lease needs the card, and it still costs its VRAM.
        Reaping the rest is what stops one leak per restart.
        """
        report = {"adopted": [], "reaped": []}
        try:
            live = survey_live_seats()
        except Exception as e:
            print(f"  [arbiter] could not survey live seats: {e}")
            return report

        # Seats WE already hold, whether or not they are listening yet. A
        # llama-server loading a 262k-context model takes tens of seconds and
        # answers nothing while it does; without this it is invisible to the
        # survey, unclaimed, and reaped by its own owner mid-load.
        claimed = set()
        for _m, _entry in list(self.procs.items()):
            try:
                _pid = getattr(_entry[0], "pid", None)
                if _pid:
                    claimed.add(int(_pid))
            except Exception:
                pass

        for model_id, (pid, port) in live.items():
            # Adoption inherits whatever the previous process decided, and a
            # seat started at 262144 keeps that window forever if we simply
            # take it. Boot then reports a healthy adoption while the card sits
            # at 448 MiB free. A seat that does not conform to policy is not
            # adopted; it is reaped, and the plan reloads it inside the cap.
            over = _seat_num_ctx(pid)
            if over and over > self.MAX_SEAT_NUM_CTX:
                print(f"  [arbiter] not adopting {model_id} on :{port}: it was "
                      f"started at {over:,} context, over the "
                      f"{self.MAX_SEAT_NUM_CTX:,} cap - reloading it instead")
                continue

            if model_id in wanted and model_id not in self.procs:
                self.procs[model_id] = (AdoptedProc(pid), port)
                claimed.add(pid)
                report["adopted"].append(f"{model_id} on :{port} (pid {pid})")
            elif model_id in self.procs:
                claimed.add(pid)

        for pid in (_llama_server_pids() - claimed):
            try:
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True, timeout=20)
                report["reaped"].append(pid)
            except Exception:
                pass

        if report["adopted"] or report["reaped"]:
            _publish_endpoints(self.procs)
        for line in report["adopted"]:
            print(f"  [arbiter] adopted {line}")
        if report["reaped"]:
            print(f"  [arbiter] reaped {len(report['reaped'])} orphaned "
                  f"llama-server process(es): {sorted(report['reaped'])}")
        return report


class ComfyUIBackend:
    """Image generation. Started for a lease, stopped on release."""

    name = "comfyui"

    def __init__(self, root: Path | None = None, port=8188):
        self.root = root or (runtime_dir() / "ComfyUI")
        self.venv = runtime_dir() / "venv-comfy" / "Scripts" / "python.exe"
        self.port = port
        self.proc = None

    def running(self):
        try:
            urllib.request.urlopen(
                "http://127.0.0.1:%d/system_stats" % self.port, timeout=2)
            return True
        except Exception:
            return False

    def start(self, timeout=300):
        if self.running():
            return 0.0
        self.proc = subprocess.Popen(
            [str(self.venv), "main.py", "--port", str(self.port)],
            cwd=str(self.root), stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.proc.poll() is not None:
                raise TransitionError("ComfyUI exited %s" % self.proc.returncode)
            if self.running():
                return round(time.time() - t0, 2)
            time.sleep(2)
        self.stop()
        raise TransitionError("ComfyUI never became ready in %ss" % timeout)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=60)
            except Exception:
                self.proc.kill()
        self.proc = None


# ─────────────────────────────────────────────────────────────────────────────
#  The Arbiter
# ─────────────────────────────────────────────────────────────────────────────

class Arbiter:
    def __init__(self, profile=None, entries=None, *, ollama=None,
                 llama=None, comfy=None, gguf_paths=None):
        self.profile = profile or hwp.get()
        self.entries = entries if entries is not None else \
            rc.installed_entries(self.profile)
        self.ollama = ollama or OllamaBackend()
        self.llama = llama or LlamaServerBackend()
        self.comfy = comfy or ComfyUIBackend()
        # Default to the extracted-GGUF registry rather than an empty dict.
        # Left empty, every pinned seat silently took the degraded-pin path and
        # the residency layer's central rule (R9) was unenforced everywhere
        # while looking configured — the caller has to remember to pass the one
        # thing without which nothing works.
        self.gguf_paths = dict(gguf_paths if gguf_paths is not None
                               else rc.gguf_models())
        self.state = STATE_DEFAULT
        self.plan = None
        self.lease = None
        self.transitions = []           # audit trail, with timings
        self._lock = threading.Lock()

    # ── planning ────────────────────────────────────────────────────────────

    def compute_plan(self, overrides=None):
        """Plan against the LIVE prompt overhead and the LIVE display draw.

        The system prompt is assembled from the vault, self-knowledge and
        persona, and the tool registry grows when someone adds a tool. Both are
        subtracted from every seat's usable window, so a plan built on a stale
        overhead figure hands out windows that are the wrong size in a way
        nothing would report.

        The desktop's VRAM draw is the same kind of moving number and was being
        treated as a constant. It is sampled here for the same reason: a plan
        is only as good as the machine it was planned against, and on
        2026-08-17 the gap between a 542 MiB cached floor and a 2,778 MiB
        compositor took a monitor off the desktop. Sampling stays out here in
        the arbiter so `rp.plan` remains a pure function of the profile and its
        golden fixtures keep meaning something.
        """
        from agent_friday.services import context_budget
        hwp.refresh_display_reserve(self.profile)
        self.plan = rp.plan(self.profile, self.entries, overrides,
                            overhead_tokens=context_budget.overhead_tokens())
        return self.plan

    def timeout_for(self, entry):
        est = (entry or {}).get("est_load_s")
        if not est:
            return TIMEOUT_FLOOR_S * TIMEOUT_MULTIPLE
        return max(TIMEOUT_FLOOR_S, est * TIMEOUT_MULTIPLE)

    # ── headroom, before anything is loaded ─────────────────────────────────

    def admit(self, entry, *, current_host_mib=0):
        """Both refusals, checked before a load is attempted (R2 + R8).

        The disk charge for a LOAD is the host-RAM portion, not the artifact
        size. The artifact is already on disk — loading it does not consume
        the disk again. What it consumes is pagefile: Phase A A7 measured free
        disk falling 27.7 -> 7.0 GB while a 29 GB model was resident, and
        recovering when it unloaded. So the pagefile grows with the RESIDENT
        SET, and charging the artifact instead both refuses loads that are fine
        and misses the growth that is not.

        `check_disk_headroom` with the artifact size remains the right check
        for a DOWNLOAD, which is a different question.
        """
        total = 0
        gpu_portion = 0
        for m in (entry.get("measured") or []):
            if (m.get("total_mib") or 0) > total:
                total = m["total_mib"]
                gpu_portion = m.get("vram_mib") or 0
        ram = rp.check_ram_headroom(self.profile, total, current_host_mib)
        if not ram["ok"]:
            return ram
        host_mib = max(0, total - gpu_portion)
        disk = rp.check_disk_headroom(self.profile, host_mib)
        if not disk["ok"]:
            return disk
        return {"ok": True, "rule_id": None, "explanation": ""}

    # ── boot ────────────────────────────────────────────────────────────────

    def authorized_daemon_models(self) -> set:
        """Models the Arbiter has actually asked the daemon to hold."""
        out = set()
        try:
            for seat in (self.plan.get("seats") or {}).values():
                if not isinstance(seat, dict):
                    continue
                if str(seat.get("backend") or seat.get("device") or "").find("ollama") >= 0 \
                        or seat.get("served_by") == "ollama":
                    if seat.get("model_id"):
                        out.add(seat["model_id"])
        except Exception:
            pass
        try:
            for m in (getattr(self, "_daemon_leases", None) or set()):
                out.add(m)
        except Exception:
            pass
        return out

    def reconcile_daemon(self, evict: bool = True) -> dict:
        """Nothing occupies that GPU without the Arbiter knowing.

        The rule does not care who started it. Ollama's daemon has its own
        scheduler, and on 2026-08-18 it seated a model at 262k context behind
        the Arbiter's back, took the card, and dropped Stephen's second
        monitor. The Arbiter had no idea, because it had never once looked.

        HONEST LIMIT, and it matters: Ollama exposes observation (`/api/ps`)
        and eviction (`keep_alive: 0`), but NO admission hook. Friday cannot
        stop `ollama run` in a terminal, or the tray app, from loading
        something -- it can only notice and reclaim. `OLLAMA_MAX_LOADED_MODELS`
        and `OLLAMA_KEEP_ALIVE` are read by the daemon at START, so enforcing
        them means owning daemon startup, which we do not. This is
        detect-and-reclaim, not prevention, and it should not be described as
        more than that.
        """
        report = {"authorized": [], "unexpected": [], "evicted": []}
        try:
            resident = self.ollama.resident() or {}
        except Exception as e:
            print(f"  [arbiter] could not read daemon residency: {e}")
            return report
        allowed = self.authorized_daemon_models()
        for model, mib in resident.items():
            if model in allowed:
                report["authorized"].append(model)
                continue
            report["unexpected"].append((model, mib))
            print(f"  [arbiter] daemon is holding {model!r} ({mib} MiB) that "
                  f"nothing here asked for")
            if evict:
                try:
                    self.ollama.evict(model)
                    report["evicted"].append(model)
                except Exception as e:
                    print(f"  [arbiter] could not evict {model!r}: {e}")
        if report["evicted"]:
            print(f"  [arbiter] reclaimed the card from "
                  f"{len(report['evicted'])} unauthorised daemon model(s)")
        return report

    def boot(self, *, measure_baseline=True):
        """Bring the machine to the default plan.

        Reconciles rather than reloading: a seat that is already resident and
        matches the plan is adopted, which on this host avoids a needless 20.5 s
        load every restart.
        """
        with self._lock:
            # BEFORE anything else. `evict_all` walks `self.procs`, which is
            # empty in a fresh process, so seats left running by the previous
            # one survived every restart -- including the baseline wipe, which
            # then measured an "idle" floor with several GB of forgotten
            # models still resident. Look at the machine first.
            self.compute_plan()
            try:
                _wanted = {s.get("model_id") for s in
                           (self.plan.get("seats") or {}).values()
                           if isinstance(s, dict) and s.get("status") == "pinned"}
                self.llama.adopt_or_reap({m for m in _wanted if m})
            except Exception as e:
                print(f"  [arbiter] adopt/reap failed (continuing): {e}")
            try:
                self.reconcile_daemon(evict=True)
            except Exception as e:
                print(f"  [arbiter] daemon reconciliation failed: {e}")

            if measure_baseline:
                # The one moment we can honestly measure the idle GPU floor.
                self.ollama.evict_all()
                self.llama.evict_all()
                time.sleep(2)
                hwp.refresh_baseline(self.profile, assert_idle=True)
            self.compute_plan()
            self.state = STATE_TRANSITIONING
            t0 = time.time()
            try:
                already = self.ollama.resident()
                for role in ("interactive_brain", "sidekick"):
                    seat = (self.plan["seats"] or {}).get(role)
                    if not seat or seat.get("status") != "pinned":
                        continue
                    if seat["model_id"] in already:
                        self._record("adopt", role, seat["model_id"], 0.0)
                        continue
                    self._load_pinned(seat, role)
                emb = (self.plan["seats"] or {}).get("embedder")
                if emb and str(emb.get("device", "")).startswith("gpu"):
                    self._load_leased(emb, "embedder")
                self.state = STATE_DEFAULT
            except Exception as e:
                self.state = STATE_ROLLING_BACK
                self._rollback()
                self.state = STATE_DEGRADED
                raise TransitionError("boot failed: %s" % e)
            self._record("boot", "plan", None, round(time.time() - t0, 2))
            return self.plan

    # ── leases ──────────────────────────────────────────────────────────────

    def grant(self, kind, *, role=None, ttl_s=300):
        """Grant a capability lease, executing its transition serially."""
        with self._lock:
            if self.lease is not None:
                return {"ok": False, "error": "lease %s already held"
                        % self.lease["kind"]}
            if self.state not in (STATE_DEFAULT,):
                return {"ok": False, "error": "arbiter is %s" % self.state}
            # DISPLAY HEADROOM, checked against the LIVE card before committing.
            #
            # 2026-08-17: the plan did its arithmetic against a floor measured
            # once at Arbiter boot (542 MiB here, against a documented ~1 GB
            # Windows compositor cost) and nothing ever asked the GPU what was
            # actually free. The card reached 322 MiB free after a heartbeat
            # loaded a 9.6 GB model, and four minutes later an indirect display
            # driver crashed and Windows lost Stephen's second monitor.
            #
            # A refusal here is a sentence he can read. A driver reset is him
            # waving a mouse at a dead screen wondering what happened.
            try:
                from agent_friday.services.hardware_profile import vram_headroom
                _hd = vram_headroom()
                if _hd.get("total_mib") and not _hd.get("ok"):
                    self.state = STATE_DEFAULT
                    return {"ok": False, "error": (
                        "not enough VRAM left for the desktop: %d MiB free "
                        "against a %d MiB display reserve (short by %d). "
                        "Loading now risks the display driver, which is how "
                        "the second monitor was lost on 2026-08-17. Free the "
                        "card or close a display-heavy app first."
                        % (_hd.get("free_mib", 0),
                           _hd.get("display_reserve_mib", 0),
                           _hd.get("shortfall_mib", 0))),
                        "refused": {"rule_id": "R-DISPLAY-RESERVE"}}
            except Exception:
                pass
            self.state = STATE_TRANSITIONING
            t0 = time.time()
            try:
                if kind == "heavy_turn":
                    seat = self.plan["seats"].get("heavy_hitter")
                    if seat is None:
                        refusal = [r for r in self.plan["refusals"]
                                   if r["role"] == "heavy_hitter"]
                        self.state = STATE_DEFAULT
                        return {"ok": False,
                                "error": refusal[0]["explanation"]
                                if refusal else "no heavy seat"}
                    entry = self._entry(seat["model_id"])
                    adm = self.admit(entry)
                    if not adm["ok"]:
                        self.state = STATE_DEFAULT
                        return {"ok": False, "refused": adm,
                                "error": adm["explanation"]}
                    displaced = self._evict_pinned()
                    self._load_leased(seat, "heavy_hitter")
                    self.lease = {"kind": kind, "role": "heavy_hitter",
                                  "model_id": seat["model_id"],
                                  "displaced": displaced,
                                  "expires_at": time.time() + ttl_s}
                elif kind == "image_job":
                    displaced = self._evict_pinned()
                    self._evict_all_but_retained()
                    took = self.comfy.start()
                    self._record("start", "image", "comfyui", took)
                    self.lease = {"kind": kind, "role": "image",
                                  "model_id": "z-image-turbo-fp8",
                                  "displaced": displaced,
                                  "expires_at": time.time() + ttl_s}
                else:
                    self.state = STATE_DEFAULT
                    return {"ok": False, "error": "unknown lease %r" % kind}
                self.state = STATE_LEASED
                el = round(time.time() - t0, 2)
                self._record("grant", kind, self.lease.get("model_id"), el)
                return {"ok": True, "lease": dict(self.lease),
                        "transition_s": el}
            except Exception as e:
                self.state = STATE_ROLLING_BACK
                self._rollback()
                return {"ok": False, "error": str(e), "rolled_back": True}

    def release(self):
        """Give the GPU back and restore the default plan."""
        with self._lock:
            if self.lease is None:
                return {"ok": True, "note": "no lease held"}
            kind = self.lease["kind"]
            self.state = STATE_TRANSITIONING
            t0 = time.time()
            try:
                if kind == "heavy_turn":
                    self.ollama.evict(self.lease["model_id"])
                    self.llama.evict(self.lease["model_id"])
                elif kind == "image_job":
                    self.comfy.stop()
                self._restore_pinned(self.lease.get("displaced"))
                self.lease = None
                self.state = STATE_DEFAULT
                el = round(time.time() - t0, 2)
                self._record("release", kind, None, el)
                return {"ok": True, "transition_s": el}
            except Exception as e:
                self.state = STATE_DEGRADED
                return {"ok": False, "error": str(e)}

    def expire_if_due(self):
        """A crashed lease holder must not strand the GPU."""
        if self.lease and time.time() > self.lease.get("expires_at", 0):
            return self.release()
        return {"ok": True, "note": "not due"}

    # ── internals ───────────────────────────────────────────────────────────

    def _entry(self, model_id):
        for e in self.entries:
            if e["model_id"] == model_id:
                return e
        return {"model_id": model_id}

    def _record(self, action, role, model_id, seconds):
        self.transitions.append({"action": action, "role": role,
                                 "model_id": model_id, "seconds": seconds,
                                 "state": self.state})

    def _measure_resident(self, seat, cold_load_s=None):
        """Record what a seat ACTUALLY cost at the context it was given.

        Placement at an unmeasured context is an estimate — extrapolated along
        the model's KV slope, or in the worst case a lower bound from a smaller
        context. Loading the seat is the moment that estimate can be replaced
        with a fact, and the next plan then works from evidence. Without this
        the ladder would keep re-deciding from the same two seed rows forever.
        """
        model_id, num_ctx = seat.get("model_id"), seat.get("num_ctx")
        if not model_id or not num_ctx:
            return
        try:
            for m in (_get(self.ollama.base_url + "/api/ps") or {}) \
                    .get("models", []):
                if m.get("name") != model_id:
                    continue
                vram = round((m.get("size_vram") or 0) / 1048576)
                total = round((m.get("size") or 0) / 1048576)
                if not total:
                    return
                row = {"num_ctx": num_ctx, "vram_mib": vram,
                       "total_mib": total,
                       "pct_gpu": round(100.0 * vram / total) if total else 0,
                       "backend": rc.BACKEND_OLLAMA}
                if cold_load_s:
                    row["cold_load_s"] = round(cold_load_s, 2)
                rc.record_measurement(
                    model_id, rc.profile_fingerprint(self.profile), row)
                return
        except Exception:
            pass

    def _load_pinned(self, seat, role):
        """R9: a pinned seat is a process we own, not a request to a daemon."""
        entry = self._entry(seat["model_id"])
        gguf = self.gguf_paths.get(seat["model_id"])
        t0 = time.time()
        why = DAEMON_SERVED.get(seat["model_id"])
        if why:
            # A deliberate exception, not a failure. It still reports as an
            # unenforced pin below, because that is exactly what it is — and a
            # seat we chose not to own must look the same in the status output
            # as one we failed to own, or the honest report becomes a
            # comfortable one.
            gguf = None
        if gguf:
            port = PORT_BASE + len(self.llama.procs)
            try:
                took = self.llama.load(seat["model_id"], seat["num_ctx"],
                                       gguf_path=gguf, port=port,
                                       timeout=self.timeout_for(entry))
                self._record("load-pinned", role, seat["model_id"], took)
                return took
            except TransitionError as e:
                # A backend that cannot hold this model is a DEGRADED PIN, not
                # a failed boot. Measured case: Ollama stores gemma4:e2b across
                # several blobs, so a single blob path gives llama-server
                # "wrong number of tensors; expected 2012, got 601". Taking the
                # whole boot down for that would leave the machine with no
                # seats at all, which is strictly worse than one unenforced pin.
                why = str(e)
        else:
            why = "no GGUF mapped"

        self.ollama.load(seat["model_id"], seat["num_ctx"])
        took = round(time.time() - t0, 2)
        self._measure_resident(seat, took)
        seat["pin_unenforced"] = (
            "%s for %s, so this seat runs on Ollama and MAY BE EVICTED by the "
            "daemon's own scheduler (R9)" % (why, seat["model_id"]))
        self._record("load-pinned-degraded", role, seat["model_id"], took)
        return took

    def _load_leased(self, seat, role):
        """Leased seats may use either backend; a lease is when eviction is
        wanted, so Ollama's own scheduler is harmless here.

        The 26b runs on llama-server by measurement: within the R3 VRAM ceiling
        the two backends tie (27.95 Ollama vs 27.80 llama-server at
        --n-cpu-moe 20), and re-pulling Ollama's 17 GB copy would itself breach
        R8. llama-server also takes explicit --n-cpu-moe and -c, which is the
        control the offload placement needs.
        """
        entry = self._entry(seat["model_id"])
        gguf = self.gguf_paths.get(seat["model_id"])
        t0 = time.time()
        if gguf:
            port = PORT_BASE + 20 + len(self.llama.procs)
            took = self.llama.load(
                seat["model_id"], seat["num_ctx"] or 2048, gguf_path=gguf,
                port=port,
                n_cpu_moe=(seat.get("offload") or {}).get("n_cpu_moe", 20)
                if seat.get("is_moe") else None,
                timeout=self.timeout_for(entry))
        else:
            self.ollama.load(seat["model_id"], seat["num_ctx"] or 2048)
            took = round(time.time() - t0, 2)
            self._measure_resident(seat, took)
        self._record("load-leased", role, seat["model_id"], took)
        return took

    def _evict_pinned(self):
        """Stand down the seats a lease may take. R10 says which it may not.

        The sidekick stays. Before 2026-08-15 a lease evicted the whole pinned
        set, so asking for depth made Friday mute for the duration — from the
        outside the machine looked hung rather than busy. Stephen's call:
        "keep e2b awake so Friday is always alive."
        """
        displaced = []
        for role in ("interactive_brain", "sidekick", "embedder"):
            if role in rp.RETAINED_THROUGH_LEASE:
                continue
            seat = (self.plan["seats"] or {}).get(role)
            if not seat or not str(seat.get("device", "")).startswith("gpu"):
                continue
            t0 = time.time()
            self.llama.evict(seat["model_id"])
            self.ollama.evict(seat["model_id"])
            displaced.append(role)
            self._record("evict", role, seat["model_id"],
                         round(time.time() - t0, 2))
        return displaced

    def _restore_pinned(self, roles=None):
        """Reload what a lease actually displaced.

        `roles` comes from the lease's own record, so a retained seat is not
        needlessly reloaded — reloading the sidekick would evict it first and
        briefly produce exactly the silence R10 exists to prevent.
        """
        for role in (roles if roles is not None
                     else ("interactive_brain", "sidekick")):
            if role in rp.RETAINED_THROUGH_LEASE:
                continue
            seat = (self.plan["seats"] or {}).get(role)
            if seat and seat.get("status") == "pinned":
                self._load_pinned(seat, role)

    def _retained_models(self):
        seats = self.plan["seats"] or {}
        return {(seats.get(r) or {}).get("model_id")
                for r in rp.RETAINED_THROUGH_LEASE if seats.get(r)}

    def _evict_all_but_retained(self):
        """Clear the card for an exclusive lease, except what R10 keeps.

        `evict_all()` on either backend would take the sidekick with everything
        else. Evicting and then reloading it would cost a cold start and open
        exactly the window of silence R10 exists to close, so the retained
        seats are named and skipped instead.
        """
        keep = self._retained_models()
        for name in list(self.ollama.resident()):
            if name not in keep:
                self.ollama.evict(name)
        for name in list(self.llama.procs):
            if name not in keep:
                self.llama.evict(name)

    def _rollback(self):
        try:
            self.comfy.stop()
            self._evict_all_but_retained()
            self._restore_pinned()
            self.lease = None
        except Exception:
            pass

    # ── introspection ───────────────────────────────────────────────────────

    def status(self):
        return {
            "state": self.state,
            "lease": dict(self.lease) if self.lease else None,
            "plan_seats": {r: (s or {}).get("model_id")
                           for r, s in (self.plan or {}).get("seats", {}).items()},
            "resident_ollama": self.ollama.resident(),
            "resident_llama_server": list(self.llama.procs),
            "transitions": list(self.transitions),
        }

    def shutdown(self):
        with self._lock:
            self.comfy.stop()
            self.llama.evict_all()
            self.state = STATE_DEFAULT
