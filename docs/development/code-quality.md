# Code quality standard

This is the standard for new and touched code in this repository. It applies to AI coding agents and to people.
The short form is in [AGENTS.md](../../AGENTS.md); the checks it refers to are listed in
[repository-guards.md](repository-guards.md).

The methods come from Lauren Tan's talk "All Code Is Slop (Except Mine)" (AI Engineer, November 2026) and her
MIT-licensed pstack skills. The wording and the fit to this project are ours.

## The one idea

A coding agent writes what its surroundings teach it. Quality is built into the surroundings, in this order of
effort:

1. **The code itself.** Shape the API and the layout so the wrong thing cannot be written.
2. **Deterministic checks.** Types, lint, guard scripts, tests, CI.
3. **Verification.** Run the real thing and keep the evidence; model the protocols that matter.
4. **Prose, last and least.** This file and AGENTS.md. Only for what needs judgement.

A rule that sits at level 4 and could sit higher is unfinished work.

## Rules

**Q1. A correction made twice becomes a check.** When the same class of mistake is corrected a second time, the
fix is not another sentence. Climb this ladder and stop at the first rung that works: redesign so it cannot happen;
a type; a lint or guard script whose message says how to fix the cause; a test; prose, only for a judgement call.
Say in the pull request which rung and why not a higher one.

**Q2. A new check is proved able to fail.** Show it failing on the real past mistake or on a deliberately broken
copy, then passing. This is the rule already held for tests, applied to guards, lint rules and models.

**Q3. Exceptions live on the line.** An allow-list entry or a suppression sits beside the offending line with a
reason and an expiry date, and a maintainer approves it. No silent, permanent exceptions.

**Q4. Boundaries are directories, and a program checks them.** Code that must not import other code lives in a
directory named for that boundary, and an import check forbids the crossing. A boundary held only by review does not
count as held.

**Q5. What needs nuance is banned outside its home.** If an API is easy to misuse, product code may not call it;
one module owns it and offers the safe form. The ban is a check, not a note.

**Q6. Comments.** Delete narration, banners, commented-out code, history, and any comment that explains why a
workaround is acceptable. Keep licence headers; a constraint forced by a platform, vendor or protocol the project
cannot change; the contract of a public API; a link to the design it implements. A comment or docstring that states
a rule about this code names the test or guard that enforces it, or the rule is encoded and the comment removed.

**Q7. Parse once, at the edge.** Outside data becomes a typed value where it enters. Inside, no raw dicts, no
magic strings, no states that should not exist. Closed sets are enums.

**Q8. Subtract first.** Before adding, remove what the change makes dead. Migrate callers and delete the old path
in the same pull request. A workaround left in the tree is a pattern the next agent copies.

**Q9. Fix the cause.** A guard around a symptom is a fix only when the state it handles is legitimate.

**Q10. Done means the real thing was run.** Not "it compiles", not an agent's own report, not a cached screenshot.
The pull request carries the command and its output, or the frames. For UI, frames someone looked at.

**Q11. Rules over all small cases beat a few examples.** A pure module with a stated rule (ordering, caps,
expiry, conservation) carries one test that walks every small case and asserts the rule, beside the example tests.

**Q12. Review the evidence.** A reviewer reads the verification artifacts first and the diff second. A review
finding counts only if a command reproduces it; style opinions are not findings.

**Q13. Names carry the meaning.** No one-letter names outside a two-line scope, no line that needs scrolling, one
job per function. If a reader needs a comment to follow it, reshape it.

## Review checklist

1. Is there evidence of the real thing running? Command and output, or frames. If not, return it.
2. Does every rule the code states name its enforcing test or guard, and does that test fail when the rule is
   broken? Try one.
3. Any comment that excuses a workaround, narrates, or records history?
4. Any raw dict, magic string or impossible state past the entry point?
5. Did the change add a path without deleting the one it replaces?
6. Any new exception, allow-list entry or suppression without a reason, an expiry and an approval?
7. Does a new import cross a boundary that only review is holding? Then the finding is "no check", not "bad import".
8. Is this the second time this class of mistake was made? Then the fix is a check (Q1).
9. Would a careful engineer reading it cold follow it without the pull request description?
