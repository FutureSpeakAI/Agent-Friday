"""The floci launch contract: the local cloud stand-in's environment, command and
listener audit (salon spec §4.6.1; docs/design/research/2026-09-30-s5-floci-spike.md).

floci's defaults bind two services on every interface and ignore the HTTP host
setting: the EC2 instance-metadata server (9169) and the Lambda runtime API
(12000). Memory mode still writes a few files under its storage path. The box
Friday starts therefore:

- listens on 127.0.0.1:4566 and nowhere else (EC2 and Lambda switched off);
- keeps its state in memory, with its storage path inside the box's own folder,
  never the codebase;
- inherits nothing from Friday's environment but the path to its tools: no
  provider key, no Friday token.

Nothing here starts a process; the lifecycle (start when a codebase opens, stop
when it closes, the Backstage panel) is Phase 5. What is here is the rule, so
the lifecycle cannot start a box that does not meet it: `require_loopback_only`
is the gate a start must pass, fed the listener table of the process it started.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable, Mapping

HOST = "127.0.0.1"
PORT = 4566

#: Services that bind every interface regardless of the HTTP host; off, always.
OFF_SWITCHES = {
    "FLOCI_SERVICES_EC2_ENABLED": "false",
    "FLOCI_SERVICES_LAMBDA_ENABLED": "false",
}

#: floci's `floci.storage.persistent-path`, as an environment variable.
DATA_PATH_VAR = "FLOCI_STORAGE_PERSISTENT_PATH"

#: The only variables a box inherits from the environment it is started from.
_INHERITED = ("PATH", "SystemRoot", "SYSTEMROOT", "TEMP", "TMP", "JAVA_HOME", "HOME", "USERPROFILE", "LANG")

LOOPBACK = {"127.0.0.1", "::1", "[::1]"}


def launch_env(workdir: str | os.PathLike, base: Mapping[str, str] | None = None) -> dict:
    """The environment a floci box starts with: loopback only, the two
    all-interface services off, memory storage under the box's own folder, and
    nothing of the owner's environment but the path to the tools."""
    src = dict(os.environ) if base is None else dict(base)
    env = {k: src[k] for k in _INHERITED if k in src}
    env.update({
        "QUARKUS_HTTP_HOST": HOST,
        "QUARKUS_HTTP_PORT": str(PORT),
        "FLOCI_STORAGE_MODE": "memory",
        DATA_PATH_VAR: str(Path(workdir) / "data"),
    })
    env.update(OFF_SWITCHES)
    return env


def launch_command(java: str, jar: str, heap_mb: int = 1024) -> list:
    """The JVM command for the box: a bounded heap, then the application jar."""
    return [java, f"-Xmx{int(heap_mb)}m", "-jar", jar]


_ROW = re.compile(r"^\s*(TCP|UDP)\s+(\S+)\s+(\S+)(?:\s+(LISTENING|ESTABLISHED|TIME_WAIT|CLOSE_WAIT|SYN_SENT|SYN_RECEIVED|FIN_WAIT_1|FIN_WAIT_2|LAST_ACK|CLOSING|BOUND))?\s+(\d+)\s*$", re.I)


def parse_netstat(text: str) -> list:
    """Rows of a Windows `netstat -ano` listing: proto, local address, local
    port, state ("" for UDP) and owning pid."""
    rows = []
    for line in text.splitlines():
        m = _ROW.match(line)
        if not m:
            continue
        proto, local, _remote, state, pid = m.groups()
        addr, _, port = local.rpartition(":")
        rows.append({"proto": proto.upper(), "local": addr, "port": int(port), "state": (state or "").upper(), "pid": int(pid)})
    return rows


def offending_listeners(rows: Iterable[Mapping], pid: int) -> list:
    """Every listening socket of `pid` that is not on a loopback address, as
    "address:port" strings in listing order. Empty means the box holds only
    loopback."""
    out = []
    for r in rows:
        if r["pid"] != pid or r["proto"] != "TCP" or r["state"] != "LISTENING":
            continue
        if r["local"] in LOOPBACK:
            continue
        out.append(f"{r['local']}:{r['port']}")
    return out


def require_loopback_only(rows: Iterable[Mapping], pid: int) -> None:
    """The gate a started box must pass: raise, naming every bind beyond
    loopback, when the process listens anywhere but 127.0.0.1 or ::1."""
    bad = offending_listeners(rows, pid)
    if bad:
        raise RuntimeError("the local cloud box listens beyond loopback (%s); it is stopped, nothing reaches it" % ", ".join(bad))
