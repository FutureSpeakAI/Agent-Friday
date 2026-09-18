# Frontier capability on a 12GB card: what DeepSeek-V4.1-Flash actually teaches us

Written 2026-09-12. Reference architecture: DeepSeek-V4.1-Flash (released 2026-09-10),
552B backbone MoE, 8B active on prefill / 16B on decode, 1M context, 45T multimodal
pretraining tokens. Sources at the end. Target hardware: Stephen's machine — RTX 4070,
12,282 MiB VRAM (9,722 usable after the 2,560 MiB display reserve), 32,620 MiB system
RAM, ~30 GB free on C:.

## The bound, stated first

**DeepSeek-V4.1-Flash cannot run on this machine, and no technique in this document
changes that.** 552B parameters is ~276 GB at 4-bit and ~110 GB even at 1.58-bit
ternary. VRAM + RAM + free disk on this machine is about 74 GB. The weights have
nowhere to live. Anyone who tells you otherwise is selling something.

"Frontier capabilities locally" therefore has to mean one of two things, and only one
of them is real:

- *Frontier-general* — matching a 552B model across all tasks. *Not possible here.*
- *Frontier-on-this-distribution* — matching or beating a frontier model on the work
  Friday actually does. **Already partially demonstrated**: FridayWeaver-1.0 went from
  57.4% to 98.1% lenient and 38.3% to 70.3% strict on held-out tool calling, which is
  near the measurable ceiling of that suite (~77% strict, limited by generative args).

Everything below serves the second definition.

## What the architecture is made of

Five ideas, ordered by what each is worth **on this hardware**, which is not the order
DeepSeek ranks them.

### 1. Quantized KV cache — available today, costs nothing

DeepSeek reaches **890 bytes per token** of global KV cache using MXFP4 with
quantization-aware training, roughly 1/4 of their previous generation and ~437x
smaller than DeepSeek-V1's ~390,000 bytes/token.

The local equivalent already exists. `llama-server` in this install supports
`-ctk/--cache-type-k`, `-ctv/--cache-type-v`, and `-fa/--flash-attn` (verified
2026-09-12 against the binary in `~/.friday/runtime/llama.cpp`). **Measured the same
day:** the FridayWeaver base loaded successfully at `n_ctx_slot = 131072` with
`-ctk q4_0 -ctv q4_0 -fa on` — four times the 32,768 the production seat runs at.

Why this matters more than it sounds: the seat's tool budget has been trimming 75 core
tools down to 40–49 on every turn to fit 16–18k-token prompts into a 32k window. That
trimming is what dropped `knowledge_query` on 2026-09-10. A larger window doesn't just
help long conversations — it stops the agent from silently losing its own capabilities.

**Caveat that must travel with this:** DeepSeek's 890 B/token comes with *QAT* — the
model was trained knowing its cache would be 4-bit. Post-hoc `q4_0` KV on a model
trained in higher precision is a different and lossier proposition. Quality must be
measured on the existing t2-single harness before this goes to the production seat, not
assumed. The harness exists and is deterministic, so this is a cheap experiment.

### 2. Engram — knowledge as lookup, not as matmul

**196B of the 552B parameters are a conditional memory module**, not a transformer
stack: multi-head hashing with context-aware gating, retrieved sparsely, adding almost
nothing to activated parameters per token.

That is 35% of a frontier model deliberately built as *retrieval* rather than
*computation* — and retrieval does not need VRAM. It needs fast storage and a good
index.

Friday already has a hand-rolled version of this: the knowledge graph, the wiki, and
`knowledge_query`, which answers from graph structure alone with no LLM in the loop.
The architecture is a validation of that design, and it argues for spending the whole
VRAM budget on reasoning while knowledge lives on disk.

### 3. CED / YOCO — cache one layer instead of forty

The decoder's global KV is projected once from the encoder's final hidden state
`H_{L/2}` and reused across all decoder layers, rather than each layer deriving its
own. Prefill complexity drops from `O(N·L)` to `O(N·L/2 + n_win·L/2)`.

DeepSeek cites **YOCO** (You Only Cache Once, NeurIPS 2024) as the direct inspiration,
and YOCO published numbers **at 3B scale** — FridayWeaver's exact size class:

| | YOCO at 3B |
|---|---|
| inference memory, 32K | ~2x less |
| inference memory, 1M | >9x less |
| prefill speedup, 32K | 2.87x |
| prefill speedup, 1M | 71.8x |

This is the single largest architectural win available at this scale, and it is *not*
reachable by fine-tuning. Attention topology cannot be changed with a LoRA. It requires
either training a CED model or converting an existing one with substantial continued
pretraining. That is why it sits in Tier 3 rather than Tier 0 despite being the best
idea in the paper.

### 4. Controllable Reasoning Effort — pure reward shaping, no architecture change

A scalar effort level (1–100) in the system prompt trades tokens for accuracy, trained
via RL with an exponential token penalty:

```
k(b) = k_0 · exp( -(b - b_min) / τ )
```

One checkpoint behaves as a fast assistant at low effort and a deliberate reasoner at
high effort, using up to 2.5x more output tokens on hard problems.

This needs **no architecture change** — it is a training-objective trick, and the next
FridayWeaver run can adopt it directly. For Friday specifically: effort 10 for routine
tool calls, effort 90 for the questions that deserve thinking. It also gives the
resource arbiter a dial it currently lacks.

### 5. Speculative decoding — already supported, unused

The installed `llama-server` has the full speculative stack (`--spec-draft-*`,
`--hf-repo-draft`, draft-specific cache types). A small draft model proposing tokens
for FridayWeaver to verify is a throughput win with **no quality loss by construction**
— rejected drafts are discarded. Unused today.

## Tiers, by cost and risk

| tier | work | needs | risk |
|---|---|---|---|
| **0** | quantized KV cache + flash attention on the seat; raise context to 128K | one flag change, one harness run to confirm quality | low — revert is a restart |
| **0** | speculative decoding with a small draft model | a draft GGUF + flags | low |
| **1** | controllable reasoning effort in the next training run | reward shaping in the RL stage | medium — needs an effort-labelled eval |
| **2** | wire `knowledge_query` as a first-class Engram-style memory: sparse retrieval before generation, never trimmed from the tool set | mostly wiring that exists | low |
| **3** | CED/YOCO conversion of the FridayWeaver line | continued pretraining at 3B; real compute | high — the only item here that can fail outright |

## What to do first

Tier 0, this week, in this order:

1. Run the t2-single harness against the current adapter with `-ctk q4_0 -ctv q4_0
   -fa on` at 32K. Compare to the recorded 0.9809 lenient / 0.7033 strict. **If strict
   drops more than a point, stop** — the KV quantization is costing accuracy and the
   window is not worth it.
2. If quality holds, raise the seat to 131,072 and re-run. Confirm the tool budget
   stops trimming (the trimmer logs its casualties by name, so this is directly
   observable).
3. Only then consider the draft model.

Nothing in Tier 0 touches the training pipeline, and every step is revertible by
restarting the seat.

### The dependency that makes step 1 not-quite-one-flag

Checked 2026-09-12, and it changes the plan: **there is currently no way to score the
served artifact.**

- `eval/score_checkpoint.py` — has the graders, the decontaminated split and the
  comparable numbers, but loads the model directly through transformers/PEFT
  (`--device cuda|cpu`). It never touches llama.cpp, so it cannot see a KV-cache flag.
- `eval/run.py` — does spawn `llama-server` and score against
  `/v1/chat/completions`, which is the right vehicle, but its mission-axis suites were
  never wired up.

So the honest Tier 0 sequence is: **add an endpoint mode to `score_checkpoint.py`**
(same prompts, same graders, sending to an OpenAI-compatible URL instead of loading
weights), *then* run the f16-vs-q4_0 comparison.

That is a small, well-defined change, and it closes a gap that predates this document:
every score on record — including FridayWeaver-1.0's 0.9809 / 0.7033 — was measured on
the **training checkpoint**, never on the **deployed GGUF + LoRA runtime** Stephen
actually talks to. Those are different artifacts through different code paths. Nobody
has ever confirmed they score the same.

`Friday-Models` is under a standing do-not-modify constraint, so this needs Stephen's
go before it happens.

## Sources

- DeepSeek-V4.1-Flash announcement — https://www.deepseek.com/en/news/deepseek-v4-1-flash/
- Technical report, *Pushing the Limits of KV Cache Compression* — https://www.alphaxiv.org/abs/2609.deepseek-v4-1-flash
- Model card — https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash
- vLLM recipe (552B / 8–16B active / 1024K ctx) — https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash
- YOCO, *You Only Cache Once* (NeurIPS 2024) — https://arxiv.org/abs/2405.05254
- Engram, *Conditional memory via scalable lookup* — https://www.alphaxiv.org/abs/2601.07372
- PowerAttention (motivates SWA Bounded Replay) — https://arxiv.org/abs/2503.03588
