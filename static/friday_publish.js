/* Publish to web: the card and the settings section.
 *
 * Spec: docs/design/active/vibe-coding-salon.md §4.10.1 (Phase 1b).
 *
 * - FridayPublishCard is the body of a `publish_web` approval card: what
 *   would go public (every file with its size and hash), where (the adapter),
 *   the scan result and the licence check, the warnings (up only while this
 *   PC is on; large media suits a hosted adapter), a sandboxed preview, and
 *   the spoken form. Two buttons: Publish, Don't publish. Nothing is public
 *   until Publish is pressed or said.
 * - FridayPublishSettings is the "Published pages" section in Settings: the
 *   status line (reachable right now, or not, and why), the owner's switch
 *   that takes every page offline at once, the list of what is up with
 *   take-down, the default host, the mark, and the hosted adapters connected
 *   with the user's own account. A token is typed on the screen and never
 *   spoken, never shown back.
 *
 * Loaded after friday_artifacts.js; defines window.FridayPublishCard and
 * window.FridayPublishSettings. index.html reaches both through one line each.
 */
(function () {
  'use strict';
  if (window.FridayPublishCard) return;
  const h = React.createElement;
  const { useState, useEffect, useCallback } = React;

  const api = (url, opts) => {
    const tok = window.__FRIDAY_API_TOKEN || '';
    const headers = Object.assign({}, opts && opts.headers);
    if (tok) headers['X-Friday-Token'] = tok;
    return fetch(url, Object.assign({}, opts, { headers }));
  };
  const getJ = url => api(url).then(r => r.json());
  const postJ = (url, body) => api(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) })
    .then(r => r.json().then(j => ({ ok: r.ok, status: r.status, j })));

  const ACCENT = '#00d4ff', AMBER = '#f59e0b', OK = '#00ff80', DENY = '#ff0080';
  const MONO = "'JetBrains Mono', monospace";
  const LABELS = { this_pc: 'This PC', cloudflare_pages: 'Cloudflare Pages', github_pages: 'GitHub Pages' };
  // The preview frame is the same box as the panel's: an opaque origin.
  const PREVIEW_SANDBOX = 'allow-scripts';

  const chip = (text, color, title) => h('span', { title, style: { fontFamily: MONO, fontSize: 9, letterSpacing: '.06em', color, border: '1px solid ' + color + '55', background: color + '14', borderRadius: 4, padding: '1px 6px', whiteSpace: 'nowrap' } }, text);
  const small = (text, color) => h('div', { style: { fontSize: 11, color: color || 'rgba(255,255,255,0.65)', marginTop: 4, lineHeight: 1.5 } }, text);

  // ── The card ──────────────────────────────────────────────────────────
  function FridayPublishCard({ a, busy, onDecide }) {
    const p = (a && a.payload) || {};
    const [showPreview, setShowPreview] = useState(false);
    const scan = p.scan || {};
    const lic = p.licences || { packages: [], flagged: 0 };
    const tierText = scan.tier === 'TIER_1' ? 'Nothing private found.' : scan.tier === 'TIER_2' ? 'Rated PRIVATE: it would be public to anyone with the link.' : scan.tier === 'TIER_3' ? 'Rated SENSITIVE: it would be public to anyone with the link.' : 'Scan result unavailable.';
    const tierColor = scan.tier === 'TIER_1' ? OK : AMBER;
    const off = !!busy;
    return h('div', { className: 'card', style: { marginBottom: 6, padding: 10 }, 'data-testid': 'approval-card', 'data-approval-id': a.approval_id, 'data-publish-card': p.slug || '' },
      h('div', { style: { display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' } },
        h('span', { style: { fontFamily: 'Orbitron, Inter, sans-serif', fontSize: 9, letterSpacing: '.22em', color: ACCENT } }, 'PUBLISH TO WEB'),
        chip(p.adapter_label || LABELS[p.adapter] || p.adapter || 'host', ACCENT, 'Where it would be hosted'),
        chip('v' + (p.version || 1), 'rgba(255,255,255,0.6)', 'Which version of the artifact')),
      h('div', { style: { fontSize: 13, color: '#f1f6fb', fontWeight: 600, marginTop: 6 } }, p.title || a.title),
      h('div', { style: { fontSize: 11, color: '#999', marginTop: 2 } }, (p.files || []).length + ' file' + ((p.files || []).length === 1 ? '' : 's') + ' · ' + (p.size || '') + ' · the page runs entirely in the visitor\'s browser, with no backend, no secrets and no tracking.'),
      (p.warnings || []).length ? h('ul', { style: { margin: '6px 0 0', paddingLeft: 16, fontSize: 11, color: '#ffd28a', lineHeight: 1.5 } }, p.warnings.map((w, i) => h('li', { key: i }, w))) : null,
      h('div', { style: { display: 'flex', gap: 14, flexWrap: 'wrap', marginTop: 8 } },
        h('div', { style: { flex: '1 1 220px', minWidth: 0 } },
          h('div', { style: { fontFamily: MONO, fontSize: 9, letterSpacing: '.08em', color: 'rgba(255,255,255,0.45)' } }, 'WHAT GOES PUBLIC'),
          h('table', { style: { width: '100%', fontSize: 11, borderCollapse: 'collapse', marginTop: 3 } }, h('tbody', null, (p.files || []).map((f, i) => h('tr', { key: i },
            h('td', { style: { padding: '2px 0', fontFamily: MONO, color: '#dfe7f2' } }, f.path),
            h('td', { style: { padding: '2px 6px', textAlign: 'right', fontFamily: MONO, color: 'rgba(255,255,255,0.55)', whiteSpace: 'nowrap' } }, f.bytes >= 1024 ? Math.round(f.bytes / 1024) + ' KB' : f.bytes + ' B'),
            h('td', { style: { padding: '2px 0', fontFamily: MONO, color: 'rgba(255,255,255,0.35)', fontSize: 9 }, title: f.sha256 }, String(f.sha256 || '').slice(0, 8))))))),
        h('div', { style: { flex: '1 1 220px', minWidth: 0 } },
          h('div', { style: { fontFamily: MONO, fontSize: 9, letterSpacing: '.08em', color: 'rgba(255,255,255,0.45)' } }, 'THE SCAN'),
          small(tierText, tierColor),
          scan.identifiers ? small('Hard identifiers: ' + scan.identifiers, DENY) : null,
          h('div', { style: { fontFamily: MONO, fontSize: 9, letterSpacing: '.08em', color: 'rgba(255,255,255,0.45)', marginTop: 8 } }, 'LICENCES'),
          (lic.packages || []).length
            ? h('div', null, lic.packages.map((x, i) => h('div', { key: i, style: { fontSize: 11, color: x.flag ? AMBER : 'rgba(255,255,255,0.7)', fontFamily: MONO } }, x.package + ' · ' + x.license + (x.flag ? ' · ' + x.flag : ''))))
            : small('No packages: nothing to check.'))),
      h('div', { style: { marginTop: 8 } },
        h('button', { className: 'btn', style: { fontSize: 11, padding: '2px 10px' }, onClick: () => setShowPreview(v => !v) }, showPreview ? 'Hide preview' : 'Show preview'),
        showPreview && p.preview_url ? h('iframe', { src: p.preview_url, sandbox: PREVIEW_SANDBOX, title: 'Preview of the page that would be published (sandboxed)', referrerPolicy: 'no-referrer',
          style: { display: 'block', width: '100%', height: 280, marginTop: 6, border: '1px solid rgba(0,212,255,0.25)', borderRadius: 6, background: '#0b0e14' } }) : null),
      p.spoken ? h('div', { style: { fontFamily: MONO, fontSize: 10, color: 'rgba(255,255,255,0.45)', marginTop: 8, lineHeight: 1.5 }, title: 'How Friday reads this card aloud' }, '\u{1F50A} ' + p.spoken) : null,
      h('div', { style: { fontSize: 10, color: 'rgba(255,255,255,0.4)', fontFamily: MONO, marginTop: 6 } }, a.kind, ' · ', a.policy_class, a.expires_at ? ' · expires ' + new Date(a.expires_at * 1000).toLocaleString() : ''),
      h('div', { style: { display: 'flex', gap: 6, marginTop: 8 } },
        h('button', { className: 'btn', style: { fontSize: 11, padding: '2px 12px', color: OK }, disabled: off, onClick: () => onDecide(a.approval_id, 'approve') }, 'Publish'),
        h('button', { className: 'btn', style: { fontSize: 11, padding: '2px 12px', color: DENY }, disabled: off, onClick: () => onDecide(a.approval_id, 'deny') }, "Don't publish")));
  }
  window.FridayPublishCard = FridayPublishCard;

  // ── Settings: Published pages ─────────────────────────────────────────
  function ConnectForm({ adapter, connected, onChanged }) {
    const [open, setOpen] = useState(false);
    const [token, setToken] = useState('');
    const [account, setAccount] = useState('');
    const [project, setProject] = useState('');
    const [busy, setBusy] = useState(false);
    const [msg, setMsg] = useState('');
    const label = LABELS[adapter];
    const isCF = adapter === 'cloudflare_pages';
    const connect = () => {
      if (!token || busy) return;
      setBusy(true); setMsg('');
      postJ('/api/publish/connect', { adapter, token, account_id: isCF ? account : undefined, project: isCF ? project : undefined, repo: isCF ? undefined : project })
        .then(({ ok, j }) => { if (!ok) { setMsg((j && j.error) || 'Could not connect.'); return; } setToken(''); setOpen(false); setMsg(label + ' connected.'); onChanged(); })
        .catch(e => setMsg('Could not connect: ' + e)).then(() => setBusy(false));
    };
    const disconnect = () => {
      if (busy) return;
      setBusy(true);
      postJ('/api/publish/disconnect', { adapter }).then(() => { setMsg(label + ' disconnected; its token is deleted.'); onChanged(); }).then(() => setBusy(false));
    };
    return h('div', { style: { marginBottom: 10 }, 'data-publish-adapter': adapter },
      h('div', { style: { display: 'flex', alignItems: 'center', gap: 8 } },
        h('span', { style: { fontSize: 12.5, color: 'var(--st-label, #ddd)', fontWeight: 500, flex: 1 } }, label),
        chip(connected ? 'CONNECTED' : 'NOT CONNECTED', connected ? OK : 'rgba(255,255,255,0.5)'),
        connected
          ? h('button', { className: 'btn', style: { fontSize: 11, padding: '2px 10px' }, disabled: busy, onClick: disconnect }, 'Disconnect')
          : h('button', { className: 'btn', style: { fontSize: 11, padding: '2px 10px' }, onClick: () => setOpen(v => !v) }, open ? 'Cancel' : 'Connect')),
      open && !connected ? h('div', { style: { marginTop: 6, display: 'grid', gap: 6 } },
        isCF ? h('input', { className: 'input', placeholder: 'Cloudflare account ID', value: account, onChange: e => setAccount(e.target.value), autoComplete: 'off' }) : null,
        h('input', { className: 'input', placeholder: isCF ? 'Pages project name (created if missing)' : 'GitHub repository, e.g. yourname/pages', value: project, onChange: e => setProject(e.target.value), autoComplete: 'off' }),
        h('input', { className: 'input', type: 'password', placeholder: isCF ? 'API token with Pages: Edit' : 'Personal access token with repo scope', value: token, onChange: e => setToken(e.target.value), autoComplete: 'new-password', 'aria-label': label + ' token' }),  // pragma: allowlist secret
        small('Typed here and stored encrypted on this PC. Never spoken, never shown back, used only to publish pages you approve.'),
        h('button', { className: 'btn', style: { fontSize: 11, padding: '3px 12px', justifySelf: 'start' }, disabled: busy || !token || !project || (isCF && !account), onClick: connect }, busy ? 'Connecting…' : 'Save and connect')) : null,
      msg ? small(msg, ACCENT) : null);
  }

  function FridayPublishSettings({ s, save }) {
    const [st, setSt] = useState(null);
    const [list, setList] = useState([]);
    const [busy, setBusy] = useState(false);
    const refresh = useCallback((probe) => Promise.all([
      getJ('/api/publish/status' + (probe ? '?refresh=1' : '')).then(setSt).catch(() => setSt(null)),
      getJ('/api/publish/list').then(d => setList(d.published || [])).catch(() => {})
    ]), []);
    useEffect(() => { refresh(false); }, [refresh]);
    const tp = (st && st.adapters && st.adapters.this_pc) || {};
    const enabled = tp.enabled !== false;
    const lineColor = !enabled ? AMBER : tp.reachable === true ? OK : tp.serving ? AMBER : 'rgba(255,255,255,0.6)';
    const toggle = () => {
      if (busy) return;
      setBusy(true);
      postJ('/api/publish/this-pc/' + (enabled ? 'disable' : 'enable')).then(() => refresh(false)).then(() => setBusy(false));
    };
    const takeDown = slug => {
      if (busy) return;
      setBusy(true);
      postJ('/api/publish/' + encodeURIComponent(slug) + '/unpublish').then(() => refresh(false)).then(() => setBusy(false));
    };
    const Section = window.StSection || (({ title, children }) => h('section', null, h('h3', null, title), children));
    const Toggle = window.StToggle;
    const Row = window.StRow || (({ label, children }) => h('div', null, h('div', null, label), children));
    return h(Section, { title: 'Published pages', desc: 'Pages you publish from a chat\'s panel. Each one asks first, runs entirely in the visitor\'s browser and carries no tracking.' },
      h('div', { 'data-publish-status': tp.reachable === true ? 'reachable' : tp.serving ? 'unreachable' : enabled ? 'idle' : 'off', style: { display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 } },
        h('span', { style: { width: 8, height: 8, borderRadius: '50%', background: lineColor, boxShadow: '0 0 6px ' + lineColor, flexShrink: 0 } }),
        h('span', { style: { fontSize: 12, color: 'var(--st-label, #ddd)', flex: 1 } }, tp.line || (st ? 'Published pages: status unknown.' : 'Checking…')),
        h('button', { className: 'btn', style: { fontSize: 11, padding: '2px 10px' }, disabled: busy, onClick: () => refresh(true), title: 'Probe the public address now' }, 'Check now')),
      Toggle ? h(Toggle, { label: enabled ? 'Local hosting is on' : 'Local hosting is OFF: every published page is offline', value: enabled, onChange: toggle, disabled: busy,
        desc: 'The switch. Off stops the page server and its tunnel at once; nothing published from this PC is reachable until it is on again.' })
        : h('button', { className: 'btn', onClick: toggle }, enabled ? 'Take all pages offline' : 'Bring pages back'),
      h(Row, { label: 'Default host', desc: 'This PC is up only while the PC is on. Cloudflare Pages and GitHub Pages stay up around the clock on your own account.' },
        h('select', { className: 'input', value: (s && s.publish_default_adapter) || 'this_pc', onChange: e => save({ publish_default_adapter: e.target.value }), 'aria-label': 'Default host' },
          Object.keys(LABELS).map(k => h('option', { key: k, value: k }, LABELS[k])))),
      Toggle ? h(Toggle, { label: 'Small "' + ((window.FRIDAY_BRAND && window.FRIDAY_BRAND.madeWith) || 'Made with Agent Friday™') + '" mark on published pages', value: !(s && s.publish_mark === false), onChange: () => save({ publish_mark: !!(s && s.publish_mark === false) }) }) : null,
      h(Row, { label: 'What is up', desc: list.length ? '' : 'Nothing is published yet.' },
        list.length ? h('table', { style: { width: '100%', fontSize: 11.5, borderCollapse: 'collapse' } }, h('tbody', null, list.map(x => h('tr', { key: x.slug, 'data-published-slug': x.slug },
          h('td', { style: { padding: '4px 0', color: 'var(--st-label, #ddd)' } }, x.title, ' ', chip('v' + x.version, 'rgba(255,255,255,0.5)'), ' ', chip(LABELS[x.adapter] || x.adapter, ACCENT)),
          h('td', { style: { padding: '4px 6px', fontFamily: MONO, fontSize: 10 } }, h('a', { href: x.url, target: '_blank', rel: 'noopener noreferrer', style: { color: ACCENT } }, x.url)),
          h('td', { style: { padding: '4px 0', textAlign: 'right' } }, h('button', { className: 'btn', style: { fontSize: 11, padding: '2px 10px', color: DENY }, disabled: busy, onClick: () => takeDown(x.slug) }, 'Take down')))))) : null),
      h(Row, { label: 'Hosted with your own account', desc: 'For pages that must stay up when this PC is off. Connected once, used only for pages you approve.' },
        h(ConnectForm, { adapter: 'cloudflare_pages', connected: !!(st && st.adapters && st.adapters.cloudflare_pages && st.adapters.cloudflare_pages.connected), onChanged: () => refresh(false) }),
        h(ConnectForm, { adapter: 'github_pages', connected: !!(st && st.adapters && st.adapters.github_pages && st.adapters.github_pages.connected), onChanged: () => refresh(false) })));
  }
  window.FridayPublishSettings = FridayPublishSettings;
})();
