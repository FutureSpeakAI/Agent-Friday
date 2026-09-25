# Background Network Activity

> Status: current for 5.14.0. Last verified against the code: 2026-09-24.

This page lists every network connection Agent Friday makes on its own, without
you asking for something in the moment: what it connects to, how often, what it
sends, and how to turn it off. Requests you cause directly (a chat with a cloud
model, a web search, sending an email) are not listed here; they go through the
egress gate and, for outward actions, the approval checkpoint.

Friday contains **no telemetry, analytics, crash reporting or license check**,
and it never contacts FutureSpeak.AI. None of the connections below carries
conversation content, vault data or personal details.

---

## Summary

| What | Destination | When | Turn it off |
|---|---|---|---|
| Update check (opt-in) | `api.github.com` | At most once a week, only if you said yes | First-run question, or Settings → About |
| Connectivity probe | Nothing by default (a routing-table lookup on this PC); `dns.google`, `8.8.8.8`, `1.1.1.1` (TCP 443) only if you opt in | Every 30 seconds | `network_probe` in `settings.json` |
| News feeds | Built-in RSS feeds (news sites, Google News) | Every 5 minutes | Turn news categories off in the News workspace |
| Web fonts | `fonts.googleapis.com`, `fonts.gstatic.com` | Every page load | Not configurable yet |
| MediaPipe scripts | `cdn.jsdelivr.net` | Every page load; model files only when tracking is on | Tracking off stops the model downloads; the scripts still load |
| Embedding model | `huggingface.co` | Never at startup. Once, the first time a feature needs it and it is not already on disk, with a notification | Pre-fetched by the installer's memory tier |
| Local voice models | `huggingface.co` | First use of local voice, if not already downloaded | Leave local voice off |
| Connector health | Your connected services (Google, MCP servers) | About every 2 minutes | Disconnect the connector |
| Scheduled jobs | Your model provider, feeds, git remotes | Per schedule | Workflows workspace |
| Phone (off by default) | Twilio API | Only when the phone is on | Off unless you turn it on |

---

## 1. Update check (opt-in)

**What it does:** Asks GitHub for the list of published Agent Friday releases
and, if a newer version exists, shows you a notification with a link.

**How often:** The scheduler looks every 6 hours, but a request is made only
when the last successful check was at least 7 days ago.

**Data sent:** One HTTPS GET to
`https://api.github.com/repos/FutureSpeakAI/Agent-Friday/releases` with only an
`Accept` header: no account, no key, no version, no identifier. GitHub sees the
IP address the request came from, as it would for any web page.

**What it never does:** Download or install anything. Updating means running
the new installer, when you choose to.

**On or off:** First-run setup asks, and an unanswered install stays off. You
can change your answer in Settings → About.

## 2. Connectivity probe

**What it does:** Every 30 seconds (first check about 5 seconds after startup)
Friday decides whether this PC is online. The `network_probe` setting chooses
how:

- `"route"` (the default): asks this PC's routing table whether it has a
  network route. It "connects" a UDP socket to a reserved documentation
  address, which sends no packet. **Nothing leaves the machine.** It notices a
  cable unplugged, Wi-Fi off or airplane mode; it does not notice a captive
  portal or an outage further upstream.
- `"internet"` (opt-in): opens a TCP connection to port 443 of `dns.google`,
  falling back to `8.8.8.8` and then `1.1.1.1`. A bare handshake with no
  payload, but it reveals your IP address to Google or Cloudflare, and that
  Friday is running.
- `"off"`: no check; Friday treats the PC as online.

**Why:** It drives the offline badge and, if `offline_auto_local` is on, the
switch to a local model while offline.

**Turn it off or change it:** set `"network_probe"` in `settings.json` while
Friday is stopped. Setting `offline_auto_local` to `false` stops the automatic
switch to local models.

## 3. News feeds

**What it does:** Fetches Friday's built-in RSS feeds (general news sites and
Google News, by category) every 5 minutes, starting about 12 seconds after
startup, to keep the News workspace current. If you have added a Brave Search
key, it may also be used as a fallback source.

**Data sent:** Ordinary HTTP GET requests to each feed. No identifiers and
nothing from your conversations.

**Turn it off:** Turn categories off in the News workspace's preferences
(`categories_enabled`). Clearing `news_priorities` does not stop the archiver.

## 4. Web fonts and MediaPipe

`index.html` loads its typefaces from Google Fonts and three MediaPipe scripts
(for head and hand tracking) from `cdn.jsdelivr.net` on every page load. The
script tags carry integrity hashes. The tracking model files are fetched from
jsdelivr only when you turn tracking on in Settings → Voice & Tracking.
Three.js and the rest of the interface are served locally.

These requests reveal your IP address to Google and jsDelivr. Serving them
locally is not done yet; see [KNOWN_ISSUES.md](../../KNOWN_ISSUES.md).

## 5. Model downloads on first use

- The embedding model (`all-MiniLM-L6-v2`, about 90 MB), used by the privacy
  classifier, conversation memory and context ranking, is loaded at startup
  only if it is already on disk. Startup never downloads it. If it is missing,
  the first feature that needs it (in practice the first chat message)
  downloads it once from `huggingface.co`, and Friday shows a notification
  before the download starts and when it finishes or fails. The Windows
  installer's memory tier fetches it ahead of time, so an installed PC
  normally never makes this request.
- Local voice downloads its speech-recognition and voice models from Hugging
  Face the first time you use it, if they are not already on disk.
- Local chat models are downloaded only when you ask for one (installer,
  Hardware Check, or Settings → Models).

## 6. Connector health

Roughly every 2 minutes Friday checks that the services you connected still
work: a token-validity check for Google (no mail or calendar content) and a
reachability check for MCP servers. A standing credential sweep also runs
locally. Disconnect a connector in Settings → Accounts & Keys to stop its check.

MCP servers you configure in `~/.friday/mcp_servers.json` are local processes
that Friday talks to over stdio, but a server may make its own network calls.
That depends entirely on the server.

## 7. Scheduled jobs

The scheduler ticks every 60 seconds and runs jobs at their configured times:
briefings, the heartbeat, daily creation, repo sync and others. What a job
sends depends on the job and on the seat it runs on. The costly built-in jobs
run on the local seat by default. Manage jobs in the Workflows workspace; the
list is stored in `~/.friday/schedules.json`. An outward action inside a
scheduled job still needs a grant or an approval card.

## 8. Phone

Off by default. When you turn it on, Friday talks to Twilio's API to send
approved texts and calls, to fetch prices, and to delete message bodies and
recordings once delivered. The inbound listener binds to `127.0.0.1:3011` only;
see [Phone](phone.md).

## Purely local work

These run in the background with no network activity: predictive pre-warming
of workspaces, provider-key bootstrap (a local decryption step), the task
watchdog and boot reconciliation.
