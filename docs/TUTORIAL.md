# Your first conversation with Friday

This gets you from a cloned repository to one working conversation. It stops
there on purpose. Everything else can wait until Friday is talking to you.

**Time:** about 20 minutes, most of it waiting for a download.
**You need:** Python 3.10+, 16 GB of RAM, about 16 GB of free disk.
**You do not need:** a graphics card, or an API key.

If you have less than 16 GB of RAM, read step 2 anyway — it will tell you
exactly what you can and cannot run rather than failing later.

**A note on platforms, because the README and this page could otherwise seem to
disagree.** Friday's *sovereign* features — the system tray, the residency
layer that manages local model seats, GPU-aware planning, and OS-protected
credential storage — are Windows-only today. This tutorial deliberately covers
the part that is **not** Windows-only: the server, the web UI, the vault, tools,
and local chat through Ollama. All of that works on Windows, macOS and Linux,
and it is enough for a first conversation. Commands are shown for both.

---

## 1. Install

Use a virtual environment. On most current Linux distributions `pip install`
outside one now fails outright (PEP 668, "externally-managed-environment"), and
on every platform it keeps Friday's dependencies away from your system Python.

**macOS / Linux:**

```bash
git clone https://github.com/FutureSpeakAI/Agent-Friday.git
cd Agent-Friday
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

**Windows:**

```
git clone https://github.com/FutureSpeakAI/Agent-Friday.git
cd Agent-Friday
scripts\install.bat
```

`install.bat` creates the virtual environment for you.

**Check that `friday` is actually runnable before continuing:**

```bash
friday --help
```

If that says `command not found`, the entry point was installed somewhere not
on your `PATH` — usually `~/.local/bin` when you skipped the venv. Either
activate the venv above, or run Friday as a module, which always works:

```bash
python -m agent_friday.cli --help
```

Everywhere this tutorial says `friday`, `python -m agent_friday.cli` is an
exact substitute.

One thing to know before you start: **`data/` and `skills/` only work from a
source checkout.** If you install from a wheel instead of cloning, the career
pipeline cannot work — its modules are not in the package. This is a known
structural issue, written up in [KNOWN_ISSUES.md](../KNOWN_ISSUES.md) §3. Clone
the repository and you will not hit it.

---

## 2. Find out what your machine can run

```bash
friday models
```

This detects your RAM, disk and GPU and tells you what is possible **before**
downloading anything. It looks like this:

```
  This machine:  16 GiB RAM   242 GiB free   GPU: none detected (nvidia-smi only)

  GET  Vault memory and tools
        1.17 GiB to fetch. CPU-only, no GPU needed: measured 57-328 ms to embed
        a chunk (8 threads, as shipped) and 358 ms per function call
        (unthrottled, as shipped).
  GET  Local conversational brain
        gemma3:4b on CPU — chat only - lacks native tool calling, so Friday
        disables tools for local turns rather than let it narrate calls it never
        made. 3.3 GiB. Generation speed on CPU is unmeasured.
  NO   Local image generation
        no NVIDIA GPU detected. Note Friday only probes nvidia-smi, so an AMD or
        Intel card reads as no card.
  NO   Managed model seats (residency layer)
        Windows-only today. On linux the seat engine is not present, so local
        inference goes through Ollama instead. Everything else works.

  Download: 4.47 GiB (3 model(s))
    - embeddinggemma:300m       0.62 GiB  indexes your vault so Friday can find things in it
    - functiongemma:270m        0.55 GiB  turns your requests into tool calls
    - gemma3:4b                 3.30 GiB  answers you without touching a cloud provider
```

Three things worth understanding here, because they are the opposite of what
most local-AI guides tell you:

**Your vault works without a graphics card.** Friday's memory and her tools run
on two small models on your CPU — measured at 57–328 ms to embed a chunk of
text and 358 ms to make a tool call. That is the part people assume needs a
GPU. It doesn't.

**A GPU buys exactly one thing: a local conversational brain** (and local image
generation). Without one, you can still use Friday fully — conversation goes to
a cloud provider if you add a key, and everything else stays on your machine.

**When it says `[no]`, it tells you why, with the arithmetic.** If your machine
cannot run something you will be told now, in a sentence you can act on, rather
than discovering it later when something quietly does nothing.

Then download what it recommends:

```bash
friday models --install
```

Each model is verified against the daemon's own inventory after it downloads.
If a download reports success but the model isn't actually there, you will be
told it failed — because it did.

---

## 3. Start her

```bash
friday
```

**The first start takes one to three minutes.** It is not hung. Friday is
merging her wiki, discovering models, loading embeddings and running a probe
battery. Later starts are faster.

She opens `http://localhost:3000` when she's ready.

**If nothing opens**, the traceback is in your terminal — `friday` runs the
server in the foreground and does not redirect anything, so read the console
first. `~/.friday/friday.log` has the application's own log alongside it.

(`~/.friday/server_stderr.log` exists only when Friday is launched from the
Windows system tray, which spawns the server as a background child and captures
its output there. Starting her with `friday` will not create that file.)

---

## 4. Say something

Type this:

```
What can you actually do on this machine right now?
```

Ask that first rather than "hello". Friday will tell you which capabilities are
live, which are refused, and why — grounded in the same hardware detection you
saw in step 2. It is the fastest way to understand what you have.

**Your first reply may take 15–50 seconds.** A local model has to load into
memory before it can answer the first time. Subsequent replies are much faster.
This is the single most common reason people think Friday has frozen.

Then try something that uses her memory:

```
Remember that I prefer short answers.
```

and, in a new message:

```
What do you know about how I like to be answered?
```

If the second answers correctly, your vault is working. That is the whole
minimum requirement, and you now have it.

---

## That's it

Friday is running, her memory works, and she can use tools. Everything else —
voice, image generation, cloud providers, workflows, the content pipeline — is
an addition to a thing that already works, and you can find it when you want
it.

Two honest notes before you go further:

- **Check [KNOWN_ISSUES.md](../KNOWN_ISSUES.md).** It is long, specific, and
  more useful than any feature list. It says what is broken, what is unverified,
  and what leaves your machine.
- **If you add API keys**, use `friday setup` rather than editing a launch
  script. Launch scripts store keys in plaintext and override the encrypted
  store, which is a defect being fixed rather than a design.

If something in this tutorial didn't work, that is a bug in the tutorial. Please
open an issue — getting a stranger to a first conversation is the thing this
project most needs to get right.
