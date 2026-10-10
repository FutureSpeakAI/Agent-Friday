# Voice

You can talk to Agent Friday™ and hear it answer, and you can dictate into any
Windows app with a held key. Voice uses the same tools and the same approvals
as typed chat, so anything outward still asks first.

## Cloud voice and local voice

Choose the mode in **Settings > Voice > Voice engine**. Cloud modes show what
leaves your PC, how long the provider keeps it and what it costs before the
switch takes effect.

| Mode | Where it runs | What leaves your PC |
|---|---|---|
| **Local** (default) | Your PC. Listening, thinking and speaking all happen here. | Nothing. |
| **Cloud (Gemini Live)** | Google's cloud. One speech-to-speech model hears, thinks and speaks. Needs a Gemini key. | Your microphone audio and the session context, after Friday's privacy gate. Questions about your own notes and memory are answered by Friday's local model, and only the answer is relayed. |
| **Local listening, cloud voice** (ElevenLabs) | Friday hears and thinks on your PC, and the provider speaks. Needs that provider's key. | Only the reply text. |
| **Local GPU (NeMo, experimental)** | An NVIDIA graphics card. | Nothing. See [Local voice models and the GPU option](local-voice-gpu-tier.md). |

A mode that cannot be used right now is greyed out, and the reason is shown.
Inworld appears as a voice provider but cannot be selected, because its
commercial-use terms could not be established.

## Local voice models

**Settings > Voice > Local voice models** lists every downloadable piece of
local voice. The list is shown in every voice mode, cloud included, so you can
prepare local voice before you need it.

- **Size is shown.** Each row shows its download size, and the confirmation adds
  up any other piece the row needs.
- **It asks first.** Nothing downloads until you choose **Download** and
  confirm. The confirmation names the size, the publisher and the licence.
- **Pinned and verified.** Files come from a fixed release or revision, and
  each is checked against a fixed SHA-256 checksum. A file that does not match
  is deleted and never used.
- **Resumable.** **Continue download** picks up where a stopped download left
  off, and **Stop** cancels a running one.

The pieces are the streaming speech recogniser (the ear), a voice-activity
detector, the fast responder models, and pronunciation helpers for the
Kokoro voice. [Local voice models and the GPU option](local-voice-gpu-tier.md)
lists each piece with its size.

### The fast responder

Local voice answers live turns with a small, quick model called the fast
responder, so you are not waiting on the large model. It has the same voice
tools as cloud voice. When a request needs more thought or your private notes,
it hands the work to Friday's main model and speaks the result.

There are three fast responder models:

- **Ternary Bonsai 1.7B** is the recommended one (about 442 MB). It is
  PrismML's ternary build of Qwen3-1.7B and stays loaded beside the large
  model. It runs on the PrismML runtime, which comes with a Bonsai deep thinker.
- **Qwen3 4B Instruct** gives the best quality of the Qwen3 models. While you
  talk, the large model steps aside, because there is not room for both.
- **Qwen3 1.7B** is smaller and stays loaded beside the large model.

You choose one on the installer's model page, and the first-run download fetches
it. The setting `voice_front_model` defaults to `auto`, which picks the first
model that can run on this PC, in the order above; a model you choose yourself
wins. If the PrismML runtime is missing, Friday answers with a Qwen3 model that
is installed and says so, and Settings > Models shows why Bonsai cannot be
picked. If no fast responder is installed, the main model answers voice turns
itself, which is slower.

#### How a voice turn is routed

A fast on-device classifier, Friday's quick judgement, decides whether a
request needs a look-up (calendar, email, files, the wiki, past conversations,
news or the web) or one of Friday's own actions (open a workspace, play a
podcast or media, voice preferences, stop a running task, undo). Friday runs it
and the fast responder only speaks the answer. A look-up turn speaks a short
acknowledgement first.

- Anything that changes the world outside Friday (send, reply, delete, create an
  event) goes to the full agent and through the usual approval card.
- A web or news search that Friday guessed at asks before it searches.
- A spoken "yes" or "no" answers an approval card only when Friday has just read
  that card aloud in the same call.
- Text that Friday looks up is treated as data, never as instructions.

On our test PC (a 12 GB NVIDIA card, with the Kokoro voice on the GPU), a
conversational reply starts about 0.1 seconds after you stop speaking (median),
and a look-up answer starts about 0.4 seconds after the acknowledgement. Other
computers will differ.

## Talking to Friday

Press the microphone button in the chat. The browser asks for microphone access
the first time.

The microphone works at `http://localhost:3000` and at the secure local address
`https://agent.<name>`. It does not work at a plain `http://agent.<name>`
address; Friday says so and tells you where it does work.

### Talking over Friday

**Settings > Voice > Listening > Interrupting Friday** has two choices.

- **Talk over her** (default). In local voice, Friday stops once you are clearly
  louder than her own voice coming back through the microphone, so open
  speakers work. In Gemini Live, she stops when you start talking, which works
  best with headphones.
- **Esc only** in local voice, or **Speaker-safe** in Gemini Live. In local
  voice, talking over her does nothing. In Gemini Live, she stops only when you
  are clearly louder than the echo.

**Esc** always stops her.

In local voice, **Pause before Friday replies** sets how long a silence counts
as the end of what you said, from 300 ms to 2000 ms (500 ms by default).

### Checking that voice works

**Voice readiness** shows three stages: ear, mind and mouth. A stage turns green
only after it has actually heard, thought or spoken in the last 15 minutes.
**Check voice now** tests all three. Voice models unload from memory after ten
idle minutes.

**Speech recognition** and **Speaking voice engine** each have a graphics card
policy: **Never**, **If free** (the default) or **Required**. Piper is the
default speaking voice and runs on the processor. Kokoro sounds more natural
and needs an NVIDIA card, or the processor opt-in, which is slower. The Piper
voice is Amy.

## Depth, pace and personality

In **Settings > Voice > Conversation style**, **Answer depth** (Adaptive,
Concise or Detailed) and **Speaking pace** (Adaptive, Measured, Natural or
Brisk) apply from your next voice session. Adaptive depth lets a simple answer
be brief and a complex one develop. Adaptive pace leaves more room around dense
ideas, names and numbers.

During a call you can say "slow down" or "keep it brief". Those adjustments
belong to that call. Ask for a lasting default explicitly, or change it in
Settings.

Voice can also create, inspect and run [workflows](workflows.md), including
their sources, timing and notification choice. A started workflow continues in
the background while you keep talking.

## Push-to-transcribe (Alt+T)

Hold **Alt+T** anywhere in Windows, speak, and let go. Friday transcribes what
you said on this PC and types it into the window that had focus when you
pressed the key.

- **On by default.** Turn it off, choose another shortcut, and set the hold time
  in **Settings > Voice**. The offered shortcuts are Alt+T, Ctrl+Shift+Space,
  Ctrl+Alt+D and Alt+Grave, and you can type your own. Alt+F is not offered
  because it opens the browser menu.
- **Always local.** Dictation uses the local speech recogniser, whatever voice
  mode you chose for conversation. No audio or transcript leaves your PC.
- **A quick tap is not a hold.** A press shorter than the hold time (100, 150,
  250 or 400 ms; 150 ms by default) is passed through to the app as a normal
  keypress.
- **Esc discards.** Press Esc while it is listening to throw the recording away.
- **Your words go where you started.** If focus moves while Friday is
  transcribing and cannot be moved back, the text is left on the clipboard
  instead of being typed into the wrong window.
- **Administrator windows.** Windows does not let a normal app type into a
  window running as administrator. Friday says so, and the text is left on the
  clipboard.
- **System-wide needs the tray.** The system-wide key is run by Friday's tray
  icon, which starts when Friday starts at sign-in, or from
  `Agent Friday (background).cmd` in the install folder.

Noticing a held key needs a Windows keyboard hook, which receives every key
press. Friday compares each key with your shortcut and ignores the rest, keeps
no keystroke history, and discards the recording as soon as it is transcribed.
Turning Push-to-Transcribe off removes the hook.

## Troubleshooting

- **"Opening the microphone..." for a moment.** Windows takes a fraction of a
  second to deliver the first audio sample. Friday shows that message until it
  is recording.
- **Nothing is typed.** Check that the tray icon is running and that the
  shortcut is on in Settings > Voice. Check the target window is not running as
  administrator.
- **Local voice is not ready.** **Voice readiness** in Settings > Voice shows
  the failing stage and the next step.
