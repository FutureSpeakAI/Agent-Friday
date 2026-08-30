# Agent Friday v5.8.0

*2026-08-30 · FutureSpeak.AI*

**This is the last release you have to find by hand.**

Until now there was no way for Friday to tell you a new version existed. You
found out because someone told you, or you thought to check. From this release
on, Friday checks once a week and says so.

If you are reading this because someone sent you the link: install it, and you
will not need the link next time.

---

## 1. Friday now tells you when there is a newer version

Once a week, Friday asks GitHub whether a newer stable release has been
published. If there is one, you get a notification with a link to it. That is
the entire feature.

**Friday never downloads or installs anything by itself.** You click through,
download the zip, and run the installer when it suits you — the same way you
always have. A notification is not an update.

**It sends nothing about you.** This is the part that took the most care, so it
is worth being precise about what happens on the wire:

- It is a single unauthenticated `GET` of a public GitHub URL — the same
  request your browser makes if you visit the releases page yourself.
- No install identifier. No usage data. No machine name, user name, or file
  paths.
- **Not even which version you are running.** The comparison happens on your
  machine, against the list GitHub returns. Nothing is sent to be compared
  against.
- No custom `User-Agent`. Anything Friday added there — a version, a build id —
  would be a fingerprint we chose to transmit, so there isn't one.

Friday ships no telemetry, and this feature did not become the exception. There
is a test that reconstructs the entire outgoing request — URL, query string,
headers, body — and fails the build if it contains anything that could identify
your install. There is a second test that feeds that check a request which
*does* leak, and fails if the check misses it, so it cannot quietly rot into a
formality.

**If your network is down, you will not hear about it.** Offline, DNS trouble,
GitHub rate-limiting or having a bad day: all of it is logged and none of it is
shown to you. A tool that nags because the wifi is off is a bad houseguest.

**You will not be told twice about the same release.** One notification per
version, dismissible, and it stays gone until there is something newer.

Turn it off in **Settings → About → Updates**. The same panel shows the version
you are running, when the last check happened, and a **Check now** button.

### A note if you are upgrading rather than installing fresh

The weekly check arrives **switched on**, including on installs that already
existed before this release.

That was a judgement call and you are entitled to disagree with it. The
reasoning: a sovereignty tool whose users silently miss security fixes is worse
off than one that asks GitHub a question with no answer in it. The check sends
nothing, and it is one click to turn off. But nobody who installed 5.7.0 asked
for a new outbound request, so it is said here plainly rather than left to be
discovered.

---

## 2. `friday status` now tells you which version you are actually running

Run `friday status` and the first thing it reports is the version — read from
the files on disk, not from what the installer wrote down.

That distinction is not pedantry. Installers before 5.6.5 short-circuited the
step that copies Friday's files, so upgrades from 5.6.0 through 5.6.4 replaced
nothing while still recording the new version number in
`install-manifest.json`. Anyone who upgraded in that window has been running
older code than their version number claims.

**If the files on disk and the manifest disagree, Friday now says so** — in
`friday status` and in Settings → About — and tells you to re-run the latest
installer to repair it.

This matters more now than it did yesterday, because the update check is built
on top of it. A checker that trusted the manifest would have told exactly those
users they were up to date. Telling someone they are current when they are not
is worse than telling them nothing, because they stop looking.

Internally there were three different pieces of code answering "what version is
this?", and one of them fell back to a hardcoded number when it could not read
the file — so an install that did not know its own version reported one it had
invented. There is now one implementation, unknown stays unknown, and a test
fails the build if a fourth one appears.

---

## 3. Fixes that had been waiting on a working CI

The test pipeline had been red since 2026-07-04 and was running zero tests. It
was repaired just before this release, and immediately found two real defects on
Windows — the platform Friday actually runs on:

- **Concurrent settings saves could corrupt `settings.json`.** The atomic write
  built its temporary file from one shared name, so two writers — Friday saves
  settings from background threads as well as from the UI — could use the same
  temp path, and one could publish a file the other was still filling. That is
  the exact corruption the atomic write existed to prevent. Every write now gets
  its own temp file.

- **Saving settings could return a 500 for no good reason.** On Windows the
  final rename fails while any other process holds the file open — a background
  reader, the search indexer, antivirus. It now retries briefly. The file is
  complete and flushed to disk before the first attempt, so a retry can never
  publish a partial file.

- **Disk speed measured as "unavailable" on the fastest disks.** The read was
  timed with a clock whose resolution on Windows is about 15.6 ms; a cached 1 MiB
  read finishes well inside that, so the elapsed time came out as exactly zero,
  tripped the guard for an impossible measurement, and reported failure. The
  model-load-time estimate that depends on it fell back to a guess. It now uses a
  monotonic nanosecond clock.

---

## Upgrading

Download `AgentFriday-Setup-5.8.0.zip`, unzip it anywhere, and run
**Install Agent Friday.cmd**.

Your notes, settings, vault and connected accounts are kept — the installer
replaces Friday's own files and nothing under `~/.friday`.

If you upgraded in place before 5.6.5, re-running this installer also repairs an
install whose files were never actually replaced. `friday status` will tell you
afterwards whether the version on disk and the manifest finally agree.
