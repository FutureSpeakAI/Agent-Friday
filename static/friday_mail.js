/* Messages: a Gmail-grade inbox over Friday's triage.
 *
 * What it does, and what it deliberately does not:
 * - Reads both connected accounts (All / each account), with Gmail's own
 *   search (Enter sends the query to Gmail's q=). A failure is shown as a
 *   failure, never as "0" or "No messages"; a partial result says which
 *   account is missing.
 * - Threads open from the account they belong to, as formatted mail in a
 *   sandboxed frame with no scripts and remote images off until asked for,
 *   with attachments to preview or download.
 * - Archive, snooze, flag, read/unread and lane moves are Friday-local (Gmail
 *   is not changed) and every one can be undone (toast, or Z).
 * - Reply, reply all, forward and compose never send. They file an approval
 *   card; the message goes out only after it is approved, and only from an
 *   account that granted sending.
 * - No delete. Nothing here can remove mail.
 *
 * Loaded after studio_files3d.js; defines window.FridayMailPanel.
 */
(function () {
  'use strict';
  if (window.FridayMailPanel) return;
  const h = React.createElement;
  const { useState, useEffect, useRef, useCallback } = React;

  const api = (url, opts) => {
    const tok = window.__FRIDAY_API_TOKEN || '';
    const headers = Object.assign({}, opts && opts.headers);
    if (tok) headers['X-Friday-Token'] = tok;
    return fetch(url, Object.assign({}, opts, { headers }));
  };
  const post = (url, body) => api(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    .then(r => r.json().then(j => ({ ok: r.ok, j })));
  const LANES = {
    all: ['All', '📥'], career: ['Career', '💼'], finance: ['Finance', '💰'], futurespeak: ['Projects', '🚀'],
    family: ['Family', '👪'], subscriptions: ['Subscriptions', '📰'], noise: ['Noise', '🔇']
  };
  const laneLabel = id => (LANES[id] || [id])[0];
  const addrOf = s => { const m = /<([^>]+)>/.exec(s || ''); return (m ? m[1] : (s || '')).trim().toLowerCase(); };
  const nameOf = s => { const m = /^\s*"?([^"<]*?)"?\s*</.exec(s || ''); return (m && m[1].trim()) || addrOf(s); };
  const splitAddrs = s => String(s || '').split(/[,;]/).map(x => x.trim()).filter(Boolean);
  const fmtWhen = ts => {
    const d = new Date(ts); if (isNaN(d)) return String(ts || '');
    const now = new Date();
    return d.toDateString() === now.toDateString() ? d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
      : d.getFullYear() === now.getFullYear() ? d.toLocaleDateString([], { month: 'short', day: 'numeric' })
        : d.toLocaleDateString();
  };
  const esc = s => String(s || '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  // ── styles (scoped by .fm-) ─────────────────────────────────────────────
  if (!document.getElementById('fm-style')) {
    const st = document.createElement('style');
    st.id = 'fm-style';
    st.textContent = `
      .fm { display:flex; flex-direction:column; gap:8px; font-family: Inter, sans-serif; color:#dbe6f5; }
      .fm-bar { display:flex; gap:6px; align-items:center; flex-wrap:wrap; }
      .fm-chip { display:inline-flex; gap:6px; align-items:center; padding:5px 10px; border-radius:999px; font-size:11px; cursor:pointer;
        border:1px solid rgba(255,255,255,0.12); background:rgba(255,255,255,0.03); color:#b8c7dc; user-select:none; }
      .fm-chip:hover { border-color:rgba(0,212,255,0.5); color:#e6f4ff; }
      .fm-chip.on { border-color:#00d4ff; color:#e6f9ff; background:rgba(0,212,255,0.12); }
      .fm-dot { width:8px; height:8px; border-radius:50%; display:inline-block; }
      .fm-count { font-family:'JetBrains Mono',monospace; font-size:10px; opacity:.85; }
      .fm-search { flex:1 1 260px; min-width:180px; background:#0b1220; color:#e6f0ff; border:1px solid #24406a; border-radius:8px; padding:8px 10px; font-size:12px; }
      .fm-search:focus { outline:none; border-color:#00d4ff; box-shadow:0 0 0 2px rgba(0,212,255,0.15); }
      .fm-banner { padding:8px 12px; border-radius:8px; font-size:12px; display:flex; gap:8px; align-items:center; }
      .fm-banner.err { background:rgba(239,68,68,0.10); border:1px solid rgba(239,68,68,0.45); color:#ffb4b4; }
      .fm-banner.warn { background:rgba(245,158,11,0.10); border:1px solid rgba(245,158,11,0.45); color:#ffd699; }
      .fm-banner.info { background:rgba(0,212,255,0.07); border:1px solid rgba(0,212,255,0.3); color:#aee9ff; }
      .fm-main { display:flex; gap:10px; min-height:0; }
      .fm-list { flex:1 1 42%; min-width:300px; max-height:calc(100vh - 330px); overflow:auto; border:1px solid rgba(0,212,255,0.12); border-radius:10px; }
      .fm-row { display:grid; grid-template-columns: 22px 10px minmax(90px,170px) 1fr auto; gap:8px; align-items:center; padding:8px 10px;
        border-bottom:1px solid rgba(255,255,255,0.05); cursor:pointer; font-size:12px; }
      .fm-row:hover { background:rgba(0,212,255,0.05); }
      .fm-row.focus { box-shadow: inset 3px 0 0 #00d4ff; background:rgba(0,212,255,0.07); }
      .fm-row.open { background:rgba(123,97,255,0.12); }
      .fm-row.unread .fm-from, .fm-row.unread .fm-subj { font-weight:700; color:#fff; }
      .fm-from { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:#c6d4e8; }
      .fm-line { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
      .fm-subj { color:#dbe6f5; } .fm-snip { color:#7f93ad; }
      .fm-when { color:#7f93ad; font-size:11px; white-space:nowrap; font-family:'JetBrains Mono',monospace; }
      .fm-badge { font-size:9px; padding:1px 6px; border-radius:999px; border:1px solid; margin-right:5px; }
      .fm-thread { flex:1 1 58%; min-width:360px; max-height:calc(100vh - 330px); overflow:auto; border:1px solid rgba(0,212,255,0.12); border-radius:10px; padding:12px; }
      .fm-msg { border:1px solid rgba(255,255,255,0.07); border-radius:8px; padding:10px; margin-bottom:10px; background:rgba(255,255,255,0.02); }
      .fm-hdr { font-size:11px; color:#8fa6c4; line-height:1.6; }
      .fm-hdr b { color:#e6f0ff; }
      .fm-body { width:100%; border:0; background:#fff; border-radius:6px; margin-top:8px; min-height:60px; }
      .fm-text { white-space:pre-wrap; font-size:12.5px; line-height:1.55; color:#dbe6f5; margin-top:8px; }
      .fm-atts { display:flex; gap:6px; flex-wrap:wrap; margin-top:8px; }
      .fm-att { display:inline-flex; gap:6px; align-items:center; font-size:11px; padding:5px 8px; border-radius:6px; border:1px solid rgba(255,255,255,0.12); background:rgba(0,0,0,0.25); color:#cfe3ff; text-decoration:none; }
      .fm-att:hover { border-color:#00d4ff; }
      .fm-btn { font-size:11px; padding:5px 10px; min-height:28px; }
      .fm-bulk { display:flex; gap:6px; align-items:center; padding:6px 10px; border-radius:8px; background:rgba(123,97,255,0.12); border:1px solid rgba(123,97,255,0.4); font-size:12px; flex-wrap:wrap; }
      .fm-compose { position:fixed; right:24px; bottom:78px; width:min(620px, 92vw); max-height:78vh; z-index:66; display:flex; flex-direction:column;
        background:rgba(8,12,22,0.97); border:1px solid rgba(0,212,255,0.35); border-radius:12px; box-shadow:0 20px 60px rgba(0,0,0,0.6), 0 0 30px rgba(0,212,255,0.12); }
      .fm-compose-h { display:flex; justify-content:space-between; align-items:center; padding:10px 12px; border-bottom:1px solid rgba(255,255,255,0.08); font-family:Orbitron,Inter,sans-serif; font-size:11px; letter-spacing:.1em; color:#00d4ff; }
      .fm-field { display:flex; gap:8px; align-items:center; padding:4px 12px; border-bottom:1px solid rgba(255,255,255,0.05); font-size:12px; position:relative; }
      .fm-field label { color:#7f93ad; width:52px; }
      .fm-field input, .fm-field select { flex:1; background:transparent; border:0; color:#e6f0ff; padding:6px 0; font-size:12.5px; outline:none; }
      .fm-field select option { background:#0b1220; }
      .fm-editor { min-height:180px; max-height:40vh; overflow:auto; padding:10px 12px; font-size:13px; line-height:1.55; outline:none; color:#e6f0ff; }
      .fm-editor blockquote { border-left:2px solid #3d5a80; margin:6px 0; padding-left:10px; color:#9fb0c8; }
      .fm-tools { display:flex; gap:4px; padding:6px 12px; border-top:1px solid rgba(255,255,255,0.06); align-items:center; flex-wrap:wrap; }
      .fm-tool { background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.12); color:#cfe3ff; border-radius:5px; min-width:28px; height:26px; cursor:pointer; font-size:12px; }
      .fm-tool:hover { border-color:#00d4ff; }
      .fm-sugg { position:absolute; left:64px; top:100%; z-index:5; background:#0b1220; border:1px solid #24406a; border-radius:6px; min-width:260px; max-height:200px; overflow:auto; }
      .fm-sugg div { padding:6px 10px; font-size:12px; cursor:pointer; } .fm-sugg div:hover, .fm-sugg div.on { background:rgba(0,212,255,0.12); }
      .fm-toast { position:fixed; left:50%; bottom:84px; transform:translateX(-50%); z-index:70; background:rgba(8,12,22,0.96); border:1px solid #2e5a8f;
        color:#e6f0ff; padding:8px 12px; border-radius:8px; font-size:12px; display:flex; gap:10px; align-items:center; box-shadow:0 8px 30px rgba(0,0,0,.5); }
      .fm-help { position:fixed; inset:0; z-index:80; background:rgba(0,0,0,0.55); display:flex; align-items:center; justify-content:center; }
      .fm-help > div { background:#0a1020; border:1px solid rgba(0,212,255,0.4); border-radius:12px; padding:18px 22px; font-size:12px; color:#cfe3ff; columns:2; column-gap:30px; max-width:640px; }
      .fm-help kbd { font-family:'JetBrains Mono',monospace; background:#16213a; border:1px solid #2b4470; border-radius:4px; padding:1px 6px; margin-right:6px; color:#fff; }
      .fm-preview { position:fixed; inset:4vh 4vw; z-index:82; background:#05080f; border:1px solid rgba(0,212,255,0.4); border-radius:12px; display:flex; flex-direction:column; }
      .fm-preview iframe, .fm-preview img { flex:1; border:0; object-fit:contain; max-width:100%; min-height:0; background:#111; }
    `;
    document.head.appendChild(st);
  }

  // ── a message body in a sandboxed frame ─────────────────────────────────
  function MailBody({ html, text, showImages }) {
    const ref = useRef(null);
    const [height, setHeight] = useState(120);
    if (!html) return h('div', { className: 'fm-text' }, text || '');
    const origin = window.location.origin;
    const csp = "default-src 'none'; style-src 'unsafe-inline'; font-src data:; img-src data: " + origin + (showImages ? ' https: http:' : '');
    const doc = '<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="' + csp + '">' +
      '<base target="_blank"><style>body{margin:12px;font:13px/1.5 -apple-system,Segoe UI,Arial,sans-serif;color:#1b1f24;word-wrap:break-word}img{max-width:100%;height:auto}</style></head><body>' + html + '</body></html>';
    const fit = () => {
      try { const d = ref.current.contentDocument; setHeight(Math.min(4000, Math.max(80, d.documentElement.scrollHeight + 4))); } catch (_) { /* cross-origin: keep default */ }
    };
    // allow-same-origin WITHOUT allow-scripts: nothing in the mail can run,
    // and Friday can still measure the frame to size it.
    return h('iframe', { ref, className: 'fm-body', title: 'Message', sandbox: 'allow-same-origin allow-popups allow-popups-to-escape-sandbox', srcDoc: doc, onLoad: fit, style: { height } });
  }

  // ── compose / reply / forward ───────────────────────────────────────────
  function AddrInput({ value, onChange, placeholder, autoFocus }) {
    const [sugg, setSugg] = useState([]);
    const [hi, setHi] = useState(0);
    const tRef = useRef(0);
    const lastPart = v => { const p = v.split(/[,;]/); return p[p.length - 1].trim(); };
    const onInput = v => {
      onChange(v);
      clearTimeout(tRef.current);
      const q = lastPart(v);
      if (q.length < 2) { setSugg([]); return; }
      tRef.current = setTimeout(() => api('/api/mail/contacts?q=' + encodeURIComponent(q)).then(r => r.json()).then(d => { setSugg((d.contacts || []).slice(0, 8)); setHi(0); }).catch(() => setSugg([])), 180);
    };
    const pick = c => {
      const parts = value.split(/[,;]/).map(s => s.trim()).filter(Boolean);
      parts[Math.max(0, parts.length - 1)] = c.email;
      onChange(parts.join(', ') + ', '); setSugg([]);
    };
    return h(React.Fragment, null,
      h('input', { value, placeholder, autoFocus, onChange: e => onInput(e.target.value),
        onKeyDown: e => {
          if (!sugg.length) return;
          if (e.key === 'ArrowDown') { setHi((hi + 1) % sugg.length); e.preventDefault(); }
          else if (e.key === 'ArrowUp') { setHi((hi + sugg.length - 1) % sugg.length); e.preventDefault(); }
          else if (e.key === 'Enter' || e.key === 'Tab') { pick(sugg[hi]); e.preventDefault(); }
          else if (e.key === 'Escape') setSugg([]);
        }, onBlur: () => setTimeout(() => setSugg([]), 150) }),
      sugg.length > 0 && h('div', { className: 'fm-sugg' }, sugg.map((c, i) => h('div', { key: c.email, className: i === hi ? 'on' : '', onMouseDown: () => pick(c) },
        (c.name ? c.name + ' · ' : '') + c.email, h('span', { style: { color: '#6f86a6', marginLeft: 6, fontSize: 10 } }, c.source)))));
  }

  function Composer({ init, accounts, canSend, onClose, say }) {
    const [from, setFrom] = useState(init.account_id || (canSend[0] && canSend[0].id) || '');
    const [to, setTo] = useState(init.to || '');
    const [cc, setCc] = useState(init.cc || '');
    const [bcc, setBcc] = useState('');
    const [showCc, setShowCc] = useState(!!init.cc);
    const [subject, setSubject] = useState(init.subject || '');
    const [atts, setAtts] = useState([]);
    const [fwd, setFwd] = useState((init.forward || []).map(a => Object.assign({ keep: true }, a)));
    const [busy, setBusy] = useState(false);
    const [state, setState] = useState(null);     // {approval_id, status, message}
    const ed = useRef(null), fileRef = useRef(null);
    useEffect(() => { if (ed.current && init.html != null) ed.current.innerHTML = init.html; if (ed.current && !init.to) {} }, []);
    const cmd = (c, v) => { document.execCommand(c, false, v); ed.current && ed.current.focus(); };
    const upload = files => {
      Array.from(files || []).forEach(f => {
        const fd = new FormData(); fd.append('file', f);
        api('/api/mail/attachment', { method: 'POST', body: fd }).then(r => r.json()).then(d => {
          if (d.status === 'ok') setAtts(a => a.concat([d])); else say(d.message || 'Could not attach ' + f.name);
        }).catch(() => say('Could not attach ' + f.name));
      });
    };
    const sendable = canSend.find(a => a.id === from);
    const request = () => {
      const html = ed.current ? ed.current.innerHTML : '';
      const text = ed.current ? ed.current.innerText : '';
      const bad = splitAddrs(to).concat(splitAddrs(cc), splitAddrs(bcc)).filter(a => !/^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$/.test(addrOf(a)));
      if (!splitAddrs(to).length) { say('Add at least one recipient.'); return; }
      if (bad.length) { say('Not an email address: ' + bad.join(', ')); return; }
      setBusy(true);
      post('/api/mail/request', {
        to: splitAddrs(to).map(addrOf), cc: splitAddrs(cc).map(addrOf), bcc: splitAddrs(bcc).map(addrOf), subject,
        body: text, html, account_id: from, requested_by: 'ui:messages',
        thread_id: init.thread_id || null, in_reply_to: init.in_reply_to || null, references: init.references || null,
        attachments: atts, forward_attachments: fwd.filter(a => a.keep)
      }).then(({ ok, j }) => {
        setBusy(false);
        if (!ok || !j.approval_id) { setState({ status: 'refused', message: j.message || 'Friday could not queue this message.' }); return; }
        setState({ approval_id: j.approval_id, status: j.approval_status, message: 'Waiting for your approval. Nothing has been sent yet.' });
      }).catch(() => { setBusy(false); setState({ status: 'refused', message: 'Could not reach Friday.' }); });
    };
    // follow the card: approved → sent (or refused), denied → not sent
    useEffect(() => {
      if (!state || !state.approval_id || /sent|denied|failed/.test(state.status)) return;
      const iv = setInterval(() => {
        api('/api/approvals/' + state.approval_id).then(r => r.json()).then(d => {
          const a = d.approval || d;
          if (!a) return;
          if (a.status === 'approved' && a.consumed) {
            const det = a.used_detail || {};
            setState(s => Object.assign({}, s, { status: det.message_id ? 'sent' : 'failed', message: det.message_id ? 'Sent.' : 'Approved, but Gmail did not accept it. See Settings › Outbox.' }));
          } else if (a.status === 'denied' || a.status === 'expired' || a.status === 'blocked') {
            setState(s => Object.assign({}, s, { status: 'denied', message: 'Not approved. Nothing was sent.' }));
          }
        }).catch(() => {});
      }, 2500);
      return () => clearInterval(iv);
    }, [state && state.approval_id, state && state.status]);
    const title = { new: 'NEW MESSAGE', reply: 'REPLY', replyAll: 'REPLY ALL', forward: 'FORWARD' }[init.mode] || 'MESSAGE';
    return h('div', { className: 'fm-compose', role: 'dialog', 'aria-label': 'Compose' },
      h('div', { className: 'fm-compose-h' }, title,
        h('button', { className: 'btn fm-btn', onClick: onClose, 'aria-label': 'Close compose' }, '✕')),
      !canSend.length && h('div', { className: 'fm-banner warn', style: { margin: 10 } },
        'Neither connected account has allowed Friday to send. You can write this and file the approval card, but it cannot go out until you reconnect an account with “allow sending” ticked (Settings › Connectors › Google).'),
      h('div', { className: 'fm-field' }, h('label', null, 'From'),
        h('select', { value: from, onChange: e => setFrom(e.target.value) },
          (canSend.length ? canSend : accounts).map(a => h('option', { key: a.id, value: a.id }, (a.label || '') + (a.email ? ' <' + a.email + '>' : '') + (canSend.find(c => c.id === a.id) ? '' : ' (read-only)'))))),
      h('div', { className: 'fm-field' }, h('label', null, 'To'), h(AddrInput, { value: to, onChange: setTo, autoFocus: !to }),
        !showCc && h('button', { className: 'fm-tool', onClick: () => setShowCc(true) }, 'Cc')),
      showCc && h('div', { className: 'fm-field' }, h('label', null, 'Cc'), h(AddrInput, { value: cc, onChange: setCc })),
      showCc && h('div', { className: 'fm-field' }, h('label', null, 'Bcc'), h(AddrInput, { value: bcc, onChange: setBcc })),
      h('div', { className: 'fm-field' }, h('label', null, 'Subject'), h('input', { value: subject, onChange: e => setSubject(e.target.value) })),
      h('div', { ref: ed, className: 'fm-editor', contentEditable: true, suppressContentEditableWarning: true, role: 'textbox', 'aria-multiline': true, 'aria-label': 'Message body', autoFocus: !!to }),
      (atts.length > 0 || fwd.length > 0) && h('div', { className: 'fm-atts', style: { padding: '0 12px 6px' } },
        atts.map((a, i) => h('span', { key: a.sha256, className: 'fm-att' }, '📎 ' + a.filename + ' · ' + Math.max(1, Math.round(a.size / 1024)) + ' KB',
          h('button', { className: 'fm-tool', onClick: () => setAtts(x => x.filter((_, j) => j !== i)), 'aria-label': 'Remove attachment' }, '✕'))),
        fwd.map((a, i) => h('label', { key: a.attachment_id, className: 'fm-att' }, h('input', { type: 'checkbox', checked: a.keep, onChange: e => setFwd(x => x.map((y, j) => j === i ? Object.assign({}, y, { keep: e.target.checked }) : y)) }), '📎 ' + a.filename))),
      h('div', { className: 'fm-tools' },
        [['B', 'bold', 'Bold'], ['I', 'italic', 'Italic'], ['U', 'underline', 'Underline']].map(([l, c, t]) => h('button', { key: c, className: 'fm-tool', title: t, onMouseDown: e => { e.preventDefault(); cmd(c); }, style: { fontWeight: c === 'bold' ? 800 : 400, fontStyle: c === 'italic' ? 'italic' : 'normal', textDecoration: c === 'underline' ? 'underline' : 'none' } }, l)),
        h('button', { className: 'fm-tool', title: 'Bulleted list', onMouseDown: e => { e.preventDefault(); cmd('insertUnorderedList'); } }, '•≡'),
        h('button', { className: 'fm-tool', title: 'Numbered list', onMouseDown: e => { e.preventDefault(); cmd('insertOrderedList'); } }, '1≡'),
        h('button', { className: 'fm-tool', title: 'Link', onMouseDown: e => { e.preventDefault(); const u = window.prompt('Link address'); if (u && /^(https?:|mailto:)/i.test(u)) cmd('createLink', u); } }, '🔗'),
        h('button', { className: 'fm-tool', title: 'Remove formatting', onMouseDown: e => { e.preventDefault(); cmd('removeFormat'); } }, 'Tx'),
        h('button', { className: 'fm-tool', title: 'Attach files', onClick: () => fileRef.current && fileRef.current.click() }, '📎'),
        h('input', { ref: fileRef, type: 'file', multiple: true, style: { display: 'none' }, onChange: e => { upload(e.target.files); e.target.value = ''; } }),
        h('span', { style: { flex: 1 } }),
        state && h('span', { style: { fontSize: 11, color: state.status === 'sent' ? '#7df0b0' : state.status === 'refused' || state.status === 'denied' || state.status === 'failed' ? '#ffb4b4' : '#ffd699', marginRight: 6 } }, state.message),
        state && state.approval_id && !/sent|denied|failed/.test(state.status) && h('button', { className: 'btn fm-btn', onClick: () => window.fridayRunActions && window.fridayRunActions([{ type: 'navigate', workspace: 'system', tab: 'approvals' }]) }, 'Review in Approvals'),
        (!state || state.status === 'refused') && h('button', { className: 'btn fm-btn', disabled: busy, onClick: request, title: 'Files an approval card. Nothing is sent until you approve it.', style: { borderColor: '#00d4ff', color: '#00d4ff' } },
          busy ? 'Asking…' : 'Send — asks for approval')));
  }

  // ── the panel ───────────────────────────────────────────────────────────
  function FridayMailPanel() {
    const [acct, setAcct] = useState('all');
    const [lane, setLane] = useState('all');
    const [data, setData] = useState(null);          // /api/messages response
    const [stats, setStats] = useState(null);
    const [loading, setLoading] = useState(true);
    const [qInput, setQInput] = useState('');
    const [query, setQuery] = useState('');
    const [unreadOnly, setUnreadOnly] = useState(false);
    const [sel, setSel] = useState(() => new Set());
    const [focus, setFocus] = useState(0);
    const [open, setOpen] = useState(null);           // {card, loading, res}
    const [showImages, setShowImages] = useState(false);
    const [compose, setCompose] = useState(null);
    const [toast, setToast] = useState(null);
    const [help, setHelp] = useState(false);
    const [preview, setPreview] = useState(null);
    const [accounts, setAccounts] = useState([]);
    const [canSend, setCanSend] = useState([]);
    const [aiDraft, setAiDraft] = useState(null);
    const undoStack = useRef([]);
    const boxRef = useRef(null), searchRef = useRef(null), listRef = useRef(null);

    const say = (text, undo) => { setToast({ text, undo, t: Date.now() }); };
    useEffect(() => { if (!toast) return; const t = setTimeout(() => setToast(x => x === toast ? null : x), toast.undo ? 7000 : 3500); return () => clearTimeout(t); }, [toast]);

    const load = useCallback((q) => {
      setLoading(true);
      const url = '/api/messages?limit=80' + (q ? '&q=' + encodeURIComponent(q) : '');
      api(url).then(r => r.json()).then(d => { setData(d); setLoading(false); }).catch(e => { setData({ status: 'error', search_failed: true, error: "Couldn't reach Friday: " + e }); setLoading(false); });
      if (!q) api('/api/messages/stats').then(r => r.json()).then(d => { setStats(d); if (d.actionable != null) window._fridayMsgActionable = d.actionable; }).catch(() => {});
    }, []);
    useEffect(() => { load(query); }, [query]);
    useEffect(() => { const iv = setInterval(() => { if (!query) load(''); }, 60000); return () => clearInterval(iv); }, [query]);
    useEffect(() => {
      api('/api/google/accounts').then(r => r.json()).then(d => setAccounts((d.accounts || []).filter(a => (a.services || []).includes('gmail')))).catch(() => {});
      api('/api/mail/can-send').then(r => r.json()).then(d => setCanSend(d.accounts || [])).catch(() => {});
    }, []);

    // deep link {workspace:'messages', lane, thread_id}
    const pending = useRef(null);
    useEffect(() => {
      const apply = t => { if (!t || t.workspace !== 'messages') return; if (t.lane) setLane(t.lane); if (t.thread_id) pending.current = t.thread_id; };
      apply(window.__fridayNavTarget);
      const f = e => apply(e.detail || {});
      window.addEventListener('friday-nav', f);
      return () => window.removeEventListener('friday-nav', f);
    }, []);

    const all = (data && data.messages) || [];
    const shown = all.filter(m => (acct === 'all' || m.account_id === acct) && (lane === 'all' || m.lane === lane) && (!unreadOnly || m.unread));
    useEffect(() => {
      if (!pending.current || !all.length) return;
      const m = all.find(x => x.thread_id === pending.current || x.id === pending.current);
      if (m) { pending.current = null; openThread(m); }
    }, [data]);
    useEffect(() => { if (focus >= shown.length) setFocus(Math.max(0, shown.length - 1)); }, [shown.length]);

    const patch = (ids, fn) => setData(d => d && Object.assign({}, d, { messages: (d.messages || []).map(m => ids.includes(m.id) ? fn(m) : m) }));
    const drop = ids => setData(d => d && Object.assign({}, d, { messages: (d.messages || []).filter(m => !ids.includes(m.id)) }));

    // Friday-local actions; each one can be undone exactly.
    const act = (cards, action, opts) => {
      cards = cards.filter(Boolean);
      if (!cards.length) return Promise.resolve();
      const ids = cards.map(c => c.id);
      const prev = cards.map(c => Object.assign({}, c));
      return post('/api/messages/action', { ids, action }).then(({ ok, j }) => {
        if (!ok) { say(j.message || 'That did not work.'); return; }
        if (action === 'archive' || action === 'snooze') { drop(ids); if (open && ids.includes(open.card.id)) setOpen(null); }
        else patch(ids, m => Object.assign({}, m, action === 'flag' ? { flagged: true } : action === 'unflag' ? { flagged: false } : action === 'read' ? { unread: false } : action === 'unread' ? { unread: true } : {}));
        const undo = { before: j.before, cards: prev, label: action };
        undoStack.current.push(undo);
        if (!(opts && opts.silent)) say(({ archive: 'Archived', snooze: 'Snoozed for 4 hours', flag: 'Flagged', unflag: 'Unflagged', read: 'Marked read', unread: 'Marked unread' }[action] || action) + (ids.length > 1 ? ' · ' + ids.length + ' messages' : '') + ' (in Friday only)', undo);
        setSel(new Set());
      });
    };
    const moveLane = (cards, newLane) => {
      cards = cards.filter(Boolean);
      Promise.all(cards.map(c => post('/api/messages/classify', { id: c.id, lane: newLane }))).then(rs => {
        const before = {};
        rs.forEach(r => Object.assign(before, (r.j && r.j.before) || {}));
        patch(cards.map(c => c.id), m => Object.assign({}, m, { lane: newLane }));
        const undo = { before, cards: cards.map(c => Object.assign({}, c)), label: 'move' };
        undoStack.current.push(undo);
        say('Moved to ' + laneLabel(newLane) + (cards.length > 1 ? ' · ' + cards.length + ' messages' : ''), undo);
        setSel(new Set());
      });
    };
    const undo = u => {
      u = u || undoStack.current.pop();
      if (!u) { say('Nothing to undo.'); return; }
      undoStack.current = undoStack.current.filter(x => x !== u);
      post('/api/messages/restore', { states: u.before }).then(({ ok }) => {
        if (!ok) { say('Undo failed.'); return; }
        setData(d => {
          if (!d) return d;
          const map = new Map(u.cards.map(c => [c.id, c]));
          const kept = (d.messages || []).map(m => map.has(m.id) ? map.get(m.id) : m);
          const missing = u.cards.filter(c => !(d.messages || []).some(m => m.id === c.id));
          return Object.assign({}, d, { messages: kept.concat(missing).sort((a, b) => String(b.timestamp).localeCompare(String(a.timestamp))) });
        });
        setToast(null); say('Undone.');
      });
    };

    const openThread = m => {
      if (!m) return;
      setOpen({ card: m, loading: true, res: null });
      setShowImages(false); setAiDraft(null);
      if (m.unread) act([m], 'read', { silent: true });
      api('/api/messages/' + encodeURIComponent(m.thread_id || m.id) + (m.account_id ? '?account=' + encodeURIComponent(m.account_id) : ''))
        .then(r => r.json()).then(res => setOpen(o => o && o.card.id === m.id ? { card: m, loading: false, res } : o))
        .catch(e => setOpen(o => o && o.card.id === m.id ? { card: m, loading: false, res: { status: 'error', error: "Couldn't reach Friday: " + e } } : o));
    };

    const quoteOf = msg => '<br><br><div>On ' + esc(msg.date || '') + ', ' + esc(msg.sender) + ' wrote:</div><blockquote>' + (msg.html || esc(msg.body).replace(/\n/g, '<br>')) + '</blockquote>';
    const startReply = mode => {
      const msgs = open && open.res && open.res.messages;
      if (!msgs || !msgs.length) return;
      const last = msgs[msgs.length - 1];
      const mine = new Set(accounts.map(a => (a.email || '').toLowerCase()));
      const subj = last.subject || '';
      if (mode === 'forward') {
        setCompose({ mode, account_id: open.res.account_id, to: '', subject: /^fwd?:/i.test(subj) ? subj : 'Fwd: ' + subj,
          html: '<br><br><div>---------- Forwarded message ---------<br>From: ' + esc(last.sender) + '<br>Date: ' + esc(last.date) + '<br>Subject: ' + esc(subj) + '<br>To: ' + esc(last.to) + '</div><br>' + (last.html || esc(last.body).replace(/\n/g, '<br>')),
          forward: (last.attachments || []).map(a => ({ account_id: open.res.account_id, message_id: last.id, attachment_id: a.attachment_id, filename: a.filename, mime: a.mime })) });
        return;
      }
      const replyTo = last.reply_to || last.sender;
      const fromMe = mine.has(addrOf(last.sender));
      let to = fromMe ? splitAddrs(last.to).map(addrOf) : [addrOf(replyTo)];
      let cc = [];
      if (mode === 'replyAll') {
        const everyone = splitAddrs(last.to).concat(splitAddrs(last.cc)).map(addrOf);
        cc = everyone.filter(a => !mine.has(a) && !to.includes(a));
      }
      setCompose({ mode, account_id: open.res.account_id, to: to.join(', '), cc: cc.join(', '),
        subject: /^re:/i.test(subj) ? subj : 'Re: ' + subj, html: (aiDraft && aiDraft.text ? esc(aiDraft.text).replace(/\n/g, '<br>') : '') + quoteOf(last),
        thread_id: last.thread_id || open.card.thread_id, in_reply_to: last.message_id_header, references: last.references });
    };
    const fridayDraft = () => {
      const m = open && open.card; if (!m) return;
      setAiDraft({ busy: true, text: '' });
      post('/api/messages/draft', { id: m.id, sender: m.sender_email || m.sender, subject: m.subject, snippet: m.snippet, lane: m.lane })
        .then(({ j }) => setAiDraft({ busy: false, text: j.draft || '', error: j.draft ? null : (j.message || 'Friday could not draft a reply.') }))
        .catch(() => setAiDraft({ busy: false, text: '', error: 'Friday could not draft a reply.' }));
    };

    const selected = () => shown.filter(m => sel.has(m.id));
    const targets = () => { const s = selected(); return s.length ? s : open ? [open.card] : shown[focus] ? [shown[focus]] : []; };
    const toggleSel = (m, e) => { setSel(s => { const n = new Set(s); if (n.has(m.id)) n.delete(m.id); else n.add(m.id); return n; }); if (e) e.stopPropagation(); };

    const onKey = e => {
      const tag = (e.target.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select' || e.target.isContentEditable) {
        if (e.key === 'Escape' && e.target === searchRef.current) { searchRef.current.blur(); boxRef.current && boxRef.current.focus(); }
        return;
      }
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const k = e.key;
      const cur = shown[focus];
      const handled = {
        j: () => { setFocus(f => Math.min(shown.length - 1, f + 1)); },
        k: () => { setFocus(f => Math.max(0, f - 1)); },
        ArrowDown: () => { setFocus(f => Math.min(shown.length - 1, f + 1)); },
        ArrowUp: () => { setFocus(f => Math.max(0, f - 1)); },
        o: () => openThread(cur), Enter: () => openThread(cur),
        u: () => setOpen(null), Escape: () => { if (help) setHelp(false); else if (preview) setPreview(null); else if (sel.size) setSel(new Set()); else setOpen(null); },
        x: () => cur && toggleSel(cur),
        e: () => act(targets(), 'archive'), s: () => { const t = targets(); act(t, t.every(m => m.flagged) ? 'unflag' : 'flag'); },
        U: () => act(targets(), 'unread'), I: () => act(targets(), 'read'), b: () => act(targets(), 'snooze'),
        r: () => startReply('reply'), a: () => startReply('replyAll'), f: () => startReply('forward'),
        c: () => setCompose({ mode: 'new' }), '/': () => searchRef.current && searchRef.current.focus(),
        z: () => undo(), '?': () => setHelp(v => !v), g: () => load(query)
      }[k];
      if (!handled) return;
      e.preventDefault(); e.stopPropagation();
      handled();
    };
    useEffect(() => {
      const row = listRef.current && listRef.current.querySelector('.fm-row.focus');
      if (row && row.scrollIntoView) row.scrollIntoView({ block: 'nearest' });
    }, [focus]);

    // account chips with unread counts
    const unreadBy = {};
    ((data && data.messages) || []).forEach(m => { if (m.unread) unreadBy[m.account_id] = (unreadBy[m.account_id] || 0) + 1; });
    const errs = (data && data.errors) || [];
    const failed = data && data.search_failed;
    const counts = (stats && stats.counts) || {};

    const Row = (m, i) => {
      const L = LANES[m.lane] || [m.lane, '•'];
      return h('div', {
        key: m.id, className: 'fm-row' + (m.unread ? ' unread' : '') + (i === focus ? ' focus' : '') + (open && open.card.id === m.id ? ' open' : ''),
        onClick: e => { setFocus(i); if (e.shiftKey || e.ctrlKey || e.metaKey) toggleSel(m, e); else openThread(m); },
        role: 'row', 'aria-selected': sel.has(m.id)
      },
        h('input', { type: 'checkbox', checked: sel.has(m.id), onClick: e => toggleSel(m, e), onChange: () => {}, 'aria-label': 'Select message' }),
        h('span', { className: 'fm-dot', style: { background: m.account_color || '#7c8aa5' }, title: m.account_label }),
        h('div', { className: 'fm-from' }, (m.flagged ? '🚩 ' : '') + (m.sender || m.sender_email)),
        h('div', { className: 'fm-line' },
          h('span', { className: 'fm-badge', style: { borderColor: 'rgba(0,212,255,0.35)', color: '#8fd3ff' } }, L[1] + ' ' + L[0]),
          h('span', { className: 'fm-subj' }, m.subject), h('span', { className: 'fm-snip' }, ' — ' + (m.snippet || ''))),
        h('div', { className: 'fm-when' }, (m.has_attachment ? '📎 ' : '') + fmtWhen(m.timestamp)));
    };

    const T = open && open.res;
    return h('div', { className: 'fm', ref: boxRef, tabIndex: 0, onKeyDown: onKey, style: { outline: 'none' } },
      // row 1: accounts, search, actions
      h('div', { className: 'fm-bar' },
        h('span', { className: 'fm-chip' + (acct === 'all' ? ' on' : ''), onClick: () => setAcct('all') }, '📬 All accounts',
          data && !data.search_failed && h('span', { className: 'fm-count' }, all.filter(m => m.unread).length + ' unread')),
        accounts.map(a => h('span', { key: a.id, className: 'fm-chip' + (acct === a.id ? ' on' : ''), onClick: () => setAcct(a.id), title: a.email },
          h('span', { className: 'fm-dot', style: { background: a.color || '#7c8aa5' } }), a.label || a.email,
          data && !data.search_failed && h('span', { className: 'fm-count' }, (unreadBy[a.id] || 0) + ' unread'),
          errs.some(x => x.account_id === a.id) && h('span', { title: 'This account could not be read', style: { color: '#ff8a8a' } }, '⚠'))),
        h('input', { ref: searchRef, className: 'fm-search', value: qInput, placeholder: 'Search mail — Gmail search: from:ada is:unread has:attachment newer_than:7d …  (press /)',
          'aria-label': 'Search mail', onChange: e => setQInput(e.target.value), onKeyDown: e => { if (e.key === 'Enter') setQuery(qInput.trim()); } }),
        query && h('button', { className: 'btn fm-btn', onClick: () => { setQInput(''); setQuery(''); } }, '✕ Clear search'),
        h('button', { className: 'btn fm-btn', onClick: () => load(query), title: 'Refresh (g)' }, '↻'),
        h('button', { className: 'btn fm-btn', onClick: () => setCompose({ mode: 'new' }), style: { borderColor: '#00d4ff', color: '#00d4ff' }, title: 'Compose (c)' }, '✎ Compose'),
        h('button', { className: 'btn fm-btn', onClick: () => setHelp(true), title: 'Keyboard shortcuts (?)' }, '⌨')),
      // row 2: lanes
      !query && h('div', { className: 'fm-bar' },
        Object.keys(LANES).map(id => h('span', { key: id, className: 'fm-chip' + (lane === id ? ' on' : ''), onClick: () => setLane(id) },
          LANES[id][1] + ' ' + LANES[id][0], id !== 'all' && counts[id] ? h('span', { className: 'fm-count' }, counts[id]) : null)),
        h('span', { className: 'fm-chip' + (unreadOnly ? ' on' : ''), onClick: () => setUnreadOnly(v => !v) }, '● Unread only')),
      // honest status
      loading && !data && h('div', { className: 'fm-banner info' }, 'Reading your mail…'),
      failed && h('div', { className: 'fm-banner err', role: 'alert' }, '⚠ ' + (data.error || "Couldn't read mail.") + ' This is not an empty inbox — the read did not happen.'),
      !failed && data && data.partial && h('div', { className: 'fm-banner warn' }, '⚠ Showing only part of your mail: ' + errs.map(e => (e.label || 'an account') + ' — ' + e.error).join('; ')),
      !failed && data && data.source && /cache|legacy/.test(data.source) && h('div', { className: 'fm-banner warn' }, 'Showing Friday’s saved copy of your mail, not a live read (' + data.source + ').'),
      query && !failed && data && h('div', { className: 'fm-banner info' }, 'Gmail search for “' + query + '”: ' + (data.total || 0) + ' result' + (data.total === 1 ? '' : 's') + ' across ' + (acct === 'all' ? 'all accounts' : 'this account') + (data.partial ? ' (partial)' : '')),
      // bulk bar
      sel.size > 0 && h('div', { className: 'fm-bulk' }, sel.size + ' selected',
        h('button', { className: 'btn fm-btn', onClick: () => act(selected(), 'archive') }, '🗄 Archive'),
        h('button', { className: 'btn fm-btn', onClick: () => act(selected(), 'read') }, 'Mark read'),
        h('button', { className: 'btn fm-btn', onClick: () => act(selected(), 'unread') }, 'Mark unread'),
        h('button', { className: 'btn fm-btn', onClick: () => act(selected(), 'flag') }, '🚩 Flag'),
        h('button', { className: 'btn fm-btn', onClick: () => act(selected(), 'snooze') }, '😴 Snooze 4h'),
        h('select', { className: 'btn fm-btn', value: '', onChange: e => e.target.value && moveLane(selected(), e.target.value), 'aria-label': 'Move to lane' },
          h('option', { value: '' }, 'Move to lane…'), Object.keys(LANES).filter(k => k !== 'all').map(k => h('option', { key: k, value: k }, LANES[k][0]))),
        h('button', { className: 'btn fm-btn', onClick: () => setSel(new Set(shown.map(m => m.id))) }, 'Select all ' + shown.length),
        h('button', { className: 'btn fm-btn', onClick: () => setSel(new Set()) }, 'Clear')),
      // list + thread
      h('div', { className: 'fm-main' },
        h('div', { className: 'fm-list', ref: listRef, role: 'grid', 'aria-label': 'Messages' },
          failed ? h('div', { style: { padding: 20, color: '#ff9a9a', fontSize: 12 } }, 'No list: the read failed (see above).')
            : shown.length ? shown.map(Row)
              : data && !loading ? h('div', { style: { padding: 20, color: '#7f93ad', fontSize: 12 } }, query ? 'Gmail found nothing for that search.' : 'Nothing here.') : null),
        open && h('div', { className: 'fm-thread' },
          h('div', { style: { display: 'flex', gap: 8, alignItems: 'flex-start', justifyContent: 'space-between' } },
            h('div', null,
              h('div', { style: { fontSize: 15, fontWeight: 700, color: '#fff' } }, open.card.subject),
              h('div', { style: { fontSize: 11, color: '#8fa6c4', marginTop: 2 } },
                h('span', { className: 'fm-dot', style: { background: open.card.account_color, marginRight: 5 } }), open.card.account_label + ' · ' + laneLabel(open.card.lane))),
            h('button', { className: 'btn fm-btn', onClick: () => setOpen(null), 'aria-label': 'Close thread' }, '✕')),
          h('div', { className: 'fm-bar', style: { margin: '8px 0' } },
            h('button', { className: 'btn fm-btn', disabled: !(T && T.status === 'ok'), onClick: () => startReply('reply'), title: 'r' }, '↩ Reply'),
            h('button', { className: 'btn fm-btn', disabled: !(T && T.status === 'ok'), onClick: () => startReply('replyAll'), title: 'a' }, '↩↩ Reply all'),
            h('button', { className: 'btn fm-btn', disabled: !(T && T.status === 'ok'), onClick: () => startReply('forward'), title: 'f' }, '↪ Forward'),
            h('button', { className: 'btn fm-btn', onClick: fridayDraft }, '💬 Draft with Friday'),
            h('button', { className: 'btn fm-btn', onClick: () => act([open.card], 'archive'), title: 'e' }, '🗄 Archive'),
            h('button', { className: 'btn fm-btn', onClick: () => act([open.card], 'snooze'), title: 'b' }, '😴 4h'),
            h('button', { className: 'btn fm-btn', onClick: () => act([open.card], open.card.flagged ? 'unflag' : 'flag'), title: 's' }, open.card.flagged ? 'Unflag' : '🚩 Flag'),
            h('button', { className: 'btn fm-btn', onClick: () => act([open.card], 'unread'), title: 'Shift+U' }, 'Mark unread'),
            h('select', { className: 'btn fm-btn', value: open.card.lane, onChange: e => moveLane([open.card], e.target.value), 'aria-label': 'Lane' },
              Object.keys(LANES).filter(k => k !== 'all').map(k => h('option', { key: k, value: k }, LANES[k][1] + ' ' + LANES[k][0]))),
            h('label', { style: { fontSize: 11, color: '#8fa6c4', display: 'flex', gap: 4, alignItems: 'center' } }, h('input', { type: 'checkbox', checked: showImages, onChange: e => setShowImages(e.target.checked) }), 'Show remote images')),
          aiDraft && h('div', { className: 'fm-banner info', style: { flexDirection: 'column', alignItems: 'stretch' } },
            aiDraft.busy ? 'Friday is drafting a reply…' : aiDraft.error ? aiDraft.error : [h('div', { key: 't', style: { whiteSpace: 'pre-wrap' } }, aiDraft.text),
              h('div', { key: 'b', style: { display: 'flex', gap: 6, marginTop: 6 } }, h('button', { className: 'btn fm-btn', onClick: () => startReply('reply') }, 'Use in reply'), h('button', { className: 'btn fm-btn', onClick: () => setAiDraft(null) }, 'Discard'))]),
          open.loading && h('div', { className: 'fm-banner info' }, 'Opening the thread…'),
          T && T.status !== 'ok' && h('div', { className: 'fm-banner err', role: 'alert' }, '⚠ ' + (T.error || 'Could not open this thread.')),
          T && T.source === 'cache' && h('div', { className: 'fm-banner warn' }, 'Showing Friday’s saved copy: ' + (T.note || 'Gmail was not reachable.')),
          T && (T.messages || []).map(msg => h('div', { key: msg.id, className: 'fm-msg' },
            h('div', { className: 'fm-hdr' }, h('b', null, msg.sender), ' · ', msg.date || fmtWhen(msg.timestamp), h('br'),
              msg.to && ['to ', msg.to], msg.cc && [h('br', { key: 'b' }), 'cc ', msg.cc]),
            h(MailBody, { html: msg.html, text: msg.body, showImages }),
            (msg.attachments || []).length > 0 && h('div', { className: 'fm-atts' }, msg.attachments.map(a => h('span', { key: a.attachment_id, style: { display: 'inline-flex', gap: 4 } },
              (/^image\/(png|jpe?g|gif|webp)$/.test(a.mime) || a.mime === 'application/pdf') && h('button', { className: 'fm-att', onClick: () => setPreview(a) }, '👁 ' + a.filename),
              h('a', { className: 'fm-att', href: a.url + '&dl=1', download: a.filename, title: 'Download' }, '⬇ ' + (/^image|pdf/.test(a.mime) ? '' : a.filename + ' ') + '· ' + Math.max(1, Math.round((a.size || 0) / 1024)) + ' KB')))))))),
      compose && h(Composer, { key: compose.mode + (compose.thread_id || '') + (compose.subject || ''), init: compose, accounts, canSend, onClose: () => setCompose(null), say }),
      preview && h('div', { className: 'fm-preview', onClick: () => setPreview(null) },
        h('div', { style: { display: 'flex', justifyContent: 'space-between', padding: 10, fontSize: 12 } }, preview.filename,
          h('span', null, h('a', { className: 'fm-att', href: preview.url, download: preview.filename, onClick: e => e.stopPropagation() }, '⬇ Download'), ' ', h('button', { className: 'btn fm-btn' }, '✕'))),
        /^image\//.test(preview.mime) ? h('img', { src: preview.url, alt: preview.filename }) : h('iframe', { src: preview.url, title: preview.filename })),
      help && h('div', { className: 'fm-help', onClick: () => setHelp(false) }, h('div', null,
        [['j / k', 'next / previous'], ['o or Enter', 'open'], ['u', 'back to list'], ['x', 'select'], ['e', 'archive'], ['s', 'flag / unflag'], ['Shift+U', 'mark unread'], ['Shift+I', 'mark read'],
         ['b', 'snooze 4 hours'], ['z', 'undo'], ['r', 'reply'], ['a', 'reply all'], ['f', 'forward'], ['c', 'compose'], ['/', 'search'], ['g', 'refresh'], ['?', 'this help'], ['Esc', 'close / clear']]
          .map(([k, d]) => h('div', { key: k, style: { breakInside: 'avoid', margin: '3px 0' } }, h('kbd', null, k), d)),
        h('div', { style: { marginTop: 10, color: '#7f93ad', columnSpan: 'all' } }, 'Archive, snooze, flag and read/unread change Friday’s view only; Gmail itself is not changed. Sending always waits for your approval.'))),
      toast && h('div', { className: 'fm-toast', role: 'status' }, toast.text,
        toast.undo && h('button', { className: 'btn fm-btn', onClick: () => undo(toast.undo) }, 'Undo (z)')));
  }

  window.FridayMailPanel = FridayMailPanel;
})();
