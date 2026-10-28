# Which module decides what about paths

Friday has four modules that look at file paths. Each answers one question; none
repeats another's. (NS-37.2-12)

| Module | The question it answers | Who asks |
|---|---|---|
| `services/path_policy.py` | **May a file tool read or write here?** The home folder (without application data, Friday's own state and stores of sign-ins), Friday's output folders, Library and search roots for reading, and folders the owner granted by a press on this PC. Judged on the resolved path. The one rule for where file tools reach (BD-26). | `read_file`, `write_file`, `search_files`, PDF forms, the action gate's command checks, isolated connector folders |
| `services/file_grants.py` | **May this file's content go to a cloud model?** Signed, content-pinned grants and deny marks for sending a file's words off the PC. It also holds the shared path-text checks (unsafe spellings, links, too-broad folders) that `path_policy` calls rather than repeats. | Cloud sends, the Library, knowledge folders, studio files |
| `services/open_safety.py` | **May this file be opened without asking?** A type allowlist for `open_path` (documents, pictures, recordings), with links resolved first. Says nothing about where a file is. | `open_path`, the action gate, item actions |
| `services/path_probe.py` | **Does this path exist?** Without a dead network share stalling the request. Not access control. | Model stores and catalogs, local seats |

Two grant lists exist and are shown in Settings > Privacy:

* **Folders Friday may use** (`path_policy`): where file tools may reach.
* **File access** (`file_grants`): whether a file's words may be sent to a cloud model.

They are separate on purpose: a folder can be open to the file tools and still
closed to the cloud.

Escapes are tested where each boundary lives: file tools in
`tests/unit/test_file_confinement.py`; commands and connector servers in
`tests/unit/test_command_and_mcp_containment.py`, `tests/unit/test_run_command_is_sandboxed.py`
and `tests/unit/test_code_sandbox.py`; network in
`tests/unit/test_egress_allowlist_proxy.py`; isolated connectors in
`tests/unit/test_connector_declared_folders.py`; code sessions in
`tests/unit/test_code_session_worktree.py`; skills in `tests/unit/test_bd26_closeout.py`.
