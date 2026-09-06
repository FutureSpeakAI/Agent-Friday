# Visual / workspace notes — capture only, no verdicts

Set up 2026-09-04 per Stephen's explicit instruction: the gauntlet's bar
stays binary and evidence-backed ("does the product do what it claims") —
no visual/aesthetic critic joins this loop, and this file is not judged
against that bar. When a finder or critic incidentally notices something
about a workspace's appearance or usability — confusing, empty when it
shouldn't be, two surfaces disagree visually, a control is easy to miss,
a layout obscures what matters, something looks unfinished — it goes here
in a line or two, with where it was seen. No investigation, no fix, no
verdict. This is raw material for a future visual pass, not a finding.

Two things stay OUT of this file because they already have verdicts and
belong in findings.jsonl instead: UI copy that claims something untrue, and
any divergence between index.html and ui_parts/app.html.

---

- **Workspace Studio's entry point is a small, unlabeled icon with no
  onboarding.** `FWin` (index.html:4379) puts a 💬 button in every open
  workspace's title bar (index.html:4516-4526) that opens the
  workspace-scoped customization/undo chat (`WorkspaceChat`,
  index.html:4637). The tooltip text is good ("tweak the layout, add
  features, undo changes") but nothing ever tells a user that button — or
  the whole capability behind it — exists; it's discoverable only by
  noticing a small icon among several others in a window title bar. First
  seen while investigating whether Workspace Studio has a UI surface at
  all (findings.jsonl H12 — it does, contrary to the premise it might not).
