"""Gmail API calls that respect quota and report failure honestly.

Every read the Messages workspace makes goes through here:

* `execute(request)` runs one API call, retrying rate-limit (403
  rateLimitExceeded / userRateLimitExceeded, 429) and transient 5xx errors
  with exponential backoff and jitter, honouring Retry-After. Other errors
  (auth, not found, bad query) are not retried.
* `batch_get(...)` fetches many messages in one HTTP round trip per 50 ids
  (Gmail's batch endpoint), instead of one call per message. Items that come
  back rate-limited are retried once, individually, with backoff.
* `describe(exc)` turns an API error into {kind, message} in plain words:
  kind is 'rate_limited' | 'auth' | 'not_found' | 'bad_request' | 'other'.
  Callers put that in their response; a failure is never reported as an
  empty result.
"""
from __future__ import annotations

import json
import logging
import random
import time
from agent_friday.user_errors import ExceptionText

_log = logging.getLogger(__name__)

MAX_TRIES = 4
BASE_DELAY_S = 0.8
MAX_DELAY_S = 8.0
BATCH_SIZE = 50                  # Gmail allows 100; 50 keeps each batch under per-user quota bursts


class GmailError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


def _status_and_reason(exc):
    status = getattr(getattr(exc, 'resp', None), 'status', None)
    reason = ''
    try:
        body = json.loads(getattr(exc, 'content', b'') or b'{}')
        err = body.get('error') or {}
        errs = err.get('errors') or []
        reason = (errs[0].get('reason') if errs else '') or err.get('status') or ''
        msg = err.get('message') or ''
    except Exception:
        msg = ''
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        status = None
    return status, str(reason or ''), str(msg or exc)


def describe(exc) -> dict:
    """{kind, message} for any exception from a Gmail call."""
    if isinstance(exc, GmailError):
        return {'kind': exc.kind, 'message': exc.message}
    status, reason, msg = _status_and_reason(exc)
    r = reason.lower()
    if status == 429 or 'ratelimit' in r or 'quota' in r or (status == 403 and 'quota' in msg.lower()):
        return {'kind': 'rate_limited',
                'message': "Gmail is limiting how fast Friday can read this account right now "
                           "(Google's rate limit). Nothing is wrong with the account; try again in a minute."}
    if status in (401,) or 'invalid_grant' in msg or 'insufficient' in r or (status == 403 and 'permission' in msg.lower()):
        return {'kind': 'auth', 'message': ExceptionText("Gmail refused access for this account; it may need reconnecting. (%s)" % msg[:160])}
    if status == 404:
        return {'kind': 'not_found', 'message': 'Gmail could not find that message or thread.'}
    if status == 400:
        return {'kind': 'bad_request', 'message': ExceptionText('Gmail rejected the request: %s' % msg[:200])}
    return {'kind': 'other', 'message': ExceptionText('Gmail request failed: %s' % msg[:200])}


def _retryable(exc) -> bool:
    status, reason, msg = _status_and_reason(exc)
    if status is None:
        return False
    if status == 429 or status >= 500:
        return True
    return status == 403 and ('ratelimit' in reason.lower() or 'quota' in msg.lower())


def _delay(attempt: int, exc=None) -> float:
    try:
        ra = float((getattr(getattr(exc, 'resp', None), 'get', lambda *_: None)('retry-after')) or 0)
    except (TypeError, ValueError):
        ra = 0.0
    d = min(MAX_DELAY_S, BASE_DELAY_S * (2 ** attempt)) * (0.7 + random.random() * 0.6)
    return min(MAX_DELAY_S, max(d, ra))


def execute(request, *, tries: int = MAX_TRIES, sleep=time.sleep):
    """Run one request with backoff. Raises GmailError on final failure."""
    last = None
    for attempt in range(tries):
        try:
            return request.execute()
        except Exception as exc:          # googleapiclient.errors.HttpError and transport errors
            last = exc
            if attempt < tries - 1 and _retryable(exc):
                sleep(_delay(attempt, exc))
                continue
            d = describe(exc)
            raise GmailError(d['kind'], d['message']) from exc
    d = describe(last)
    raise GmailError(d['kind'], d['message'])


def batch_get(svc, ids, *, fmt: str = 'metadata', headers=None, kind: str = 'messages', sleep=time.sleep):
    """Fetch many messages (or threads) in batches. Returns (results_by_id,
    errors) where errors is a list of {id, kind, message}. Order of ids is
    preserved by the caller via results_by_id."""
    results, failed = {}, []
    ids = [i for i in ids if i]
    res = svc.users().messages() if kind == 'messages' else svc.users().threads()

    def make(i):
        kw = {'userId': 'me', 'id': i, 'format': fmt}
        if headers and fmt == 'metadata':
            kw['metadataHeaders'] = list(headers)
        return res.get(**kw)

    for start in range(0, len(ids), BATCH_SIZE):
        chunk = ids[start:start + BATCH_SIZE]
        retry = []

        def cb(request_id, response, exception, _retry=retry):
            if exception is None:
                results[request_id] = response
            elif _retryable(exception):
                _retry.append(request_id)
            else:
                d = describe(exception)
                failed.append({'id': request_id, **d})

        batch = svc.new_batch_http_request(callback=cb)
        for i in chunk:
            batch.add(make(i), request_id=i)
        try:
            execute(batch, sleep=sleep)
        except GmailError as e:
            # the whole batch failed (e.g. rate limit on the batch itself)
            for i in chunk:
                if i not in results:
                    retry.append(i)
            if e.kind != 'rate_limited':
                for i in retry:
                    failed.append({'id': i, 'kind': e.kind, 'message': e.message})
                continue
        for n, i in enumerate(dict.fromkeys(retry)):
            sleep(_delay(min(n, 3)))
            try:
                results[i] = execute(make(i), tries=2, sleep=sleep)
            except GmailError as e:
                failed.append({'id': i, 'kind': e.kind, 'message': e.message})
    return results, failed
