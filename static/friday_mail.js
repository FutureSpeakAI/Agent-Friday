/* Messages: a Gmail-grade inbox over Friday's triage.
 *
 * What it does, and what it deliberately does not:
 * - Reads both connected accounts (All / each account), with Gmail's own
 *   search (Enter sends the query to Gmail's q=) and Gmail's folders and
 *   labels down the side. A failure is shown as a failure, never as "0" or
 *   "No messages"; a partial result says which account is missing.
 * - Every row acts without being opened: hover buttons, checkboxes with a
 *   bulk bar, the Gmail keyboard shortcuts and a right-click menu.
 * - The owner's own clicks act at once and can be undone (toast, or Z):
 *   archive, Delete (Gmail's Trash, kept 30 days and restorable), spam,
 *   star, important, read/unread, labels, snooze and mute. Trash, spam,
 *   importance and labels exist only in Gmail and need an account
 *   reconnected with sending (gmail.modify); without it they are refused and
 *   the page says so. Archive, star and read state then change in Friday
 *   only. Snooze and mute are always Friday's own. Nothing deletes
 *   permanently.
 * - Threads open from the account they belong to, as mail in a sandboxed
 *   frame with no scripts and remote images off until asked for, adapted to
 *   Friday's dark theme (or shown as sent, per message, remembered per
 *   sender), with attachments to preview or download, the original source,
 *   and print.
 * - Reply, reply all, forward and compose never send. They file an approval
 *   card; the message goes out only after it is approved, and only from an
 *   account that granted sending. Unsubscribing tells the sender the address
 *   is read, so it is confirmed first.
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
    .then(r => r.json().then(j => ({ ok: r.ok, status: r.status, j })));
  const LANES = {
    all: ['All', '📥'], career: ['Career', '💼'], finance: ['Finance', '💰'], futurespeak: ['Projects', '🚀'],
    family: ['Family', '👪'], subscriptions: ['Subscriptions', '📰'], noise: ['Noise', '🔇']
  };
  const laneLabel = id => (LANES[id] || [id])[0];
  // Gmail's folders; '' is Friday's own triage view.
  const FOLDERS = [
    ['', '✨', 'Priority', 'Friday’s triage: what is recent or unread, sorted into lanes'],
    ['inbox', '📥', 'Inbox'], ['starred', '⭐', 'Starred'], ['important', '❗', 'Important'], ['snoozed', '⏰', 'Snoozed', 'Snoozed in Gmail itself'],
    ['sent', '📤', 'Sent'], ['drafts', '📝', 'Drafts'], ['scheduled', '🗓', 'Scheduled'], ['all', '🗂', 'All Mail'],
    ['spam', '⚠', 'Spam'], ['trash', '🗑', 'Trash']
  ];
  const CATEGORIES = [['primary', 'Primary'], ['social', 'Social'], ['promotions', 'Promotions'], ['updates', 'Updates'], ['forums', 'Forums']];
  const folderName = f => { if (!f) return 'Priority'; if (f.indexOf('label:') === 0) return f.slice(6); if (f.indexOf('category:') === 0) return 'Inbox · ' + f.slice(9); const x = FOLDERS.find(r => r[0] === f); return x ? x[2] : f; };
  const SEARCH_CHIPS = [['has:attachment', '📎 Has attachment'], ['is:unread', '● Unread'], ['is:starred', '⭐ Starred'], ['newer_than:7d', '🕑 Last 7 days'], ['to:me', '→ To me'], ['from:me', '← From me'], ['larger:5M', '⬛ Over 5 MB']];
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
  const store = {
    get: (k, d) => { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (_) { return d; } },
    set: (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) {} }
  };

  // Snooze choices, Gmail's: later today, tomorrow, this weekend, next week.
  const at = (d, hr) => { const x = new Date(d); x.setHours(hr, 0, 0, 0); return x; };
  const snoozeChoices = () => {
    const now = new Date(), out = [];
    const later = now.getHours() < 17 ? at(now, 18) : new Date(now.getTime() + 3 * 3600e3);
    out.push(['Later today', later]);
    const tmr = new Date(now); tmr.setDate(now.getDate() + 1); out.push(['Tomorrow morning', at(tmr, 8)]);
    const sat = new Date(now); sat.setDate(now.getDate() + ((6 - now.getDay() + 7) % 7 || 7)); out.push(['This weekend', at(sat, 8)]);
    const mon = new Date(now); mon.setDate(now.getDate() + ((1 - now.getDay() + 7) % 7 || 7)); out.push(['Next week', at(mon, 8)]);
    return out;
  };
  const fmtUntil = d => d.toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' });
  // Friday keeps snooze times as local wall-clock ISO (no zone), like the server's own now().
  const localIso = d => { const p = n => String(n).padStart(2, '0'); return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + 'T' + p(d.getHours()) + ':' + p(d.getMinutes()) + ':00'; };
  // Gmail sends snippets HTML-escaped ("it&#39;s"); a textarea decodes them without running anything.
  const unent = s => { if (!/&[#a-z0-9]+;/i.test(s || '')) return s || ''; const t = document.createElement('textarea'); t.innerHTML = s; return t.value; };
  // An account record lists its services as a list or as {service: true}.
  const hasGmail = a => { const s = a && a.services; return Array.isArray(s) ? s.includes('gmail') : !!(s && s.gmail); };

  // ── styles (scoped by .fm-) ─────────────────────────────────────────────
  if (!document.getElementById('fm-style')) {
    const st = document.createElement('style');
    st.id = 'fm-style';
    st.textContent = `
      .fm { display:flex; flex-direction:column; gap:8px; font-family: Inter, sans-serif; color:#dbe6f5; container-type:inline-size; }
      /* The panes take the height of the frame (a tab or a window; see "Filling the frame"), not a guess from the viewport */
      .fm.ws-fill { min-height:460px; }
      .fm select { color-scheme: dark; }
      .fm select option, .fm select optgroup { background-color:#0b1220; color:#e6f0ff; }
      .fm-bar { display:flex; gap:6px; align-items:center; flex-wrap:wrap; }
      .fm-chip { display:inline-flex; gap:6px; align-items:center; padding:5px 10px; border-radius:999px; font-size:11px; cursor:pointer;
        border:1px solid rgba(255,255,255,0.12); background:rgba(255,255,255,0.03); color:#b8c7dc; user-select:none; }
      .fm-chip:hover { border-color:rgba(0,212,255,0.5); color:#e6f4ff; }
      .fm-chip.on { border-color:#00d4ff; color:#e6f9ff; background:rgba(0,212,255,0.12); }
      .fm-chip.sm { padding:3px 8px; font-size:10.5px; }
      .fm-dot { width:8px; height:8px; border-radius:50%; display:inline-block; flex:none; }
      .fm-count { font-family:'JetBrains Mono',monospace; font-size:10px; opacity:.85; }
      .fm-search { flex:1 1 260px; min-width:180px; background:#0b1220; color:#e6f0ff; border:1px solid #24406a; border-radius:8px; padding:8px 10px; font-size:12px; }
      .fm-search:focus { outline:none; border-color:#00d4ff; box-shadow:0 0 0 2px rgba(0,212,255,0.15); }
      .fm-banner { padding:8px 12px; border-radius:8px; font-size:12px; display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
      .fm-banner.err { background:rgba(239,68,68,0.10); border:1px solid rgba(239,68,68,0.45); color:#ffb4b4; }
      .fm-banner.warn { background:rgba(245,158,11,0.10); border:1px solid rgba(245,158,11,0.45); color:#ffd699; }
      .fm-banner.info { background:rgba(0,212,255,0.07); border:1px solid rgba(0,212,255,0.3); color:#aee9ff; }
      .fm-main { position:relative; display:flex; gap:10px; min-height:0; flex:1 1 auto; }
      .fm-side { flex:0 0 188px; display:flex; flex-direction:column; gap:1px; min-height:0; overflow:auto; font-size:12px; }
      .fm-mid .fm-side, .fm-narrow .fm-side { position:absolute; z-index:20; top:0; bottom:0; left:0; width:230px; padding-top:6px;
        background:rgba(6,10,18,0.98); border:1px solid rgba(0,212,255,0.25); border-radius:10px; box-shadow:10px 0 34px rgba(0,0,0,0.55); }
      .fm-side-close { justify-content:flex-end; color:#8fa6c4 !important; font-size:11px !important; }
      .fm-side button { display:flex; gap:8px; align-items:center; text-align:left; background:transparent; border:0; border-radius:0 999px 999px 0; color:#b8c7dc; padding:6px 10px; cursor:pointer; font-size:12px; }
      .fm-side button:hover { background:rgba(0,212,255,0.06); color:#e6f4ff; }
      .fm-side button.on { background:rgba(0,212,255,0.14); color:#e6f9ff; font-weight:700; }
      .fm-side .h { font-size:9.5px; letter-spacing:.12em; text-transform:uppercase; color:#5f7896; padding:10px 10px 4px; }
      .fm-listwrap { flex:1 1 38%; min-width:300px; min-height:0; display:flex; flex-direction:column; border:1px solid rgba(0,212,255,0.12); border-radius:10px; overflow:hidden; }
      .fm-listhead { display:flex; gap:8px; align-items:center; padding:6px 10px; border-bottom:1px solid rgba(255,255,255,0.06); font-size:11px; color:#8fa6c4; background:rgba(255,255,255,0.015); }
      .fm-list { flex:1 1 auto; min-height:0; overflow:auto; }
      .fm-narrow .fm-listwrap { min-width:0; }
      .fm-narrow.fm-reading .fm-listwrap { display:none; }
      .fm-row { position:relative; display:grid; grid-template-columns: 22px 10px minmax(90px,170px) 1fr auto; gap:8px; align-items:center; padding:8px 10px;
        border-bottom:1px solid rgba(255,255,255,0.05); cursor:pointer; font-size:12px; }
      .fm-row:hover { background:rgba(0,212,255,0.05); }
      .fm-row.focus { box-shadow: inset 3px 0 0 #00d4ff; background:rgba(0,212,255,0.07); }
      .fm-row.open { background:rgba(123,97,255,0.12); }
      .fm-row.sel { background:rgba(123,97,255,0.16); }
      .fm-row.busy { opacity:.55; pointer-events:none; }
      .fm-row.unread .fm-from, .fm-row.unread .fm-subj { font-weight:700; color:#fff; }
      .fm-row.await .fm-from::after { content:'↩'; color:#7df0b0; margin-left:5px; font-weight:400; }
      .fm-from { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:#c6d4e8; }
      .fm-line { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
      .fm-subj { color:#dbe6f5; } .fm-snip { color:#7f93ad; }
      .fm-when { color:#7f93ad; font-size:11px; white-space:nowrap; font-family:'JetBrains Mono',monospace; }
      .fm-acts { display:none; gap:2px; }
      .fm-row:hover .fm-acts, .fm-row.focus .fm-acts { display:flex; }
      .fm-row:hover .fm-when, .fm-row.focus .fm-when { display:none; }
      .fm-act { background:rgba(8,12,22,0.9); border:1px solid rgba(255,255,255,0.12); color:#cfe3ff; border-radius:6px; width:28px; height:26px; cursor:pointer; font-size:13px; line-height:1; padding:0; }
      .fm-act:hover { border-color:#00d4ff; background:rgba(0,212,255,0.15); }
      .fm-act.danger:hover { border-color:#ef4444; background:rgba(239,68,68,0.18); }
      .fm-badge { font-size:9px; padding:1px 6px; border-radius:999px; border:1px solid; margin-right:5px; }
      .fm-lab { font-size:9px; padding:1px 6px; border-radius:4px; background:rgba(255,255,255,0.08); color:#cfe3ff; margin-right:5px; }
      .fm-thread { flex:1 1 62%; min-width:0; min-height:0; overflow:auto; border:1px solid rgba(0,212,255,0.12); border-radius:10px; padding:12px; }
      .fm-reading-empty { display:flex; align-items:center; justify-content:center; }
      /* until a conversation is open, the list has the larger share */
      .fm:not(.fm-reading) .fm-listwrap { flex-basis:58%; }
      .fm:not(.fm-reading) .fm-thread { flex-basis:42%; }
      .fm-reading-hint { text-align:center; color:#7f93ad; font-size:13px; max-width:340px; }
      .fm-msg { border:1px solid rgba(255,255,255,0.07); border-radius:8px; padding:10px; margin-bottom:10px; background:rgba(255,255,255,0.02); }
      .fm-hdr { font-size:11px; color:#8fa6c4; line-height:1.6; display:flex; gap:8px; align-items:flex-start; }
      .fm-hdr b { color:#e6f0ff; }
      .fm-link { background:none; border:0; color:#7dd3fc; cursor:pointer; font-size:11px; padding:0; text-decoration:underline; }
      .fm-body { width:100%; border:0; border-radius:6px; margin-top:8px; min-height:60px; background:#fff; }
      .fm-body.adapted { background:#0f1522; }
      .fm-text { white-space:pre-wrap; font-size:12.5px; line-height:1.55; color:#dbe6f5; margin-top:8px; }
      .fm-atts { display:flex; gap:6px; flex-wrap:wrap; margin-top:8px; }
      .fm-att { display:inline-flex; gap:6px; align-items:center; font-size:11px; padding:5px 8px; border-radius:6px; border:1px solid rgba(255,255,255,0.12); background:rgba(0,0,0,0.25); color:#cfe3ff; text-decoration:none; }
      .fm-att:hover { border-color:#00d4ff; }
      .fm-btn { font-size:11px; padding:5px 10px; min-height:28px; }
      .fm-btn.danger { border-color:rgba(239,68,68,0.6); color:#ffb4b4; }
      .fm-bulk { display:flex; gap:6px; align-items:center; padding:6px 10px; border-radius:8px; background:rgba(123,97,255,0.12); border:1px solid rgba(123,97,255,0.4); font-size:12px; flex-wrap:wrap; }
      .fm-menu { position:fixed; z-index:90; min-width:220px; max-width:320px; max-height:70vh; overflow:auto; background:#0a1020; border:1px solid rgba(0,212,255,0.4); border-radius:10px; padding:5px; box-shadow:0 14px 40px rgba(0,0,0,.6); font-size:12px; }
      .fm-menu .it { display:flex; gap:9px; align-items:center; padding:7px 10px; border-radius:6px; cursor:pointer; color:#dbe6f5; }
      .fm-menu .it:hover, .fm-menu .it.hi { background:rgba(0,212,255,0.14); }
      .fm-menu .it.off { opacity:.45; cursor:default; }
      .fm-menu .it .k { margin-left:auto; font-family:'JetBrains Mono',monospace; font-size:10px; color:#6f86a6; }
      .fm-menu .it .ic { width:16px; text-align:center; }
      .fm-menu .hd { padding:7px 10px 3px; font-size:9.5px; letter-spacing:.12em; text-transform:uppercase; color:#5f7896; }
      .fm-menu .sp { height:1px; background:rgba(255,255,255,0.08); margin:4px 2px; }
      .fm-menu input { width:100%; box-sizing:border-box; background:#0b1220; color:#e6f0ff; border:1px solid #24406a; border-radius:6px; padding:6px 8px; font-size:12px; color-scheme:dark; }
      .fm-dialog { position:fixed; inset:0; z-index:95; background:rgba(0,0,0,0.55); display:flex; align-items:center; justify-content:center; }
      .fm-dialog > div { background:#0a1020; border:1px solid rgba(0,212,255,0.4); border-radius:12px; padding:18px 20px; font-size:12.5px; color:#cfe3ff; width:min(560px, 92vw); max-height:84vh; overflow:auto; }
      .fm-dialog h4 { margin:0 0 10px; color:#fff; font-size:14px; }
      .fm-dialog pre { white-space:pre-wrap; word-break:break-all; font-size:11px; background:#05080f; border:1px solid #1d2c47; border-radius:6px; padding:8px; max-height:40vh; overflow:auto; }
      .fm-dialog table { font-size:11px; border-collapse:collapse; width:100%; }
      .fm-dialog td { border-bottom:1px solid rgba(255,255,255,0.06); padding:3px 6px; vertical-align:top; word-break:break-all; }
      .fm-dialog td:first-child { color:#8fa6c4; white-space:nowrap; }
      .fm-compose { position:fixed; right:24px; bottom:78px; width:min(620px, 92vw); max-height:78vh; z-index:66; display:flex; flex-direction:column;
        background:rgba(8,12,22,0.97); border:1px solid rgba(0,212,255,0.35); border-radius:12px; box-shadow:0 20px 60px rgba(0,0,0,0.6), 0 0 30px rgba(0,212,255,0.12); }
      .fm-compose-h { display:flex; justify-content:space-between; align-items:center; padding:10px 12px; border-bottom:1px solid rgba(255,255,255,0.08); font-family:Orbitron,Inter,sans-serif; font-size:11px; letter-spacing:.1em; color:#00d4ff; }
      .fm-field { display:flex; gap:8px; align-items:center; padding:4px 12px; border-bottom:1px solid rgba(255,255,255,0.05); font-size:12px; position:relative; }
      .fm-field label { color:#7f93ad; width:52px; }
      .fm-field input, .fm-field select { flex:1; background:transparent; border:0; color:#e6f0ff; padding:6px 0; font-size:12.5px; outline:none; }
      .fm-editor { min-height:180px; max-height:40vh; overflow:auto; padding:10px 12px; font-size:13px; line-height:1.55; outline:none; color:#e6f0ff; }
      .fm-editor blockquote { border-left:2px solid #3d5a80; margin:6px 0; padding-left:10px; color:#9fb0c8; }
      .fm-editor .fm-sig { color:#9fb0c8; }
      .fm-tools { display:flex; gap:4px; padding:6px 12px; border-top:1px solid rgba(255,255,255,0.06); align-items:center; flex-wrap:wrap; }
      .fm-tool { background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.12); color:#cfe3ff; border-radius:5px; min-width:28px; height:26px; cursor:pointer; font-size:12px; }
      .fm-tool:hover { border-color:#00d4ff; }
      .fm-sugg { position:absolute; left:64px; top:100%; z-index:5; background:#0b1220; border:1px solid #24406a; border-radius:6px; min-width:260px; max-height:200px; overflow:auto; }
      .fm-sugg div { padding:6px 10px; font-size:12px; cursor:pointer; } .fm-sugg div:hover, .fm-sugg div.on { background:rgba(0,212,255,0.12); }
      .fm-toast { position:fixed; left:50%; bottom:84px; transform:translateX(-50%); z-index:70; background:rgba(8,12,22,0.96); border:1px solid #2e5a8f;
        color:#e6f0ff; padding:8px 12px; border-radius:8px; font-size:12px; display:flex; gap:10px; align-items:center; box-shadow:0 8px 30px rgba(0,0,0,.5); max-width:80vw; }
      .fm-toast.err { border-color:rgba(239,68,68,0.6); color:#ffd0d0; }
      .fm-help { position:fixed; inset:0; z-index:80; background:rgba(0,0,0,0.55); display:flex; align-items:center; justify-content:center; }
      .fm-help > div { background:#0a1020; border:1px solid rgba(0,212,255,0.4); border-radius:12px; padding:18px 22px; font-size:12px; color:#cfe3ff; columns:2; column-gap:30px; max-width:680px; }
      .fm-help kbd { font-family:'JetBrains Mono',monospace; background:#16213a; border:1px solid #2b4470; border-radius:4px; padding:1px 6px; margin-right:6px; color:#fff; }
      .fm-preview { position:fixed; inset:4vh 4vw; z-index:82; background:#05080f; border:1px solid rgba(0,212,255,0.4); border-radius:12px; display:flex; flex-direction:column; }
      .fm-preview iframe, .fm-preview img { flex:1; border:0; object-fit:contain; max-width:100%; min-height:0; background:#111; }
    `;
    document.head.appendChild(st);
  }

  // ── links in a message ──────────────────────────────────────────────────
  // Only web and mail links do anything. A web link opens in a new browser
  // tab from Friday's own page, with no opener and no referrer, so the page
  // it leads to cannot reach back into Friday. (A tab spawned from inside
  // the sandboxed message frame inherits the frame's restrictions and never
  // loads, which is why the frame itself opens nothing.) A mail link starts
  // a message in Friday's composer, which sends nothing without approval.
  const openLink = (href, onMailto) => {
    const u = String(href || '').trim();
    if (/^https?:\/\//i.test(u)) { window.open(u, '_blank', 'noopener,noreferrer'); return true; }
    if (/^mailto:/i.test(u) && onMailto) { onMailto(u); return true; }
    return false;
  };
  const URL_RE = /\bhttps?:\/\/[^\s<>"')\]]+[^\s<>"')\].,;:!?]/gi;
  function Linkified({ text, onMailto }) {
    const out = [];
    let last = 0, m, k = 0;
    const src = String(text || '');
    URL_RE.lastIndex = 0;
    while ((m = URL_RE.exec(src))) {
      if (m.index > last) out.push(src.slice(last, m.index));
      const url = m[0];
      out.push(h('a', { key: k++, href: url, rel: 'noopener noreferrer', style: { color: '#7dd3fc' },
        onClick: e => { e.preventDefault(); openLink(url, onMailto); } }, url));
      last = m.index + url.length;
    }
    out.push(src.slice(last));
    return h('div', { className: 'fm-text' }, out);
  }

  // ── matching Friday's theme ─────────────────────────────────────────────
  // Mail is designed for a white page. "Match Friday's theme" re-colours it
  // for a dark one: light, greyish backgrounds turn dark (keeping their hue),
  // text that would then be unreadable turns light, and hard light borders
  // soften. Saturated colours (brand bars, buttons) and every image are left
  // as they are, and text on them keeps its own colour. It runs on the
  // message's own document after it loads; nothing in the message runs.
  const PAGE = { r: 15, g: 21, b: 34 };                          // #0f1522
  const rgbOf = s => { const m = /rgba?\(([^)]+)\)/.exec(s || ''); if (!m) return null; const p = m[1].split(/[,\s/]+/).filter(Boolean).map(parseFloat); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
  const lumOf = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
  const contrast = (a, b) => { const x = lumOf(a), y = lumOf(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const toHsl = c => {
    const r = c.r / 255, g = c.g / 255, b = c.b / 255, mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 2;
    if (mx === mn) return [0, 0, l];
    const d = mx - mn, s = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
    const hh = mx === r ? (g - b) / d + (g < b ? 6 : 0) : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
    return [hh / 6, s, l];
  };
  const fromHsl = (hh, s, l) => {
    const f = n => { const k = (n + hh * 12) % 12, a = s * Math.min(l, 1 - l); return Math.round(255 * (l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1)))); };
    return { r: f(0), g: f(8), b: f(4), a: 1 };
  };
  const css = c => 'rgb(' + c.r + ',' + c.g + ',' + c.b + ')';
  function adaptToDark(d) {
    const win = d.defaultView;
    if (!win || !d.body) return 0;
    let changed = 0;
    const bgOf = new Map();
    const set = (el, prop, val) => { el.style.setProperty(prop, val, 'important'); changed++; };
    d.documentElement.style.setProperty('background', css(PAGE), 'important');
    set(d.body, 'background-color', css(PAGE));
    bgOf.set(d.body, PAGE);
    const all = [d.body].concat(Array.from(d.body.querySelectorAll('*')));
    all.forEach(el => {
      if (el === d.body || /^(IMG|PICTURE|SVG|VIDEO|CANVAS)$/i.test(el.tagName)) return;
      const cs = win.getComputedStyle(el);
      const bg = rgbOf(cs.backgroundColor);
      if (bg && bg.a > 0.05) {
        const [hh, s, l] = toHsl(bg);
        if (l > 0.8 || (l > 0.55 && s < 0.2)) {                 // white, grey, pastel: a page, not a brand
          const dark = fromHsl(hh, Math.min(s, 0.3), 0.06 + (1 - l) * 0.45);
          set(el, 'background-color', css(dark));
          bgOf.set(el, dark);
        } else bgOf.set(el, bg);
      }
      ['Top', 'Right', 'Bottom', 'Left'].forEach(side => {
        if (parseFloat(cs['border' + side + 'Width']) > 0) {
          const bc = rgbOf(cs['border' + side + 'Color']);
          if (bc && bc.a > 0.05) { const [, s, l] = toHsl(bc); if (l > 0.75 && s < 0.25) set(el, 'border-' + side.toLowerCase() + '-color', 'rgba(255,255,255,0.14)'); }
        }
      });
    });
    const behind = el => { for (let n = el; n; n = n.parentElement) if (bgOf.has(n)) return bgOf.get(n); return PAGE; };
    all.forEach(el => {
      if (/^(IMG|PICTURE|SVG|VIDEO|CANVAS|BR|HR)$/i.test(el.tagName)) return;
      const fg = rgbOf(win.getComputedStyle(el).color), bg = behind(el);
      if (!fg || contrast(fg, bg) >= 4.5 || lumOf(bg) > 0.25) return;  // readable, or on a light brand colour of its own
      const [hh, s, l] = toHsl(fg);
      let light = fromHsl(hh, s, Math.max(1 - l, 0.78));
      if (contrast(light, bg) < 4.5) light = fromHsl(hh, Math.min(s, 0.4), 0.9);
      set(el, 'color', css(light));
    });
    return changed;
  }
  window.__fridayAdaptMailToDark = adaptToDark;              // for tests

  // ── a message body in a sandboxed frame ─────────────────────────────────
  function MailBody({ html, text, showImages, onMailto, adapt }) {
    const ref = useRef(null);
    const [height, setHeight] = useState(120);
    if (!html) return h(Linkified, { text, onMailto });
    const origin = window.location.origin;
    const csp = "default-src 'none'; style-src 'unsafe-inline'; font-src data:; img-src data: " + origin + (showImages ? ' https: http:' : '');
    const base = adapt ? 'html,body{background:#0f1522;color:#dbe6f5}a{color:#7dd3fc}' : 'body{color:#1b1f24}';
    const doc = '<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="' + csp + '">' +
      '<base target="_blank"><style>body{margin:12px;font:13px/1.5 -apple-system,Segoe UI,Arial,sans-serif;word-wrap:break-word}' + base + 'img{max-width:100%;height:auto}a{cursor:pointer}</style></head><body>' + html + '</body></html>';
    const onLoad = () => {
      let d;
      try { d = ref.current.contentDocument; } catch (_) { return; }
      if (!d) return;
      if (adapt) { try { adaptToDark(d); } catch (_) { /* a message that cannot be adapted is shown as sent */ } }
      setHeight(Math.min(4000, Math.max(80, d.documentElement.scrollHeight + 4)));
      // Friday's page listens inside the frame (the frame is same-origin
      // but runs no scripts of its own) and opens the link itself.
      const onClick = e => {
        const a = e.target && e.target.closest && e.target.closest('a[href]');
        if (!a) return;
        e.preventDefault();
        openLink(a.getAttribute('href'), onMailto);
      };
      d.addEventListener('click', onClick, true);
      d.addEventListener('auxclick', e => { if (e.button === 1) onClick(e); }, true);
      d.querySelectorAll('a[href]').forEach(a => { if (!a.title) a.title = a.getAttribute('href'); });
    };
    // allow-same-origin WITHOUT allow-scripts: nothing in the mail can run,
    // and Friday can size the frame, adapt its colours and handle its links.
    // No allow-popups: the frame opens nothing itself.
    return h('iframe', { key: adapt ? 'a' : 'o', ref, className: 'fm-body' + (adapt ? ' adapted' : ''), title: 'Message', sandbox: 'allow-same-origin', srcDoc: doc, onLoad, style: { height }, 'data-adapted': adapt ? '1' : '0' });
  }

  // ── menus and dialogs ───────────────────────────────────────────────────
  // One small menu for the right-click menu, labels, lanes and snooze.
  // items: {label, ico, hint, onClick, off, checked, title} | {head} | {sep} | {input}
  function Menu({ menu, onClose }) {
    const ref = useRef(null);
    const [pos, setPos] = useState({ left: menu.x, top: menu.y });
    useEffect(() => {
      const el = ref.current; if (!el) return;
      const r = el.getBoundingClientRect(), W = window.innerWidth, H = window.innerHeight;
      setPos({ left: Math.max(6, Math.min(menu.x, W - r.width - 8)), top: Math.max(6, Math.min(menu.y, H - r.height - 8)) });
      const off = e => { if (!el.contains(e.target)) onClose(); };
      const key = e => { if (e.key === 'Escape') { e.stopPropagation(); onClose(); } };
      setTimeout(() => document.addEventListener('mousedown', off), 0);
      document.addEventListener('keydown', key, true);
      return () => { document.removeEventListener('mousedown', off); document.removeEventListener('keydown', key, true); };
    }, [menu]);
    return h('div', { ref, className: 'fm-menu', role: 'menu', 'aria-label': menu.title || 'Menu', style: pos, onContextMenu: e => e.preventDefault() },
      menu.items.map((it, k) => it.sep ? h('div', { key: k, className: 'sp' })
        : it.head ? h('div', { key: k, className: 'hd' }, it.head)
          : it.input ? h('div', { key: k, style: { padding: '4px 6px' } }, it.input)
            : h('div', { key: k, role: 'menuitem', className: 'it' + (it.off ? ' off' : ''), title: it.title || '', 'aria-disabled': !!it.off,
              onClick: () => { if (it.off) return; const keep = it.onClick && it.onClick(); if (keep !== true) onClose(); } },
            h('span', { className: 'ic' }, it.checked === true ? '☑' : it.checked === false ? '☐' : (it.ico || '')), it.label, it.hint && h('span', { className: 'k' }, it.hint))));
  }

  function Dialog({ title, children, onClose }) {
    useEffect(() => {
      const key = e => { if (e.key === 'Escape') { e.stopPropagation(); onClose(); } };
      document.addEventListener('keydown', key, true);
      return () => document.removeEventListener('keydown', key, true);
    }, []);
    return h('div', { className: 'fm-dialog', onMouseDown: e => { if (e.target === e.currentTarget) onClose(); } },
      h('div', { role: 'dialog', 'aria-label': title }, h('h4', null, title), children));
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

  // A Gmail signature is the owner's own HTML, but it goes into Friday's page,
  // so anything active is removed first. Its images (a logo) stay.
  const cleanSig = html => {
    const d = new DOMParser().parseFromString(html || '', 'text/html');
    d.querySelectorAll('script,style,link,meta,base,iframe,frame,object,embed,form,input,button,svg').forEach(n => n.remove());
    d.querySelectorAll('*').forEach(n => [...n.attributes].forEach(at => {
      const k = at.name.toLowerCase(), v = String(at.value || '');
      if (k.startsWith('on') || (k === 'style' && /url\s*\(/i.test(v)) || ((k === 'href' || k === 'src') && !/^(https?:|mailto:|data:image\/)/i.test(v.trim()))) n.removeAttribute(at.name);
    }));
    return d.body.innerHTML;
  };

  function Composer({ init, accounts, canSend, onClose, say }) {
    const [from, setFrom] = useState(init.account_id || (canSend[0] && canSend[0].id) || (accounts[0] && accounts[0].id) || '');
    const [to, setTo] = useState(init.to || '');
    const [cc, setCc] = useState(init.cc || '');
    const [bcc, setBcc] = useState('');
    const [showCc, setShowCc] = useState(!!init.cc);
    const [subject, setSubject] = useState(init.subject || '');
    const [atts, setAtts] = useState([]);
    const [fwd, setFwd] = useState((init.forward || []).map(a => Object.assign({ keep: true }, a)));
    const [busy, setBusy] = useState(false);
    const [state, setState] = useState(null);     // {approval_id, status, message, seconds}
    const [sendAt, setSendAt] = useState('');       // datetime-local, '' = as soon as approved
    const [drafting, setDrafting] = useState(false);
    const ed = useRef(null), fileRef = useRef(null);
    useEffect(() => {
      if (ed.current && init.html != null) ed.current.innerHTML = init.html;
      // replies and forwards start typing above the quoted original
      if (ed.current && init.to && init.mode !== 'new') {
        ed.current.focus();
        try { const r = document.createRange(); r.setStart(ed.current, 0); r.collapse(true); const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r); } catch (_) {}
      }
    }, []);
    // The sending account's own Gmail signature, where Gmail would put it:
    // under what is typed and above anything quoted.
    useEffect(() => {
      if (!from || init.noSignature) return;
      let live = true;
      api('/api/mail/signature?account=' + encodeURIComponent(from)).then(r => r.json()).then(d => {
        const el = ed.current;
        if (!live || !el) return;
        let slot = el.querySelector('.fm-sig');
        const sig = d && d.status === 'ok' && d.signature ? cleanSig(d.signature) : '';
        if (!sig) { if (slot) slot.innerHTML = ''; return; }
        if (!slot) {
          slot = document.createElement('div'); slot.className = 'fm-sig';
          const q = el.querySelector('.fm-quote');
          if (q) el.insertBefore(slot, q); else { el.appendChild(document.createElement('br')); el.appendChild(slot); }
        }
        slot.innerHTML = '-- <br>' + sig;
      }).catch(() => {});
      return () => { live = false; };
    }, [from]);
    const cmd = (c, v) => { document.execCommand(c, false, v); ed.current && ed.current.focus(); };
    const upload = files => {
      Array.from(files || []).forEach(f => {
        const fd = new FormData(); fd.append('file', f);
        api('/api/mail/attachment', { method: 'POST', body: fd }).then(r => r.json()).then(d => {
          if (d.status === 'ok') setAtts(a => a.concat([d])); else say(d.message || 'Could not attach ' + f.name);
        }).catch(() => say('Could not attach ' + f.name));
      });
    };
    const fromAcct = accounts.find(a => a.id === from);
    const canDraft = !!(fromAcct && fromAcct.mail && fromAcct.mail.modify);
    const fields = () => {
      const html = ed.current ? ed.current.innerHTML : '';
      const text = ed.current ? ed.current.innerText : '';
      return { to: splitAddrs(to).map(addrOf), cc: splitAddrs(cc).map(addrOf), bcc: splitAddrs(bcc).map(addrOf), subject,
        body: text, html, account_id: from, thread_id: init.thread_id || null, in_reply_to: init.in_reply_to || null,
        references: init.references || null, attachments: atts, forward_attachments: fwd.filter(a => a.keep) };
    };
    // Into his own Gmail Drafts. Sends nothing; needs the mailbox permission.
    const saveDraft = () => {
      setDrafting(true);
      post('/api/mail/draft', fields()).then(({ ok, j }) => {
        setDrafting(false);
        say(ok ? 'Saved to Gmail Drafts (' + (fromAcct.email || fromAcct.label || 'account') + '). Nothing was sent.' : (j.message || 'Gmail did not save the draft.'));
      }).catch(() => { setDrafting(false); say('Could not reach Friday.'); });
    };
    // Undo send: take an approved message back while it waits.
    const undoSend = () => {
      if (!state || !state.approval_id) return;
      post('/api/mail/held/' + encodeURIComponent(state.approval_id) + '/cancel', {}).then(({ ok, j }) => {
        setState(s => Object.assign({}, s, ok ? { status: 'cancelled', message: 'Taken back. Nothing was sent.' } : { message: j.message || 'Too late to take it back.' }));
      }).catch(() => say('Could not reach Friday.'));
    };
    const request = () => {
      const bad = splitAddrs(to).concat(splitAddrs(cc), splitAddrs(bcc)).filter(a => !/^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$/.test(addrOf(a)));
      if (!splitAddrs(to).length) { say('Add at least one recipient.'); return; }
      if (bad.length) { say('Not an email address: ' + bad.join(', ')); return; }
      setBusy(true);
      post('/api/mail/request', Object.assign(fields(), { requested_by: 'ui:messages',
        send_at: sendAt ? new Date(sendAt).toISOString() : null })).then(({ ok, j }) => {
        setBusy(false);
        if (!ok || !j.approval_id) { setState({ status: 'refused', message: j.message || 'Friday could not queue this message.' }); return; }
        setState({ approval_id: j.approval_id, status: j.approval_status, message: 'Waiting for your approval. Nothing has been sent yet.' });
      }).catch(() => { setBusy(false); setState({ status: 'refused', message: 'Could not reach Friday.' }); });
    };
    // follow the card: approved → sent (or refused), denied → not sent
    useEffect(() => {
      if (!state || !state.approval_id || /sent|denied|failed|cancelled/.test(state.status)) return;
      const iv = setInterval(() => {
        api('/api/approvals/' + state.approval_id).then(r => r.json()).then(d => {
          const a = d.approval || d;
          if (!a) return;
          if (a.status === 'approved' && a.consumed) {
            const det = a.used_detail || {};
            setState(s => Object.assign({}, s, { status: det.message_id ? 'sent' : 'failed', message: det.message_id ? 'Sent.' : 'Not sent. See Settings › Outbox.' }));
          } else if (a.status === 'approved') {
            // approved and waiting: the undo window, or its scheduled time
            api('/api/mail/held').then(r => r.json()).then(hd => {
              const row = (hd.held || []).find(x => x.approval_id === state.approval_id);
              if (!row) return;
              const when = row.scheduled ? 'Scheduled for ' + new Date(row.due * 1000).toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' }) : 'Sending in ' + row.seconds_left + ' s';
              setState(s => Object.assign({}, s, { status: 'held', message: when + ' — you can still take it back.' }));
            }).catch(() => {});
          } else if (a.status === 'denied' || a.status === 'expired' || a.status === 'blocked') {
            setState(s => Object.assign({}, s, { status: 'denied', message: 'Not approved. Nothing was sent.' }));
          }
        }).catch(() => {});
      }, state.status === 'held' ? 1000 : 2500);
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
        h('button', { "aria-label": 'Bulleted list',  className: 'fm-tool', title: 'Bulleted list', onMouseDown: e => { e.preventDefault(); cmd('insertUnorderedList'); } }, '•≡'),
        h('button', { "aria-label": 'Numbered list',  className: 'fm-tool', title: 'Numbered list', onMouseDown: e => { e.preventDefault(); cmd('insertOrderedList'); } }, '1≡'),
        h('button', { "aria-label": 'Link',  className: 'fm-tool', title: 'Link', onMouseDown: e => { e.preventDefault(); const u = window.prompt('Link address'); if (u && /^(https?:|mailto:)/i.test(u)) cmd('createLink', u); } }, '🔗'),
        h('button', { className: 'fm-tool', title: 'Remove formatting', onMouseDown: e => { e.preventDefault(); cmd('removeFormat'); } }, 'Tx'),
        h('button', { "aria-label": 'Attach files',  className: 'fm-tool', title: 'Attach files', onClick: () => fileRef.current && fileRef.current.click() }, '📎'),
        h('input', { ref: fileRef, type: 'file', multiple: true, style: { display: 'none' }, onChange: e => { upload(e.target.files); e.target.value = ''; } }),
        h('span', { style: { flex: 1 } }),
        state && h('span', { style: { fontSize: 11, color: state.status === 'sent' ? '#7df0b0' : state.status === 'refused' || state.status === 'denied' || state.status === 'failed' ? '#ffb4b4' : '#ffd699', marginRight: 6 } }, state.message),
        state && state.status === 'held' && h('button', { className: 'btn fm-btn', onClick: undoSend, style: { borderColor: '#ffb86b', color: '#ffb86b' } }, 'Undo send'),
        state && state.approval_id && !/sent|denied|failed|cancelled|held/.test(state.status) && h('button', { className: 'btn fm-btn', onClick: () => window.fridayRunActions && window.fridayRunActions([{ type: 'navigate', workspace: 'system', tab: 'approvals' }]) }, 'Review in Approvals'),
        (!state || state.status === 'refused') && canDraft && h('button', { className: 'btn fm-btn', disabled: drafting, onClick: saveDraft, title: 'Save into your Gmail Drafts. Sends nothing.' }, drafting ? 'Saving…' : 'Save draft'),
        (!state || state.status === 'refused') && h('span', { style: { fontSize: 11, color: '#7f93ad', marginLeft: 4 } }, '⏰ Later'),
        (!state || state.status === 'refused') && h('input', { type: 'datetime-local', value: sendAt, onChange: e => setSendAt(e.target.value), title: 'Send later (optional). The time is part of what you approve.',
          'aria-label': 'Send later', style: { background: '#0b1220', color: sendAt ? '#e6f0ff' : '#6f86a6', border: '1px solid #24406a', borderRadius: 6, fontSize: 11, padding: '4px 6px', minHeight: 28, colorScheme: 'dark' } }),
        (!state || state.status === 'refused') && h('button', { className: 'btn fm-btn', disabled: busy, onClick: request, title: 'Files an approval card. Nothing is sent until you approve it.', style: { borderColor: '#00d4ff', color: '#00d4ff' } },
          busy ? 'Asking…' : sendAt ? 'Send later — asks for approval' : 'Send — asks for approval')));
  }

  // Printing opens a plain copy of the conversation in a new window of
  // Friday's own, with no scripts, and asks the browser to print it.
  function printThread(subject, messages, showImages) {
    const w = window.open('', '_blank');
    if (!w) { alert('The browser blocked the print window. Allow pop-ups for Friday and try again.'); return; }
    const clean = html => {
      const d = new DOMParser().parseFromString(html || '', 'text/html');
      d.querySelectorAll('script,iframe,frame,object,embed,form,base,link,meta').forEach(n => n.remove());
      d.querySelectorAll('*').forEach(n => [...n.attributes].forEach(at => { if (/^on/i.test(at.name) || /^\s*javascript:/i.test(at.value)) n.removeAttribute(at.name); }));
      if (!showImages) d.querySelectorAll('img').forEach(img => { if (/^https?:/i.test(img.getAttribute('src') || '')) img.removeAttribute('src'); });
      return d.body.innerHTML;
    };
    const parts = messages.map(m => '<section><div class="h"><b>' + esc(m.sender) + '</b><br>' + esc(m.date || '') + (m.to ? '<br>To: ' + esc(m.to) : '') + (m.cc ? '<br>Cc: ' + esc(m.cc) : '') + '</div>' +
      (m.html ? clean(m.html) : '<pre>' + esc(m.body) + '</pre>') + '</section>');
    w.document.open();
    w.document.write('<!doctype html><html><head><meta charset="utf-8"><title>' + esc(subject) + '</title><meta http-equiv="Content-Security-Policy" content="script-src \'none\'">' +
      '<style>body{font:13px/1.5 Arial,sans-serif;margin:24px;color:#111}h1{font-size:18px}section{border-top:1px solid #ccc;padding:12px 0}.h{color:#444;font-size:12px;margin-bottom:8px}pre{white-space:pre-wrap;font:inherit}img{max-width:100%}</style></head><body><h1>' +
      esc(subject) + '</h1>' + parts.join('') + '</body></html>');
    w.document.close();
    try { w.opener = null; } catch (_) {}
    setTimeout(() => { try { w.focus(); w.print(); } catch (_) {} }, 300);
  }

  // Settings › Connectors, where an account is reconnected with sending.
  const openReconnect = () => {
    window.__fridaySettingsTab = 'connectors';
    try { window.dispatchEvent(new CustomEvent('friday:settings-tab', { detail: { tab: 'connectors' } })); } catch (_) {}
    if (window.__FRIDAY_STANDALONE__ || !window.fridayRunActions) window.open('/?workspace=settings', '_blank');
    else window.fridayRunActions([{ type: 'navigate', workspace: 'settings', tab: 'connectors' }]);
  };

  // ── the panel ───────────────────────────────────────────────────────────
  function FridayMailPanel() {
    const [acct, setAcct] = useState('all');
    const [lane, setLane] = useState('all');
    const [folder, setFolder] = useState('');
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
    const [original, setOriginal] = useState(() => store.get('fm_show_original', {}));   // sender -> true
    const [compose, setCompose] = useState(null);
    const [toast, setToast] = useState(null);
    const [labelsBy, setLabelsBy] = useState({});      // account id -> its own Gmail labels
    const [help, setHelp] = useState(false);
    const [preview, setPreview] = useState(null);
    const [accounts, setAccounts] = useState([]);
    const [canSend, setCanSend] = useState([]);
    const [aiDraft, setAiDraft] = useState(null);
    const [menu, setMenu] = useState(null);
    const [dialog, setDialog] = useState(null);
    const [busyIds, setBusyIds] = useState(() => new Set());
    const [sideOpen, setSideOpen] = useState(() => store.get('fm_side', null));
    // wide: folders | list | reading pane; mid: the folders become a drawer;
    // narrow (a small window, a phone): one pane at a time
    const [layout, setLayout] = useState('wide');
    const undoStack = useRef([]);
    const boxRef = useRef(null), searchRef = useRef(null), listRef = useRef(null), starRef = useRef(0);

    const say = (text, undo, err) => { setToast({ text, undo, err, t: Date.now() }); };
    useEffect(() => { if (!toast) return; const t = setTimeout(() => setToast(x => x === toast ? null : x), toast.undo ? 8000 : 4500); return () => clearTimeout(t); }, [toast]);
    // the folders go down the side when there is room for them
    useEffect(() => {
      const el = boxRef.current; if (!el || !window.ResizeObserver) return;
      const ro = new ResizeObserver(() => { const w = el.offsetWidth; setLayout(w >= 1100 ? 'wide' : w >= 720 ? 'mid' : 'narrow'); });
      ro.observe(el);
      return () => ro.disconnect();
    }, []);
    // wide: shown unless hidden (remembered); otherwise a drawer, opened on request
    const showSide = layout === 'wide' ? sideOpen !== false : sideOpen === true;
    const pickFolder = f => { setFolder(f); setOpen(null); if (layout !== 'wide') setSideOpen(null); };

    const seq = useRef(0);
    const load = useCallback((q, f) => {
      const n = ++seq.current;
      setLoading(true);
      // results of a different search or folder are never shown under this one's name
      setData(d => (d && (d.query || '') === (q || '') && (d.folder || '') === (f || '')) ? d : null);
      const url = '/api/messages?limit=80' + (q ? '&q=' + encodeURIComponent(q) : '') + (f ? '&folder=' + encodeURIComponent(f) : '');
      const done = d => { if (n !== seq.current) return; if (d) { d.query = q || ''; d.folder = f || ''; } setData(d); setLoading(false); };
      api(url).then(r => r.json()).then(done).catch(e => done({ status: 'error', search_failed: true, error: "Couldn't reach Friday: " + e }));
      if (!q && !f) api('/api/messages/stats').then(r => r.json()).then(d => { setStats(d); if (d.actionable != null) window._fridayMsgActionable = d.actionable; }).catch(() => {});
    }, []);
    useEffect(() => { load(query, folder); setSel(new Set()); setFocus(0); }, [query, folder]);
    useEffect(() => { const iv = setInterval(() => { if (!query && !folder && document.visibilityState !== 'hidden') load('', ''); }, 60000); return () => clearInterval(iv); }, [query, folder]);
    useEffect(() => {
      api('/api/google/accounts').then(r => r.json()).then(d => setAccounts((d.accounts || []).filter(hasGmail))).catch(() => {});
      api('/api/mail/can-send').then(r => r.json()).then(d => setCanSend(d.accounts || [])).catch(() => {});
    }, []);
    // every account's own labels, for the side, the rows and the label menu
    useEffect(() => { accounts.forEach(a => loadLabels(a.id)); }, [accounts.length]);

    // deep link {workspace:'messages', lane, thread_id, folder}
    const pending = useRef(null);
    useEffect(() => {
      const apply = t => { if (!t || t.workspace !== 'messages') return; if (t.lane) setLane(t.lane); if (t.folder != null) setFolder(t.folder); if (t.thread_id) pending.current = { id: t.thread_id, reply: !!t.reply }; };
      apply(window.__fridayNavTarget);
      const f = e => apply(e.detail || {});
      window.addEventListener('friday-nav', f);
      return () => window.removeEventListener('friday-nav', f);
    }, []);

    const all = (data && data.messages) || [];
    const shown = all.filter(m => (acct === 'all' || m.account_id === acct) && (lane === 'all' || m.lane === lane) && (!unreadOnly || m.unread));
    const replyWhenOpen = useRef(null);
    useEffect(() => {
      if (!pending.current || !all.length) return;
      const want = pending.current;
      const m = all.find(x => x.thread_id === want.id || x.id === want.id);
      if (m) { pending.current = null; if (want.reply) replyWhenOpen.current = m.id; openThread(m); }
    }, [data]);
    // "Reply" from 3D triage: open the conversation, then start the reply
    useEffect(() => {
      if (open && replyWhenOpen.current === open.card.id && open.res && open.res.status === 'ok') { replyWhenOpen.current = null; startReply('reply'); }
    }, [open]);
    useEffect(() => { if (focus >= shown.length) setFocus(Math.max(0, shown.length - 1)); }, [shown.length]);

    const patch = (ids, fn) => setData(d => d && Object.assign({}, d, { messages: (d.messages || []).map(m => ids.includes(m.id) ? fn(m) : m) }));
    const drop = ids => setData(d => d && Object.assign({}, d, { messages: (d.messages || []).filter(m => !ids.includes(m.id)) }));

    const acctOf = aid => accounts.find(x => x.id === aid);
    const canModify = aid => { const a = acctOf(aid); return !!(a && a.mail && a.mail.modify); };
    const readOnlyAccts = accounts.filter(a => !(a.mail && a.mail.modify));
    const loadLabels = aid => {
      if (!aid || labelsBy[aid]) return Promise.resolve(labelsBy[aid] || []);
      return api('/api/mail/labels?account=' + encodeURIComponent(aid)).then(r => r.json()).then(d => {
        const list = d.status === 'ok' ? d.labels || [] : [];
        setLabelsBy(m => Object.assign({}, m, { [aid]: list }));
        return list;
      }).catch(() => []);
    };
    const labelName = (aid, id) => { const l = (labelsBy[aid] || []).find(x => x.id === id); return l ? l.name : null; };
    const allLabelNames = () => Array.from(new Set([].concat.apply([], Object.keys(labelsBy).filter(a => acct === 'all' || a === acct).map(a => (labelsBy[a] || []).map(l => l.name))))).sort((a, b) => a.localeCompare(b));

    // ── acting on conversations ──
    // The row stays where it is (dimmed) until Friday answers; it changes
    // only after the server, and Gmail where it applies, confirm.
    const NOUN = { archive: 'Archived', trash: 'Moved to Trash', untrash: 'Restored from Trash', spam: 'Reported as spam', notspam: 'Marked not spam',
      snooze: 'Snoozed', flag: 'Starred', unflag: 'Unstarred', read: 'Marked read', unread: 'Marked unread', important: 'Marked important',
      unimportant: 'Marked not important', mute: 'Muted', unmute: 'Unmuted', unarchive: 'Moved to Inbox' };
    const AWAY = ['archive', 'trash', 'untrash', 'spam', 'notspam', 'snooze', 'mute'];
    const act = (cards, action, opts) => {
      cards = cards.filter(Boolean);
      opts = opts || {};
      if (!cards.length) return Promise.resolve();
      const asked = cards.map(c => c.id);
      const prev = cards.map(c => Object.assign({}, c));
      // each card's account and conversation, so Gmail itself changes where allowed
      const gmail = cards.map(c => ({ id: c.id, account_id: c.account_id, thread_id: c.thread_id || c.gmail_id }));
      setBusyIds(s => new Set([].concat(Array.from(s), asked)));
      const body = { ids: asked, action, gmail, requested_by: 'ui:messages' };
      if (opts.until) body.until = opts.until;
      return post('/api/messages/action', body).then(({ ok, j }) => {
        setBusyIds(s => { const n = new Set(s); asked.forEach(i => n.delete(i)); return n; });
        const refusedMap = (j && j.not_changed) || {};
        if (!ok || j.status === 'error') {
          const why = j.message || 'That did not work.';
          say(why, null, true);
          if (/Reconnect/.test(why)) setDialog({ kind: 'reconnect', why });
          return;
        }
        const ids = j.ids || asked;                    // conversations Gmail refused are left as they were
        const st = Object.values(j.gmail_status || {});
        const where = ['snooze', 'mute', 'unmute'].includes(action) ? ' (in Friday only)'
          : ['trash', 'untrash', 'spam', 'notspam', 'important', 'unimportant'].includes(action) ? ' in Gmail'
            : st.includes('synced') && !st.includes('not_permitted') ? ' (also in Gmail)'
              : st.includes('synced') ? ' (in Gmail where allowed; the rest in Friday only)'
                : ' (in Friday only' + (st.includes('not_permitted') ? ' — Reconnect with sending to change Gmail too' : '') + ')';
        const refused = Object.keys(refusedMap).length;
        const leaves = AWAY.includes(action) || (folder === 'starred' && action === 'unflag') || (folder === 'important' && action === 'unimportant');
        if (leaves) { drop(ids); if (open && ids.includes(open.card.id)) setOpen(null); }
        else patch(ids, m => Object.assign({}, m, { flag: { flagged: true }, unflag: { flagged: false }, read: { unread: false }, unread: { unread: true },
          important: { important: true }, unimportant: { important: false } }[action] || {}));
        if (open && ids.includes(open.card.id) && !leaves) setOpen(o => o && Object.assign({}, o, { card: Object.assign({}, o.card, { flag: { flagged: true }, unflag: { flagged: false }, read: { unread: false }, unread: { unread: true }, important: { important: true }, unimportant: { important: false } }[action] || {}) }));
        const undo = { before: j.before, gmail: j.gmail_changes || {}, cards: prev.filter(c => ids.includes(c.id)), label: action, away: leaves };
        undoStack.current.push(undo);
        if (!opts.silent) {
          const msg = (NOUN[action] || action) + (action === 'snooze' && opts.untilText ? ' until ' + opts.untilText : '') + (ids.length > 1 ? ' · ' + ids.length + ' conversations' : '') + where +
            (action === 'trash' ? '. Gmail keeps it in Trash for 30 days.' : '') +
            (refused ? ' · ' + refused + ' not changed: ' + Object.values(refusedMap)[0] : '');
          say(msg, ids.length ? undo : null, refused > 0);
        }
        setSel(new Set());
      }).catch(() => { setBusyIds(s => { const n = new Set(s); asked.forEach(i => n.delete(i)); return n; }); say('Could not reach Friday. Nothing changed.', null, true); });
    };
    const moveLane = (cards, newLane) => {
      cards = cards.filter(Boolean);
      Promise.all(cards.map(c => post('/api/messages/classify', { id: c.id, lane: newLane }).catch(() => ({ ok: false, j: {} })))).then(rs => {
        const before = {};
        const moved = cards.filter((c, k) => rs[k].ok);
        rs.forEach(r => r.ok && Object.assign(before, (r.j && r.j.before) || {}));
        if (!moved.length) { say('Nothing moved: ' + ((rs[0] && rs[0].j && rs[0].j.message) || 'Friday refused the change.'), null, true); return; }
        patch(moved.map(c => c.id), m => Object.assign({}, m, { lane: newLane }));
        if (open && moved.some(c => c.id === open.card.id)) setOpen(o => o && Object.assign({}, o, { card: Object.assign({}, o.card, { lane: newLane }) }));
        const undo = { before, cards: moved.map(c => Object.assign({}, c)), label: 'move' };
        undoStack.current.push(undo);
        say('Moved to ' + laneLabel(newLane) + (moved.length > 1 ? ' · ' + moved.length + ' conversations' : '') + ' (Friday learns from it)' + (moved.length < cards.length ? ' · ' + (cards.length - moved.length) + ' could not be moved' : ''), undo);
        setSel(new Set());
      });
    };
    // Gmail labels, by name: each account has its own, so the same name is
    // looked up per account. Adding a label an account lacks is refused for
    // that account and said so.
    const setLabel = (cards, name, on) => {
      cards = cards.filter(Boolean);
      const byAcct = {};
      cards.forEach(c => { (byAcct[c.account_id] = byAcct[c.account_id] || []).push(c); });
      const accts = Object.keys(byAcct);
      Promise.all(accts.map(aid => loadLabels(aid).then(list => {
        if (!canModify(aid)) return { aid, err: (acctOf(aid) || {}).label + ' has not allowed Friday to change Gmail labels' };
        const lab = list.find(l => l.name === name);
        if (!lab) return { aid, err: (acctOf(aid) || {}).label + ' has no label “' + name + '”' };
        const tids = byAcct[aid].map(c => c.thread_id || c.gmail_id);
        return post('/api/mail/modify', { account_id: aid, thread_ids: tids, add: on ? [lab.id] : [], remove: on ? [] : [lab.id] })
          .then(({ ok, j }) => ok && j.status === 'ok' ? { aid, changed: j.changed, lab, cards: byAcct[aid] } : { aid, err: j.message || 'Gmail refused' });
      }))).then(rs => {
        const done = rs.filter(r => r.changed), bad = rs.filter(r => r.err);
        done.forEach(r => patch(r.cards.map(c => c.id), m => Object.assign({}, m, { labels: on ? Array.from(new Set((m.labels || []).concat([r.lab.id]))) : (m.labels || []).filter(x => x !== r.lab.id) })));
        if (!done.length) { say('Label not changed: ' + (bad[0] ? bad[0].err : 'nothing to change'), null, true); if (bad.some(b => /allowed/.test(b.err))) setDialog({ kind: 'reconnect', why: bad[0].err }); return; }
        const u = { label: 'label', gmailMulti: done.map(r => ({ account_id: r.aid, changed: r.changed })), cards: done.reduce((a, r) => a.concat(r.cards), []), labelPatch: { id: done.map(r => r.lab.id), on } };
        undoStack.current.push(u);
        say((on ? 'Labelled “' : 'Removed “') + name + '”' + (on ? '' : ' label') + ' in Gmail' + (bad.length ? ' · ' + bad.map(b => b.err).join('; ') : ''), u, !!bad.length);
        setSel(new Set());
      });
    };
    const newLabel = cards => {
      const name = (window.prompt('New Gmail label name') || '').trim();
      if (!name) return;
      const aids = Array.from(new Set(cards.map(c => c.account_id)));
      Promise.all(aids.map(aid => post('/api/mail/labels', { account_id: aid, name }).then(({ ok, j }) => {
        if (ok && j.status === 'ok') setLabelsBy(m => Object.assign({}, m, { [aid]: (m[aid] || []).concat([j.label]) }));
        return ok && j.status === 'ok' ? j.label : null;
      }))).then(made => {
        if (!made.some(Boolean)) { say('Gmail did not create the label (the account may be read-only).', null, true); return; }
        setTimeout(() => setLabel(cards, name, true), 50);
      }).catch(() => say('Could not reach Friday.', null, true));
    };
    const undo = u => {
      u = u || undoStack.current.pop();
      if (!u) { say('Nothing to undo.'); return; }
      undoStack.current = undoStack.current.filter(x => x !== u);
      if (u.gmailMulti) {
        Promise.all(u.gmailMulti.map(g => post('/api/mail/modify/undo', g))).then(rs => {
          const ok = rs.every(r => r.ok && r.j.status === 'ok');
          if (ok) load(query, folder);
          setToast(null); say(ok ? 'Undone in Gmail.' : 'Undo failed in Gmail.', null, !ok);
        });
        return;
      }
      post('/api/messages/restore', { states: u.before, gmail_changes: u.gmail || {} }).then(({ ok, j }) => {
        if (!ok || (j.gmail_failed && Object.keys(j.gmail_failed).length)) { say('Undo failed' + (ok ? ' in Gmail for some conversations.' : '.'), null, true); if (!ok) return; }
        setData(d => {
          if (!d) return d;
          const map = new Map(u.cards.map(c => [c.id, c]));
          const kept = (d.messages || []).map(m => map.has(m.id) ? map.get(m.id) : m);
          const missing = u.cards.filter(c => !(d.messages || []).some(m => m.id === c.id));
          return Object.assign({}, d, { messages: kept.concat(missing).sort((a, b) => (Date.parse(b.timestamp) || 0) - (Date.parse(a.timestamp) || 0)) });
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
        .then(r => r.json()).then(res => setOpen(o => o && o.card.id === m.id ? { card: o.card, loading: false, res } : o))
        .catch(e => setOpen(o => o && o.card.id === m.id ? { card: o.card, loading: false, res: { status: 'error', error: "Couldn't reach Friday: " + e } } : o));
    };

    // The quoted original goes into an editor in Friday's own page, outside
    // the sandboxed frame, so nothing in it may load: images (tracking
    // pixels), frames, media and style urls are removed first. A DOMParser
    // document is inert and fetches nothing while it is being cleaned.
    const quotable = html => {
      const d = new DOMParser().parseFromString(html, 'text/html');
      d.querySelectorAll('script,style,link,meta,base,iframe,frame,object,embed,video,audio,source,picture,svg,form,input,button').forEach(n => n.remove());
      d.querySelectorAll('img').forEach(n => n.replaceWith(d.createTextNode(n.getAttribute('alt') ? '[image: ' + n.getAttribute('alt') + ']' : '[image]')));
      d.querySelectorAll('*').forEach(n => [...n.attributes].forEach(at => {
        const k = at.name.toLowerCase();
        if (k.startsWith('on') || k === 'background' || k === 'src' || k === 'srcset' || (k === 'style' && /url\s*\(/i.test(at.value))) n.removeAttribute(at.name);
      }));
      return d.body.innerHTML;
    };
    const bodyOf = msg => msg.html ? quotable(msg.html) : esc(msg.body).replace(/\n/g, '<br>');
    const quoteOf = msg => '<br><div class="fm-sig"></div><div class="fm-quote"><br><div>On ' + esc(msg.date || '') + ', ' + esc(msg.sender) + ' wrote:</div><blockquote>' + bodyOf(msg) + '</blockquote></div>';
    // a mailto: link in a message starts a new message (sent only on approval)
    const mailtoCompose = (href, extra) => {
      let to = '', subject = '', body = '';
      try {
        const u = new URL(href);
        to = decodeURIComponent(u.pathname || '');
        subject = u.searchParams.get('subject') || '';
        body = u.searchParams.get('body') || '';
      } catch (_) { to = String(href).replace(/^mailto:/i, '').split('?')[0]; }
      setCompose(Object.assign({ mode: 'new', account_id: open && open.res && open.res.account_id, to, subject, html: esc(body).replace(/\n/g, '<br>') }, extra || {}));
    };
    const startReply = mode => {
      const msgs = open && open.res && open.res.messages;
      if (!msgs || !msgs.length) return;
      const last = msgs[msgs.length - 1];
      const mine = new Set(accounts.map(a => (a.email || '').toLowerCase()));
      const subj = last.subject || '';
      if (mode === 'forward') {
        setCompose({ mode, account_id: open.res.account_id, to: '', subject: /^fwd?:/i.test(subj) ? subj : 'Fwd: ' + subj,
          html: '<br><div class="fm-sig"></div><div class="fm-quote"><br><div>---------- Forwarded message ---------<br>From: ' + esc(last.sender) + '<br>Date: ' + esc(last.date) + '<br>Subject: ' + esc(subj) + '<br>To: ' + esc(last.to) + '</div><br>' + bodyOf(last) + '</div>',
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

    // ── unsubscribe, original, filter-like-these ──
    const firstMessageId = card => {
      const msgs = open && open.card.id === card.id && open.res && open.res.messages;
      if (msgs && msgs.length) return Promise.resolve(msgs[msgs.length - 1].id);
      return api('/api/messages/' + encodeURIComponent(card.thread_id || card.id) + '?account=' + encodeURIComponent(card.account_id || ''))
        .then(r => r.json()).then(d => { const ms = d.messages || []; return ms.length ? ms[ms.length - 1].id : null; });
    };
    const unsubscribe = card => {
      firstMessageId(card).then(mid => {
        if (!mid) { say('Could not read that message to find its unsubscribe link.', null, true); return; }
        api('/api/mail/unsubscribe?account=' + encodeURIComponent(card.account_id) + '&message=' + encodeURIComponent(mid)).then(r => r.json()).then(opt => {
          if (opt.status !== 'ok') { say(opt.message || 'This message does not say how to unsubscribe.', null, true); return; }
          setDialog({ kind: 'unsub', card, mid, opt });
        });
      }).catch(() => say('Could not reach Friday.', null, true));
    };
    const doUnsubscribe = d => {
      setDialog(null);
      if (d.opt.method === 'link') { openLink(d.opt.url); say('Opened the list’s own unsubscribe page in your browser.'); return; }
      post('/api/mail/unsubscribe', { account_id: d.card.account_id, message_id: d.mid, confirmed: true, requested_by: 'ui:messages' }).then(({ ok, j }) => {
        if (j.status === 'done') { say(j.message || 'Unsubscribed.'); return; }
        if (j.status === 'next' && j.method === 'mailto') {
          mailtoCompose('mailto:' + j.mailto.to + '?subject=' + encodeURIComponent(j.mailto.subject) + '&body=' + encodeURIComponent(j.mailto.body), { account_id: d.card.account_id, noSignature: true });
          say('The unsubscribe message is ready; sending it asks for your approval.');
          return;
        }
        if (j.status === 'next' && j.method === 'link') { openLink(j.url); return; }
        say(j.message || 'Could not unsubscribe.', null, true);
      }).catch(() => say('Could not reach Friday.', null, true));
    };
    const showOriginal = (card, msg) => {
      setDialog({ kind: 'original', loading: true, card, msg });
      api('/api/mail/original?account=' + encodeURIComponent(card.account_id) + '&message=' + encodeURIComponent(msg.id)).then(r => r.json())
        .then(d => setDialog(x => x && x.kind === 'original' ? Object.assign({}, x, { loading: false, d }) : x))
        .catch(e => setDialog(x => x && x.kind === 'original' ? Object.assign({}, x, { loading: false, d: { status: 'error', message: String(e) } }) : x));
    };
    const filterLike = card => {
      const who = card.sender_email || addrOf(card.sender);
      setFolder(''); setQInput('from:' + who); setQuery('from:' + who);
      say('Showing everything from ' + who + '. Saving this as a Gmail filter needs Gmail’s settings permission, which Friday has not asked for.');
    };
    const toggleOriginal = sender => {
      const k = (sender || '').toLowerCase();
      setOriginal(o => { const n = Object.assign({}, o); if (n[k]) delete n[k]; else n[k] = true; store.set('fm_show_original', n); return n; });
    };

    const selected = () => shown.filter(m => sel.has(m.id));
    const targets = () => { const s = selected(); return s.length ? s : open ? [open.card] : shown[focus] ? [shown[focus]] : []; };
    const toggleSel = (m, e) => { setSel(s => { const n = new Set(s); if (n.has(m.id)) n.delete(m.id); else n.add(m.id); return n; }); if (e) e.stopPropagation(); };
    const inTrash = folder === 'trash', inSpam = folder === 'spam';

    // ── menus ──
    const rowPoint = () => {
      const row = listRef.current && listRef.current.querySelector('.fm-row.focus');
      const r = row ? row.getBoundingClientRect() : { left: 200, bottom: 200, width: 0 };
      return { x: r.left + Math.min(260, r.width / 2), y: r.bottom };
    };
    const snoozeMenu = (cards, p) => setMenu(Object.assign({ title: 'Snooze until', items: [{ head: 'Snooze until… (in Friday only)' }].concat(
      snoozeChoices().map(([label, d]) => ({ label, hint: fmtUntil(d), ico: '⏰', onClick: () => act(cards, 'snooze', { until: localIso(d), untilText: fmtUntil(d) }) })),
      [{ sep: true }, { label: 'Pick date & time…', ico: '📅', onClick: () => { setDialog({ kind: 'snooze', cards }); } }]) }, p));
    const laneMenu = (cards, p) => setMenu(Object.assign({ title: 'Move to lane', items: [{ head: 'Friday’s lanes (Friday learns from it)' }].concat(
      Object.keys(LANES).filter(k => k !== 'all').map(k => ({ label: LANES[k][0], ico: LANES[k][1], checked: cards.length === 1 ? cards[0].lane === k : undefined, onClick: () => moveLane(cards, k) }))) }, p));
    const labelMenu = (cards, p) => {
      const aids = Array.from(new Set(cards.map(c => c.account_id)));
      Promise.all(aids.map(loadLabels)).then(() => {
        const names = Array.from(new Set([].concat.apply([], aids.map(a => (labelsBy[a] || []).map(l => l.name))))).sort((a, b) => a.localeCompare(b));
        const has = name => cards.every(c => (c.labels || []).some(id => labelName(c.account_id, id) === name));
        const ro = aids.filter(a => !canModify(a));
        setMenu(Object.assign({ title: 'Label as', items: [{ head: 'Gmail labels' + (ro.length ? ' (read-only account)' : '') }].concat(
          names.length ? names.map(n => ({ label: n, checked: has(n), off: ro.length === aids.length, title: ro.length === aids.length ? 'Reconnect with sending to label in Gmail' : '', onClick: () => setLabel(cards, n, !has(n)) })) : [{ label: 'No labels yet', off: true }],
          [{ sep: true }, { label: 'New label…', ico: '＋', off: ro.length === aids.length, onClick: () => newLabel(cards) },
            { sep: true }, { head: 'Friday’s lanes' }],
          Object.keys(LANES).filter(k => k !== 'all').map(k => ({ label: LANES[k][0], ico: LANES[k][1], checked: cards.length === 1 ? cards[0].lane === k : undefined, onClick: () => moveLane(cards, k) }))) }, p));
      });
    };
    const moreItems = (cards) => {
      const one = cards.length === 1 ? cards[0] : null;
      return [
        { label: 'Mute', ico: '🔕', hint: 'm', title: 'Hide this conversation and keep new replies out of view (in Friday only)', onClick: () => act(cards, 'mute') },
        inSpam ? { label: 'Not spam', ico: '✅', onClick: () => act(cards, 'notspam') } : { label: 'Report spam', ico: '⚠', hint: '!', onClick: () => act(cards, 'spam') },
        { label: cards.every(c => c.important) ? 'Mark not important' : 'Mark important', ico: '❗', hint: cards.every(c => c.important) ? '-' : '+', onClick: () => act(cards, cards.every(c => c.important) ? 'unimportant' : 'important') },
        one && { sep: true },
        one && one.list_unsubscribe && { label: 'Unsubscribe…', ico: '✂', title: 'Leave this mailing list (confirms first)', onClick: () => unsubscribe(one) },
        one && { label: 'Filter messages like these', ico: '⧩', onClick: () => filterLike(one) },
        one && { label: 'Block sender', ico: '⛔', off: true, title: 'Blocking is a Gmail filter; it needs Gmail’s settings permission, which Friday has not asked for yet.' }
      ].filter(Boolean);
    };
    const contextMenu = (m, e) => {
      e.preventDefault();
      const cards = sel.has(m.id) ? selected() : [m];
      const one = cards.length === 1;
      setMenu({ x: e.clientX, y: e.clientY, title: 'Message actions', items: [
        one && { label: 'Open', ico: '↗', hint: 'o', onClick: () => openThread(m) },
        one && { sep: true },
        inTrash ? { label: 'Restore to Inbox', ico: '↩', onClick: () => act(cards, 'untrash') } : { label: 'Archive', ico: '🗄', hint: 'e', onClick: () => act(cards, 'archive') },
        !inTrash && { label: 'Delete (to Trash)', ico: '🗑', hint: '#', onClick: () => act(cards, 'trash') },
        { label: cards.every(c => c.unread) ? 'Mark read' : 'Mark unread', ico: '✉', hint: cards.every(c => c.unread) ? 'Shift+I' : 'Shift+U', onClick: () => act(cards, cards.every(c => c.unread) ? 'read' : 'unread') },
        { label: cards.every(c => c.flagged) ? 'Unstar' : 'Star', ico: '⭐', hint: 's', onClick: () => act(cards, cards.every(c => c.flagged) ? 'unflag' : 'flag') },
        { label: 'Snooze…', ico: '⏰', hint: 'b', onClick: () => { snoozeMenu(cards, { x: e.clientX, y: e.clientY }); return true; } },
        { label: 'Label as…', ico: '🏷', hint: 'l', onClick: () => { labelMenu(cards, { x: e.clientX, y: e.clientY }); return true; } },
        { label: 'Move to lane…', ico: '🗂', hint: 'v', onClick: () => { laneMenu(cards, { x: e.clientX, y: e.clientY }); return true; } },
        { sep: true }].concat(moreItems(cards)).filter(Boolean) });
    };

    const onKey = e => {
      const tag = (e.target.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select' || e.target.isContentEditable) {
        if (e.key === 'Escape' && e.target === searchRef.current) { searchRef.current.blur(); boxRef.current && boxRef.current.focus(); }
        return;
      }
      if (e.ctrlKey || e.metaKey || e.altKey || menu || dialog) return;
      const k = e.key;
      const cur = shown[focus];
      // * then a / n / r / u : select all, none, read, unread (Gmail's)
      if (starRef.current && Date.now() - starRef.current < 1500) {
        starRef.current = 0;
        const pick = { a: () => shown, n: () => [], r: () => shown.filter(m => !m.unread), u: () => shown.filter(m => m.unread), s: () => shown.filter(m => m.flagged) }[k];
        if (pick) { e.preventDefault(); setSel(new Set(pick().map(m => m.id))); return; }
      }
      const handled = {
        j: () => { setFocus(f => Math.min(shown.length - 1, f + 1)); },
        k: () => { setFocus(f => Math.max(0, f - 1)); },
        ArrowDown: () => { setFocus(f => Math.min(shown.length - 1, f + 1)); },
        ArrowUp: () => { setFocus(f => Math.max(0, f - 1)); },
        o: () => openThread(cur), Enter: () => openThread(cur),
        u: () => setOpen(null), Escape: () => { if (help) setHelp(false); else if (preview) setPreview(null); else if (layout !== 'wide' && sideOpen === true) setSideOpen(null); else if (sel.size) setSel(new Set()); else setOpen(null); },
        x: () => cur && toggleSel(cur),
        '*': () => { starRef.current = Date.now(); say('Select: a all · n none · r read · u unread · s starred'); },
        e: () => act(targets(), inTrash ? 'untrash' : 'archive'), y: () => act(targets(), 'archive'),
        '#': () => act(targets(), inTrash ? 'untrash' : 'trash'), Delete: () => act(targets(), inTrash ? 'untrash' : 'trash'),
        '!': () => act(targets(), inSpam ? 'notspam' : 'spam'),
        s: () => { const t = targets(); act(t, t.every(m => m.flagged) ? 'unflag' : 'flag'); },
        '+': () => act(targets(), 'important'), '=': () => act(targets(), 'important'), '-': () => act(targets(), 'unimportant'),
        m: () => act(targets(), 'mute'),
        U: () => act(targets(), 'unread'), I: () => act(targets(), 'read'),
        b: () => { const t = targets(); if (t.length) snoozeMenu(t, rowPoint()); },
        l: () => { const t = targets(); if (t.length) labelMenu(t, rowPoint()); },
        v: () => { const t = targets(); if (t.length) laneMenu(t, rowPoint()); },
        r: () => startReply('reply'), a: () => startReply('replyAll'), f: () => startReply('forward'),
        c: () => setCompose({ mode: 'new' }), '/': () => searchRef.current && searchRef.current.focus(),
        z: () => undo(), '?': () => setHelp(v => !v), g: () => load(query, folder)
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
    const searchToks = qInput.trim().split(/\s+/).filter(Boolean);
    const toggleChip = tok => {
      const has = searchToks.includes(tok);
      const next = (has ? searchToks.filter(t => t !== tok) : searchToks.concat([tok])).join(' ');
      setQInput(next); setQuery(next.trim());
    };

    const Row = (m, i) => {
      const L = LANES[m.lane] || [m.lane, '•'];
      const labs = (m.labels || []).map(id => labelName(m.account_id, id)).filter(Boolean).slice(0, 3);
      const stop = f => e => { e.stopPropagation(); f(e); };
      const btn = (ico, title, f, cls) => h('button', { className: 'fm-act' + (cls ? ' ' + cls : ''), title, 'aria-label': title, onClick: stop(f), onMouseDown: e => e.stopPropagation() }, ico);
      return h('div', {
        key: (m.account_id || '') + ':' + m.id, 'data-id': m.id,
        className: 'fm-row' + (m.unread ? ' unread' : '') + (i === focus ? ' focus' : '') + (open && open.card.id === m.id ? ' open' : '') + (sel.has(m.id) ? ' sel' : '') + (busyIds.has(m.id) ? ' busy' : '') + (m.awaiting_reply && !folder ? ' await' : ''),
        onClick: e => { setFocus(i); if (e.shiftKey || e.ctrlKey || e.metaKey) toggleSel(m, e); else openThread(m); },
        onContextMenu: e => { setFocus(i); contextMenu(m, e); },
        role: 'row', 'aria-selected': sel.has(m.id), title: m.awaiting_reply && !folder ? 'Waiting for your reply' : undefined
      },
        h('input', { type: 'checkbox', checked: sel.has(m.id), onClick: e => toggleSel(m, e), onChange: () => {}, 'aria-label': 'Select conversation' }),
        h('span', { className: 'fm-dot', style: { background: m.account_color || '#7c8aa5' }, title: m.account_label }),
        h('div', { className: 'fm-from' }, (m.flagged ? '⭐ ' : '') + (m.important ? '❗' : '') + (m.sender || m.sender_email), m.thread_count > 1 && h('span', { className: 'fm-count', style: { marginLeft: 6, opacity: 0.8 }, title: m.thread_count + ' messages in this conversation' }, m.thread_count)),
        h('div', { className: 'fm-line' },
          !folder && h('span', { className: 'fm-badge', style: { borderColor: 'rgba(0,212,255,0.35)', color: '#8fd3ff' } }, L[1] + ' ' + L[0]),
          labs.map(n => h('span', { key: n, className: 'fm-lab' }, n)),
          h('span', { className: 'fm-subj' }, m.subject), h('span', { className: 'fm-snip' }, ' — ' + unent(m.snippet))),
        h('div', { className: 'fm-when' }, (m.has_attachment ? '📎 ' : '') + fmtWhen(m.timestamp)),
        h('div', { className: 'fm-acts', role: 'group', 'aria-label': 'Actions' },
          inTrash ? btn('↩', 'Restore to Inbox', () => act([m], 'untrash')) : btn('🗄', 'Archive (e)', () => act([m], 'archive')),
          !inTrash && btn('🗑', 'Delete — to Trash (#)', () => act([m], 'trash'), 'danger'),
          btn(m.unread ? '✉' : '📭', m.unread ? 'Mark read (Shift+I)' : 'Mark unread (Shift+U)', () => act([m], m.unread ? 'read' : 'unread')),
          btn('⏰', 'Snooze (b)', e => { const r = e.currentTarget.getBoundingClientRect(); snoozeMenu([m], { x: r.left, y: r.bottom + 2 }); }),
          btn('🏷', 'Label or move to a lane (l)', e => { const r = e.currentTarget.getBoundingClientRect(); labelMenu([m], { x: r.left, y: r.bottom + 2 }); })));
    };

    const T = open && open.res;
    const allChecked = shown.length > 0 && shown.every(m => sel.has(m.id));
    const someChecked = shown.some(m => sel.has(m.id));
    const headRef = useRef(null);
    useEffect(() => { if (headRef.current) headRef.current.indeterminate = someChecked && !allChecked; }, [someChecked, allChecked]);
    const bulk = selected();
    const sideLabels = allLabelNames();
    return h('div', { className: 'fm ws-fill fm-' + layout + (open ? ' fm-reading' : ''), ref: boxRef, tabIndex: 0, onKeyDown: onKey, style: { outline: 'none' } },
      // row 1: accounts, search, actions
      h('div', { className: 'fm-bar' },
        h('button', { className: 'btn fm-btn', onClick: () => { if (layout === 'wide') { const v = showSide ? false : null; setSideOpen(v); store.set('fm_side', v); } else setSideOpen(showSide ? null : true); },
          title: showSide ? 'Hide the folders and labels' : 'Show the folders and labels', 'aria-label': showSide ? 'Hide the folders and labels' : 'Show the folders and labels', 'aria-pressed': showSide }, '☰ Folders'),
        h('span', { className: 'fm-chip' + (acct === 'all' ? ' on' : ''), onClick: () => setAcct('all') }, '📬 All accounts',
          data && !data.search_failed && h('span', { className: 'fm-count' }, all.filter(m => m.unread).length + ' unread')),
        accounts.map(a => h('span', { key: a.id, className: 'fm-chip' + (acct === a.id ? ' on' : ''), onClick: () => setAcct(a.id), title: a.email + (a.mail && a.mail.modify ? '' : ' — read-only in Gmail') },
          h('span', { className: 'fm-dot', style: { background: a.color || '#7c8aa5' } }), a.label || a.email,
          data && !data.search_failed && !errs.some(x => x.account_id === a.id) && h('span', { className: 'fm-count' }, (unreadBy[a.id] || 0) + ' unread'),
          !(a.mail && a.mail.modify) && h('span', { title: 'Read-only in Gmail', style: { opacity: 0.7 } }, '🔒'),
          errs.some(x => x.account_id === a.id) && h('span', { title: 'This account could not be read', style: { color: '#ff8a8a' } }, '⚠'))),
        h('input', { ref: searchRef, className: 'fm-search', value: qInput, placeholder: 'Search mail — Gmail search: from:ada is:unread has:attachment newer_than:7d …  (press /)',
          'aria-label': 'Search mail', onChange: e => setQInput(e.target.value), onKeyDown: e => { if (e.key === 'Enter') setQuery(qInput.trim()); } }),
        query && h('button', { className: 'btn fm-btn', onClick: () => { setQInput(''); setQuery(''); } }, '✕ Clear search'),
        h('button', { className: 'btn fm-btn', onClick: () => load(query, folder), title: 'Read your mail again (g)', 'aria-label': 'Refresh' }, '↻ Refresh'),
        h('button', { className: 'btn fm-btn', onClick: () => setCompose({ mode: 'new' }), style: { borderColor: '#00d4ff', color: '#00d4ff' }, title: 'Compose (c)' }, '✎ Compose'),
        h('button', { className: 'btn fm-btn', onClick: () => setHelp(true), title: 'Keyboard shortcuts (?)', 'aria-label': 'Keyboard shortcuts' }, '⌨ Shortcuts')),
      // search chips
      h('div', { className: 'fm-bar', role: 'group', 'aria-label': 'Search chips' },
        SEARCH_CHIPS.map(([tok, lbl]) => h('span', { key: tok, className: 'fm-chip sm' + (searchToks.includes(tok) ? ' on' : ''), role: 'switch', 'aria-checked': searchToks.includes(tok), onClick: () => toggleChip(tok), title: tok }, lbl))),
      // row 2: lanes (Friday's triage) or Gmail's categories (Inbox)
      !query && !folder && h('div', { className: 'fm-bar' },
        Object.keys(LANES).map(id => h('span', { key: id, className: 'fm-chip' + (lane === id ? ' on' : ''), onClick: () => setLane(id) },
          LANES[id][1] + ' ' + LANES[id][0], id !== 'all' && counts[id] ? h('span', { className: 'fm-count' }, counts[id]) : null)),
        h('span', { className: 'fm-chip' + (unreadOnly ? ' on' : ''), onClick: () => setUnreadOnly(v => !v) }, '● Unread only')),
      (folder === 'inbox' || folder.indexOf('category:') === 0) && h('div', { className: 'fm-bar', role: 'tablist', 'aria-label': 'Categories' },
        h('span', { className: 'fm-chip' + (folder === 'inbox' ? ' on' : ''), role: 'tab', onClick: () => setFolder('inbox') }, 'All inbox'),
        CATEGORIES.map(([k, l]) => h('span', { key: k, role: 'tab', className: 'fm-chip' + (folder === 'category:' + k ? ' on' : ''), onClick: () => setFolder('category:' + k) }, l))),
      // honest status
      readOnlyAccts.length > 0 && h('div', { className: 'fm-banner warn', 'data-testid': 'fm-readonly' },
        '🔒 Read-only in Gmail: ' + readOnlyAccts.map(a => a.label || a.email).join(', ') + '. Delete, spam, importance and labels need Gmail’s permission to change mail; archive, star and read changes there stay in Friday.',
        h('button', { className: 'btn fm-btn', onClick: openReconnect, style: { marginLeft: 'auto' } }, 'Reconnect with sending…')),
      loading && !data && h('div', { className: 'fm-banner info' }, query ? 'Searching Gmail for “' + query + '”…' : folder ? 'Reading ' + folderName(folder) + '…' : 'Reading your mail…'),
      failed && h('div', { className: 'fm-banner err', role: 'alert' }, '⚠ ' + (data.error || "Couldn't read mail.") + ' This is not an empty inbox — the read did not happen.'),
      !failed && data && data.partial && h('div', { className: 'fm-banner warn' }, '⚠ Showing only part of your mail: ' + errs.map(e => (e.label || 'an account') + ' — ' + e.error).join('; ')),
      !failed && data && /^cache/.test(data.source || '') && h('div', { className: 'fm-banner warn' }, 'Showing Friday’s saved copy of your mail, not a live read: Gmail could not be reached.'),
      !failed && data && /^legacy/.test(data.source || '') && h('div', { className: 'fm-banner warn' }, 'Showing only your main account: the read across all accounts failed.'),
      query && !failed && data && h('div', { className: 'fm-banner info' }, 'Gmail search for “' + query + '”' + (folder ? ' in ' + folderName(folder) : '') + ': ' + (data.total || 0) + ' result' + (data.total === 1 ? '' : 's') + ' across ' + (acct === 'all' ? 'all accounts' : 'this account') + (data.partial ? ' (partial)' : '')),
      inTrash && h('div', { className: 'fm-banner info' }, '🗑 Gmail’s Trash. Gmail deletes what is here for good after 30 days. Restore (e or #) puts a conversation back in the inbox. Friday never empties the Trash.'),
      inSpam && h('div', { className: 'fm-banner info' }, '⚠ Gmail’s Spam. “Not spam” (!) moves a conversation back to the inbox.'),
      // bulk bar
      sel.size > 0 && h('div', { className: 'fm-bulk', role: 'toolbar', 'aria-label': 'Selected conversations' }, sel.size + ' selected',
        inTrash ? h('button', { className: 'btn fm-btn', onClick: () => act(bulk, 'untrash') }, '↩ Restore') : h('button', { className: 'btn fm-btn', onClick: () => act(bulk, 'archive') }, '🗄 Archive'),
        !inTrash && h('button', { className: 'btn fm-btn danger', onClick: () => act(bulk, 'trash') }, '🗑 Delete'),
        h('button', { className: 'btn fm-btn', onClick: () => act(bulk, inSpam ? 'notspam' : 'spam') }, inSpam ? '✅ Not spam' : '⚠ Spam'),
        h('button', { className: 'btn fm-btn', onClick: () => act(bulk, 'read') }, 'Mark read'),
        h('button', { className: 'btn fm-btn', onClick: () => act(bulk, 'unread') }, 'Mark unread'),
        h('button', { className: 'btn fm-btn', onClick: () => act(bulk, 'flag') }, '⭐ Star'),
        h('button', { className: 'btn fm-btn', onClick: e => { const r = e.currentTarget.getBoundingClientRect(); snoozeMenu(bulk, { x: r.left, y: r.bottom + 2 }); } }, '⏰ Snooze ▾'),
        h('button', { className: 'btn fm-btn', onClick: e => { const r = e.currentTarget.getBoundingClientRect(); labelMenu(bulk, { x: r.left, y: r.bottom + 2 }); } }, '🏷 Label ▾'),
        h('button', { className: 'btn fm-btn', onClick: e => { const r = e.currentTarget.getBoundingClientRect(); laneMenu(bulk, { x: r.left, y: r.bottom + 2 }); } }, '🗂 Lane ▾'),
        h('button', { className: 'btn fm-btn', onClick: e => { const r = e.currentTarget.getBoundingClientRect(); setMenu({ x: r.left, y: r.bottom + 2, title: 'More', items: moreItems(bulk) }); } }, '⋯ More'),
        h('button', { className: 'btn fm-btn', onClick: () => setSel(new Set(shown.map(m => m.id))) }, 'Select all ' + shown.length),
        h('button', { className: 'btn fm-btn', onClick: () => setSel(new Set()) }, 'Clear')),
      // folders, list, thread
      h('div', { className: 'fm-main' },
        showSide && h('nav', { className: 'fm-side', 'aria-label': 'Folders and labels' },
          layout !== 'wide' && h('button', { className: 'fm-side-close', onClick: () => setSideOpen(null), 'aria-label': 'Close the folders' }, '✕ Close'),
          FOLDERS.map(([id, ico, lbl, tip]) => h('button', { key: id || 'priority', className: folder === id || (id === 'inbox' && folder.indexOf('category:') === 0) ? 'on' : '', title: tip || lbl, onClick: () => pickFolder(id) },
            h('span', null, ico), lbl, id === '' && stats && stats.actionable ? h('span', { className: 'fm-count', style: { marginLeft: 'auto' } }, stats.actionable) : null)),
          sideLabels.length > 0 && h('div', { className: 'h' }, 'Labels'),
          sideLabels.map(n => h('button', { key: n, className: folder === 'label:' + n ? 'on' : '', onClick: () => pickFolder('label:' + n) }, h('span', null, '🏷'), n))),
        h('div', { className: 'fm-listwrap' },
          h('div', { className: 'fm-listhead' },
            h('input', { ref: headRef, type: 'checkbox', checked: allChecked, onChange: () => setSel(allChecked ? new Set() : new Set(shown.map(m => m.id))), 'aria-label': 'Select all shown', title: 'Select all shown (* a)' }),
            h('span', { style: { fontWeight: 700, color: '#dbe6f5' } }, folderName(folder)),
            h('span', null, shown.length + (shown.length === 1 ? ' conversation' : ' conversations')),
            h('span', { style: { marginLeft: 'auto' } }, 'Right-click a row for everything')),
          h('div', { className: 'fm-list', ref: listRef, role: 'grid', 'aria-label': 'Messages' },
            failed ? h('div', { style: { padding: 20, color: '#ff9a9a', fontSize: 12 } }, 'No list: the read failed (see above).')
              : shown.length ? shown.map(Row)
                : data && !loading ? h('div', { style: { padding: 20, color: '#7f93ad', fontSize: 12 } }, query ? 'Gmail found nothing for that search.' : folder ? 'Nothing in ' + folderName(folder) + '.' : 'Nothing here.') : null)),
        (open || layout !== 'narrow') && h('div', { className: 'fm-thread' + (open ? '' : ' fm-reading-empty') },
          open ? h(React.Fragment, null, h('div', { style: { display: 'flex', gap: 8, alignItems: 'flex-start', justifyContent: 'space-between' } },
            h('div', null,
              h('div', { style: { fontSize: 15, fontWeight: 700, color: '#fff' } }, open.card.subject),
              h('div', { style: { fontSize: 11, color: '#8fa6c4', marginTop: 2 } },
                h('span', { className: 'fm-dot', style: { background: open.card.account_color, marginRight: 5 } }), open.card.account_label + ' · ' + laneLabel(open.card.lane),
                (open.card.labels || []).map(id => labelName(open.card.account_id, id)).filter(Boolean).map(n => h('span', { key: n, className: 'fm-lab', style: { marginLeft: 6 } }, n)))),
            h('button', { className: 'btn fm-btn', onClick: () => setOpen(null), title: layout === 'narrow' ? 'Back to the list (u)' : 'Close the conversation (u)', 'aria-label': layout === 'narrow' ? 'Back to the list' : 'Close the conversation' }, layout === 'narrow' ? '← Back to the list' : '✕ Close')),
          h('div', { className: 'fm-bar', style: { margin: '8px 0' }, role: 'toolbar', 'aria-label': 'Conversation actions' },
            h('button', { className: 'btn fm-btn', disabled: !(T && T.status === 'ok'), onClick: () => startReply('reply'), title: 'r' }, '↩ Reply'),
            h('button', { className: 'btn fm-btn', disabled: !(T && T.status === 'ok'), onClick: () => startReply('replyAll'), title: 'a' }, '↩↩ Reply all'),
            h('button', { className: 'btn fm-btn', disabled: !(T && T.status === 'ok'), onClick: () => startReply('forward'), title: 'f' }, '↪ Forward'),
            h('button', { className: 'btn fm-btn', onClick: fridayDraft }, '💬 Draft with Friday'),
            inTrash ? h('button', { className: 'btn fm-btn', onClick: () => act([open.card], 'untrash') }, '↩ Restore') : h('button', { className: 'btn fm-btn', onClick: () => act([open.card], 'archive'), title: 'e' }, '🗄 Archive'),
            !inTrash && h('button', { className: 'btn fm-btn danger', onClick: () => act([open.card], 'trash'), title: 'Delete — to Trash, kept 30 days (#)' }, '🗑 Delete'),
            h('button', { className: 'btn fm-btn', onClick: () => act([open.card], inSpam ? 'notspam' : 'spam'), title: '!' }, inSpam ? '✅ Not spam' : '⚠ Spam'),
            h('button', { className: 'btn fm-btn', onClick: e => { const r = e.currentTarget.getBoundingClientRect(); snoozeMenu([open.card], { x: r.left, y: r.bottom + 2 }); }, title: 'b' }, '⏰ Snooze ▾'),
            h('button', { className: 'btn fm-btn', onClick: () => act([open.card], open.card.flagged ? 'unflag' : 'flag'), title: 's' }, open.card.flagged ? '★ Unstar' : '⭐ Star'),
            h('button', { className: 'btn fm-btn', onClick: () => act([open.card], 'unread'), title: 'Shift+U' }, 'Mark unread'),
            h('button', { className: 'btn fm-btn', 'data-testid': 'fm-label-btn', onClick: e => { const r = e.currentTarget.getBoundingClientRect(); labelMenu([open.card], { x: r.left, y: r.bottom + 2 }); }, title: 'Gmail labels and Friday’s lanes (l)' }, '🏷 ' + laneLabel(open.card.lane) + ' ▾'),
            h('button', { className: 'btn fm-btn', onClick: e => {
              const r = e.currentTarget.getBoundingClientRect(), msgs = (T && T.messages) || [], last = msgs[msgs.length - 1];
              setMenu({ x: r.left, y: r.bottom + 2, title: 'More', items: moreItems([open.card]).concat([{ sep: true },
                { label: 'Print', ico: '🖨', off: !msgs.length, onClick: () => printThread(open.card.subject, msgs, showImages) },
                { label: 'Show original', ico: '🧾', off: !last, onClick: () => showOriginal(open.card, last) },
                { label: 'Download message (.eml)', ico: '⬇', off: !last, onClick: () => { if (last) window.open('/api/mail/original?account=' + encodeURIComponent(open.card.account_id) + '&message=' + encodeURIComponent(last.id) + '&dl=1', '_blank'); } }]) });
            } }, '⋯ More'),
            h('label', { style: { fontSize: 11, color: '#8fa6c4', display: 'flex', gap: 4, alignItems: 'center' } }, h('input', { type: 'checkbox', checked: showImages, onChange: e => setShowImages(e.target.checked) }), 'Show remote images')),
          aiDraft && h('div', { className: 'fm-banner info', style: { flexDirection: 'column', alignItems: 'stretch' } },
            aiDraft.busy ? 'Friday is drafting a reply…' : aiDraft.error ? aiDraft.error : [h('div', { key: 't', style: { whiteSpace: 'pre-wrap' } }, aiDraft.text),
              h('div', { key: 'b', style: { display: 'flex', gap: 6, marginTop: 6 } }, h('button', { className: 'btn fm-btn', onClick: () => startReply('reply') }, 'Use in reply'), h('button', { className: 'btn fm-btn', onClick: () => setAiDraft(null) }, 'Discard'))]),
          open.loading && h('div', { className: 'fm-banner info' }, 'Opening the thread…'),
          T && T.status !== 'ok' && h('div', { className: 'fm-banner err', role: 'alert' }, '⚠ ' + (T.error || 'Could not open this thread.')),
          T && T.source === 'cache' && h('div', { className: 'fm-banner warn' }, 'Showing Friday’s saved copy: ' + (T.note || 'Gmail was not reachable.')),
          T && (T.messages || []).map(msg => {
            const who = addrOf(msg.sender), asSent = !!original[who];
            return h('div', { key: msg.id, className: 'fm-msg' },
              h('div', { className: 'fm-hdr' },
                h('div', { style: { flex: 1 } }, h('b', null, msg.sender), ' · ', msg.date || fmtWhen(msg.timestamp),
                  msg.list_unsubscribe && [' · ', h('button', { key: 'u', className: 'fm-link', onClick: () => unsubscribe(open.card), title: 'Leave this mailing list (confirms first)' }, 'Unsubscribe')], h('br'),
                  msg.to && ['to ', msg.to], msg.cc && [h('br', { key: 'b' }), 'cc ', msg.cc]),
                msg.html && h('button', { className: 'fm-link', 'data-testid': 'fm-theme-toggle', onClick: () => toggleOriginal(who),
                  title: asSent ? 'Re-colour this sender’s mail for Friday’s dark theme' : 'Show this sender’s mail exactly as it was sent (remembered for this sender)' }, asSent ? '🌙 Match Friday’s theme' : '☀ Show original colours'),
                h('button', { className: 'fm-link', onClick: () => showOriginal(open.card, msg), title: 'The message source and all its headers' }, 'Headers')),
              h(MailBody, { html: msg.html, text: msg.body, showImages, onMailto: mailtoCompose, adapt: !asSent }),
              (msg.attachments || []).length > 0 && h('div', { className: 'fm-atts' }, msg.attachments.map(a => h('span', { key: a.attachment_id, style: { display: 'inline-flex', gap: 4 } },
                (/^image\/(png|jpe?g|gif|webp)$/.test(a.mime) || a.mime === 'application/pdf') && h('button', { className: 'fm-att', onClick: () => setPreview(a) }, '👁 ' + a.filename),
                h('a', { className: 'fm-att', href: a.url + '&dl=1', download: a.filename, title: 'Download' }, '⬇ ' + (/^image|pdf/.test(a.mime) ? '' : a.filename + ' ') + '· ' + Math.max(1, Math.round((a.size || 0) / 1024)) + ' KB')))));
          })) : h('div', { className: 'fm-reading-hint' }, h('div', { style: { fontSize: 30, marginBottom: 8 } }, '✉'), 'Choose a conversation to read it here.', h('div', { style: { marginTop: 6, fontSize: 11, color: '#5f7896' } }, 'j / k move · o or Enter opens · right-click a row for every action')))),
      compose && h(Composer, { key: compose.mode + (compose.thread_id || '') + (compose.subject || ''), init: compose, accounts, canSend, onClose: () => setCompose(null), say }),
      preview && h('div', { className: 'fm-preview', onClick: () => setPreview(null) },
        h('div', { style: { display: 'flex', justifyContent: 'space-between', padding: 10, fontSize: 12 } }, preview.filename,
          h('span', null, h('a', { className: 'fm-att', href: preview.url, download: preview.filename, onClick: e => e.stopPropagation() }, '⬇ Download'), ' ', h('button', { className: 'btn fm-btn', 'aria-label': 'Close the preview', title: 'Close the preview (Esc)' }, '✕ Close'))),
        /^image\//.test(preview.mime) ? h('img', { src: preview.url, alt: preview.filename }) : h('iframe', { src: preview.url, title: preview.filename })),
      menu && h(Menu, { menu, onClose: () => setMenu(null) }),
      dialog && dialog.kind === 'unsub' && h(Dialog, { title: 'Unsubscribe?', onClose: () => setDialog(null) },
        h('p', null, 'Leave the mailing list that sent “' + (dialog.card.subject || '') + '”' + (dialog.opt.sender ? ' (' + dialog.opt.sender + ')' : '') + '?'),
        h('p', { style: { color: '#ffd699' } }, dialog.opt.method === 'one_click' ? 'Friday will send the list’s own one-click unsubscribe request to ' + dialog.opt.host + '. That tells the sender this address is read.'
          : dialog.opt.method === 'mailto' ? 'This list unsubscribes by email to ' + dialog.opt.mailto.to + '. Friday will write that message; sending it asks for your approval.'
            : 'This list unsubscribes on its own web page (' + dialog.opt.host + '). Friday will open it in your browser.'),
        h('div', { className: 'fm-bar', style: { justifyContent: 'flex-end', marginTop: 12 } },
          h('button', { className: 'btn fm-btn', onClick: () => setDialog(null) }, 'Cancel'),
          h('button', { className: 'btn fm-btn', style: { borderColor: '#00d4ff', color: '#00d4ff' }, autoFocus: true, onClick: () => doUnsubscribe(dialog) }, 'Unsubscribe'))),
      dialog && dialog.kind === 'original' && h(Dialog, { title: 'Original message', onClose: () => setDialog(null) },
        dialog.loading ? h('div', null, 'Reading it from Gmail…')
          : dialog.d.status !== 'ok' ? h('div', { className: 'fm-banner err' }, dialog.d.message || 'Gmail did not return the message.')
            : h(React.Fragment, null,
              h('table', null, h('tbody', null, dialog.d.headers.map(([k, v], i) => h('tr', { key: i }, h('td', null, k), h('td', null, v))))),
              h('div', { style: { margin: '10px 0 4px', color: '#8fa6c4' } }, 'Source (' + Math.round(dialog.d.size / 1024) + ' KB' + (dialog.d.truncated ? ', first 2 MB shown' : '') + ')'),
              h('pre', null, dialog.d.source)),
        h('div', { className: 'fm-bar', style: { justifyContent: 'flex-end', marginTop: 10 } },
          h('a', { className: 'btn fm-btn', href: '/api/mail/original?account=' + encodeURIComponent(dialog.card.account_id) + '&message=' + encodeURIComponent(dialog.msg.id) + '&dl=1', target: '_blank', rel: 'noopener' }, '⬇ Download .eml'),
          h('button', { className: 'btn fm-btn', onClick: () => setDialog(null) }, 'Close'))),
      dialog && dialog.kind === 'snooze' && h(SnoozePicker, { onClose: () => setDialog(null), onPick: d => { const cards = dialog.cards; setDialog(null); act(cards, 'snooze', { until: localIso(d), untilText: fmtUntil(d) }); } }),
      dialog && dialog.kind === 'reconnect' && h(Dialog, { title: 'Gmail has not allowed this yet', onClose: () => setDialog(null) },
        h('p', null, dialog.why + '.'),
        h('p', null, 'Friday can delete (to Trash), label, report spam and mark importance in Gmail once the account is reconnected with “allow sending and mailbox changes” ticked. Nothing else about the account changes.'),
        h('div', { className: 'fm-bar', style: { justifyContent: 'flex-end', marginTop: 12 } },
          h('button', { className: 'btn fm-btn', onClick: () => setDialog(null) }, 'Not now'),
          h('button', { className: 'btn fm-btn', style: { borderColor: '#00d4ff', color: '#00d4ff' }, onClick: () => { setDialog(null); openReconnect(); } }, 'Open Settings › Connectors'))),
      help && h('div', { className: 'fm-help', onClick: () => setHelp(false) }, h('div', null,
        [['j / k', 'next / previous'], ['o or Enter', 'open'], ['u', 'back to list'], ['x', 'select'], ['* a / * n', 'select all / none'], ['e', 'archive'], ['#  or Delete', 'delete (to Trash)'], ['!', 'report spam'],
         ['s', 'star / unstar'], ['+ / -', 'important / not'], ['Shift+U', 'mark unread'], ['Shift+I', 'mark read'], ['b', 'snooze…'], ['l', 'label…'], ['v', 'move to lane…'], ['m', 'mute'],
         ['z', 'undo'], ['r', 'reply'], ['a', 'reply all'], ['f', 'forward'], ['c', 'compose'], ['/', 'search'], ['g', 'refresh'], ['?', 'this help'], ['Esc', 'close / clear'], ['right-click', 'every action']]
          .map(([k, d]) => h('div', { key: k, style: { breakInside: 'avoid', margin: '3px 0' } }, h('kbd', null, k), d)),
        h('div', { style: { marginTop: 10, color: '#7f93ad', columnSpan: 'all' } }, 'Your own clicks act at once and can be undone (Z). Delete moves to Gmail’s Trash, kept 30 days; nothing here deletes for good. Delete, spam, importance and labels need an account reconnected with sending; archive, star and read there stay in Friday. Snooze and mute are Friday’s own. Sending always waits for your approval, then 10 seconds you can take it back in.'))),
      toast && h('div', { className: 'fm-toast' + (toast.err ? ' err' : ''), role: 'status' }, toast.text,
        toast.undo && h('button', { className: 'btn fm-btn', onClick: () => undo(toast.undo) }, 'Undo (z)')));
  }

  function SnoozePicker({ onClose, onPick }) {
    const def = (() => { const d = new Date(Date.now() + 86400e3); d.setHours(8, 0, 0, 0); const p = n => String(n).padStart(2, '0'); return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + 'T08:00'; })();
    const [v, setV] = useState(def);
    const d = new Date(v), ok = !isNaN(d) && d.getTime() > Date.now();
    return h(Dialog, { title: 'Snooze until', onClose },
      h('input', { type: 'datetime-local', value: v, onChange: e => setV(e.target.value), style: { background: '#0b1220', color: '#e6f0ff', border: '1px solid #24406a', borderRadius: 6, padding: 6, colorScheme: 'dark' }, 'aria-label': 'Snooze until' }),
      h('div', { style: { marginTop: 8, color: '#8fa6c4', fontSize: 11 } }, 'Snooze is Friday’s own: the conversation leaves Friday’s view and comes back then. Gmail is not changed.'),
      h('div', { className: 'fm-bar', style: { justifyContent: 'flex-end', marginTop: 12 } },
        h('button', { className: 'btn fm-btn', onClick: onClose }, 'Cancel'),
        h('button', { className: 'btn fm-btn', disabled: !ok, onClick: () => onPick(d), style: { borderColor: '#00d4ff', color: '#00d4ff' } }, 'Snooze')));
  }

  window.FridayMailPanel = FridayMailPanel;
})();
