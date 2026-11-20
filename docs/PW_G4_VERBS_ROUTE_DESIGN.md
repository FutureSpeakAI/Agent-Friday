# PW-G4b: `POST /tv/v1/verbs/<workspace>/<verb>`: design note before any code

Status: design only (BURN-DOWN, 2026-11-17). The build brief (`BURNDOWN_BUILD_BRIEF_full_control_gateway_2026-11-15.md`
section 5) says: find where a Laya verb is dispatched today and put the device call on that path; if there is no single
dispatcher, write a short design note and do **not** write a second dispatcher for devices. This is that note.

## What exists (checked in the code)

* A manifest verb (`resources/laya_manifests/<ws>.json`, read through `services/laya_manifests.py`) is a **description**:
  `id`, `action`, `group`, `tool` (one tool name, e.g. `propose_wiki_update`), `also_tools`, `gate {class, ring, card}`,
  `laya_tier`, `undo`, and so on. It carries **no argument schema** and no call path.
* There is no verb dispatcher. Laya's trusted router resolves a phrase to a verb and runs its **tool**; the one place any
  tool runs is `services/agent.py::_execute_tool(name, tool_input, pii_lookup, session_ctx, handler)`, which is where
  the action gate (`governance/action_gate.py`) and the approvals cards are applied.
* No manifest has a `device` block yet (PW-G1, DECISION-MODELS), so a device cannot say which arguments a verb takes.

## Why no route is built yet

A generic `verbs/<ws>/<verb>` that takes arguments from the phone would be a remote tool executor: any verb's tool with
caller-chosen arguments. That is the "second dispatcher" the brief forbids and more power than the spec's rule "the phone
draws only verbs the PC sent in that item's `verbs` list, with the arguments the PC named".

## Proposal (when PW-G1 lands)

1. The route accepts only a verb that is in the manifest, not `held`, not under `not_for_laya`, whose `device` block
   (PW-G1) names its arguments with types and caps. Unknown verb, unknown argument or a wrong type: `refused`, with the
   reason, before anything runs (bytes typed at the edge, Q7).
2. It runs the verb's `tool` through **`_execute_tool`** with a `session_ctx` whose principal is the device
   (`device:<id>`, owner-bound, holding `pc_act`), so the gate, the taint rules and the approvals cards apply exactly as for
   an owner turn. No second path.
3. The answer is one of: `done` (with the tool's plain-words result, no host paths), `card` (the gate raised an approval;
   the answer carries the approval id and the phone shows the fixed shell card and signs it with #836's route when it
   holds `pc_approve`), `refused` (the gate or the PC said no, with the reason).
4. A `device_act` receipt per call names workspace, verb, tool, outcome and approval id; never the arguments' text.
5. Undo is returned only where the manifest's `undo` names one.

## Tests (each with a red twin)

A verb whose gate class needs a card returns `card` and does not run; an unknown verb, an argument not named by the
manifest or a held / `not_for_laya` verb is refused; without `pc_act` or from a non-owner device nothing runs and the PC
asks; the device principal reaches the gate (a spy on `_execute_tool`); a hostile argument (path, URL, newline) is refused
by the type rule; the receipt carries no argument text.

## Questions for DECISION-MODELS (PW-G1)

* The `device` block needs, per verb, `args: [{name, type, max, required}]` and `placement`; until it exists this route
  stays unbuilt. Shall BURN-DOWN define the `args` schema and its contract test, or will G1?
* Verbs whose tool needs session context beyond the principal (e.g. `navigate_to`) are navigation, not acts: the phone
  does those itself. Please mark them `device.local: true`.

Interim: the dedicated routes already built cover the acts the spec needs first: wiki save and create (#944), job
control (#837), media writes (#840), signed approvals (#836).
