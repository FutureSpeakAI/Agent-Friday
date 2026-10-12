/* The Meetings workspace's own parts: the notes pad, the notes that come out of it, and the archive.
 *
 * The recorder, the start card, the list of past meetings and the plain notes live in MeetingsPanel
 * (index.html); this file adds what the Meetings workspace does with a meeting:
 *   - Pad: shorthand bullets, jotted while the call runs (also a field Friday may write into, never submit);
 *   - After: the bullets expanded into notes by a model on this PC, each line followed by the transcript
 *     line it came from; people, decisions and projects offered to memory for review; the summary offered
 *     to the calendar event as a card; the notes copied;
 *   - Archive: the choice to make meetings searchable from the Library, and the question box over them,
 *     answered by the Library's own path with footnotes.
 *
 * Rules this file keeps: --fr-* tokens only; every sentence says what is true (what ran, where, what was
 * left out); nothing is chosen for the owner; nothing leaves this PC from here.
 *
 * Loaded by index.html after library_ws.js; defines window.MeetingsExtras.
 */
(function () {
  'use strict';
  if (window.MeetingsExtras) return;
  const h = React.createElement;
  const { useState, useEffect, useRef, useCallback } = React;

  function api(url, opts) {
    const tok = window.__FRIDAY_API_TOKEN || '';
    const headers = Object.assign({}, opts && opts.headers);
    if (tok) headers['X-Friday-Token'] = tok;
    return fetch(url, Object.assign({}, opts, { headers }));
  }
  const json = (url, opts) => api(url, opts).then(r => r.json().then(d => { d.__http = r.status; return d; }));
  const post = (url, body) => json(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  const clock = t => {
    t = Math.max(0, Math.floor(t || 0));
    const p = n => String(n).padStart(2, '0');
    return p(Math.floor(t / 3600)) + ':' + p(Math.floor(t / 60) % 60) + ':' + p(t % 60);
  };
  const say = d => (d && (d.error || d.message || d.reason)) || '';
  const who = () => (window.fridayName ? window.fridayName() : 'Friday');

  // Reads a text/event-stream response with fetch (the page's session token rides on fetch, not EventSource).
  function readEventStream(res, onData) {
    const rd = res.body.getReader(), dec = new TextDecoder();
    let buf = '';
    const pump = () => rd.read().then(({ done, value }) => {
      if (done) return undefined;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const frame = buf.slice(0, i); buf = buf.slice(i + 2);
        const line = frame.split('\n').find(l => l.indexOf('data:') === 0);
        if (line) onData(line.slice(5).trim());
      }
      return pump();
    });
    return pump();
  }

  const CSS = `
.mt-box{border:1px solid var(--fr-glass-edge);background:var(--fr-glass);border-radius:10px;padding:10px 12px;margin:10px 0;display:flex;flex-direction:column;gap:8px;color:var(--fr-text);font-size:var(--fr-text-md)}
.mt-title{font-family:var(--fr-font-display);font-size:var(--fr-text-2xs);letter-spacing:var(--fr-track-label);text-transform:uppercase;color:var(--fr-dim)}
.mt-note{color:var(--fr-dim);font-size:var(--fr-text-sm);max-width:80ch}
.mt-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.mt-row input{flex:1 1 240px;font:inherit;color:var(--fr-text);background:var(--fr-glass);border:1px solid var(--fr-glass-edge);border-radius:8px;padding:7px 10px}
.mt-row input:focus{outline:2px solid var(--fr-cyan);outline-offset:1px}
.mt-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:6px}
.mt-list li{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap;overflow-wrap:anywhere}
.mt-time{font-family:var(--fr-font-mono);font-size:var(--fr-text-xs);color:var(--fr-dim)}
.mt-cite{background:transparent;border:1px solid var(--fr-glass-edge);color:var(--fr-label);border-radius:10px;padding:1px 8px;font:inherit;font-family:var(--fr-font-mono);font-size:var(--fr-text-xs);cursor:pointer}
.mt-cite:hover,.mt-cite[aria-expanded="true"]{border-color:var(--fr-cyan);color:var(--fr-cyan)}
.mt-quote{margin:4px 0 2px 0;padding:4px 10px;border-left:2px solid var(--fr-cyan);color:var(--fr-label);font-size:var(--fr-text-sm);width:100%;box-sizing:border-box}
.mt-answer{max-width:80ch;border-left:2px solid var(--fr-cyan);padding-left:12px}
.mt-ev{display:flex;flex-direction:column;gap:4px;border-top:1px solid var(--fr-glass-edge);padding-top:6px}
.mt-ev .q{white-space:pre-wrap;overflow-wrap:anywhere;font-size:var(--fr-text-sm);color:var(--fr-label)}
.mt-pill{font-family:var(--fr-font-display);font-size:var(--fr-text-2xs);letter-spacing:.12em;text-transform:uppercase;padding:2px 7px;border-radius:10px;border:1px solid var(--fr-glass-edge);color:var(--fr-dim)}
@media (prefers-reduced-motion:reduce){.meetings-ws *{transition:none!important;animation:none!important}}
`;
  function ensureStyles() {
    if (document.getElementById('meetings-ws-styles')) return;
    const s = document.createElement('style'); s.id = 'meetings-ws-styles'; s.textContent = CSS; document.head.appendChild(s);
  }

  // ── the notes pad ─────────────────────────────────────────────────────────────
  function Pad({ meetingId }) {
    const [items, setItems] = useState([]);
    const [text, setText] = useState('');
    const [msg, setMsg] = useState('');
    const textRef = useRef(text); textRef.current = text;
    useEffect(() => { ensureStyles(); }, []);
    const load = useCallback(() => {
      if (!meetingId) return;
      json('/api/meetings/' + meetingId + '/pad').then(d => setItems(d.pad || [])).catch(() => {});
    }, [meetingId]);
    useEffect(load, [load]);
    // See & Touch: the pad's line is a field Friday may write into. She never adds it for you.
    useEffect(() => window.fridayStage ? window.fridayStage.registerField('meetings', {
      key: 'pad', label: 'Notes pad', read: () => textRef.current,
      write: t => setText(String(t).replace(/\s+/g, ' ').trim()),
    }) : undefined, []);
    const add = () => {
      const t = text.trim();
      if (!t) return;
      post('/api/meetings/' + meetingId + '/pad', { text: t }).then(d => {
        if (d.status === 'ok') { setText(''); setMsg(''); load(); } else setMsg(say(d) || 'That did not save.');
      }).catch(() => setMsg('That did not save.'));
    };
    const drop = id => json('/api/meetings/' + meetingId + '/pad/' + id, { method: 'DELETE' }).then(load).catch(() => {});
    return h('div', { className: 'mt-box meetings-pad', role: 'group', 'aria-label': 'Your notes pad' },
      h('div', { className: 'mt-title' }, 'Your notes pad'),
      h('div', { className: 'mt-note' }, 'Jot short bullets while the call runs. Afterwards ' + who() + ' can turn them into notes from the transcript, on this PC, and show the line each one came from.'),
      h('div', { className: 'mt-row' },
        h('input', { type: 'text', value: text, placeholder: 'A few words: "Priya: budget by Friday"', 'aria-label': 'A bullet for your notes',
          onChange: e => setText(e.target.value), onKeyDown: e => { if (e.key === 'Enter') add(); } }),
        h('button', { className: 'btn', onClick: add, disabled: !text.trim() }, 'Add')),
      msg && h('div', { className: 'mt-note', role: 'status' }, msg),
      items.length > 0 && h('ul', { className: 'mt-list' }, items.map(b => h('li', { key: b.id },
        b.t != null && h('span', { className: 'mt-time' }, clock(b.t)),
        h('span', { style: { flex: 1 } }, b.text),
        h('button', { className: 'btn', onClick: () => drop(b.id), 'aria-label': 'Take this bullet away' }, '×')))));
  }

  // ── a citation: segment index and time, and the line itself on request ──────────
  function Cite({ span, lines }) {
    const [open, setOpen] = useState(false);
    const ln = lines && lines[span.i];
    return h(React.Fragment, null,
      h('button', { className: 'mt-cite', 'aria-expanded': open ? 'true' : 'false', title: 'The transcript line this rests on',
        onClick: () => setOpen(!open) }, 'line ' + span.i + ' · ' + clock(span.t)),
      open && h('div', { className: 'mt-quote' }, ln ? ((ln.speaker ? ln.speaker + ': ' : '') + ln.text) : 'Loading the line…'));
  }

  function notesText(m) {
    const s = m.summary || {}, out = [];
    if (s.status === 'done') {
      if ((s.notes || []).length) out.push('Notes', ...s.notes.map(x => '- ' + x), '');
      if ((s.decisions || []).length) out.push('Decisions', ...s.decisions.map(x => '- ' + x), '');
      if ((s.action_items || []).length) out.push('Action items', ...s.action_items.map(a => '- ' + a.task + (a.owner ? ' (' + a.owner + (a.due ? ', ' + a.due : '') + ')' : (a.due ? ' (' + a.due + ')' : ''))), '');
    }
    const p = m.polished || {};
    if (p.status === 'done' && (p.lines || []).length) {
      out.push('Notes from my bullets', ...p.lines.map(l => '- ' + l.text + ' [' + (l.spans || []).map(sp => 'line ' + sp.i + ' at ' + clock(sp.t)).join('; ') + ']'));
    }
    return (m.title ? m.title + '\n\n' : '') + out.join('\n');
  }

  // ── what comes after the call ─────────────────────────────────────────────────
  function After({ meeting, reload }) {
    const m = meeting;
    const [lines, setLines] = useState(null);
    const [busy, setBusy] = useState('');
    const [msg, setMsg] = useState('');
    const [props, setProps] = useState([]);
    useEffect(() => { ensureStyles(); }, []);
    useEffect(() => { setLines(null); setMsg(''); setProps([]); }, [m.id]);
    const needLines = useCallback(() => {
      if (lines) return;
      json('/api/meetings/' + m.id + '/lines').then(d => setLines(d.lines || [])).catch(() => setLines([]));
    }, [m.id, lines]);
    const [untraced, setUntraced] = useState([]);
    useEffect(() => {
      setUntraced([]);
      json('/api/meetings/' + m.id + '/actions').then(d => setUntraced((d.items || []).filter(a => !a.traced))).catch(() => {});
    }, [m.id, m.summary && m.summary.at]);
    const loadProps = useCallback(() => json('/api/meetings/' + m.id + '/memory').then(d => setProps(d.proposals || [])).catch(() => {}), [m.id]);
    useEffect(() => { loadProps(); }, [loadProps]);
    const pad = m.pad || [];
    const pol = m.polished || null;
    const polish = () => {
      setBusy('polish'); setMsg('');
      post('/api/meetings/' + m.id + '/polish').then(d => {
        setBusy('');
        const p = d.polished || {};
        if (p.status === 'done') {
          setMsg(p.lines.length + (p.lines.length === 1 ? ' line' : ' lines') + ' from ' + p.bullets + (p.bullets === 1 ? ' bullet' : ' bullets') + (p.left_out ? '. ' + p.left_out + ' left out because they did not trace to the transcript.' : '.'));
        } else setMsg(say(p) || say(d) || 'The notes could not be written.');
        reload && reload();
      }).catch(() => { setBusy(''); setMsg('The notes could not be written.'); });
    };
    const remember = () => {
      setBusy('memory'); setMsg('');
      post('/api/meetings/' + m.id + '/memory').then(d => {
        setBusy('');
        setMsg(d.proposed ? d.proposed + ' suggested below. Nothing is kept unless you choose it, and a kept suggestion joins what Friday knows about you.' : (d.note || d.reason || 'Nothing in this meeting looked worth remembering.'));
        loadProps();
      }).catch(() => { setBusy(''); setMsg('That could not be done just now.'); });
    };
    const decide = (id, keep) => post('/api/memory/proposals/' + (keep ? 'approve' : 'reject'), { fact_ids: [id] }).then(loadProps).catch(() => {});
    const attach = () => {
      setBusy('calendar'); setMsg('');
      post('/api/meetings/' + m.id + '/calendar').then(d => { setBusy(''); setMsg(say(d) || 'Done.'); }).catch(() => { setBusy(''); setMsg('That could not be done just now.'); });
    };
    const copy = () => {
      const t = notesText(m);
      (navigator.clipboard && navigator.clipboard.writeText ? navigator.clipboard.writeText(t) : Promise.reject()).then(() => setMsg('Copied.')).catch(() => setMsg('Your browser would not let me copy. Select the notes and copy them by hand.'));
    };
    const hasNotes = (m.summary && m.summary.status === 'done') || (pol && pol.status === 'done');
    return h('div', { className: 'mt-box meetings-after', role: 'group', 'aria-label': 'Notes from your bullets' },
      pad.length > 0 && h(React.Fragment, null,
        h('div', { className: 'mt-title' }, 'Your bullets'),
        h('ul', { className: 'mt-list' }, pad.map(b => h('li', { key: b.id }, b.t != null && h('span', { className: 'mt-time' }, clock(b.t)), h('span', null, b.text), b.by === 'friday' && h('span', { className: 'mt-note' }, ' (added by Friday)')))),
        h('div', { className: 'mt-row' },
          h('button', { className: 'btn', onClick: polish, disabled: !!busy }, busy === 'polish' ? 'Writing…' : (pol && pol.status === 'done' ? 'Write them again' : 'Turn my bullets into notes')),
          h('span', { className: 'mt-note' }, 'A model on this PC reads the transcript. Each line below says which transcript line it came from, and a line that does not trace to one is left out.'))),
      pol && pol.status === 'done' && h('div', null,
        h('div', { className: 'mt-title' }, 'Notes from your bullets'),
        (pol.lines || []).length === 0 ? h('div', { className: 'mt-note' }, 'No line could be traced to the transcript, so none is shown.') :
          h('ul', { className: 'mt-list' }, pol.lines.map((l, k) => h('li', { key: k },
            h('span', { style: { flex: '1 1 320px' } }, l.text),
            (l.spans || []).map((sp, j) => h('span', { key: j, onClick: needLines }, h(Cite, { span: sp, lines }))))))),
      pol && pol.status && pol.status !== 'done' && h('div', { className: 'mt-note' }, pol.reason || 'The notes were not written.'),
      untraced.length > 0 && h('div', { className: 'mt-note', role: 'note' }, 'Check before you add these to Tasks. I could not find them in the transcript: ' + untraced.map(a => a.task).join('; ') + '.'),
      props.length > 0 && h('div', null,
        h('div', { className: 'mt-title' }, 'Suggested for her memory'),
        h('ul', { className: 'mt-list' }, props.map(p => h('li', { key: p.fact_id },
          h('span', { className: 'mt-pill' }, p.category),
          h('span', { style: { flex: '1 1 280px' } }, p.text),
          p.provenance && h('span', { className: 'mt-time' }, 'line ' + p.provenance.span.segment + ' · ' + clock(p.provenance.span.t) + ' · ' + p.provenance.model),
          h('button', { className: 'btn', onClick: () => decide(p.fact_id, true) }, 'Keep (it joins what Friday knows about you, which a cloud model may be told)'),
          h('button', { className: 'btn', onClick: () => decide(p.fact_id, false) }, 'Not now'))))),
      h('div', { className: 'mt-row' },
        h('button', { className: 'btn', onClick: remember, disabled: !!busy || !hasNotes }, busy === 'memory' ? 'Reading…' : 'Suggest what to remember'),
        m.event_id && h('button', { className: 'btn', onClick: attach, disabled: !!busy || !(m.summary && m.summary.status === 'done') }, 'Add the summary to the calendar event'),
        h('button', { className: 'btn', onClick: copy, disabled: !hasNotes }, 'Copy the notes')),
      h('div', { className: 'mt-note' }, 'Memory suggestions wait for your yes. The calendar note is a card you approve first, and it goes to your calendar account.'),
      msg && h('div', { className: 'mt-note', role: 'status' }, msg));
  }

  // ── the archive and the question box ──────────────────────────────────────────
  function Archive() {
    const [st, setSt] = useState(null);
    const [q, setQ] = useState('');
    const [run, setRun] = useState(null);
    const [msg, setMsg] = useState('');
    const ctl = useRef(null);
    useEffect(() => { ensureStyles(); }, []);
    const load = useCallback(() => json('/api/meetings/archive').then(setSt).catch(() => {}), []);
    useEffect(() => { load(); return () => { if (ctl.current) ctl.current.abort(); }; }, [load]);
    const choose = c => {
      if (c === 'off' && st && st.documents > 0 && !window.confirm('Stop making your meetings searchable? What was read into the Library from them is removed. The meetings themselves stay.')) return;
      post('/api/meetings/archive', { choice: c }).then(d => {
        if (d.status === 'ok') {
          setSt(d); setRun(null);
          setMsg(c === 'on' ? 'Reading your meetings into the Library. It takes a moment.' : 'Your meetings are not searchable now.');
          if (c === 'on') [3000, 8000, 20000].forEach(ms => setTimeout(load, ms));
        } else setMsg(say(d) || 'That did not save.');
      }).catch(() => setMsg('That did not save.'));
    };
    const ask = () => {
      const text = q.trim();
      if (!text) return;
      if (ctl.current) ctl.current.abort();
      const c = new AbortController(); ctl.current = c;
      setRun({ busy: true, q: text, answer: null, note: '', evidence: null, error: '' });
      api('/api/library/search?scope=meetings&answer=1&q=' + encodeURIComponent(text), { signal: c.signal }).then(res => {
        if (!res.ok || !res.body) throw new Error('refused');
        return readEventStream(res, data => {
          let e; try { e = JSON.parse(data); } catch (_) { return; }
          setRun(r => {
            if (!r) return r;
            if (e.event === 'evidence') return Object.assign({}, r, { evidence: e.evidence || [] });
            if (e.event === 'answer') return Object.assign({}, r, { answer: e.text });
            if (e.event === 'answer_unavailable') return Object.assign({}, r, { note: e.reason || '' });
            if (e.event === 'failed') return Object.assign({}, r, { error: e.error, busy: false });
            if (e.event === 'done') return Object.assign({}, r, { busy: false });
            return r;
          });
        });
      }).catch(err => { if (!(err && err.name === 'AbortError')) setRun(r => r && Object.assign({}, r, { busy: false, error: 'The search stopped before it finished.' })); });
    };
    const openEv = ev => { if (window.fridayOpenLibrary && ev.doc_id) window.fridayOpenLibrary({ doc: ev.doc_id, block: ev.block_id }); };
    if (!st) return null;
    const on = st.choice === 'on';
    return h('div', { className: 'mt-box meetings-archive', role: 'region', 'aria-label': 'Ask your meetings' },
      h('div', { className: 'mt-title' }, 'Ask your meetings'),
      st.choice !== 'on' && st.available === false && h('div', { className: 'mt-note', role: 'status' }, st.unavailable_reason || 'Not available on this PC: the Library index isn’t encrypted.'),
      st.choice === '' && st.available !== false && h(React.Fragment, null,
        h('div', null, 'Make your meetings searchable?'),
        h('div', { className: 'mt-note' }, who() + ' reads each meeting’s transcript and notes into your Library, in a scope of its own, on this PC. Then you can ask “what did we decide about the budget?” and get an answer with the transcript lines it came from. A cloud model never reads them from the Library. Nothing is read until you choose.' + (st.index_encrypted === false ? ' The Library’s index is a plain file on this PC, because its optional encryption component is not installed.' : '')),
        h('div', { className: 'mt-row' },
          h('button', { className: 'btn', onClick: () => choose('on') }, 'Make my meetings searchable'),
          h('button', { className: 'btn', onClick: () => choose('off') }, 'Not now'))),
      st.choice === 'off' && st.available !== false && h('div', { className: 'mt-row' },
        h('span', { className: 'mt-note' }, 'Your meetings are not searchable. Nothing from them is in the Library.'),
        h('button', { className: 'btn', onClick: () => choose('on') }, 'Make my meetings searchable')),
      on && h(React.Fragment, null,
        h('div', { className: 'mt-row' },
          h('input', { type: 'text', value: q, placeholder: 'What did we decide about…', 'aria-label': 'Ask about your meetings',
            onChange: e => setQ(e.target.value), onKeyDown: e => { if (e.key === 'Enter') ask(); } }),
          h('button', { className: 'btn', onClick: ask, disabled: !q.trim() || (run && run.busy) }, run && run.busy ? 'Looking…' : 'Ask')),
        run && run.error && h('div', { className: 'mt-note', role: 'alert' }, run.error),
        run && run.answer && h('div', { className: 'mt-answer', dangerouslySetInnerHTML: { __html: window.renderFridayMarkdown ? window.renderFridayMarkdown(run.answer) : '' } }),
        run && run.note && h('div', { className: 'mt-note' }, 'No answer was written: ' + run.note + '.'),
        run && run.evidence && run.evidence.length === 0 && !run.busy && h('div', { className: 'mt-note' }, 'Nothing in your meetings matches that. Try other words, or make sure the meeting has finished its notes.'),
        run && run.evidence && run.evidence.length > 0 && h('div', { role: 'list', 'aria-label': 'Passages found' }, run.evidence.map(ev => h('div', { key: ev.label, className: 'mt-ev', role: 'listitem' },
          h('div', { className: 'mt-row' }, h('b', null, ev.doc), ev.t_start != null && h('span', { className: 'mt-time' }, clock(ev.t_start)), h('span', { className: 'mt-pill' }, ev.sure),
            h('button', { className: 'btn', onClick: () => openEv(ev) }, 'Open')),
          h('div', { className: 'q' }, ev.text)))),
        h('div', { className: 'mt-row' },
          h('span', { className: 'mt-note' }, st.documents + (st.documents === 1 ? ' document' : ' documents') + ' from your meetings in the Library.'),
          h('button', { className: 'btn', onClick: () => choose('off') }, 'Stop making them searchable'))),
      msg && h('div', { className: 'mt-note', role: 'status' }, msg));
  }

  window.MeetingsExtras = { Pad, After, Archive, notesText };
})();
