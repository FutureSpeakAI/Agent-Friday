# Getting Friday running

This takes you from nothing to your first conversation. It stops there —
everything else can wait until she's talking to you.

**About 30 minutes**, most of it waiting for downloads. You don't need to know
anything technical. You will copy some commands into a black window; that's the
whole skill required.

Friday is built for **Windows 10 or 11 with an NVIDIA graphics card**. That's
the path below. (macOS and Linux can run most of her — see [the end](#other-platforms).)

---

## Before you start: four things to install

Install these **in this order**. The last one matters most — Friday can't
download her models without it.

### 1. Python 3.12

[python.org/downloads](https://www.python.org/downloads/) → the big yellow
**Download Python** button.

**When the installer opens, tick "Add python.exe to PATH" at the bottom before
clicking Install.** It's easy to miss and it's the single most common reason
these instructions stop working. If you missed it, run the installer again and
choose Modify.

### 2. Git

[git-scm.com/download/win](https://git-scm.com/download/win) → 64-bit installer.

Click Next through every screen. The defaults are all fine.

### 3. Ollama

[ollama.com/download](https://ollama.com/download) → Download for Windows.

This is what actually runs Friday's local models — the ones that let her work
without sending anything to the internet. **It is not optional.** After
installing, it runs quietly in the background; you'll see a small llama icon
near your clock.

### 4. Check all three worked

Open **PowerShell**: press the Windows key, type `powershell`, press Enter.

Copy each line, press Enter, and check you get a version number back:

```powershell
python --version
git --version
ollama --version
```

Three version numbers means you're ready. If any says *"not recognized"*, that
program didn't install correctly — reinstall it and, for Python, make sure the
PATH box is ticked.

> **Tip for the whole guide:** you can paste into PowerShell with **Ctrl+V** or
> a right-click. If a command seems to do nothing, look for a blinking cursor —
> it's probably still working.

---

## Step 1 — Download Friday

In PowerShell:

```powershell
cd $HOME
git clone https://github.com/FutureSpeakAI/Agent-Friday.git
cd Agent-Friday
```

That last line moves you into the folder. **Every command from here on assumes
you're in it.** If you close PowerShell and come back later, run `cd $HOME\Agent-Friday`
first.

---

## Step 2 — Set up her workspace

This creates a private space for Friday's parts so she doesn't disturb anything
else on your laptop.

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

**If the second line gives you a red error about "running scripts is disabled"**,
Windows is being cautious about scripts it didn't write. Run this once, then try
again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\venv\Scripts\Activate.ps1
```

That only affects this one window and resets when you close it.

**You'll know it worked** when `(venv)` appears at the start of your prompt:

```
(venv) PS C:\Users\you\Agent-Friday>
```

That `(venv)` needs to be there for every remaining step. If you open a fresh
PowerShell window later, run the `Activate.ps1` line again.

Now install Friday herself:

```powershell
pip install -e .
```

This prints a lot of scrolling text for a minute or two. That's normal. When
it stops, check she's there:

```powershell
friday --help
```

You should see a list of commands. If it says *"not recognized"*, the `(venv)`
is missing from your prompt — go back and activate it.

---

## Step 3 — Find out what your laptop can run

```powershell
friday models
```

Friday checks your memory, disk and graphics card, then tells you what she can
and can't do **before** downloading anything. On a laptop with 16 GB of memory
and an 8 GB NVIDIA card you'll see something close to this:

```
  This machine:  16 GiB RAM   300 GiB free   GPU: 8 GiB NVIDIA

  OK   Memory
        Runs on your processor, no graphics card needed. Arrives with the
        install rather than as a separate download.
  GET  Local conversational brain
        gemma3:4b on GPU. 3.3 GiB.
  NO   Local image generation
        8 GiB card, but 2.5 GiB goes to your desktop and 1.0 GiB to seat
        overhead, leaving 4.5 GiB. Image models need about 6 GiB.
  OK   Managed model seats (residency layer)
        16 GiB RAM, 6 GiB available.
```

`NO` lines aren't errors. Friday is telling you what she won't be able to do on
this machine, with the reason, so you don't discover it later. Making pictures
on your own graphics card needs a bigger one — she can still make them through
the internet if you set that up later.

Now download what she recommends:

```powershell
friday models --install
```

**This takes 5–15 minutes** depending on your connection. It's about 3.3 GB.
You can leave it running.

Each download is checked afterwards — if something says it downloaded but didn't
actually arrive, Friday will tell you it failed rather than pretending.

---

## Step 4 — Set her up

```powershell
friday setup
```

This asks you a few questions: your name, a passphrase to protect your private
notes, and — at step 5 — some API keys.

**Skip the keys here.** Press Enter at the Anthropic prompt, then answer **y**
when it asks whether you're sure. You'll add your key in the next step instead,
where Friday encrypts it. Entering it here writes it in plain text into two
files on your laptop, which is worth avoiding for thirty seconds' work.

**Pick a passphrase you'll remember.** It protects your private notes and
nobody can recover it for you — not even Friday, which is rather the point.

---

## Step 4b — Add your Claude key, the safe way

Start Friday (Step 5 below), then in the browser:

**Settings → Providers → Anthropic → paste your key → Save.**

That's it. Friday encrypts the key on your machine, starts using it
immediately without a restart, and loads it automatically every time she starts
from then on. You never need to touch a file.

*Why not the wizard?* The wizard writes keys as readable text into
`config.yaml` and a startup script. Settings → Providers puts them through the
encrypted store instead. Both work; only one of them is private.

You can change or remove the key here at any time.

---

## Step 5 — Start her

```powershell
friday
```

**The first start takes one to three minutes.** She is not stuck. She's loading
models, checking herself over and warming up. Later starts are quicker.

She'll open a browser tab at `localhost:3000` when she's ready.

### Is it slow, or has it hung?

This is the single most confusing thing about a first run, so:

| What you see | What it means |
|---|---|
| Text still appearing in PowerShell, even slowly | Working. Leave it. |
| No new text for **under 3 minutes** on first start | Working. Leave it. |
| No new text for **over 5 minutes** on first start | Something's wrong — see below |
| Browser opened, page is blank or spinning | Give it 60 seconds, then refresh |
| First reply takes 15–50 seconds | **Normal.** The model is waking up. |

If you think it's stuck: press **Ctrl+C** in PowerShell to stop her, then run
`friday` again. If it happens twice, the error is in the PowerShell window —
scroll up, and that text is exactly what Stephen needs to see.

---

## Step 6 — Say something

In the browser, try this first:

```
What can you actually do on this machine right now?
```

Better than "hello" — she'll tell you what's working and what isn't on *your*
laptop specifically.

Then try her memory:

```
Remember that I prefer short answers.
```

And in a **new** message:

```
What do you know about how I like to be answered?
```

If she gets that right, everything essential is working. Her memory is the
heart of her, and it's now running entirely on your machine.

**That's it. You're done.**

---

## What works, with and without keys

Friday can work two ways: entirely on your laptop, or with help from an AI
service over the internet. Here's the honest picture of each.

**Everything on your laptop, no key, nothing sent anywhere:**

- Conversation — she runs a model called `gemma3:4b` on your graphics card
- Her memory — remembering things, finding them later, building up a picture
  of what matters to you
- Everything you write to her stays on the machine

**What a Claude key adds:**

- **Tools. This is the real difference.** Reading files, searching the web,
  working with your calendar. Friday can only use her tools through a cloud
  model at the moment — running them from a local model hasn't been built yet.
  So this isn't about your laptop being too small; a bigger one wouldn't change
  it.
- **Sharper conversation** on complicated questions and long documents.

**What a Google Gemini key adds:** talking to Friday out loud, and making
images and video. Without it those don't work.

**So, plainly:** with no keys you get a private assistant who talks and
remembers. With a Claude key you get one who also *does things*. With both you
get one you can talk to out loud. Each is real; they're different products, and
you can move between them whenever you like.

You can add or change keys any time in **Settings → Providers**. Nothing you've
set up gets lost.

> **One caution.** A no-key install hasn't been tested end to end by anyone yet.
> It should work — everything is in place for it — but you'd be the first. If
> step 6 doesn't behave, that's worth reporting rather than assuming you did
> something wrong.

---

## The tray icon

On Windows, Friday can also run from a small icon near your clock instead of
from PowerShell. Right-click it for Start, Stop and Open.

Worth knowing: **the icon does not change colour when she stops.** It looks the
same whether she's running or not. To check properly, right-click and read the
status line in the menu.

You don't need the tray for anything in this guide. The `friday` command in
PowerShell does the same job and shows you more when something's wrong.

---

## If you get stuck

Almost everything that goes wrong at this stage is one of five things:

| It said | Try |
|---|---|
| `python is not recognized` | Reinstall Python with **Add to PATH** ticked |
| `friday is not recognized` | Missing `(venv)` — run `.\venv\Scripts\Activate.ps1` |
| `running scripts is disabled` | Run the `Set-ExecutionPolicy` line in Step 2 |
| `FileNotFoundError: 'ollama'` | Ollama isn't installed — see the pre-flight list |
| Something else | Copy the whole PowerShell window and send it to Stephen |

There's also [KNOWN_ISSUES.md](../KNOWN_ISSUES.md), which lists what's broken
and what's untested. It's blunt and it's meant to be — you shouldn't have to
find that out by hitting it.

---

## Other platforms

macOS and Linux run the server, the web interface, the vault and local chat
through Ollama. They **can't** run the tray, the model residency layer, or
protected credential storage — those are Windows-only. Apple Silicon is
explicitly not supported for local models.

Same steps, with these differences:

```bash
python3 -m venv venv
source venv/bin/activate        # instead of .\venv\Scripts\Activate.ps1
pip install -e .
```

A virtual environment is not optional on most current Linux distributions — a
bare `pip install` now refuses to run outside one.

If `friday` isn't found afterwards, `python -m agent_friday.cli` works
identically everywhere.
