'use strict';
/**
 * Where the app suite runs. Tests never write to the owner's live Friday.
 *
 * - FRIDAY_BASE unset (the default): a scratch server on 127.0.0.1 with a
 *   temporary FRIDAY_HOME, started by Playwright's webServer.
 * - FRIDAY_BASE naming the live server (the live port on loopback, or any
 *   non-loopback host such as the local Caddy name): refused unless
 *   LIVE_READONLY=1, and in that mode every non-GET request is blocked and
 *   fails the test (tests/app/fixtures.ts).
 * - FRIDAY_BASE naming another loopback port: used as given (a server
 *   someone started for testing).
 *
 * Plain CommonJS so the config, the specs and a Python test can all load it.
 */
const LOOPBACK = new Set(['localhost', '127.0.0.1', '[::1]', '::1']);

function resolveTarget(env) {
  env = env || {};
  const livePort = String(env.FRIDAY_LIVE_PORT || '3000');
  const scratchPort = String(env.FRIDAY_SCRATCH_PORT || '3197');
  const base = String(env.FRIDAY_BASE || '').trim().replace(/\/+$/, '');
  if (!base) {
    return { baseURL: 'http://127.0.0.1:' + scratchPort, live: false, scratch: true, scratchPort };
  }
  let u;
  try {
    u = new URL(base);
  } catch (e) {
    throw new Error('FRIDAY_BASE is not a URL: ' + base);
  }
  const port = u.port || (u.protocol === 'https:' ? '443' : '80');
  const live = !LOOPBACK.has(u.hostname) || port === livePort;
  if (live && env.LIVE_READONLY !== '1') {
    throw new Error(
      'Refusing to run the app suite against the live Friday at ' + base +
      ': tests never write to live. Leave FRIDAY_BASE unset to run against a ' +
      'scratch server, or set LIVE_READONLY=1 for a read-only run in which ' +
      'every non-GET request is blocked and fails the test.');
  }
  return { baseURL: base, live, scratch: false };
}

/** Playwright webServer entry for a scratch Friday with its own home. */
function scratchServer(target, repoRoot, python, home) {
  return {
    command: '"' + python + '" server.py',
    cwd: repoRoot,
    url: target.baseURL + '/api/health',
    reuseExistingServer: false,
    timeout: 240000,
    env: Object.assign({}, process.env, {
      FRIDAY_HOME: home,
      FRIDAY_PORT: target.scratchPort,
      FRIDAY_TESTING: '1',
    }),
  };
}

module.exports = { resolveTarget, scratchServer };
