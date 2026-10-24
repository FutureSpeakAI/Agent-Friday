/* Settings > Privacy > "Folder grants" (services/path_policy.py, BD-26).

   Friday's file tools read and write only inside the owner's home folder (not its
   application data or Friday's own state folder), her output folders, and the folders
   listed here. Adding a folder is a press on this page (the server refuses it from
   anywhere else); removing one is always allowed. Plain React.createElement, so the
   same file serves index.html and ui_parts/app.html. */
(function (root) {
  'use strict';
  if (!root) return;

  function FridayFolderGrants() {
    var R = root.React;
    var rs = R.useState([]), rows = rs[0], setRows = rs[1];
    var ps = R.useState(''), path = ps[0], setPath = ps[1];
    var ws = R.useState(false), write = ws[0], setWrite = ws[1];
    var ds = R.useState(30), days = ds[0], setDays = ds[1];
    var ms = R.useState(''), msg = ms[0], setMsg = ms[1];
    var api = root.apiFetch || root.fetch;

    function load() {
      api('/api/files/folder-grants').then(function (r) { return r.json(); })
        .then(function (d) { setRows((d && d.grants) || []); }).catch(function () {});
    }
    R.useEffect(load, []);

    function add() {
      setMsg('Saving\u2026');
      api('/api/files/folder-grants', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: path, ops: write ? ['read', 'write'] : ['read'], days: days, purpose: 'Allowed in Settings' }) })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d && d.status === 'ok') { setMsg('Allowed \u2713'); setPath(''); load(); }
          else setMsg((d && d.error) || 'Not allowed.');
        }).catch(function () { setMsg('Not saved.'); });
    }

    function revoke(id) {
      api('/api/files/folder-grants/' + encodeURIComponent(id), { method: 'DELETE' })
        .then(function () { load(); }).catch(function () {});
    }

    var list = rows.length ? R.createElement('ul', { style: { margin: '6px 0', paddingLeft: 18 } }, rows.map(function (g) {
      var left = Math.max(0, Math.round(((g.expires_at || 0) * 1000 - Date.now()) / 86400000));
      return R.createElement('li', { key: g.id, style: { fontSize: 12, marginBottom: 4 } },
        g.root + ' \u2014 ' + (g.ops || []).join(' and ') + ', ' + left + ' days left ',
        R.createElement('button', { type: 'button', className: 'settings-btn', style: { fontSize: 11, marginLeft: 6 },
          'aria-label': 'Stop allowing ' + g.root, onClick: function () { revoke(g.id); } }, 'Remove'));
    })) : R.createElement('div', { style: { fontSize: 12, color: 'var(--st-dim)' } }, 'No extra folders.');

    return R.createElement('div', { 'data-folder-grants': '1' }, list,
      R.createElement('div', { style: { display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap', marginTop: 6 } },
        R.createElement('input', { className: 'input', type: 'text', value: path, placeholder: 'Full folder path, for example D:\\Projects',
          'aria-label': 'Folder to grant', onChange: function (e) { setPath(e.target.value); }, style: { flex: 1, minWidth: 220 } }),
        R.createElement('label', { style: { fontSize: 12 } },
          R.createElement('input', { type: 'checkbox', checked: write, 'aria-label': 'Grant writing too',
            onChange: function (e) { setWrite(!!e.target.checked); } }), ' grant writing too'),
        R.createElement('select', { className: 'input', value: days, 'aria-label': 'Grant for how long',
          onChange: function (e) { setDays(Number(e.target.value)); } },
          [7, 30, 90].map(function (n) { return R.createElement('option', { key: n, value: n }, n + ' days'); })),
        R.createElement('button', { type: 'button', className: 'settings-btn', disabled: !path.trim(), onClick: add }, 'Allow')),
      msg ? R.createElement('div', { style: { fontSize: 10, marginTop: 6, color: 'var(--st-dim)' } }, msg) : null);
  }

  root.FridayFolderGrants = FridayFolderGrants;
})(typeof window !== 'undefined' ? window : null);
