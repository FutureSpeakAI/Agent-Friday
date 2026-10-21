"""Writes through a connected service's own API, in the egress ledger
(north star §26.7, no unannounced egress; BD-15 slice P4).

`recorded(svc, channel, provider, destination)` wraps a googleapiclient
service object. Reads pass through untouched (they fetch the owner's own data
from their own account). Every WRITE method (insert, patch, update, delete,
quickAdd, move, import, create, updateContact ...) records one ledger row when
its request is executed: the channel, where it went, whether it went through,
and the body it carried (fingerprinted, and by default kept encrypted, A20).

Wrapping the client rather than each call site means a new write added to a
service that builds its client through here is recorded without anyone
remembering to; the guard test (tests/unit/test_no_unannounced_egress.py)
checks that every place that builds such a client wraps it.
"""
from __future__ import annotations

from typing import Any

#: googleapiclient method names that change something on the service.
WRITE_METHODS = frozenset({
    "insert", "patch", "update", "delete", "quickAdd", "move", "import_", "create", "clear",
    "createContact", "updateContact", "deleteContact", "batchCreateContacts", "batchUpdateContacts",
    "batchDeleteContacts", "send", "modify", "trash", "untrash", "batchModify", "batchDelete",
})


class _Request:
    def __init__(self, req: Any, rec: "recorded", method: str, kwargs: dict):
        self._req, self._rec, self._method, self._kwargs = req, rec, method, kwargs

    def execute(self, *a, **k):
        try:
            out = self._req.execute(*a, **k)
        except Exception:
            self._rec._record(self._method, self._kwargs, "failed")
            raise
        self._rec._record(self._method, self._kwargs, "sent")
        return out

    def __getattr__(self, name):
        return getattr(self._req, name)


class _Resource:
    def __init__(self, res: Any, rec: "recorded", path: str):
        self._res, self._rec, self._path = res, rec, path

    def __getattr__(self, name):
        attr = getattr(self._res, name)
        if not callable(attr):
            return attr

        def call(*a, **kwargs):
            out = attr(*a, **kwargs)
            if name in WRITE_METHODS:
                return _Request(out, self._rec, f"{self._path}.{name}", kwargs)
            # A sub-resource (events().instances, people().connections) or a
            # read request: wrap resources so their writes are seen too.
            if hasattr(out, "execute"):
                return out
            return _Resource(out, self._rec, f"{self._path}.{name}")
        return call


class recorded:
    """A service client whose writes are recorded. Behaves like `svc`."""

    def __init__(self, svc: Any, channel: str, provider: str, destination: str):
        self._svc, self._channel, self._provider, self._destination = svc, channel, provider, destination

    def _record(self, method: str, kwargs: dict, result: str) -> None:
        try:
            from agent_friday.services import egress_gate as _eg
            body = kwargs.get("body")
            content = {"method": method, "body": body} if body is not None else {"method": method}
            recipients = None
            if isinstance(body, dict) and isinstance(body.get("attendees"), list):
                recipients = len(body["attendees"])
            _eg.record_service_send(self._channel, self._provider, destination=self._destination,
                                    content=content, result=result, recipients=recipients,
                                    part=method.split(".")[-1])
        except Exception:
            pass

    def __getattr__(self, name):
        attr = getattr(self._svc, name)
        if not callable(attr):
            return attr

        def call(*a, **kwargs):
            return _Resource(attr(*a, **kwargs), self, name)
        return call


def calendar(svc: Any) -> Any:
    return recorded(svc, "calendar", "google-calendar", "www.googleapis.com (Calendar)")


def tasks(svc: Any) -> Any:
    return recorded(svc, "tasks", "google-tasks", "tasks.googleapis.com")


def contacts(svc: Any) -> Any:
    return recorded(svc, "contacts", "google-contacts", "people.googleapis.com")
