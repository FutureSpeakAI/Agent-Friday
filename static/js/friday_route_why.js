/* Why this reply was answered where it was (north star 12.3,
   services/route_record.py).

   Every reply carries `route`: the seat, the router's reason, what was not
   chosen and why, the expected cost, how much was sent, the fallback policy and
   the time from routing to reply. The message badge shows a small "why"; one
   press opens the whole record. Nothing here calls the server.

   Plain React.createElement, so the same file serves index.html and
   ui_parts/app.html. Exposed as window.FridayRouteWhy({route}). */
(function (root) {
  'use strict';
  if (!root) return;

  function cost(c) {
    if (!c) return '';
    if (c.usd === 0) return 'nothing (' + (c.basis || '') + ')';
    if (c.usd === null || c.usd === undefined) return c.basis || 'not known';
    return 'about $' + Number(c.usd).toFixed(4) + ' (' + (c.basis || '') + ')';
  }

  function lines(r) {
    var out = [];
    var seat = r.seat === 'none' ? 'nothing was sent' : (r.seat + (r.model ? ' · ' + r.model : ''));
    out.push(['Answered by', r.answered_by ? (r.answered_by + ' (the route chose ' + (r.model || '?') + ')') : seat]);
    if (r.reason) out.push(['Why', r.reason]);
    if (r.verified === false) out.push(['Note', 'no model reported answering; this is the decision only']);
    (Array.isArray(r.alternatives) ? r.alternatives : []).forEach(function (a) {
      if (!a || typeof a !== 'object') return;
      var who = (a.label || a.provider || a.seat || '') + (a.model ? ' · ' + a.model : '');
      out.push(['Not ' + who, a.why || '']);
    });
    if (r.expected_cost && typeof r.expected_cost === 'object') out.push(['Expected cost', cost(r.expected_cost)]);
    if (r.context && typeof r.context === 'object') out.push(['Sent', (r.context.messages || 0) + ' messages, about ' +
                                     (r.context.prompt_tokens_estimate || 0) + ' tokens']);
    var p = (r.fallback_policy && typeof r.fallback_policy === 'object') ? r.fallback_policy : {};
    if (p.cloud_to_cloud) out.push(['If the cloud provider fails', p.cloud_to_cloud]);
    if (p.local_to_cloud && p.local_to_cloud !== 'not applicable in this mode') out.push(['Local to cloud', p.local_to_cloud]);
    if (p.cloud_to_local) out.push(['Cloud to local', p.cloud_to_local]);
    if (p.vault) out.push(['Vault', p.vault]);
    if (r.mode) out.push(['Routing mode', r.mode]);
    if (typeof r.latency_ms === 'number') out.push(['Time to reply', (r.latency_ms / 1000).toFixed(1) + ' s']);
    return out;
  }

  function FridayRouteWhy(props) {
    var R = root.React;
    var r = props && props.route;
    if (!R || !r || typeof r !== 'object') return null;
    var rows;
    try { rows = lines(r); } catch (e) { return null; }   // a bad record never takes the chat down
    return R.createElement('details', {
        className: 'friday-route-why',
        style: { display: 'inline-block', marginLeft: 6, fontSize: 9, verticalAlign: 'top' }
      },
      R.createElement('summary', {
        title: 'Why this reply was answered here',
        'aria-label': 'Why this reply was answered here',
        style: { cursor: 'pointer', color: '#94a3b8', listStyle: 'none', fontFamily: 'JetBrains Mono' }
      }, 'why'),
      R.createElement('div', {
          role: 'note',
          style: { marginTop: 4, padding: '6px 8px', maxWidth: 420, borderRadius: 4,
                   background: 'rgba(15,23,42,0.95)', border: '1px solid rgba(148,163,184,0.25)',
                   color: '#cbd5e1', fontSize: 11, lineHeight: 1.45, whiteSpace: 'normal' }
        },
        rows.map(function (kv, i) {
          return R.createElement('div', { key: i },
            R.createElement('span', { style: { color: '#94a3b8' } }, kv[0] + ': '), String(kv[1]));
        })));
  }

  root.FridayRouteWhy = FridayRouteWhy;
  root.fridayRouteWhyLines = lines;
})(typeof window !== 'undefined' ? window : null);
