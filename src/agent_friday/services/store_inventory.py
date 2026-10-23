"""Every store Friday keeps, with its version, its size and where it lives
(north star §37.3-1: "inventory every existing store and version").

One list, ``STORES``, names each durable store in the data folder: its
location relative to the folder, its format and how its version is recorded.
``inventory(home)`` looks at the folder and reports, for each store that is
there, its version (or "none" when the store records none), its size and
file count, and when it last changed. The Doctor page shows it, the Doctor
report carries it, and every Friday Bundle and every readable export puts it
in its manifest, so a later migration knows exactly what it starts from.

Read-only, and careful about what it reads:

* nothing is created, migrated, locked or written; SQLite files are read by
  their 100-byte header (``PRAGMA user_version`` lives there) and, for the one
  store that keeps its version in a table, opened read-only and immutable, so
  no -wal or -shm file appears;
* stores that hold credentials (account tokens, provider keys, connector
  secrets, Friday's own keys, browser cookies) are listed with their size and
  never opened;
* links and junctions are never followed: a store reached through one is
  listed as a link and not read or measured;
* a JSON file is opened only when it is small, and only its version field is
  kept; a JSONL file only has its first line read.

Names that are not in ``STORES`` (a file the owner dropped in the folder, a
store from a newer Friday) are counted with their size, never named: the
inventory goes into the Doctor report, which holds no file names.
"""
from __future__ import annotations

import json
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

INVENTORY_VERSION = 1

#: Version rules. ("json", keys): the first of these top-level keys in the
#: JSON object. ("jsonl", key): that key in the first line. ("sqlite",): the
#: header's user_version. ("sqlite_meta", table, kcol, key, vcol): a row of a
#: version table. ("in", rel, rule): the rule applied to a file inside the
#: folder ("*" in rel matches one name). ("none",): the store records no
#: version. ("secret",): never opened.
_J = ("json", ("version", "schema_version", "format", "v"))
_L = ("jsonl", "v")
_S = ("sqlite",)
_N = ("none",)
_X = ("secret",)

MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_LINE_BYTES = 64 * 1024
#: Walking a folder stops after this many entries or this long in all; the
#: sizes then say "at least".
MAX_ENTRIES = 400_000
TIME_BUDGET_S = 8.0

_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


@dataclass(frozen=True)
class Store:
    name: str
    location: str          # relative, POSIX; a folder ends in "/"
    fmt: str
    version: tuple = _N
    durable: bool = True   # False: a download, cache, log or this run's state

    @property
    def is_folder(self) -> bool:
        return self.location.endswith("/")


def _f(name, loc, fmt, version=_N, durable=True):
    return Store(name, loc, fmt, version, durable)


STORES: tuple[Store, ...] = (
    # settings, persona, identity
    _f("settings", "settings.json", "json", _J),
    _f("config_yaml", "config.yaml", "yaml", _X),
    _f("setup_complete_marker", ".setup_complete", "marker"),
    _f("soul", "SOUL.md", "markdown"),
    _f("soul_history", "soul_history/", "markdown folder"),
    _f("personality", "personality.json", "json", _J),
    _f("agent_personality", "agent-personality.txt", "text"),
    _f("avatar_growth", "evolution.json", "json", _J),
    _f("privacy_shield", "privacy_shield.json", "json", _J),
    _f("mcp_servers", "mcp_servers.json", "json", _X),
    _f("channels", "channels.json", "json", _X),
    _f("platforms_config", "platforms.json", "json", _X),
    _f("onboarding", "onboarding.json", "json", _J),
    _f("setup_chat", "setup_chat.json", "json", _J),
    _f("connections_choice", "connections_choice.json", "json", _J),
    _f("cloud_release", "cloud_release.json", "json", _J),
    _f("update_check", "update_check.json", "json", _J),
    _f("user_id", "user_id", "text"),
    _f("agent_id", ".agent-id", "text"),
    _f("steer", "STEER.md", "markdown"),
    _f("agent_stop", "AGENT_STOP", "marker"),
    _f("build_hours", "BUILD_HOURS", "text"),
    # memory and knowledge
    _f("core_memory", "core_memory/", "markdown and jsonl folder"),
    _f("memory", "memory/", "folder"),
    _f("memory_json", "memory.json", "json", _J),
    _f("user_model", "user_model.db", "sqlite", _S),
    _f("user_model_json", "user_model.json", "json", _J),
    _f("learning", "learning.db", "sqlite", _S),
    _f("dreams", "dreams.db", "sqlite", _S),
    _f("knowledge_graph", "knowledge-graph/", "folder"),
    _f("wiki", "wiki/", "markdown folder"),
    _f("wiki_pending", "wiki-pending.json", "json", _J),
    _f("library", "library/", "sqlite and json folder", ("in", "*/library.sqlite", _S)),
    _f("instructions", "instructions/", "markdown and jsonl folder", ("in", "manifest.json", _J)),
    _f("people_graph", "people_graph.json", "json", _J),
    _f("trust_graph", "trust_graph.json", "json", _J),
    _f("trajectories", "trajectories.jsonl", "jsonl", _L),
    _f("epistemic_scores", "epistemic_scores.json", "json", _J),
    _f("epistemic_history", "epistemic_history.jsonl", "jsonl", _L),
    _f("epistemic_governance_events", "epistemic_governance_events.jsonl", "jsonl", _L),
    _f("learning_records", "learning_records/", "json folder", ("in", "records.json", _J)),
    _f("learning_folder", "learning/", "folder"),
    _f("dreams_folder", "dreams/", "folder"),
    _f("session_summaries", "session_summaries/", "folder"),
    _f("self_improvement", "self_improvement/", "folder"),
    _f("user_profile", "user_profile.json", "json", _J),
    _f("usage_patterns", "usage_patterns.json", "json", _J),
    # conversations
    _f("conversations", "conversations/", "jsonl folder"),
    _f("conversation_index", "conversations/index.sqlite", "sqlite",
       ("sqlite_meta", "meta", "k", "version", "v")),
    _f("chat_history_legacy", "chat_history.json", "json", _J),
    # receipts and records
    _f("action_ledger", "ledger/", "jsonl hash chain", ("in", "actions.jsonl", _L)),
    _f("action_journal_legacy", "actions/", "jsonl folder", ("in", "receipts.jsonl", _L)),
    _f("decision_bom_legacy", "decision-bom.jsonl", "jsonl", _L),
    _f("decisions", "decisions.jsonl", "jsonl", _L),
    _f("decision_probes", "decisions-probes.jsonl", "jsonl", _L),
    _f("gate_events", "gate_events.jsonl", "jsonl", _L),
    _f("approvals", "approvals.json", "json", _J),
    _f("activity_ledger", "activity_ledger.jsonl", "jsonl", _L),
    _f("sent_mail", "sent_mail.jsonl", "jsonl", _L),
    _f("spend_halts", "spend_halts.jsonl", "jsonl", _L),
    _f("traces", "traces/", "jsonl folder", ("in", "ledger.jsonl", _L)),
    _f("task_journals", "tasks/", "jsonl folder"),
    _f("setup_record", "setup/", "jsonl folder", ("in", "ledger.jsonl", _L)),
    _f("provenance", "provenance/", "jsonl folder"),
    _f("governance_records", "governance/", "jsonl folder"),
    _f("trust", "trust/", "json and jsonl folder"),
    _f("trash", "trash/", "folder"),
    _f("inbox_cleanup", "inbox_cleanup/", "folder"),
    _f("costs", "costs.db", "sqlite", _S),
    _f("work_log", "work_log.db", "sqlite", _S),
    _f("budgets", "budgets.db", "sqlite", _S),
    _f("arbiter", "arbiter.db", "sqlite", _S),
    _f("economy", "economy.db", "sqlite", _S),
    _f("ownership", "ownership.db", "sqlite", _S),
    _f("login_throttle", "login_throttle.db", "sqlite", _S),
    # goals, schedules, work
    _f("goals", "goals/", "folder"),
    _f("todos", "todos.json", "json", _J),
    _f("schedules", "schedules.json", "json", _J),
    _f("schedule_runs", "schedule_runs.jsonl", "jsonl", _L),
    _f("notifications", "notifications.json", "json", _J),
    _f("notification_triggers", "notif_trigger_state.json", "json", _J),
    _f("desktop_cards", "desktop_cards.json", "json", _J),
    _f("interruptions", "interruptions.json", "json", _J),
    _f("situation_pins", "situation_pins.json", "json", _J),
    _f("subagent_scopes", "subagent_scopes.json", "json", _J),
    _f("setting_changes", "setting_changes.json", "json", _J),
    _f("stand_down", "stand_down.json", "json", _J),
    _f("moderation_policy", "moderation_policy.json", "json", _J),
    _f("message_rules", "message_rules.json", "json", _J),
    _f("work_orders", "work_orders.enc", "encrypted"),
    _f("work_queue", "work_queue/", "folder"),
    _f("workflows", "workflows/", "folder"),
    _f("code_plans", "code_plans/", "folder"),
    _f("interactive_sessions", "interactive_sessions/", "folder"),
    _f("vibe_code", "vibe-code/", "folder"),
    _f("projects", "projects/", "folder"),
    _f("futurespeak_projects", "futurespeak_projects.json", "json", _J),
    _f("job_tracker", "job_tracker.json", "json", _J),
    _f("email_send_migration", "email_send_migration.json", "json", _J),
    _f("crew", "crew/", "folder"),
    _f("pipelines", "pipelines/", "folder"),
    _f("routines", "routines/", "folder"),
    # skills and workspaces
    _f("skills", "skills/", "folder"),
    _f("skillopt", "skillopt/", "jsonl folder"),
    _f("workspaces", "workspaces/", "folder"),
    _f("codebases", "codebases/", "folder"),
    _f("workspace_studio", "workspace_studio/", "folder"),
    _f("browser_workspaces", "browser-workspaces/", "folder"),
    # made things, media, research
    _f("creations", "creations/", "folder"),
    _f("friday_creations", "friday-creations/", "folder"),
    _f("creations_meta", "creations_meta/", "folder"),
    _f("content", "content/", "folder"),
    _f("content_pipeline", "content_pipeline.db", "sqlite", _S),
    _f("content_policies", "content_policies.db", "sqlite", _S),
    _f("timelines", "timelines/", "folder"),
    _f("podcasts", "podcasts/", "folder"),
    _f("media", "media/", "folder"),
    _f("media_index", "media/index.sqlite", "sqlite", _S),
    _f("research", "research/", "folder"),
    _f("contacts_research", "contacts-research/", "folder"),
    _f("outreach", "outreach/", "folder"),
    _f("profile", "profile/", "folder"),
    _f("sites", "sites/", "folder"),
    _f("published", "published/", "folder"),
    _f("publish_this_pc", "publish-this-pc.json", "json", _J),
    _f("recipes", "recipes/", "folder"),
    _f("distributions", "distros/", "folder"),
    _f("public_lists", "public_lists/", "folder"),
    _f("documents", "documents/", "folder"),
    _f("drafts", "drafts/", "folder"),
    _f("editorials", "editorials/", "folder"),
    _f("front_pages", "front_pages/", "folder"),
    _f("news", "news/", "folder"),
    _f("news_banned_sources", "banned_sources.json", "json", _J),
    _f("news_boosted_sources", "boosted_sources.json", "json", _J),
    _f("briefing_prefs", "briefing_prefs.json", "json", _J),
    _f("read_later", "read_later.json", "json", _J),
    _f("briefing_runs", "briefing_runs/", "folder"),
    _f("finance", "finance/", "folder"),
    _f("health", "health/", "folder"),
    _f("avatar", "avatar/", "folder"),
    # messages and calendar
    _f("messages", "messages/", "folder"),
    _f("calendar", "calendar/", "folder"),
    _f("gmail", "gmail/", "folder"),
    _f("mail_outbox", "mail_outbox/", "folder"),
    _f("offline_queue", "offline_queue/", "folder"),
    _f("flow_queue", "flow-queue/", "folder"),
    _f("meetings", "meetings/", "encrypted folder"),
    _f("phone_spool", "phone/phone.db", "sqlite", _S),
    _f("phone", "phone/", "folder"),
    # the wider network
    _f("federation", "federation.db", "sqlite", _S),
    _f("defederation", "defederation.db", "sqlite", _S),
    _f("marketplace", "marketplace.db", "sqlite", _S),
    _f("compute_jobs", "compute_jobs.db", "sqlite", _S),
    _f("compute_sent_jobs", "compute_sent_jobs.db", "sqlite", _S),
    _f("futurespeak", "futurespeak/", "folder"),
    _f("domains", "domains/", "json folder", ("in", "state.json", _J)),
    # monitoring Friday keeps about herself
    _f("behavioral_monitor", "behavioral_monitor/", "jsonl folder"),
    _f("model_seat_conformance", "model_seat_conformance/", "folder"),
    _f("candidates", "candidates/", "folder"),
    _f("runs", "runs/", "folder"),
    _f("laya", "laya/", "folder"),
    _f("laya_labels", "laya_labels.jsonl", "jsonl", _L),
    _f("voice_tool_usage", "voice_tool_usage.json", "json", _J),
    _f("bench", "bench/", "folder"),
    _f("overrides", "overrides/", "folder"),
    _f("backups_schedule", "backups/", "json folder", ("in", "schedule.json", _J)),
    _f("heartbeat_state", "heartbeat_state.json", "json", _J),
    _f("ambient_state", "ambient_state.json", "json", _J),
    _f("seat_state", "seat_state.json", "json", _J),
    _f("daily_scheduler_state", "daily_scheduler_state.json", "json", _J),
    _f("routine_status", "routine_status.json", "json", _J),
    # vault, keys and credentials: listed, never opened (the vault's own
    # settings file and the keystore's version field excepted)
    _f("vault", "vault/", "encrypted folder", ("in", ".vault_config.json", _J)),
    _f("keystore", "security/keystore.json", "json", _J),
    _f("security", "security/", "credentials folder", _X),
    _f("privacy_records", "privacy/", "folder", _X),
    _f("google_accounts", "google_accounts/", "credentials folder", _X),
    _f("provider_keys", "providers/", "credentials folder", _X),
    _f("mcp_oauth", "mcp_oauth/", "credentials folder", _X),
    _f("platforms", "platforms/", "credentials folder", _X),
    _f("signing", "signing/", "credentials folder", _X),
    _f("tls", "tls/", "credentials folder", _X),
    _f("local_address", "local-address/", "credentials folder", _X),
    _f("google_token_legacy", "google_token.json", "credentials", _X),
    _f("credentials_legacy", "credentials.json", "credentials", _X),
    _f("oauth_keys_legacy", "oauth-keys.json", "credentials", _X),
    _f("web_session_secret", "secret_key", "credentials", _X),
    _f("dotenv", ".env", "credentials", _X),
    _f("desktop_control_grant", "cc_permission", "credentials", _X),
    # not durable: downloads, caches, logs, this run's state
    _f("runtime", "runtime/", "downloads", _N, False),
    _f("models", "models/", "downloads", _N, False),
    _f("local_voice", "local_voice/", "downloads", _N, False),
    _f("voice_assets", "voice_assets/", "downloads", _N, False),
    _f("cache", "cache/", "cache", _N, False),
    _f("audio_cache", "audio-cache/", "cache", _N, False),
    _f("gmail_cache", "gmail-cache.json", "cache", _N, False),
    _f("tool_output", "tool-output/", "cache", _N, False),
    _f("tmp", "tmp/", "cache", _N, False),
    _f("publish_staging", "publish-staging/", "cache", _N, False),
    _f("logs", "logs/", "logs", _N, False),
    _f("friday_log", "friday.log", "log", _N, False),
    _f("server_stderr_log", "server_stderr.log", "log", _N, False),
    _f("voice_debug_log", "voice_debug.log", "log", _N, False),
    _f("voice_install_log", "voice-install.log", "log", _N, False),
    _f("vibe_code_logs", "vibe-code-logs/", "logs", _N, False),
    _f("payload_trace", "payload_trace.jsonl", "log", _N, False),
    _f("doctor_reports", "doctor/", "reports on this PC", _N, False),
    _f("safe_mode", "safe_mode/", "this run state", _N, False),
    _f("boot_guard", "boot_guard/", "this run state", _N, False),
    _f("restore_state", "restore/", "this run state", _N, False),
    _f("startup_report", "startup-report.json", "this run state", _N, False),
    _f("server_lock", "friday_server.lock", "this run state", _N, False),
    _f("server_pid", "friday_server.pid", "this run state", _N, False),
    _f("server_port", "friday_server.port", "this run state", _N, False),
    _f("browser_profile", "browser-profile/", "browser cookies", _X, False),
    _f("browser_sessions", "browser-sessions/", "browser cookies", _X, False),
)

#: Names checked when telling a known top-level entry from an unknown one
#: (lower case: Windows folders are not case-sensitive).
_KNOWN_TOPS = frozenset(s.location.rstrip("/").split("/")[0].lower() for s in STORES)


# ── looking without touching ─────────────────────────────────────────────────

def _is_link(st: os.stat_result) -> bool:
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT)


def _reach(home: Path, rel: str) -> tuple[Optional[Path], Optional[os.stat_result], bool]:
    """(path, lstat, through_a_link) for `rel` under `home`, checking every
    component: a link or junction anywhere on the way is never followed."""
    p = home
    st = None
    for part in [x for x in rel.rstrip("/").split("/") if x]:
        p = p / part
        try:
            st = os.lstat(p)
        except OSError:
            return None, None, False
        if _is_link(st):
            return p, st, True
    return p, st, False


class _Budget:
    def __init__(self):
        self.deadline = time.monotonic() + TIME_BUDGET_S
        self.entries = 0

    def spent(self) -> bool:
        return self.entries >= MAX_ENTRIES or time.monotonic() > self.deadline


def _measure(folder: Path, budget: _Budget) -> dict:
    """Bytes and files in `folder`, without following links or junctions."""
    total = files = links = 0
    newest = 0.0
    partial = False
    stack = [folder]
    while stack:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            partial = True
            continue
        with it:
            for e in it:
                budget.entries += 1
                if budget.spent():
                    partial = True
                    stack.clear()
                    break
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    partial = True
                    continue
                if _is_link(st):
                    links += 1
                    continue
                if stat.S_ISDIR(st.st_mode):
                    stack.append(Path(e.path))
                elif stat.S_ISREG(st.st_mode):
                    files += 1
                    total += st.st_size
                    newest = max(newest, st.st_mtime)
    out = {"bytes": total, "files": files, "modified": _iso(newest) if newest else None}
    if links:
        out["links_not_followed"] = links
    if partial:
        out["at_least"] = True
    return out


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _short(v):
    """A version value as it may be reported: a number or a short word."""
    if isinstance(v, bool):
        return "unrecognised"
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str) and 0 < len(v) <= 40 and all(c.isalnum() or c in "._-+" for c in v):
        return v
    return "unrecognised"


def _version_json(p: Path, keys) -> tuple:
    if os.lstat(p).st_size > MAX_JSON_BYTES:
        return "not_read_too_large", None
    with open(p, "rb") as f:
        d = json.loads(f.read().decode("utf-8-sig"))
    if isinstance(d, dict):
        for k in keys:
            if k in d and d[k] is not None:
                return _short(d[k]), "json:" + k
    return "none", None


def _version_jsonl(p: Path, key: str) -> tuple:
    with open(p, "rb") as f:
        first = f.readline(MAX_LINE_BYTES)
    if not first.strip():
        return "empty", None
    try:
        d = json.loads(first.decode("utf-8-sig"))
    except ValueError:
        return "unreadable", None
    if isinstance(d, dict) and d.get(key) is not None:
        return _short(d[key]), "first line:" + key
    return "none", None


_SQLITE_MAGIC = b"SQLite format 3\x00"


def _version_sqlite(p: Path) -> tuple:
    with open(p, "rb") as f:
        head = f.read(100)
    if len(head) < 100:
        return ("empty", None) if not head else ("unreadable", None)
    if not head.startswith(_SQLITE_MAGIC):
        return "encrypted_or_not_sqlite", None
    uv = int.from_bytes(head[60:64], "big")
    return (uv, "sqlite:user_version") if uv else ("none", None)


def _version_sqlite_meta(p: Path, table: str, kcol: str, key: str, vcol: str) -> tuple:
    import sqlite3
    ver, how = _version_sqlite(p)
    if ver in ("empty", "unreadable", "encrypted_or_not_sqlite"):
        return ver, how
    # read-only and immutable: no lock is taken and no -wal or -shm is made
    uri = p.resolve().as_uri() + "?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True, timeout=1)
    try:
        row = conn.execute('SELECT "%s" FROM "%s" WHERE "%s" = ?' % (vcol, table, kcol), (key,)).fetchone()
    except sqlite3.Error:
        row = None
    finally:
        conn.close()
    if row and row[0] is not None:
        return _short(row[0]), "sqlite:%s.%s" % (table, key)
    return ver, how


def _apply(rule: tuple, p: Path) -> tuple:
    kind = rule[0]
    if kind == "json":
        return _version_json(p, rule[1])
    if kind == "jsonl":
        return _version_jsonl(p, rule[1])
    if kind == "sqlite":
        return _version_sqlite(p)
    if kind == "sqlite_meta":
        return _version_sqlite_meta(p, *rule[1:])
    return "none", None


def _version(home: Path, s: Store, base: Path) -> tuple:
    rule = s.version
    if rule[0] == "none":
        return "none", None
    if rule[0] == "secret":
        return "not_read", None
    try:
        if rule[0] == "in":
            rel, inner = rule[1], rule[2]
            if "*" in rel:
                head, _, tail = rel.partition("*/")
                found = []
                try:
                    names = sorted(e.name for e in os.scandir(base / head) if not e.name.startswith("."))
                except OSError:
                    names = []
                for name in names[:50]:
                    q, st, linked = _reach(home, s.location + head + name + "/" + tail)
                    if q is not None and not linked and st is not None and stat.S_ISREG(st.st_mode):
                        found.append(_apply(inner, q))
                vers = sorted({str(v) for v, _h in found})
                if not vers:
                    return "none", None
                return (found[0][0] if len(vers) == 1 else "mixed:" + ",".join(vers)[:40]), found[0][1]
            q, st, linked = _reach(home, s.location + rel)
            if q is None or st is None:
                return "none", None
            if linked or not stat.S_ISREG(st.st_mode):
                return "not_read_link", None
            v, how = _apply(inner, q)
            return v, (rel + " " + how) if how else None
        return _apply(rule, base)
    except Exception:  # noqa: BLE001 - one store never stops the inventory
        return "unreadable", None


# ── the inventory ────────────────────────────────────────────────────────────

def inventory(home: Optional[Path] = None) -> dict:
    """What is in the data folder, store by store. Read-only.

    Returns {inventory_version, made_at, app, stores: [...], absent: [names],
    unlisted: {entries, bytes}, totals: {durable_bytes, other_bytes}}.
    Each store: {store, location, format, durable, version, version_from,
    bytes, files, modified} (+ "link": true when reached through a link and so
    not read, + "at_least": true when the walk ran out of time or entries).
    """
    if home is None:
        from agent_friday.paths import friday_home
        home = friday_home()
    home = Path(home)
    budget = _Budget()
    stores, absent = [], []
    durable_bytes = other_bytes = 0
    for s in STORES:
        p, st, linked = _reach(home, s.location)
        if p is None or st is None:
            absent.append(s.name)
            continue
        row = {"store": s.name, "location": s.location, "format": s.fmt, "durable": s.durable}
        if linked:
            row.update(version="not_read_link", version_from=None, bytes=None, files=None,
                       modified=None, link=True)
            stores.append(row)
            continue
        if s.is_folder != stat.S_ISDIR(st.st_mode):
            row.update(version="unexpected_kind", version_from=None, bytes=None, files=None,
                       modified=None)
            stores.append(row)
            continue
        if s.is_folder:
            row.update(_measure(p, budget))
        else:
            row.update(bytes=st.st_size, files=1, modified=_iso(st.st_mtime))
        ver, how = _version(home, s, p)
        row["version"], row["version_from"] = ver, how
        stores.append(row)
        if "/" not in s.location.rstrip("/"):       # top level: counted once
            if s.durable:
                durable_bytes += row.get("bytes") or 0
            else:
                other_bytes += row.get("bytes") or 0
    unlisted = {"entries": 0, "bytes": 0}
    try:
        with os.scandir(home) as it:
            for e in it:
                if e.name.lower() in _KNOWN_TOPS:
                    continue
                unlisted["entries"] += 1
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                if _is_link(st):
                    continue
                if stat.S_ISDIR(st.st_mode):
                    m = _measure(Path(e.path), budget)
                    unlisted["bytes"] += m["bytes"]
                    if m.get("at_least"):
                        unlisted["at_least"] = True
                elif stat.S_ISREG(st.st_mode):
                    unlisted["bytes"] += st.st_size
    except OSError:
        pass
    return {"inventory_version": INVENTORY_VERSION, "made_at": _iso(time.time()),
            "app": _app(), "stores": stores, "absent": absent, "unlisted": unlisted,
            "totals": {"durable_bytes": durable_bytes, "other_bytes": other_bytes}}


def _app() -> dict:
    out = {}
    try:
        from agent_friday import release
        out.update(release=release.RELEASE_TAG, build_sequence=release.BUILD_SEQUENCE)
    except Exception:  # noqa: BLE001
        pass
    return out


def human_size(n) -> str:
    if n is None:
        return "?"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024
    return "%.1f GB" % n


def render_text(inv: dict) -> list[str]:
    """Lines for a plain-text report."""
    lines = ["Stores (inventory v%s): %d present, %d not created yet" % (
        inv.get("inventory_version"), len(inv.get("stores") or []), len(inv.get("absent") or []))]
    for r in inv.get("stores") or []:
        lines.append("  %-28s %-14s %10s  %s%s" % (
            r.get("store"), str(r.get("version")), human_size(r.get("bytes")),
            r.get("location"), "" if r.get("durable", True) else "  (not durable)"))
    u = inv.get("unlisted") or {}
    if u.get("entries"):
        lines.append("  other entries in the data folder: %d, %s" % (u["entries"], human_size(u.get("bytes"))))
    return lines
