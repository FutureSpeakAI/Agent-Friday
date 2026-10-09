'use strict';
/** Answer the first-run cloud-consent question in the scratch home, once, the careful way ("cloud_guarded":
 *  sensitive material stays held back, exactly as while unanswered). A fresh home asks it over every page
 *  after the page loads, which covers whatever a spec is about to click. The live Friday is never touched:
 *  this runs only against the scratch server the config started. */
module.exports = async function setup(config) {
  const base = process.env.FRIDAY_BASE;
  if (!base) return;
  const url = new URL('/api/privacy/cloud-consent', base);
  if (url.hostname !== '127.0.0.1' && url.hostname !== 'localhost') return;
  const status = await (await fetch(url, { signal: AbortSignal.timeout(120000) })).json();
  if (!status.needs_prompt) return;
  const r = await fetch(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ choice: 'cloud_guarded' }), signal: AbortSignal.timeout(120000),
  });
  if (!r.ok) throw new Error('could not record the cloud-consent answer in the scratch home: HTTP ' + r.status);
};
