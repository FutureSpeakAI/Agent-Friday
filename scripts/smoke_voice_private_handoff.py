"""Live smoke check: a private-summary handoff sends zero seeded PII.

One question, a few lines of output, exit 0 or 1 — meant for the deploy lane
to run after a voice change lands. No restart, no GPU, no cloud key.

    python scripts/smoke_voice_private_handoff.py

What it proves, on the real code paths:

  1. a local answer full of identifiers scrubs to placeholders;
  2. the text that would reach the cloud voice session carries none of the
     seeded values — checked where it is handed to the call, not on the
     scrub's return value;
  3. the share signs a receipt that verifies and names where it went;
  4. a declined card hands over nothing;
  5. with no local model, nothing is shared at all.

The local model and the live call are stood in for, because what is under
test is the seam between them. Every seeded value comes from a range
reserved for fiction, and the receipt is written into a throwaway home so
this never touches the owner's real decision BOM.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

os.environ.setdefault("FRIDAY_HOME", tempfile.mkdtemp(prefix="friday_smoke_"))
os.environ.setdefault("FRIDAY_TESTING", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

SEEDED = {
    "ssn": "900-00-0000",  # pragma: allowlist secret - never-issued SSN block
    "card": "4111 1111 1111 1111",  # pragma: allowlist secret - Visa test number
    "phone": "(512) 555-0147",
    "email": "not.a.real.person@example.org",
    "street": "1847 Cedar Hollow Lane",
}
RAW = ("Her details: reach her on {phone} or {email}. She lives at {street}. "
       "Her social is {ssn} and the card on file ends with {card}. "
       "She likes hiking on weekends.").format(**SEEDED)

LINES: list[str] = []
FAILED: list[str] = []


def check(label, ok, detail=""):
    LINES.append(("  ok   " if ok else "  FAIL ") + label
                 + (("  [" + detail + "]") if detail and not ok else ""))
    if not ok:
        FAILED.append(label + ((" - " + detail) if detail else ""))


def leaked(text):
    """Seeded values found in `text`, by name; digits too, in case the
    formatting changed on the way through."""
    text = text or ""
    out = [n for n, v in SEEDED.items() if v in text]
    digits = "".join(c for c in text if c.isdigit())
    for n, v in SEEDED.items():
        bare = "".join(c for c in v if c.isdigit())
        if len(bare) >= 7 and bare in digits and n not in out:
            out.append(n + "(digits)")
    return out


def main() -> int:
    from agent_friday.governance import action_gate as ag
    from agent_friday.services import local_context as lc

    handed: list[tuple] = []
    lc._deliver = lambda cid, text, kind: handed.append((kind, text)) or True
    lc._note_in_conversation = lambda *a, **k: None

    # The scrub on its own, first and unconditionally. The egress floor would
    # also refuse this answer whole, so a build with a broken scrub can still
    # end up leaking nothing — which is defence in depth working, and not a
    # reason for a smoke check to stay quiet about the broken scrub.
    scrubbed, placeholders = lc.scrub(RAW)
    check("the scrub leaves no seeded identifier",
          bool(scrubbed.strip()) and not leaked(scrubbed),
          ",".join(leaked(scrubbed)) or "the scrub returned nothing")
    check("the scrub reports what it replaced", bool(placeholders))

    draft = lc.prepare(RAW)
    payload = {"text": draft["text"], "placeholders": draft.get("placeholders"),
               "local_model": "smoke-local", "cloud_model": "smoke-cloud",
               "conversation_id": "smoke", "question": "what does she enjoy?",
               "version": 1}

    shared = False
    if draft["text"]:
        shared = lc._send("smoke-1", "smoke", draft["text"], payload)
        check("the approved text reached the call", shared)
        ctx = [t for k, t in handed if k == "context"]
        check("nothing bound for the cloud carries seeded PII",
              bool(ctx) and not leaked(ctx[0]),
              ",".join(leaked(ctx[0])) if ctx else "nothing was sent")
    else:
        check("the floor refused the answer whole, so nothing leaves",
              not [t for k, t in handed if k == "context"])

    rows = []
    p = ag.receipts_path()
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("tool") == "voice.local_context_share":
                rows.append(r)
    check("the share signed a receipt", bool(rows) or not shared,
          "a share happened with no receipt")
    if rows:
        last = rows[-1]
        check("the receipt verifies under the governance key",
              ag.verify_receipt(last))
        check("the receipt names where it went",
              last.get("destination") == "smoke-cloud",
              str(last.get("destination")))
        check("the receipt itself carries no seeded PII",
              not leaked(json.dumps(last)),
              ",".join(leaked(json.dumps(last))))

    handed.clear()
    lc._on_decision({"approval_id": "smoke-2", "status": "declined",
                     "payload": {"conversation_id": "smoke", "text": RAW,
                                 "version": 1}})
    check("a declined card sends no context",
          not [t for k, t in handed if k == "context"])
    check("the decline notice carries no seeded PII",
          not any(leaked(t) for _k, t in handed))

    handed.clear()
    out = lc.request("what does she enjoy?", conversation_id="smoke",
                     cloud_model="smoke-cloud", answer_fn=lambda q: (RAW, None))
    check("with no local model nothing is shared",
          out.get("status") == "unavailable"
          and not [t for k, t in handed if k == "context"],
          str(out.get("status")))

    print("\n".join(LINES))
    if FAILED:
        print("\nSMOKE FAIL - private-summary handoff: %d of %d checks failed"
              % (len(FAILED), len(LINES)))
        for f in FAILED:
            print("  - " + f)
        return 1
    print("\nSMOKE PASS - a private-summary handoff sends zero seeded PII "
          "(%d checks)" % len(LINES))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        # A smoke check that cannot run has not passed.
        print("SMOKE FAIL - the check could not run: %s: %s"
              % (type(e).__name__, e))
        sys.exit(1)
