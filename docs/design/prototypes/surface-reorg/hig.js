// Agent Friday™ HIG prototypes: shared behaviour. Design prototype, not product code.
// Everything here is keyboard-complete and holds no data of the owner's.

// The triad gradient every outline icon strokes with (BRAND.md "Iconography").
(function defs() {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('width', '0'); svg.setAttribute('height', '0'); svg.style.position = 'absolute';
  svg.innerHTML = '<defs><linearGradient id="triad" x1="0" y1="0" x2="1" y2="1">' +
    '<stop offset="0" stop-color="#00d4ff"/><stop offset=".5" stop-color="#7b61ff"/><stop offset="1" stop-color="#ff00ff"/></linearGradient></defs>';
  document.addEventListener('DOMContentLoaded', () => document.body.prepend(svg));
})();

// Outline icons, 24-box, one idea each.
const ICONS = {
  news: '<path d="M4 5h13v14H4z"/><path d="M17 8h3v9a2 2 0 0 1-2 2"/><path d="M7 9h7M7 12h7M7 15h4"/>',
  messages: '<path d="M4 6h16v10H8l-4 3z"/>',
  calendar: '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M8 3v4M16 3v4"/>',
  people: '<circle cx="9" cy="9" r="3"/><path d="M3 19c0-3 3-5 6-5s6 2 6 5"/><circle cx="17" cy="8" r="2.5"/><path d="M15 14c3 0 6 2 6 5"/>',
  career: '<rect x="3" y="8" width="18" height="12" rx="2"/><path d="M9 8V6h6v2M3 13h18"/>',
  code: '<path d="M8 8l-4 4 4 4M16 8l4 4-4 4M13 6l-2 12"/>',
  sites: '<circle cx="12" cy="12" r="8"/><path d="M4 12h16M12 4c3 3 3 13 0 16M12 4c-3 3-3 13 0 16"/>',
  media: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M10 9l5 3-5 3z"/>',
  knowledge: '<circle cx="12" cy="12" r="2"/><circle cx="5" cy="7" r="1.5"/><circle cx="19" cy="7" r="1.5"/><circle cx="7" cy="18" r="1.5"/><circle cx="18" cy="17" r="1.5"/><path d="M6.3 8l4 3M17.7 8l-4 3M8 17l3-3.5M17 16l-3.5-3"/>',
  routines: '<circle cx="12" cy="12" r="8"/><path d="M12 7v5l3 2"/>',
  activity: '<path d="M3 12h4l3-7 4 14 3-7h4"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/>',
  search: '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
  chat: '<path d="M4 5h16v11H9l-5 4z"/>',
  bell: '<path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4zM10 20a2 2 0 0 0 4 0"/>',
  more: '<circle cx="6" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="18" cy="12" r="1.2"/>',
  compose: '<path d="M4 20h4l11-11-4-4L4 16z"/><path d="M13 7l4 4"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-2 6"/><path d="M20 4v7h-7"/>',
  inspector: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M15 5v14"/>',
  inbox: '<path d="M4 13l2-8h12l2 8v6H4z"/><path d="M4 13h5l1 2h4l1-2h5"/>',
  star: '<path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z"/>',
  send: '<path d="M4 12l16-8-6 16-2-6z"/>',
  draft: '<path d="M6 3h9l4 4v14H6z"/><path d="M9 12h6M9 16h6"/>',
  clock: '<circle cx="12" cy="12" r="8"/><path d="M12 7v5l3 2"/>',
  mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
  check: '<path d="M5 12l4 4L19 6"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  max: '<path d="M14 4h6v6M10 20H4v-6M20 4l-7 7M4 20l7-7"/>',
  tab: '<path d="M14 4h6v6M20 4l-9 9"/><path d="M19 14v5H5V5h5"/>',
  snapL: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 12h9M12 5v14"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  shield: '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/>',
  key: '<circle cx="8" cy="14" r="4"/><path d="M11 11l9-9M16 6l3 3M14 8l2 2"/>',
  wallet: '<rect x="3" y="7" width="18" height="12" rx="2"/><path d="M3 11h18M16 15h2"/>',
  brain: '<path d="M9 4a3 3 0 0 0-3 3v1a3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 3 2h1V4zM15 4a3 3 0 0 1 3 3v1a3 3 0 0 1 2 5 3 3 0 0 1-2 5 3 3 0 0 1-3 2h-1V4z"/>',
  voice: '<path d="M4 10v4M8 7v10M12 4v16M16 7v10M20 10v4"/>',
  eye: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  plug: '<path d="M9 3v5M15 3v5M6 8h12v3a6 6 0 0 1-12 0zM12 17v4"/>',
  devices: '<rect x="3" y="5" width="13" height="10" rx="1.5"/><path d="M6 19h7M9.5 15v4"/><rect x="17" y="9" width="4" height="10" rx="1"/>',
  health: '<path d="M3 12h4l2-5 3 10 3-7 1.5 2H21"/>',
  wrench: '<path d="M14 4a5 5 0 0 0 6 6l-9 9a2 2 0 0 1-3-3l9-9z"/>',
  info: '<circle cx="12" cy="12" r="8"/><path d="M12 11v5M12 8h.01"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/>',
  sparkle: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>',
  play: '<path d="M8 5l11 7-11 7z"/>',
  file: '<path d="M6 3h9l4 4v14H6z"/>',
  book: '<path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z"/><path d="M4 19V5"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/>',
};
function icon(name, cls) {
  return '<svg class="' + (cls || '') + '" viewBox="0 0 24 24" aria-hidden="true">' + (ICONS[name] || '') + '</svg>';
}

function protoNav(current) {
  const pages = [['index.html', 'Overview'], ['desktop.html', 'Desktop chrome'], ['workspace.html', 'Workspace frame'], ['settings.html', 'Settings']];
  const bar = document.createElement('div');
  bar.className = 'proto-bar';
  bar.innerHTML = '<span class="tag">Prototype</span><span>Agent Friday™ HIG · design prototype, not product code · fictional data</span>' +
    '<nav>' + pages.map(([h, l]) => '<a href="' + h + '"' + (h === current ? ' aria-current="page"' : '') + '>' + l + '</a>').join('') + '</nav>';
  document.body.prepend(bar);
}

function toast(msg, err) {
  let t = document.querySelector('.toast');
  if (!t) { t = document.createElement('div'); t.className = 'toast glass'; document.body.appendChild(t); }
  t.textContent = msg; t.classList.toggle('err', !!err); t.classList.add('show');
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove('show'), err ? 15000 : 4000);
}

// The command palette: actions of the focused workspace first, then workspaces,
// then settings rows, then approvals. Ctrl+K opens it anywhere; Esc closes.
function installPalette(getItems) {
  const wrap = document.createElement('div');
  wrap.className = 'palette-wrap';
  wrap.innerHTML = '<div class="palette glass" role="dialog" aria-label="Command palette">' +
    '<input type="text" placeholder="Type a command, a workspace, a setting, or a person…" aria-label="Search commands">' +
    '<ul role="listbox"></ul></div>';
  document.body.appendChild(wrap);
  const input = wrap.querySelector('input'), list = wrap.querySelector('ul');
  let sel = 0, flat = [];
  function render() {
    const q = input.value.trim().toLowerCase();
    const groups = getItems();
    list.innerHTML = ''; flat = [];
    for (const g of groups) {
      const items = g.items.filter(i => !q || (i.label + ' ' + (i.hint || '')).toLowerCase().includes(q));
      if (!items.length) continue;
      const gl = document.createElement('li'); gl.className = 'g label-caps'; gl.textContent = g.name; list.appendChild(gl);
      for (const i of items.slice(0, q ? 8 : 6)) {
        const li = document.createElement('li'); li.className = 'it'; li.setAttribute('role', 'option');
        li.innerHTML = icon(i.icon || 'sparkle') + '<span>' + i.label + '</span>' + (i.hint ? '<span class="hint">' + i.hint + '</span>' : '') +
          (i.keys ? '<span class="k">' + i.keys.map(k => '<kbd>' + k + '</kbd>').join('') + '</span>' : '');
        li.addEventListener('click', () => { close(); i.run && i.run(); });
        list.appendChild(li); flat.push(li);
      }
    }
    sel = 0; mark();
  }
  function mark() { flat.forEach((li, i) => li.setAttribute('aria-selected', i === sel ? 'true' : 'false')); if (flat[sel]) flat[sel].scrollIntoView({ block: 'nearest' }); }
  function open() { wrap.classList.add('open'); input.value = ''; render(); input.focus(); }
  function close() { wrap.classList.remove('open'); }
  input.addEventListener('input', render);
  input.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown') { sel = Math.min(sel + 1, flat.length - 1); mark(); e.preventDefault(); }
    else if (e.key === 'ArrowUp') { sel = Math.max(sel - 1, 0); mark(); e.preventDefault(); }
    else if (e.key === 'Enter') { flat[sel] && flat[sel].click(); }
    else if (e.key === 'Escape') close();
  });
  wrap.addEventListener('click', e => { if (e.target === wrap) close(); });
  document.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); wrap.classList.contains('open') ? close() : open(); }
    else if (e.key === 'Escape' && wrap.classList.contains('open')) close();
  });
  return { open, close };
}

// List keys shared by every list (HIG §4.4): ↑ ↓ j k move, Enter opens, Esc backs out.
function installListKeys(listEl, onOpen) {
  listEl.addEventListener('keydown', e => {
    const rows = Array.from(listEl.querySelectorAll('.row'));
    let i = rows.findIndex(r => r.getAttribute('aria-selected') === 'true');
    if (e.key === 'ArrowDown' || e.key === 'j') { i = Math.min(i + 1, rows.length - 1); }
    else if (e.key === 'ArrowUp' || e.key === 'k') { i = Math.max(i - 1, 0); }
    else if (e.key === 'Enter') { onOpen && onOpen(rows[i]); return; }
    else return;
    e.preventDefault();
    rows.forEach((r, j) => r.setAttribute('aria-selected', j === i ? 'true' : 'false'));
    rows[i] && rows[i].focus();
    rows[i] && rows[i].dispatchEvent(new CustomEvent('select', { bubbles: true }));
  });
}
