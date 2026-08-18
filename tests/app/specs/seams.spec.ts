/**
 * SEAM TESTS — the joins between parts, where this codebase keeps breaking.
 *
 * These need no browser and run in about a second, so they belong in the
 * fast tier. They catch a class of defect that is invisible to unit tests
 * because each side of the seam is individually correct.
 */
import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { apiJson, BASE } from '../harness';

const REPO = path.resolve(__dirname, '../../..');

function pythonFiles(dir: string): string[] {
  const out: string[] = [];
  const walk = (d: string) => {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      if (e.name === '__pycache__') continue;
      const p = path.join(d, e.name);
      if (e.isDirectory()) walk(p);
      else if (e.name.endsWith('.py')) out.push(p);
    }
  };
  walk(dir);
  return out;
}

test.describe('Seams', () => {

  /**
   * Flask serves the FIRST rule registered for a URL and silently ignores any
   * later one. So a second registration is not an error — it is dead code that
   * looks live. Motivated by a workspace revert that was registered twice and
   * whose replacement never ran.
   */
  test('no URL is registered by two different route handlers', async () => {
    const files = pythonFiles(path.join(REPO, 'src', 'agent_friday', 'routes'));
    const seen = new Map<string, string[]>();
    const re = /@(\w+)\.route\(\s*(['"])([^'"]+)\2(?:\s*,\s*methods\s*=\s*\[([^\]]*)\])?/g;

    for (const f of files) {
      const src = fs.readFileSync(f, 'utf8');
      for (const m of src.matchAll(re)) {
        const methods = (m[4] || "'GET'").replace(/['"\s]/g, '').toUpperCase();
        const key = `${m[3]} [${methods}]`;
        const where = `${path.relative(REPO, f).split(path.sep).join('/')} (blueprint ${m[1]})`;
        seen.set(key, [...(seen.get(key) || []), where]);
      }
    }

    const dupes = [...seen.entries()].filter(([, w]) => w.length > 1);
    expect(
      dupes.map(([url]) => url),
      dupes.length === 0 ? '' :
      `These URLs are claimed by more than one handler. Flask serves whichever is registered ` +
      `first and ignores the rest, so the others are dead code that will never run — ` +
      `they look implemented but return whatever the first one returns:\n` +
      dupes.map(([url, where]) => `  ${url}\n${where.map(w => `      ${w}`).join('\n')}`).join('\n'),
    ).toEqual([]);
  });

  /**
   * A control must write a key that something actually reads. Motivated by a
   * settings picker whose selection wrote a config key nothing consumed, so the
   * app agreed with the user while serving a different model.
   *
   * Read-only on purpose: this asserts the CURRENT selection is coherent, and
   * never writes to the real configuration.
   */
  test('every selected model exists in the catalog and is available', async ({ page }) => {
    const data = await apiJson(page, '/api/models');
    const byId = new Map<string, any>((data.models || []).map((m: any) => [m.id, m]));
    const problems: string[] = [];

    for (const [role, id] of Object.entries(data.selected || {})) {
      if (!id || id === 'auto') continue;
      const m = byId.get(String(id));
      if (!m) problems.push(`"${role}" is set to "${id}", which is not in the catalog at all — the app cannot serve it.`);
      else if (m.available === false) problems.push(`"${role}" is set to "${m.label || id}", which the catalog marks as unavailable.`);
    }

    expect(problems, `The saved model selection does not match what the app can serve:\n  ` + problems.join('\n  ')).toEqual([]);
  });

  /**
   * Motivated by a dropdown that listed six hardcoded Claude models because the
   * catalog fetch 401'd and it silently fell back to constants. The fallback is
   * not the bug — the silence is. If a catalog is stale the app must say so.
   */
  test('no model catalog is silently serving stale hardcoded fallbacks', async ({ page }) => {
    const data = await apiJson(page, '/api/models');
    const stale = Object.entries(data.catalog_meta || {})
      .filter(([, v]: any) => v?.stale)
      .map(([p, v]: any) => `${p} (last fetched: ${v.fetched_at ? new Date(v.fetched_at * 1000).toISOString() : 'never'})`);

    expect(
      stale,
      `These model catalogs failed to load and the app is serving a built-in fallback list instead:\n  ` +
      stale.join('\n  ') +
      `\n  Anyone opening the model picker sees a hardcoded list presented as the live one.`,
    ).toEqual([]);
  });
});
