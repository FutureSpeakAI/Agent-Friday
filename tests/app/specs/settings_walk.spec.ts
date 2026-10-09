/**
 * THE SETTINGS WALK. Opens every Settings section, exercises every control
 * that is safe to exercise, and proves the change is kept: it is read back
 * from the saved settings (GET /api/settings) AND from the control itself
 * after a full page reload.
 *
 * Runs against the scratch server only (playwright.app.config.ts: its own
 * FRIDAY_HOME, FRIDAY_NO_ARBITER=1). Nothing here can reach a live Friday.
 *
 * What it will NOT do, by construction:
 *   - click any button that downloads, installs, spends, sends, connects an
 *     account, erases or contacts another host. Those are listed as
 *     "present" and, where a confirmation step exists, the confirmation is
 *     opened and then cancelled (the voice-model download below);
 *   - type into a secret, key, token, number or address field;
 *   - let any request leave 127.0.0.1 or reach a route in NEVER (the page
 *     is routed; a hit fails the test).
 *
 * Output (the test's output folder): settings_walk.json and settings_walk.md,
 * one row per control with its result.
 *
 * The inventory that backs this walk, and the static guard that runs without
 * a browser, are tests/unit/settings_controls.json and
 * tests/unit/test_settings_controls_have_handler_and_reader.py.
 */
import * as fs from 'fs';
import { test, expect } from '../fixtures';
import type { Page } from '../fixtures';
import type { Request } from '@playwright/test';
import { BASE } from '../harness';

type Section = { id: string; label: string };
const SECTIONS: Section[] = [
  { id: 'general', label: 'General' },
  { id: 'voice', label: 'Voice' },
  { id: 'intelligence', label: 'Models' },
  { id: 'costs', label: 'Spending' },
  { id: 'privacy', label: 'Privacy & Data' },
  { id: 'connections', label: 'Connections' },
  { id: 'appearance', label: 'Appearance' },
  { id: 'advanced', label: 'Advanced' },
  { id: 'about', label: 'About' },
];

/** Old tab ids that deep links, events and cards still use, and where each must land. */
const ALIASES: Record<string, string> = {
  accounts: 'Connections', providers: 'Connections', connectors: 'Connections', phone: 'Connections',
  keys: 'Connections', signing: 'Connections', dock: 'Appearance', tracking: 'Appearance',
  notifications: 'General', knowledge: 'Advanced', work: 'Advanced', models: 'Models', spending: 'Spending',
};

/** Routes the walk must never reach: they download, spend, send, connect or erase. */
const NEVER = [
  /\/api\/voice\/setup\/install/, /\/api\/updates\/check/, /\/api\/phone\//, /\/api\/providers\/[^/]+\/key/,
  /\/api\/google\/accounts\/connect/, /\/api\/content\/platforms\//, /\/api\/models\/(pull|fetch|download)/,
  /\/api\/data\/(erase|export)/, /\/api\/browser\/profile\/clear/, /\/api\/costs\/.*(stop|limit)/,
];

/** A control whose row says any of this is looked at, never changed. */
const GUARDED = /phone|twilio|texting|sms|voicemail|your cell|computer control|vault|passphrase|erase|delete|forget|clear|remove|disconnect|certificate|signature|key|token|secret|password|download|install|update|check for|cloud|unrestricted|budget|hard stop|alert|federation|economy|wallet|google|oauth|connect|publish|substack|mcp|salon|workspaces? (permission|history)|export|calibrate|elevenlabs|gemini|microphone|camera|webcam|listener|local address|grant|off the record|send|texts?|by text|invites?/i;

/**
 * Whole groups the walk looks at and never changes: forms that create a standing permission or reach another
 * service (a scheduled job's grants, file and computer access, agent workspaces, local hosting, Google accounts, the
 * texting registration, and the Models browser, whose pickers filter a list and are not settings). Their pickers feed a button that is not pressed here, so changing one saves nothing by design.
 */
const GUARDED_GROUP = /scheduled jobs|file access|computer control|agent workspaces|published pages|google accounts|texting registration|model browser|permission|grant/i;
/** Sections that are accounts and phone lines from top to bottom: opened and listed, never changed. */
const LOOK_ONLY_SECTIONS = new Set(['connections']);

/** The only free-text fields the walk types into. Everything else is present-only. */
const TEXT_OK = /^(name|language|speaking style)\b/i;

type Row = {
  section: string; group: string; kind: string; label: string; action: string;
  posted: string; apiPersisted: string; uiPersisted: string; result: 'pass' | 'fail' | 'info'; detail: string;
};

async function openSettings(page: Page) {
  // A fresh scratch home has finished no setup, so the first-run consent flow would cover Settings.
  // The walk is of Settings after setup, as the owner uses it; the settings themselves stay real.
  await page.route('**/api/setup/status', r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ initialized: true }) }));
  // The same holds for the cloud-consent question a fresh home asks once. The walk answers it the careful way
  // ("cloud_guarded": sensitive material stays held back, as while unanswered) in the scratch home, so the gate
  // does not appear over Settings after a reload.
  const cc = await (await page.request.get(`${BASE}/api/privacy/cloud-consent`, { timeout: 120_000 })).json();
  if (cc.needs_prompt) {
    const r = await page.request.post(`${BASE}/api/privacy/cloud-consent`, { data: { choice: 'cloud_guarded' }, timeout: 120_000 });
    expect(r.ok(), 'recording the careful cloud-consent answer in the scratch home').toBeTruthy();
  }
  await page.goto(`${BASE}/w/settings`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.st-root', { timeout: 60000 });
}

async function goSection(page: Page, s: Section) {
  const rail = page.getByRole('navigation', { name: 'Settings sections' });
  await rail.getByRole('button', { name: s.label, exact: true }).click();
  await expect(rail.getByRole('button', { name: s.label, exact: true })).toHaveAttribute('aria-current', 'page');
  // Panels fetch their own data; give the section a moment to settle.
  await page.waitForTimeout(900);
}

/** Tag every control in the open section and describe it. Deterministic order. */
async function discover(page: Page) {
  return page.evaluate(() => {
    const root = document.querySelector('.fx-settings-sections');
    if (!root) return [];
    const out: any[] = [];
    let n = 0;
    const rowText = (el: Element) => {
      let p: Element | null = el;
      for (let i = 0; i < 5 && p && p !== root; i++, p = p.parentElement) {
        const t = ((p as HTMLElement).innerText || '').trim().split('\n')[0];
        if (t && t.length > 1 && !(el as HTMLElement).contains(p)) return t.slice(0, 90);
        if (p !== el && t) return t.slice(0, 90);
      }
      return ((el as HTMLElement).getAttribute('aria-label') || (el as HTMLElement).innerText || '').trim().slice(0, 90);
    };
    root.querySelectorAll('button, select, input, textarea').forEach(el => {
      const e = el as HTMLInputElement;
      if (e.closest('[aria-hidden="true"]')) return;
      const r = e.getBoundingClientRect();
      let kind = '';
      const tag = e.tagName.toLowerCase();
      if (tag === 'button') kind = e.getAttribute('role') === 'switch' ? 'switch' : 'button';
      else if (tag === 'select') kind = 'select';
      else if (tag === 'textarea') kind = 'text';
      else {
        const t = (e.type || 'text').toLowerCase();
        kind = t === 'range' ? 'slider' : t === 'checkbox' ? 'checkbox' : t === 'password' ? 'secret'
          : t === 'file' ? 'file' : t === 'radio' ? 'radio' : 'text';
      }
      const label = kind === 'button' ? (e.innerText || e.getAttribute('aria-label') || '').trim().slice(0, 90)
        : kind === 'switch' ? ((e.innerText || '').trim().split('\n')[0] || '').slice(0, 90)
        : rowText(e);
      e.setAttribute('data-walk-id', String(n));
      const sec = e.closest('[data-st-section]');
      const group = sec ? (sec.getAttribute('data-st-section') || '') : '';
      out.push({ id: n, kind, label, group, visible: r.width > 0 && r.height > 0, disabled: !!e.disabled });
      n++;
    });
    return out;
  });
}

function deepContains(actual: any, expected: any): boolean {
  if (expected && typeof expected === 'object' && !Array.isArray(expected)) {
    if (!actual || typeof actual !== 'object') return false;
    return Object.keys(expected).every(k => deepContains(actual[k], expected[k]));
  }
  return JSON.stringify(actual) === JSON.stringify(expected);
}

async function savedSettings(page: Page): Promise<any> {
  const r = await page.request.get(`${BASE}/api/settings`);
  expect(r.ok(), 'GET /api/settings').toBeTruthy();
  return (await r.json()).settings || {};
}

test.describe('Settings walk', () => {
  test.setTimeout(900_000);
  // A control that cannot be acted on is a finding, reported in its row, not a wait for the whole test's budget.
  test.use({ actionTimeout: 10_000 });

  test('every section opens, and every old section id still lands on the right one', async ({ page }) => {
    await openSettings(page);
    const rail = page.getByRole('navigation', { name: 'Settings sections' });
    const names = await rail.getByRole('button').allInnerTexts();
    expect(names.map(s => s.trim())).toEqual(SECTIONS.map(s => s.label));
    for (const s of SECTIONS) {
      await goSection(page, s);
      await expect(page.locator('.fx-settings-heading h2')).toHaveText(s.label);
    }
    for (const [alias, want] of Object.entries(ALIASES)) {
      await page.evaluate(a => window.dispatchEvent(new CustomEvent('friday:settings-tab', { detail: { tab: a } })), alias);
      await expect(rail.getByRole('button', { name: want, exact: true }), `alias ${alias}`)
        .toHaveAttribute('aria-current', 'page');
    }
  });

  test('the sticky heading is opaque and nothing renders through it', async ({ page }) => {
    await openSettings(page);
    await goSection(page, SECTIONS[1]);
    const box = await page.evaluate(() => {
      const h = document.querySelector('.fx-settings-heading') as HTMLElement;
      const c = getComputedStyle(h);
      const m = c.backgroundColor.match(/[\d.]+/g) || [];
      return { alpha: m.length > 3 ? Number(m[3]) : 1, position: c.position, z: Number(c.zIndex) };
    });
    expect(box.alpha).toBe(1);
    expect(box.position).toBe('sticky');
    expect(box.z).toBeGreaterThanOrEqual(20);
  });

  test('a download shows its confirmation, and Not now cancels it', async ({ page }) => {
    const hits: string[] = [];
    await page.route('**/api/voice/setup/install', route => { hits.push(route.request().method()); return route.abort(); });
    await openSettings(page);
    await goSection(page, SECTIONS[1]);
    const dl = page.getByRole('button', { name: /^Download$|^Continue download$/ }).first();
    if (await dl.count() === 0) {
      // Nothing installable on this machine; the list itself must still be there.
      await expect(page.getByText('Local voice models').first()).toBeVisible();
      return;
    }
    await dl.click();
    const card = page.getByText(/will be saved on this computer/).first();
    await expect(card).toBeVisible();
    const text = (await card.innerText()).toLowerCase();
    // The wording follows what pins the artifact: a checksum for files, a version for packages.
    expect(/fixed checksum|fixed version/.test(text), text).toBeTruthy();
    await page.getByRole('button', { name: 'Not now' }).first().click();
    await expect(card).toHaveCount(0);
    expect(hits, 'Not now must not start a download').toEqual([]);
  });

  test('every safe control keeps its change: read back from the saved settings and after a reload', async ({ page }, info) => {
    const forbidden: string[] = [];
    const external: string[] = [];
    await page.route('**/*', route => {
      const u = new URL(route.request().url());
      if (u.hostname !== '127.0.0.1' && u.hostname !== 'localhost') {
        external.push(route.request().method() + ' ' + u.hostname);
        return route.abort();
      }
      if (route.request().method() !== 'GET' && NEVER.some(rx => rx.test(u.pathname))) {
        forbidden.push(route.request().method() + ' ' + u.pathname);
        return route.abort();
      }
      return route.continue();
    });

    const rows: Row[] = [];
    await openSettings(page);

    for (const sec of SECTIONS) {
      await goSection(page, sec);
      const controls = await discover(page);
      const posted: any[] = [];
      const onReq = (r: Request) => {
        if (r.method() === 'POST' && new URL(r.url()).pathname === '/api/settings') {
          try { posted.push(r.postDataJSON()); } catch { /* not JSON */ }
        }
      };
      page.on('request', onReq);
      const expectedUi: { label: string; kind: string; value: string; nth: number }[] = [];
      const seen: Record<string, number> = {};

      for (const c of controls) {
        const key = c.kind + '|' + c.label;
        const nth = (seen[key] = (seen[key] || 0) + 1) - 1;
        const base = { section: sec.label, group: c.group || '', kind: c.kind, label: c.label };
        const loc = page.locator(`[data-walk-id="${c.id}"]`);
        if (!c.visible || c.disabled || (await loc.count()) === 0) {
          rows.push({ ...base, action: 'skipped', posted: '', apiPersisted: '', uiPersisted: '', result: 'info',
            detail: c.disabled ? 'disabled' : 'not visible or re-rendered' });
          continue;
        }
        const guarded = GUARDED.test(c.label) || GUARDED_GROUP.test(c.group || '') || LOOK_ONLY_SECTIONS.has(sec.id);
        const mutable = (c.kind === 'switch' || c.kind === 'select' || c.kind === 'slider' || c.kind === 'checkbox'
          || (c.kind === 'text' && TEXT_OK.test(c.label))) && !guarded;
        if (!mutable) {
          rows.push({ ...base, action: guarded && c.kind !== 'secret' && c.kind !== 'file' ? 'present (guarded)' : 'present',
            posted: '', apiPersisted: '', uiPersisted: '', result: 'pass', detail: 'seen; not changed by the walk' });
          continue;
        }
        const before = posted.length;
        // The save this control causes, if it saves through /api/settings at all: observed, not slept for.
        const settingsSaved = page.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/settings',
          { timeout: 30_000 }).catch(() => null);
        let value = '';
        try {
          if (c.kind === 'switch') {
            const was = await loc.getAttribute('aria-checked');
            await loc.click();
            value = was === 'true' ? 'false' : 'true';
            // A switch shows the new state once the server has confirmed the save.
            await settingsSaved;
            await expect(loc).toHaveAttribute('aria-checked', value, { timeout: 10_000 });
          } else if (c.kind === 'checkbox') {
            const was = await loc.isChecked();
            await loc.setChecked(!was);
            value = String(!was);
          } else if (c.kind === 'select') {
            const opts = await loc.locator('option').evaluateAll(os => os.map(o => (o as HTMLOptionElement).value));
            const cur = await loc.inputValue();
            // never a cloud destination: the walk does not send the owner's knowledge anywhere
            const next = opts.find(v => v !== cur && v !== '' && !/cloud/i.test(v));
            if (!next) {
              rows.push({ ...base, action: 'present', posted: '', apiPersisted: '', uiPersisted: '', result: 'pass', detail: 'one option only' });
              continue;
            }
            await loc.selectOption(next);
            value = next;
          } else if (c.kind === 'slider') {
            await loc.focus();
            const max = Number(await loc.getAttribute('max') || 100);
            const cur = Number(await loc.inputValue());
            await loc.press(cur >= max ? 'ArrowLeft' : 'ArrowRight');
            value = await loc.inputValue();
            await loc.blur();
          } else {
            value = 'Walk ' + Date.now().toString(36);
            await loc.fill(value);
            await loc.blur();
          }
        } catch (e: any) {
          rows.push({ ...base, action: 'changed', posted: '', apiPersisted: '', uiPersisted: '', result: 'fail',
            detail: 'the control did not respond: ' + String(e.message || e).split('\n')[0] });
          continue;
        }
        // Controls that keep their value elsewhere (their own route) never POST /api/settings: give those a short look.
        await Promise.race([settingsSaved, new Promise(res => setTimeout(res, 2000))]);
        const mine = posted.slice(before);
        expectedUi.push({ label: c.label, kind: c.kind, value, nth });
        const saved = mine.map(m => m && m.settings).filter(Boolean);
        if (!saved.length) {
          // It may save through its own route; the reload below still has to show it.
          rows.push({ ...base, action: 'changed', posted: '(own route)', apiPersisted: 'n/a', uiPersisted: '?', result: 'info',
            detail: 'no POST /api/settings; kept in the control after reload is checked below' });
          continue;
        }
        const keys = Object.keys(Object.assign({}, ...saved));
        const now = await savedSettings(page);
        const api = saved.every(s => deepContains(now, s));
        rows.push({ ...base, action: 'changed', posted: keys.join(', '), apiPersisted: api ? 'yes' : 'NO', uiPersisted: '?',
          result: api ? 'pass' : 'fail', detail: api ? 'GET /api/settings has the new value' : 'GET /api/settings does not show what was saved: saved ' + JSON.stringify(saved).slice(0, 160) + ' now ' + JSON.stringify(keys.reduce((o: any, k) => (o[k] = now[k], o), {})).slice(0, 160) });
      }
      page.off('request', onReq);

      // Reload and read each changed control back from the UI and the saved settings.
      if (expectedUi.length) {
        await openSettings(page);
        await goSection(page, sec);
        const again = await discover(page);
        const seen2: Record<string, number> = {};
        for (const want of expectedUi) {
          const key = want.kind + '|' + want.label;
          const nth = (seen2[key] = (seen2[key] || 0) + 1) - 1;
          const c = again.filter(a => a.kind === want.kind && a.label === want.label)[nth];
          const row = rows.find(r => r.section === sec.label && r.kind === want.kind && r.label === want.label && r.uiPersisted === '?');
          if (!row) continue;
          if (!c) { row.uiPersisted = 'NO'; row.result = 'fail'; row.detail += '; the control is gone after reload'; continue; }
          const loc = page.locator(`[data-walk-id="${c.id}"]`);
          let got = '';
          if (want.kind === 'switch') got = String(await loc.getAttribute('aria-checked'));
          else if (want.kind === 'checkbox') got = String(await loc.isChecked());
          else got = await loc.inputValue();
          const ok = got === want.value;
          row.uiPersisted = ok ? 'yes' : 'NO (shows ' + got + ')';
          if (!ok) row.result = 'fail';
          else if (row.result === 'info') row.result = 'pass';
        }
      }
    }

    // Put the report where the run keeps its artifacts.
    const out = info.outputPath('settings_walk.json');
    fs.mkdirSync(info.outputDir, { recursive: true });
    fs.writeFileSync(out, JSON.stringify(rows, null, 2));
    const md = ['# Settings walk', '', '| Section | Group | Kind | Control | Action | Saved keys | Settings API | After reload | Result |', '|---|---|---|---|---|---|---|---|---|']
      .concat(rows.map(r => `| ${r.section} | ${r.group.replace(/\|/g, '/')} | ${r.kind} | ${r.label.replace(/\|/g, '/')} | ${r.action} | ${r.posted} | ${r.apiPersisted} | ${r.uiPersisted} | ${r.result.toUpperCase()} |`));
    fs.writeFileSync(info.outputPath('settings_walk.md'), md.join('\n') + '\n');
    await info.attach('settings_walk.md', { path: info.outputPath('settings_walk.md') });
    await info.attach('settings_walk.json', { path: out });

    expect(external, 'the walk must not reach another host').toEqual([]);
    expect(forbidden, 'the walk must not start a download, spend, send or erase').toEqual([]);
    const failed = rows.filter(r => r.result === 'fail');
    expect(failed.map(r => `${r.section} > ${r.label}: ${r.detail}`), 'controls whose change was not kept').toEqual([]);
    expect(rows.filter(r => r.action === 'changed').length, 'the walk changed nothing at all').toBeGreaterThan(20);
  });
});
