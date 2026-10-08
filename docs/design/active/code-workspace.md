# Repository-first Code workspace

The Code workspace organizes work around one selected codebase. A catalog sits
beside the source tools, with the selected codebase's Friday conversation beside
them on wide screens and below them in smaller panes. It uses the existing
Knowledge-style repository atlas and Agent Friday brand tokens.

## The working path

1. Choose a managed codebase, or choose a local repository and explicitly create
   a study copy. The catalog distinguishes these kinds of work.
2. Use **Understand** to follow files, symbols, relationships and a guided tour.
   A source link opens that file in the same codebase's editor.
3. Use **Files**, **Changes**, **Preview** and **Run** to edit, review, inspect the
   result and ask for a command through the existing salon execution path.
4. Explore, Learn and Adapt use the selected codebase's actual conversation.
   Adapt begins with a plan. The visible native chat retains model controls,
   privacy decisions and the conversation's unsent draft. Code keeps citation
   controls and per-message sources; the all-chats daily source dossier remains
   available in ordinary chat, rather than being presented as repository context.

The project catalog searches registered salon codebases and repositories found
under the configured Projects folder. It does not infer an association from
matching names. A local repository's Git tools explicitly act on the original
folder; the study copy's tools act on the managed copy. Global process and log
views are labeled as activity across Friday, not as project-specific results.

## Ownership and continuity

Selecting a repository performs no write, checkout or execution. **Study a
copy** uses the existing bounded text intake. It excludes dependencies, generated
files, binary assets and credential material. The copy is neither a synchronized
repository nor a guarantee of a runnable app. Refreshing its map reads the copy.

One codebase id controls the center pane, and its verified conversation binding
controls the chat. The host checks the conversation immediately before sending;
the UI cannot dispatch an atlas or run request to a different active chat. An
inactive Code workspace cannot take over the current conversation. Delayed
responses are tied to the selection or conversation that started them.

Repository read, understand, edit and undo calls resolve an implicit codebase
once before the permission checks. The checks and the handler receive that same
target even if the conversation is rebound while the call is awaiting approval.
Existing-folder writes retain their existing permission policy.

Unsaved file edits block switching the codebase or replacing the editor with a
creation form. A catalog refresh keeps the current dirty editor even if its
metadata changed elsewhere, and independently rechecks the conversation binding.
An older catalog read cannot erase a newly created codebase. Navigation requests
are consumed once; refresh never replays an earlier project selection.

## Implementation

`static/friday_code_studio.js` supplies the workspace arrangement.
`FridayCodebasePanel` supplies the existing tools. `CodeWS` connects the native
conversation owner and legacy repository activity components. The served UI and
JSX mirror load the same studio script and stylesheet. There is no additional
graph renderer, model provider, editor backend or application server.

Keyboard users can select catalog entries and use the graph's existing controls.
The layout responds to available pane width, preserving readable source and
conversation areas in both Simple and Classic presentation styles.
