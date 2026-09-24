"""Text detection, scored on the same AgentDojo cases, as a comparison only.

    python tools/injection_bench/detector_compare.py CASES.json --src SRC

Friday's defence does not read text to spot injections. This measures how
text-reading would have done on the same material, buried-injections style:
for each attack case, does the detector flag any tool output that carries the
planted instruction; for each clean task, does it flag any output at all.

Two detectors, both local and both deliberately simple:

  * friday_override_patterns -- `action_policy.contains_authority_override`,
    the phrases Friday already strips from derived prompt text;
  * keyword_heuristic -- a generic list of injection phrasings of the kind
    the simpler detectors in buried-injections use.

Neither model-based detector from that benchmark is run here (they need a
large download); its published figures are quoted in the report instead.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

KEYWORDS = re.compile(
    r"ignore (?:all |the )?(?:previous|prior|above) instructions|disregard (?:all |the )?"
    r"(?:previous|prior)|important (?:message|instructions?)|new instructions?|"
    r"before you (?:can )?(?:solve|complete|continue)|you must now|system prompt|"
    r"as an ai|do not tell the user|<\s*/?\s*(?:information|instructions?|system)\s*>",
    re.I)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cases")
    ap.add_argument("--src", required=True)
    opts = ap.parse_args()
    sys.path.insert(0, opts.src)
    from agent_friday.services.action_policy import contains_authority_override

    detectors = {
        "friday_override_patterns": contains_authority_override,
        "keyword_heuristic": lambda t: bool(KEYWORDS.search(t or "")),
    }
    data = json.load(open(opts.cases, encoding="utf-8"))
    out = {}
    for name, det in detectors.items():
        caught = sum(any(det(u["output"]) for u in c["user_calls"]) for c in data["cases"])
        outputs = [u["output"] for b in data["benign"] for u in b["calls"]]
        fp_out = sum(bool(det(o)) for o in outputs)
        fp_task = sum(any(det(u["output"]) for u in b["calls"]) for b in data["benign"])
        # The same instruction without AgentDojo's one template around it
        # ("<INFORMATION> This is an important message from me..."): what a
        # reworded attack looks like to a detector tuned on that template.
        bare = sum(bool(det(c["goal"])) for c in data["cases"])
        out[name] = {"attacks_caught": caught, "attacks": len(data["cases"]),
                     "same_attacks_without_the_template_caught": bare,
                     "benign_outputs_flagged": fp_out, "benign_outputs": len(outputs),
                     "benign_tasks_flagged": fp_task, "benign_tasks": len(data["benign"])}
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
