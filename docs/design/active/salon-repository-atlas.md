# Understand repositories in the salon

Status: implemented on the feature branch; deployment and broad-suite validation are separate checks.

The salon's **Understand** tab helps a person explore an unfamiliar repository,
learn a technique, or plan an adaptation while retaining source references.
It is an independent Friday implementation inspired by
[Understand Anything](https://github.com/Egonex-AI/Understand-Anything), with
compatible graph import. No upstream runner or dashboard is bundled.

## Workflow

Use **+ Codebase → Study a repository** and choose a local repository folder.
Friday creates a managed text snapshot. The original folder, branch and history
are untouched. Dependency folders, generated output, binary files and credential
material are omitted, and the snapshot records its coverage. It is a source
study, not a promise that the application is ready to run. Fetch a remote repo
through the normal governed coding workflow before studying its local folder.

Open **Understand** in any codebase. The map builds on first open. Search for a
file or symbol, follow its relationships, open its source, or move through the
guided tour. **Explore**, **Learn** and **Adapt** ask in the codebase's own chat;
the selected node id is resolved again by the tool. Adaptation starts with an
explanation and a plan through the salon's existing plan workflow.

The local scanner reads files only. Python definitions and imports use the
standard AST; JavaScript and TypeScript extraction is heuristic. Other supported
languages are inventoried by file. Local layers are directory groups, not an
inference of business architecture. File, byte, node and elapsed-time limits keep
inspection bounded; partial coverage stays visible. Refresh scans current file
contents, including uncommitted changes. Managed study snapshots change only when
edited in the salon; they do not synchronize with the original repository.

## Portable graph integration

The adapter reads `.understand-anything/knowledge-graph.json` or
`.ua/knowledge-graph.json` when present, validates references and contained source
paths, and retains valid upstream nodes, edges, layers and tours. Imported prose
is untrusted analysis. Its correspondence to current source is not assumed;
the UI and model brief carry the import freshness limitation. Invalid or oversized
graphs fall back to a local map with a warning.

Compatibility was checked against upstream commit
`790b157028637b626c8666fa7fa28248944b900c` (plugin 2.9.7), whose `version: "1.0.0"`
codebase schema is defined in `packages/core/src/types.ts`. This adapter does not
run upstream's worktree-to-main redirect. Any future use of its runner must set
`UNDERSTAND_NO_WORKTREE_REDIRECT=1` and preserve Friday's checkout boundaries.
Upstream is MIT, copyright Yuxiang Lin and Infinite Universe, Inc.; copied
substantial code or prompts must retain its full license notices.

## Contracts and limits

- `GET /api/codebases/<id>/atlas` authenticates through the existing codebase
  route. `?refresh=1` bypasses parsed-graph reuse. Responses are not browser cached.
- `codebase_understand(mode, node_id?, query?, codebase_id?)` resolves the chat's
  codebase, returns a bounded source-linked brief, and is governed as a scoped
  read. Missing scope remains outward. Model output still crosses Friday's
  existing egress controls; no model provider or second server is introduced.
- Atlas building never executes source, imports its modules, runs package
  scripts, changes Git branches, or makes network requests. Source paths reject
  traversal and links; source and imported metadata pass credential screening.
- Explore/Learn/Adapt handoffs contain ids and user intent, not raw source or
  untrusted graph descriptions. Source content reaches the model through its
  normal read tools and context.
- Local scanning is a quick structural view. It does not reproduce upstream's
  Tree-sitter coverage, semantic multi-agent analysis, business-domain modeling,
  vector search, call-graph accuracy or incremental model enrichment.

Implementation: `services/repo_intake.py`, `services/repo_atlas.py`,
`services/repo_atlas_context.py`, `routes/codebases.py`, the
`codebase_understand` tool, and `static/friday_repo_atlas.js` in the existing panel.
Focused tests cover source isolation, limits, graph integrity, content freshness,
conversation scope, route authentication and bounded briefs. The new read
classification in `governance/action_gate.py` requires sensitive-subsystem review.
