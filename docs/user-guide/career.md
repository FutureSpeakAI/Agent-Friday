# Career: choose roles, prepare, and track your search

Career is in the Work group of the dock. It brings your job search into one
workspace: find promising roles, compare them with your real experience,
prepare application materials, and keep track of what happens next. Agent
Friday™ helps you prepare; you review and apply. Career uses Friday's native
tools and your local career-ops files.

## Your job search in one place

The **Your job search, with Friday** card stays available before and after setup.
Choose **Take a tour** for six short steps inside Career, or **Ask Friday** for a
conversation. You can also ask from any chat: “What can Career do?” or “Walk me
through Career.” An explanation does not start a scan, change files, or run a
workflow. Friday may read your setup status to give you a useful next step.

**Review setup** opens the setup panel when files are missing. **Go to job
actions** takes you to the role controls when setup is ready. If the status check
failed, **Retry setup check** tries the check again; an unavailable status is not
the same as an incomplete setup.

## Connect your career folder

Select an existing career-ops folder and save its location. The default is
`~/Projects/career-ops`. The folder needs:

| File | What it contains |
|---|---|
| `cv.md` | Your CV in Markdown, with your actual experience and achievements. |
| `config/profile.yml` | Your profile, target roles and compensation preferences. |
| `portals.yml` | Companies, public job boards, and title/location filters to scan. |

The panel names missing files and flags example profile values or a CV that looks
too short. Readiness does not test whether the model, network or portal
configuration works. Saving a folder does not download career-ops or create your
CV, and there is no profile-editing wizard. Update your source files, then refresh
the check. You can change the folder later from the same setup panel.

An optional `modes/_profile.md` supplies additional career narrative. The
`data/applications.md` tracker is optional for the readiness check, but its
career-ops template must exist before Friday can record tracker changes. Native
scanning and evaluation do not require Node or an upstream scanner install.

## Work on one role

- **Scan opportunities** reads the companies configured in your folder. The
  native scanner supports public Greenhouse, Ashby and Lever boards, applies
  title/location filters, and removes known duplicates. It reports unsupported
  sources and failed requests. Configured web-search queries are not executed by
  this scan. Scanning is read-only by default; saving a pipeline is a separate
  action that asks first.
- Paste a posting URL and choose **Evaluate job**. Friday reads its description,
  compares it with your CV/profile and saves a new Markdown report under
  `reports/`. You can provide pasted job-description text in chat when a posting
  cannot be read. The 1–5 fit score is separate from evidence confidence; neither
  promises an interview. Invalid scores and unchecked research remain unknown.
- **Tailor CV** prepares a new draft using your actual experience. Ask Friday for
  a cover letter too. The original CV is unchanged. The draft is saved as
  Markdown; a Word document is also attempted when OfficeCLI is available.
  Friday reports document failures rather than claiming a finished Word file.
- **Prepare interview** opens a conversation for likely questions and
  STAR+Reflection examples: Situation, Task, Action, Result, and what you learned.
  Friday uses your CV, reports and posting, and flags missing examples. This is
  conversational preparation, not a separate reusable story-bank manager.

Each task has its own conversation and shows progress and results in Career. The
conversation link lets you continue, add information, or resolve a privacy
decision. Model calls follow Friday's configured model and privacy handling;
Career does not imply that every model call runs locally.

## Make the search repeatable

Choose **Add Career search workflow**, or add **Career search** from the starters
in **Workflows**. It saves an ordinary editable workflow with three steps:

1. **Check career setup** reports what is ready and what needs attention.
2. **Scan new opportunities** reads configured boards and reports coverage gaps.
3. **Evaluate and shortlist** reads the selected postings and saves fit reports.

Adding the workflow does not run it, create a schedule, or replace your edits to
an existing workflow. Review the steps in Workflows, then run it when you choose.
Scheduling is a separate action. The starter asks Friday to consider up to three
new roles; this is editable guidance, not an enforced ceiling in the workflow
engine. Missing descriptions and scan failures should remain visible in the
result rather than being presented as successful evaluations.

## Follow your results

Refresh Reports and the tracker after work completes. A report is not a tracker
update. Friday proposes additions or changes on an approval card showing the
before and after, then writes only after you approve.

Career prefers the configured career-ops tracker and same-named reports over
older wiki records, which remain a fallback. Report counts may include older
notes; they are not a count of independently verified evaluations. If you ask
Friday to check recruiter mail, connected Gmail can supply reply and follow-up
suggestions. Unavailable mail is reported as unchecked, not “no replies.”

## Review before you apply

Check every draft against your real experience. Career does not submit
applications or send outreach. You answer legal and demographic questions and
submit on the employer's site. A prepared draft is not an application marked
Applied; record that stage after you confirm submission.

For related behavior, see [Approvals and receipts](approvals-and-receipts.md),
[Privacy](privacy.md), and [Scheduled jobs](scheduled-jobs.md).
