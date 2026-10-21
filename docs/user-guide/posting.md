# Posting to your accounts

Agent Friday™ can write, schedule and publish posts to your social accounts.
Every post waits for your approval card before it leaves the PC. This page
says which accounts you can connect, how to make a post, and when it goes out.

## Which accounts you can connect

Accounts live in **Settings > Connections > Publishing channels**. Each
platform has a card that says what Friday can and cannot do there.

- **Bluesky.** Choose **🔗 Connect**, enter your handle and an app password
  (made in Bluesky under Settings, App Passwords), then **Save & connect**.
  Friday signs in at once to check them. The card says "✓ Connected — session
  verified" only when the sign-in worked.
- **Mastodon.** Choose **🔗 Connect**, enter your server (for example
  `https://mastodon.social`) and an access token (made in Mastodon under
  Preferences, Development), then **Save & connect**. Friday checks the token
  with your server before it says connected.
- **Substack.** Substack has no public way to post. Choose **Enable manual
  mode**, enter your publication address, and choose **Enable manual handoff**.
  Friday then prepares a ready-to-paste package on this PC, and you publish it
  in Substack yourself.

LinkedIn, X / Twitter, Instagram, YouTube, Reddit and TikTok also have cards.
They cannot be connected from the app yet. **🔗 Connect** on those cards shows
"The connect flow is not available yet." In the composer their chips show
"connect ↗" and cannot be picked.

Passwords and tokens are stored encrypted on this PC. On a connected card,
**Test post** checks that the connection still works; it does not publish
anything. **Disconnect** asks first, then withdraws Friday's access on the
platform where it can, and deletes the stored credential.

## Making a post

A post is a card in [Media](media.md). You can start one in three ways:

- On a Media card, choose **Turn this into…** and then **a post**. The new post
  card starts from the first paragraph, with the picture, clip or audio
  attached when there is one.
- From another workspace, choose **Share / Post…** in its send menu, or
  **📤 Share / Post…** on a creation in the gallery. This opens a quick post:
  pick the platforms, check the preview, then choose **📅 Schedule (next
  optimal)** or **🚀 Post now**.
- Ask Friday (see [Ask by voice or chat](#ask-by-voice-or-chat)).

## The composer

Open a post card in Media to see the composer.

- **Platform chips.** Pick where it goes. Only connected platforms can be
  picked.
- **Format.** Each chosen platform has a format list, such as post or thread.
- **Preview.** Each platform gets its own preview, with a character count
  ("used / limit"). It turns red and says "over the limit" when the text is too
  long. A long post split into a thread shows each part numbered.
- **Hashtags.** Click a suggested hashtag to leave it out. Type in **#add** to
  add your own.
- **🖼 Attach from gallery** adds a file from your creations.
- **Alt text.** Each attached image has a box for a description. Until you fill
  it in, the preview warns: "Add alt text — this platform supports it and your
  audience deserves it."
- **＋ Variant** adds an alternate version (Hook A, Hook B…) that you can
  rename and edit.
- **💾 Save draft** keeps the post without scheduling it.
- **📅 Schedule…** opens **Pick a slot**. Choose a date and time, or tick
  **✨ Let Friday pick the optimal time**. Suggested slots say where they come
  from: "learned from your audience" (how your own posts did) or "general best
  practice". Then choose **Confirm schedule**.
- **🚀 Post now** asks for approval and sends the post once you approve.

The preview is made on this PC, without a model. When you save, schedule or
post, Friday rewrites the text for each platform with the model your routing
picks. That can be a cloud model; the text passes the privacy check before it
goes to one. If the rewrite cannot run, Friday adapts the text by fixed rules.

### Clashes

If another post is due on the same platform close to yours, Friday still
schedules it and warns: "Scheduled with 1 conflict warning(s) — see the
Calendar". "Close" means within 2 hours. To change that, set **Conflict window
(hours)** under **Global controls** in **Publishing channels**, then choose
**Save**. When Friday picks the time for you, it avoids slots that clash.

## The Pipeline board and the Calendar

In Media, **Pipeline** shows every card in five columns: Idea, Draft, In
review, Scheduled and Published. Drag a card to another column, or select it
and press **]** to move it one stage on.

- **Into Scheduled** asks for a date and time. Friday asks again, on an
  approval card, before the post goes.
- **Into Published** raises the approval card. Nothing leaves until you approve.
- Moving a scheduled post back to Draft or Idea takes it off the schedule.

**Calendar** shows only cards with a time. Choose **2 weeks** or **Month**. The
legend shows Published, Scheduled and Needs you by. Drag a scheduled card to
another day to move it; it keeps its time of day, and a message confirms the
new day and time. A published card cannot be moved. Click a card for its
details, with **Open** and **Publish now…**.

## Approvals

Each place a post goes gets its own approval card. A post to Bluesky and
Mastodon raises two cards. Each card names the platform and account, when it
goes, the media, and the exact text. The card appears when you schedule or
post, or at the latest when the post is due.

The approval covers exactly those words and that media. If Friday has to retry
the same words after a temporary failure, your approval still covers it.
Changed words, or a post sent again after it failed for good or was declined,
get a new card.

A scheduled job of yours can schedule posts on its own only with a grant. Make
one in **Settings > Privacy & Data**, under **Scheduled jobs: what they may do
on their own**. Pick the job and the actions **Schedule a new social post** or
**Schedule an existing social post**, how long it lasts, and how many uses.
While the grant lasts, posts that job schedules go out without a card. See
[Approvals and receipts](approvals-and-receipts.md).

## Posts held for private data

Just before sending, Friday checks the final text for harmful content and for
private data. If the text looks like it contains private data, the post is
held, not changed. You get a notice, "Post held — review before publishing",
that names the platform and says "post held — it looks like it contains private
data". A post is also held when the check itself cannot run.

In Media, a held post's card shows **Held for you** and sits in In review.
Media has no button to release a held post yet, and Friday cannot release one.
If nobody acts on a held post for 7 days, it is cancelled and nothing is sent.

## When posts go out

Scheduled posts are sent by a built-in job, **Content publisher**, which runs
every 15 minutes. It starts switched off. Turn it on under **Friday's built-in
routines** in **Workflows** (see [Scheduled jobs](scheduled-jobs.md)), or a
scheduled post does not go out at its time.

- A post can go out up to about 15 minutes after its time.
- The PC must be on and Friday running. A post missed while the PC was off goes
  out at the first run after Friday is running again, once approved.
- **🚀 Post now** does not wait for the job: the post goes out right after you
  approve it.
- **Mastodon** can hold a post on its own schedule. When the time is at least
  10 minutes away and you have approved, Friday hands the post to Mastodon,
  which then publishes it on time even if your PC is off. A notice says
  "Scheduled natively on Mastodon".

## When something fails

- **Temporary problems** (the platform is busy, a server error, the network)
  are retried, up to three attempts in all. The waits grow: about 5 minutes,
  then about 20, or as long as the platform asks. A retry happens at the next
  publisher run after the wait.
- **Uncertain results.** If a send timed out after the post left, Friday first
  checks the platform for the post. If it is there, it is marked published and
  not sent again. If it is not, the post is retried as above. If the platform
  offers no way to check, Friday does not resend, and tells you it failed.
- **Sign-in problems.** Friday tries to refresh the sign-in once. If that fails,
  it stops and tells you to reconnect the account.

## Pause all publishing

**⏸ Pause all publishing** is under **Global controls** in **Settings >
Connections > Publishing channels**. While it is on, nothing is sent and the
button reads **⏸ Publishing paused — resume**. The pause lasts across
restarts. A post already handed to Mastodon's own schedule is on Mastodon, and
pausing does not recall it.

## Ask by voice or chat

- "Post this to Bluesky tomorrow morning." Friday drafts the post, adapts it,
  and schedules it. You can name a time ("tonight", "friday 3pm") or let her
  pick the best time.
- "Reschedule that post to tonight." Friday moves it and warns about clashes
  on the same platform.
- "What's queued?" or "Did my Mastodon post go out?" Friday lists upcoming and
  held posts and recent history, or one post's status on each platform. Friday
  cannot release a held post.
- "Turn this blog post into posts for Bluesky and Mastodon." Friday writes a
  draft for each platform. Drafts only: you review and schedule them.

Every post made this way still waits for its approval card.

## What stays on your PC, and what can leave

- **On this PC:** drafts, previews, the schedule, held posts, and your stored
  passwords and tokens.
- **Leaves the PC:** a post's text and media, to the platform, after you
  approve. Connecting sends your handle and app password to Bluesky, or your
  token to your Mastodon server, to check them. The per-platform rewrite may
  use a cloud model, as described above.
- **Needs your approval:** every post, to every platform, unless a grant you
  made covers it.

Related: [Media](media.md), [Connections](connections.md),
[Approvals and receipts](approvals-and-receipts.md).
