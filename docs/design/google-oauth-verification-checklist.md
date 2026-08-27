# Google OAuth: what to publish, and what verification needs from Stephen

Status: **mechanism built, credential not minted.** Nothing has been submitted
to Google, and nothing will be without Stephen doing it himself.
Written 2026-08-26. Supersedes the options section of
`docs/design/google-oauth-onboarding.md`, which posed the question this answers.

Decision taken: **ship a client AND keep bring-your-own, both first-class.**

---

## 1. Publish in "In production", not "Testing"

This is not a close call, and it is the one fact worth checking before
anything else is built on top of it.

| | Testing | **In production** (unverified) |
|---|---|---|
| Refresh tokens | **expire after 7 days** | no 7-day expiry |
| Who can connect | 100 users, **each email added by hand** in the console | anyone with a Google account |
| Cap | 100 listed testers | 100 **new users, lifetime**, not resettable |
| Warning screen | yes | yes |

Google's wording, from the OAuth 2.0 documentation:

> A Google Cloud Platform project with an OAuth consent screen configured for
> an external user type and a publishing status of "Testing" is issued a
> refresh token expiring in 7 days.

**Testing mode is disqualifying twice over.** Stephen would have to add every
single user's email address to a list in his own Cloud Console before they
could connect — Janet included — and every one of them would silently lose the
connection a week later and have to reconnect. Either alone is far worse than
a warning screen the user was told to expect.

So: **In production, unverified, to start.** The warning screen and the
100-new-user lifetime cap are the accepted costs. Verification (§4) removes
both.

### The cap is the reason BYO is load-bearing

100 new users **over the lifetime of the project**, and the counter cannot be
reset. That is not a soft limit that grows — it is a wall. When it is hit, the
shared client stops being able to connect anybody at all, and the only way in
is a client the user made themselves. That is why bring-your-own is built as
an equal path with a guided flow, and why the failure at the cap hands over to
it explicitly rather than showing an OAuth error.

---

## 2. Which of Friday's scopes need what

Checked against Google's published restricted list on 2026-08-26. The
restricted list covers **Gmail, Drive, Fit, Chat, Data Portability, Photos
Ambient and Health** — nothing else.

| Scope | Tier | Why it matters |
|---|---|---|
| `gmail.readonly` | **RESTRICTED** | verification **+ annual CASA security assessment** |
| `drive.readonly` | **RESTRICTED** | same |
| `calendar` (read **and write**) | sensitive | review only, free |
| `tasks` (read **and write**) | sensitive | review only, free |
| `contacts.readonly` | sensitive | review only, free |
| `documents.readonly`, `spreadsheets.readonly` | sensitive | review only, free |
| `userinfo.email` | neither | no review |

Two things worth stating plainly because both contradict a reasonable guess:

**The housekeeper's write scopes do not make this worse.** Calendar write and
Tasks write are *sensitive*, not restricted. Nothing about that work needs
slowing down for verification reasons.

**`gmail.send` is not restricted.** Google's restricted Gmail list is exactly
eight scopes — `mail.google.com/`, `gmail.readonly`, `gmail.metadata`,
`gmail.modify`, `gmail.insert`, `gmail.compose`, `gmail.settings.basic`,
`gmail.settings.sharing` — and `gmail.send` is not among them. Friday could
*send* mail in the sensitive tier. It is *reading* mail that is expensive.

That gives a real staging option if the assessment cost lands badly: drop
`gmail.readonly` and `drive.readonly`, keep everything else including Gmail
*send*, and Friday becomes a sensitive-only app — weeks of free review, no
CASA, no annual fee. The cost is Gmail read (briefings, email search) and
whole-Drive search, both genuinely load-bearing. It is a trade, not a freebie.

---

## 3. What verification costs

* **Sensitive scopes**: Google review. Weeks. No fee.
* **Restricted scopes**: the above **plus an annual third-party security
  assessment (CASA)** by a Google-empanelled assessor, re-certified every 12
  months. Assessors are commonly reported at **$15,000–$75,000/year**; Google
  publishes no figure.

**The unresolved question is still unresolved, and it is worth up to $75k a
year.** Google's restricted-scope page scopes assessment to apps handling
restricted data *"from or through a third-party server"* — Friday has no
server — while the Security Assessment page says flatly that all
restricted-scope apps must be assessed annually. Ask Google directly, in
writing, before committing to the restricted tier. One email.

---

## 4. The submission checklist — what only Stephen can supply

Everything below is his to produce. None of it can be automated and none of it
should be submitted on his behalf.

### 4a. Before anything else — mint the client

1. Create the Google Cloud project that will own Friday's public identity.
2. Configure the OAuth consent screen: **External**, app name **Agent Friday**,
   logo, support email, developer contact email.
3. Set Publishing status to **In production** (§1).
4. Create credentials → OAuth client ID → **Desktop app**. Web will not work;
   it produces `redirect_uri_mismatch` over the loopback redirect.
5. Paste the two values into `BUNDLED_CLIENT_ID` / `BUNDLED_CLIENT_SECRET` in
   `src/agent_friday/services/google_oauth_client.py`. They are public on
   purpose — see `THREAT_MODEL.md` §4 and the module docstring. The secret
   scanner already allowlists both **by name, with the reasoning attached**.

Until step 5, everything ships inert: Friday behaves exactly as it does today
and falls through to bring-your-own.

### 4b. Assets the review requires

- [ ] **A homepage on a domain Stephen controls.** Must describe what Friday
      is and be reachable publicly. `futurespeak.ai` presumably.
- [ ] **A privacy policy**, hosted on **the same domain as the homepage**,
      linked from the consent screen. It must state specifically how Friday
      accesses, uses, stores and shares Google user data. Friday's actual
      answer is unusually good here and should be said plainly: data stays on
      the user's machine, tokens are encrypted at rest, nothing transits a
      FutureSpeak server.
- [ ] **Domain ownership verified** in Google Search Console for that domain.
- [ ] **App name, logo and support email on the consent screen must match the
      real, public-facing product.** Reviewers compare the live consent screen
      against the submission.

### 4c. The demo video — the most common rejection

Hosted on YouTube (**Unlisted** is fine) or Drive, and it must show:

- [ ] The **end-to-end flow**, including the OAuth grant, in the app being
      submitted — not a mock-up and not a different build.
- [ ] The **full consent screen**, with the language toggle at the bottom-left
      **set to English**.
- [ ] **Every requested scope visible** on that consent screen.
- [ ] **Each scope actually being used** in the app afterwards. Not "Friday can
      read mail" — show a briefing being generated from real mail, a calendar
      event being read, a task being written.

Google's own list of why videos get rejected: the link is not accessible; the
video shows a different app; the consent workflow is not shown; the video does
not demonstrate how the scopes are used. Three of those four are avoidable by
recording carefully once.

### 4d. Scope justifications

One per sensitive/restricted scope, each tied to **a concrete feature**, plus
an explanation of **why a narrower scope will not do**. Google explicitly
rejects justifications that do not tie a scope to a feature. Drafts:

| Scope | Feature it powers | Why not narrower |
|---|---|---|
| `gmail.readonly` | daily briefing; "what did X say about Y"; the housekeeper's triage | `gmail.metadata` carries no bodies, so summarising is impossible |
| `calendar` | reading the day's schedule **and** creating/moving events on request | `calendar.readonly` cannot create the events the user asks for |
| `tasks` | completing and creating tasks by voice | `tasks.readonly` cannot complete a task |
| `drive.readonly` | `search_drive`, `read_doc_or_sheet` | `drive.file` only sees files the app itself created, so it cannot search an existing Drive |
| `documents.readonly` / `spreadsheets.readonly` | reading a named doc or sheet into context | no narrower read scope exists |
| `contacts.readonly` | resolving "email Janet" to an address | no narrower scope exists |

Stephen should rewrite these in his own voice before submitting — a reviewer
reading obviously-generated text is a bad first impression.

---

## 5. Suggested order

1. **Send the CASA question** (§3). It gates the tier decision and costs one
   email.
2. **Mint the client** (§4a) and paste it in. This alone gets Janet, and the
   next 99 people, connecting in one click.
3. **Homepage + privacy policy + domain verification** (§4b). Slow-moving,
   needed regardless, and useful independently.
4. **Submit for sensitive scopes.** Free, weeks, removes the warning screen and
   the cap for everything except Gmail and Drive.
5. **Decide on restricted** once §3 has an answer.

Steps 2 and 3 are independent — the client works unverified from the day it is
pasted in, and verification catches up behind it.
