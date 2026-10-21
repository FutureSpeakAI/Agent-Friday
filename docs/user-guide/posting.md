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
- **Medium.** Choose **Enable manual mode**, paste a Medium integration token
  in **Paste access token**, then **Save & connect**. Friday checks the token
  with Medium's own account lookup, which posts nothing, and says "✓ Connected
  — session verified" only when Medium accepts it.
- **LinkedIn, X / Twitter, Instagram, Reddit and TikTok.** These need your own
  developer app on the platform. Choose **🔗 Connect**. The card says
  "Create an app in [platform]'s developer site, set its redirect address to"
  an address it shows, "then enter its details here." Make the app, enter its
  **Client ID** (for TikTok, its **Client key**) and **Client secret**, then
  choose **Save and sign in**. Friday opens the platform's sign-in page in a
  new window; sign in there and approve access, and the card refreshes when
  you come back. If your browser blocks the window, allow pop-ups for Friday
  and press **🔗 Connect** again. The client secret is stored encrypted on
  this PC and is never shown again or logged.
- **YouTube** cannot be connected yet. **🔗 Connect** says: "YouTube can't be
  connected from here yet: its sign-in goes through Google, and linking a
  Google account to YouTube publishing isn't finished."

In the composer, a platform that is not connected shows "connect ↗" on its
chip and cannot be picked.

Passwords and tokens are stored encrypted on this PC. On a connected card,
**Check connection** checks the saved sign-in and posts nothing. Where the
platform has a way to look up your account, Friday asks it, and says
"Connected to [platform] as [account]. Nothing was posted." or that the
platform did not accept the saved credential. Where it has no such lookup,
Friday says a credential is saved but was not checked. **Disconnect** asks
first, then withdraws Friday's access on the platform where it can, and
deletes the stored credential.

## Making a post

A post is a card in [Media](media.md). You can start one in three ways:

- On a Media card, choose **Turn this into…** and then **a post**. The new post
  card starts from the first paragraph, with the picture, clip or audio
  attached when there is one.
- From another workspace, choose **Share / Post…** in its send menu, or
  **📤 Share / Post…** on a creation in the gallery. This opens a quick post:
  pick the platforms, check the preview, then choose **📅 Schedule (next
  optimal)** or **🚀 Post now**. **Open full composer →** saves the post as a
  draft and opens its card in Media.
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
  approval card, before the post goes. If something will stop the post going
  out (see [When posts go out](#when-posts-go-out)), you see only that notice,
  not "Scheduled. Friday asks again before it goes."
- **Into Published** raises the approval card. Nothing leaves until you approve.
- Moving a scheduled post back to Draft or Idea takes it off the schedule.

**Calendar** shows only cards with a time. Choose **2 weeks** or **Month**. The
legend shows Published, Scheduled and Needs you by. Drag a scheduled card to
another day to move it; it keeps its time of day, and a message confirms the
new day and time (or, if the post won't go out, the notice says why). A
published card cannot be moved. Click a card for its
details, with **Open** and **Publish now…**.

## Approvals

Each place a post goes gets its own approval card. A post to Bluesky and
Mastodon raises two cards. Each card names the platform and account, when it
goes, the media, and the exact text. The card appears when you schedule or
post, or at the latest when the post is due.

The approval covers exactly those words and that media. If Friday has to retry
the same words after a temporary failure, your approval still covers it.
Changed words or media, or a post sent again after it failed for good or was
declined, get a new card.

### Late posts ask again

A scheduled post that is more than a day late does not go out on an approval
you gave before then. Its card asks again, and says "When: it was due [time]
(UTC) and did not go out then; it goes as soon as you approve". A notification,
"A late post asks you again", says the same: "It goes only if you approve it
again; or reschedule it." This also holds if you choose **🚀 Post now**,
reschedule it, or choose **Publish anyway…** on a late post: the old approval
still doesn't count, and a new card asks you.

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

In Media, a held post's card shows **Held for you** and sits in In review. The
notice's **Open the post in Media** opens that card.

To send it anyway, choose **Publish anyway…** on the card. Friday shows why it
was held and asks "Publish it anyway?" before anything changes. Releasing it
sends nothing by itself: the post is checked again for harmful content, and
its approval card still asks you before it goes out. A post the harmful-content
check blocks is not sent, and if the check cannot run the post is held again.
Friday cannot release a held post for you.

**Publish anyway…** covers the words and media that were held, nothing else.
If you edit them afterwards, the privacy check applies again, and the post is
held again if it still looks private; the reason then ends "(changed after you
released it)".

If nobody acts on a held post for 7 days, it is cancelled and nothing is sent.

## When posts go out

Scheduled posts are sent by a built-in job, **Content publisher**, which runs
every 15 minutes. It starts switched off, and switches itself on the first
time you schedule or post to a connected account (or to Substack's manual
hand-off). You get a notification, "Content publisher turned on": "It sends
your scheduled posts, each only once you have approved it. You can switch it
off in Workflows." If posts are already waiting, it also says how many: "2
posts you already approved are due now and go out on its next run, after the
usual checks." and "3 posts more than a day late will ask you again before
going out." Switching it on sends nothing: each post still waits for its
approval card, and a late post asks again (see
[Late posts ask again](#late-posts-ask-again)).

If Friday can't save the switch-on, the post doesn't go out and you are told:
"This post won't go out until the Content publisher is on: Friday tried to
switch it on but couldn't save the change. Turn it on in Workflows." with
**Open Workflows**. If Friday can't read its list of jobs just then, it says
"This post may not go out" and asks you to check Workflows.

The job shows as on under **Friday's built-in routines** in **Workflows** (see
[Scheduled jobs](scheduled-jobs.md)), with a note that it was switched on
automatically, when and why. You can switch it off there.

If you switch it off yourself, it stays off. Scheduling a post then shows "This
post won't go out: the Content publisher is switched off. Turn it on in
Workflows." with **Open Workflows**. **🚀 Post now** says "The Content
publisher is switched off, so if this post can't go out straight away it won't
be tried again." Friday also tells you
when something else will stop a post: publishing switched off in Settings, all
publishing paused, or none of its platforms connected. Each of these notices
has a button that opens the place to fix it. When a post won't go out, the
notice is the whole answer: the screen doesn't also say "Scheduled".

Only your own switch-off stays off. A scheduled or background job can't pause
the Content publisher. If you ask Friday in chat to pause it, she does, and a
notification says "The Content publisher was paused from a chat": "Scheduled
posts won't go out while it is paused. Scheduling a post switches it back on;
to keep it off, switch it off in Workflows."

- A post can go out up to about 15 minutes after its time.
- The PC must be on and Friday running. A post missed while the PC was off goes
  out at the first run after Friday is running again, once approved. If it is
  by then more than a day late, it asks you again first.
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
  it stops and tells you to reconnect the account in Settings, Connections.
  The notice's **Open Connections** takes you there.

Notices about a failed or blocked post have **Open the post in Media**, which
opens that post's card.

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
  on the same platform. For a late post whose approval is too old, what Friday
  is told back says it "will ask you again (more than a day late)", not that
  it is approved.
- "What's queued?" or "Did my Mastodon post go out?" Friday lists upcoming and
  held posts and recent history, or one post's status on each platform. Friday
  cannot release a held post; use **Publish anyway…** on its card.
- "Turn this blog post into posts for Bluesky and Mastodon." Friday writes a
  draft for each platform. Drafts only: you review and schedule them.

Every post made this way still waits for its approval card.

## What stays on your PC, and what can leave

- **On this PC:** drafts, previews, the schedule, held posts, and your stored
  passwords, tokens and client secrets.
- **Leaves the PC:** a post's text and media, to the platform, after you
  approve. Connecting sends your handle and app password to Bluesky, your
  token to your Mastodon server, or your token to Medium, to check them.
  **Check connection** asks the platform about your account and posts nothing.
  For LinkedIn, X / Twitter, Instagram, Reddit and TikTok you sign in on the
  platform's own page. The per-platform rewrite may use a cloud model, as
  described above.
- **Needs your approval:** every post, to every platform, unless a grant you
  made covers it.

Related: [Media](media.md), [Connections](connections.md),
[Approvals and receipts](approvals-and-receipts.md).
