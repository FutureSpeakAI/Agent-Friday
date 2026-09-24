# Voice

You can talk to Friday and hear it answer, and you can dictate into any
Windows app with a held key. By default both run entirely on your PC.

## Voice engines

Choose the engine in **Settings → Voice & Tracking**.

| Engine | Where it runs | Notes |
|---|---|---|
| **Local** (default) | This PC's CPU, or the GPU when one is free | faster-whisper for listening, Piper for speaking. Nothing leaves the PC. |
| **Local GPU** | An NVIDIA GPU | NVIDIA NeMo; see [Local voice, GPU tier](local-voice-gpu-tier.md). |
| **Gemini** | Google's cloud | Gemini Live, with natural interruption. Needs a Gemini key. Audio goes to Google. |
| **ElevenLabs**, **Inworld** | The provider's cloud | Cloud voices for speaking. Need that provider's key. |

Local voice needs the voice packages that the Windows installer's
recommended tier installs. The first time you use it, Friday downloads the
speech-recognition model and the voice from Hugging Face if they are not
already on disk. The readiness check in Settings says what is missing and what
to do.

The default local voice is Piper **Amy**. The Piper Lessac voice is no longer
offered, because its training data is licensed for non-commercial research
only; an install that already uses it keeps it. Kokoro is available as an
alternative speaking engine on an NVIDIA GPU.

## Talking to Friday

Press the microphone button in the chat. The browser asks for microphone
access the first time.

The microphone works at `http://localhost:3000` and at the secure local
address `https://agent.<name>`. It does not work at a plain `http://agent.<name>`
address; Friday says so and tells you where it does work.

Voice uses the same tools and the same approvals as typed chat. Anything
outward still asks first.

## Push-to-transcribe (Alt+T)

Hold **Alt+T** anywhere in Windows, speak, and let go. Friday transcribes what
you said on this PC and types it into the window that had focus when you
pressed the key.

- **On by default.** Turn it off, or choose another key and the hold time, in
  **Settings → Voice & Tracking**.
- **Always local.** Dictation uses the local speech recogniser, whatever voice
  engine you chose for conversation.
- **A quick tap is not a hold.** A press shorter than 150 ms is passed through
  to the app as a normal keypress.
- **Your words go where you started.** If focus moves to another window while
  Friday is transcribing and cannot be moved back, the text is left on the
  clipboard instead of being typed into the wrong window.
- **Administrator windows.** Windows does not let a normal app type into a
  window running as administrator. Friday says so, and the text is left on the
  clipboard.
- **System-wide needs the tray.** The system-wide key is run by Friday's tray
  icon, which starts when Friday starts at sign-in (or from
  `Agent Friday (background).cmd` in the install folder). When Friday was
  started from the desktop shortcut, Alt+T works inside Friday's own page.

To see which key is held, the tray uses a Windows keyboard hook. Windows gives
such a hook every key press; Friday ignores everything except its own
shortcut and records nothing.

## Troubleshooting

- **"Opening the microphone…" for a moment.** Windows takes about half a
  second to deliver the first audio sample. Friday shows "Listening" only once
  it is actually recording.
- **Nothing is typed.** Check that the tray icon is running and that the
  shortcut is on in Settings. Check the target window is not running as
  administrator.
- **Local voice is not ready.** Settings → Voice & Tracking shows the
  readiness check and the next step.
