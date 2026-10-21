/* Settings > Privacy > "When a cloud provider fails" (amendment A6,
   services/provider_fallback.py).

   Friday never moves from one cloud provider to another on her own. With
   "Ask me each time" (the default) a failure is shown with the other provider
   offered as a button. An order chosen here is the owner's advance permission
   to move along it; even then every move is announced.

   The choice is saved through POST /api/models/cloud-fallback-order, which
   accepts only a press on Friday's own page. Plain React.createElement, so the
   same file serves index.html and ui_parts/app.html. */
(function (root) {
  'use strict';
  if (!root) return;
  var CHOICES = [
    ['ask', 'Ask me each time (recommended)'],
    ['anthropic_then_openai', 'Anthropic first, then my OpenAI-compatible provider (e.g. OpenRouter)'],
    ['openai_then_anthropic', 'My OpenAI-compatible provider first, then Anthropic'],
  ];

  function FridayCloudFallbackOrder() {
    var R = root.React;
    var st = R.useState(''), order = st[0], setOrder = st[1];
    var ms = R.useState(''), msg = ms[0], setMsg = ms[1];
    R.useEffect(function () {
      var alive = true;
      (root.apiFetch || root.fetch)('/api/models/cloud-fallback-order')
        .then(function (r) { return r.json(); })
        .then(function (d) { if (alive && d && d.order) setOrder(d.order); })
        .catch(function () {});
      return function () { alive = false; };
    }, []);
    function choose(e) {
      var v = e.target.value, before = order;
      setOrder(v); setMsg('Saving…');
      (root.apiFetch || root.fetch)('/api/models/cloud-fallback-order', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ order: v }),
      }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (x) {
          if (x.ok && x.d && x.d.order === v) setMsg('Saved ✓');
          else { setOrder(before); setMsg((x.d && x.d.error) || 'Not saved.'); }
        })
        .catch(function () { setOrder(before); setMsg('Not saved.'); });
    }
    return R.createElement('div', { 'data-setting-key': 'model_routing.cloud_fallback_order' },
      R.createElement('select', {
        className: 'input', 'aria-label': 'When a cloud provider fails',
        value: order, onChange: choose, disabled: !order, style: { maxWidth: 520 },
      }, CHOICES.map(function (c) { return R.createElement('option', { key: c[0], value: c[0] }, c[1]); })),
      msg ? R.createElement('div', { style: { fontSize: 10, marginTop: 6, color: 'var(--st-dim)' } }, msg) : null);
  }

  root.FridayCloudFallbackOrder = FridayCloudFallbackOrder;
})(typeof window !== 'undefined' ? window : null);
