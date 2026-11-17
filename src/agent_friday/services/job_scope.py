"""The closed scope of a peer job and the decision whether one action fits it (NS-40.3-2, -4, -5, -7;
spec: Terms and consent, Event/state rules). TEST CREDITS ONLY: pure, no I/O, nothing is transferred or run.

A signed offer carries a `scope` (the bundle's terms-v2 schema): the exact input digests, their recipient,
media type, byte limit and purpose, the readers, the approved tools and models, hard compute, memory,
storage and output limits, and what may be released or published. The owner's LOCAL GRANT may be narrower.
`decide` allows an action only when BOTH say yes. A refusal names one closed code and says whether it was the
signed scope (so terms may be renegotiated) or the owner's own grant (so they may not).

Rules and the tests that hold them (tests/unit/test_job_scope.py):
- The scope parses exactly as the schema says, with no network, no cloud and no delegation possible:
  test_the_bundle_scope_parses_and_anything_looser_is_refused.
- Every dimension is checked against the signed scope: test_each_dimension_is_denied_by_the_signed_scope.
- Every dimension is checked against the owner's narrower grant: test_each_dimension_is_denied_by_the_owners_grant.
- Both must allow, and a grant can never widen a scope: test_a_grant_never_widens_the_signed_scope.
- Outbound network, cloud and delegation are always denied: test_worker_execution_has_no_network_cloud_or_delegation.
- Candidate bytes and guessable hashes go only to an authorized recipient, only when released:
  test_candidate_bytes_and_hashes_reach_only_an_authorized_recipient.
- Human review needs a named viewer and the source owner's consent: test_human_review_needs_a_named_viewer_and_consent.
- A refusal never echoes the action: test_a_refusal_carries_only_a_closed_code.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

MAX_INT = 2 ** 53 - 1
_HEX64 = re.compile(r"[a-f0-9]{64}")
_MEDIA = re.compile(r"[A-Za-z0-9.+/-]{1,127}")
_PURPOSE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}")
_FIELDS = ("inputs", "readers", "approved_tools", "allowed_models", "compute_ms", "memory_bytes", "storage_bytes",
           "output_bytes", "network", "cloud", "delegate", "preview", "final", "publication", "review_human_keys")
_INPUT_FIELDS = {"digest", "media_type", "max_bytes", "recipient", "purpose"}
_LIMITS = ("compute_ms", "memory_bytes", "storage_bytes", "output_bytes")


class ScopeError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class Deny(str, Enum):
    INPUT = "input-not-in-scope"
    RECIPIENT = "recipient-not-allowed"
    MEDIA_TYPE = "media-type-not-allowed"
    TOO_BIG = "over-the-byte-limit"
    PURPOSE = "purpose-not-allowed"
    TOOL = "tool-not-approved"
    MODEL = "model-not-allowed"
    LIMIT = "over-a-hard-limit"
    VIEWER = "viewer-not-allowed"
    RELEASE = "release-is-held"
    PUBLICATION = "publication-not-allowed"
    NETWORK = "no-outbound-network"
    CLOUD = "no-cloud-fallback"
    DELEGATE = "no-onward-delegation"


class Source(str, Enum):
    SIGNED_SCOPE = "signed-scope"       # terms may be renegotiated
    LOCAL_GRANT = "local-grant"         # the owner's own limit: not negotiable by the peer


@dataclass(frozen=True)
class Decision:
    allowed: bool
    code: Deny | None = None
    source: Source | None = None

    @property
    def renegotiate(self) -> bool:
        return not self.allowed and self.source is Source.SIGNED_SCOPE


ALLOW = Decision(True)


def _hex(value) -> bool:
    return isinstance(value, str) and bool(_HEX64.fullmatch(value))


def _hex_list(value, most) -> tuple:
    if (not isinstance(value, list) or len(value) > most or not all(_hex(v) for v in value)
            or len(set(value)) != len(value)):
        raise ScopeError("hex_list")
    return tuple(value)


def _whole(value) -> int:
    if type(value) is not int or not 0 <= value <= MAX_INT:
        raise ScopeError("limit")
    return value


@dataclass(frozen=True)
class InputRule:
    digest: str
    media_type: str
    max_bytes: int
    recipient: str
    purpose: str


@dataclass(frozen=True)
class Scope:
    inputs: tuple
    readers: tuple
    approved_tools: tuple
    allowed_models: tuple
    compute_ms: int
    memory_bytes: int
    storage_bytes: int
    output_bytes: int
    preview: str
    final: str
    publication: str
    review_human_keys: tuple


def parse_scope(document) -> Scope:
    """The bundle's terms-v2 `scope`, exactly. network must be 'none', cloud and delegate false."""
    if not isinstance(document, dict) or set(document) != set(_FIELDS):
        raise ScopeError("fields")
    if document["network"] != "none":
        raise ScopeError("network")
    if document["cloud"] is not False:
        raise ScopeError("cloud")
    if document["delegate"] is not False:
        raise ScopeError("delegate")
    for name, allowed in (("preview", ("hold", "send_to_requester")), ("final", ("hold", "send_to_requester")),
                          ("publication", ("private", "signed_release_required"))):
        if document[name] not in allowed or not isinstance(document[name], str):
            raise ScopeError(name)
    raw = document["inputs"]
    if not isinstance(raw, list) or len(raw) > 64:
        raise ScopeError("inputs")
    rules = []
    for item in raw:
        if (not isinstance(item, dict) or set(item) != _INPUT_FIELDS or not _hex(item["digest"])
                or not _hex(item["recipient"]) or not isinstance(item["media_type"], str)
                or not _MEDIA.fullmatch(item["media_type"]) or not isinstance(item["purpose"], str)
                or not _PURPOSE.fullmatch(item["purpose"])):
            raise ScopeError("inputs")
        rules.append(InputRule(item["digest"], item["media_type"], _whole(item["max_bytes"]), item["recipient"],
                               item["purpose"]))
    return Scope(tuple(rules), _hex_list(document["readers"], 32), _hex_list(document["approved_tools"], 32),
                 _hex_list(document["allowed_models"], 16), *(_whole(document[n]) for n in _LIMITS),
                 document["preview"], document["final"], document["publication"],
                 _hex_list(document["review_human_keys"], 16))


@dataclass(frozen=True)
class LocalGrant:
    """What the owner allows on this PC. Everything here can only narrow the signed scope."""
    input_digests: frozenset = frozenset()
    recipients: frozenset = frozenset()
    media_types: frozenset = frozenset()
    max_input_bytes: int = 0
    purposes: frozenset = frozenset()
    tools: frozenset = frozenset()
    models: frozenset = frozenset()
    readers: frozenset = frozenset()
    compute_ms: int = 0
    memory_bytes: int = 0
    storage_bytes: int = 0
    output_bytes: int = 0
    may_send_preview: bool = False
    may_send_final: bool = False
    may_publish: bool = False


@dataclass(frozen=True)
class InputTransfer:
    digest: str
    media_type: str
    size: int
    recipient: str
    purpose: str


@dataclass(frozen=True)
class ToolRun:
    tool_digest: str
    model_digest: str | None
    compute_ms: int
    memory_bytes: int
    storage_bytes: int
    output_bytes: int


@dataclass(frozen=True)
class View:
    reader_key: str


@dataclass(frozen=True)
class Release:
    kind: str                  # "preview" or "final"
    recipient: str


@dataclass(frozen=True)
class Publish:
    signed_release: bool = False


@dataclass(frozen=True)
class Network:
    pass


@dataclass(frozen=True)
class CloudFallback:
    pass


@dataclass(frozen=True)
class Delegate:
    pass


def _no(code: Deny, source: Source) -> Decision:
    return Decision(False, code, source)


def _scope_input(scope: Scope, action: InputTransfer):
    return next((r for r in scope.inputs if r.digest == action.digest), None)


def decide(scope: Scope, grant: LocalGrant, action, *, requester_key: str | None = None) -> Decision:
    """Allowed only when the signed scope AND the owner's grant both allow it; the first refusal is named."""
    sc, lg = Source.SIGNED_SCOPE, Source.LOCAL_GRANT
    if isinstance(action, Network):
        return _no(Deny.NETWORK, sc)
    if isinstance(action, CloudFallback):
        return _no(Deny.CLOUD, sc)
    if isinstance(action, Delegate):
        return _no(Deny.DELEGATE, sc)
    if isinstance(action, InputTransfer):
        rule = _scope_input(scope, action)
        if rule is None:
            return _no(Deny.INPUT, sc)
        if action.recipient != rule.recipient:
            return _no(Deny.RECIPIENT, sc)
        if action.media_type != rule.media_type:
            return _no(Deny.MEDIA_TYPE, sc)
        if type(action.size) is not int or not 0 <= action.size <= rule.max_bytes:
            return _no(Deny.TOO_BIG, sc)
        if action.purpose != rule.purpose:
            return _no(Deny.PURPOSE, sc)
        if action.digest not in grant.input_digests:
            return _no(Deny.INPUT, lg)
        if action.recipient not in grant.recipients:
            return _no(Deny.RECIPIENT, lg)
        if action.media_type not in grant.media_types:
            return _no(Deny.MEDIA_TYPE, lg)
        if action.size > grant.max_input_bytes:
            return _no(Deny.TOO_BIG, lg)
        if action.purpose not in grant.purposes:
            return _no(Deny.PURPOSE, lg)
        return ALLOW
    if isinstance(action, ToolRun):
        if action.tool_digest not in scope.approved_tools:
            return _no(Deny.TOOL, sc)
        if action.model_digest is not None and action.model_digest not in scope.allowed_models:
            return _no(Deny.MODEL, sc)
        for name in _LIMITS:
            value = getattr(action, name)
            if type(value) is not int or not 0 <= value <= getattr(scope, name):
                return _no(Deny.LIMIT, sc)
        if action.tool_digest not in grant.tools:
            return _no(Deny.TOOL, lg)
        if action.model_digest is not None and action.model_digest not in grant.models:
            return _no(Deny.MODEL, lg)
        if any(getattr(action, name) > getattr(grant, name) for name in _LIMITS):
            return _no(Deny.LIMIT, lg)
        return ALLOW
    if isinstance(action, View):
        if action.reader_key not in scope.readers:
            return _no(Deny.VIEWER, sc)
        return ALLOW if action.reader_key in grant.readers else _no(Deny.VIEWER, lg)
    if isinstance(action, Release):
        mode = {"preview": scope.preview, "final": scope.final}.get(action.kind)
        if mode != "send_to_requester" or action.recipient != requester_key:
            return _no(Deny.RELEASE, sc)
        permitted = {"preview": grant.may_send_preview, "final": grant.may_send_final}[action.kind]
        return ALLOW if permitted else _no(Deny.RELEASE, lg)
    if isinstance(action, Publish):
        if scope.publication != "signed_release_required" or not action.signed_release:
            return _no(Deny.PUBLICATION, sc)
        return ALLOW if grant.may_publish else _no(Deny.PUBLICATION, lg)
    raise TypeError("not an action this module knows")


@dataclass(frozen=True)
class Egress:
    candidate_bytes: bool
    content_hash: bool
    status: bool


def candidate_egress(scope: Scope, grant: LocalGrant, kind: str, recipient: str, requester_key: str) -> Egress:
    """What may leave this PC about a candidate. Bytes and a guessable content hash go only to the requester, only
    when that release is 'send_to_requester' in the signed scope AND the owner's grant allows it. A held candidate
    stays here; only its status may go, and only to the requester."""
    released = decide(scope, grant, Release(kind, recipient), requester_key=requester_key).allowed
    return Egress(released, released, recipient == requester_key)


def may_open_for_review(scope: Scope, reviewer_key: str, source_owner_consented: bool) -> bool:
    """A person on another machine may open the result only if the signed scope names them AND the source
    owner consented first."""
    return source_owner_consented is True and reviewer_key in scope.review_human_keys
