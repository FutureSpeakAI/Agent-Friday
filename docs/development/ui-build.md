# UI build

## Which file is the UI

`index.html` at the repository root is the file the server serves and the
source of truth. It contains precompiled React (`React.createElement` calls),
with React and Three.js vendored under `static/vendor/` so the interface works
offline.

`ui_parts/app.html` is a hand-maintained JSX mirror. Every top-level component
in `app.html` also exists in `index.html`, but the reverse is not true: a
number of shipped components exist only in `index.html`. The mirror is kept for
readability and history; it is not what runs.

A UI change therefore edits `index.html` directly and, where the component
exists in both files, keeps `app.html` in step.

## The build tool

`src/agent_friday/ui/build_ui.py` assembles `index.html` from `ui_parts/`.
Because the mirror is a strict subset, a naive build would delete shipped
code. The build refuses to write output that drops any top-level component
the existing `index.html` defines, and names the components it would lose.
`--force` overrides that refusal; use it only when you intend to discard the
named components.

The precompile step falls back to in-browser Babel when it cannot run. Check
that precompilation actually happened rather than assuming it.

## Editing safely

- The server reads `index.html` from disk on every request, so a partial
  edit reaches a running app immediately. `scripts/ui_stage.py` stages an
  edit so a half-finished page is never served.
- After editing compiled code in `index.html`, syntax-check the function you
  touched: extract it to a scratch file, prefix
  `const {useState,useRef,useEffect}=React;`, and run `node --check`.
- Keep `# pragma: allowlist secret` style markers where they appear in
  comments around key-handling inputs; the scanner reads the HTML too.

## Tests that read the UI files

`tests/unit/test_cost_panel_is_reachable.py` and
`tests/gauntlet/test_app_html_missing_high_severity_components.py` parse the
HTML files and pin the presence of specific components. Run them after any
structural UI change.
