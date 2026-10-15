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
  function useRange(start, end, reloadKey) {
    const { useState, useEffect } = R();
    const [st, setSt] = useState({ loading: true, events: [], errors: [], failed: '' });
    useEffect(() => {
      let live = true;
      // A new week or month starts empty and says it is reading; a reread of the same one keeps what is shown.
      setSt(s => (s.key === start + end ? Object.assign({}, s, { loading: true }) : { loading: true, events: [], errors: [], failed: '', key: start + end }));
      api('/api/calendar/range?start=' + start + '&end=' + end).then(r => r.json().then(d => ({ ok: r.ok, d })))
        .then(({ ok, d }) => { if (live) setSt({ loading: false, events: ok ? (d.events || []) : [], errors: d.errors || [], failed: ok ? '' : (d.message || 'The calendar did not answer.'), key: start + end }); })
        .catch(() => { if (live) setSt({ loading: false, events: [], errors: [], failed: 'The calendar did not answer.', key: start + end }); });
      return () => { live = false; };
    }, [start, end, reloadKey]);
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
  function eventLabel(ev) {
    const t = ev.all_day ? 'all day' : timeOf(ev.start_time) + (ev.end_time ? ' to ' + timeOf(ev.end_time) : '');
    return ev.title + ', ' + t + (ev.calendar_name ? ', ' + ev.calendar_name : '');
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
    }, rowProps(ev)),
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
    const data = useRange(r.start, r.end, props.reloadKey);
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
    const data = useRange(r.start, r.end, props.reloadKey);
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

  // ── styles (tokens only; the event colour arrives as --ev) ─────────────────
  const CSS = `
.calv-toolbar{display:flex;align-items:center;gap:var(--fr-gap,8px);flex-wrap:wrap;margin:0 0 10px}
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

  window.CalendarViews = { VIEWS, ViewSwitch, Nav, WeekView, MonthView, State, Errors, useRange, rangeFor, step, title, iso, parseDay, addDays, weekStart, daysCovered, lanes, START_HOUR, END_HOUR, HOUR_PX };
})();
