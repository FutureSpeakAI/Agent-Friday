/* Bundle workspaces and "Improve this workspace"
 * (docs/design/active/vibe-coding-salon.md §4.9, §4.9.1; Phase 2b).
 *
 * Loaded after friday_artifacts.js. Defines:
 *   window.fridayImproveWorkspace(id, title) -> Promise<string>  a message to show, or ''
 *   window.FridayBundleFrame        the body of an installed bundle workspace: its page in a
 *                                   sandboxed frame (allow-scripts, never allow-same-origin),
 *                                   attached to the frame broker for its read-only capabilities
 *   window.FridayWorkspaceSwapCard  the approval card for a swap: what changes, the checks,
 *                                   a sandboxed preview, the spoken line, two buttons
 *   window.FridayBundleHistory      the installed versions with one-click rollback
 *
 * A bundle never holds Friday's origin. Its page is fetched as text and given
 * to the frame as srcdoc under the artifact frame's CSP; the only way out of
 * the frame is postMessage to the broker. Nothing here widens that.
 */
(function () {
  if (window.FridayBundleFrame) return;
  const h = React.createElement;
  const { useState, useEffect, useRef, useCallback } = React;

  const ACCENT = '#00d4ff', OK = '#00ff80', DENY = '#ff0080', AMBER = '#f59e0b';
  const MONO = 'JetBrains Mono, Consolas, monospace';
  // allow-scripts WITHOUT allow-same-origin: an opaque origin. Never widened.
  const SANDBOX = 'allow-scripts';

  const api = (url, opts) => {
    const tok = window.__FRIDAY_API_TOKEN || '';
    const headers = Object.assign({}, opts && opts.headers);
    if (tok) headers['X-Friday-Token'] = tok;
    return fetch(url, Object.assign({}, opts, { headers }));
  };
  const getJ = url => api(url).then(r => r.json());
  const getT = url => api(url).then(r => r.ok ? r.text() : Promise.reject(new Error('HTTP ' + r.status)));
  const postJ = (url, body) => api(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) })
    .then(r => r.json().then(j => ({ ok: r.ok, status: r.status, j })));
  const ws = id => '/api/workspaces/' + encodeURIComponent(id);
  const frameDoc = html => (window.fridayArtifactFrameDoc ? window.fridayArtifactFrameDoc(html) : String(html || ''));
  const short = sha => String(sha || '').slice(0, 7);
  const when = ts => { try { return new Date(ts).toLocaleString(); } catch (e) { return String(ts || ''); } };

  // ── Improve this workspace ─────────────────────────────────────────────
  // Resolves to '' when the chat opened, else to one plain sentence to show.
  window.fridayImproveWorkspace = function (id, title) {
    return postJ(ws(id) + '/improve', {}).then(({ ok, j }) => {
      if (ok && j && j.conversation_id) {
        if (window.fridayOpenChatWindow) window.fridayOpenChatWindow(j.conversation_id, 'Improve ' + (j.label || title || id));
        return '';
      }
      if (j && j.blocker === 'needs_phase_7') return 'Not yet: ' + (j.error || (title + ' is part of Friday herself; improving it means editing her own source, which is not built yet.'));
      return 'Could not open the salon for ' + (title || id) + ': ' + ((j && j.error) || 'unknown error');
    }).catch(e => 'Could not open the salon for ' + (title || id) + ': ' + e);
  };

  // ── The bundle frame ───────────────────────────────────────────────────
  function FridayBundleFrame({ wsId }) {
    const [doc, setDoc] = useState(null);
    const [ver, setVer] = useState(null);
    const [err, setErr] = useState('');
    const ref = useRef(null);
    const load = useCallback(() => {
      getT(ws(wsId) + '/bundle').then(html => setDoc(frameDoc(html))).catch(e => setErr('Could not read this workspace: ' + e.message));
      getJ(ws(wsId) + '/versions').then(v => setVer(v)).catch(() => {});
    }, [wsId]);
    useEffect(() => { load(); }, [load]);
    useEffect(() => {
      if (!window.fridayBusSubscribe) return undefined;
      return window.fridayBusSubscribe(m => { if (m && m.type === 'workspace_bundle_changed' && m.workspace_id === wsId) load(); });
    }, [wsId, load]);
    useEffect(() => { if (ref.current && window.fridayAttachFrame) { try { window.fridayAttachFrame(ref.current); } catch (e) {} } }, [doc]);
    return h('div', { className: 'ws-bundle', 'data-bundle-ws': wsId, style: { display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 } },
      err ? h('div', { style: { padding: 14, fontSize: 12, color: '#f8b4c8' } }, err) : null,
      doc == null && !err ? h('div', { style: { padding: 14, fontSize: 12, color: 'rgba(255,255,255,0.55)' } }, 'Loading your workspace…') : null,
      doc != null ? h('iframe', { key: ver && ver.current ? ver.current : 'v', ref: ref, sandbox: SANDBOX, srcDoc: doc, referrerPolicy: 'no-referrer',
        title: 'Your workspace (sandboxed)', style: { flex: 1, width: '100%', border: 'none', background: '#0b0e14' } }) : null,
      h('div', { style: { fontFamily: MONO, fontSize: 9, letterSpacing: '.08em', color: 'rgba(255,255,255,0.4)', padding: '3px 8px', borderTop: '1px solid rgba(0,212,255,0.12)', display: 'flex', gap: 10 } },
        h('span', null, 'BUNDLE'),
        ver && ver.current ? h('span', { title: ver.current }, 'version ' + short(ver.current)) : null,
        ver && ver.versions ? h('span', null, ver.versions.length + (ver.versions.length === 1 ? ' version' : ' versions') + ' kept') : null,
        h('span', { style: { marginLeft: 'auto' } }, 'runs in its own frame · reaches nothing on its own')));
  }
  window.FridayBundleFrame = FridayBundleFrame;

  // ── The swap card ──────────────────────────────────────────────────────
  function FridayWorkspaceSwapCard({ a, busy, onDecide }) {
    const p = (a && a.payload) || {};
    const [showPreview, setShowPreview] = useState(false);
    const [doc, setDoc] = useState(null);
    useEffect(() => {
      if (!showPreview || doc != null || !p.codebase_id) return;
      getJ('/api/codebases/' + encodeURIComponent(p.codebase_id) + '/preview').then(d => setDoc(frameDoc(d.html || ''))).catch(() => setDoc(frameDoc('<p>Preview unavailable.</p>')));
    }, [showPreview, doc, p.codebase_id]);
    const off = !!busy;
    const smoke = p.smoke || {};
    const smokeLine = smoke.ran ? (smoke.ok ? 'Loaded cleanly in a test browser.' : 'Threw at load: ' + (smoke.errors || []).join('; ')) : 'Browser check not run.';
    const chip = (text, color, title) => h('span', { title, style: { fontFamily: MONO, fontSize: 9, letterSpacing: '.08em', padding: '1px 6px', borderRadius: 3, border: '1px solid ' + color, color } }, text);
    return h('div', { className: 'card', style: { marginBottom: 6, padding: 10 }, 'data-testid': 'approval-card', 'data-approval-id': a.approval_id, 'data-swap-card': p.target_id || p.workspace_id || '' },
      h('div', { style: { display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' } },
        h('span', { style: { fontFamily: 'Orbitron, Inter, sans-serif', fontSize: 9, letterSpacing: '.22em', color: ACCENT } }, p.is_new ? 'NEW WORKSPACE' : 'SWAP WORKSPACE'),
        chip(p.brand === 'ok' ? 'brand ok' : 'brand ?', p.brand === 'ok' ? OK : AMBER, 'No reserved status colour is repainted'),
        chip(smoke.ran ? (smoke.ok ? 'loads clean' : 'throws') : 'unchecked', smoke.ran ? (smoke.ok ? OK : DENY) : 'rgba(255,255,255,0.5)', smokeLine),
        p.head ? chip('step ' + p.head, 'rgba(255,255,255,0.6)', 'The codebase step that would go live') : null),
      h('div', { style: { fontSize: 13, color: '#f1f6fb', fontWeight: 600, marginTop: 6 } }, p.label || a.title),
      h('div', { style: { fontSize: 11, color: '#999', marginTop: 2 } },
        p.is_new ? 'A new workspace in your dock under Mine. It runs only in its own sandboxed frame and reaches nothing on its own.'
                 : (p.changed || 0) + ' file' + (p.changed === 1 ? '' : 's') + ' changed since the version you use now. The old version stays one click away.'),
      (p.steps || []).length ? h('ul', { style: { margin: '6px 0 0', paddingLeft: 16, fontSize: 11, color: 'rgba(255,255,255,0.7)', lineHeight: 1.5 } }, p.steps.slice(0, 6).map((s, i) => h('li', { key: i }, s))) : null,
      h('div', { style: { marginTop: 8 } },
        h('button', { className: 'btn', style: { fontSize: 11, padding: '2px 10px' }, onClick: () => setShowPreview(v => !v) }, showPreview ? 'Hide preview' : 'Show the improved page'),
        showPreview && doc != null ? h('iframe', { srcDoc: doc, sandbox: SANDBOX, title: 'The improved workspace (sandboxed)', referrerPolicy: 'no-referrer',
          style: { display: 'block', width: '100%', height: 280, marginTop: 6, border: '1px solid rgba(0,212,255,0.25)', borderRadius: 6, background: '#0b0e14' } }) : null),
      p.spoken ? h('div', { style: { fontFamily: MONO, fontSize: 10, color: 'rgba(255,255,255,0.45)', marginTop: 8, lineHeight: 1.5 }, title: 'How Friday reads this card aloud' }, '\u{1F50A} ' + p.spoken) : null,
      h('div', { style: { display: 'flex', gap: 6, marginTop: 8 } },
        h('button', { className: 'btn', style: { fontSize: 11, padding: '2px 12px', color: OK }, disabled: off, onClick: () => onDecide(a.approval_id, 'approve') }, p.is_new ? 'Install it' : 'Swap it in'),
        h('button', { className: 'btn', style: { fontSize: 11, padding: '2px 12px', color: DENY }, disabled: off, onClick: () => onDecide(a.approval_id, 'deny') }, 'Leave it')));
  }
  window.FridayWorkspaceSwapCard = FridayWorkspaceSwapCard;

  // ── Versions and rollback ──────────────────────────────────────────────
  function FridayBundleHistory({ wsId, label }) {
    const [v, setV] = useState(null);
    const [busy, setBusy] = useState('');
    const [err, setErr] = useState('');
    const load = useCallback(() => getJ(ws(wsId) + '/versions').then(setV).catch(() => setErr('Could not read the versions.')), [wsId]);
    useEffect(() => { load(); }, [load]);
    const roll = sha => {
      setBusy(sha); setErr('');
      postJ(ws(wsId) + '/rollback', { sha256: sha }).then(({ ok, j }) => {
        if (!ok) setErr((j && j.error) || 'That did not work.');
        try { window.dispatchEvent(new CustomEvent('friday:ws-custom-changed', { detail: { workspace: wsId } })); } catch (e) {}
        return load();
      }).catch(e => setErr('That did not work: ' + e.message)).then(() => setBusy(''));
    };
    const dim = { fontSize: 11, color: 'rgba(255,255,255,0.5)', lineHeight: 1.5 };
    const btn = { background: 'none', border: '1px solid rgba(0,212,255,0.3)', borderRadius: 4, color: '#7dd3fc', cursor: 'pointer', fontSize: 11, padding: '3px 9px', fontFamily: 'inherit' };
    const versions = ((v && v.versions) || []).slice().reverse();
    return h('div', { 'data-bundle-history': wsId, style: { marginBottom: 14, paddingBottom: 10, borderBottom: '1px solid rgba(0,212,255,0.15)' } },
      h('div', { style: { fontFamily: MONO, fontSize: 9, letterSpacing: '.08em', color: 'rgba(255,255,255,0.45)', marginBottom: 6 } }, 'INSTALLED VERSIONS OF ' + (label || wsId).toUpperCase()),
      err ? h('div', { style: Object.assign({}, dim, { color: 'rgba(255,120,120,0.85)' }) }, err) : null,
      !v && !err ? h('div', { style: dim }, 'Reading the versions…') : null,
      versions.map(e => {
        const current = v.current === e.sha256;
        return h('div', { key: e.sha256, 'data-version': e.sha256, 'data-current': current ? '1' : '0', style: { borderLeft: '2px solid ' + (current ? ACCENT : 'rgba(0,212,255,0.3)'), paddingLeft: 10, marginBottom: 10 } },
          h('div', { style: { fontSize: 12, fontFamily: MONO } }, 'version ' + short(e.sha256) + (e.step ? ' · step ' + e.step : '') + (current ? ' · in use now' : '')),
          h('div', { style: dim }, when(e.at) + ' · by ' + (e.by || 'you') + (e.approval_id ? ' · approved' : '')),
          current ? null : h('button', { style: Object.assign({}, btn, { marginTop: 4 }), disabled: !!busy, 'data-rollback': e.sha256, onClick: () => roll(e.sha256) }, busy === e.sha256 ? '…' : 'Roll back to this'));
      }),
      v && !versions.length ? h('div', { style: dim }, 'No versions yet.') : null);
  }
  window.FridayBundleHistory = FridayBundleHistory;
})();
