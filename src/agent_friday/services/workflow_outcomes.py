"""Check workflow deliverables against evidence from the same invocation.

Reading an output is not new authority. Artifacts stay in their conversation;
file reads use the ordinary governed tool executor supplied by the caller.
Free-form quality requirements are shown as review coverage, never inferred
from the fact that a tool returned successfully.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


def _object(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            out = json.loads(value)
            return out if isinstance(out, dict) else {}
        except (TypeError, ValueError):
            pass
    return {}


def successful(entry):
    """Require an actual successful receipt, including structured failures."""
    from agent_friday.services.completion_receipts import receipt_ok
    if not receipt_ok(entry):
        return False
    result = entry.get("result")
    obj = _object(result)
    if obj:
        if obj.get("ok") is False or obj.get("error"):
            return False
        status = str(obj.get("status") or "ok").lower()
        return status in {"ok", "success", "complete", "completed", "done"}
    text = str(result or "").strip().lower()
    return bool(text) and not text.startswith((
        "error", "read error", "write error", "not done", "could not", "cannot",
        "file not found", "not a file", "invalid path", "[", "denied", "refused"))


def _substantive(text):
    value = str(text or "").strip()
    if len(value) < 20 or len(value.split()) < 4:
        return False
    low = value.lower()
    if low in {"no change", "nothing new", "no new updates"}:
        return False
    return not low.startswith(("[", "error:", "tool error", "i'll ", "i will ",
                               "let me ", "give me a moment", "(no output)"))


def fingerprint(text, outputs):
    """Content identity, independent of fresh task/artifact identifiers."""
    hashes = sorted(str(o.get("sha256")) for o in outputs if o.get("sha256"))
    content = {"outputs": hashes} if hashes else {"reply": " ".join(str(text or "").split())}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode("utf-8")).hexdigest()


def verify(contract, result_text, tool_trace, *, conversation_id=None,
           started_at=0, file_reader=None, code_reader=None, baseline=None, change_only=False):
    """Return structural outcome evidence; no provider calls or arbitrary reads.

    ``file_reader(path)`` must enforce the same permissions as read_file.
    ``code_reader(codebase_id, commit)`` must scope the codebase to this chat.
    Both return a normal tool result, and neither is invoked without a matching
    successful write receipt from this run.
    """
    contract = contract or {}
    output = contract.get("output") or {"kind": "reply"}
    kind = output.get("kind") or "reply"
    checks, outputs = [], []

    def check(name, passed, detail):
        checks.append({"name": name, "status": "passed" if passed else "unverified",
                       "detail": detail})

    trace = [t for t in (tool_trace or []) if successful(t)]
    no_change = change_only and str(result_text or '').strip().upper() == 'NO CHANGE'
    source_reads = {'search_web', 'browse_web', 'read_file', 'search_files', 'search_email',
                    'query_calendar', 'read_doc', 'search_drive', 'read_wiki', 'search_wiki',
                    'search_news', 'search_library'}
    if kind == "reply" and no_change:
        reads = [t for t in (tool_trace or []) if t.get('name') in source_reads]
        passed = bool(baseline and baseline.get('fingerprint') and reads
                      and all(successful(t) for t in reads))
        check("comparison_read", passed, "Source reads and a previous successful baseline support this no-change result."
              if passed else "NO CHANGE needs a previous successful baseline and successful source-read receipts.")
        if passed:
            outputs.append({'kind': 'reply', 'title': 'No change',
                            'sha256': baseline['fingerprint']})
    elif kind == "reply":
        passed = _substantive(result_text)
        check("reply_present", passed, "A substantive reply is available." if passed else
              "No substantive reply was produced.")
        if passed:
            outputs.append({"kind": "reply", "title": output.get("title") or "Result",
                            "sha256": fingerprint(result_text, [])})
    elif kind == "artifact":
        from agent_friday.services import artifacts
        for entry in trace:
            if entry.get("name") != "artifact_put" or not conversation_id:
                continue
            result = _object(entry.get("result"))
            args = entry.get("input") or {}
            if args.get("conversation_id") not in (None, "", conversation_id):
                continue
            aid, version = result.get("artifact_id"), result.get("version")
            if not isinstance(aid, str) or not isinstance(version, int):
                continue
            try:
                rec = artifacts.get(conversation_id, aid, version=version)
            except (ValueError, OSError):
                continue
            if not rec or rec.get("conversation_id") != conversation_id:
                continue
            if float(rec.get("ts") or 0) < float(started_at or 0):
                continue
            if output.get("title") and output["title"] != rec.get("title"):
                continue
            if not rec.get("content") or not rec.get("sha256"):
                continue
            if artifacts._sha(rec["content"]) != rec["sha256"]:
                continue
            outputs.append({"kind": "artifact", "artifact_id": aid,
                            "conversation_id": conversation_id, "version": version,
                            "title": rec.get("title"), "sha256": rec["sha256"]})
        check("artifact_saved", bool(outputs), "Read back a version written by this run in its conversation."
              if outputs else "No matching artifact version could be read back.")
    elif kind == "file":
        expected = output.get("path")
        for entry in trace:
            if entry.get("name") != "write_file" or file_reader is None:
                continue
            path = (entry.get("input") or {}).get("path")
            if not isinstance(path, str) or not path:
                continue
            try:
                resolved = Path(path).expanduser().resolve()
                if expected and resolved != Path(expected).expanduser().resolve():
                    continue
                readback = file_reader(str(resolved))
            except Exception:
                continue
            if not successful({"name": "read_file", "result": readback}):
                continue
            text = str(readback or "").strip()
            if not text or text.startswith(("This document is on", "Could not read")):
                continue
            outputs.append({"kind": "file", "path": str(resolved),
                            "title": output.get("title") or resolved.name,
                            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                            "evidence": "governed_readback"})
        check("file_saved", bool(outputs), "Read back the output through the normal file permission checks."
              if outputs else "No successfully written output could be read back with current permissions.")
    elif kind == "code":
        for entry in trace:
            if entry.get("name") != "codebase_edit" or code_reader is None:
                continue
            result = _object(entry.get("result"))
            cbid = result.get("codebase")
            sha = (result.get("step") or {}).get("sha")
            if not isinstance(cbid, str) or not re.fullmatch(r"[0-9a-f]{7,40}", str(sha or "")):
                continue
            try:
                receipt = code_reader(cbid, sha)
            except Exception:
                continue
            if not receipt or receipt.get("commit") != sha:
                continue
            if not receipt.get("files") and not receipt.get("deleted"):
                continue
            outputs.append({"kind": "code", "codebase_id": cbid, "commit": sha,
                            "title": output.get("title") or "Code change",
                            "sha256": sha, "tests": receipt.get("tests")})
        check("code_change_saved", bool(outputs), "Read back the commit and its change receipt."
              if outputs else "No matching committed change receipt was found in this conversation's codebase.")
        checks.append({"name": "code_review", "status": "not_automatically_checked",
                       "detail": "A saved change is not proof of test success or review approval."})
    else:
        check("supported_output", False, "The output kind is not supported.")
    if contract.get("success_criteria"):
        checks.append({"name": "success_criteria", "status": "not_automatically_checked",
                       "detail": str(contract["success_criteria"]),
                       "note": "These quality requirements need review; structural checks do not establish them."})
    passed = bool(outputs)
    return {"status": "verified" if passed else "unverified", "verified": passed,
            "scope": "deliverable_structure", "checks": checks, "outputs": outputs,
            "fingerprint": baseline["fingerprint"] if no_change and passed else fingerprint(result_text, outputs)}
