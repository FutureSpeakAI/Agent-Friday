# Career workspace

Status: implemented. Career uses Friday's native conversation tools and the local career-ops file format.

## Set up

Open Career and choose the folder containing your career-ops project. The default is `~/Projects/career-ops`. The setup panel checks for your CV (`cv.md`), profile (`config/profile.yml`), and job-board configuration (`portals.yml`); it also identifies example profile values. Add your own career history and targets before starting a task, then refresh.

Friday reads these source files without rewriting them. Native evaluation reports are new Markdown files under `reports/`; tailored documents are saved to Friday's career documents folder. When career-ops and older wiki records share a name, Career displays the career-ops version. Older wiki records remain available as a fallback.

## Work with a role

- **Scan opportunities** reads the public boards configured in your project and reports unsupported boards or failures.
- **Evaluate job** reads the supplied job URL, compares it with your CV and profile, and saves a fit report.
- **Tailor CV** prepares a draft from your real experience for the supplied job URL.
- **Prepare interview** asks Friday for likely questions and evidence-based examples, including missing information to confirm.

Each task opens a separate conversation and shows its progress and response in Career. Its conversation link lets you continue with Friday or resolve a privacy decision. Reports and tracker data can be refreshed without reopening the workspace.

Job postings are source material, not instructions. A draft is not an application submission. Tracker changes use Friday's existing approval cards; submitting an application remains your action.

## Career search workflow

Choose **Add Career search workflow** in Career, or add **Career search** from the starters in Workflows. Adding it saves an ordinary editable workflow. It does not run, create a schedule, or replace your existing edited workflow.

The three steps check your setup, scan configured boards, and evaluate a shortlist. The starter asks Friday to consider at most three new roles and to report missing descriptions and coverage gaps. This is editable workflow guidance, not a separate hard limit in the workflow engine. Review the steps in Workflows, then run it when needed or explicitly set a schedule there.

## Scope

The native board scanner supports Greenhouse, Ashby, and Lever public APIs. Other portals and configured web-search queries are reported as coverage gaps. It does not install or execute third-party scanners automatically. Fit evaluation uses the supplied description and candidate sources; company research and posting availability that have not been checked must remain unconfirmed.

This integration adapts career-ops conventions from [career-ops-hq/career-ops](https://github.com/career-ops-hq/career-ops). See `CREDITS.md` and `NOTICE` for attribution and the MIT license notice.
