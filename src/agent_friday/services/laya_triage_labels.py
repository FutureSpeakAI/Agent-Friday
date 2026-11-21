"""A short list of real items for the owner to label, and the score against his answers.

The triage heads (services/triage_heads) read notifications and news in shadow:
they log what they WOULD say beside what the incumbent said, as 16-hex digests,
never words. Nothing can say whether they are right without the owner's own
answer, so this module asks him. It does three things, all on this PC:

  build()    pick a small mixed list of recent real items the shadow log
             disagrees on (and a few near-the-line agreements), find each
             item's words again from the notification store and the news
             archive, and write the list to ~/.friday/laya_triage_queue.json.
  answer()   record one answer, yes / no / skip, as a line in
             ~/.friday/laya_labels.jsonl with a receipt that chains to the
             one before it, so an edited line, or one removed from the middle,
             shows (a removed last line does not: the chain has no head).
  score()    set the owner's yes / no answers against what the heads and the
             incumbent said. A skip is never an answer and never scored.

Nothing here decides anything, changes a head's mode, or sends a word off the
machine. The list file holds item text, so it stays under ~/.friday with the
other stores of the owner's own words.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from agent_friday.services import laya_labels, triage_heads

_log = logging.getLogger("friday.laya_triage_labels")
_LOCK = threading.Lock()

QUEUE_NAME = "laya_triage_queue.json"
NOTIFICATIONS_NAME = "notifications.json"

#: One plain question per head. Yes always means "the head should have raised it".
QUESTIONS = {"notify": "Should this have interrupted you?",
             "news": "Is this worth your attention?"}
HEADS = tuple(QUESTIONS)
ANSWERS = ("yes", "no", "skip")
YES, NO, SKIP = ANSWERS

#: Labels of the news head that mean "not worth attention"; the rest are.
NEWS_NOT_WORTH = frozenset({"promo", "other"})
#: The notify head's label that means "interrupt".
NOTIFY_RAISE = "needs_you"
QUESTION_KEY_PREFIX = "triage_"
#: Why an item is on the list: the two sides answer the question oppositely, or it is a close call.
KIND_DISAGREE, KIND_EDGE = "disagree", "edge"
SUBJECT_PREFIX = "item:"

DEFAULT_LIMIT = 25
#: At most this many of the list come from the notify head: it has far fewer rows than news.
NOTIFY_SHARE = 12
#: Texts are shown on one line.
LINE_CHARS = 160
_GENESIS = "0" * 16


def queue_path() -> Path:
    from agent_friday.core import FRIDAY_DIR
    return FRIDAY_DIR / QUEUE_NAME


def _question_key(head: str) -> str:
    return QUESTION_KEY_PREFIX + head


def _one_line(text: str) -> str:
    line = " ".join(str(text or "").split())
    return line if len(line) <= LINE_CHARS else line[:LINE_CHARS - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
#  CHOOSING THE ITEMS
# ---------------------------------------------------------------------------

def _latest_by_digest(rows: Iterable[dict]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for r in rows:
        if r.get("head") in HEADS and r.get("h") and r.get("label") and r.get("incumbent"):
            out[r["h"]] = r
    return out


def choose(rows: Dict[str, dict], texts: Dict[str, str], limit: int = DEFAULT_LIMIT,
           notify_share: int = NOTIFY_SHARE) -> List[dict]:
    """The items to ask about, mixed between the heads, most informative first.

    Only rows whose words were found (`texts`) can be asked. Within a head the
    disagreements come first, one per (incumbent, head) pair in turn so no one
    kind of disagreement fills the list, then the agreements nearest the line
    (lowest confidence). The two heads are interleaved. Pure and deterministic.
    """
    limit = max(0, limit)
    by_head: Dict[str, List[dict]] = {head: [] for head in HEADS}
    for digest, row in rows.items():
        if texts.get(digest):
            by_head[row["head"]].append(row)
    notify = _spread(by_head["notify"], min(notify_share, limit))
    news = _spread(by_head["news"], limit - len(notify))
    ordered = _interleave(notify, news)
    return [{"id": SUBJECT_PREFIX + row["h"], "head": row["head"], "text": _one_line(texts[row["h"]]),
             "question": QUESTIONS[row["head"]], "head_said": row["label"], "incumbent_said": row["incumbent"],
             "p": row.get("p"), "kind": KIND_DISAGREE if _reads_differently(row) else KIND_EDGE}
            for row in ordered[:limit]]


def _reads_differently(row: dict) -> bool:
    """The two sides land on opposite answers to the owner's question. The head's
    abstain label ("other") and two labels that both mean the same yes or no are not a disagreement:
    his answer could not tell head from incumbent."""
    return _head_says_yes(row["head"], row["label"]) != _head_says_yes(row["head"], row["incumbent"])


def _spread(rows: List[dict], cap: int) -> List[dict]:
    cells: Dict[tuple, List[dict]] = {}
    agree: List[dict] = []
    for row in rows:
        if _reads_differently(row):
            cells.setdefault((row["incumbent"], row["label"]), []).append(row)
        else:
            agree.append(row)
    ranked = sorted(cells.values(), key=lambda cell: (-len(cell), cell[0]["incumbent"], cell[0]["label"]))
    for cell in ranked:
        cell.sort(key=lambda row: (-(row.get("p") or 0.0), row["h"]))
    out: List[dict] = []
    for rank in range(max((len(cell) for cell in ranked), default=0)):
        for cell in ranked:
            if rank < len(cell):
                out.append(cell[rank])
    agree.sort(key=lambda row: ((row.get("p") or 0.0), row["h"]))
    return (out + agree)[:cap]


def _interleave(a: List[dict], b: List[dict]) -> List[dict]:
    out: List[dict] = []
    for i in range(max(len(a), len(b))):
        for src in (a, b):
            if i < len(src):
                out.append(src[i])
    return out


# ---------------------------------------------------------------------------
#  FINDING THE WORDS AGAIN
# ---------------------------------------------------------------------------

def _notification_texts() -> Iterable[str]:
    from agent_friday.core import FRIDAY_DIR
    try:
        data = json.loads((FRIDAY_DIR / NOTIFICATIONS_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    for n in (data.get("items") if isinstance(data, dict) else None) or []:
        if isinstance(n, dict):
            yield "%s. %s" % (n.get("title") or "", n.get("body") or "")


def _news_titles() -> Iterable[str]:
    from agent_friday.services.news_engine import NEWS_ARCHIVE_DIR
    try:
        files = sorted(Path(NEWS_ARCHIVE_DIR).glob("*.json"), reverse=True)
    except OSError:
        return
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        items = data if isinstance(data, list) else (data.get("articles") or data.get("items") or [])
        for a in items:
            if isinstance(a, dict) and a.get("title"):
                yield str(a["title"])


def recover_texts(wanted: Dict[str, dict]) -> Dict[str, str]:
    """digest -> words, for the rows asked about, from this PC's own stores. The digest
    is the one the heads logged (triage_heads.digest of the clipped text)."""
    found: Dict[str, str] = {}
    sources = {"notify": _notification_texts, "news": _news_titles}
    for head, source in sources.items():
        need = {digest for digest, row in wanted.items() if row["head"] == head}
        if not need:
            continue
        for text in source():
            clipped = triage_heads._clip(text)
            digest = triage_heads.digest(clipped)
            if digest in need and digest not in found:
                found[digest] = clipped
                if len(found) == len(wanted):
                    return found
    return found


def build(limit: int = DEFAULT_LIMIT) -> dict:
    """Write the list of items to ask about. Returns what was written, counts only."""
    rows = _latest_by_digest(_read_jsonl(triage_heads.shadow_path()))
    answered = _answered_subjects()
    rows = {h: r for h, r in rows.items() if SUBJECT_PREFIX + h not in answered}
    texts = recover_texts(rows)
    items = choose(rows, texts, limit)
    _write_json(queue_path(), {"v": 1, "items": items})
    heads = {h: sum(1 for i in items if i["head"] == h) for h in HEADS}
    return {"items": len(items), "by_head": heads, "path": str(queue_path()),
            "rows_without_words": len(rows) - len(texts)}


def _read_jsonl(path: Path) -> List[dict]:
    out: List[dict] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    out.append(row)
    except OSError:
        pass
    return out


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
#  ANSWERS AND THEIR RECEIPTS
# ---------------------------------------------------------------------------

def _triage_rows() -> List[dict]:
    return [r for r in _read_jsonl(laya_labels.labels_path())
            if str(r.get("question") or "").startswith(QUESTION_KEY_PREFIX)]


def _answered_subjects() -> Dict[str, dict]:
    """subject -> the newest answer (a changed mind is a new line)."""
    out: Dict[str, dict] = {}
    for row in _triage_rows():
        if isinstance(row.get("subject"), str):
            out[row["subject"]] = row
    return out


def receipt_of(prev: str, row: dict) -> str:
    """16 hex that binds this row to the one before it."""
    body = {k: v for k, v in row.items() if k != "receipt"}
    blob = prev + json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


_ITEM_FIELDS = ("id", "head", "text", "question", "head_said", "incumbent_said")


def queue_items() -> List[dict]:
    """The listed items, read once at the edge: a damaged file, or an entry missing a field, is
    dropped here so nothing past this point has to guess."""
    try:
        data = json.loads(queue_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    listed = data.get("items") if isinstance(data, dict) else None
    return [item for item in listed if _is_item(item)] if isinstance(listed, list) else []


def _is_item(item) -> bool:
    return (isinstance(item, dict) and all(isinstance(item.get(f), str) and item[f] for f in _ITEM_FIELDS)
            and item["head"] in HEADS and item["id"].startswith(SUBJECT_PREFIX))


def queue() -> dict:
    """The items still waiting, and how far along the owner is."""
    items = queue_items()
    done = _answered_subjects()
    waiting = [item for item in items if item["id"] not in done]
    return {"items": waiting, "total": len(items), "answered": len(items) - len(waiting)}


def answer(subject: str, choice: str, *, by: str = "owner") -> dict:
    """Record one answer for a listed item. Raises ValueError for a subject that is not on the list
    or an answer that is not yes / no / skip. Returns the row with its receipt."""
    from agent_friday.user_errors import UserFacingValueError
    if choice not in ANSWERS:
        raise UserFacingValueError("the answer must be yes, no or skip")
    with _LOCK:
        item = next((i for i in queue_items() if i["id"] == subject), None)
        if item is None:
            raise UserFacingValueError("that item is not on the list")
        rows = _triage_rows()
        prev = rows[-1].get("receipt") if rows and rows[-1].get("receipt") else _GENESIS
        row = {"at": time.time(), "subject": subject, "question": _question_key(item["head"]),
               "answer": choice, "by": str(by)[:40], "head_said": item["head_said"],
               "incumbent_said": item["incumbent_said"]}
        row["receipt"] = receipt_of(prev, row)
        _append_line(laya_labels.labels_path(), json.dumps(row, ensure_ascii=False))
    return row


def _append_line(path: Path, line: str) -> None:
    """Append one line, starting a fresh one first if a crash left the file's last line unfinished."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab+") as fh:
        size = fh.seek(0, os.SEEK_END)
        torn = False
        if size:
            fh.seek(size - 1)
            torn = fh.read(1) != b"\n"
        fh.write((b"\n" if torn else b"") + line.encode("utf-8") + b"\n")


def chain_ok() -> bool:
    """True when every answer's receipt matches the one before it."""
    prev = _GENESIS
    for r in _triage_rows():
        if r.get("receipt") != receipt_of(prev, r):
            return False
        prev = r["receipt"]
    return True


# ---------------------------------------------------------------------------
#  THE SCORE
# ---------------------------------------------------------------------------

def _head_says_yes(head: str, said: str) -> bool:
    return said == NOTIFY_RAISE if head == "notify" else said not in NEWS_NOT_WORTH


def score() -> dict:
    """Per head: the owner's yes / no answers against what the head and the incumbent said.

    right        the side agreed with the owner
    missed       the owner said yes, the side said no
    false_alarm  the owner said no, the side said yes
    """
    out: Dict[str, dict] = {h: {"answered": 0, "skipped": 0, "owner_yes": 0, "owner_no": 0,
                                "head": {"right": 0, "missed": 0, "false_alarm": 0},
                                "incumbent": {"right": 0, "missed": 0, "false_alarm": 0},
                                "head_only_right": 0, "incumbent_only_right": 0}
                            for h in HEADS}
    for r in _answered_subjects().values():
        head = str(r.get("question") or "")[len(QUESTION_KEY_PREFIX):]
        if head not in out:
            continue
        s = out[head]
        if r.get("answer") == SKIP:
            s["skipped"] += 1
            continue
        if r.get("answer") not in (YES, NO):
            continue
        owner = r["answer"] == YES
        s["answered"] += 1
        s["owner_yes" if owner else "owner_no"] += 1
        verdict = {}
        for side, said in (("head", r.get("head_said")), ("incumbent", r.get("incumbent_said"))):
            yes = _head_says_yes(head, str(said))
            kind = "right" if yes == owner else ("missed" if owner else "false_alarm")
            s[side][kind] += 1
            verdict[side] = kind == "right"
        if verdict["head"] and not verdict["incumbent"]:
            s["head_only_right"] += 1
        if verdict["incumbent"] and not verdict["head"]:
            s["incumbent_only_right"] += 1
    return out


def main() -> None:
    print(json.dumps(build(), indent=1))


if __name__ == "__main__":
    main()
