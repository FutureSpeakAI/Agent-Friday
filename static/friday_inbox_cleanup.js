/* Messages > Clean up: the inbox clean-up board (first-run spec, B3).
 *
 * One list of mailing lists the person hasn't opened lately. Every sender has
 * its own tick and nothing is ticked at first; "Tick all" is a button. The
 * ticked ones get the standard one-click unsubscribe request, sent by the
 * server only after re-checking each sender's own evidence. Senders that need
 * the person (their own page, an email) are listed with the link or address,
 * which Friday never follows itself. Probable spam is never unsubscribed:
 * that would tell the sender the address is live.
 *
 * The found ledger shows two numbers that are never added together: senders
 * confirmed to have stopped, and senders asked but not yet checked.
 *
 * Defines window.FridayInboxCleanup. Loaded before friday_mail.js.
 */
(function () {
  'use strict';
  if (typeof window === 'undefined' || window.FridayInboxCleanup) return;
  const h = React.createElement;
  const { useState, useEffect } = React;
  const W = window;
  const fridayName = () => (typeof W.fridayName === 'function' ? W.fridayName() : 'Friday');
  const api = (url, opts) => {
    const tok = W.__FRIDAY_API_TOKEN || '';
    const headers = Object.assign({}, opts && opts.headers);
    if (tok) headers['X-Friday-Token'] = tok;
    return fetch(url, Object.assign({}, opts, { headers }));
  };
  const post = (url, body) => api(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    .then(r => r.json().then(j => ({ ok: r.ok, status: r.status, j })).catch(() => ({ ok: false, status: r.status, j: {} })));

  const CSS = `
    .ic { display:flex; flex-direction:column; gap:12px; padding:12px 14px; overflow:auto; min-height:0; font-size:12.5px; color:#dbe6f5; }
    .ic h2 { margin:0; font-size:15px; color:#fff; }
    .ic h3 { margin:6px 0 2px; font-size:12px; letter-spacing:.06em; text-transform:uppercase; color:#8fa6c4; }
    .ic p { margin:0; line-height:1.6; color:#b8c7dc; }
    .ic .ic-row { display:flex; gap:10px; align-items:flex-start; padding:8px 10px; border:1px solid rgba(255,255,255,0.08); border-radius:10px; }
    .ic .ic-row.on { border-color:rgba(0,212,255,0.45); background:rgba(0,212,255,0.05); }
    .ic .ic-name { font-weight:700; color:#e6f4ff; }
    .ic .ic-dim { color:#8fa6c4; font-size:11.5px; }
    .ic .ic-bar { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
    .ic .ic-ledger { display:flex; gap:12px; flex-wrap:wrap; }
    .ic .ic-ledger > div { flex:1 1 200px; border:1px solid rgba(255,255,255,0.08); border-radius:10px; padding:8px 10px; }
    .ic .ic-num { font-size:20px; font-weight:700; color:#fff; }
    .ic .ic-ok { color:#7fe3b0; }
    .ic .ic-bad { color:#ff9a9a; }
    .ic input[type=text] { flex:1; min-width:220px; background:rgba(255,255,255,0.04); color:#fff; border:1px solid rgba(255,255,255,0.15); border-radius:8px; padding:7px 9px; }
  `;
  function Style() { return h('style', null, CSS); }

  function Row({ r, ticked, onTick }) {
    const id = 'ic-' + r.id;
    return h('div', { className: 'ic-row' + (ticked ? ' on' : '') },
      onTick && h('input', { id, type: 'checkbox', checked: !!ticked, onChange: e => onTick(r.id, e.target.checked),
        'aria-label': 'Unsubscribe from ' + r.name }),
      h('label', { htmlFor: onTick ? id : undefined, style: { flex: 1 } },
        h('div', null, h('span', { className: 'ic-name' }, r.name), ' ', h('span', { className: 'ic-dim' }, r.domain)),
        h('div', { className: 'ic-dim' }, r.streak_words + ' About ' + r.per_month + ' a month.'),
        r.why && h('div', { className: 'ic-dim' }, r.why),
        r.url && h('div', { className: 'ic-dim' }, 'Their page: ', h('a', { href: r.url, target: '_blank', rel: 'noopener noreferrer' }, r.host || r.url)),
        r.mailto && h('div', { className: 'ic-dim' }, 'Email to unsubscribe: ' + r.mailto)));
  }

  function Ledger({ found }) {
    if (!found) return null;
    const c = found.confirmed || [], p = found.possible || [], s = found.still_sending || [];
    if (!c.length && !p.length && !s.length) return null;
    return h('section', { 'aria-label': 'What the clean-up has done' },
      h('h3', null, 'So far'),
      h('div', { className: 'ic-ledger' },
        h('div', null, h('div', { className: 'ic-num' }, c.length), h('div', null, 'Confirmed: stopped sending'),
          h('div', { className: 'ic-dim' }, 'About ' + found.confirmed_per_month + ' fewer emails a month, checked 14 days after.')),
        h('div', null, h('div', { className: 'ic-num' }, p.length), h('div', null, 'Asked, not checked yet'),
          h('div', { className: 'ic-dim' }, 'Estimate: about ' + found.possible_per_month + ' a month, once they stop. I check after 14 days.'))),
      s.length > 0 && h('p', { className: 'ic-bad' }, 'Still sending: ' + s.length + ' (' + s.map(x => x.name).join(', ') + '). You can mark them as spam, or try their own page.'));
  }

  function FridayInboxCleanup() {
    const [b, setB] = useState(null);
    const [ticks, setTicks] = useState({});
    const [busy, setBusy] = useState(false);
    const [msg, setMsg] = useState('');
    const [result, setResult] = useState(null);
    const [mbox, setMbox] = useState('');
    const load = () => api('/api/inbox-cleanup').then(r => r.json()).then(d => setB(d)).catch(() => setMsg('Could not load the clean-up.'));
    useEffect(() => { load(); }, []);
    const state = b && b.job && b.job.state;
    useEffect(() => {
      if (state !== 'scanning') return undefined;
      const iv = setInterval(load, 3000);
      return () => clearInterval(iv);
    }, [state]);
    if (!b) return h('div', { className: 'ic' }, h(Style), msg || 'Loading…');
    const start = source => {
      setBusy(true); setMsg('');
      post('/api/inbox-cleanup/start', { source, requested_by: 'ui:inbox-cleanup' }).then(({ ok, j }) => {
        setBusy(false);
        if (!ok) { setMsg(j.message || 'Could not start.'); return; }
        setB(j); setTicks({}); setResult(null);
      });
    };
    const rows = b.rows || { one_click: [], needs_you: [], spam: [] };
    const ticked = rows.one_click.filter(r => ticks[r.id]).map(r => r.id);
    const tick = (id, on) => setTicks(t => Object.assign({}, t, { [id]: on }));
    const tickAll = () => { const t = {}; rows.one_click.forEach(r => { t[r.id] = true; }); setTicks(t); };
    const go = () => {
      if (!ticked.length) return;
      setBusy(true); setMsg('');
      post('/api/inbox-cleanup/unsubscribe', { senders: ticked, requested_by: 'ui:inbox-cleanup' }).then(({ ok, j }) => {
        setBusy(false);
        if (!ok) { setMsg(j.message || 'Could not unsubscribe.'); return; }
        setResult(j); setTicks({});
        if (j.board) setB(j.board);
      });
    };
    const job = b.job || {};
    return h('div', { className: 'ic', role: 'region', 'aria-label': 'Inbox clean-up' }, h(Style),
      h('h2', null, 'Clean up your inbox'),
      h('p', null, 'Mailing lists you haven\'t opened lately. I read only the headers of the last 12 months, on this PC. Tick the ones to leave; nothing is unsubscribed until you do.'),
      msg && h('p', { role: 'alert', className: 'ic-bad' }, msg),
      (state === 'idle' || state === 'error' || !state) && h('section', { 'aria-label': 'Where to read from' },
        job.error && h('p', { className: 'ic-bad' }, job.error),
        (b.accounts || []).length
          ? h('div', { className: 'ic-bar' }, (b.accounts || []).map(a => h('button', { key: a.id, className: 'btn fm-btn', disabled: busy, onClick: () => start({ kind: 'gmail', account_id: a.id }) }, 'Read ' + (a.email || 'this Gmail account'))))
          : h('p', null, 'No email I can read is connected. Connect Gmail in Settings > Connections, or use a Google Takeout export:'),
        h('div', { className: 'ic-bar', style: { marginTop: 8 } },
          h('label', { htmlFor: 'ic-mbox', className: 'ic-dim' }, 'Takeout .mbox file:'),
          h('input', { id: 'ic-mbox', type: 'text', value: mbox, placeholder: 'C:\\...\\All mail Including Spam and Trash.mbox', onChange: e => setMbox(e.target.value) }),
          h('button', { className: 'btn fm-btn', disabled: busy || !mbox.trim(), onClick: () => start({ kind: 'mbox', path: mbox.trim() }) }, 'Read the file'))),
      state === 'scanning' && h('p', { role: 'status' }, 'Reading ' + ((job.source || {}).label || 'your mail') + ': ' + (job.scanned || 0) + ' messages so far. You can leave this; it carries on, and picks up where it stopped if ' + fridayName() + ' closes.'),
      state === 'ready' && h(React.Fragment, null,
        h('section', { 'aria-label': 'One click' },
          h('h3', null, 'One click (' + rows.one_click.length + ')'),
          rows.one_click.length ? h(React.Fragment, null,
            h('div', { className: 'ic-bar' },
              h('button', { className: 'btn fm-btn', onClick: tickAll, disabled: busy }, 'Tick all'),
              h('button', { className: 'btn fm-btn', onClick: () => setTicks({}), disabled: busy || !ticked.length }, 'Clear'),
              h('button', { className: 'btn fm-btn', onClick: go, disabled: busy || !ticked.length },
                ticked.length ? 'Unsubscribe from ' + ticked.length + ' ticked' : 'Tick the ones to leave'),
              h('span', { className: 'ic-dim' }, 'Each gets the standard one-click request. That tells them this address is read.')),
            rows.one_click.map(r => h(Row, { key: r.id, r, ticked: !!ticks[r.id], onTick: tick })))
            : h('p', { className: 'ic-dim' }, 'Nothing here can be left with one click.')),
        rows.needs_you.length > 0 && h('section', { 'aria-label': 'These need you' },
          h('h3', null, 'These need you (' + rows.needs_you.length + ')'),
          h('p', { className: 'ic-dim' }, 'I won\'t contact these for you. The link or address is here if you want to.'),
          rows.needs_you.map(r => h(Row, { key: r.id, r }))),
        rows.spam.length > 0 && h('section', { 'aria-label': 'Probably spam' },
          h('h3', null, 'Probably spam (' + rows.spam.length + ')'),
          h('p', { className: 'ic-dim' }, 'Mark these as spam in Gmail instead. Unsubscribing would tell the sender your address is live.'),
          rows.spam.map(r => h(Row, { key: r.id, r }))),
        h('div', { className: 'ic-bar' }, h('button', { className: 'btn fm-btn', disabled: busy, onClick: () => setB(Object.assign({}, b, { job: { state: 'idle' } })) }, 'Read again…'))),
      result && h('section', { 'aria-label': 'What just happened', role: 'status' },
        result.done && result.done.length > 0 && h('p', { className: 'ic-ok' }, 'Unsubscribed: ' + result.done.map(x => x.name).join(', ') + '. Each has a receipt.'),
        result.not_done && result.not_done.map(x => h('p', { key: x.id, className: 'ic-bad' }, (x.name || x.id) + ': ' + x.why))),
      h(Ledger, { found: b.found }),
      h('p', { className: 'ic-dim' }, b.archive_hint || ''));
  }

  W.FridayInboxCleanup = FridayInboxCleanup;
})();
