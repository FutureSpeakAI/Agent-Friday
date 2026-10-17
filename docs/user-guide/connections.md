# Connect your services

**Connect your services** is the one screen for adding what Agent Friday™ can
use: AI model providers, creative and media tools, search, your own accounts,
and MCP servers. You see it in two places:

- in the setup chat, at the **Connect** step;
- at the top of **Settings > Connections**, any time later.

(If you set Friday up in the terminal, the same list is step 5 of the terminal
wizard.)

**Every entry is optional, and nothing is ticked, filled or connected for
you.** You need no key at all to use Friday with a local model.

## The categories

| Category | What is in it |
|---|---|
| **AI model providers** | Anthropic, OpenRouter, Google Gemini, Groq, Mistral, xAI, DeepSeek, Together and others; Ollama and LM Studio on this PC; and any service that speaks the OpenAI API, by its address. One provider is enough. |
| **Creative and media** | Higgsfield (sign in with your Higgsfield account), kie.ai, and the ElevenLabs and Inworld voices. |
| **Search and web** | Brave Search and Firecrawl. Without them Friday uses a free search that works, more roughly. |
| **Accounts** | Your Google accounts (Gmail, Calendar, Drive and more), GitHub, Slack and Discord tokens, and Notion and Linear through their own sign-in. |
| **Add any MCP server** | MCP is an open standard for plugging tools into AI assistants. Add a server by its `https://` address or by the command that starts it, or pick one from a short list. |
| **More** | Phone, social and publishing accounts, messaging apps and network. |

Some services people ask for are listed as **coming soon**, with no key field
and no sign-up link, so nobody pays for a key Friday cannot use yet:
Replicate, fal, Stability AI, Runway, Tavily, Exa (as search; Exa's own MCP
server can be added today), Microsoft (Outlook, Calendar, OneDrive) and
Spotify.

## What each card shows

- **How to connect**: three short steps, and a button to the vendor's own page
  to get a key or sign up. Every link was checked against the vendor's site.
- **Cost**, only where the vendor states it (a free tier, prepaid credit, paid
  per use). Otherwise the card says to see their pricing page.
- **Recommended**, on a few cards, always with a reason (for example
  Anthropic: "Friday's tools work best with Claude"). It is a label, never a
  choice made for you.
- **What it can access**, in plain words. For a model provider: it gets the
  messages and context Friday sends it for the turns that run there, your
  never-send list is checked first every time, and it cannot open your files or
  mail by itself. For search: the search words and addresses, never your
  conversations. For a voice: only the text you choose to have spoken.
- A **masked key field**. Paste the key, press **Save securely**, then **Test
  connection**.

The key is stored encrypted on this PC. It is never shown back to you, never
written to a log, and never goes into a conversation. If a provider ships
switched off, saving a key for it switches it on.

Accounts such as Google, Notion, Linear and Higgsfield connect through the
service's **own sign-in screen** in your browser. For Google, sending mail and
changing your mailbox are separate permissions that start off; tick them only
if you want them.

## Test connection

**Test connection** checks the card and answers in one of four ways:

| Result | What it means |
|---|---|
| **Test passed** | The service answered with this key or sign-in. |
| **Test failed** | The service refused, or nothing answered. A **How to fix** line says what to do: copy a fresh key, add credit, start Ollama, check the address ends in `/v1`, sign in again. |
| **Not connected yet** | Nothing is saved for this service yet. |
| **Could not tell** | Friday could not reach the service to check. That is not a verdict on the key; check the internet connection and try again. |

A test sends one small request to that service and nothing else.

## "I'll just use local AI"

If you want no cloud keys at all, press **I'll just use local AI**. Friday
then stops suggesting cloud keys, and setup moves on without asking for one.

This button **does not change any privacy or routing setting**. Where Friday
may think is the separate privacy question in the setup chat and in **Settings
> Privacy & Data > Cloud consent** and **Settings > Models**. To undo the
button, press **Show cloud options again**. You can still connect anything at
any time.

## By voice

Say "connect OpenRouter" (or another service's name) and Settings opens on
that card with its key field ready. Voice never types or submits a key: you
paste it yourself. Adding an MCP server by command needs your own click on the
screen; it is never done by voice.

## A quiet fresh install

On a fresh install, services you have never connected show **not connected**
in grey and stay silent. There are no start-up warnings about them. Scheduled
jobs do not post notices until setup is finished; their runs are still
recorded.

## MCP servers

- **By address**: give it a short name and its `https://` address. Leave keys
  out of the address; if the server needs a key, add it in the secure field.
  A server on this PC or your own network may use `http://`.
- **By command**: paste the command that starts it. It runs on this PC as you,
  so add only servers you trust.
- Anything an MCP server does that is not a plain read (sending, posting,
  changing or deleting) asks you first on a card.

## Related

- [The setup chat](setup-chat.md)
- [Privacy: local and cloud](privacy.md)
- [Troubleshooting: a service says "not connected"](troubleshooting.md#a-service-says-not-connected-or-test-connection-fails)
- [Configuration: provider keys](configuration.md#provider-keys)
