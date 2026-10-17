# Adding a workspace

> **Status:** current · **Last verified against the code:** 2026-10-17 (main b605f2216)

A workspace (News, Messages, Calendar, Library and the rest) is one entry in
the workspace registry, one React component in the UI, one Laya manifest and
two fixture rows. Most of the wiring derives from the registry; the tests
below fail until every piece agrees. Design guidance is in the
[Human Interface Guidelines, section 11](../design/hig/README.md#11-laya-in-every-workspace).

## 1. Register it

`static/workspace_registry.js` holds `window.FRIDAY_WORKSPACE_REGISTRY`, the
one list every surface reads: the dock, titles, deep links, the command
palette, notifications, the navigate tools, voice and Friday's own prompts.
The server parses the JSON between `/*BEGIN JSON*/` and `/*END JSON*/`
(`services/workspace_registry.py`), so it must stay strict JSON: double
quotes, no trailing commas, no comments inside.

An entry has:

| Field | Rule |
|---|---|
| `id` | Stable; deep links, saved layouts and settings key on it. |
| `boundary` | `{"kind": "native", "components": [...], "scripts": [...]}`: the React components in `index.html` and any static scripts that render it. |
| `label` | The name, which is also what people say: short, easy to say, hard to mishear. |
| `group` | `life`, `work` or `system`. |
| `core` | Whether it is a core workspace. |
| `icon`, `glyph` | `assets/icons/<icon>.svg` must exist; `glyph` is the emoji shown while the image loads. |
| `accent` | A brand accent token; never a status hue (green, amber, red). |
| `blurb` | One plain line saying what it is for. |
| `aliases` | Other words people use. No alias may point at two workspaces or be another workspace's label. |
| `held` | Optional: a switch in `settings.held_features`; while it is off nothing offers the workspace. |

`tests/unit/test_workspace_registry_ui.py` checks the icon, distinct names
and aliases, brand accents, the blurb, and that every `boundary` component
exists. `tests/unit/test_workspace_aliases.py` checks the aliases. The Laya
router builds its workspace words from the registry ids and labels
(`services/laya_router.py`), so a new workspace can be opened by voice
without a router edit.

## 2. Build the UI

`index.html` is the served UI and the source of truth; `ui_parts/app.html`
is its hand-maintained mirror ([UI build](ui-build.md)).

1. Add the component (for example `function NewsWS()`) to `index.html`, and
   to `ui_parts/app.html` where you keep the mirror.
2. Add it to `wsMap` in both files.
3. If it has deep links, declare them with
   `fridayDeclareNav('<id>', {key, keys, sections})`, in both files.
   `tests/unit/test_nav_manifest_matches_handlers.py` checks that a handler
   reads only declared keys, that declared sections exist, and that the
   mirror declares the same keys.
4. Syntax-check the compiled function you touched (see [UI build](ui-build.md)),
   and run every test that reads `index.html` or `ui_parts`.

## 3. Write its Laya manifest

Every registry workspace has exactly one manifest,
`src/agent_friday/resources/laya_manifests/<id>.json`, and nothing else has
one. Copy a small existing manifest and change it. Top-level keys:

`schema`, `id`, `label`, `group`, `held` (all four must match the registry),
`spoken_words` (`{"router_extra": []}`), `nav` (`key`, `keys`, `sections`),
`objects`, `verbs`, `jobs`, `not_for_laya`, `resolver_sources`,
`workspace_tools` (`{"key": ..., "pools": [...]}`).

- **Every UI action is exactly one of** a verb or a `not_for_laya` entry.
  - A verb has `id` (equal to `action`), `action`, `group`, `ui.control`,
    `tool`, `also_tools`, `gate` (`class`, `ring`, `card`), `laya_tier`,
    `phrasings`, `deictic`, `undo`, `voice` (`live`, `shared`),
    `quiet_result`, `eval`, and `point_verb` on point-act tiers. The gate
    class must equal the one `governance/action_gate.py` gives the tool, and
    the ring must equal `TOOL_RINGS` in `services/agent.py`. **The manifest
    never loosens a gate**; it describes and `action_gate` decides.
  - A `not_for_laya` entry is `{"action", "kind", "reason"}`, with a reason of
    one line of at most 160 characters. `kind` is `by_design` (an owner-only
    control; it leaves the eligible count) or `gap` (work still owed, which
    needs a `wave` naming a package in `laya_manifests.PACKAGE_IDS`).
- **Objects (rows).** A workspace that publishes rows to the stage declares
  each row kind with `kind`, `stage_workspace`, `noun`, `ref_template`,
  `see_touch`, `open` and `resolver`. `open` names a `navigate_to` kind the
  desktop has (`services/desktop_targets.py` `KINDS`) or, where there is no
  row-level target, `{"tool": null, "none_because": ..., "gap": true, "wave": ...}`.
- **Jobs.** Every button, schedule or idle sweep that starts model work is
  behind `services/visible_jobs.visible_job` and is declared in `jobs` with
  `id`, `trigger`, `model_class`, `helper`, `task_kind`, `activity_label`,
  `entry` and, for a schedule, `schedule_ref`
  ([Visible jobs](visible-jobs.md)).
- **Workspace tools.** If the workspace has its own chat tools, its module
  registers them into `agent.WORKSPACE_TOOLS` under a key, and the manifest's
  `workspace_tools.key` names that key (or is `null`).

## 4. Add the fixture rows

Both fixtures are keyed by every registry id:

- `tests/fixtures/laya_ws_ui_actions.json`: one entry per UI action, ids
  prefixed `ws.`, with an `audit_status` of `L`, `LD`, `M`, `N` or `new`.
- `tests/fixtures/laya_coverage_floor.json`: the workspace's floor, normally
  `{"laya": 0, "pct": 0.0}` for a new one. The floor is a ratchet: measured
  coverage must be at or above it, per workspace and overall, on both the
  covered count and the percentage of Laya-eligible actions. **Coverage may
  only go up.** When a change raises coverage, raise the floor to the new
  value in the same commit; the overall row is derived from the workspaces.

## 5. If it publishes rows to the stage

Only a workspace whose rows Friday can see and touch needs these, and the
contract test names each one that disagrees:

- `services/screen_stage.py`: `STAGE_FACETS`, `FILTER_KEYS`, `NOUNS`,
  `ROW_KINDS`, and `PRIVATE_WORKSPACES` if it publishes counts only;
- `static/workspace_stages.js`;
- `_TICKABLE` in `services/agent.py`, if rows can be ticked;
- `CANDIDATE_SOURCES` in `services/laya_resolver.py`;
- a new `KINDS` value, resolver and alias in `services/desktop_targets.py`
  only if it needs a row-level open target that does not exist yet.

Some contract tests pin exact sets that a new workspace changes on purpose:
the workspaces with no stage of their own (`{"crew", "knowledge",
"marketplace", "settings"}`), the number of stages in
`workspace_stages.js` (12) and the gap rows (`_GAP_ROWS`). Update them in the
same change and say why in the pull request.

## 6. Run the checks

```powershell
pytest tests/test_laya_workspace_contracts.py
pytest tests/unit/test_workspace_registry_ui.py tests/unit/test_workspace_aliases.py tests/unit/test_nav_manifest_matches_handlers.py
python scripts/check_visible_jobs.py
```

CI runs `tests/test_laya_workspace_contracts.py` in its own step of
`tests.yml`, and the other two lines too; run them yourself before you push. Then follow
[Testing and gates](testing-and-gates.md) for the rest of the suite.
