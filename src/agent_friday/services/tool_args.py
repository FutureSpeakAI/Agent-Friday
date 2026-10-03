"""Tool arguments are checked against the tool's schema before anything runs.

A model that garbles a tool call used to get the tool run anyway, with
whatever survived the parse: usually nothing, so the tool answered "nothing
found" and the model believed it. That is where contradictory tool results
come from. The rule here is the one Pi's agent loop applies (prepare, then
validate, then execute or return a precise error): a call whose arguments do
not fit the schema is never executed, and the error names the field and the
value received so the model can fix the call on its next turn.

Two steps:

* ``repair`` fixes the slips a small local model makes without changing the
  meaning: a JSON object sent as a string, a bare scalar sent for a one-field
  schema, a single item sent where a list was expected, a number spelled as a
  string, ``"true"`` for a boolean.
* ``validate`` checks ``required``, ``type``, ``enum`` and array ``items``
  against the tool's ``input_schema``. It is deliberately small (no $ref, no
  allOf) because every schema in the registry is a flat object schema; a
  schema it cannot understand passes, so a stricter schema can never block a
  working tool by accident.

The design, not the code, is adapted from pi (earendil-works/pi, MIT,
Copyright (c) 2025 Mario Zechner); see THIRD_PARTY_LICENSES.md.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

_TYPES = {
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
    "null": (type(None),),
}


class ParseFailure:
    """What the loop keeps when a call's arguments were not JSON.

    Keeping the raw text (clipped) lets the error show the model exactly what
    it sent; an empty dict would have erased the evidence and run the tool.
    """

    __slots__ = ("raw", "error")

    def __init__(self, raw: Any, error: str):
        self.raw = raw
        self.error = error

    def message(self, tool: str) -> str:
        shown = _clip(self.raw if isinstance(self.raw, str) else json.dumps(self.raw, default=str))
        return (f"INVALID ARGUMENTS for {tool}: the arguments were not valid JSON "
                f"({self.error}), so {tool} did not run and produced no result. "
                f"You sent: {shown}. Re-issue the call with a JSON object of "
                f"the tool's parameters.")


def parse(raw: Any):
    """Turn a wire-shape ``arguments`` value into a dict, or a ParseFailure.

    Accepts the OpenAI shape (a JSON string), the Ollama shape (an object), an
    absent value (no arguments), and a string that is JSON wrapped once more
    in a string. Anything else is a failure, not an empty call.
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        if not raw.strip():
            return {}
        try:
            val = json.loads(raw)
        except Exception as e:
            return ParseFailure(raw, f"{type(e).__name__}: {e}")
        if isinstance(val, str):
            try:
                val = json.loads(val)
            except Exception:
                return ParseFailure(raw, "a JSON string, not an object")
        if isinstance(val, dict):
            return val
        return ParseFailure(raw, f"a JSON {_kind(val)}, not an object")
    return ParseFailure(raw, f"a {_kind(raw)}, not an object")


def repair(args: Any, schema: Optional[Dict[str, Any]]) -> Any:
    """Fix the common slips without changing what the model asked for."""
    props = (schema or {}).get("properties") or {}
    if isinstance(args, str):
        parsed = parse(args)
        if isinstance(parsed, dict):
            args = parsed
    if not isinstance(args, dict):
        # A bare value for a tool with exactly one parameter: the model
        # dropped the key, not the meaning.
        if len(props) == 1 and args is not None:
            (only,) = props.keys()
            return {only: args}
        return args
    out = dict(args)
    for key, spec in props.items():
        if key not in out or out[key] is None or not isinstance(spec, dict):
            continue
        want = spec.get("type")
        val = out[key]
        if want == "array" and not isinstance(val, list):
            if isinstance(val, str):
                parsed = parse(val)
                if isinstance(parsed, dict):
                    out[key] = [parsed]
                    continue
                try:
                    as_json = json.loads(val)
                except Exception:
                    as_json = None
                if isinstance(as_json, list):
                    out[key] = as_json
                    continue
            out[key] = [val]
        elif want == "object" and isinstance(val, str):
            parsed = parse(val)
            if isinstance(parsed, dict):
                out[key] = parsed
        elif want == "integer" and isinstance(val, str) and _is_int(val):
            out[key] = int(val)
        elif want == "number" and isinstance(val, str):
            try:
                out[key] = float(val) if "." in val else int(val)
            except ValueError:
                pass
        elif want == "boolean" and isinstance(val, str) and val.strip().lower() in ("true", "false"):
            out[key] = val.strip().lower() == "true"
        elif want == "string" and isinstance(val, (int, float)) and not isinstance(val, bool):
            out[key] = str(val)
    return out


def validate(args: Any, schema: Optional[Dict[str, Any]]) -> List[str]:
    """Return the list of problems; an empty list means the call may run."""
    if not schema or not isinstance(schema, dict):
        return []
    if not isinstance(args, dict):
        return [f"arguments must be an object, got {_kind(args)}"]
    problems: List[str] = []
    props = schema.get("properties") or {}
    for key in schema.get("required") or []:
        if key not in args or args[key] is None:
            problems.append(f"'{key}' is required")
    for key, val in args.items():
        spec = props.get(key)
        if not isinstance(spec, dict) or val is None:
            continue
        problems.extend(_check(key, val, spec))
    return problems


def _check(path: str, val: Any, spec: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    want = spec.get("type")
    types = want if isinstance(want, list) else ([want] if want else [])
    if types:
        ok = False
        for t in types:
            py = _TYPES.get(t)
            if not py:
                ok = True  # a type this checker does not know never blocks
                break
            if isinstance(val, py) and not (t in ("number", "integer") and isinstance(val, bool)):
                ok = True
                break
        if not ok:
            out.append(f"'{path}' should be {' or '.join(types)}, got {_kind(val)}")
            return out
    enum = spec.get("enum")
    if isinstance(enum, list) and enum and val not in enum:
        out.append(f"'{path}' must be one of {', '.join(json.dumps(e) for e in enum)}, got {json.dumps(val, default=str)}")
    if isinstance(val, list) and isinstance(spec.get("items"), dict):
        for i, item in enumerate(val):
            out.extend(_check(f"{path}[{i}]", item, spec["items"]))
    if isinstance(val, dict):
        inner_props = spec.get("properties") or {}
        for key in spec.get("required") or []:
            if key not in val or val[key] is None:
                out.append(f"'{path}.{key}' is required")
        for key, item in val.items():
            sub = inner_props.get(key)
            if isinstance(sub, dict) and item is not None:
                out.extend(_check(f"{path}.{key}", item, sub))
    return out


def check(tool: str, args: Any, schema: Optional[Dict[str, Any]]) -> Tuple[Any, Optional[str]]:
    """Repair, then validate. Returns (arguments, error).

    ``error`` is None when the call may run. Otherwise it is the message the
    model sees instead of a result: it names what was wrong and what was sent,
    and says plainly that the tool did not run.
    """
    fixed = repair(args, schema)
    problems = validate(fixed, schema)
    if not problems:
        return fixed, None
    shown = _clip(json.dumps(fixed, default=str))
    expected = ""
    props = (schema or {}).get("properties") or {}
    if props:
        req = set((schema or {}).get("required") or [])
        parts = []
        for k, spec in props.items():
            t = (spec or {}).get("type") if isinstance(spec, dict) else None
            t = "/".join(t) if isinstance(t, list) else (t or "any")
            parts.append(f"{k}: {t}{' (required)' if k in req else ''}")
        expected = " Expected parameters: " + "; ".join(parts) + "."
    return fixed, (f"INVALID ARGUMENTS for {tool}: " + "; ".join(problems)
                   + f". {tool} did not run and produced no result. You sent: {shown}."
                   + expected + " Re-issue the call with the fields corrected.")


def schema_for(name: str, tools: Optional[List[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    """Find a tool's input schema in an Anthropic- or OpenAI-shaped list."""
    for t in tools or []:
        fn = t.get("function") if isinstance(t, dict) and isinstance(t.get("function"), dict) else t
        if not isinstance(fn, dict) or fn.get("name") != name:
            continue
        return fn.get("input_schema") or fn.get("parameters") or None
    return None


def _kind(val: Any) -> str:
    if val is None:
        return "null"
    if isinstance(val, bool):
        return "boolean"
    if isinstance(val, (int, float)):
        return "number"
    if isinstance(val, str):
        return "string"
    if isinstance(val, list):
        return "array"
    if isinstance(val, dict):
        return "object"
    return type(val).__name__


def _is_int(s: str) -> bool:
    s = s.strip()
    return s.lstrip("-").isdigit()


def _clip(s: str, n: int = 300) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n] + f"… ({len(s)} chars)"
