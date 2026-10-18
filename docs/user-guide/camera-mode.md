# Camera mode

Camera mode lets you show Friday your screen, your webcam, or both, while you
chat. Friday takes a picture every few seconds and keeps it in the page.
Nothing is sent until you type a message.

## Turning it on

Click the camera button (📷) in the top bar. If the top bar is narrow, the
button may be under **More Friday controls**.

Camera mode starts with **screen sharing**, not the webcam. Your browser asks
which screen or window to share. A small picture appears, marked **LIVE**,
with the **CAMERA MODE** panel beside it:

- **Source**: **screen**, **webcam** or **both**.
- **Facing**: **front** or **rear**, for the webcam. The webcam picture also
  has a **⇄** button to flip between them.
- **Camera is active**, shown the whole time Camera mode is on.
- **Frame every 3s**: how often a picture is taken. The default is 3 seconds;
  set `camera_interval_sec` to 1, 3 or 5 (see
  [Configuration](configuration.md)).
- **Stop**.

You can drag the picture around the screen. While Camera mode is on,
left-click the camera button to show or hide the panel.

## Turning it off

Any of these turns Camera mode off:

- **Stop** in the panel.
- The **×** on the picture.
- Right-click the camera button.
- Ending the screen share in your browser.

Turning it off releases the camera and the screen share, and drops the last
picture.

## What is sent, and when

- Pictures are JPEG, at most 640 pixels wide. They stay in the page.
- Nothing is sent while you are not typing.
- Each message you send while Camera mode is on carries the latest picture,
  once.
- Messages you send while Camera mode is off carry no picture.
- With **both** sources on, only the screen picture is sent.

## Who sees the picture

The chat model never receives the picture. Friday's server has it described,
and adds the description to that one message only. The description is not
used again for later messages, and the picture is not stored in the
conversation.

Who describes it depends on your routing mode in **Settings > Models** (see
[Privacy](privacy.md)):

| Mode | Who describes the picture |
|---|---|
| **Local only** | Your local model. If it cannot, nothing is sent, and the reply says "🔒 I did not describe that image. Local only is on and the local seat could not do it", with the reason. |
| **Local preferred** | Your local model first. If it cannot, Google Gemini. |
| **Cloud only** | Google Gemini. |

When the picture goes to Gemini, the conversation says "👁 The camera frame
went to Google (Gemini) to be described. Switch to Local only in Settings →
Models to keep images on this machine." The send is recorded in the egress log
(`.friday\vault\egress-log.jsonl`), and so is a picture Local only kept back.

If no Google Gemini key is connected, nothing is sent and Friday says a key is
missing. You can add one in **Settings > Connections**.

## If you refuse permission

If you refuse the screen share or the webcam, Camera mode switches itself off.
With **both** sources, if you refuse the webcam, the screen keeps going and the
webcam picture shows the error.

Related: [See & Touch](see-and-touch.md), [Privacy](privacy.md),
[Configuration](configuration.md).
