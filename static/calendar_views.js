/* The Calendar workspace's views beyond the day: Week and Month, drawn in the owner's Google colours.
 *
 * CalendarWS (index.html, mirrored in ui_parts/app.html) keeps the Day view, the quick-add line and the prep
 * cards; this file adds what Google Calendar has that the Day view did not:
 *   - ViewSwitch: Day, Week, Month (and Free/busy once that view is here), a segmented control in the toolbar;
 *   - WeekView: seven columns on the same hour grid as the Day view, all-day entries above, overlapping events
 *     side by side;
 *   - MonthView: the month as a grid, each day with its first events and a count of the rest.
 *
 * Every event is drawn in the colour Google gives it (the event's own colour, else its calendar's), read by
 * services/calendar_views.py. Every event row carries the stage identity (fridayRow), so Friday can see,
 * point at and tick the events in every view, not only the Day view.
 *
 * Rules this file keeps: --fr-* tokens for everything but the event colours, which are the owner's data;
 * a failed read says so (never an empty week); nothing here writes anything.
 *
 * Loaded by index.html before the page script; defines window.CalendarViews.
 */
(function () {
  'use strict';
  if (window.CalendarViews) return;
  const h = (...a) => window.React.createElement(...a);
  const R = () => window.React;

  const START_HOUR = 6, END_HOUR = 24, HOUR_PX = 48;
  const WEEK_START = 0;   // Sunday, as Google Calendar shows a US week
  const VIEWS = [
    { id: 'day', label: 'Day' },
    { id: 'week', label: 'Week' },
    { id: 'month', label: 'Month' },
    { id: 'freebusy', label: 'Free/busy' },
  ];

  // ── dates ────────────────────────────────────────────────────────────────
  const pad = n => String(n).padStart(2, '0');
  const iso = d => d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  const parseDay = s => { const [y, m, d] = String(s || '').split('-').map(Number); return (y && m && d) ? new Date(y, m - 1, d) : new Date(); };
  const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
  const weekStart = d => { const x = new Date(d); x.setHours(0, 0, 0, 0); x.setDate(x.getDate() - ((x.getDay() - WEEK_START + 7) % 7)); return x; };
  const monthGridStart = d => weekStart(new Date(d.getFullYear(), d.getMonth(), 1));
  function rangeFor(view, anchorIso) {
    const a = parseDay(anchorIso);
    if (view === 'month') { const s = monthGridStart(a); return { start: iso(s), end: iso(addDays(s, 42)), days: 42 }; }
    if (view === 'week') { const s = weekStart(a); return { start: iso(s), end: iso(addDays(s, 7)), days: 7 }; }
    return { start: iso(a), end: iso(addDays(a, 1)), days: 1 };
  }
  function step(view, anchorIso, dir) {
    const a = parseDay(anchorIso);
    if (view === 'month') return iso(new Date(a.getFullYear(), a.getMonth() + dir, 1));
    return iso(addDays(a, (view === 'week' ? 7 : 1) * dir));
  }
  function title(view, anchorIso) {
    const a = parseDay(anchorIso);
    if (view === 'month') return a.toLocaleDateString([], { month: 'long', year: 'numeric' });
    if (view === 'week') {
      const s = weekStart(a), e = addDays(s, 6);
      const same = s.getMonth() === e.getMonth();
      return s.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' – ' +
        e.toLocaleDateString([], same ? { day: 'numeric' } : { month: 'short', day: 'numeric' }) + ', ' + e.getFullYear();
    }
    return a.toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' });
  }
  const timeOf = s => { const d = new Date(s); return isNaN(d) ? '' : d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }); };
  const dayKeyOf = s => { if (!s) return ''; if (String(s).length === 10) return String(s); const d = new Date(s); return isNaN(d) ? '' : iso(d); };
  // An all-day entry ends on the day after its last day (Google's exclusive end); a timed one on its end's day.
  function daysCovered(ev) {
    const s = parseDay(dayKeyOf(ev.start_time));
    let e = ev.end_time ? parseDay(dayKeyOf(ev.end_time)) : s;
    if (ev.all_day) e = addDays(e, -1);
    const out = [];
    for (let d = new Date(s), i = 0; d <= e && i < 62; d = addDays(d, 1), i++) out.push(iso(d));
    return out.length ? out : [iso(s)];
  }
  const yOf = s => { const d = new Date(s); if (isNaN(d)) return 0; return Math.max(0, (d.getHours() + d.getMinutes() / 60 - START_HOUR) * HOUR_PX); };

  // ── reading ──────────────────────────────────────────────────────────────
  function api(url, opts) {
    const tok = window.__FRIDAY_API_TOKEN || '';
    const headers = Object.assign({}, opts && opts.headers);
    if (tok) headers['X-Friday-Token'] = tok;
    return fetch(url, Object.assign({}, opts, { headers }));
  }
  function useRange(start, end, reloadKey, keys) {
    const { useState, useEffect } = R();
    const [st, setSt] = useState({ loading: true, events: [], errors: [], failed: '' });
    useEffect(() => {
      let live = true;
      // A new week or month starts empty and says it is reading; a reread of the same one keeps what is shown.
      const k = start + end + '|' + (keys || '');
      setSt(s => (s.key === k ? Object.assign({}, s, { loading: true }) : { loading: true, events: [], errors: [], failed: '', key: k }));
      api('/api/calendar/range?start=' + start + '&end=' + end + (keys ? '&keys=' + encodeURIComponent(keys) : '')).then(r => r.json().then(d => ({ ok: r.ok, d })))
        .then(({ ok, d }) => { if (live) setSt({ loading: false, events: ok ? (d.events || []) : [], errors: d.errors || [], failed: ok ? '' : (d.message || 'The calendar did not answer.'), key: k }); })
        .catch(() => { if (live) setSt({ loading: false, events: [], errors: [], failed: 'The calendar did not answer.', key: k }); });
      return () => { live = false; };
    }, [start, end, reloadKey, keys]);
    return st;
  }

  // ── pieces ───────────────────────────────────────────────────────────────
  function rowProps(ev) {
    if (!window.fridayRow || ev.media) return {};
    return window.fridayRow('event', ev.id, ev.title, {
      kind: ev.type || 'normal', all_day: !!ev.all_day, has_guests: !!(ev.attendees && ev.attendees.length),
      when: window.fridayWhen ? window.fridayWhen(ev.start_time) : '', recurring: !!ev.recurring,
    });
  }
  const colorStyle = ev => (ev.color ? { '--ev': ev.color } : {});
  // Google's own reminders for an event (the event's, else its calendar's), in words; '' when it has none.
  function reminderText(ev) {
    const pops = (ev.reminders || []).filter(r => (r.method || 'popup') === 'popup').map(r => r.minutes).sort((a, b) => a - b);
    if (!pops.length) return '';
    const w = m => m === 0 ? 'at the start' : m % 1440 === 0 ? (m / 1440) + (m === 1440 ? ' day' : ' days') : m % 60 === 0 ? (m / 60) + (m === 60 ? ' hour' : ' hours') : m + ' min';
    return 'Reminder ' + pops.map(w).join(', ') + (pops[0] === 0 && pops.length === 1 ? '' : ' before');
  }
  function eventLabel(ev) {
    const t = ev.all_day ? 'all day' : timeOf(ev.start_time) + (ev.end_time ? ' to ' + timeOf(ev.end_time) : '');
    const rem = reminderText(ev);
    const rsvp = ev.self_rsvp && !ev.organizer_self ? '. Your reply: ' + ({ accepted: 'going', tentative: 'maybe', declined: 'not going' }[ev.self_rsvp] || 'not yet') : '';
    return ev.title + ', ' + t + (ev.calendar_name ? ', ' + ev.calendar_name : '') + (rem ? '. ' + rem : '') + rsvp;
  }

  function ViewSwitch(props) {
    const views = props.views || VIEWS;
    return h('div', { className: 'calv-switch', role: 'group', 'aria-label': 'Calendar view' },
      views.map(v => h('button', {
        key: v.id, type: 'button', className: 'btn' + (props.view === v.id ? ' active' : ''),
        'aria-pressed': props.view === v.id, 'data-cal-view': v.id, onClick: () => props.onChange(v.id),
      }, v.label)));
  }

  function Nav(props) {
    return h('div', { className: 'calv-nav' },
      h('button', { type: 'button', className: 'btn', 'aria-label': 'Previous', onClick: () => props.onStep(-1) }, '‹'),
      h('button', { type: 'button', className: 'btn', onClick: props.onToday }, 'Today'),
      h('button', { type: 'button', className: 'btn', 'aria-label': 'Next', onClick: () => props.onStep(1) }, '›'),
      h('span', { className: 'calv-title' }, props.title));
  }

  function State(props) {
    return h('div', { className: 'calv-state', role: props.kind === 'failed' ? 'alert' : 'status' },
      h('div', { className: 'calv-state-1' }, props.line1),
      props.line2 ? h('div', { className: 'calv-state-2' }, props.line2) : null,
      props.action || null);
  }

  function Errors(props) {
    const errs = props.errors || [];
    if (!errs.length) return null;
    return h('div', { className: 'calv-errors', role: 'status' },
      errs.length === 1 ? errs[0].error : errs.length + ' calendars could not be read: ' + errs.map(e => e.name).join(', ') + '.');
  }

  // Where an event's block ends on its start day's grid: an end on a later day (midnight included) runs to the
  // bottom; no end means an hour.
  function endY(ev, top) {
    if (!ev.end_time) return top + HOUR_PX;
    if (dayKeyOf(ev.end_time) !== dayKeyOf(ev.start_time)) return (END_HOUR - START_HOUR) * HOUR_PX;
    return yOf(ev.end_time);
  }
  // Overlapping timed events share their column: each takes a lane, and the lanes split the width.
  function lanes(evs) {
    const sorted = evs.slice().sort((a, b) => yOf(a.start_time) - yOf(b.start_time));
    const out = [], ends = [];
    let group = [], groupEnd = -1;
    const flush = () => { const n = Math.max(1, ...group.map(g => g.lane + 1)); group.forEach(g => { g.of = n; }); group = []; };
    sorted.forEach(ev => {
      const top = yOf(ev.start_time), bot = Math.max(top + 22, endY(ev, top));
      if (top >= groupEnd) { flush(); ends.length = 0; groupEnd = -1; }
      let lane = ends.findIndex(e => e <= top);
      if (lane < 0) { lane = ends.length; ends.push(bot); } else ends[lane] = bot;
      const g = { ev, top, height: bot - top, lane, of: 1 };
      group.push(g); out.push(g); groupEnd = Math.max(groupEnd, bot);
    });
    flush();
    return out;
  }

  function TimedEvent(props) {
    const { g, onOpen } = props, ev = g.ev;
    const w = 100 / g.of;
    return h('div', Object.assign({
      key: ev.calendar_key + ev.id, className: 'cal-event calv-ev' + (ev.conflict ? ' conflict' : ''), tabIndex: 0,
      'aria-label': eventLabel(ev), 'data-cal-ev': ev.id, title: eventLabel(ev),
      style: Object.assign({ top: g.top, height: g.height, left: 'calc(' + (g.lane * w) + '% + 2px)', width: 'calc(' + w + '% - 4px)' }, colorStyle(ev)),
      onClick: () => onOpen && onOpen(ev),
      onKeyDown: e => { if (e.key === 'Enter' && onOpen) onOpen(ev); },
    }, rowProps(ev), dragProps(ev, { mode: 'week' })),
      h('div', { className: 'cal-ev-title' }, ev.title),
      g.height >= 34 ? h('div', { className: 'cal-ev-meta' }, timeOf(ev.start_time)) : null);
  }

  function HourLabels() {
    const out = [];
    for (let hr = START_HOUR; hr < END_HOUR; hr++) {
      out.push(h('div', { key: hr, className: 'calv-hour-label', style: { top: (hr - START_HOUR) * HOUR_PX } },
        hr === 12 ? '12 PM' : hr < 12 ? hr + ' AM' : (hr - 12) + ' PM'));
    }
    return h('div', { className: 'calv-hours', 'aria-hidden': 'true' }, out);
  }

  function WeekView(props) {
    const { useMemo } = R();
    const r = rangeFor('week', props.anchor);
    const data = useRange(r.start, r.end, props.reloadKey, (props.keys || []).join(','));
    const days = useMemo(() => { const s = parseDay(r.start); return [0, 1, 2, 3, 4, 5, 6].map(i => iso(addDays(s, i))); }, [r.start]);
    const today = iso(new Date());
    if (data.loading && !data.events.length) return h(State, { kind: 'loading', line1: 'Reading your week…' });
    if (data.failed) return h(State, { kind: 'failed', line1: "Couldn't read your calendar.", line2: data.failed,
      action: h('button', { className: 'btn', onClick: props.onRetry }, 'Try again') });
    const byDay = {}, allDay = {};
    days.forEach(d => { byDay[d] = []; allDay[d] = []; });
    data.events.forEach(ev => {
      if (ev.all_day) daysCovered(ev).forEach(d => { if (allDay[d]) allDay[d].push(ev); });
      else { const d = dayKeyOf(ev.start_time); if (byDay[d]) byDay[d].push(ev); }
    });
    const gridH = (END_HOUR - START_HOUR) * HOUR_PX;
    return h('div', { className: 'calv-week', 'data-cal-range': r.start },
      h(Errors, { errors: data.errors }),
      h('div', { className: 'calv-week-head' }, h('div', { className: 'calv-gutter' }),
        days.map(d => { const dt = parseDay(d); return h('button', {
          key: d, type: 'button', className: 'calv-dayhead' + (d === today ? ' today' : ''), onClick: () => props.onPickDay(d),
          'aria-label': 'Open ' + dt.toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' }),
        }, h('span', { className: 'calv-dow' }, dt.toLocaleDateString([], { weekday: 'short' })), h('span', { className: 'calv-dnum' }, dt.getDate())); })),
      h('div', { className: 'calv-week-allday' }, h('div', { className: 'calv-gutter calv-allday-label' }, 'All day'),
        days.map(d => h('div', { key: d, className: 'calv-allday-cell' }, allDay[d].map(ev => h('div', Object.assign({
          key: ev.calendar_key + ev.id, className: 'calv-chip', style: colorStyle(ev), title: eventLabel(ev), tabIndex: 0,
          'aria-label': eventLabel(ev), onClick: () => props.onOpen && props.onOpen(ev),
        }, rowProps(ev)), ev.title))))),
      h('div', { className: 'calv-week-body' },
        h('div', { className: 'calv-gutter', style: { height: gridH, position: 'relative' } }, h(HourLabels)),
        days.map(d => h('div', { key: d, className: 'calv-col' + (d === today ? ' today' : ''), style: { height: gridH }, 'data-cal-day': d },
          lanes(byDay[d]).map(g => h(TimedEvent, { key: g.ev.calendar_key + g.ev.id, g, onOpen: props.onOpen })),
          d === today ? h(NowLine) : null))));
  }

  function NowLine() {
    const { useState, useEffect } = R();
    const [now, setNow] = useState(new Date());
    useEffect(() => { const t = setInterval(() => setNow(new Date()), 60000); return () => clearInterval(t); }, []);
    if (now.getHours() < START_HOUR) return null;
    return h('div', { className: 'cal-now-line calv-now', style: { top: yOf(now.toISOString()) }, 'aria-hidden': 'true' });
  }

  const MONTH_SHOWN = 3;
  function MonthView(props) {
    const r = rangeFor('month', props.anchor);
    const data = useRange(r.start, r.end, props.reloadKey, (props.keys || []).join(','));
    const month = parseDay(props.anchor).getMonth();
    const today = iso(new Date());
    if (data.loading && !data.events.length) return h(State, { kind: 'loading', line1: 'Reading your month…' });
    if (data.failed) return h(State, { kind: 'failed', line1: "Couldn't read your calendar.", line2: data.failed,
      action: h('button', { className: 'btn', onClick: props.onRetry }, 'Try again') });
    const byDay = {};
    data.events.forEach(ev => daysCovered(ev).forEach(d => { (byDay[d] = byDay[d] || []).push(ev); }));
    Object.values(byDay).forEach(l => l.sort((a, b) => (b.all_day - a.all_day) || String(a.start_time).localeCompare(String(b.start_time))));
    const s = parseDay(r.start), cells = [];
    for (let i = 0; i < 42; i++) cells.push(iso(addDays(s, i)));
    const heads = [0, 1, 2, 3, 4, 5, 6].map(i => addDays(s, i).toLocaleDateString([], { weekday: 'short' }));
    return h('div', { className: 'calv-month', 'data-cal-range': r.start },
      h(Errors, { errors: data.errors }),
      h('div', { className: 'calv-month-head' }, heads.map(x => h('div', { key: x }, x))),
      h('div', { className: 'calv-month-grid' }, cells.map(d => {
        const evs = byDay[d] || [], dt = parseDay(d), extra = evs.length - MONTH_SHOWN;
        return h('div', { key: d, className: 'calv-cell' + (dt.getMonth() !== month ? ' other' : '') + (d === today ? ' today' : ''), 'data-cal-day': d },
          h('button', { type: 'button', className: 'calv-cell-num', onClick: () => props.onPickDay(d),
            'aria-label': 'Open ' + dt.toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' }) + (evs.length ? ', ' + evs.length + ' events' : '') }, dt.getDate()),
          evs.slice(0, MONTH_SHOWN).map(ev => h('div', Object.assign({
            key: ev.calendar_key + ev.id, className: 'calv-chip' + (ev.all_day ? ' allday' : ''), style: colorStyle(ev), tabIndex: 0,
            title: eventLabel(ev), 'aria-label': eventLabel(ev), onClick: () => props.onOpen && props.onOpen(ev),
          }, rowProps(ev)), ev.all_day ? null : h('span', { className: 'calv-chip-time' }, timeOf(ev.start_time)), ev.title)),
          extra > 0 ? h('button', { type: 'button', className: 'calv-more', onClick: () => props.onPickDay(d) }, extra + ' more') : null);
      })));
  }

  // ── moving an event: drag, or Alt+arrows; always a confirmation unless the owner allowed a batch ─────
  // A drag only PROPOSES a move. The proposal goes to MoveLayer (one per Calendar), which asks
  // "Move <title> from <when> to <when>?" and writes only on Move. A drag of less than one 15-minute step
  // proposes nothing, so a slipped click never moves a meeting. "Don't ask again for 30 minutes" is the owner's
  // batch permission: it lives in this page's memory only and runs out; each move still gets an Undo.
  const STEP_MIN = 15;
  const MOVE_EVENT = 'friday:cal-propose-move';
  let suppressClickUntil = 0;
  function canMove(ev) { return !!ev && !ev.media && !ev.all_day && ev.can_edit === true && ev.source === 'google'; }
  function shifted(isoText, days, minutes) {
    const d = new Date(isoText);
    d.setDate(d.getDate() + days);
    d.setMinutes(d.getMinutes() + minutes);
    return d;
  }
  function withOffset(d) {      // local wall time with its UTC offset, as Google takes it
    const off = -d.getTimezoneOffset(), s = off >= 0 ? '+' : '-', a = Math.abs(off);
    return iso(d) + 'T' + pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':00' + s + pad(Math.floor(a / 60)) + ':' + pad(a % 60);
  }
  function propose(ev, days, minutes) {
    if (!days && !minutes) return;
    const ns = shifted(ev.start_time, days, minutes), ne = shifted(ev.end_time || ev.start_time, days, minutes);
    window.dispatchEvent(new CustomEvent(MOVE_EVENT, { detail: { ev, start: withOffset(ns), end: withOffset(ne) } }));
  }
  function dragProps(ev, opts) {
    if (!canMove(ev)) return {};
    const o = Object.assign({ mode: 'day', hourPx: HOUR_PX }, opts || {});
    return {
      'data-cal-drag': 'on',
      'aria-description': 'Drag, or Alt and an arrow key, to move it. You confirm before anything changes.',
      onClickCapture: e => { if (Date.now() < suppressClickUntil) { e.stopPropagation(); e.preventDefault(); } },
      onKeyDownCapture: e => {
        if (!e.altKey) return;
        const map = { ArrowUp: [0, -STEP_MIN], ArrowDown: [0, STEP_MIN], ArrowLeft: [-1, 0], ArrowRight: [1, 0] };
        const m = map[e.key];
        if (!m) return;
        e.preventDefault(); e.stopPropagation();
        propose(ev, m[0], m[1]);
      },
      onPointerDown: e => {
        if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey) return;   // Ctrl/Cmd-click ticks the row
        // A press on a control or on the prep card inside the event is that control's, never a drag.
        if (e.target && e.target.closest && e.target.closest('button, a, input, select, textarea, label, [data-no-drag]')) return;
        const el = e.currentTarget, y0 = e.clientY, x0 = e.clientX, pid = e.pointerId;
        const col0 = el.closest('[data-cal-day]');
        const stepPx = o.hourPx * STEP_MIN / 60;        // a drag starts only after one whole 15-minute step
        let moving = false, minutes = 0, days = 0;
        const label = t => el.setAttribute('data-cal-drag-time', t);
        function onMove(m) {
          if (m.pointerId !== undefined && m.pointerId !== pid) return;
          if (m.buttons === 0) { end(true); return; }   // the release was lost (another window, alt-tab): abandon
          const dy = m.clientY - y0, dx = m.clientX - x0;
          const colW = col0 ? col0.getBoundingClientRect().width : Infinity;
          if (!moving && Math.abs(dy) < stepPx && !(o.mode === 'week' && Math.abs(dx) >= colW / 2)) return;
          moving = true;
          el.classList.add('calv-dragging');
          minutes = Math.trunc(dy / stepPx) * STEP_MIN;
          days = 0;
          let shiftX = 0;
          if (o.mode === 'week' && col0) {
            // The dragged block itself is under the pointer; it does not take pointer events while dragging
            // (.calv-dragging), so the column found is the one beneath it.
            const hit = document.elementsFromPoint(m.clientX, m.clientY)
              .filter(n => n !== el && !el.contains(n)).map(n => n.closest && n.closest('[data-cal-day]')).find(Boolean);
            if (hit && hit !== col0 && hit.parentNode === col0.parentNode) {
              days = Math.round((parseDay(hit.getAttribute('data-cal-day')) - parseDay(col0.getAttribute('data-cal-day'))) / 86400000);
              shiftX = hit.getBoundingClientRect().left - col0.getBoundingClientRect().left;
            }
          }
          el.style.transform = 'translate(' + shiftX + 'px,' + (minutes / 60 * o.hourPx) + 'px)';
          label(timeOf(shifted(ev.start_time, days, minutes).toISOString()));
        }
        let ended = false;
        function end(cancel) {
          if (ended) return;
          ended = true;
          window.removeEventListener('pointermove', onMove);
          window.removeEventListener('pointerup', onUp);
          window.removeEventListener('pointercancel', onCancel);
          window.removeEventListener('blur', onCancel);
          window.removeEventListener('keydown', onKey, true);
          el.classList.remove('calv-dragging');
          el.style.transform = '';
          el.removeAttribute('data-cal-drag-time');
          if (moving) suppressClickUntil = Date.now() + 400;
          if (!cancel && moving) propose(ev, days, minutes);
        }
        function onUp(u) { if (u.pointerId === undefined || u.pointerId === pid) end(false); }
        function onCancel() { end(true); }
        function onKey(k) { if (k.key === 'Escape') { k.stopPropagation(); end(true); } }
        window.addEventListener('pointermove', onMove);
        window.addEventListener('pointerup', onUp);
        window.addEventListener('pointercancel', onCancel);
        window.addEventListener('blur', onCancel);
        window.addEventListener('keydown', onKey, true);
      },
    };
  }
  const whenText = s => { const d = new Date(s); return isNaN(d) ? String(s) : d.toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }); };
  let moveGrantUntil = 0;           // the owner's batch permission, page memory only
  const GRANT_MS = 30 * 60 * 1000;

  function MoveLayer(props) {
    const { useState, useEffect, useRef } = R();
    const [pending, setPending] = useState(null);
    const [busy, setBusy] = useState(false);
    const [grant, setGrant] = useState(false);
    const [done, setDone] = useState(null);       // {text, receipt, failed}
    const cancelRef = useRef(null);
    const run = async (p) => {
      setBusy(true);
      try {
        const r = await api('/api/calendar/events/' + encodeURIComponent(p.ev.id) + '/move', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ calendar_key: p.ev.calendar_key, start_time: p.start, end_time: p.end, confirmed: true }),
        });
        const d = await r.json();
        if (r.ok && d.status === 'ok') {
          setDone({ text: 'Moved “' + p.ev.title + '” to ' + whenText(p.start) + '.', receipt: d.receipt_id });
          if (props.onMoved) props.onMoved();
        } else setDone({ text: "Couldn't move “" + p.ev.title + '”: ' + (d.message || 'Google did not take the change') + '. Nothing changed.', failed: true });
      } catch (_) {
        setDone({ text: "Couldn't reach Friday to move “" + p.ev.title + '”. Nothing changed.', failed: true });
      }
      setBusy(false);
      setPending(null);
    };
    const runRef = useRef(null);
    runRef.current = run;              // the listener below is registered once; it calls this render's run
    useEffect(() => {
      const on = e => {
        const p = e.detail;
        if (!p || !p.ev) return;
        if (Date.now() < moveGrantUntil) runRef.current(p); else { setGrant(false); setPending(p); }
      };
      window.addEventListener(MOVE_EVENT, on);
      return () => window.removeEventListener(MOVE_EVENT, on);
    }, []);
    useEffect(() => { if (pending && cancelRef.current) cancelRef.current.focus(); }, [pending]);
    useEffect(() => { if (!done) return; const t = setTimeout(() => setDone(null), done.failed ? 15000 : 10000); return () => clearTimeout(t); }, [done]);
    const undo = async () => {
      const rid = done && done.receipt;
      setDone(null);
      if (!rid) return;
      try {
        const r = await api('/api/actions/receipts/' + encodeURIComponent(rid) + '/undo', { method: 'POST' });
        setDone(r.ok ? { text: 'Put back where it was.' } : { text: "Couldn't put it back. Open the event in Google Calendar to check it.", failed: true });
        if (props.onMoved) props.onMoved();
      } catch (_) { setDone({ text: "Couldn't put it back.", failed: true }); }
    };
    const confirm = () => { if (grant) moveGrantUntil = Date.now() + GRANT_MS; run(pending); };
    const guests = pending && pending.ev.attendees && pending.ev.attendees.length;
    return h(R().Fragment, null,
      pending ? h('div', { className: 'calv-modal-back', onClick: () => !busy && setPending(null) },
        h('div', { className: 'calv-modal', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'calv-move-title',
          onClick: e => e.stopPropagation(),
          onKeyDown: e => { if (e.key === 'Escape') { e.stopPropagation(); if (!busy) setPending(null); } } },
          h('div', { id: 'calv-move-title', className: 'calv-modal-title' }, 'Move “' + pending.ev.title + '”?'),
          h('div', { className: 'calv-modal-line' }, 'From ' + whenText(pending.ev.start_time)),
          h('div', { className: 'calv-modal-line' }, 'To ' + whenText(pending.start)),
          pending.ev.calendar_name ? h('div', { className: 'calv-modal-dim' }, 'On ' + pending.ev.calendar_name) : null,
          guests ? h('div', { className: 'calv-modal-dim' }, guests + (guests === 1 ? ' guest is' : ' guests are') + ' on this event. Google will not email them about the change.') : null,
          h('label', { className: 'calv-modal-grant' }, h('input', { type: 'checkbox', checked: grant, onChange: e => setGrant(e.target.checked) }),
            ' Move without asking for the next 30 minutes'),
          h('div', { className: 'calv-modal-actions' },
            h('button', { type: 'button', className: 'btn', ref: cancelRef, onClick: () => setPending(null), disabled: busy }, 'Cancel'),
            h('button', { type: 'button', className: 'btn active', onClick: confirm, disabled: busy }, busy ? 'Moving…' : 'Move')))) : null,
      done ? h('div', { className: 'calv-undo' + (done.failed ? ' failed' : ''), role: done.failed ? 'alert' : 'status' },
        h('span', null, done.text),
        done.receipt ? h('button', { type: 'button', className: 'btn', onClick: undo }, 'Undo') : null,
        h('button', { type: 'button', className: 'btn', 'aria-label': 'Dismiss', onClick: () => setDone(null) }, '×')) : null);
  }

  // ── quick add: the owner sees what will be added, and where, before anything is written ─────────────
  function QuickAddPreview(props) {
    const { useState, useEffect } = R();
    const p = props.preview || {};
    const cals = props.calendars || [];
    const [cal, setCal] = useState(cals.length ? cals[0].key : '');
    useEffect(() => { setCal(cals.length ? cals[0].key : ''); }, [props.preview]);
    const where = cals.length ? null : h('div', { className: 'calv-modal-dim' }, 'Google Calendar cannot be written to from here, so it will be kept on this PC.');
    return h('div', { className: 'calv-preview', role: 'group', 'aria-label': 'Check the event before adding it' },
      h('div', { className: 'calv-preview-title' }, p.title),
      h('div', { className: 'calv-modal-line' }, whenText(p.start_time) + ' – ' + timeOf(p.end_time)),
      p.location ? h('div', { className: 'calv-modal-dim' }, p.location) : null,
      p.attendees && p.attendees.length ? h('div', { className: 'calv-modal-dim' }, 'Guests: ' + p.attendees.join(', ') + ' (Google will not email them)') : null,
      where,
      h('div', { className: 'calv-modal-actions' },
        cals.length ? h('label', { className: 'calv-preview-cal' }, 'Calendar ',
          h('select', { className: 'input', value: cal, onChange: e => setCal(e.target.value) },
            cals.map(c => h('option', { key: c.key, value: c.key }, c.name)))) : null,
        h('button', { type: 'button', className: 'btn', onClick: props.onCancel, disabled: props.busy }, 'Cancel'),
        h('button', { type: 'button', className: 'btn active', onClick: () => props.onAdd(cal), disabled: props.busy },
          props.busy ? 'Adding…' : 'Add to ' + ((cals.find(c => c.key === cal) || {}).name || 'this PC'))));
  }

  // ── which calendars: the owner's ticks (kept), and Friday's "only these" (a chip, never kept) ──────────
  function useCalendars(reloadKey) {
    const { useState, useEffect } = R();
    const [st, setSt] = useState({ loading: true, calendars: [], accounts: [], failed: '' });
    useEffect(() => {
      let live = true;
      api('/api/calendar/calendars').then(r => r.json().then(d => ({ ok: r.ok, d })))
        .then(({ ok, d }) => { if (live) setSt({ loading: false, calendars: ok ? (d.calendars || []) : [], accounts: d.accounts || [], failed: ok ? '' : (d.message || 'The calendar list did not answer.') }); })
        .catch(() => { if (live) setSt({ loading: false, calendars: [], accounts: [], failed: 'The calendar list did not answer.' }); });
      return () => { live = false; };
    }, [reloadKey]);
    return st;
  }
  const slug = s => String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40);

  function CalendarsPanel(props) {
    const { useState } = R();
    const [busy, setBusy] = useState('');
    const [err, setErr] = useState('');
    const cals = props.calendars || [];
    const toggle = async c => {
      setBusy(c.key); setErr('');
      try {
        const r = await api('/api/calendar/calendars/visible', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ changes: { [c.key]: !c.on } }) });
        if (!r.ok) throw new Error('refused');
        if (props.onChanged) props.onChanged();
      } catch (_) { setErr("Couldn't switch “" + c.name + '”. Nothing changed.'); }
      setBusy('');
    };
    const accounts = props.accounts || [];
    const groups = accounts.length ? accounts : [{ account_id: null, label: '' }];
    return h('div', { className: 'calv-cals', role: 'dialog', 'aria-label': 'Calendars shown in Friday',
      onKeyDown: e => { if (e.key === 'Escape' && props.onClose) { e.stopPropagation(); props.onClose(); } } },
      h('div', { className: 'calv-cals-head' }, h('span', null, 'Calendars shown in Friday'),
        h('button', { type: 'button', className: 'btn', 'aria-label': 'Close', onClick: props.onClose }, '×')),
      h('div', { className: 'calv-modal-dim' }, 'Switching one here changes Friday only, never Google.'),
      groups.map(a => h('div', { key: a.account_id || 'legacy', className: 'calv-cals-group' },
        a.label ? h('div', { className: 'calv-cals-account' }, a.label) : null,
        a.reason ? h('div', { className: 'calv-modal-dim' }, a.reason) : null,
        cals.filter(c => accounts.length ? c.account_id === a.account_id : true).map(c => h('label', { key: c.key, className: 'calv-cals-row' },
          h('input', { type: 'checkbox', checked: !!c.on, disabled: busy === c.key, onChange: () => toggle(c), 'aria-label': c.name }),
          h('span', { className: 'calv-swatch', style: { '--ev': c.color }, 'aria-hidden': 'true' }),
          h('span', { className: 'calv-cals-name' }, c.name),
          !c.google_selected && !c.primary ? h('span', { className: 'calv-modal-dim' }, ' hidden in Google') : null)))),
      err ? h('div', { className: 'calv-errors', role: 'alert' }, err) : null);
  }

  function OnlyChip(props) {
    if (!props.names || !props.names.length) return null;
    return h('span', { className: 'calv-chip-friday', role: 'status' },
      'Only ' + props.names.join(', ') + ' · by ' + (window.fridayName ? window.fridayName() : 'Friday'),
      h('button', { type: 'button', className: 'calv-chip-x', 'aria-label': 'Show every calendar again', onClick: props.onClear }, '×'));
  }

  // ── free and busy: the week as time taken and time open, calendars named, titles never ───────────────
  const durText = m => (m >= 60 ? Math.floor(m / 60) + ' h' + (m % 60 ? ' ' + (m % 60) + ' m' : '') : m + ' m');
  function FreeBusyView(props) {
    const { useState, useEffect } = R();
    const r = rangeFor('week', props.anchor);
    const [st, setSt] = useState({ loading: true, data: null, failed: '' });
    const keys = (props.keys || []).join(',');
    useEffect(() => {
      let live = true;
      setSt(s => Object.assign({}, s, { loading: true }));
      api('/api/calendar/freebusy?start=' + r.start + '&end=' + r.end + (keys ? '&keys=' + encodeURIComponent(keys) : ''))
        .then(x => x.json().then(d => ({ ok: x.ok, d })))
        .then(({ ok, d }) => { if (live) setSt({ loading: false, data: ok ? d : null, failed: ok ? '' : (d.message || 'The calendar did not answer.') }); })
        .catch(() => { if (live) setSt({ loading: false, data: null, failed: 'The calendar did not answer.' }); });
      return () => { live = false; };
    }, [r.start, keys, props.reloadKey]);
    if (st.loading && !st.data) return h(State, { kind: 'loading', line1: 'Working out your free time…' });
    if (st.failed) return h(State, { kind: 'failed', line1: "Couldn't read your calendar.", line2: st.failed,
      action: h('button', { className: 'btn', onClick: props.onRetry }, 'Try again') });
    const d = st.data, today = iso(new Date()), gridH = (END_HOUR - START_HOUR) * HOUR_PX;
    const w = d.working || {};
    return h('div', { className: 'calv-week calv-fb', 'data-cal-range': r.start },
      h(Errors, { errors: d.errors }),
      h('div', { className: 'calv-fb-sum', role: 'status' }, durText(d.free_minutes) + ' free this week inside working hours (' + w.start + '–' + w.end + '). Only calendar names are shown here, never titles.'),
      h('div', { className: 'calv-week-head' }, h('div', { className: 'calv-gutter' }),
        d.days.map(x => { const dt = parseDay(x.date); return h('button', { key: x.date, type: 'button', className: 'calv-dayhead' + (x.date === today ? ' today' : ''), onClick: () => props.onPickDay(x.date) },
          h('span', { className: 'calv-dow' }, dt.toLocaleDateString([], { weekday: 'short' })), h('span', { className: 'calv-dnum' }, dt.getDate()),
          h('span', { className: 'calv-fb-free' }, x.working ? durText(x.free_minutes) + ' free' : 'not a work day')); })),
      h('div', { className: 'calv-week-body' },
        h('div', { className: 'calv-gutter', style: { height: gridH, position: 'relative' } }, h(HourLabels)),
        d.days.map(x => h('div', { key: x.date, className: 'calv-col' + (x.date === today ? ' today' : ''), style: { height: gridH }, 'data-cal-day': x.date },
          x.free.map((f, i) => { const top = yOf(f.start), bot = Math.max(top + 14, yOf(f.end) || (END_HOUR - START_HOUR) * HOUR_PX);
            return h('div', { key: 'f' + i, className: 'calv-free', style: { top, height: bot - top }, title: 'Free ' + timeOf(f.start) + ' to ' + timeOf(f.end) }, 'Free · ' + durText(f.minutes)); }),
          x.busy.map((b, i) => { const top = yOf(b.start); const endY = String(b.end).slice(0, 10) !== x.date ? (END_HOUR - START_HOUR) * HOUR_PX : yOf(b.end);
            const bot = Math.max(top + 14, endY);
            return h('div', { key: 'b' + i, className: 'calv-busy', style: { top, height: bot - top },
              title: 'Busy ' + timeOf(b.start) + ' to ' + timeOf(b.end) + (b.calendars.length ? ' (' + b.calendars.join(', ') + ')' : '') },
              'Busy' + (b.calendars.length ? ' · ' + b.calendars.join(', ') : '')); })))));
  }

  // ── replying to an invitation: the owner's click, then a confirmation, because the organiser hears it ─────
  const RSVP_EVENT = 'friday:cal-propose-rsvp';
  const RSVP_WORDS = { accepted: 'Going', tentative: 'Maybe', declined: 'Not going', needsAction: 'Not replied' };
  function RsvpButtons(props) {
    const ev = props.ev;
    if (!ev || !ev.self_rsvp || ev.organizer_self || ev.source !== 'google') return null;
    return h('span', { className: 'calv-rsvp', role: 'group', 'aria-label': 'Your reply to ' + ev.title, onClick: e => e.stopPropagation() },
      h('span', { className: 'calv-rsvp-now' }, RSVP_WORDS[ev.self_rsvp] || ev.self_rsvp),
      ['accepted', 'tentative', 'declined'].map(r => h('button', {
        key: r, type: 'button', className: 'calv-rsvp-btn' + (ev.self_rsvp === r ? ' on' : ''), 'aria-pressed': ev.self_rsvp === r,
        disabled: ev.self_rsvp === r,
        onClick: e => { e.stopPropagation(); window.dispatchEvent(new CustomEvent(RSVP_EVENT, { detail: { ev, response: r } })); },
      }, RSVP_WORDS[r])));
  }
  function RsvpLayer(props) {
    const { useState, useEffect, useRef } = R();
    const [pending, setPending] = useState(null);
    const [busy, setBusy] = useState(false);
    const [done, setDone] = useState(null);
    const cancelRef = useRef(null);
    useEffect(() => {
      const on = e => { if (e.detail && e.detail.ev) setPending(e.detail); };
      window.addEventListener(RSVP_EVENT, on);
      return () => window.removeEventListener(RSVP_EVENT, on);
    }, []);
    useEffect(() => { if (pending && cancelRef.current) cancelRef.current.focus(); }, [pending]);
    useEffect(() => { if (!done) return; const t = setTimeout(() => setDone(null), done.failed ? 15000 : 8000); return () => clearTimeout(t); }, [done]);
    const send = async () => {
      const p = pending;
      setBusy(true);
      try {
        const r = await api('/api/calendar/events/' + encodeURIComponent(p.ev.id) + '/rsvp', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ calendar_key: p.ev.calendar_key, response: p.response, confirmed: true }),
        });
        const d = await r.json();
        if (r.ok && d.status === 'ok') { setDone({ text: 'Replied “' + RSVP_WORDS[p.response] + '” to “' + p.ev.title + '”. You can change it any time.' }); if (props.onReplied) props.onReplied(); }
        else setDone({ text: "Couldn't send the reply: " + (d.message || 'Google did not take it') + '. Nothing was sent.', failed: true });
      } catch (_) { setDone({ text: "Couldn't reach Friday to send the reply. Nothing was sent.", failed: true }); }
      setBusy(false);
      setPending(null);
    };
    return h(R().Fragment, null,
      pending ? h('div', { className: 'calv-modal-back', onClick: () => !busy && setPending(null) },
        h('div', { className: 'calv-modal', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'calv-rsvp-title', onClick: e => e.stopPropagation(),
          onKeyDown: e => { if (e.key === 'Escape') { e.stopPropagation(); if (!busy) setPending(null); } } },
          h('div', { id: 'calv-rsvp-title', className: 'calv-modal-title' }, 'Reply “' + RSVP_WORDS[pending.response] + '” to “' + pending.ev.title + '”?'),
          h('div', { className: 'calv-modal-line' }, whenText(pending.ev.start_time)),
          h('div', { className: 'calv-modal-dim' }, 'Google will let the organiser know.'),
          h('div', { className: 'calv-modal-actions' },
            h('button', { type: 'button', className: 'btn', ref: cancelRef, onClick: () => setPending(null), disabled: busy }, 'Cancel'),
            h('button', { type: 'button', className: 'btn active', onClick: send, disabled: busy }, busy ? 'Sending…' : 'Send reply')))) : null,
      done ? h('div', { className: 'calv-undo' + (done.failed ? ' failed' : ''), role: done.failed ? 'alert' : 'status' },
        h('span', null, done.text), h('button', { type: 'button', className: 'btn', 'aria-label': 'Dismiss', onClick: () => setDone(null) }, '×')) : null);
  }

  // ── styles (tokens only; the event colour arrives as --ev) ─────────────────
  const CSS = `
.calv-toolbar{position:relative;display:flex;align-items:center;gap:var(--fr-gap,8px);flex-wrap:wrap;margin:0 0 10px}
.calv-switch{display:inline-flex;gap:4px}
.calv-nav{display:inline-flex;align-items:center;gap:4px;margin-left:auto}
.calv-title{font-family:var(--fr-font-body);font-weight:600;color:var(--fr-text);margin-left:8px;font-size:var(--fr-text-md,13px)}
.calv-state{text-align:center;padding:40px 16px}
.calv-state-1{color:var(--fr-text);font-weight:500}
.calv-state-2{color:var(--fr-dim);margin-top:4px;font-size:var(--fr-text-sm,12px)}
.calv-errors{color:var(--fr-dim);font-size:var(--fr-text-sm,12px);margin:0 0 8px}
.calv-week{container-type:inline-size}
.calv-week-head,.calv-week-allday,.calv-week-body{display:grid;grid-template-columns:54px repeat(7,minmax(0,1fr));gap:2px}
.calv-dayhead{background:none;border:0;color:var(--fr-dim);padding:4px 0;cursor:pointer;display:flex;flex-direction:column;align-items:center;border-radius:6px}
.calv-dayhead:focus-visible,.calv-cell-num:focus-visible,.calv-chip:focus-visible,.calv-ev:focus-visible{box-shadow:var(--fr-focus);outline:none}
.calv-dayhead.today .calv-dnum{color:var(--fr-cyan)}
.calv-dow{font-size:var(--fr-text-2xs,10px);text-transform:uppercase;letter-spacing:.06em}
.calv-dnum{font-size:16px;color:var(--fr-text)}
.calv-allday-label{font-size:var(--fr-text-2xs,10px);color:var(--fr-dim);align-self:center;text-align:right;padding-right:6px}
.calv-allday-cell{min-height:20px;display:flex;flex-direction:column;gap:2px}
.calv-week-body{max-height:62vh;overflow-y:auto;position:relative}
.calv-hours{position:relative}
.calv-hour-label{position:absolute;right:6px;transform:translateY(-6px);font-size:var(--fr-text-2xs,10px);color:var(--fr-faint,var(--fr-dim));font-family:var(--fr-font-mono,monospace)}
.calv-col{position:relative;border-left:1px solid var(--fr-glass-edge);background:repeating-linear-gradient(to bottom,transparent 0,transparent ${HOUR_PX - 1}px,var(--fr-glass-edge) ${HOUR_PX - 1}px,var(--fr-glass-edge) ${HOUR_PX}px)}
.calv-col.today{background-color:color-mix(in srgb,var(--fr-cyan) 4%,transparent)}
.calv-ev{position:absolute;right:auto;box-sizing:border-box;padding:3px 6px;border-radius:6px;overflow:hidden;min-height:20px;cursor:pointer}
.calv-ev .cal-ev-title{white-space:normal}
.cal-event[style*="--ev"]{background:color-mix(in srgb,var(--ev) 30%,var(--fr-surface))}
.calv-now{left:0;right:0}
.calv-chip{font-size:11px;line-height:16px;padding:1px 6px;border-radius:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;cursor:pointer;color:var(--fr-text);border-left:3px solid var(--ev,var(--fr-cyan));background:color-mix(in srgb,var(--ev,var(--fr-cyan)) 22%,transparent)}
.calv-chip.allday{background:color-mix(in srgb,var(--ev,var(--fr-cyan)) 55%,transparent)}
.calv-chip-time{color:var(--fr-dim);margin-right:4px;font-family:var(--fr-font-mono,monospace);font-size:10px}
.calv-month{container-type:inline-size}
.calv-month-head,.calv-month-grid{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:2px}
.calv-month-head div{font-size:var(--fr-text-2xs,10px);color:var(--fr-dim);text-transform:uppercase;letter-spacing:.06em;padding:2px 6px}
.calv-cell{min-height:96px;border:1px solid var(--fr-glass-edge);border-radius:6px;padding:3px;display:flex;flex-direction:column;gap:2px;min-width:0}
.calv-cell.other{opacity:.5}
.calv-cell.today{border-color:var(--fr-cyan)}
.calv-cell-num{align-self:flex-start;background:none;border:0;color:var(--fr-text);cursor:pointer;font-size:12px;padding:1px 5px;border-radius:6px}
.calv-cell.today .calv-cell-num{color:var(--fr-cyan);font-weight:600}
.calv-more{background:none;border:0;color:var(--fr-dim);font-size:11px;text-align:left;cursor:pointer;padding:0 6px}
.calv-dragging{opacity:.85;z-index:20;cursor:grabbing;box-shadow:var(--fr-focus);pointer-events:none}
[data-cal-drag="on"]{touch-action:none}
.calv-dragging[data-cal-drag-time]::after{content:attr(data-cal-drag-time);position:absolute;right:4px;top:2px;font-family:var(--fr-font-mono,monospace);font-size:10px;color:var(--fr-text)}
.calv-modal-back{position:fixed;inset:0;z-index:2147483000;display:flex;align-items:center;justify-content:center;background:color-mix(in srgb,var(--fr-surface) 55%,transparent)}
.calv-modal,.calv-preview{background:var(--fr-glass);backdrop-filter:blur(var(--fr-glass-blur,12px));border:1px solid var(--fr-glass-edge);border-radius:var(--fr-radius,10px);padding:16px;min-width:300px;max-width:440px;color:var(--fr-text)}
.calv-preview{max-width:none;margin:0 0 12px}
.calv-modal-title,.calv-preview-title{font-weight:600;margin-bottom:6px}
.calv-modal-line{font-size:var(--fr-text-md,13px)}
.calv-modal-dim{font-size:var(--fr-text-sm,12px);color:var(--fr-dim);margin-top:4px}
.calv-modal-grant{display:flex;gap:6px;align-items:center;font-size:var(--fr-text-sm,12px);color:var(--fr-dim);margin-top:10px}
.calv-modal-actions{display:flex;gap:8px;justify-content:flex-end;align-items:center;margin-top:12px;flex-wrap:wrap}
.calv-preview-cal{font-size:var(--fr-text-sm,12px);color:var(--fr-dim);margin-right:auto}
.calv-undo{position:sticky;bottom:8px;display:flex;gap:8px;align-items:center;margin-top:10px;padding:8px 12px;border-radius:var(--fr-radius,10px);background:var(--fr-glass);border:1px solid var(--fr-glass-edge);color:var(--fr-text);font-size:var(--fr-text-sm,12px)}
.calv-undo.failed{border-color:var(--fr-error)}
.calv-undo span{flex:1}
.cal-ev-remind{color:var(--fr-dim)}
.calv-cals{position:absolute;right:0;top:44px;z-index:30;background:var(--fr-glass);backdrop-filter:blur(var(--fr-glass-blur,12px));border:1px solid var(--fr-glass-edge);border-radius:var(--fr-radius,10px);padding:12px;min-width:280px;max-height:60vh;overflow:auto;color:var(--fr-text)}
.calv-cals-head{display:flex;justify-content:space-between;align-items:center;font-weight:600}
.calv-cals-account{font-size:var(--fr-text-2xs,10px);letter-spacing:var(--fr-track-label,.08em);color:var(--fr-dim);margin:10px 0 4px}
.calv-cals-row{display:flex;align-items:center;gap:8px;min-height:var(--fr-row-h-dense,28px);cursor:pointer}
.calv-swatch{width:12px;height:12px;border-radius:3px;background:var(--ev,var(--fr-cyan));flex:none}
.calv-cals-name{flex:1}
.calv-chip-friday{display:inline-flex;align-items:center;gap:6px;font-size:var(--fr-text-sm,12px);padding:2px 4px 2px 10px;border-radius:999px;border:1px solid var(--fr-cyan);color:var(--fr-text)}
.calv-chip-x{background:none;border:0;color:var(--fr-dim);cursor:pointer;min-width:24px;min-height:24px}
.calv-fb-sum{font-size:var(--fr-text-sm,12px);color:var(--fr-dim);margin:0 0 8px}
.calv-fb-free{font-size:10px;color:var(--fr-dim)}
.calv-free,.calv-busy{position:absolute;left:2px;right:2px;box-sizing:border-box;border-radius:6px;font-size:10px;padding:2px 6px;overflow:hidden}
.calv-free{border:1px dashed var(--fr-glass-edge);color:var(--fr-dim)}
.calv-busy{background:color-mix(in srgb,var(--fr-neutral) 35%,transparent);border-left:3px solid var(--fr-neutral);color:var(--fr-text)}
.calv-rsvp{display:inline-flex;align-items:center;gap:4px;flex-wrap:wrap}
.calv-rsvp-now{color:var(--fr-dim)}
.calv-rsvp-btn{background:none;border:1px solid var(--fr-glass-edge);border-radius:6px;color:var(--fr-text);font-size:10px;padding:1px 6px;cursor:pointer;min-height:20px}
.calv-rsvp-btn.on{border-color:var(--fr-cyan);color:var(--fr-cyan);cursor:default}
.calv-rsvp-btn:focus-visible{box-shadow:var(--fr-focus);outline:none}
@container (max-width:719px){.calv-cell{min-height:64px}.calv-chip-time{display:none}}
`;
  function injectStyle() {
    if (document.getElementById('calv-style')) return;
    const st = document.createElement('style');
    st.id = 'calv-style';
    st.textContent = CSS;
    document.head.appendChild(st);
  }
  if (document.head) injectStyle(); else document.addEventListener('DOMContentLoaded', injectStyle);

  window.CalendarViews = { RsvpButtons, RsvpLayer, VIEWS, ViewSwitch, Nav, WeekView, MonthView, FreeBusyView, CalendarsPanel, OnlyChip, useCalendars, slug, State, Errors, useRange, reminderText, MoveLayer, QuickAddPreview, dragProps, canMove, withOffset, MOVE_EVENT, rangeFor, step, title, iso, parseDay, addDays, weekStart, daysCovered, lanes, START_HOUR, END_HOUR, HOUR_PX };
})();
