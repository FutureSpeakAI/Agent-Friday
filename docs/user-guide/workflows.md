# Workflows

A workflow keeps a useful procedure, its timing and its results together. It
has a name, one or more steps, the result you want, the sources it reads, what
should be checked, and (optionally) a schedule. Agent Friday™ runs the steps,
shows each run's progress and keeps the result.

The same workflows are available from the Workflows workspace, from chat and
from voice. Open the **Workflows** workspace, or ask in chat or by voice.

## Start with a result

Describe what you want in the box that asks what Friday should take care of,
and choose **Draft it**. Friday turns your words into steps, a result and
timing, and shows them for you to check before anything is saved. If no model
is available for the draft, your words become a single step you can edit.

You can also open **Start from an example**. The starters are **Research
brief**, **Daily briefing**, **Watch for changes**, **Notes to document** and
**Specification to reviewed change**, plus **Career search**. Choosing a starter
fills a draft in the editor and saves nothing. The Career search starter is
added to your list without being run or scheduled, and you choose when it runs
after reviewing it.

In **Result and context**, choose:

- the **project** and the conversation the result returns to;
- the result: an **answer in the conversation**, an **editable artifact**, a
  **file**, or a **code change**;
- **what should be checked**, in your own words;
- **sources and notes**, one per line. Sources must already be available to
  Friday. Writing the name of a service does not connect it.

Each step receives the previous step's result. Run a first example, look at its
result, and correct the procedure as needed.

Saving creates a numbered revision. A run keeps the revision it started with,
even if you edit the workflow while it is working. Friday rejects a save from
an older editor if another edit has already changed that revision.

## Follow the work

Each workflow is a card. **Details** shows its steps, the checks, the sources
and the result links for the latest run. Result links open the exact saved
document version the run produced. If that version is no longer available,
Friday says so and offers **Browse other artifacts** instead of quietly opening
a different one.

### What is checked

After a run, Friday looks for the output you asked for and reads it back
against the run's saved evidence.

- **Deliverable verified** means the expected output was found and its
  structure was checked.
- **Finished, not verified** means the run ended but the output could not be
  confirmed. The card says which check did not pass.
- Quality requirements, such as whether an argument is convincing or every
  requested source was covered, stay marked as unverified for you to review.
- A saved code change is not proof that its tests passed.

Execution and delivery have separate records. If delivery to the conversation
fails, **Retry delivery** posts the existing result again without rerunning the
steps. **Do again** starts a new run; it is not a way to find out whether an
earlier outward action happened.

While a run is working, a card can read **Waiting for a model** or **Needs your
decision**. Approval requests still arrive as they do anywhere else in Friday.
**Stop run** asks the active run to stop after its current tool finishes.

## Make it a routine

Choose **Make routine**, or set timing in the editor: only when you run it,
every weekday, on certain days, every few hours, or once. Choose
**When a run finishes**, **Only when something changes**, or **Keep results
here; no routine notifications**.

A change monitor takes a first baseline and keeps it for later comparisons. A
source that could not be read is not evidence that nothing changed.

The switch on a card turns its schedule off and on. Work already running is
unchanged. A scheduled workflow runs only while Friday is running on your
computer, which must be awake, and its services and models must be available.
Closing the Workflows screen does not stop it. Workflows have no always-on
remote host and no mailbox or webhook triggers.

Scheduled prompts saved before workflows existed appear in the same list, with
**Run now** and the same on and off switch. See
[Scheduled jobs](scheduled-jobs.md).

## Reuse and correction

After a successful run with a verified deliverable, **Keep as reusable
procedure** saves its steps as a [skill](skills.md). Friday can retrieve the
whole procedure when it is relevant. Learning saves a procedure, not model
training or new permissions. If the workflow changed after that run, Friday asks
you to check the current revision first.

You can ask Friday to show a workflow's saved revisions or restore an earlier
one. Restoring creates another revision and keeps the history in between.

Use the project filter, **Show workflows for**, to see only one project's
workflows. **Delete workflow** removes a workflow from the list.

## Ask in chat or voice

Chat and voice use the same operations and permissions as the desktop controls.
Examples:

- "Show the steps and last result of my research brief."
- "Turn these notes into an editable document in this project."
- "Run that workflow, and tell me when the result is ready."
- "Make it run every weekday at eight, and only notify me about changes."
- "Pause its schedule." or "Stop the run that's working now."
- "What can you do here, and what needs connecting first?"

In voice, a started workflow continues in the background while you keep
talking, and Friday does not guess its result. Private data, file access and
outward actions keep their existing checks. A stored workflow or skill does not
approve those actions.
