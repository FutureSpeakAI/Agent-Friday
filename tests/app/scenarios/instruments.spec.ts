/**
 * INSTRUMENT SCENARIOS — turning Friday's own self-checks into tests.
 *
 * These need no conversation and run in seconds, so they come first. They also
 * illustrate something worth saying out loud: Friday already ships instruments
 * that know when she is broken. Nothing was reading them.
 */
import { test, expect } from '@playwright/test';
import { BASE } from './scenario';

test.describe('Scenario 28 — her own liveness check', () => {
  /**
   * /api/liveness reports subsystems that "ran" but produced nothing. That is
   * precisely the failure mode this whole effort exists to catch, and Friday
   * was already detecting it internally while nothing asserted on it.
   */
  test('nothing reports as having run while producing nothing', async ({ request }) => {
    const res = await request.get(BASE + '/api/liveness');
    expect(res.ok(), `GET /api/liveness returned HTTP ${res.status()}`).toBeTruthy();
    const { findings = [] } = await res.json();

    const dead = findings.filter((f: any) =>
      f.ran === true && (f.produced === false || f.status === 'empty'));

    expect(
      dead.map((f: any) => f.name),
      `Friday's own liveness check reports ${dead.length} subsystem(s) that run and produce nothing:` +
      String.fromCharCode(10) +
      dead.map((f: any) => `  - ${f.name} (${f.tier}): ${f.detail}`).join(String.fromCharCode(10)) +
      String.fromCharCode(10) +
      `  She is already detecting this internally. Nothing was reading the instrument.`,
    ).toEqual([]);
  });

  /** Anything produced should be consumed by something, or it is write-only. */
  test('nothing is produced that no one consumes', async ({ request }) => {
    const { findings = [] } = await (await request.get(BASE + '/api/liveness')).json();
    const orphaned = findings.filter((f: any) => f.produced === true && f.consumed === false);
    expect(
      orphaned.map((f: any) => `${f.name} -> ${f.consumer ?? 'nobody'}`),
      `${orphaned.length} subsystem(s) produce output that nothing consumes:` + String.fromCharCode(10) +
      orphaned.map((f: any) => `  - ${f.name}: ${f.detail}`).join(String.fromCharCode(10)),
    ).toEqual([]);
  });
});

test.describe('Scenario 30 — the dead route, proven at runtime', () => {
  /**
   * /api/workspace/<id>/revert is registered by two blueprints. Flask serves
   * whichever registered first; the other never runs. The two handlers happen
   * to disagree on their error shape, so a deliberately invalid request — which
   * changes nothing — reveals which one is actually live.
   *
   *   workspace_studio.py answers {"status": "error", "message": ...}
   *   workspace_undo.py   answers {"ok": false,      "error":   ...}
   */
  test('only one of the two revert handlers can ever answer', async ({ request }) => {
    // No version_id: both handlers reject it, neither mutates anything.
    const res = await request.post(BASE + '/api/workspace/zz-test-nonexistent/revert', {
      data: {}, timeout: 30_000,
    });
    const body: any = await res.json();

    const live = 'status' in body ? 'workspace_studio.py'
               : 'ok' in body ? 'workspace_undo.py'
               : 'neither (unrecognised shape)';
    const dead = live === 'workspace_studio.py' ? 'workspace_undo.py'
               : live === 'workspace_undo.py' ? 'workspace_studio.py'
               : 'unknown';

    // Record which one wins, so the finding is actionable rather than abstract.
    console.log(`      revert is served by ${live}; ${dead} is unreachable. Body: ${JSON.stringify(body)}`);

    expect(
      dead,
      `Two blueprints register POST /api/workspace/<ws_id>/revert with no url_prefix.` +
      String.fromCharCode(10) +
      `  Flask serves ${live}. Every handler in ${dead} for this URL is dead code:` +
      String.fromCharCode(10) +
      `  it looks implemented, it is imported, it is never called.` + String.fromCharCode(10) +
      `  The same is true of /api/workspace/<ws_id>/reset.`,
    ).toBe('none — no duplicate registration');
  });
});
