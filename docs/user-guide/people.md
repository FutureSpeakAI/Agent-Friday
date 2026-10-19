# People

The **People** workspace (👥 in the dock) lists everyone you deal with and
what Agent Friday™ remembers about them. Friday keeps a timeline of who you
have emailed and met. The timeline is built on this computer, from mail and
calendar headers only, and stays here.

## Relationship memory

The **Relationship memory** card sits at the top of the People list. It says
when the last sync ran and how many interactions are stored.

### Building the timeline

**Build the timeline from mail and calendar** is on by default. While it is
on, Friday reads your connected Google mail and calendar about once an hour.
**↻ Sync now** runs a sync straight away and reports "Added N interactions".
If an account could not be read, the message counts the account errors and
gives the reason. If there was more mail
than one sync takes, it adds "some mail not read yet, sync again".

The first sync reaches back 30 days. Each sync reads at most 500 messages per
account.

### What is stored

For an email, Friday keeps the sender, the recipients, the cc list, the date,
the subject and whether you sent it or received it. For a meeting, she keeps
its title, its start time and its attendees. She never stores message text,
snippets or event descriptions.

The timeline and your follow-ups are encrypted and kept in
`%USERPROFILE%\.friday\relationships\`. Nothing from the timeline is sent
anywhere by the sync.

Friday skips mail that is not from a person:

- any message with an unsubscribe header;
- addresses such as no-reply, notifications, alerts, mailer-daemon and bounces;
- promotions, social and forum mail;
- your own addresses and meeting-room bookings;
- people you told Friday to forget (see [Forgetting a person](#forgetting-a-person)).

### Nudges when nobody replies

**Nudge me when nobody has replied after [N] days (0 = off)** asks Friday to
tell you about threads that went quiet. It is off (0) by default. You can set
0 to 90 days.

A thread counts when you sent the last message and that message is between N
and N + 14 days old. Older threads are left alone. Each thread is nudged once
for each last message, and Friday sends at most 3 nudges per hourly check.

The nudge is a low-priority notification: "No reply from [name] in [N] days",
with "On "[subject]"." underneath. Clicking it opens that person. See
[Notifications](notifications.md) for how notices reach you.

### Importing LinkedIn connections

**⬆ Import LinkedIn connections (CSV)** reads the connections file you
download from LinkedIn. The button's tooltip says where to find it:
**LinkedIn → Settings → Data privacy → Get a copy of your data →
Connections**.

The import runs on this computer. It reports "LinkedIn: N added, N updated",
and how many rows it skipped. A file larger than 5 MB, or one without
LinkedIn's First Name and Last Name columns, is refused. People you told
Friday to forget are left out.

### Saving to Google Contacts

A standard Google connection lets Friday read your contacts but not change
them. For each such account the card shows **Allow saving to Google
Contacts: [account]**. Clicking it explains that "Google will ask [account] to
allow creating and editing contacts", with **Continue to Google** and
**Cancel**. Continue opens Google's own consent page for that wider
permission.

Even with that permission, every save waits for your approval. A saved
contact leaves this PC and goes to your Google account. See
[Approvals and receipts](approvals-and-receipts.md).

### Follow-ups

When you have follow-ups, the card lists up to 8 under **Follow-ups**, each
with its person, note and due date. **Done** clears one.

## A person's page

Choose a person in the list to open their page.

### Timeline

The **Timeline** card opens with a summary line: last contact, when they last
wrote, when you last wrote, and how many emails were received and sent and how
many meetings you had. Below it are the 10 most recent entries, each marked
**meeting**, **you wrote** or **email**, with its subject or title.

If nothing is recorded yet, it says "No emails or meetings recorded with them
yet." If the name matches several people, it lists them.

### Setting a follow-up

Under the timeline, type a note in **Follow up about…**, set **in [N] days**
(3 by default, up to 365) and press **Remind me**. Friday confirms: "Friday
will remind you on [date]." (with her name as you set it).

On that day you get a notification, "Follow up with [person]", showing your
note. Nothing is sent to the person.

### Trust Dimensions

The **Trust Dimensions** card shows five bars with a percentage each:
reliability, information quality, emotional trust, timeliness and domain
expertise. A dimension with no score yet shows 50%.

## Asking in chat

Friday has four tools for people. You can ask in your own words, for example:

| Ask | What Friday does | Needs your approval? |
|---|---|---|
| "When did I last talk to Dana?" or "What do I know about Dana?" | Reads that person's timeline and open follow-ups (`person_timeline`) | No |
| "Who at Acme have I talked to?" | Lists people at an organisation, by email domain and by the company on imported contacts (`people_at`) | No |
| "Remind me to follow up with Dana next week" | Sets a local follow-up (`set_follow_up`) | No |
| "Save Dana to my Google contacts" | Creates or updates a Google contact (`save_google_contact`) | Yes, every time |

The timeline is built locally. When you ask about it in chat, the answer goes
to the chat's model under the rules in [Privacy](privacy.md).

## Forgetting a person

On a person's page, **🗑 Forget** shows what Friday holds about them and asks
"Remove what Friday holds about [name]?". **Remove them** deletes it;
**Cancel** keeps it. A receipt then lists what was removed.

Forgetting removes the contact record, the map entries and links, their
follow-ups, and their address from every timeline entry. Their name becomes
"[removed]" in stored subjects and titles. Later syncs and LinkedIn imports
skip them. Your own pages and conversations stay as you wrote them; see
[Privacy](privacy.md#other-people).

There is no chat command for forgetting someone. Use the button.
