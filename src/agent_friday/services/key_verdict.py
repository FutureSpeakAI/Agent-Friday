"""Does this API key actually work, and if not, whose problem is it?

The settings-side twin of `packaging/windows/lib/Heal.ps1::Test-AnthropicKey`,
which the installer has had since 5.6.2 and Settings did not.

Its docstring carries the whole argument, and it is worth repeating here
because the mistake it warns about is the one Settings was making:

    It deliberately uses the MESSAGES endpoint and the model healing will
    actually use, not a free metadata endpoint. A key with no credit
    authenticates perfectly well - it fails when you ask it to think, which
    is the case worth catching.

`/api/providers/<name>/test` probed Anthropic with `GET /v1/models` and
Google with `GET /v1beta/models` -- metadata endpoints a broke key passes
cheerfully -- and gated its one-token ping to openai-compatible providers.
So the two providers Friday ships with by default were the two she could not
tell the truth about.

Stephen, 2026-08-26: this "would turn 'Friday isn't working' into 'this key
was rejected', which is the difference between a user who fixes it and a
user who gives up." A key that never worked and a key that stopped working
look identical from the outside; only a real round-trip separates them.

FAILS OPEN, exactly as the PowerShell does. A verdict is 'rejected' or
'no_credit' only when the API said so plainly. No network, a 5xx, a timeout,
a corporate proxy -> 'unknown', and the caller warns and carries on. A
pre-flight check must never become the reason someone cannot use their own
key.

Response bodies are inspected in memory and NEVER logged: an auth failure
body can echo request headers on some proxies.
"""
from __future__ import annotations

OK = "ok"
REJECTED = "rejected"
NO_CREDIT = "no_credit"
UNKNOWN = "unknown"

# Wording that means "the key is fine, the account is not". Matched against
# the response body, because the status code alone cannot separate a
# rate-limit 429 (slow down) from a quota 429 (you are out).
_CREDIT_MARKERS = (
    "credit balance",
    "insufficient_quota",
    "insufficient quota",
    "exceeded your current quota",
    "quota exceeded",
    "resource_exhausted",
    "billing",
    "payment",
    "out of credit",
)


def verdict_for(status_code: int | None, body: str | None) -> str:
    """Map one HTTP outcome to ok | rejected | no_credit | unknown."""
    text = (body or "").lower()

    if status_code is None:
        return UNKNOWN

    if 200 <= status_code < 300:
        return OK

    # Out of money reads as several different status codes depending on the
    # provider (Anthropic 400, OpenAI/Google 429, some gateways 402), so the
    # body decides and the code does not. Checked BEFORE the auth branch:
    # a 403 that says "billing" is a billing problem wearing an auth code.
    if any(m in text for m in _CREDIT_MARKERS):
        return NO_CREDIT

    if status_code in (401, 403):
        return REJECTED

    # Everything else -- 429 without billing wording, 5xx, a teapot -- is not
    # something we can attribute. Say so rather than guess: telling someone
    # to go buy credit they already have is worse than saying "could not
    # tell".
    return UNKNOWN


def explain(verdict: str, provider_label: str) -> str:
    """The verdict as a sentence a person can act on."""
    label = provider_label or "This provider"
    if verdict == OK:
        return ("%s answered a real request with this key — it works."
                % label)
    if verdict == REJECTED:
        return ("%s rejected this key. It is wrong, revoked, or was copied "
                "incompletely — replace it with a fresh one." % label)
    if verdict == NO_CREDIT:
        # Deliberately does not say "invalid". The key authenticated
        # perfectly; sending someone to regenerate it wastes their time on
        # the one thing that is not broken.
        return ("The key is accepted, but the %s account behind it has no "
                "credit or has hit its billing quota. Top the account up — "
                "the key itself is fine." % label)
    return ("Could not reach %s to check this key. That is not a verdict on "
            "the key — it may be the network, a proxy, or %s being down. "
            "Nothing has been changed." % (label, label))


def probe_spec(prov: dict, model: str | None) -> dict | None:
    """How to spend one token against `prov`, or None if we cannot.

    Returns {method, url, json}. Headers are the caller's job -- they come
    from `auth_headers` and must not be rebuilt here. None means "no probe
    exists for this adapter", which the caller reports as `unknown` rather
    than inventing an endpoint.
    """
    ptype = (prov or {}).get("type") or ""
    base = ((prov or {}).get("base_url") or "").rstrip("/")
    if not base or not model:
        return None

    if ptype == "anthropic":
        return {
            "method": "POST",
            "url": base + "/v1/messages",
            "json": {"model": model, "max_tokens": 1,
                     "messages": [{"role": "user", "content": "hi"}]},
        }
    if ptype == "google":
        return {
            "method": "POST",
            "url": "%s/v1beta/models/%s:generateContent" % (base, model),
            "json": {"contents": [{"parts": [{"text": "hi"}]}],
                     "generationConfig": {"maxOutputTokens": 1}},
        }
    if ptype == "openai-compatible":
        return {
            "method": "POST",
            "url": base + "/chat/completions",
            "json": {"model": model, "max_tokens": 1,
                     "messages": [{"role": "user", "content": "hi"}]},
        }
    return None
