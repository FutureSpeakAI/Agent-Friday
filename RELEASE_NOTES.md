# Agent Friday 5.14.3

*26 September 2026. FutureSpeak.AI*

**A security release.** GitHub's code scanner had flagged 706 possible
weaknesses in Friday's code, and its dependency scanner had flagged 7 in the
libraries Friday uses. Every one has now been looked at. The real problems are
fixed. The rest are recorded, with the reason each is safe, in the project's
security documents. Two approval rules are also stricter: which files Friday
may open without asking, and which pictures she may send to a cloud service.

Full detail is in the [CHANGELOG](CHANGELOG.md); what is not right yet is in
[KNOWN_ISSUES](KNOWN_ISSUES.md).

---

## What was fixed

Most of what the scanners flagged turned out to be safe, but some of it was
real. The most important:

- **Files outside Friday's folders could be deleted or read.** Some requests
  took a name from the caller and used it as part of a file path without
  checking it. A crafted name could delete a folder next to Friday's task or
  project store, or read a file elsewhere in your home folder. Every such name
  is now checked to stay inside the folder it belongs to. Nothing we know of
  used this, and reaching it needed access to Friday itself.
- **Error messages showed internals.** When something failed, some screens
  showed the raw technical error, which could include file paths or pieces of
  a program trace. Screens now say plainly what failed, with a short error
  code; the full details go to Friday's log on your PC under that code. Friday
  herself still sees the real error, so she can explain what went wrong.
- **A web page could steer a fetch.** When Friday reads an article for you, a
  link could redirect her to an address on your own network. Every redirect is
  now checked.
- **A few things were written or logged less carefully than they should be.**
  - Your "never send" list entries could appear in a log file.
  - Sign-in tokens for one kind of connection had a plain-text fallback.
  - Errors that quoted a web address could log a key that was part of that
    address.

  All three are fixed.
- **Some text checks could be made very slow.** A specially written message
  could make certain text checks run for seconds or minutes. They now run in a
  fraction of a second whatever they are given.

## New approval rules

- **Opening files.** Friday opens documents, pictures, music, videos, plain
  text and folders for you straight away. Anything else asks first, with an
  approval card that says where the request came from, and never runs quietly.
  That includes programs, scripts, shortcuts, and web pages saved as files.
  If something Friday read (an email, a web page) tries to get her to open a
  program, the card makes that visible.
- **Pictures for video and music.** When Friday makes a video or music from a
  picture, she uses pictures from her own creations folder, or one you named
  yourself in the conversation. Any other picture needs your approval first,
  because it would be uploaded to a cloud generation service.

## Other changes

- **Local news is yours to set.** Friday no longer ships with one city's local
  news outlets built in. To keep a Local section in your briefing, open News ›
  Customize Briefing › Local beat and enter your city and the local outlets you
  trust.
- **The GPU voice option installs from Settings.** The optional NVIDIA voice
  tier is no longer part of Friday's standard dependency list. Settings installs
  it, pinned to a tested version, when you ask for it. Nothing changes for you
  unless you use it.
- **Task results come back to your chat.** When a task you started from a chat
  needs your approval, the result now appears in that chat once you approve.
- **An honest health line.** A privacy layer that is off on purpose is no longer
  reported as broken. One that should be running and is not still is.

## Security

Four ChromaDB advisories have no fix yet. Friday uses ChromaDB only inside the
app and never runs the server they affect, so they cannot be reached; see
[docs/security/dependency-advisories.md](docs/security/dependency-advisories.md).

## Upgrade notes

- **Your data and your vault passphrase are preserved.** Run the new installer
  over the old one. [Updating](docs/user-guide/updating-and-uninstalling.md)
- **If you used the Local news section**, set your city and outlets in News ›
  Customize Briefing › Local beat after upgrading.

## Known issues

The most likely to affect you: moving your `.friday` folder to another PC
holds outward actions until you re-confirm the rules in Settings. All known
issues, with workarounds, are in [KNOWN_ISSUES.md](KNOWN_ISSUES.md).
