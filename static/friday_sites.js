/* Saved sites share the same operations and approval records as chat and voice. */
(function () {
  'use strict';
  if (window.FridaySitesWorkspace) return;
  const h = React.createElement;
  const {useState, useEffect, useCallback, useRef} = React;
  const hosts = {cloudflare_pages: 'Cloudflare Pages', github_pages: 'GitHub Pages'};
  const states = {
    awaiting_approval: 'Waiting for your review', applying: 'Working', built: 'Build ready', failed: 'Failed',
    refused: 'Needs a fresh review', denied: 'Declined', expired: 'Review expired', unknown: 'Not confirmed',
    provider_accepted: 'Host accepted the upload', provider_ready: 'Host reports ready', partial: 'Setup incomplete',
    verified_live: 'Verified live', provider_verified: 'Confirmed at Name.com', renewal_observed: 'Expiration extended',
    awaiting_provider_checkout: 'Review at Name.com', pending: 'Pending', not_connected: 'Not connected',
    content_pending: 'Published content not confirmed', certificate_pending: 'Certificate not confirmed',
    verification_pending: 'Live page not confirmed', provider_pending: 'Waiting for the host', previously_verified: 'Previously verified'
  };
  function safeUrl(value, local) {
    if (typeof value !== 'string' || /[\\\u0000-\u0020]/.test(value)) return null;
    if (value.startsWith('/') && !value.startsWith('//')) return value;
    if (local) return null;
    try { const u = new URL(value); return u.protocol === 'https:' && !u.username && !u.password ? u.href : null; } catch (_) { return null; }
  }
  function previewUrl(value) {
    return typeof value === 'string' && /^\/api\/sites\/preview-frame\/p[0-9a-f]{48}$/.test(value) ? value : null;
  }
  function revokePreview(value) {
    const url = previewUrl(value);
    if (!url) return;
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 5000);
    fetch(url, {method: 'DELETE', signal: controller.signal, headers: {'X-Friday-Token': window.__FRIDAY_API_TOKEN || ''}})
      .catch(() => {}).finally(() => clearTimeout(timer));
  }
  function stamp(value) {
    if (!value) return 'Not checked';
    const d = new Date(typeof value === 'number' ? value * 1000 : value);
    return Number.isNaN(d.getTime()) ? 'Unknown date' : d.toLocaleString([], {dateStyle: 'medium', timeStyle: 'short'});
  }
  function dateOnly(value) {
    if (!value) return 'Date unavailable';
    const day = String(value).slice(0, 10), d = new Date(day + 'T12:00:00');
    return Number.isNaN(d.getTime()) ? 'Date unavailable' : d.toLocaleDateString([], {dateStyle: 'medium'});
  }
  function domainDate(row, now = Date.now()) {
    const verified = row.verified === true && !!row.expire_date;
    const imported = row.imported || {};
    const value = verified ? row.expire_date : imported.next_date;
    const kind = verified ? 'expires' : imported.next_date_kind;
    const time = value ? Date.parse(String(value).slice(0, 10) + 'T12:00:00Z') : NaN;
    const days = Number.isFinite(time) ? Math.ceil((time - now) / 86400000) : null;
    return {value, kind, verified, days, urgent: kind === 'expires' && days !== null && days <= 30,
      text: value ? (kind === 'renews' ? 'Renews ' : 'Expires ') + dateOnly(value) : 'Renewal date unavailable',
      source: verified ? 'Name.com' : imported.source || 'No authenticated date'};
  }
  function parseImport(text) {
    const lines = text.split(/\r?\n/).map(x => x.trim()).filter(Boolean);
    if (!lines.length) throw new Error('Paste at least one domain row.');
    return lines.map((line, i) => {
      const parts = line.split(/[\t,]/).map(x => x.trim());
      if (parts.length !== 3 || !/^(renews|expires)$/i.test(parts[1]) || !/^\d{4}-\d{2}-\d{2}$/.test(parts[2]))
        throw new Error('Row ' + (i + 1) + ': use domain, Renews or Expires, YYYY-MM-DD.');
      return {domain: parts[0], next_date_kind: parts[1].toLowerCase(), next_date: parts[2], source: 'user_import'};
    });
  }
  function deploymentLabel(item) { return item ? states[item.status] || 'Status unavailable' : 'Nothing published yet'; }
  window.FridaySitesState = {safeUrl, previewUrl, domainDate, parseImport, deploymentLabel};
  function api(url, body, extraHeaders = {}) {
    const token = window.__FRIDAY_API_TOKEN || '';
    return fetch(url, {method: body === undefined ? 'GET' : 'POST', headers: {
      ...extraHeaders, ...(token ? {'X-Friday-Token': token} : {}), ...(body === undefined ? {} : {'Content-Type': 'application/json'})
    }, ...(body === undefined ? {} : {body: JSON.stringify(body)})}).then(async r => {
      const data = await r.json().catch(() => ({}));
      if (!r.ok || data.status === 'error' || data.ok === false) throw new Error(data.message || data.error || 'The request could not finish.');
      return data;
    });
  }
  const postSite = (action, args, conversation_id) => api('/api/sites/action', {action, args, ...(conversation_id ? {conversation_id} : {})});
  const postDomain = (action, args) => api('/api/domains/action', {action, args});
  function useReplyGuard(owner) {
    const scope = useRef({owner, sequence: 0, active: true});
    if (scope.current.owner !== owner) {scope.current.owner = owner; ++scope.current.sequence;}
    useEffect(() => {scope.current.active = true; return () => {scope.current.active = false; ++scope.current.sequence;};}, []);
    return {
      begin: () => {const sequence = ++scope.current.sequence; return () => scope.current.active && scope.current.sequence === sequence;},
      invalidate: () => {++scope.current.sequence;}
    };
  }
  const button = (label, onClick, props = {}) => h('button', {type: 'button', className: 'site-btn', onClick, ...props}, label);
  const quiet = text => h('p', {className: 'site-quiet'}, text);
  const field = (label, control, hint) => h('label', {className: 'site-field'}, h('span', null, label), control, hint && h('small', null, hint));
  const input = props => h('input', {className: 'site-input', ...props});
  const select = (props, children) => h('select', {className: 'site-input', ...props}, children);
  const status = (text, tone = '') => h('span', {className: 'site-status ' + tone}, text);
  function link(label, url, props = {}) { const href = safeUrl(url); return href ? h('a', {className: 'site-link', href, target: '_blank', rel: 'noopener noreferrer', ...props}, label) : null; }
  function go(target) {
    if (window.fridayOpenWorkspace) window.fridayOpenWorkspace(target);
    else window.dispatchEvent(new CustomEvent('friday-nav', {detail: target}));
  }
  function Review({operation, onRefresh, busy}) {
    if (!operation) return null;
    const checkout = operation.status === 'awaiting_provider_checkout';
    return h('div', {className: 'site-review', role: 'status', 'data-site-operation': operation.operation_id},
      h('strong', null, states[operation.status] || operation.status || 'Operation recorded'),
      operation.domain && quiet((operation.account_label || 'Selected account') + ' · ' + operation.domain),
      operation.message && quiet(operation.message), operation.error && h('p', {className: 'site-error'}, operation.error),
      operation.action === 'prepare_dns' && h('div', {className: 'site-diff'},
        h('div', null, h('b', null, 'Before'), h('pre', null, operation.before ? JSON.stringify(operation.before, null, 2) : 'No record')),
        h('div', null, h('b', null, 'After'), h('pre', null, operation.after ? JSON.stringify(operation.after, null, 2) : 'Delete this record'))),
      operation.action === 'prepare_autorenew' && h('div', null,
        quiet('Auto-renew: ' + (operation.before?.autorenew_enabled ? 'on' : 'off') + ' → ' + (operation.after?.autorenew_enabled ? 'on' : 'off')),
        quiet(operation.after?.autorenew_enabled ? 'Enabling auto-renew permits future registrar charges on this account.' : 'With auto-renew off, renew separately to keep the domain.')),
      checkout && h('div', null,
        quiet('Renewal term: ' + operation.years + (operation.years === 1 ? ' year' : ' years')),
        operation.quote && quiet('Quoted subtotal: ' + operation.quote.subtotal + ' ' + operation.quote.currency + '. Tax and final total are confirmed at checkout.'),
        operation.quote?.quoted_at && quiet('Quote checked ' + stamp(operation.quote.quoted_at)),
        quiet('Review the account, domain, term and full price at Name.com. Opening checkout does not make a payment.'),
        link('Review renewal at Name.com', operation.checkout_url)),
      h('div', {className: 'site-actions'},
        operation.approval_id && operation.status === 'awaiting_approval' && button('Open approval', () => go({workspace: 'system', tab: 'approvals'}), {'data-approval-id': operation.approval_id}),
        onRefresh && button(checkout ? 'Check after checkout' : 'Check operation', onRefresh, {disabled: busy})),
      operation.checked_at && quiet('Last checked ' + stamp(operation.checked_at)));
  }
  function AccountForm({account, onDone, onCancel}) {
    const [label, setLabel] = useState(account?.label || '');
    const [username, setUsername] = useState(''), [token, setToken] = useState('');
    const [environment, setEnvironment] = useState(account?.environment || 'production');
    const [inventoryOnly, setInventoryOnly] = useState(false);
    const [busy, setBusy] = useState(false), [error, setError] = useState('');
    const save = async e => {
      e.preventDefault(); setBusy(true); setError('');
      try {
        await api('/api/domains/accounts/connect', {label, username: inventoryOnly ? '' : username, token: inventoryOnly ? '' : token,
          environment, ...(account ? {account_id: account.account_id, revision: account.revision} : {})});
        setToken(''); setUsername(''); onDone();
      } catch (err) { setError(err.message); setToken(''); } finally { setBusy(false); }
    };
    return h('form', {className: 'site-panel site-form', onSubmit: save, 'data-testid': 'domain-account-form'},
      h('h3', null, account ? 'Reconnect ' + account.label : 'Add a Name.com account'),
      field('Account name', input({value: label, onChange: e => setLabel(e.target.value), required: true, maxLength: 100, placeholder: 'Work domains'})),
      !account && h('label', {className: 'site-check'}, input({type: 'checkbox', checked: inventoryOnly, onChange: e => setInventoryOnly(e.target.checked)}), 'Track an inventory before connecting'),
      !inventoryOnly && h(React.Fragment, null,
        field('Name.com API username', input({value: username, onChange: e => setUsername(e.target.value), required: true, autoComplete: 'off'}), 'Use the API username from Name.com. Your Google sign-in email may be different.'),
        field('Name.com API token', input({type: 'password', value: token, onChange: e => setToken(e.target.value), required: true, autoComplete: 'new-password'})),
        quiet('Stored encrypted on this PC. Credentials stay out of chat, voice and site builds.')),
      field('Environment', select({value: environment, onChange: e => setEnvironment(e.target.value), disabled: !!account},
        [h('option', {value: 'production', key: 'production'}, 'Name.com'), h('option', {value: 'sandbox', key: 'sandbox'}, 'Name.com sandbox')])),
      error && h('p', {role: 'alert', className: 'site-error'}, error),
      h('div', {className: 'site-actions'}, button('Cancel', onCancel, {disabled: busy}),
        h('button', {type: 'submit', className: 'site-btn site-primary', disabled: busy}, busy ? 'Saving…' : inventoryOnly ? 'Save account' : 'Check and connect')));
  }
  function HostingForm({connection, onDone, onCancel}) {
    const [form, setForm] = useState({adapter: 'github_pages', name: '', repo: '', branch: 'gh-pages', account_id: '', project: '', ...connection, token: ''});
    const [busy, setBusy] = useState(false), [error, setError] = useState('');
    const update = key => e => setForm(p => ({...p, [key]: e.target.value}));
    const cloud = form.adapter === 'cloudflare_pages';
    const submit = async e => {
      e.preventDefault();
      if (form.adapter !== 'github_pages') {setError('Cloudflare Pages is not available for saved Sites yet.'); return;}
      setBusy(true); setError('');
      try {
        await api('/api/sites/hosting', {adapter: form.adapter, name: form.name, token: form.token,
          ...(connection ? {connection_id: connection.connection_id, revision: connection.revision} : {}),
          ...(cloud ? {account_id: form.account_id, project: form.project} : {repo: form.repo, branch: form.branch})});
        setForm(p => ({...p, token: ''})); onDone();
      } catch (err) { setError(err.message); setForm(p => ({...p, token: ''})); } finally { setBusy(false); }
    };
    return h('form', {className: 'site-panel site-form', onSubmit: submit, 'data-testid': 'site-hosting-form'},
      h('h3', null, connection ? 'Reconnect hosting' : 'Connect hosting'),
      field('Connection name', input({value: form.name, onChange: update('name'), required: true, placeholder: 'Portfolio hosting'})),
      field('Host', select({value: form.adapter, onChange: update('adapter'), disabled: !!connection}, [h('option', {key: 'github_pages', value: 'github_pages'}, hosts.github_pages)])),
      quiet('Cloudflare Pages is not available for saved Sites yet.'),
      cloud ? h(React.Fragment, null,
        field('Cloudflare account ID', input({value: form.account_id, onChange: update('account_id'), required: true, disabled: !!connection})),
        field('Pages project', input({value: form.project, onChange: update('project'), required: true, disabled: !!connection}))) : h(React.Fragment, null,
        field('Publishing repository', input({value: form.repo, onChange: update('repo'), required: true, placeholder: 'owner/website', disabled: !!connection})),
        field('Publishing branch', input({value: form.branch, onChange: update('branch'), required: true, disabled: !!connection}))),
      field('Hosting API token', input({type: 'password', value: form.token, onChange: update('token'), autoComplete: 'new-password', required: true})),
      quiet('Stored encrypted. This connection fixes the destination for the site; publication still needs your review.'),
      error && h('p', {role: 'alert', className: 'site-error'}, error),
      h('div', {className: 'site-actions'}, button('Cancel', onCancel, {disabled: busy}), h('button', {type: 'submit', className: 'site-btn site-primary', disabled: busy}, busy ? 'Saving…' : 'Save hosting connection')));
  }
  function DomainDetail({row, account, site, onClose, onChanged, history = [], requiredRecords, onDraftChange}) {
    const authority = JSON.stringify([row.account_id, row.domain, account?.revision, account?.connection_status, site?.site_id, site?.revision, site?.domain?.hostname]);
    const requirementKey = JSON.stringify(requiredRecords || []);
    const replies = useReplyGuard(authority + '/' + requirementKey);
    const freshRow = row.account_revision === account?.revision ? row : {...row, verified: false, expire_date: null, autorenew_enabled: null, dns_authority: 'unknown', last_synced_at: null};
    const [info, setInfo] = useState(freshRow), [records, setRecords] = useState(null), [editing, setEditing] = useState(null);
    const [operation, setOperation] = useState(null), [years, setYears] = useState('1');
    const [busy, setBusy] = useState(false), [error, setError] = useState(''), [note, setNote] = useState(''), [dnsCheck, setDnsCheck] = useState(null);
    const [cacheOwner, setCacheOwner] = useState(authority);
    useEffect(() => {onDraftChange?.(!!editing);}, [!!editing, onDraftChange]);
    useEffect(() => () => onDraftChange?.(false), [onDraftChange]);
    const shownInfo = cacheOwner === authority ? info : freshRow;
    const currentCache = cacheOwner === authority;
    useEffect(() => {setCacheOwner(authority); setInfo(freshRow); setRecords(null); setOperation(null); setDnsCheck(null); setBusy(false); setError(''); setNote('');}, [authority]);
    useEffect(() => {setDnsCheck(null); setBusy(false);}, [requirementKey]);
    useEffect(() => { setInfo(previous => Number(freshRow.last_synced_at || 0) >= Number(previous.last_synced_at || 0) ? freshRow : previous); }, [row]);
    const args = {account_id: row.account_id, domain: row.domain, ...(site ? {site_id: site.site_id, site_revision: site.revision} : {})};
    const act = async (action, extra = {}) => {
      const current = replies.begin();
      setBusy(true); setError(''); setNote('');
      try {
        const result = await postDomain(action, action === 'operation' ? {operation_id: extra.operation_id} : {...args, ...extra});
        if (!current()) return null;
        if (result.domain && typeof result.domain === 'object') setInfo(p => ({...p, ...result.domain, verified: true, last_synced_at: result.domain.verified_at}));
        if (result.records) setRecords(result.records);
        if (result.operation_id) { setOperation(result); setEditing(null); }
        else if (result.message) setNote(result.message);
        if (result.checks) setDnsCheck(result);
        onChanged(); return result;
      } catch (err) { if (current()) setError(err.message); return null; } finally { if (current()) setBusy(false); }
    };
    const checkOperation = async () => {
      const current = replies.begin();
      setBusy(true); setError('');
      try {
        const result = await postDomain(operation.status === 'awaiting_approval' ? 'operation' : 'reconcile', {operation_id: operation.operation_id});
        if (!current()) return;
        setOperation(result);
        if (['provider_verified', 'renewal_observed'].includes(result.status)) setInfo(p => ({...p, ...result.evidence, last_synced_at: result.checked_at || p.last_synced_at}));
        onChanged();
      }
      catch (err) { if (current()) setError(err.message); } finally { if (current()) setBusy(false); }
    };
    const date = domainDate(shownInfo), connected = account?.connection_status === 'verified';
    return h('section', {className: 'site-panel site-domain-detail', 'aria-label': 'Domain ' + row.domain, 'data-testid': 'domain-detail'},
      h('div', {className: 'site-heading'}, h('div', null, h('h3', null, row.domain), quiet((account?.label || 'Account unavailable') + ' · ' + (account?.environment === 'sandbox' ? 'Sandbox' : 'Name.com'))), onClose && button('Close domain', onClose)),
      h('div', {className: 'site-facts'}, h('div', null, h('b', null, date.text), quiet(date.verified ? 'Authenticated Name.com expiration date' : 'Imported date · not verified')),
        h('div', null, h('b', null, shownInfo.autorenew_enabled == null ? 'Auto-renew unknown' : shownInfo.autorenew_enabled ? 'Auto-renew on' : 'Auto-renew off'), quiet('A setting does not confirm a future payment.')),
        h('div', null, h('b', null, shownInfo.dns_authority === 'namecom' ? 'DNS at Name.com' : shownInfo.dns_authority === 'external' ? 'DNS hosted elsewhere' : 'DNS authority not checked'), quiet('Last sync ' + stamp(shownInfo.last_synced_at)))),
      h('div', {className: 'site-actions'}, button('Check domain', () => act('inspect'), {disabled: busy || !connected}), button('Read DNS records', () => act('records'), {disabled: busy || !connected})),
      !connected && quiet('Connect this named account to check its domain and prepare changes.'),
      error && h('p', {className: 'site-error', role: 'alert'}, error),
      note && h('p', {role: 'status', className: 'site-quiet'}, note),
      requiredRecords?.length > 0 && button('Check public DNS against these requirements', () => act('verify', {records: requiredRecords}), {disabled: busy || !connected}),
      currentCache && dnsCheck && h('div', {className: 'site-inset'}, h('b', null, dnsCheck.matches ? 'Required DNS matches this resolver' : 'Required DNS is not confirmed'),
        quiet(dnsCheck.message), quiet((dnsCheck.resolver || 'Public resolver') + ' · ' + stamp(dnsCheck.checked_at)),
        dnsCheck.checks.map((check, i) => h('div', {key: i, className: 'site-wrap'}, check.type + ' · ' + check.hostname + ' · ' + (check.matches ? 'Matches' : 'Pending or different')))),
      h('details', {className: 'site-disclosure'}, h('summary', null, 'Renewal and auto-renew'),
        h('div', {className: 'site-actions'}, field('Renewal term', select({value: years, onChange: e => setYears(e.target.value)}, Array.from({length: 10}, (_, i) => h('option', {key: i, value: String(i + 1)}, (i + 1) + ((i + 1) === 1 ? ' year' : ' years'))))),
          button('Review renewal price', () => act('prepare_renewal', {years: Number(years)}), {disabled: busy || !connected}),
          button(shownInfo.autorenew_enabled === true ? 'Review turning auto-renew off' : 'Review turning auto-renew on', () => act('prepare_autorenew', {enabled: shownInfo.autorenew_enabled !== true}), {disabled: busy || !connected || shownInfo.autorenew_enabled == null}))),
      currentCache && records !== null && h('div', {className: 'site-records'}, h('div', {className: 'site-heading'}, h('h4', null, 'DNS records'), button('Add a record', () => setEditing({host: '', type: 'CNAME', answer: '', ttl: 300, owner: authority}), {disabled: busy || shownInfo.dns_authority !== 'namecom'})),
        shownInfo.dns_authority !== 'namecom' && quiet('These records are read-only here because Name.com is not the authoritative DNS host.'),
        !records.length && quiet('No records returned.'), records.map(record => h('div', {className: 'site-record', key: record.id},
          h('div', null, h('b', null, record.type + ' · ' + (record.host || '@')), h('div', {className: 'site-wrap'}, record.answer), quiet('TTL ' + record.ttl + ' · record ' + record.id)),
          h('div', {className: 'site-actions'}, button('Edit', () => setEditing({...record, owner: authority}), {disabled: busy || shownInfo.dns_authority !== 'namecom', 'aria-label': 'Edit record ' + record.id}),
            button('Review deletion', () => act('prepare_dns', {record_id: record.id, delete: true}), {disabled: busy || shownInfo.dns_authority !== 'namecom', 'aria-label': 'Review deletion of record ' + record.id}))))),
      editing && h('form', {className: 'site-form site-inset', onSubmit: e => { e.preventDefault(); if (editing.owner !== authority || !connected || shownInfo.dns_authority !== 'namecom') return; const {id, ...record} = editing; act('prepare_dns', {record: {host: record.host, type: record.type, answer: record.answer, ttl: Number(record.ttl), ...(record.priority != null && record.priority !== '' ? {priority: Number(record.priority)} : {})}, ...(id ? {record_id: id} : {})}); }},
        h('h4', null, editing.id ? 'Edit this DNS record' : 'New DNS record'),
        editing.owner !== authority && quiet('The account or site changed. Your draft is kept; cancel it and read current DNS records before preparing another change.'),
        field('Host', input({value: editing.host || '', onChange: e => setEditing({...editing, host: e.target.value}), placeholder: 'www or leave empty for root'})),
        field('Record type', select({value: editing.type, onChange: e => setEditing({...editing, type: e.target.value})}, ['A', 'AAAA', 'ANAME', 'CNAME', 'MX', 'SRV', 'TXT'].map(type => h('option', {key: type, value: type}, type)))),
        field('Value', input({value: editing.answer || '', onChange: e => setEditing({...editing, answer: e.target.value}), required: true})),
        field('TTL in seconds', input({type: 'number', min: 300, value: editing.ttl, onChange: e => setEditing({...editing, ttl: e.target.value}), required: true})),
        ['MX', 'SRV'].includes(editing.type) && field('Priority', input({type: 'number', min: 0, max: 65535, value: editing.priority ?? 0, onChange: e => setEditing({...editing, priority: e.target.value})})),
        h('div', {className: 'site-actions'}, button('Cancel record edit', () => setEditing(null)), h('button', {type: 'submit', className: 'site-btn', disabled: busy || editing.owner !== authority || !connected || shownInfo.dns_authority !== 'namecom'}, 'Prepare exact change'))),
      h(Review, {operation: currentCache ? operation : null, onRefresh: checkOperation, busy}),
      history.length > 0 && h('details', {className: 'site-disclosure'}, h('summary', null, 'Domain change history'), history.map(op => h('div', {className: 'site-record', key: op.operation_id},
        h('div', null, h('b', null, ({prepare_dns: 'DNS', prepare_autorenew: 'Auto-renew', prepare_renewal: 'Renewal'})[op.action] || 'Domain operation'), quiet(states[op.status] || op.status), quiet(stamp(op.created_at))),
        button('Review operation', () => act('operation', {operation_id: op.operation_id}), {disabled: busy})))));
  }
  function DomainInventory({data, onRefresh, onClose, onPick, pickUnavailable, pickBusy, target, onAccepted, onDraftChange}) {
    const [search, setSearch] = useState(''), [accountId, setAccountId] = useState(target?.account_id || '');
    const [detail, setDetail] = useState(null), [form, setForm] = useState(null), [importText, setImportText] = useState('');
    const [detailDraft, setDetailDraft] = useState(false);
    const [busy, setBusy] = useState(false), [error, setError] = useState(''), [note, setNote] = useState('');
    const accounts = data.accounts || [], inventory = data.inventory || [];
    useEffect(() => {onDraftChange(!!form || !!importText.trim() || detailDraft);}, [form, importText, detailDraft, onDraftChange]);
    useEffect(() => () => onDraftChange(false), [onDraftChange]);
    useEffect(() => { if (target?.account_id) setAccountId(target.account_id); if (target?.domain) setDetail({account_id: target.account_id, domain: target.domain}); }, [target?.account_id, target?.domain]);
    const rows = inventory.filter(row => (!accountId || row.account_id === accountId) && (row.domain + ' ' + (accounts.find(a => a.account_id === row.account_id)?.label || '')).toLowerCase().includes(search.toLowerCase()));
    const action = async fn => { setBusy(true); setError(''); setNote(''); try { await fn(); await onRefresh(); } catch (err) { setError(err.message); } finally { setBusy(false); } };
    const chosenAccount = accounts.find(a => a.account_id === accountId);
    const selected = detail && inventory.find(d => d.account_id === detail.account_id && d.domain === detail.domain);
    useEffect(() => {
      onAccepted(chosenAccount ? {account_id: chosenAccount.account_id, ...(selected ? {domain: selected.domain, domain_ref: chosenAccount.account_id + '/' + selected.domain} : {})} : {});
    }, [chosenAccount?.account_id, selected?.domain, onAccepted]);
    return h('section', {className: 'site-drawer', 'aria-label': 'Domains and accounts', 'data-testid': 'domain-inventory'},
      h('div', {className: 'site-heading'}, h('div', null, h('h2', null, 'Your domains'), quiet('Named accounts, saved inventory and exact change reviews.')), button('Close domains', onClose)),
      pickUnavailable && quiet('This picker belonged to another site version. Your account forms are kept; choose a domain again from the current site.'),
      h('div', {className: 'site-form-grid'}, field('Find a domain', input({type: 'search', value: search, onChange: e => setSearch(e.target.value), placeholder: 'Search domain or account'})),
        field('Name.com account', select({value: accountId, onChange: e => {setAccountId(e.target.value); setDetail(null);}}, [h('option', {key: '', value: ''}, 'All accounts'), ...accounts.map(a => h('option', {key: a.account_id, value: a.account_id}, a.label + (a.environment === 'sandbox' ? ' · Sandbox' : '')))]))),
      h('div', {className: 'site-actions'}, button('Add Name.com account', () => setForm({}), {disabled: !!form}),
        chosenAccount && button('Sync this account', () => action(async () => { await postDomain('sync', {account_id: accountId}); setNote('Inventory refreshed from Name.com.'); }), {disabled: busy || chosenAccount.connection_status !== 'verified'})),
      error && h('p', {role: 'alert', className: 'site-error'}, error), note && h('p', {role: 'status'}, note),
      accountId && !chosenAccount && h('p', {className: 'site-attention'}, 'This exact account is unavailable. Choose another account.'),
      form && h(AccountForm, {key: form.account_id || 'new', account: form.account_id ? form : null, onCancel: () => setForm(null), onDone: () => {setForm(null); onRefresh();}}),
      !accounts.length && quiet('Add separate names for each account. You can import an inventory before connecting their API credentials.'),
      h('div', {className: 'site-domain-list'}, rows.map(row => { const date = domainDate(row), account = accounts.find(a => a.account_id === row.account_id); return h('article', {className: 'site-domain-row', key: row.account_id + '/' + row.domain},
        h('div', null, h('strong', {className: 'site-wrap'}, row.domain), quiet(account?.label || 'Account unavailable'),
          h('div', {className: date.urgent ? 'site-attention' : ''}, date.text), date.urgent && quiet(date.days < 0 ? 'Date passed · check with Name.com' : 'Due within 30 days · review renewal'),
          quiet((date.verified ? 'Name.com' : 'Imported · unverified') + ' · last sync ' + stamp(row.last_synced_at))),
        h('div', {className: 'site-actions'}, button('Inspect', () => {setAccountId(row.account_id); setDetail(row);}), onPick && button('Use this domain', () => onPick(row, account), {disabled: busy || pickBusy || !row.verified || row.account_revision !== account?.revision || account?.connection_status !== 'verified', title: row.verified ? 'Select this verified account and domain' : 'Sync this account before binding this domain'}))); })),
      accounts.length > 0 && !rows.length && quiet('No domains match. Sync an account or import its inventory below.'),
      selected && h(DomainDetail, {key: selected.account_id + '/' + selected.domain, row: selected, account: accounts.find(a => a.account_id === selected.account_id), onClose: () => setDetail(null), onChanged: onRefresh,
        onDraftChange: setDetailDraft,
        history: (data.operations || []).filter(op => op.account_id === selected.account_id && op.domain === selected.domain)}),
      detail && !selected && h('p', {className: 'site-attention'}, 'This exact domain is unavailable in the selected account inventory. Sync its account to check it.'),
      h('details', {className: 'site-disclosure'}, h('summary', null, 'Manage accounts and import inventory'),
        accounts.map(a => h('div', {className: 'site-record', key: a.account_id}, h('div', null, h('b', null, a.label), quiet((a.connection_status === 'verified' ? 'API connected' : 'Not connected') + ' · ' + a.environment + ' · last sync ' + stamp(a.last_synced_at))),
          h('div', {className: 'site-actions'}, button('Reconnect', () => setForm(a), {disabled: !!form}), a.connection_status === 'verified' && button('Disconnect', () => action(() => api('/api/domains/accounts/' + encodeURIComponent(a.account_id) + '/disconnect', {revision: a.revision})), {disabled: busy})))),
        h('form', {className: 'site-form', onSubmit: e => { e.preventDefault(); action(async () => { await api('/api/domains/import', {account_id: accountId, entries: parseImport(importText)}); setImportText(''); setNote('Inventory imported. Dates remain unverified until an account sync.'); }); }},
          h('h3', null, 'Import a saved inventory'), quiet('Select one account above. Paste one row per line: domain, Renews or Expires, YYYY-MM-DD.'),
          field('Inventory rows', h('textarea', {className: 'site-input', value: importText, onChange: e => setImportText(e.target.value), rows: 4, placeholder: 'example.com, Expires, 2027-01-15'})),
          h('button', {type: 'submit', className: 'site-btn', disabled: busy || !accountId || !importText.trim()}, 'Import for selected account'))));
  }
  function SiteEditor({site, codebases, hosting, onSave, onCancel, onConnect}) {
    const [form, setForm] = useState({name: '', codebase_id: '', build_root: '.', build_command: '', output_dir: '.', ...site});
    const [busy, setBusy] = useState(false), [error, setError] = useState('');
    const update = key => e => setForm(p => ({...p, [key]: e.target.value}));
    const selectedCodebase = codebases.find(cb => cb.id === form.codebase_id);
    const availableCodebases = site ? codebases.filter(cb => cb.conversation_id === site.conversation_id) : codebases;
    const submit = async e => {
      e.preventDefault(); setBusy(true); setError('');
      try { await onSave({...(site ? {site_id: site.site_id, revision: site.revision} : {}), name: form.name, codebase_id: form.codebase_id,
        build_root: form.build_root, build_command: form.build_command, output_dir: form.output_dir, hosting: form.hosting || null, domain: site?.domain || null}, site?.conversation_id || selectedCodebase?.conversation_id); }
      catch (err) { setError(err.message); } finally { setBusy(false); }
    };
    return h('form', {className: 'site-panel site-form', onSubmit: submit, 'data-testid': 'site-editor'},
      h('h2', null, site ? 'Edit site setup' : 'Add a repository site'),
      field('Site name', input({value: form.name, onChange: update('name'), required: true, maxLength: 120})),
      field('Repository codebase', select({value: form.codebase_id, onChange: update('codebase_id'), required: true}, [h('option', {value: '', key: ''}, 'Choose a codebase'),
        ...(form.codebase_id && !availableCodebases.some(cb => cb.id === form.codebase_id) ? [h('option', {key: form.codebase_id, value: form.codebase_id}, 'Saved codebase unavailable')] : []),
        ...availableCodebases.map(cb => h('option', {key: cb.id, value: cb.id}, cb.title || cb.name || cb.id))]), site ? 'Keeps this site in its owning chat and project.' : 'Uses this codebase’s owning chat and project.'),
      !codebases.length && h('div', null, quiet('Add or import a repository in Code first.'), button('Open Code', () => go({workspace: 'code'}))),
      h('div', {className: 'site-form-grid'}, field('Build folder', input({value: form.build_root, onChange: update('build_root'), required: true}), 'Relative to the repository.'),
        field('Output folder', input({value: form.output_dir, onChange: update('output_dir'), required: true}), 'Static files to preview and publish.')),
      field('Build command', input({value: form.build_command, onChange: update('build_command'), placeholder: 'npm run build'}), 'Leave empty when the output folder already contains the finished static site.'),
      field('Hosting connection', select({value: form.hosting?.connection_id || '', onChange: e => setForm(p => ({...p, hosting: e.target.value ? {connection_id: e.target.value} : null}))},
        [h('option', {value: '', key: ''}, 'Choose later'), ...hosting.map(c => h('option', {key: c.connection_id, value: c.connection_id, disabled: c.adapter !== 'github_pages'}, c.name + (c.adapter !== 'github_pages' ? ' · unavailable for saved Sites' : c.connected ? '' : ' · reconnect needed')))])),
      button('Connect a host', onConnect), quiet('Saving sets up this site. Build and publish each prepare an exact review before running.'),
      error && h('p', {className: 'site-error', role: 'alert'}, error),
      h('div', {className: 'site-actions'}, button('Cancel', onCancel, {disabled: busy}), h('button', {type: 'submit', className: 'site-btn site-primary', disabled: busy || !selectedCodebase?.conversation_id || !availableCodebases.some(cb => cb.id === form.codebase_id)}, busy ? 'Saving…' : 'Save site')));
  }
  function SiteDetail({site, codebases, hosting, domains, busy, onAction, onEdit, onDomains, onRefresh, previewTarget, onPreviewAccepted, onPreviewRequested}) {
    const [buildId, setBuildId] = useState(site.latest_build?.build_id || '');
    const [preview, setPreview] = useState(null), [operation, setOperation] = useState(null), [requirements, setRequirements] = useState(null);
    const [previewBusy, setPreviewBusy] = useState(false);
    const consumedPreview = useRef(null), previewAttempt = useRef(0), issuedPreview = useRef(null);
    const [error, setError] = useState(''), [hostname, setHostname] = useState(site.domain?.hostname || '');
    const builds = site.builds || [], build = builds.find(b => b.build_id === buildId);
    const cb = codebases.find(c => c.id === site.codebase_id), connection = hosting.find(c => c.connection_id === site.hosting?.connection_id);
    const deployment = site.current_deployment, latest = site.latest_deployment;
    const binding = site.domain, domainRow = binding && domains.inventory?.find(d => d.account_id === binding.account_id && d.domain === binding.domain);
    const account = binding && domains.accounts?.find(a => a.account_id === binding.account_id);
    const authority = JSON.stringify([site.site_id, site.revision, connection?.connection_id, connection?.revision, account?.account_id, account?.revision, account?.connection_status]);
    const visiblePreview = preview?.authority === authority && preview.build_id === buildId ? preview : null;
    const replies = useReplyGuard(authority + '/' + buildId);
    const latestAttach = useRef(null), savedHostname = useRef(site.domain?.hostname || '');
    latestAttach.current = {site_id: site.site_id, revision: site.revision, hostname};
    useEffect(() => () => {latestAttach.current = null;}, []);
    useEffect(() => { if (!buildId && site.latest_build?.build_id) setBuildId(site.latest_build.build_id); }, [site.latest_build?.build_id]);
    useEffect(() => {setRequirements(null); setPreview(null); setOperation(null); setError('');}, [authority]);
    useEffect(() => {setPreview(null); setOperation(null); setError('');}, [buildId]);
    useEffect(() => {
      const changed = () => {replies.invalidate(); ++previewAttempt.current; revokePreview(issuedPreview.current); issuedPreview.current = null; setPreviewBusy(false); setPreview(null);};
      window.addEventListener('friday:off-record', changed);
      return () => window.removeEventListener('friday:off-record', changed);
    }, []);
    useEffect(() => {const next = site.domain?.hostname || ''; const previous = savedHostname.current; savedHostname.current = next; setHostname(value => value === previous ? next : value);}, [site.domain?.hostname]);
    const act = async (action, args = {}) => {
      const current = replies.begin();
      setError('');
      try { const r = await onAction(action, {site_id: site.site_id, ...args}); if (!current()) return null; if (r.operation) setOperation(r.operation);
        if (r.requirements) setRequirements(r.requirements); return r;
      } catch (err) { if (current()) setError(err.message); return null; }
    };
    const openPreview = async (requestId = null) => {
      const current = replies.begin();
      const attempt = ++previewAttempt.current;
      setError(''); setPreview(null); setPreviewBusy(true);
      try {
        const r = await api('/api/sites/preview', {site_id: site.site_id, site_revision: site.revision, build_id: buildId,
          mode: requestId === null ? 'manual' : 'navigation', ...(requestId === null ? {} : {request_id: requestId})},
          {'X-Friday-Preview-Mode': requestId === null ? 'manual' : 'navigation'});
        if (!current()) {revokePreview(r.preview_url); return;}
        const url = previewUrl(r.preview_url), expires = Number(r.expires_at) * 1000;
        if (!url || r.site_id !== site.site_id || r.site_revision !== site.revision || r.build_id !== buildId ||
            !Number.isFinite(expires) || expires <= Date.now() || expires > Date.now() + 360000) {
          revokePreview(r.preview_url);
          throw new Error('The frozen preview address is unavailable. Open this build again.');
        }
        issuedPreview.current = url;
        setPreview(previous => current() ? {url, site_id: r.site_id, site_revision: r.site_revision, authority, build_id: buildId, expires} : previous);
      } catch (err) { if (current()) setError(err.message); }
      finally { if (attempt === previewAttempt.current) setPreviewBusy(false); }
    };
    useEffect(() => {
      ++previewAttempt.current; setPreviewBusy(false);
      return () => {++previewAttempt.current; revokePreview(issuedPreview.current); issuedPreview.current = null;};
    }, [authority, buildId]);
    useEffect(() => {
      if (!preview) return;
      let active = true;
      const timer = setTimeout(() => {if (active) {setPreview(previous => previous === preview ? null : previous); setError('This preview expired. Open the saved build again.');}}, Math.max(0, preview.expires - Date.now()));
      return () => {active = false; clearTimeout(timer); revokePreview(preview.url); if (issuedPreview.current === preview.url) issuedPreview.current = null;};
    }, [preview]);
    useEffect(() => {
      if (!visiblePreview) return;
      let active = true, timer, controller;
      const poll = async () => {
        controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 4000);
        try {
          const r = await fetch(visiblePreview.url, {method: 'HEAD', cache: 'no-store', redirect: 'error', signal: controller.signal,
            headers: {'X-Friday-Token': window.__FRIDAY_API_TOKEN || ''}});
          if (!r.ok || r.headers.get('X-Friday-Site-Preview') !== 'active') throw new Error('Preview ended.');
        } catch (_) {
          if (active) {setPreview(previous => previous === visiblePreview ? null : previous); setError('The preview closed because its site or privacy context changed. Open it again when ready.');}
          return;
        } finally {clearTimeout(timeout);}
        if (active) timer = setTimeout(poll, 2000);
      };
      timer = setTimeout(poll, 2000);
      return () => {active = false; clearTimeout(timer); if (controller) controller.abort();};
    }, [visiblePreview]);
    useEffect(() => {
      onPreviewAccepted(visiblePreview ? {site_id: visiblePreview.site_id, site_revision: visiblePreview.site_revision, build_id: visiblePreview.build_id} : null);
      return () => onPreviewAccepted(null);
    }, [visiblePreview, onPreviewAccepted]);
    useEffect(() => {
      if (!previewTarget?.preview_build_id || previewTarget.site_id !== site.site_id || consumedPreview.current === previewTarget) return;
      if (typeof previewTarget.preview_request_id !== 'string' || !/^n[0-9a-f]{48}$/.test(previewTarget.preview_request_id)) {
        consumedPreview.current = previewTarget; onPreviewRequested(previewTarget); setError('Open the saved build again to start a fresh preview.'); return;
      }
      const selectedBuild = builds.find(b => b.build_id === previewTarget.preview_build_id && b.status === 'built');
      if (!selectedBuild) {consumedPreview.current = previewTarget; onPreviewRequested(previewTarget); setError('The requested saved build is unavailable.'); return;}
      if (buildId !== selectedBuild.build_id) {setBuildId(selectedBuild.build_id); return;}
      consumedPreview.current = previewTarget;
      onPreviewRequested(previewTarget);
      openPreview(previewTarget.preview_request_id);
    }, [previewTarget, buildId, authority]);
    const attach = async (row, selectedAccount) => {
      const owner = latestAttach.current;
      if (!owner || owner.site_id !== site.site_id || owner.revision !== site.revision || owner.hostname !== hostname)
        throw new Error('This domain picker changed. Open it again from the current site.');
      const host = hostname.trim() || 'www.' + row.domain;
      return onAction('save', {site_id: site.site_id, revision: site.revision, domain: {account_id: row.account_id, account_revision: selectedAccount.revision, domain: row.domain, hostname: host}});
    };
    const openDomains = () => onDomains({onPick: attach, target: binding || null, owner: {site_id: site.site_id, revision: site.revision}});
    return h('div', {className: 'site-detail', 'data-testid': 'site-detail', 'data-site-revision': site.revision},
      h('div', {className: 'site-heading'}, h('div', null, h('h2', null, site.name), status(deploymentLabel(deployment), deployment?.status === 'verified_live' ? 'site-good' : '')),
        h('div', {className: 'site-actions'}, button('Refresh status', onRefresh, {disabled: busy}), button('Edit setup', onEdit, {disabled: busy}))),
      error && h('p', {role: 'alert', className: 'site-error'}, error),
      h('section', {className: 'site-panel'}, h('div', {className: 'site-step'}, h('span', null, '1'), h('h3', null, 'Repository')),
        h('strong', null, cb?.title || cb?.name || 'Saved codebase'), quiet('Build folder: ' + site.build_root + ' · output: ' + site.output_dir),
        h('div', {className: 'site-actions'}, link('Open owning chat', '/?chrome=chat&conversation=' + encodeURIComponent(site.conversation_id)), button('Open codebase', () => go({workspace: 'code', codebase_id: site.codebase_id}))),
        quiet(site.build_command ? 'Build command: ' + site.build_command : 'Static files · no build command')),
      h('section', {className: 'site-panel'}, h('div', {className: 'site-step'}, h('span', null, '2'), h('h3', null, 'Build and preview')),
        site.latest_build && site.latest_build.status !== 'built' && h('div', {className: 'site-attention'}, states[site.latest_build.status] || site.latest_build.status, site.latest_build.error && ': ' + site.latest_build.error),
        h('div', {className: 'site-actions'}, button('Review new build', () => act('build'), {disabled: busy, className: 'site-btn site-primary'})),
        builds.length > 0 && field('Saved build', select({value: buildId, onChange: e => {setBuildId(e.target.value); setPreview(null);}}, builds.map(b => h('option', {key: b.build_id, value: b.build_id}, stamp(b.created_at) + ' · ' + (states[b.status] || b.status) + ' · ' + String(b.source_revision || b.source_hash || b.build_id).slice(0, 8))))),
        build && h('div', null, quiet('Source ' + String(build.source_revision || 'uncommitted snapshot') + (build.source_hash ? ' · snapshot ' + build.source_hash.slice(0, 12) : '')),
          h('div', {className: 'site-actions'}, button(previewBusy ? 'Opening preview…' : 'Preview this build', () => openPreview(), {disabled: busy || previewBusy || build.status !== 'built'}),
            button('Review publication', () => act('prepare_publish', {build_id: build.build_id}), {disabled: busy || build.status !== 'built' || !connection?.connected || connection.adapter !== 'github_pages'}))),
        !builds.length && quiet('Prepare a build to capture this repository and preview the exact files before publication.'),
        visiblePreview && h('div', {className: 'site-preview'}, h('div', {className: 'site-heading'}, h('b', null, 'Frozen build preview'), button('Close preview', () => {replies.invalidate(); setPreview(null);})),
          quiet('Layout preview only. JavaScript interactions do not run. This preview expires after five minutes.'),
          h('iframe', {src: visiblePreview.url, sandbox: '', referrerPolicy: 'no-referrer', title: 'Frozen site build preview', 'data-build-id': visiblePreview.build_id}))),
      h('section', {className: 'site-panel'}, h('div', {className: 'site-step'}, h('span', null, '3'), h('h3', null, 'Published version')),
        h('strong', null, deploymentLabel(deployment)), quiet(connection ? connection.name + ' · ' + (hosts[connection.adapter] || connection.adapter) : 'Choose a hosting connection in site setup.'),
        deployment && h('div', null, quiet('Build ' + deployment.build_id + ' · checked ' + stamp(deployment.checked_at)),
          link('Open site address', deployment.verification?.url || deployment.url || deployment.provider_url),
          button('Verify deployment', () => act('deployment_status', {operation_id: deployment.operation_id}), {disabled: busy}),
          deployment.verification?.note && quiet(deployment.verification.note), deployment.message && quiet(deployment.message), deployment.error && h('p', {className: 'site-attention'}, deployment.error)),
        latest && latest.operation_id !== deployment?.operation_id && h('div', {className: 'site-inset'}, h('b', null, 'Latest publication: ' + deploymentLabel(latest)),
          quiet(latest.error || latest.message || 'The previous published version is shown above.'), button('Check latest attempt', () => act('deployment_status', {operation_id: latest.operation_id}), {disabled: busy})),
        quiet('Git status, a completed build and host acceptance are separate from a verified live page.')),
      h('section', {className: 'site-panel'}, h('div', {className: 'site-step'}, h('span', null, '4'), h('h3', null, 'Domain')),
        binding ? h('div', null, h('strong', {className: 'site-wrap'}, binding.hostname), quiet((account?.label || 'Account unavailable') + ' · ' + binding.domain)) : quiet('Choose a domain from one of your named Name.com accounts.'),
        field('Website hostname', input({value: hostname, onChange: e => setHostname(e.target.value), placeholder: 'www.example.com'}), 'Choose the full hostname, then select its domain. A domain maps to a site host.'),
        h('div', {className: 'site-actions'}, button(binding ? 'Choose or update domain' : 'Choose a domain', openDomains, {disabled: busy}),
          binding && button('Read hosting requirements', () => act('hosting_requirements'), {disabled: busy || !connection?.connected || connection.adapter !== 'github_pages'})),
        requirements && h('div', {className: 'site-inset'}, h('h4', null, 'Required domain setup'), quiet(requirements.note),
          (requirements.records || []).map((record, i) => h('div', {className: 'site-record', key: i}, h('div', null,
            h('b', null, record.type + ' · ' + (record.host || '@')), h('div', {className: 'site-wrap'}, record.answer), quiet('TTL ' + record.ttl)))),
          quiet('Review the exact DNS record below. Reading these requirements changes nothing.')),
        domainRow && h(DomainDetail, {key: binding.account_id + '/' + binding.domain, row: domainRow, account, site, onChanged: onRefresh, requiredRecords: requirements?.records,
          history: (domains.operations || []).filter(op => op.account_id === binding.account_id && op.domain === binding.domain)})),
      h(Review, {operation, busy, onRefresh: () => act(operation.action === 'publish' ? 'deployment_status' : 'operation', {operation_id: operation.operation_id})}),
      h('details', {className: 'site-disclosure'}, h('summary', null, 'Build and publication history'),
        !(site.history || []).length ? quiet('No operations yet.') : site.history.map(op => h('div', {className: 'site-record', key: op.operation_id},
          h('div', null, h('b', null, (op.action === 'build' ? 'Build' : 'Publish') + ' · ' + (states[op.status] || op.status)), quiet(stamp(op.created_at)), op.error && quiet(op.error)),
          button('Inspect operation', () => act('operation', {operation_id: op.operation_id}), {disabled: busy})))));
  }
  function FridaySitesWorkspace({legacy, target, onAccepted}) {
    const [sites, setSites] = useState([]), [codebases, setCodebases] = useState([]), [hosting, setHosting] = useState([]);
    const [domains, setDomains] = useState({accounts: [], inventory: []});
    const [selected, setSelected] = useState(target?.site_id || ''), [editor, setEditor] = useState(null), [hostForm, setHostForm] = useState(null);
    const [drawer, setDrawer] = useState(null), [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [error, setError] = useState('');
    const [domainAccepted, setDomainAccepted] = useState({}), [catalogValid, setCatalogValid] = useState(false), [showLegacy, setShowLegacy] = useState(false), [navLoading, setNavLoading] = useState(false);
    const [domainDraft, setDomainDraft] = useState(false);
    const [previewAccepted, setPreviewAccepted] = useState(null);
    const [previewNavigation, setPreviewNavigation] = useState(null);
    const consumedPreviewTarget = useRef(null), previewNavigationEpoch = useRef(0), requestedTarget = useRef(target);
    requestedTarget.current = target;
    const previewRequested = useCallback(request => {consumedPreviewTarget.current = request;}, []);
    const cancelPreviewNavigation = useCallback(() => {
      ++previewNavigationEpoch.current; consumedPreviewTarget.current = requestedTarget.current; setPreviewNavigation(null);
    }, []);
    useEffect(() => {
      window.addEventListener('friday:off-record', cancelPreviewNavigation);
      return () => {++previewNavigationEpoch.current; window.removeEventListener('friday:off-record', cancelPreviewNavigation);};
    }, [cancelPreviewNavigation]);
    const readSequence = useRef(0), domainSequence = useRef(0);
    const selection = useRef({key: '', generation: 0, navigation: 0}), drawerRef = useRef(null);
    const site = sites.find(s => s.site_id === selected);
    const selectionKey = JSON.stringify([selected, site?.revision]);
    if (selection.current.key !== selectionKey) selection.current = {key: selectionKey, site_id: selected, generation: selection.current.generation + 1,
      navigation: selection.current.navigation + (selection.current.site_id === selected ? 0 : 1)};
    drawerRef.current = drawer;
    const pickerIsCurrent = value => value?.onPick && value.owner?.site_id === selection.current.site_id && value.generation === selection.current.generation;
    const refreshDomains = useCallback(async () => {
      const seq = ++domainSequence.current;
      try { const d = await api('/api/domains/overview'); if (seq === domainSequence.current) setDomains(d); }
      catch (err) { if (seq === domainSequence.current) setError(err.message); }
    }, []);
    const load = useCallback(async () => {
      const seq = ++readSequence.current;
      try { const [s, c, hostsData, d] = await Promise.all([api('/api/sites'), api('/api/codebases'), api('/api/sites/hosting'), api('/api/domains/overview')]);
        if (seq !== readSequence.current) return;
        setSites(s.sites || []); setCodebases(c.codebases || []); setHosting(hostsData.connections || []); ++domainSequence.current; setDomains(d); setCatalogValid(true);
        setSelected(id => id || s.sites?.[0]?.site_id || ''); setError('');
        return {sites: s.sites || [], domains: d};
      } catch (err) { if (seq === readSequence.current) {setError(err.message); setCatalogValid(false);} }
      finally { if (seq === readSequence.current) setLoading(false); }
    }, []);
    useEffect(() => {load(); return () => {++readSequence.current; ++domainSequence.current;};}, [load]);
    useEffect(() => {
      if (!target?.site_id && !target?.account_id && !target?.domain) return;
      const previewEpoch = ++previewNavigationEpoch.current;
      setPreviewNavigation(null);
      if (editor) {consumedPreviewTarget.current = target; setError('Finish or cancel the site setup before opening another selection.'); return;}
      if (hostForm || domainDraft) {consumedPreviewTarget.current = target; setError('Finish or cancel the open form before opening another selection.'); return;}
      let active = true;
      setNavLoading(true); onAccepted({}); setLoading(true); setCatalogValid(false); setDomainAccepted({});
      if (target.site_id) setSelected(target.site_id);
      setDrawer(target.account_id || target.domain ? {target} : null);
      load().then(data => {if (active && previewEpoch === previewNavigationEpoch.current && data && target.preview_build_id) setPreviewNavigation(target);})
        .finally(() => {if (active) {setNavLoading(false); setLoading(false);}});
      return () => {active = false;};
    // A blocked navigation is consumed; finishing a form must not replay it.
    }, [target, load, onAccepted]);
    const action = async (name, args, cid) => {
      const generation = selection.current.generation;
      setBusy(true); setError(''); ++readSequence.current;
      try { const r = await postSite(name, args, cid); await load(); return r; }
      catch (err) { if (generation === selection.current.generation) setError(err.message); throw err; } finally { setBusy(false); }
    };
    const save = async (args, cid) => { const r = await action('save', args, cid); setSelected(r.site.site_id); setEditor(null); };
    const openDomains = options => {setDomainAccepted({}); setDrawer({...options, generation: selection.current.generation});};
    useEffect(() => {
      if (navLoading || loading || !catalogValid || editor || hostForm) {onAccepted({}); return;}
      onAccepted({...(site ? {site_id: site.site_id} : {}), ...(drawer ? domainAccepted : {}),
        ...(site && previewAccepted?.site_id === site.site_id && previewAccepted.site_revision === site.revision ? {preview_build_id: previewAccepted.build_id} : {})});
    }, [site?.site_id, site?.revision, previewAccepted, navLoading, loading, catalogValid, editor, hostForm, drawer, domainAccepted, onAccepted]);
    const hasPending = site?.history?.some(op => ['awaiting_approval', 'applying'].includes(op.status));
    useEffect(() => { if (!hasPending || editor) return; const timer = setInterval(load, 6000); return () => clearInterval(timer); }, [hasPending, editor, load]);
    return h('div', {className: 'sites-workspace', 'data-testid': 'sites-workspace'},
      h('div', {className: 'site-toolbar'}, field('Saved site', select({value: selected, onChange: e => {cancelPreviewNavigation(); setSelected(e.target.value); setEditor(null);}, disabled: !!editor},
        [h('option', {key: '', value: ''}, sites.length ? 'Choose a site' : 'No saved sites yet'), ...(selected && !site ? [h('option', {key: selected, value: selected}, 'Unavailable saved site')] : []), ...sites.map(s => h('option', {key: s.site_id, value: s.site_id}, s.name))])),
        h('div', {className: 'site-actions'}, button('Add site', () => {cancelPreviewNavigation(); setEditor({});}, {disabled: !!editor || busy}), button('Domains and accounts', () => openDomains(), {disabled: busy}))),
      error && h('div', {role: 'alert', className: 'site-error'}, error, ' ', button('Refresh', load)),
      loading && quiet('Reading your sites…'),
      drawer && h(DomainInventory, {data: domains, onRefresh: refreshDomains, onClose: () => setDrawer(null), target: drawer.target, onAccepted: setDomainAccepted,
        onDraftChange: setDomainDraft, pickBusy: busy, pickUnavailable: !!drawer.onPick && !pickerIsCurrent(drawer),
        onPick: pickerIsCurrent(drawer) && catalogValid && !editor && !navLoading ? async (row, account) => {
          if (drawerRef.current !== drawer || !pickerIsCurrent(drawer)) return;
          const navigation = selection.current.navigation;
          try { const result = await drawer.onPick(row, account); if (drawerRef.current === drawer && navigation === selection.current.navigation && result?.site?.site_id === selection.current.site_id) setDrawer(null); }
          catch (err) { if (drawerRef.current === drawer && pickerIsCurrent(drawer)) setError(err.message); }
        } : null}),
      hostForm && h(HostingForm, {key: hostForm.connection_id || 'new', connection: hostForm.connection_id ? hostForm : null, onDone: () => {setHostForm(null); load();}, onCancel: () => setHostForm(null)}),
      editor && h(SiteEditor, {key: editor.site_id || 'new', site: editor.site_id ? editor : null, codebases, hosting, onSave: save, onCancel: () => setEditor(null), onConnect: () => setHostForm({})}),
      !loading && !editor && site && h(SiteDetail, {key: site.site_id, site, codebases, hosting, domains, busy, onAction: action, onEdit: () => {cancelPreviewNavigation(); setEditor(site);}, onDomains: openDomains, onRefresh: load,
        previewTarget: consumedPreviewTarget.current === previewNavigation ? null : previewNavigation, onPreviewAccepted: setPreviewAccepted, onPreviewRequested: previewRequested}),
      !loading && !editor && !site && h('div', {className: 'site-panel site-empty'}, h('h2', null, selected ? 'This saved site is unavailable' : 'From repository to a published site'),
        quiet(selected ? 'The exact site may have moved or its owning chat is unavailable. Choose another saved site or refresh.' : 'Connect an existing codebase, preview a captured build, then review publication and domain setup.'),
        !selected && button('Add your first site', () => setEditor({}), {className: 'site-btn site-primary'})),
      h('details', {className: 'site-disclosure'}, h('summary', null, 'Hosting connections'),
        hosting.map(c => h('div', {className: 'site-record', key: c.connection_id}, h('div', null, h('b', null, c.name), quiet((hosts[c.adapter] || c.adapter) + ' · ' + (c.project || c.repo || '') + ' · ' + (c.connected ? 'Credential saved' : 'Reconnect needed'))),
          h('div', {className: 'site-actions'}, c.adapter === 'github_pages' ? button('Reconnect', () => setHostForm(c)) : quiet('Cloudflare Pages is not available for saved Sites yet.'), c.connected && button('Disconnect', async () => {setBusy(true); try {await api('/api/sites/hosting/' + encodeURIComponent(c.connection_id) + '/disconnect', {revision: c.revision}); await load();} catch (err) {setError(err.message);} finally {setBusy(false);}}, {disabled: busy})))), button('Connect hosting', () => setHostForm({}))),
      legacy && h('details', {className: 'site-disclosure site-legacy', onToggle: e => setShowLegacy(e.currentTarget.open)}, h('summary', null, 'Earlier portfolio entries'), quiet('These entries show saved links and local Git status. They do not prove a deployment is live.'), showLegacy && legacy));
  }
  window.FridaySitesWorkspace = FridaySitesWorkspace;
})();
