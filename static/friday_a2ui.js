// The desktop's A2UI draw step (contract draft 3, section 4): it draws the tree the PC already bound and
// checked (services/a2ui_render.render + apply_customization), the same tree the phone draws. It never
// validates, fetches data or follows a link: every string is escaped, the only elements are div, p, h2,
// section, table parts, ul/li, hr and button, and a verb or nav button only carries its id for the shell.
// tests/ui/a2ui_draw.test.cjs runs it over every tree in tests/fixtures/a2ui/conformance_v1.json.
(function () {
  'use strict';
  var ACCENT = /^#[0-9a-fA-F]{6}$/;
  var REFUSED = "This view could not be shown. Friday's standard view is used instead.";

  function e(v) {
    return String(v == null ? '' : v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function cell(v) {
    if (v == null) return '';
    if (typeof v === 'object') return e(JSON.stringify(v));
    return e(v);
  }

  function table(n) {
    var cols = Array.isArray(n.columns) ? n.columns : [];
    var rows = Array.isArray(n.rows) ? n.rows : [];
    var head = cols.map(function (c) { return '<th>' + e(c) + '</th>'; }).join('');
    var body = rows.map(function (r) {
      return '<tr>' + cols.map(function (c) { return '<td>' + cell(r && r[c]) + '</td>'; }).join('') + '</tr>';
    }).join('');
    var empty = rows.length ? '' : '<p data-a2ui-empty>Nothing here yet.</p>';
    return '<table data-a2ui="record_table"><thead><tr>' + head + '</tr></thead><tbody>' + body +
      '</tbody></table>' + empty;
  }

  function one(n) {
    var t = n.type;
    var kids = (n.children || []).map(one).join('');
    switch (t) {
      case 'column': case 'row': return '<div data-a2ui="' + t + '">' + kids + '</div>';
      case 'divider': return '<hr data-a2ui="divider">';
      case 'status_block':
        return '<section data-a2ui="status_block"><p data-a2ui-title>' + e(n.title) + '</p>' + kids + '</section>';
      case 'text_block':
        if (n.style === 'heading') return '<h2 data-a2ui="text_block">' + e(n.text) + '</h2>';
        return '<p data-a2ui="text_block" data-style="' + e(n.style) + '">' + e(n.text) + '</p>';
      case 'record_table': return table(n);
      case 'key_values':
        return '<ul data-a2ui="key_values">' + (Array.isArray(n.rows) ? n.rows : []).map(function (r) {
          return '<li>' + cell(r && r.key) + ': ' + cell(r && r.value) + '</li>';
        }).join('') + '</ul>';
      case 'verb_button':
        return '<button type="button" data-a2ui="verb_button" data-verb="' + e(n.verb) + '">' + e(n.label) + '</button>';
      case 'nav_link':
        return '<button type="button" data-a2ui="nav_link" data-nav="' + e(n.destination) + '">' + e(n.label) + '</button>';
      case 'approval_slot':
        return '<div data-shell-slot="approval">' + (n.cards || []).map(function (c) {
          return '<div data-shell-card="approval" data-approval-id="' + e(c.approval_id) + '"></div>';
        }).join('') + '</div>';
      default: {
        var body = {};
        Object.keys(n).sort().forEach(function (k) {
          if (['type', 'id', 'a11y', 'children'].indexOf(k) < 0) body[k] = n[k];
        });
        return '<div data-a2ui="' + e(t) + '"><pre data-a2ui-data>' + e(JSON.stringify(body)) + '</pre></div>';
      }
    }
  }

  function toHtml(tree, look) {
    look = look || {};
    var attrs = ' data-density="' + (look.density === 'compact' ? 'compact' : 'comfortable') + '"';
    if (typeof look.accent === 'string' && ACCENT.test(look.accent)) attrs += ' data-accent="' + look.accent + '"';
    var drawn = (tree || []).map(one).join('');
    if (look.summary) {
      // the summary is the shell's plain text, drawn under the view's first heading
      var at = drawn.indexOf('</h2>');
      var line = '<p data-a2ui-summary>' + e(look.summary) + '</p>';
      drawn = at >= 0 ? drawn.slice(0, at + 5) + line + drawn.slice(at + 5) : line + drawn;
    }
    return '<div data-a2ui-view' + attrs + '>' + drawn + '</div>';
  }

  function refusedHtml() { return '<div data-a2ui-view><p data-a2ui="text_block">' + e(REFUSED) + '</p></div>'; }

  function mount(el, payload) {
    el.innerHTML = payload && payload.ok ? toHtml(payload.tree, payload.look) : refusedHtml();
    if (payload && payload.look && ACCENT.test(payload.look.accent || '')) {
      el.style.setProperty('--a2ui-accent', payload.look.accent);
    }
  }

  window.FridayA2ui = { toHtml: toHtml, refusedHtml: refusedHtml, mount: mount };
}());
