# Home, Code, Sites, Health, Finance and Family

This page covers the Agent Friday™ workspaces that do not have a page of
their own. Each section says what you see and what you can do there.

## Home

Home looks different in each display style. You choose the style in
**Settings > Appearance > Display style**, or with the **Style: Simple**
button in the top bar. **Simple** is the default. The choice is saved in this
browser only.

### Home in Simple

The Simple desktop has the views **Day**, **Projects**, **Explore**,
**Activity** and **Workspaces**. Home is the **Day** view. It shows the date
and a greeting for the time of day. Links take you to **Calendar**, **Avatars
& depth** and any decisions waiting for you. The box **Ask, make, or pick up
where you left off…** starts a conversation; you can pick a project and add
**+ Materials**. **Continue →** opens your draft in the conversation, ready to
review.

Above that box is the Home board, **Your working day**. Its cards update from
activity on this computer while Home is open. It has three tabs:

- **Today**: the cards for now.
- **Later**: snoozed cards. They come back at the time you chose.
- **Hidden**: dismissed and expired cards, kept until you restore them.

**Add card** opens **Add to Home**. Choose **Write a card** (a title and
details, then **Save card**) or **Track something** (a source and what to
follow, then **Start tracking**).

Each card has **Pin**, **Read card** and a **More** menu. **More** holds:

- **Move earlier** and **Move later** (or drag the ↕ handle);
- **Snooze for 1 hour**, or **Snooze until tomorrow** (9:00 the next day);
- **Dismiss card**. Dismissing hides the card only; its source is unchanged.

Each snooze and dismiss offers an Undo. In **Later** or **Hidden**, **Show
again** returns a card to **Today**.

### Home in Classic

In **Classic**, the start screen shows a greeting for the time of day, the
chat field **Ask Friday anything...**, the mic, and up to 4 countdowns.

Countdowns are worked out on this computer from what Friday already holds.
No model is asked. They come from:

- your calendar (the events already read, 60 days ahead);
- to-dos with a deadline, and open follow-ups;
- goals and milestones with a due date;
- dated lines in your wiki's people, family and personal pages, such as
  birthdays and anniversaries.

Each countdown says where it came from, for example "from your calendar" or
"from your wiki". Public holidays are not added, unless they are on your
calendar or in your wiki.

**🌅 START MY DAY** gives a spoken briefing in this order: today's calendar,
email that needs attention, news relevant to you, then your active tasks and
commitments. It ends with one suggestion for the day.

When this cluster shows is set in **Settings > Appearance > Start screen >
Show my day**:

- **Smart (recommended)**, the default, shows it when it is useful (back at
  your desk, the start of the day, something within the hour) and fades it
  while you work or talk.
- **Always** keeps it on screen.
- **Never** shows it only when you ask.

Either way, Ctrl+/ reaches the chat field and Ctrl+Shift+Space the mic.

## Code

The **Code** workspace is the Code Salon, a place to understand code and make
small projects with Friday. Building with Friday in a chat is covered under
[Build mode](chat-hub.md#build-mode).

### The header

The header has **Projects** (shows or hides the list), **Friday** (when a
codebase is open) and **Activity**. **Activity** opens **All processes** or
**System logs**, across all of Friday.

### The list

The list has two groups:

- **Your codebases**: study copies, working folders and codebases Friday
  manages.
- **Local repositories**: repositories Friday found in your Projects folder.

**Study a repository** brings a repository on this PC into the salon as an
editable copy. Give it a name and the folder, then press **Create study
copy**. The original's files, branch and history stay untouched.

A study copy is source text only. Dependencies, binaries, generated files and
credential material are left out. It is not a runnable clone and does not
stay in sync with the original.

**New codebase** starts a small project with its own conversation, files and
change history. The starting point is **Web page · HTML, CSS and JavaScript**
or **React app**.

### A repository's details

Choose a local repository to see its branch, working files and folder. It
offers **Study a copy** and **Repository Git tools**. The screen notes the
difference: "Git tools act on this original folder. Study actions act on a
separate copy."

### Inside a codebase

An open codebase has these views: **Understand**, **Files**, **Changes**,
**Preview** and **Run**. A codebase with saved artifacts also has
**Artifacts**.

## Sites

The **Sites** workspace connects a repository to its builds, preview,
publication and domain. A build and a publication each have their own review
before they run. Sites publishes to GitHub Pages only. The full guide is
[Sites and domains](../features/sites.md).

## Health, Finance and Family

On a new install, these three workspaces are not in the dock. To show them,
turn on **Settings > General > Workspaces > Show all workspaces**. It is off
on a fresh install. Installs that existed before keep it on. Once you arrange
the dock yourself in **Appearance**, your arrangement wins.

### Health

Health has five tabs: **overview**, **medications**, **appointments**,
**insurance** and **vehicles**.

- **insurance** shows your plan, policy number, group number and member
  services. Each filled row has **Copy**.
- **appointments**: a provider with an email has **Copy Email**.
- **vehicles** shows **Fleet Health** and, under it, **Mechanics**. Each
  mechanic's **Copy** copies their name.

### Finance

Finance has the tabs **overview**, **portfolio**, **perks**, **contacts** and
**quickref**, and a **Refresh Data** button.

On **overview**, the **Card Perks** card counts perks "expiring soon": those
that expire within the next 60 days. On **perks**, a used perk shows
**USED**.

### Editing your records

Health and Finance have no editing in the app. The screens tell you which
file to edit instead:

| The screen says to edit | For |
|---|---|
| `~/.friday/health/medications.json` | Medications |
| `~/.friday/health/insurance.json` | Insurance details |
| `~/.friday/finance/portfolio.json` | Portfolio positions |

`~` is your user folder, `%USERPROFILE%`. If one of these files is missing,
Friday creates a starter file for you. The **contacts** tab is a placeholder:
its **Your Financial Advisor** and **Your CPA / Accountant** cards say
"Placeholder: this tab does not read any file yet."

The `health` and `finance` folders are part of the vault. When Friday has a
vault key, she encrypts the files in them at start-up, and a starter file is
written encrypted. A plain file you save there is still read, and it is
encrypted at the next start. An encrypted file cannot be read or edited in a
text editor. See [Locks](locks.md) and [Privacy](privacy.md).

### Family

Family is a single page. Its top line names the next family date, for example
"[birthday] is [when].". Below are up to 8 personal countdowns, such as
birthdays and anniversaries from your calendar and wiki.

Four cards ask Friday for help in the chat:

| Card | Sends |
|---|---|
| **Family Members** | "Help me add my family members to the wiki." |
| **Add Countdown** | "Help me add birthday and anniversary countdowns for my family." |
| **Pets** | "Help me add pet info to my family wiki." |
| **Routines** | "Help me set up family routines and shared tasks." |

### What stays on your PC

Health, Finance and Family show files kept on this computer. When you ask
Friday about these records in chat, the rules in [Privacy](privacy.md) decide
what may reach a cloud model.
