"""Gmail calls retry rate limits with backoff, fetch in batches, and turn
every failure into a plain {kind, message} instead of an empty result."""
import json

import pytest

from agent_friday.services import gmail_api, gmail_read


class HttpErr(Exception):
    def __init__(self, status, reason='', message='x'):
        super().__init__(message)
        self.resp = {'status': status}
        self.resp = type('R', (), {'status': status, 'get': lambda s, k, d=None: None})()
        self.content = json.dumps({'error': {'errors': [{'reason': reason}], 'message': message}}).encode()


class Req:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def execute(self):
        self.calls += 1
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return o


def test_rate_limit_is_retried_then_succeeds():
    r = Req([HttpErr(403, 'rateLimitExceeded', 'Quota exceeded'), HttpErr(429), {'ok': 1}])
    assert gmail_api.execute(r, sleep=lambda s: None) == {'ok': 1}
    assert r.calls == 3


def test_not_found_is_not_retried_and_is_described():
    r = Req([HttpErr(404, 'notFound')])
    with pytest.raises(gmail_api.GmailError) as ei:
        gmail_api.execute(r, sleep=lambda s: None)
    assert r.calls == 1 and ei.value.kind == 'not_found'


def test_rate_limit_that_persists_says_so_in_plain_words():
    r = Req([HttpErr(403, 'userRateLimitExceeded', 'Quota exceeded')] * 4)
    with pytest.raises(gmail_api.GmailError) as ei:
        gmail_api.execute(r, sleep=lambda s: None)
    assert ei.value.kind == 'rate_limited' and 'rate limit' in ei.value.message
    assert r.calls == gmail_api.MAX_TRIES


class FakeBatch:
    def __init__(self, cb, answers):
        self.cb, self.answers, self.items = cb, answers, []

    def add(self, req, request_id):
        self.items.append(request_id)

    def execute(self):
        for i in self.items:
            a = self.answers[i].pop(0) if isinstance(self.answers[i], list) else self.answers[i]
            if isinstance(a, Exception):
                self.cb(i, None, a)
            else:
                self.cb(i, a, None)


class FakeSvc:
    def __init__(self, answers):
        self.answers, self.batches, self.singles = answers, 0, 0
        svc = self

        class Msgs:
            def get(self, **kw):
                i = kw['id']
                class One:
                    def execute(_):
                        svc.singles += 1
                        a = svc.answers[i].pop(0) if isinstance(svc.answers[i], list) else svc.answers[i]
                        if isinstance(a, Exception):
                            raise a
                        return a
                return One()

        class Users:
            def messages(self):
                return Msgs()

            def threads(self):
                return Msgs()
        self._users = Users()

    def users(self):
        return self._users

    def new_batch_http_request(self, callback):
        self.batches += 1
        return FakeBatch(callback, self.answers)


def test_batch_get_uses_one_batch_per_50_and_retries_rate_limited_items():
    ids = ['m%d' % i for i in range(60)]
    answers = {i: {'id': i} for i in ids}
    answers['m3'] = [HttpErr(429), {'id': 'm3'}]          # rate-limited in the batch, fine on retry
    answers['m7'] = HttpErr(404)                          # really gone
    svc = FakeSvc(answers)
    got, failed = gmail_api.batch_get(svc, ids, sleep=lambda s: None)
    assert svc.batches == 2
    assert set(got) == set(ids) - {'m7'}
    assert [f['id'] for f in failed] == ['m7'] and failed[0]['kind'] == 'not_found'
    assert svc.singles == 1                                # only m3 went round again


def test_scrub_html_removes_active_content():
    html = ('<p onclick="x()">Hi<script>alert(1)</script><iframe src=x></iframe>'
            '<a href="javascript:evil()">l</a><form action=y><input></form><img src=cid:abc></p>')
    out = gmail_read.scrub_html(html)
    for bad in ('<script', 'onclick', '<iframe', 'javascript:', '<form', '<input'):
        assert bad not in out.lower()
    assert 'Hi' in out and 'cid:abc' in out
