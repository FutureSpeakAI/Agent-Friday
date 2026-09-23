"""Reading Gmail threads for the Messages workspace (read-only, gmail.readonly).

* `get_thread(thread_id, account_id)` reads a whole thread from the account
  it belongs to. With no account it tries each connected account; "not
  found" in one account is not a failure while another may have it.
* Each message comes back with plain text, HTML (script, iframes, forms,
  event handlers and javascript: URLs removed; cid: inline images pointed
  at the attachment route), To/Cc/Reply-To, the RFC Message-ID and
  References (so a reply can thread), and its attachments.
* `get_attachment(...)` returns one attachment's bytes.

The HTML is also shown only inside a sandboxed, script-less iframe by the
UI; the scrub here is a second wall, not the only one.
"""
from __future__ import annotations

import base64
import re
from urllib.parse import quote

from agent_friday.services import gmail_api


def _b64(data: str) -> bytes:
    try:
        return base64.urlsafe_b64decode((data or '') + '===')
    except Exception:
        return b''


def _walk(payload, out):
    if not isinstance(payload, dict):
        return
    mime = (payload.get('mimeType') or '').lower()
    body = payload.get('body') or {}
    fname = payload.get('filename') or ''
    headers = {h['name'].lower(): h['value'] for h in payload.get('headers') or []}
    cid = (headers.get('content-id') or '').strip('<>')
    if body.get('attachmentId') or (fname and body.get('data')):
        out['attachments'].append({
            'attachment_id': body.get('attachmentId') or '',
            'filename': fname or ('inline-' + (cid or 'part')),
            'mime': mime or 'application/octet-stream',
            'size': int(body.get('size') or 0),
            'inline': bool(cid) and 'inline' in (headers.get('content-disposition') or 'inline').lower(),
            'cid': cid,
            'data': body.get('data') if not body.get('attachmentId') else None,
        })
    elif body.get('data') and mime == 'text/plain' and not out['text']:
        out['text'] = _b64(body['data']).decode('utf-8', 'ignore')
    elif body.get('data') and mime == 'text/html' and not out['html']:
        out['html'] = _b64(body['data']).decode('utf-8', 'ignore')
    for part in payload.get('parts') or []:
        _walk(part, out)


_DROP_BLOCKS = re.compile(r'<(script|style\s+[^>]*src|iframe|frame|frameset|object|embed|applet|form|base|meta\s+http-equiv)[^>]*>.*?</\1\s*>', re.I | re.S)
_DROP_TAGS = re.compile(r'<\s*/?\s*(script|iframe|frame|frameset|object|embed|applet|form|input|button|base|link|meta)\b[^>]*>', re.I)
_ON_ATTR = re.compile(r'\son[a-z]+\s*=\s*("[^"]*"|\'[^\']*\'|[^\s>]+)', re.I)
_JS_URL = re.compile(r'(href|src|action|formaction|xlink:href)\s*=\s*(["\']?)\s*(javascript|vbscript|data:text/html)[^"\'>\s]*\2', re.I)


def scrub_html(html: str) -> str:
    html = _DROP_BLOCKS.sub('', html or '')
    html = _DROP_TAGS.sub('', html)
    html = _ON_ATTR.sub('', html)
    html = _JS_URL.sub(r'\1=""', html)
    return html


def _attachment_url(account_id, message_id, att):
    return ('/api/messages/attachment?account=%s&message=%s&id=%s&name=%s&mime=%s'
            % (quote(account_id or ''), quote(message_id), quote(att['attachment_id'] or ''),
               quote(att['filename']), quote(att['mime'])))


def _message(msg, account_id):
    payload = msg.get('payload') or {}
    headers = {h['name'].lower(): h['value'] for h in payload.get('headers') or []}
    parts = {'text': '', 'html': '', 'attachments': []}
    _walk(payload, parts)
    html = parts['html']
    atts = []
    for a in parts['attachments']:
        if a['data']:                       # small inline part carried in the message itself
            if a['cid'] and html:
                html = html.replace('cid:' + a['cid'], 'data:%s;base64,%s' % (a['mime'], base64.b64encode(_b64(a['data'])).decode()))
            continue
        url = _attachment_url(account_id, msg.get('id'), a)
        if a['cid'] and html:
            html = html.replace('cid:' + a['cid'], url)
        if not (a['inline'] and a['cid'] and a['mime'].startswith('image/')):
            atts.append({k: a[k] for k in ('attachment_id', 'filename', 'mime', 'size')} | {'url': url})
    ts = msg.get('internalDate')
    return {
        'id': msg.get('id'),
        'thread_id': msg.get('threadId'),
        'sender': headers.get('from', 'unknown'),
        'to': headers.get('to', ''),
        'cc': headers.get('cc', ''),
        'reply_to': headers.get('reply-to', ''),
        'subject': headers.get('subject', '(no subject)'),
        'date': headers.get('date', ''),
        'internal_ms': int(ts) if ts else None,
        'message_id_header': headers.get('message-id', ''),
        'references': headers.get('references', ''),
        'labels': msg.get('labelIds') or [],
        'snippet': msg.get('snippet') or '',
        'body': parts['text'] or re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html)).strip() or msg.get('snippet') or '',
        'html': scrub_html(html) if html else '',
        'attachments': atts,
    }


def _service(creds):
    from googleapiclient.discovery import build
    return build('gmail', 'v1', credentials=creds, cache_discovery=False)


def get_thread(thread_id: str, account_id: str | None = None) -> dict:
    """{status:'ok', account_id, messages:[...]} or {status:'error', kind, error}."""
    from agent_friday.services import google_accounts as ga
    accounts = ga._accounts_with('gmail')
    if account_id:
        accounts = [a for a in accounts if a['id'] == account_id] or accounts
    if not accounts:
        return {'status': 'error', 'kind': 'auth', 'error': 'No Gmail account is connected.'}
    last = None
    for rec in accounts:
        creds = ga.credentials_for(rec['id'])
        if not creds:
            last = {'kind': 'auth', 'error': '%s needs reconnecting.' % (rec.get('label') or 'This account')}
            continue
        try:
            t = gmail_api.execute(_service(creds).users().threads().get(userId='me', id=thread_id, format='full'))
        except gmail_api.GmailError as e:
            last = {'kind': e.kind, 'error': e.message}
            if e.kind == 'not_found':
                continue
            if account_id:
                break
            continue
        return {'status': 'ok', 'account_id': rec['id'], 'account_label': rec.get('label'),
                'thread_id': thread_id, 'messages': [_message(m, rec['id']) for m in t.get('messages') or []]}
    last = last or {'kind': 'not_found', 'error': 'Gmail could not find that thread.'}
    return {'status': 'error', **last}


def get_attachment(account_id: str, message_id: str, attachment_id: str) -> bytes:
    from agent_friday.services import google_accounts as ga
    creds = ga.credentials_for(account_id)
    if not creds:
        raise gmail_api.GmailError('auth', 'That account needs reconnecting.')
    r = gmail_api.execute(_service(creds).users().messages().attachments().get(
        userId='me', messageId=message_id, id=attachment_id))
    return _b64(r.get('data') or '')
