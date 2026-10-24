"""A fake Google Tasks service for the phone-list tests (services/tasks_inbox).

It keeps what the real API keeps and nothing more [Tasks API v1, read 2026-10-22]:
ids, an etag that changes on every write, `updated`, `status`, `completed`,
`deleted` (returned only with showDeleted), `hidden`, one level of `parent`, and
`position`. Like the real API it says nothing about WHO changed a task.

The helpers at the bottom act as "another device or app" (the owner's phone, a
borrowed phone, an app with Tasks access): tick, untick, edit, reorder, delete,
add a foreign item. Nothing here touches the network (LESSONS L8, L11).
"""
from __future__ import annotations

import itertools
import time
from datetime import datetime, timezone


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


class _Call:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeTasks:
    def __init__(self):
        self.lists: dict = {}
        self.store: dict = {}          # every task, by id (not `tasks`: that is the API method)
        self.writes: list = []          # (method, list, task) for every write made through the API
        self._n = itertools.count(1)
        self._last = time.time()

    # ── bookkeeping ──
    def _stamp(self, t: dict, at: float | None = None) -> None:
        now = at if at is not None else max(time.time(), self._last + 0.001)
        self._last = max(self._last, now)
        t["etag"] = '"e%d"' % next(self._n)
        t["updated"] = _iso(now)

    def _public(self, t: dict) -> dict:
        return {k: v for k, v in t.items() if not k.startswith("_")}

    # ── the API surface tasks_inbox uses ──
    def tasklists(self):
        fake = self

        class _Lists:
            def insert(self, body):
                def run():
                    lid = "list%d" % next(fake._n)
                    fake.lists[lid] = {"id": lid, "title": body.get("title")}
                    fake.writes.append(("tasklists.insert", lid, None))
                    return dict(fake.lists[lid])
                return _Call(run)

            def get(self, tasklist):
                def run():
                    if tasklist not in fake.lists:
                        raise RuntimeError("404")
                    return dict(fake.lists[tasklist])
                return _Call(run)

            def delete(self, tasklist):
                def run():
                    fake.lists.pop(tasklist, None)
                    fake.writes.append(("tasklists.delete", tasklist, None))
                    return {}
                return _Call(run)

            def list(self, maxResults=10):
                return _Call(lambda: {"items": [dict(v) for v in fake.lists.values()]})

        return _Lists()

    def tasks(self):
        fake = self

        class _Tasks:
            def insert(self, tasklist, body, parent=None):
                def run():
                    tid = "task%d" % next(fake._n)
                    t = {"id": tid, "_list": tasklist, "title": body.get("title", ""),
                         "notes": body.get("notes", ""), "status": "needsAction",
                         "position": "%020d" % next(fake._n)}
                    if parent:
                        t["parent"] = parent
                    fake._stamp(t)
                    fake.store[tid] = t
                    fake.writes.append(("tasks.insert", tasklist, tid))
                    return fake._public(t)
                return _Call(run)

            def patch(self, tasklist, task, body):
                def run():
                    t = fake.store[task]
                    if t.get("deleted"):
                        raise RuntimeError("404")
                    for k in ("title", "notes", "status"):
                        if k in body:
                            t[k] = body[k]
                    if body.get("status") == "completed":
                        t["completed"] = _iso(time.time())
                    fake._stamp(t)
                    fake.writes.append(("tasks.patch", tasklist, task))
                    return fake._public(t)
                return _Call(run)

            def delete(self, tasklist, task):
                def run():
                    fake._delete(task)
                    fake.writes.append(("tasks.delete", tasklist, task))
                    return {}
                return _Call(run)

            def list(self, tasklist, showCompleted=True, showHidden=False, showDeleted=False,
                     showAssigned=False, maxResults=20, updatedMin=None, pageToken=None):
                def run():
                    since = _parse(updatedMin) if updatedMin else None
                    out = []
                    for t in fake.store.values():
                        if t["_list"] != tasklist:
                            continue
                        if t.get("deleted") and not showDeleted:
                            continue
                        if t.get("status") == "completed" and not showCompleted:
                            continue
                        if since is not None and _parse(t["updated"]) < since:
                            continue
                        out.append(fake._public(t))
                    return {"items": out}
                return _Call(run)

        return _Tasks()

    def _delete(self, task: str, at: float | None = None) -> None:
        t = self.store[task]
        t["deleted"] = True
        self._stamp(t, at)
        for child in self.store.values():
            if child.get("parent") == task and not child.get("deleted"):
                child["deleted"] = True
                self._stamp(child, at)

    # ── another device or app ──
    def phone_tick(self, task: str, at: float | None = None) -> None:
        t = self.store[task]
        t["status"] = "completed"
        t["completed"] = _iso(at or time.time())
        self._stamp(t, at)

    def phone_untick(self, task: str) -> None:
        t = self.store[task]
        t["status"] = "needsAction"
        t.pop("completed", None)
        self._stamp(t)

    def phone_edit(self, task: str, title: str | None = None, notes: str | None = None) -> None:
        t = self.store[task]
        if title is not None:
            t["title"] = title
        if notes is not None:
            t["notes"] = notes
        self._stamp(t)

    def phone_reorder(self, task: str) -> None:
        t = self.store[task]
        t["position"] = "%020d" % next(self._n)
        self._stamp(t)

    def phone_delete(self, task: str, at: float | None = None) -> None:
        self._delete(task, at)

    def phone_add(self, tasklist: str, title: str) -> str:
        tid = "foreign%d" % next(self._n)
        t = {"id": tid, "_list": tasklist, "title": title, "notes": "", "status": "needsAction",
             "position": "0"}
        self._stamp(t)
        self.store[tid] = t
        return tid

    # ── reading ──
    def items_in(self, tasklist: str, include_deleted: bool = False) -> list:
        return [self._public(t) for t in self.store.values()
                if t["_list"] == tasklist and (include_deleted or not t.get("deleted"))]

    def top_level(self, tasklist: str) -> list:
        return [t for t in self.items_in(tasklist) if not t.get("parent")]

    def children(self, task: str) -> list:
        return [self._public(t) for t in self.store.values() if t.get("parent") == task and not t.get("deleted")]
