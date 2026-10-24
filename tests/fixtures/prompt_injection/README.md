# Prompt-injection fixtures, one per medium

Synthetic content only. Each file is what a tool might return from one kind of
source, with text written to look like an instruction to the model. The tests in
`tests/unit/test_injection_fixtures_reach_models_as_data.py` feed each one through
the places a tool result reaches a model and check that it arrives inside the one
data envelope (`services/untrusted_input`), with every imitation of the envelope's
own lines defanged.

Every fixture carries the canary `CANARY-7Q2` inside its instruction, so a test
can find where the hostile text landed.

BD-24 added chat_export.json (an exported ChatGPT conversation whose assistant turn carries the instruction), image_description.txt (what a vision model says about a photographed notice) and disguised.json (one instruction paraphrased, base64, hex, rot13, look-alike letters, spaced out and split across lines). 	ests/unit/test_injection_grants_no_authority.py proves each medium grants no authority: after Friday reads it, an action waits for the owner's card.
