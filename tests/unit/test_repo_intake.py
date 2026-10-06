"""Local repository intake copies source and never operates on its checkout."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_friday.services import codebases as cb
from agent_friday.services import repo_intake as intake


@pytest.fixture
def local_source(monkeypatch, tmp_path):
    home = tmp_path / "home"
    source = home / "projects" / "sample"
    source.mkdir(parents=True)
    (source / "README.md").write_text("# Sample\nA repository to study.\n", encoding="utf-8")
    runtime = home / ".friday"
    monkeypatch.setattr(intake, "user_home", lambda: home)
    monkeypatch.setattr(intake, "friday_home", lambda: runtime)
    monkeypatch.setattr(cb, "_root", lambda: runtime / "codebases")
    # Place-based checks retain their production implementation with isolated roots.
    monkeypatch.setattr(intake.credential_paths, "_home", lambda: home)
    monkeypatch.setattr(intake.credential_paths, "_friday", lambda: runtime)
    calls = []

    def fake_git(repo, *args):
        calls.append((repo, args))
        if args[0] == "init":
            (repo / ".git").mkdir()

    monkeypatch.setattr(intake, "_git", fake_git)
    return source, calls


def test_snapshot_preserves_source_and_configuration_in_a_new_managed_repo(local_source):
    source, calls = local_source
    (source / "src").mkdir()
    (source / "src" / "main.py").write_text("def hello():\n    return 'hi'\n", encoding="utf-8")
    (source / ".github" / "workflows").mkdir(parents=True)
    (source / ".github" / "workflows" / "check.yml").write_text("name: Check\n", encoding="utf-8")
    (source / ".gitignore").write_text("*.py\n", encoding="utf-8")
    (source / "LICENSE").write_text("Example license\n", encoding="utf-8")
    (source / ".git").mkdir()
    (source / ".git" / "HEAD").write_text("ref: refs/heads/source-branch\n", encoding="utf-8")
    before = {str(p.relative_to(source)): p.read_bytes() for p in source.rglob("*") if p.is_file()}

    record = intake.create("Study Sample", str(source))

    repo = cb.repo_path(record["id"])
    assert repo != source and cb.is_managed(record["id"])
    assert cb.load(record["id"])["source_snapshot"] == record["source_snapshot"]
    assert (repo / ".github" / "workflows" / "check.yml").read_text() == "name: Check\n"
    assert (repo / ".gitignore").read_text() == "*.py\n"
    assert (repo / "LICENSE").read_text() == "Example license\n"
    assert (repo / "src" / "main.py").is_file()
    assert not (repo / ".git" / "HEAD").exists()
    assert all(path == repo for path, _ in calls)
    assert [args[0] for _, args in calls] == ["init", "add", "commit"]
    assert calls[1][1] == ("add", "-f", "--all", "--", ".")
    assert before == {str(p.relative_to(source)): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    snapshot = record["source_snapshot"]
    assert snapshot["copied_files"] == 5 and snapshot["skipped_entries"] == 1
    assert snapshot["source_name"] == "sample" and len(snapshot["sha256"]) == 64
    assert str(source) not in json.dumps(snapshot)
    assert "source" not in record and record["existing"] is False


def test_graph_artifacts_are_preserved_but_intermediates_are_skipped(local_source):
    source, _ = local_source
    for directory in (".ua", ".understand-anything"):
        graph_dir = source / directory
        (graph_dir / "intermediate").mkdir(parents=True)
        (graph_dir / "knowledge-graph.json").write_text('{"version":"1.0.0","nodes":[]}', encoding="utf-8")
        (graph_dir / "intermediate" / "batch-1.json").write_text("{}", encoding="utf-8")
    record = intake.create("Graphs", str(source))
    repo = cb.repo_path(record["id"])
    assert (repo / ".ua" / "knowledge-graph.json").is_file()
    assert (repo / ".understand-anything" / "knowledge-graph.json").is_file()
    assert not (repo / ".ua" / "intermediate").exists()
    assert record["source_snapshot"]["skipped_by_reason"]["analysis_scratch"] == 2


def test_credentials_links_and_generated_files_never_enter_the_snapshot(local_source):
    source, _ = local_source
    for directory in (".aws", ".ssh", "node_modules", "dist", ".friday", ".codex"):
        (source / directory).mkdir()
        (source / directory / "data.txt").write_text("excluded data", encoding="utf-8")
    (source / ".env").write_text("REGION=example\n", encoding="utf-8")
    (source / ".env.local").write_text("REGION=example\n", encoding="utf-8")
    (source / ".env.example").write_text("REGION=example\n", encoding="utf-8")
    (source / "settings.yaml").write_text("password: example123!\n", encoding="utf-8")  # pragma: allowlist secret
    (source / "picture.png").write_bytes(b"\x89PNG\x00\xff")
    (source / "notes.txt").write_text("ordinary notes\n", encoding="utf-8")
    record = intake.create("Safe copy", str(source))
    repo = cb.repo_path(record["id"])
    assert (repo / ".env.example").is_file() and (repo / "notes.txt").is_file()
    assert not (repo / "settings.yaml").exists() and not (repo / "picture.png").exists()
    assert not (repo / ".env").exists() and not (repo / ".env.local").exists()
    counts = record["source_snapshot"]["skipped_by_reason"]
    assert counts["excluded_folders"] == 6 and counts["credential_files"] == 2
    assert counts["secret_content"] == 1 and counts["binary_or_encoding"] == 1


def test_hidden_credential_directory_is_refused_as_the_selected_root(local_source):
    source, _ = local_source
    credential_dir = source / ".aws"
    credential_dir.mkdir()
    (credential_dir / "notes.txt").write_text("not a credential", encoding="utf-8")
    with pytest.raises(ValueError, match="reserved"):
        intake.create("No", str(credential_dir))
    assert not cb._root().exists()


def test_credential_containers_and_content_gated_files_are_omitted_before_policy_reads(local_source, monkeypatch):
    source, _ = local_source
    names = {"credentials.json", "secrets.yaml", "tokens.json", "certificate.pem", "private.key"}
    for name in names:
        (source / name).write_text('{"password":"ordinaryword"}', encoding="utf-8")
    original = intake.credential_paths.check
    def check(path, **kwargs):
        assert path.name not in names, "credential helper must not sniff these before bounded intake"
        return original(path, **kwargs)
    monkeypatch.setattr(intake.credential_paths, "check", check)
    record = intake.create("No credentials", str(source))
    repo = cb.repo_path(record["id"])
    assert all(not (repo / name).exists() for name in names)
    assert record["source_snapshot"]["skipped_by_reason"]["credential_files"] == len(names)


def test_content_gated_source_root_is_refused_before_policy_inspection(local_source, monkeypatch):
    source, _ = local_source
    folder = source / "bundle.key"
    folder.mkdir()
    def check(*args, **kwargs):
        pytest.fail("a content-gated source root must be rejected before policy inspection")
    monkeypatch.setattr(intake.credential_paths, "check", check)
    with pytest.raises(ValueError, match="reserved"):
        intake.create("No", str(folder))


@pytest.mark.parametrize("value", ["https://example.test/repo.git", "//server/share/repo", r"\\server\share\repo", "", "C:relative"])
def test_remote_and_ambiguous_paths_are_refused(local_source, value):
    with pytest.raises(ValueError):
        intake.create("No", value)
    assert not cb._root().exists()


def test_mapped_network_drive_is_refused_before_source_access(local_source, monkeypatch):
    source, _ = local_source
    monkeypatch.setattr(intake, "_local_drive", lambda path: False)
    def unreadable(*args, **kwargs):
        pytest.fail("network-backed source must not be read")
    monkeypatch.setattr(Path, "lstat", unreadable)
    with pytest.raises(ValueError, match="mapped network drive"):
        intake.create("No", str(source))


def test_home_and_ancestor_roots_are_refused(local_source):
    source, _ = local_source
    home = source.parents[1]
    for path in (home, home.parent, Path(home.anchor)):
        with pytest.raises(ValueError, match="repository folder"):
            intake.create("No", str(path))


def test_runtime_and_managed_roots_are_refused(local_source):
    source, _ = local_source
    runtime = source.parents[1] / ".friday"
    managed = runtime / "codebases" / "cb-example" / "repo"
    managed.mkdir(parents=True)
    for path in (runtime, managed):
        with pytest.raises(ValueError, match="runtime or managed"):
            intake.create("No", str(path))


def test_source_size_limit_fails_before_allocating_a_partial_copy(local_source, monkeypatch):
    source, _ = local_source
    monkeypatch.setattr(intake, "MAX_FILE_BYTES", 8)
    with pytest.raises(ValueError, match="file exceeds"):
        intake.create("Too big", str(source))
    assert not cb._root().exists()


def test_oversized_known_binary_assets_are_omitted_without_reading(local_source, monkeypatch):
    source, _ = local_source
    for name in ("demo.GIF", "parser.wasm", "manual.pdf"):
        (source / name).write_bytes(b"\x00" * 200)
    monkeypatch.setattr(intake, "MAX_FILE_BYTES", 40)
    monkeypatch.setattr(intake, "MAX_TOTAL_BYTES", 100)
    original = intake._read
    reads = []
    def read(path, root, maximum):
        reads.append(path.name)
        assert path.suffix.casefold() not in intake._BINARY_SUFFIXES
        return original(path, root, maximum)
    monkeypatch.setattr(intake, "_read", read)
    record = intake.create("Source with assets", str(source))
    assert reads == ["README.md"]
    assert record["source_snapshot"]["copied_files"] == 1
    assert record["source_snapshot"]["skipped_by_reason"]["binary_or_encoding"] == 3


@pytest.mark.parametrize("limit", ["MAX_FILES", "MAX_TOTAL_BYTES", "MAX_ENTRIES", "MAX_DEPTH"])
def test_collection_limits_fail_instead_of_truncating(local_source, monkeypatch, limit):
    source, _ = local_source
    (source / "nested").mkdir()
    (source / "nested" / "file.txt").write_text("some content", encoding="utf-8")
    monkeypatch.setattr(intake, limit, 0)
    with pytest.raises(ValueError, match="limit"):
        intake.create("Too much", str(source))
    assert not cb._root().exists()


def test_graph_uses_its_separate_bounded_limit(local_source, monkeypatch):
    source, _ = local_source
    (source / ".ua").mkdir()
    (source / ".ua" / "knowledge-graph.json").write_text('{"nodes":[]}' + " " * 60, encoding="utf-8")
    monkeypatch.setattr(intake, "MAX_FILE_BYTES", 40)
    monkeypatch.setattr(intake, "MAX_GRAPH_BYTES", 100)
    record = intake.create("Graph", str(source))
    assert record["source_snapshot"]["copied_files"] == 2


def test_omitted_binary_files_still_count_toward_total_read_budget(local_source, monkeypatch):
    source, _ = local_source
    (source / "a.bin").write_bytes(b"\x00" * 30)
    (source / "b.bin").write_bytes(b"\x00" * 30)
    monkeypatch.setattr(intake, "MAX_TOTAL_BYTES", 75)
    with pytest.raises(ValueError, match="read size limit"):
        intake.create("Too much", str(source))
    assert not cb._root().exists()


def test_hardlinked_files_are_omitted(local_source):
    source, _ = local_source
    original = source.parent / "shared.txt"
    original.write_text("outside data", encoding="utf-8")
    try:
        os.link(original, source / "alias.txt")
    except OSError:
        pytest.skip("Hard links are unavailable on this filesystem")
    record = intake.create("Links", str(source))
    assert not (cb.repo_path(record["id"]) / "alias.txt").exists()
    assert record["source_snapshot"]["skipped_by_reason"]["linked_entries"] == 1


def test_directory_entry_link_count_hint_does_not_replace_full_file_metadata(local_source, monkeypatch):
    source, _ = local_source
    original = intake.os.scandir
    class DirectoryEntries:
        def __init__(self, folder):
            with original(folder) as entries:
                self.entries = [SimpleNamespace(
                    name=entry.name, path=entry.path,
                    stat=lambda **kwargs: SimpleNamespace(st_mode=stat.S_IFREG, st_nlink=0, st_file_attributes=0),
                ) for entry in entries]
        def __enter__(self):
            return iter(self.entries)
        def __exit__(self, *_):
            return False
    monkeypatch.setattr(intake.os, "scandir", DirectoryEntries)
    record = intake.create("Windows enumeration", str(source))
    assert record["source_snapshot"]["copied_files"] == 1
    assert "linked_entries" not in record["source_snapshot"]["skipped_by_reason"]
    assert (cb.repo_path(record["id"]) / "README.md").read_text() == (source / "README.md").read_text()


def test_symlinked_files_and_directories_are_not_followed(local_source):
    source, _ = local_source
    outside = source.parent / "outside"
    outside.mkdir()
    (outside / "data.txt").write_text("outside data", encoding="utf-8")
    try:
        (source / "linked").symlink_to(outside, target_is_directory=True)
        (source / "alias.txt").symlink_to(outside / "data.txt")
    except OSError:
        pytest.skip("Symbolic links are unavailable on this filesystem")
    record = intake.create("Links", str(source))
    assert record["source_snapshot"]["skipped_by_reason"]["linked_entries"] == 2
    assert not (cb.repo_path(record["id"]) / "linked").exists()
    with pytest.raises(ValueError, match="original folder"):
        intake.create("No", str(source / "linked"))


def test_windows_reparse_attribute_is_treated_as_a_link():
    assert intake._linked(SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400))
    assert not intake._linked(SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0))


def test_content_detection_failure_closes_the_entire_import(local_source, monkeypatch):
    source, _ = local_source
    def fail(_):
        raise RuntimeError("detector unavailable")
    monkeypatch.setattr(intake.secret_patterns, "contains_secret", fail)
    with pytest.raises(ValueError, match="no snapshot"):
        intake.create("No", str(source))
    assert not cb._root().exists()


def test_changed_source_is_not_published(local_source, monkeypatch):
    source, _ = local_source
    original = intake._source_info
    count = 0
    def race(path, root):
        nonlocal count
        if path.name == "README.md":
            count += 1
            if count == 2:
                path.write_text("changed during copy", encoding="utf-8")
        return original(path, root)
    monkeypatch.setattr(intake, "_source_info", race)
    with pytest.raises(ValueError, match="changed"):
        intake.create("No", str(source))
    assert not cb._root().exists()


def test_failed_destination_setup_rolls_back_only_its_new_directory(local_source, monkeypatch):
    source, _ = local_source
    survivor = cb._root() / "cb-existing"
    survivor.mkdir(parents=True)
    (survivor / "keep.txt").write_text("keep", encoding="utf-8")
    def fail(*_):
        raise ValueError("init failed")
    monkeypatch.setattr(intake, "_git", fail)
    with pytest.raises(ValueError, match="init failed"):
        intake.create("No", str(source))
    assert list(cb._root().iterdir()) == [survivor]
    assert (source / "README.md").is_file()


def test_destination_collision_does_not_remove_existing_directory(local_source, monkeypatch):
    source, _ = local_source
    monkeypatch.setattr(cb, "new_id", lambda: "cb-collision")
    existing = cb._dir("cb-collision")
    existing.mkdir(parents=True)
    (existing / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        intake.create("No", str(source))
    assert (existing / "keep.txt").read_text() == "keep"


def test_git_bootstrap_disables_inherited_hooks_config_and_filters(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setenv("GIT_DIR", "source-control")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "untrusted-hooks")
    def capture(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(intake.subprocess, "run", capture)
    intake._git(tmp_path, "init", "-q", "-b", "main")
    command, kwargs = calls[0]
    assert "init.templateDir=" in command
    assert "core.hooksPath=" + str(tmp_path / ".git" / "disabled-hooks") in command
    assert kwargs["env"]["GIT_CONFIG_GLOBAL"] == os.devnull
    assert kwargs["env"]["GIT_CONFIG_NOSYSTEM"] == "1"
    assert "GIT_DIR" not in kwargs["env"] and "GIT_CONFIG_COUNT" not in kwargs["env"]
    assert "shell" not in kwargs


def test_utf16_source_is_saved_as_editable_utf8(local_source):
    source, _ = local_source
    (source / "script.ps1").write_bytes("Write-Output 'hello'\n".encode("utf-16"))
    record = intake.create("Encoding", str(source))
    assert (cb.repo_path(record["id"]) / "script.ps1").read_text(encoding="utf-8") == "Write-Output 'hello'\n"


def test_conversation_binding_receives_only_the_new_codebase(local_source, monkeypatch):
    source, _ = local_source
    bindings = []
    monkeypatch.setattr(cb, "bind", lambda cid, conversation_id: bindings.append((cid, conversation_id)))
    record = intake.create("Study", str(source), conversation_id="conversation-example")
    assert bindings == [(record["id"], "conversation-example")]
