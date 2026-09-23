# Kimi K3 (experimental CPU engine)

Status: **listed, not installed, never selectable.** Nothing in Friday can route
to it; it appears in the Model Browser so its requirements are visible, and
`services/kimi_k3.py` refuses to run until it is deliberately configured.

Engine: <https://github.com/FareedKhan-dev/kimi-k3-in-c> — a 2.78-trillion-parameter
Kimi K3 run by a C99 CLI, CPU only, streaming weights from disk so peak RSS
stays near 8 GB.

## What it is and is not

The README's own sentence settles most of this:

> Version one is text-only: there are no tools, images, server, or context
> compaction.

* **No server.** No HTTP endpoint, no OpenAI-compatible API, no daemon. The
  only interface is running `bin/k3` and reading stdout.
* **No tool calling.** So it can never serve a seat that dispatches tools,
  which is most of Friday's work.
* **It is chat-capable**, which is easy to get wrong. `--chat` uses the
  official Kimi K3 XTML segments and tokenizer control tokens and "does not
  fall back to ChatML or a handwritten generic prompt". Raw continuation is
  what you get *without* `--chat`, not the only mode available.
* **Text only.** No images.

## Requirements

| | |
|---|---|
| Disk | **~1.7 TB free** — 1.56 TB checkpoint + 109 GB packed trunk |
| RAM | 8.2 GB (`laptop`) · 31.9 GB (`desktop`) · 95.5 GB (`workstation`) · ~128 GB (`server`) · ~224 GB (`max`) |
| Speed | **26.5 s/token** at 8 GB · **5.6 s/token** at 128 GB+ |
| CPU | x86-64 with AVX2 and FMA. AVX-512 unnecessary |
| Windows build | MSYS2 MinGW-w64 GCC. There is no MSVC build |

`--preset` names a *memory budget*, not a serving mode: `server` means "~128 GB
peak RSS", not an HTTP server. The first three presets re-read the model from
disk on every token, so a slow disk is slow at every step; only the 128 GB+ row keeps
everything in memory.

Seconds per token is the figure that decides usability. A 256-token answer is
roughly 24 minutes at 5.6 s/token and about two hours at 26.5.

## Licensing — two licences, not one

The **engine** is Apache-2.0. The **weights** are not covered by it:

> Kimi K3 is created and released by Moonshot AI under its own license. This
> repository contains **no model weights** and grants no rights to them.

Whether Moonshot AI's terms permit a given use has to be answered against that
licence before any weights are fetched. Weights come from `moonshotai/Kimi-K3`
on Hugging Face and need an `HF_TOKEN`.

## Why it cannot be selected

Its catalog row carries `roles: []` and `curated: False`. `build_catalog` files
an entry under a role only when the entry is curated **and** names that role, so
both properties together keep it out of every picker. It is also absent from
every provider's `models` list and from the router's fallback chain — a fallback
is Friday's own choice rather than the user's, and an engine that cannot call
tools and answers in seconds per token must never be one.

It is **not** a provider descriptor, deliberately. A descriptor earns
`classification: local` only with a local-capable adapter *and* a private
`base_url`; a subprocess has no URL at all, so registering it as a provider
would fail that check and the row would be badged `cloud` — backwards for
something that never touches a network. `_arbiter_seat_entries` builds local
rows by hand for the same reason, and `_experimental_engine_entries` follows it.

## Configuring it

Off and unconfigured by default, in `~/.friday/settings.json`:

```json
"kimi_k3": {
  "enabled": false,
  "binary": "",
  "model_dir": "",
  "trunk_dir": "",
  "tokenizer_dir": "",
  "preset": "laptop"
}
```

`services/kimi_k3.status()` reports why it cannot run, in a sentence meant to be
passed straight through. `complete()` raises `KimiK3Unavailable` carrying that
same sentence rather than failing obscurely.

The prompt is written to a temp file and passed with `--prompt-file`, never on
`argv`: the README prefers it "for anything non-ASCII: the shell re-encodes
argv, whereas a file is read verbatim". Output is captured as bytes and decoded
UTF-8 explicitly, because `text=True` on Windows decodes with the locale code
page and corrupts every multi-byte character.
