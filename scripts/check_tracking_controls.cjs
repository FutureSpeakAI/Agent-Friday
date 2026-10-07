/* Exercise native tracking preferences through the rendered controls. Settings
 * writes stay in a browser route fixture; no camera or microphone is acquired. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {chromium, expect} = require('@playwright/test');
const base = process.env.FRIDAY_DESIGN_URL || 'http://127.0.0.1:3194/';
const output = process.env.FRIDAY_TRACKING_PROOFS;
const records = [];
const seed = {parallax_strength:.75, depth_strength:.6, head_smoothing:.65,
  head_response:.3, hand_gain:3.4, neutral_face_width:.22};
const gate = () => assert.ok(os.freemem() > 6 * 2 ** 30, 'At least 6 GB free is required');
const close = page => page.getByRole('button', {name:'Close Scene and depth', exact:true}).click();
const native = (page, key) => page.evaluate(k => window.FridayTracking?.get()?.[k], key);
async function open(page) {
  const trigger = page.getByRole('button', {name:'Scene and workspace depth', exact:true});
  if (!await trigger.isVisible()) await page.getByRole('button', {name:'More Friday controls', exact:true}).click();
  await trigger.click();
  const dialog = page.getByRole('dialog', {name:'Scene & depth', exact:true});
  await expect(dialog).toBeVisible();
  return dialog;
}
async function proof(page, name) {
  records.push({name, tracking:await page.evaluate(() => FridayTracking.get())});
  if (output) await page.screenshot({path:path.join(output, name+'.png'), animations:'disabled'});
  console.log('PASS '+name);
}

(async () => {
  const url = new URL(base);
  assert.ok(['127.0.0.1','localhost'].includes(url.hostname) && url.port === '3194', 'Use the isolated preview on port 3194');
  gate();
  if (output) {
    const relative = path.relative(path.resolve(__dirname, '..'), path.resolve(output));
    assert.ok(relative.startsWith('..'+path.sep) || path.isAbsolute(relative), 'Keep proof outputs outside the repository');
    fs.mkdirSync(output, {recursive:true});
  }
  const browser = await chromium.launch({headless:true, channel:'chrome'});
  try {
    const page = await browser.newPage({viewport:{width:1600,height:1000}, reducedMotion:'reduce'});
    page.setDefaultTimeout(15000);
    const errors = [], writes = [];
    let saved = null, responseShape = null, initializing = null, failNext = false, deviceRequests = 0;
    page.on('pageerror', error => errors.push(error.message));
    await page.exposeFunction('__recordTrackingDeviceRequest', () => { deviceRequests++; });
    await page.addInitScript(() => {
      localStorage.setItem('friday.display-style.v1', 'simple');
      window.__trackingDeviceRequests = 0;
      if (navigator.mediaDevices) navigator.mediaDevices.getUserMedia = () => {
        window.__trackingDeviceRequests++;
        void window.__recordTrackingDeviceRequest();
        return Promise.reject(new Error('This preference check never opens a camera or microphone.'));
      };
    });
    await page.route('**/api/settings', async route => {
      if (!initializing) initializing = (async () => {
        const upstream = await route.fetch({method:'GET'});
        responseShape = await upstream.json();
        saved = {...responseShape.settings, tracking:{...responseShape.settings?.tracking, ...seed}};
      })();
      await initializing;
      if (route.request().method() === 'POST') {
        const body = route.request().postDataJSON(), patch = body.settings || {};
        writes.push(body);
        if (failNext && patch.tracking) {
          failNext = false;
          return route.fulfill({status:500, json:{status:'error', error:'Synthetic save failure'}});
        }
        saved = {...saved, ...patch, tracking:{...saved.tracking, ...patch.tracking}};
      }
      await route.fulfill({json:{...responseShape, status:'ok', settings:saved}});
    });
    await page.goto(base);
    await page.waitForFunction(() => window.FridayTracking && typeof window.apiFetch === 'function');
    await expect.poll(() => native(page, 'parallax_strength')).toBe(seed.parallax_strength);
    let dialog = await open(page);
    for (const [label, key] of [['Head movement','parallax_strength'], ['Lean response','depth_strength'],
      ['Steadiness','head_smoothing'], ['Quick movement response','head_response']]) {
      await expect(dialog.getByRole('slider', {name:label, exact:true})).toHaveValue(String(seed[key]));
    }
    const status = () => dialog.locator('[data-holo-tracking-status]');
    const trackingWrites = () => writes.filter(body => body.settings?.tracking);
    const expectPartial = (body, patch) => assert.deepEqual(body, {settings:{tracking:patch}}, 'Save only the dials touched by this action');

    const head = dialog.getByRole('slider', {name:'Head movement', exact:true});
    await head.focus();
    await head.press('ArrowLeft');
    await expect.poll(() => native(page, 'parallax_strength')).toBe(.7);
    await expect(status()).toHaveText('Tracking preferences saved.');
    expectPartial(trackingWrites().at(-1), {parallax_strength:.7});
    assert.equal(saved.tracking.hand_gain, seed.hand_gain);
    assert.equal(saved.tracking.neutral_face_width, seed.neutral_face_width);

    const lean = dialog.getByRole('slider', {name:'Lean response', exact:true});
    await lean.scrollIntoViewIfNeeded();
    const bounds = await lean.boundingBox();
    const before = trackingWrites().length;
    await page.mouse.move(bounds.x + bounds.width * .45, bounds.y + bounds.height / 2);
    await page.mouse.down();
    const dragged = Number(await lean.inputValue());
    assert.notEqual(dragged, seed.depth_strength, 'Mouse movement must change the actual range');
    await expect.poll(() => native(page, 'depth_strength')).toBe(dragged);
    assert.equal(trackingWrites().length, before, 'Dragging previews without writing before release');
    await page.mouse.up();
    await expect(status()).toHaveText('Tracking preferences saved.');
    expectPartial(trackingWrites().at(-1), {depth_strength:dragged});
    const savedBeforeDepth = {...await page.evaluate(() => FridayTracking.get())};
    await dialog.locator('[data-holo-mode="quiet"]').click();
    assert.deepEqual(await page.evaluate(() => FridayTracking.get()), savedBeforeDepth, 'Decorative workspace depth must not change native tracking dials');
    await proof(page, 'native-comfort-keyboard-and-mouse');
    await close(page);
    dialog = await open(page);
    await expect(dialog.getByRole('slider', {name:'Head movement',exact:true})).toHaveValue('0.7');
    await expect(dialog.getByRole('slider', {name:'Lean response',exact:true})).toHaveValue(String(dragged));
    await close(page);
    await page.reload();
    await expect.poll(() => native(page, 'parallax_strength')).toBe(.7);
    await expect.poll(() => native(page, 'depth_strength')).toBe(dragged);
    dialog = await open(page);
    await expect(dialog.getByRole('slider', {name:'Head movement',exact:true})).toHaveValue('0.7');
    await proof(page, 'comfort-preferences-survive-reload');

    failNext = true;
    const steady = dialog.getByRole('slider', {name:'Steadiness',exact:true});
    await steady.focus(); await steady.press('ArrowRight');
    await expect.poll(() => native(page, 'head_smoothing')).toBe(.66);
    await expect(status()).toContainText('not saved');
    assert.equal(saved.tracking.head_smoothing, .65, 'A failed save cannot claim durable state');
    await proof(page, 'comfort-save-failure-is-visible');
    await close(page); dialog = await open(page);
    await expect(dialog.getByRole('slider', {name:'Steadiness',exact:true})).toHaveValue('0.66');
    await expect(status()).toContainText('not saved');
    await close(page); await page.reload();
    await expect.poll(() => native(page, 'head_smoothing')).toBe(.65);

    await page.getByRole('button', {name:'Switch workspace',exact:true}).click();
    const switcher = page.getByRole('dialog', {name:'Switch workspace',exact:true});
    await switcher.getByRole('searchbox').fill('Settings');
    await switcher.locator('.fx-switcher-option').click();
    const settings = page.locator('.st-root');
    await expect(settings).toBeVisible();
    await settings.getByRole('button', {name:'Voice & Tracking',exact:true}).click();
    const nativeHead = settings.getByRole('slider', {name:'Parallax strength',exact:true});
    await expect(nativeHead).toHaveValue('0.7');
    // The governed voice tool persists first, then delivers this public UI action.
    const voicePatch = {parallax_strength:.3, head_smoothing:.8};
    Object.assign(saved.tracking, voicePatch);
    await page.evaluate(patch => fridayRunActions([{type:'tracking',tracking:patch}]), voicePatch);
    await expect(nativeHead).toHaveValue('0.3');
    await expect(settings.getByRole('slider', {name:'Head smoothing',exact:true})).toHaveValue('0.8');
    const hand = settings.getByRole('slider', {name:'Sensitivity',exact:true});
    await hand.focus(); await hand.press('ArrowRight');
    await expect.poll(() => native(page, 'hand_gain')).toBe(3.5);
    await expect.poll(() => saved.tracking.hand_gain).toBe(3.5);
    expectPartial(trackingWrites().at(-1), {hand_gain:3.5});
    assert.equal(saved.tracking.parallax_strength, .3, 'Hand editing preserves the newer voice head setting');
    assert.equal(saved.tracking.head_smoothing, .8, 'Hand editing preserves the newer voice smoothing setting');
    await proof(page, 'mounted-settings-follow-voice-and-preserve-siblings');
    dialog = await open(page);
    await expect(dialog.getByRole('slider', {name:'Head movement',exact:true})).toHaveValue('0.3');
    await expect(dialog.getByRole('slider', {name:'Steadiness',exact:true})).toHaveValue('0.8');
    await proof(page, 'scene-controls-and-settings-share-native-state');
    await close(page);
    assert.equal(await page.evaluate(() => window.__trackingDeviceRequests), 0, 'Preference changes must not acquire camera or microphone');
    assert.equal(deviceRequests, 0, 'No earlier page load may acquire camera or microphone');
    assert.deepEqual(errors, [], 'Tracking controls must not throw application errors');
  } finally {
    if (output) fs.writeFileSync(path.join(output, 'tracking-controls.json'), JSON.stringify(records,null,2));
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
