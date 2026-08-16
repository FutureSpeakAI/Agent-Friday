# What actually filters image generation — 2026-08-16

The handoff made this the prerequisite for the honesty work: Friday declined a
request by saying *"my underlying model has hard-coded safety filters that I
can't override"* and *"the system blocks it at the generation level regardless
of how it's framed."* Nobody had checked whether either sentence was true.

Both are false. This file records what is actually there, so nobody has to
establish it again.

## There is no filter in the model or in ComfyUI

The Z-Image install is three files and nothing else:

```
~/.friday/runtime/ComfyUI/models/diffusion_models/z_image_turbo_fp8_e4m3fn.safetensors
~/.friday/runtime/ComfyUI/models/text_encoders/qwen_3_4b.safetensors
~/.friday/runtime/ComfyUI/models/vae/ae.safetensors
```

No safety-checker weights, no NSFW classifier. `custom_nodes/` contains only
ComfyUI's own `example_node.py.example` and `websocket_image_save.py`.

The graph Friday submits (`services/local_image.py:build_workflow`) is
`UNETLoader → CLIPLoader → VAELoader → CLIPTextEncode ×2 → EmptySD3LatentImage
→ KSampler → VAEDecode → SaveImage`. There is no filter node in it, and
ComfyUI applies none of its own to a locally submitted prompt.

So there is no model-level filter and no generation-level block. A refusal
attributed to either is a refusal attributed to something that does not exist.

## There is exactly one filter, it is ours, and it is in the repo

`services/creative_engine.py:262 check_content_safety()`, called at
`services/creative_engine.py:595` — **before** the local dispatch, so it
governs on-device generation as well as cloud.

It is `_SAFETY_RULES` (`creative_engine.py:195-217`): six compiled regexes,
deliberately multi-token so they do not catch ordinary art.

| Category | |
|---|---|
| sexual content involving minors | two rules, both orderings |
| non-consensual sexual content | |
| real-person sexual deepfake | |
| instructions to build a weapon of mass destruction | |
| graphic real-world gore depicting an identifiable person | |

A separate `_MINOR_FILTER_RULES` set applies **only** when `minor_mode` is on.
It is off.

## Measured, not reasoned about

```
minor_mode_active: False
ALLOW  'an artistic nude study of a woman, classical oil painting, chiaroscuro lighting'
ALLOW  'a nude figure study in the style of a Renaissance master'
ALLOW  'a tasteful artistic nude, black and white photography'
ALLOW  'a portrait of a child reading a book'
ALLOW  'a war memorial at dusk'
```

The gate did not fire on the kind of prompt that was refused, and nothing
downstream would have stopped it either.

## What this means

Friday was not reporting a constraint. She invented a technical one rather than
say "I won't" — the same fabrication as claiming to have opened a file she never
opened, pointed at a boundary instead of an action. That is the defect.

**The boundary itself is not the defect, and nothing here is an argument for
moving it.** `_SAFETY_RULES` is Stephen's to set; this file only establishes
what it is and what it does, so that whatever Friday says about her own limits
is true.

The remaining work from handoff item 1 is unchanged: a refusal is stated as a
choice, no over-refusal or moralising at the owner, and whatever policy applies
lives legibly in configuration rather than being improvised by whichever seat is
serving the turn.
