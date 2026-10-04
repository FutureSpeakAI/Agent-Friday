"""The Library's index: one SQLite file per principal.

Isolation is by separate files, never by a WHERE clause one bug could drop:
`store_for(principal)` opens `<friday home>/library/<principal>/library.sqlite`
and nothing else. Open-shelf text is plain (like every Friday index); a vault
shelf document's block and passage text is sealed with the vault key, kept out
of full-text search, and readable only while the vault is unlocked.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable, Iterable

from agent_friday import paths

SCHEMA_VERSION = 1
# A document indexed under an older version is read again at the next sweep, so this is raised
# whenever what an index may hold changes (version 2: credentials are withheld before indexing).
INDEX_VERSION = 2
_PRINCIPAL = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
OWNER = "owner"


class VaultLocked(Exception):
    """A vault-shelf document was read while the vault is locked."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS folders(
  id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL, rel TEXT NOT NULL, name TEXT NOT NULL,
  parent_id INTEGER, UNIQUE(scope, rel));
CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL UNIQUE, sha256 TEXT, size INTEGER, mtime REAL,
  kind TEXT, ext TEXT, title TEXT, pages INTEGER, shelf TEXT NOT NULL DEFAULT 'open',
  folder_id INTEGER, scope TEXT, index_version INTEGER, state TEXT NOT NULL DEFAULT 'queued',
  state_detail TEXT, indexed_at REAL, ocr_pages INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS documents_folder ON documents(folder_id);
CREATE INDEX IF NOT EXISTS documents_state ON documents(state);
CREATE TABLE IF NOT EXISTS sections(
  id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id INTEGER NOT NULL, parent_id INTEGER, ord INTEGER NOT NULL,
  heading TEXT NOT NULL, level INTEGER, first_block INTEGER, last_block INTEGER,
  page_from INTEGER, page_to INTEGER);
CREATE INDEX IF NOT EXISTS sections_doc ON sections(doc_id, ord);
CREATE TABLE IF NOT EXISTS blocks(
  id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id INTEGER NOT NULL, ord INTEGER NOT NULL, section_id INTEGER,
  page INTEGER, t_start REAL, t_end REAL, x0 REAL, y0 REAL, x1 REAL, y1 REAL,
  kind TEXT NOT NULL, level INTEGER, text TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS blocks_doc ON blocks(doc_id, ord);
CREATE TABLE IF NOT EXISTS passages(
  id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id INTEGER NOT NULL, section_id INTEGER NOT NULL,
  block_ids TEXT NOT NULL, char_len INTEGER, text TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS passages_section ON passages(section_id);
CREATE INDEX IF NOT EXISTS passages_doc ON passages(doc_id);
CREATE TABLE IF NOT EXISTS profiles(
  node_kind TEXT NOT NULL, node_id INTEGER NOT NULL, text TEXT NOT NULL,
  PRIMARY KEY(node_kind, node_id));
CREATE TABLE IF NOT EXISTS vectors(
  node_kind TEXT NOT NULL, node_id INTEGER NOT NULL, doc_id INTEGER, vec BLOB NOT NULL,
  PRIMARY KEY(node_kind, node_id));
CREATE INDEX IF NOT EXISTS vectors_doc ON vectors(doc_id);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(title, headings, body, tokenize='porter unicode61');
CREATE TABLE IF NOT EXISTS receipts(id INTEGER PRIMARY KEY, search_id TEXT, ts REAL, data TEXT);
CREATE TABLE IF NOT EXISTS cited_in(
  block_id INTEGER NOT NULL, doc_id INTEGER NOT NULL, conversation_id TEXT, message_id TEXT, ts REAL,
  stamp TEXT, receipt_id TEXT);
CREATE INDEX IF NOT EXISTS cited_doc ON cited_in(doc_id);
CREATE TABLE IF NOT EXISTS tombstones(sha256 TEXT, path TEXT, ts REAL, PRIMARY KEY(sha256, path));
"""


def library_root() -> Path:
    return paths.friday_home() / "library"


def principal_dir(principal: str) -> Path:
    if not _PRINCIPAL.match(principal or ""):
        raise ValueError("invalid principal")
    return library_root() / principal


_PLAIN_HEADER = b"SQLite format 3\x00"


def cipher_module():
    """The optional SQLCipher binding, or None. With it the whole index is
    encrypted at rest; without it the index is a plain SQLite file, protected
    by the Windows account like Friday's other indexes."""
    try:
        from sqlcipher3 import dbapi2
        return dbapi2
    except Exception:
        return None


def _key_clause(key: bytes) -> str:
    return "\"x'%s'\"" % key.hex()


def _is_plain(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(16) == _PLAIN_HEADER
    except OSError:
        return False


def _encrypt_in_place(path: Path, key: bytes, mod) -> None:
    """Rewrite a plain index as an encrypted one (sqlcipher_export), then swap."""
    tmp = path.with_name(path.name + ".enc")
    if tmp.exists():
        tmp.unlink()
    plain = mod.connect(str(path), isolation_level=None)
    try:
        ver = plain.execute("PRAGMA user_version").fetchone()[0]
        plain.execute("ATTACH DATABASE ? AS enc KEY " + _key_clause(key), (str(tmp),))
        plain.execute("SELECT sqlcipher_export('enc')")
        plain.execute("PRAGMA enc.user_version=%d" % int(ver))
        plain.execute("DETACH DATABASE enc")
    finally:
        plain.close()
    for suffix in ("-wal", "-shm"):
        side = path.with_name(path.name + suffix)
        if side.exists():
            side.unlink()
    os.replace(tmp, path)


def key_protection(directory: Path) -> str:
    """How the index key is held: 'vault', 'dpapi' (the Windows account), 'plain', or 'none'."""
    try:
        head = (directory / "index.key").read_bytes()[:12]
    except OSError:
        return "none"
    return "vault" if head.startswith(b"FRIDAYVAULT") else "dpapi" if head.startswith(b"FRIDAYDPAPI") else "plain"


def index_key(directory: Path) -> bytes | None:
    """The 32-byte key of a principal's index, held beside it under the Windows
    account's protection (the credential store), or None when the optional
    SQLCipher binding is not installed."""
    if cipher_module() is None:
        return None
    from agent_friday.services import credential_store as cs
    kp = directory / "index.key"
    if kp.exists():
        try:
            k = cs.read_secret(kp)
            if len(k) == 32:
                return k
        except Exception:
            return None            # an unreadable key is not replaced: that would orphan the index
    k = os.urandom(32)
    directory.mkdir(parents=True, exist_ok=True)
    cs.write_secret(kp, k)
    try:
        cs.harden_permissions(kp)
    except Exception:
        pass
    return k


class IndexUnreadable(Exception):
    """The encrypted index could not be opened with its key."""


class Store:
    def __init__(self, path: Path, *, seal: Callable[[str], str] | None = None,
                 unseal: Callable[[str], str] | None = None, key: bytes | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._seal, self._unseal = seal, unseal
        self.note = ""
        mod = cipher_module() if key else None
        self.encrypted = False
        self.db = None
        if mod is not None and self.path.exists() and _is_plain(self.path):
            try:
                _encrypt_in_place(self.path, key, mod)
            except Exception:
                # A migration that fails leaves the plain index exactly as it was and says so:
                # nothing is renamed, deleted or half-written, and no plaintext copy is set aside.
                tmp = self.path.with_name(self.path.name + ".enc")
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
                mod = None
                self.note = "The index could not be encrypted yet and is still a plain file; it will be tried again."
        if mod is not None:
            try:
                self.db = mod.connect(str(self.path), check_same_thread=False, isolation_level=None)
                self.db.execute("PRAGMA key = " + _key_clause(key))
                self.db.execute("SELECT count(*) FROM sqlite_master").fetchone()
                self.db.row_factory = mod.Row
                self.encrypted = True
            except Exception:
                # The key does not open this file. The index is derived data (the files are the
                # truth): the encrypted copy is set aside as it is and a fresh one is started.
                self._set_aside(mod)
                self.note = "The Library's index could not be opened and was set aside; documents are being read again."
                self.db = mod.connect(str(self.path), check_same_thread=False, isolation_level=None)
                self.db.execute("PRAGMA key = " + _key_clause(key))
                self.db.row_factory = mod.Row
                self.encrypted = True
        if self.db is None:
            if self.path.exists() and self.path.stat().st_size and not _is_plain(self.path):
                # An encrypted index with no key (the optional binding is gone, or the key cannot be read).
                self._set_aside(None)
                self.note = ("The Library's index is encrypted and its key is not available, so it was set aside; "
                             "documents are being read again.")
            self.db = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
            self.db.row_factory = sqlite3.Row
        with self._lock:
            if self._fresh():
                self.db.execute("PRAGMA auto_vacuum=INCREMENTAL")      # only takes effect before the first table
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA secure_delete=ON")
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.executescript(_SCHEMA)
            have = {r[1] for r in self.db.execute("PRAGMA table_info(cited_in)").fetchall()}
            for col in ("stamp", "receipt_id"):
                if col not in have:
                    self.db.execute("ALTER TABLE cited_in ADD COLUMN %s TEXT" % col)
            self.db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def _set_aside(self, mod) -> None:
        """Move an index this process cannot open out of the way (already ciphertext)."""
        try:
            if self.db is not None:
                self.db.close()
        except Exception:
            pass
        self.db = None
        aside = self.path.with_name(self.path.name + ".unreadable")
        if self.path.exists():
            os.replace(self.path, aside)
        for suffix in ("-wal", "-shm"):
            side = self.path.with_name(self.path.name + suffix)
            if side.exists():
                side.unlink()

    def _fresh(self) -> bool:
        return self.db.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0

    # -- sealing --------------------------------------------------------------

    def set_sealer(self, seal, unseal) -> None:
        self._seal, self._unseal = seal, unseal

    def vault_open(self) -> bool:
        return self._seal is not None and self._unseal is not None

    def _enc(self, text: str, shelf: str) -> str:
        if shelf != "vault":
            return text
        if self._seal is None:
            raise VaultLocked("the vault is locked")
        return self._seal(text)

    def dec(self, text: str, shelf: str) -> str:
        if shelf != "vault":
            return text
        if self._unseal is None:
            raise VaultLocked("the vault is locked")
        return self._unseal(text)

    # -- generic --------------------------------------------------------------

    def q(self, sql: str, args: Iterable = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.db.execute(sql, tuple(args)).fetchall()

    def one(self, sql: str, args: Iterable = ()):
        with self._lock:
            return self.db.execute(sql, tuple(args)).fetchone()

    def x(self, sql: str, args: Iterable = ()):
        with self._lock:
            return self.db.execute(sql, tuple(args))

    def close(self) -> None:
        with self._lock:
            try:
                self.db.close()
            except sqlite3.Error:
                pass

    # -- folders --------------------------------------------------------------

    def folder_id(self, scope: str, rel: str) -> int:
        """The folder row for `rel` under `scope` (created with its parents)."""
        rel = rel.replace("\\", "/").strip("/")
        with self._lock:
            row = self.one("SELECT id FROM folders WHERE scope=? AND rel=?", (scope, rel))
            if row:
                return row["id"]
            parent = None
            name = Path(scope).name or scope
            if rel:
                head, _, tail = rel.rpartition("/")
                parent = self.folder_id(scope, head)
                name = tail
            cur = self.x("INSERT INTO folders(scope, rel, name, parent_id) VALUES(?,?,?,?)",
                         (scope, rel, name, parent))
            return cur.lastrowid

    # -- documents ------------------------------------------------------------

    def upsert_document(self, path: str, *, sha256: str | None, size: int, mtime: float, kind: str | None,
                        ext: str | None, scope: str, folder_id: int, title: str, shelf: str = "open") -> int:
        with self._lock:
            row = self.one("SELECT id FROM documents WHERE path=?", (path,))
            if row:
                self.x("UPDATE documents SET sha256=?, size=?, mtime=?, kind=?, ext=?, scope=?, folder_id=?, "
                       "title=COALESCE(title, ?), shelf=?, state='queued', state_detail=NULL WHERE id=?",
                       (sha256, size, mtime, kind, ext, scope, folder_id, title, shelf, row["id"]))
                return row["id"]
            cur = self.x("INSERT INTO documents(path, sha256, size, mtime, kind, ext, title, shelf, folder_id, "
                         "scope, state) VALUES(?,?,?,?,?,?,?,?,?,?, 'queued')",
                         (path, sha256, size, mtime, kind, ext, title, shelf, folder_id, scope))
            return cur.lastrowid

    def set_state(self, doc_id: int, state: str, detail: str | None = None) -> None:
        self.x("UPDATE documents SET state=?, state_detail=? WHERE id=?", (state, detail, doc_id))

    def get_document(self, doc_id: int):
        return self.one("SELECT * FROM documents WHERE id=?", (doc_id,))

    def find_document(self, path: str):
        return self.one("SELECT * FROM documents WHERE path=?", (path,))

    def list_documents(self, state: str | None = None, folder_id: int | None = None) -> list[sqlite3.Row]:
        sql, args = "SELECT * FROM documents", []
        where = []
        if state:
            where.append("state LIKE ?")
            args.append(state + "%")
        if folder_id is not None:
            where.append("folder_id=?")
            args.append(folder_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
        return self.q(sql + " ORDER BY title COLLATE NOCASE", args)

    def counts(self) -> dict:
        rows = self.q("SELECT state, count(*) n FROM documents GROUP BY state")
        by = {r["state"].split(":")[0]: 0 for r in rows}
        for r in rows:
            by[r["state"].split(":")[0]] += r["n"]
        return {"indexed": by.get("indexed", 0), "queued": by.get("queued", 0),
                "failed": by.get("failed", 0), "skipped": by.get("skipped", 0),
                "total": sum(by.values())}

    # -- content --------------------------------------------------------------

    def replace_content(self, doc_id: int, *, title: str, pages: int | None, shelf: str, blocks: list[dict],
                        sections: list[dict], passages: list[dict], doc_profile: str,
                        section_profiles: dict[int, str], ocr_pages: int = 0, sha256: str | None = None) -> dict:
        """Atomically replace everything stored for a document. `blocks` carry
        `ord`; `sections` carry their local `id`/`parent`/`first`/`last`;
        `passages` carry `section` (local id), `blocks` (ordinals), `text`."""
        with self._lock:
            self.x("BEGIN")
            try:
                self._purge_rows(doc_id)
                sec_ids: dict[int, int] = {}
                for s in sections:
                    cur = self.x("INSERT INTO sections(doc_id, parent_id, ord, heading, level, first_block, "
                                 "last_block, page_from, page_to) VALUES(?,?,?,?,?,?,?,?,?)",
                                 (doc_id, sec_ids.get(s["parent"]), s["id"], self._enc(s["heading"], shelf), s["level"],
                                  s["first"], s["last"], s["page_from"], s["page_to"]))
                    sec_ids[s["id"]] = cur.lastrowid
                block_of_ord: dict[int, int] = {}
                sec_of_block: dict[int, int] = {}
                for s in sections:
                    for b in s["blocks"]:
                        sec_of_block[b["ord"]] = sec_ids[s["id"]]
                for b in blocks:
                    bb = b.get("bbox") or [None] * 4
                    cur = self.x("INSERT INTO blocks(doc_id, ord, section_id, page, t_start, t_end, x0, y0, x1, y1, "
                                 "kind, level, text) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                 (doc_id, b["ord"], sec_of_block.get(b["ord"]), b.get("page"), b.get("t0"),
                                  b.get("t1"), bb[0], bb[1], bb[2], bb[3], b["kind"], b.get("level") or 0,
                                  self._enc(b["text"], shelf)))
                    block_of_ord[b["ord"]] = cur.lastrowid
                head_of = {s["id"]: s["heading"] for s in sections}
                for p in passages:
                    ids = [block_of_ord[o] for o in p["blocks"] if o in block_of_ord]
                    cur = self.x("INSERT INTO passages(doc_id, section_id, block_ids, char_len, text) "
                                 "VALUES(?,?,?,?,?)",
                                 (doc_id, sec_ids[p["section"]], json.dumps(ids), p["chars"],
                                  self._enc(p["text"], shelf)))
                    p["id"] = cur.lastrowid
                    if shelf != "vault":
                        self.x("INSERT INTO fts(rowid, title, headings, body) VALUES(?,?,?,?)",
                               (cur.lastrowid, title, head_of[p["section"]], p["text"]))
                self.x("INSERT OR REPLACE INTO profiles(node_kind, node_id, text) VALUES('document',?,?)",
                       (doc_id, self._enc(doc_profile, shelf)))
                for local, text in section_profiles.items():
                    self.x("INSERT OR REPLACE INTO profiles(node_kind, node_id, text) VALUES('section',?,?)",
                           (sec_ids[local], self._enc(text, shelf)))
                self.x("UPDATE documents SET title=?, pages=?, shelf=?, index_version=?, state='indexed', "
                       "state_detail=NULL, indexed_at=?, ocr_pages=?, sha256=COALESCE(?, sha256) WHERE id=?",
                       (title, pages, shelf, INDEX_VERSION, time.time(), ocr_pages, sha256, doc_id))
                self.x("COMMIT")
                return {"sections": sec_ids}          # passages carry their new "id" in place
            except Exception:
                self.x("ROLLBACK")
                raise

    def _purge_rows(self, doc_id: int) -> None:
        """Delete everything stored for a document except the document row."""
        self.x("DELETE FROM fts WHERE rowid IN (SELECT id FROM passages WHERE doc_id=?)", (doc_id,))
        self.x("DELETE FROM vectors WHERE doc_id=?", (doc_id,))
        self.x("DELETE FROM profiles WHERE (node_kind='document' AND node_id=?) OR "
               "(node_kind='section' AND node_id IN (SELECT id FROM sections WHERE doc_id=?))", (doc_id, doc_id))
        self.x("DELETE FROM passages WHERE doc_id=?", (doc_id,))
        self.x("DELETE FROM blocks WHERE doc_id=?", (doc_id,))
        self.x("DELETE FROM sections WHERE doc_id=?", (doc_id,))

    def purge_document(self, doc_id: int, *, keep_row: bool = False) -> None:
        """Remove a document from the Library: rows, FTS entries, vectors and
        profiles, with secure_delete on so freed pages are overwritten."""
        with self._lock:
            self.x("BEGIN")
            try:
                self._purge_rows(doc_id)
                if keep_row:
                    self.x("UPDATE documents SET state='skipped:removed', state_detail=NULL WHERE id=?", (doc_id,))
                else:
                    self.x("DELETE FROM documents WHERE id=?", (doc_id,))
                self.x("COMMIT")
            except Exception:
                self.x("ROLLBACK")
                raise
            self.compact()

    def compact(self) -> None:
        """Make deleted text actually leave the file: merge the full-text index's
        segments (its old ones hold the tokens of deleted passages), then give the
        freed pages back; secure_delete zeroes them as they are freed."""
        try:
            with self._lock:
                try:
                    self.db.execute("INSERT INTO fts(fts) VALUES('optimize')")
                except Exception:
                    pass
                self.db.execute("PRAGMA incremental_vacuum")
                self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass

    def drop_empty_folders(self) -> None:
        with self._lock:
            while self.x("DELETE FROM folders WHERE id NOT IN (SELECT DISTINCT folder_id FROM documents "
                         "WHERE folder_id IS NOT NULL) AND id NOT IN (SELECT DISTINCT parent_id FROM folders "
                         "WHERE parent_id IS NOT NULL)").rowcount:
                pass

    def put_vectors(self, rows: list[tuple[str, int, int | None, bytes]]) -> None:
        """(node_kind, node_id, doc_id, float16 blob) rows, replacing any existing."""
        with self._lock:
            self.x("BEGIN")
            try:
                for r in rows:
                    self.x("INSERT OR REPLACE INTO vectors(node_kind, node_id, doc_id, vec) VALUES(?,?,?,?)", r)
                self.x("COMMIT")
            except Exception:
                self.x("ROLLBACK")
                raise

    # -- receipts and citations (nothing is written off the record) -----------

    def add_receipt(self, search_id: str, data: dict) -> None:
        """Why a search found what it found: ids and numbers, never passage text."""
        from agent_friday.services import off_record
        if off_record.skip("library"):
            return
        self.x("INSERT INTO receipts(search_id, ts, data) VALUES(?,?,?)",
               (search_id, time.time(), json.dumps(data, separators=(",", ":"))))

    def add_citation(self, block_id: int, doc_id: int, conversation_id: str | None,
                     message_id: str | None, stamp: str | None = None, receipt_id: str | None = None) -> None:
        """Record that an answer cited a block, with the versions of the search that found it."""
        from agent_friday.services import off_record
        if off_record.skip("library"):
            return
        self.x("INSERT INTO cited_in(block_id, doc_id, conversation_id, message_id, ts, stamp, receipt_id) "
               "VALUES(?,?,?,?,?,?,?)", (block_id, doc_id, conversation_id, message_id, time.time(), stamp, receipt_id))

    # -- reading --------------------------------------------------------------

    def block(self, block_id: int):
        return self.one("SELECT b.*, d.shelf, d.title, d.path FROM blocks b JOIN documents d ON d.id=b.doc_id "
                        "WHERE b.id=?", (block_id,))

    def block_text(self, row) -> str:
        return self.dec(row["text"], row["shelf"])

    def passage_text(self, row, shelf: str) -> str:
        return self.dec(row["text"], shelf)

    def sections_of(self, doc_id: int) -> list[dict]:
        """The document's sections with headings readable (a vault-shelf heading
        reads 'Locked section' while the vault is locked)."""
        doc = self.get_document(doc_id)
        shelf = doc["shelf"] if doc else "open"
        out = []
        for r in self.q("SELECT * FROM sections WHERE doc_id=? ORDER BY ord", (doc_id,)):
            d = dict(r)
            try:
                d["heading"] = self.dec(d["heading"], shelf)
            except VaultLocked:
                d["heading"] = "Locked section"
            out.append(d)
        return out

    def profile(self, kind: str, node_id: int, shelf: str = "open") -> str | None:
        row = self.one("SELECT text FROM profiles WHERE node_kind=? AND node_id=?", (kind, node_id))
        if not row:
            return None
        try:
            return self.dec(row["text"], shelf)
        except VaultLocked:
            return None

    def tombstoned(self, sha256: str | None, path: str) -> bool:
        return bool(self.one("SELECT 1 FROM tombstones WHERE (sha256=? AND sha256 IS NOT NULL) OR path=?",
                             (sha256, path)))

    def size_bytes(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def delete_everything(self) -> None:
        """Remove the whole principal's index from disk."""
        self.close()
        shutil.rmtree(self.path.parent, ignore_errors=True)


_stores: dict[tuple[str, str], Store] = {}
_stores_lock = threading.Lock()


def store_for(principal: str = OWNER) -> Store:
    """The principal's store. The principal comes from the request context in
    callers, never from a request argument."""
    root = str(library_root())
    key = (root, principal)
    with _stores_lock:
        st = _stores.get(key)
        if st is None:
            d = principal_dir(principal)
            st = Store(d / "library.sqlite", key=index_key(d))
            _stores[key] = st
        return st


def forget_open_stores() -> None:
    with _stores_lock:
        for st in _stores.values():
            st.close()
        _stores.clear()
