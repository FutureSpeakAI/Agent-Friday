# Google OAuth onboarding — the wall every new user hits

Status: **decision required from Stephen**. Nothing here is built.
Written 2026-08-26, after Friday's first install on a second person's machine.

---

## 1. What Janet saw

She installed Friday, opened Settings, and tried to connect her Google
account. She got this:

> No Google OAuth client found. Place a Desktop OAuth client JSON at
> `~/.friday/credentials.json` or `~/.gmail-mcp/oauth-keys.json`, or set
> `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.

`routes/google.py:60`, and again at `services/google_accounts.py:1015`.

Stephen: *"New users must never experience this."* He is right, and it is
worth being exact about how far the wall goes. That message asks her to:

1. know what an OAuth client is,
2. create a Google Cloud project,
3. configure a consent screen,
4. create a Desktop OAuth client,
5. download the JSON,
6. know where `~/.friday` is on Windows,
7. put the file there under the right name.

Seven steps, of which roughly zero are things a person who wants her email
summarised has any reason to have heard of. This is not a rough edge. It is
the whole feature, gated behind being the author.

The connect flow itself is **finished and working** — `+ Add Account`,
multi-account, encrypted tokens, per-account service toggles. Only the
client is missing. Which is why this is a decision and not a build.

---

## 2. The obvious answer, and why it is not free

The normal thing for a desktop app is to ship an OAuth client inside the
application. Google's own documentation for native apps accepts that a
desktop client secret is not really secret — RFC 8252 treats installed apps
as public clients that cannot keep secrets, and Google says so directly.

So shipping one is *technically* fine and *legally* fine. The cost is not
the secret. The cost is verification, and it lands in three places.

### 2a. The 100-user cap is harsher than it sounds

An unverified project is capped at **100 users for the lifetime of the
project**. Not 100 concurrent. Not 100 per year. One hundred grants, ever,
and the counter **cannot be reset**. User 101 cannot connect, and the only
remedy is verification.

It is also **scope-specific, not status-specific**: an app in Production
that uses a sensitive scope it never got verified for has the cap re-applied
to it. "We shipped" does not clear it.

### 2b. The warning screen

Until verified, every user meets Google's unverified-app interstitial —
"Google hasn't verified this app" — and has to click through *Advanced ->
Go to (unsafe)*. For a product whose entire pitch is that it keeps your
private life on your own machine, asking the user to click past a safety
warning on their first run is a bad trade even where it is survivable.

### 2c. Verification has two tiers, and Friday is in the expensive one

This is the part worth getting exactly right, because the two tiers are
nothing like each other.

| Tier | What it costs | What triggers it |
|---|---|---|
| **Sensitive** | Google review, roughly 4–6 weeks, no fee | Calendar, Tasks, Contacts, Docs, Sheets |
| **Restricted** | The above **plus an annual third-party security assessment (CASA)** | Gmail, Drive |

Friday's canonical scope set is `services/google_accounts.py:68-81`. Sorted
against Google's published restricted list:

| Scope | Tier |
|---|---|
| `gmail.readonly` | **RESTRICTED** |
| `drive.readonly` | **RESTRICTED** |
| `calendar`, `calendar.readonly` | sensitive |
| `tasks`, `tasks.readonly` | sensitive |
| `contacts.readonly` | sensitive |
| `documents.readonly`, `spreadsheets.readonly` | sensitive |
| `userinfo.email` | neither |

**Two scopes put Friday in the expensive tier.** Everything else is ordinary
sensitive.

Google's restricted list covers Gmail, Drive, Fit, Chat, Data Portability,
Photos Ambient and Health. Calendar and Contacts are **not** on it.

### 2d. A correction worth having before you decide

The concern that the housekeeper's write scopes make this worse does not
hold, and it changes the shape of the decision:

* **Calendar write and Tasks write are sensitive, not restricted.** Adding
  them costs nothing in tier.
* **Gmail write is restricted — but Friday is already restricted** because
  of `gmail.readonly`. It adds no new category.

So the Tasks/Calendar/Gmail housekeeper work does **not** move Friday into a
worse bracket. It is already in the worst bracket, and has been since
`gmail.readonly` was added. The housekeeper is not the thing to slow down.

### 2e. The open question that decides the price

Google's two pages disagree, and the disagreement is worth real money.

* **Restricted scope verification** says assessment applies to apps handling
  restricted data *"from or through a third-party server"*. Friday has no
  server. Tokens are encrypted on the user's machine; mail never transits
  anything Stephen operates.
* **Security Assessment** says flatly: *"Applications requesting access to
  restricted scopes must undergo an annual security assessment."* No
  exemption is listed.

Third-party assessors are commonly reported at **$15,000–$75,000 per year**
(secondary sources — Google does not publish a figure). Annual
recertification is required either way.

**So the same product is either $0/yr or $15k–75k/yr depending on how one
sentence is read.** This is the highest-value unknown in this document, and
it should be settled with Google in writing before any other option is
costed. Everything below assumes it is unresolved.

---

## 3. The options

### Option A — Ship an unverified client

Bundle a Desktop client. Works immediately, zero process.

* Janet connects in one click. So do the next 99 people.
* Every one of them clicks past a scary warning.
* User 101 is permanently stuck, and the project cannot be un-stuck.
* Fine for a private beta of known people. Not a product.

### Option B — Ship a client and pursue verification

The real answer if Friday is going to have users who are strangers.

* Sensitive-tier review is weeks and free.
* Restricted tier adds CASA — **cost unknown until 2e is settled**.
* Requires a privacy policy, a homepage, a demo video, and a named legal
  entity. FutureSpeak.AI already supplies most of that.
* Nothing else on this list ends with a product a stranger can install.

### Option C — Keep bring-your-own-client, but make it a guided flow

Not a JSON drop: a wizard that walks the user through creating their own
Cloud project, with a copy button per field and a link per step.

* No cap, no warning screen, no verification, no fee. Each user is their own
  developer, using their own quota.
* An honest fit for a sovereignty product: their client, their project,
  their data. Some users will actively prefer it.
* But it is still *"create a Google Cloud project"*. Best case it takes a
  motivated person ten minutes. Janet would not have finished it, and Janet
  is the test.

### Option D — Narrow the scopes out of the restricted tier

Not on the original list. It emerged from 2c and it is the cheapest lever
available.

Drop `gmail.readonly` and `drive.readonly` and Friday becomes a
**sensitive-only** app: weeks of free review, no CASA, no annual fee, and
2e stops mattering.

The cost is real and should not be soft-pedalled:

* **Gmail read is load-bearing.** Briefings, email search, the housekeeper.
  Losing it removes a headline capability.
* **Drive is genuinely wired** — `search_drive` and `read_doc_or_sheet` are
  live tools (`services/agent.py:1179,1209`), not decoys. Dropping
  `drive.readonly` costs those two. `drive.file` (per-file, non-restricted)
  keeps a narrower version alive but cannot search a whole Drive.

Worth pricing as a **staged** option: ship sensitive-only, get verified fast
and free, add Gmail and Drive as a later restricted upgrade once 2e is
answered and the user base justifies it.

---

## 4. Recommendation

**Settle 2e first — it is one email and it moves the price by up to
$75k/year.** Do not cost anything else until it is answered.

Then: **D staged into B.** Ship a bundled client with the sensitive-only
scope set, take the free 4–6 week review, and let a stranger install Friday
and connect her calendar with one click and no warning screen. Add Gmail and
Drive back as an explicit restricted-tier upgrade when 2e is known and there
are enough users to justify it.

Reasoning: A caps the product at 100 people forever, which is not a product.
C is defensible but fails the only real test this project has been given —
Janet — and failing it is what produced this document. B is right in the
long run, and D is how you get there without paying for CASA before you know
whether you owe it.

**A is still correct as an interim**, today, for a handful of known
installs. It is not a destination.

---

## 5. Questions only Stephen can answer

* **Q-G1.** Is Friday a product for strangers, or a tool for Stephen and
  people he knows? A is fine for the second and fatal for the first. Every
  other answer follows from this one.
* **Q-G2.** Who is the verifying legal entity — FutureSpeak.AI? Verification
  needs a named owner, a privacy policy URL and a homepage.
* **Q-G3.** Is losing Gmail read for the first release acceptable, if it buys
  free, fast, warning-free onboarding? (This is the crux of D.)
* **Q-G4.** Is `drive.file` enough, or is whole-Drive search load-bearing?
* **Q-G5.** If the answer to 2e is $15k–75k/yr, is Gmail read worth that at
  the expected user count? At 100 users it is $150–750 per user per year.
* **Q-G6.** Does the repo being public change the calculus of embedding a
  client? (It should not — the secret is not a secret for installed apps —
  but it will be raised, and it is better to have an answer ready.)

---

## 6. What ships regardless of the decision

These do not depend on any answer above and should not wait for one.

1. **The error message must name an action, not a path.** Today it names
   three file locations and two environment variables. Whatever option wins,
   a user who reaches this state should be offered a button.
2. **The installer's connect-services step must stop implying it connected
   something.** Fixed 2026-08-26 — it now reports the real account store and
   says where the flow lives. See `setup_wizard.step_connectors`.
3. **`Settings -> Connectors` is where connecting lives.** Everything that
   mentions connecting should say those words and nothing else. The
   `+ Add Account` path is the only one that requests the full scope set;
   the older Connectors path downgrades scopes and must not be advertised.

---

## Sources

* [Restricted scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification)
* [Restricted Scopes list](https://support.google.com/cloud/answer/13464325)
* [Security Assessment](https://support.google.com/cloud/answer/13465431)
* [Manage App Audience (user cap)](https://support.google.com/cloud/answer/15549945)
* [OAuth 2.0 for iOS and Desktop Apps](https://developers.google.com/identity/protocols/oauth2/native-app)
* [OAuth API Verification FAQ](https://support.google.com/cloud/answer/9110914)

Assessor cost figures are secondary-source and unverified by Google.
