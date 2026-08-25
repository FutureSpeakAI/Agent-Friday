"""
services/higgsfield_generate.py — dispatch for Higgsfield-seated creative models.

The picker must never offer what nothing can call. services/higgsfield_catalog
enumerates ~120 Higgsfield models into the creative picker; this module is the
other half of that promise — the path that actually runs one.

It routes through the SAME MCP connector the catalogue is enumerated from
(~/.friday/mcp_servers.json -> servers.higgsfield), rather than a second HTTP
client with its own auth. One surface for listing and for calling means the
picker cannot drift from what is runnable: if enumeration works, dispatch
works, and if the connector is down both fail together and say so.

Lifecycle: submit -> poll to terminal -> pull the bytes to disk. The pull is
part of the job, not an afterthought. Higgsfield deletes output after *at
least* seven days, so a generation that is not on disk is a generation that
will disappear; services/creative_store.download_output verifies the bytes and
writes provenance.

Cost: every submit is preflighted with `get_cost` and the credits are returned
on the result, because the spread across this catalogue is roughly 150x
(0.15 credits for z_image, 22.5 for seedance_2_0). A caller that shows the
number before spending it is the point of surfacing these models at all.
"""
from __future__ import annotations

import json
import logging
import re
import time

_log = logging.getLogger("friday.higgsfield_generate")

MCP_SERVER = "higgsfield"

#: kind -> MCP tool. `3d` is included because the account really does carry 17
#: 3D models; the Higgsfield CLI's `model list` has no 3D type, which is
#: exactly why this maps from the MCP catalogue and not from that list.
_TOOLS = {"image": "generate_image", "video": "generate_video",
          "audio": "generate_audio", "3d": "generate_3d"}

#: Terminal job states, per services/creative_store.TERMINAL.
_TERMINAL = ("completed", "failed", "nsfw", "canceled")

#: Poll budget. `jobs_wait` long-polls up to 15s per call, so this is the
#: number of consecutive waits before giving up and reporting the job id so
#: the output can still be rescued by hand.
_MAX_WAITS = 40

class EgressBlocked(Exception):
    """The egress gate refused an argument before it left the machine.

    Distinct from a transport failure: nothing was submitted, nothing was
    charged, and the caller must say so rather than reporting a generic
    outage.
    """


_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}"
    r"-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b")


# ── The single call seam ─────────────────────────────────────────────────────

def _call(tool: str, arguments: dict, timeout: float = 120.0):
    """Invoke one Higgsfield MCP tool and return its parsed reply.

    Monkeypatch this in tests; nothing else here touches the connector.
    """
    from agent_friday.services import agent as _agent
    mgr = getattr(_agent, "_MCP_MANAGER", None)
    if mgr is None:
        raise RuntimeError("MCP manager not initialized — the Higgsfield "
                           "connector is not running")
    arguments = dict(arguments)
    # Higgsfield is a REMOTE MCP server: every prompt sent here is cloud
    # egress. The tool-loop path gates these through agent._mcp_gate_args
    # ("the single egress choke point for remote MCP tool calls"), but this
    # module calls mgr.call directly and so bypassed it — a hole opened by
    # the dispatch branch in 5379e8d and inherited by every caller since.
    # Gating HERE covers image, video and music in one place rather than
    # asking three call sites to remember.
    gate = getattr(_agent, "_mcp_gate_args", None)
    if gate is not None:
        ok, explanation = gate(MCP_SERVER, tool, arguments)
        if not ok:
            # Refused outright, never submitted partially redacted: a
            # half-gated prompt is a different request than the one that was
            # asked for.
            raise EgressBlocked(explanation or
                                "blocked by the egress gate before submission")
    raw = mgr.call(MCP_SERVER, tool, arguments, timeout=timeout)
    if isinstance(raw, (dict, list)):
        return raw
    text = str(raw or "").strip()
    if not text:
        raise ValueError(f"{tool} returned an empty response")
    if text.startswith("[mcp error]") or text.startswith("[blocked]"):
        raise RuntimeError(text[:300])
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        # Some tools answer in prose around the payload. Keep the text so the
        # caller can report something true rather than a parse error.
        return {"_text": text}


# ── Catalogue membership ─────────────────────────────────────────────────────

def is_higgsfield_model(model_id: str) -> bool:
    """True when this id belongs to the enumerated Higgsfield catalogue.

    Reads the discovery cache only — never the network. An unknown id is not
    claimed: a model this machine has never seen enumerated returns False and
    the caller falls through to its other providers.
    """
    mid = str(model_id or "").strip()
    if not mid:
        return False
    try:
        from agent_friday.services.model_discovery import cached_model_ids
        from agent_friday.services.higgsfield_catalog import PROVIDER
        return mid in set(cached_model_ids(PROVIDER) or [])
    except Exception:
        return False


def model_constraints(model_id: str) -> dict:
    """Published constraints for one enumerated model (aspect ratios,
    durations, resolutions, required inputs), or {}."""
    try:
        from agent_friday.services.model_discovery import cached_models
        from agent_friday.services.higgsfield_catalog import PROVIDER
        for m in (cached_models(PROVIDER)[0] or []):
            if m.get("id") == str(model_id):
                return dict(m.get("constraints") or {})
    except Exception:
        pass
    return {}


# ── Cost ─────────────────────────────────────────────────────────────────────

def _extract_credits(reply) -> float | None:
    if isinstance(reply, dict):
        for key in ("credits", "cost", "cost_credits"):
            val = reply.get(key)
            if isinstance(val, (int, float)):
                return float(val)
        for nested in reply.values():
            if isinstance(nested, dict):
                found = _extract_credits(nested)
                if found is not None:
                    return found
    return None


def estimate_credits(kind: str, model: str, prompt: str = "",
                     **params) -> float | None:
    """Credits this generation would cost, or None when the estimate fails.

    None means "cost unknown" and must be rendered as exactly that. A guessed
    number attached to someone's balance is worse than no number.
    """
    tool = _TOOLS.get(str(kind))
    if not tool:
        return None
    payload = {"model": model, "get_cost": True}
    if prompt:
        payload["prompt"] = prompt
    payload.update({k: v for k, v in params.items() if v is not None})
    try:
        return _extract_credits(_call(tool, {"params": payload}, timeout=60.0))
    except Exception as e:
        _log.info("higgsfield cost preflight failed for %s: %s", model, e)
        return None


# ── Submit + poll ────────────────────────────────────────────────────────────

def _job_ids(reply) -> list:
    """Every generation job id in a submit reply, order preserved."""
    ids, seen = [], set()

    def walk(node):
        if isinstance(node, dict):
            for key in ("job_id", "id", "generation_id"):
                val = node.get(key)
                if isinstance(val, str) and _UUID_RE.fullmatch(val) \
                        and val not in seen:
                    seen.add(val)
                    ids.append(val)
            for val in node.values():
                walk(val)
        elif isinstance(node, list):
            for val in node:
                walk(val)
        elif isinstance(node, str):
            for match in _UUID_RE.findall(node):
                if match not in seen:
                    seen.add(match)
                    ids.append(match)

    walk(reply)
    return ids


def _collect_urls(node, out: list) -> None:
    """Every terminal output URL in a status reply.

    Prefers rawUrl over minUrl/thumbnailUrl via creative_store's parser —
    filing a downscaled preview as the creation would quietly substitute
    something worse for what was paid for.
    """
    from agent_friday.services.creative_store import parse_higgsfield_status
    if isinstance(node, dict):
        _status, urls = parse_higgsfield_status(node)
        for url in urls:
            if url not in out:
                out.append(url)
        for key in ("rawUrl", "url", "output_url"):
            val = node.get(key)
            if isinstance(val, str) and val.startswith("http") and val not in out:
                out.append(val)
        for val in node.values():
            _collect_urls(val, out)
    elif isinstance(node, list):
        for val in node:
            _collect_urls(val, out)


def _wait(job_ids: list) -> tuple:
    """Poll until every job is terminal. Returns (all_terminal, urls, last)."""
    jobs = [{"index": i, "job_id": j} for i, j in enumerate(job_ids[:12])]
    urls, last = [], None
    for _ in range(_MAX_WAITS):
        try:
            last = _call("jobs_wait", {"jobs": jobs, "timeout_seconds": 15},
                         timeout=60.0)
        except Exception as e:
            _log.warning("higgsfield jobs_wait failed: %s", e)
            return False, urls, {"error": str(e)[:200]}
        _collect_urls(last, urls)
        done = isinstance(last, dict) and last.get("all_terminal")
        if done or urls:
            return True, urls, last
        blob = json.dumps(last, default=str).lower() if last else ""
        if any(state in blob for state in ("failed", "nsfw", "canceled")):
            return True, urls, last
        time.sleep(1.0)
    return False, urls, last


# ── Public entry point ───────────────────────────────────────────────────────

def generate(kind: str, prompt: str, *, model: str, aspect_ratio=None,
             n: int = 1, dest_dir=None, extra: dict | None = None) -> dict:
    """Run one Higgsfield generation end to end and land the bytes on disk.

    Returns the creative_engine result shape:
      {status: 'ok', files: [...], model, api_model, prompt, credits, provider}
    or {status: 'error'|'unavailable', reason, ...}. Never raises.

    A submitted job whose output could not be downloaded is reported with its
    job id and URLs rather than as a bare failure — the vendor has already
    charged for it and the file is retrievable for at least seven days.
    """
    tool = _TOOLS.get(str(kind))
    if not tool:
        return {"status": "error", "reason": f"unsupported kind {kind!r}"}

    params = {"model": model}
    if prompt:
        params["prompt"] = prompt
    if aspect_ratio:
        params["aspect_ratio"] = aspect_ratio
    try:
        count = max(1, min(int(n), 4))
    except (TypeError, ValueError):
        count = 1
    if count > 1:
        params["count"] = count
    # Spend credits, explicitly. Omitting this asks the server to decide and
    # can return an interactive `unlim_choice` question instead of submitting;
    # a free-trial allowance is the user's to spend deliberately, not
    # something to consume on their behalf from a settings picker.
    params["use_unlim"] = False
    params.update({k: v for k, v in (extra or {}).items() if v is not None})

    credits = estimate_credits(kind, model, prompt,
                               **{k: v for k, v in params.items()
                                  if k not in ("model", "prompt", "use_unlim")})

    try:
        reply = _call(tool, {"params": params}, timeout=180.0)
    except EgressBlocked as e:
        # Nothing left the machine and nothing was charged. Reported as
        # `blocked`, matching creative_engine's content-policy shape, so the
        # caller never renders this as a provider outage.
        return {"status": "blocked", "provider": "higgsfield",
                "model": model, "prompt": prompt, "reason": str(e)[:300]}
    except Exception as e:
        return {"status": "unavailable", "provider": "higgsfield",
                "model": model, "prompt": prompt,
                "reason": f"Higgsfield submit failed: {e}"[:300]}

    if isinstance(reply, dict) and reply.get("unlim_choice"):
        # Surfaced rather than silently answered — it is a question about
        # spending the user's allowance.
        return {"status": "error", "provider": "higgsfield", "model": model,
                "reason": "Higgsfield asked whether to spend free-trial "
                          "generations; answer it before retrying.",
                "unlim_choice": reply.get("unlim_choice")}

    job_ids = _job_ids(reply)
    urls = []
    _collect_urls(reply, urls)
    if not urls and job_ids:
        _ok, urls, last = _wait(job_ids)
        if not urls:
            return {"status": "error", "provider": "higgsfield",
                    "model": model, "prompt": prompt, "credits": credits,
                    "job_ids": job_ids,
                    "reason": "Higgsfield job did not produce an output URL "
                              "within the poll budget. It may still finish — "
                              f"job {job_ids[0]}.",
                    "detail": str(last)[:300]}
    if not urls:
        return {"status": "error", "provider": "higgsfield", "model": model,
                "prompt": prompt, "credits": credits,
                "reason": "Higgsfield returned no job id and no output URL.",
                "detail": str(reply)[:300]}

    from agent_friday.services import creative_store
    job = {"provider": "higgsfield", "kind": kind,
           "request_id": (job_ids[0] if job_ids else "manual"),
           "prompt": prompt, "model": model}
    files, failures = [], []
    for url in urls:
        try:
            res = creative_store.download_output(url, job=job, dest_dir=dest_dir)
        except Exception as e:
            failures.append(f"{url}: {e}")
            continue
        if res.get("ok"):
            files.append({"path": res.get("path"), "url": url})
        else:
            failures.append(f"{url}: {res.get('error') or 'download failed'}")

    if not files:
        # Paid for, produced, not saved. Say all three.
        return {"status": "error", "provider": "higgsfield", "model": model,
                "prompt": prompt, "credits": credits, "job_ids": job_ids,
                "output_urls": urls,
                "reason": "Higgsfield finished but the output could not be "
                          "saved locally. The files are still on Higgsfield "
                          "for at least seven days: " + "; ".join(urls[:3]),
                "detail": "; ".join(failures)[:300]}

    out = {"status": "ok", "provider": "higgsfield", "files": files,
           "model": model, "api_model": model, "prompt": prompt,
           "credits": credits, "job_ids": job_ids}
    if failures:
        out["partial"] = failures
    return out
