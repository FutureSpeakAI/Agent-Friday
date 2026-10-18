/**
 * Camera mode: turning it on, what goes to Friday and when, turning it off, and a refusal.
 *
 * SCRATCH server only (FRIDAY_BASE must be set; the live :3000 is refused).
 *
 * Camera mode (index.html: the camera button in the top bar, CameraMode, sendChat) opens the
 * screen, the webcam or both, keeps the latest frame in the page, and attaches that frame to the
 * next message the owner types, as `image` on /api/chat/stream. Nothing is sent while nobody
 * types. Turning it off stops every track it opened.
 *
 * A CI runner has no camera and no screen to share, so navigator.mediaDevices is replaced before
 * the page loads (installMediaStub): getDisplayMedia and getUserMedia return a canvas stream, or
 * refuse with NotAllowedError, as each test asks, and every track handed out is kept so the test
 * can read its readyState. Everything above that (the button, the picture, the capture, the
 * send, the release) is the app's own code. /api/chat/stream and /api/work/forecast are answered
 * by the spec, so no model runs and every outgoing chat body is read as sent.
 */
import { test, expect, type Page } from '../fixtures';
import { bootApp } from '../harness';

const BASE = process.env.FRIDAY_BASE || '';
test.skip(!BASE || /:3000(\/|$)/.test(BASE), 'set FRIDAY_BASE to a scratch server (never the live :3000)');
test.use({ viewport: { width: 1500, height: 900 } });

const OFF_LABEL = 'Enable camera mode';
const ON_LABEL = 'Camera on, open controls';
const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

type Track = { kind: 'screen' | 'webcam'; state: string };
type MediaLog = { calls: { fn: string; video: boolean }[]; tracks: Track[] };

/** Fake screen and webcam: a painted canvas stream per request, or a refusal. */
async function installMediaStub(page: Page) {
  await page.addInitScript(() => {
    const w = window as any;
    const log = { calls: [] as any[], tracks: [] as any[], deny: { screen: false, webcam: false } };
    w.__zzCam = log;
    const stream = (kind: string) => {
      const c = document.createElement('canvas');
      c.width = 320; c.height = 240;
      c.style.cssText = 'position:fixed;left:-400px;top:0;width:320px;height:240px;pointer-events:none';
      (document.body || document.documentElement).appendChild(c);
      const g = c.getContext('2d')!;
      let n = 0;
      const paint = () => {
        n++;
        g.fillStyle = kind === 'screen' ? '#1d4f7a' : '#2f7a3a';
        g.fillRect(0, 0, 320, 240);
        g.fillStyle = '#fff'; g.font = '20px sans-serif'; g.fillText('zz-test ' + kind + ' ' + n, 20, 40);
      };
      paint();
      setInterval(paint, 100);
      const s = (c as any).captureStream(10) as MediaStream;
      s.getTracks().forEach(track => log.tracks.push({ kind, track }));
      return s;
    };
    const refuse = () => Promise.reject(new DOMException('Permission denied', 'NotAllowedError'));
    let md = navigator.mediaDevices as any;
    if (!md) { md = {}; Object.defineProperty(navigator, 'mediaDevices', { value: md, configurable: true }); }
    md.getDisplayMedia = (c: any) => {
      log.calls.push({ fn: 'getDisplayMedia', video: !!(c && c.video) });
      return log.deny.screen ? refuse() : Promise.resolve(stream('screen'));
    };
    md.getUserMedia = (c: any) => {
      log.calls.push({ fn: 'getUserMedia', video: !!(c && c.video) });
      return log.deny.webcam ? refuse() : Promise.resolve(stream('webcam'));
    };
    w.__zzCamRead = () => ({
      calls: log.calls.slice(),
      tracks: log.tracks.map((t: any) => ({ kind: t.kind, state: t.track.readyState })),
    });
  });
}

const media = (page: Page) => page.evaluate(() => (window as any).__zzCamRead()) as Promise<MediaLog>;
const deny = (page: Page, which: 'screen' | 'webcam') =>
  page.evaluate(k => { (window as any).__zzCam.deny[k] = true; }, which);
const camState = (page: Page) => page.evaluate(() => {
  const c = (window as any).fridayCamera;
  const f = c && c.latestFrame();
  return { on: !!(c && c.isOn()), frame: f ? String(f.primary || '') : '' };
});

type Sent = { url: string; body: any };

/** Open the app with the chat turn answered by the spec. Returns every chat body sent. */
async function open(page: Page) {
  const chats: Sent[] = [];
  const posts: string[] = [];
  page.on('request', r => {
    if (r.method() !== 'GET') posts.push(r.method() + ' ' + r.url() + ' ' + (r.postData() || ''));
  });
  await installMediaStub(page);
  await page.route('**/api/setup/status', r => r.fulfill(json({ initialized: true })));
  await page.route('**/api/work/forecast', r => r.fulfill(json({ will_pause: false })));
  await page.route('**/api/chat/stream', r => {
    const req = r.request();
    chats.push({ url: req.url(), body: req.postDataJSON() });
    const reply = 'zz-test reply ' + chats.length;
    const frames = [
      { started: true, turn_id: 'zz-test-turn-' + chats.length },
      { done: true, payload: { response: reply, vision_events: [], actions: [] } },
    ];
    return r.fulfill({
      status: 200,
      headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' },
      body: frames.map(f => 'data: ' + JSON.stringify(f) + '\n\n').join(''),
    });
  });
  await bootApp(page);
  await expect(page.getByTestId('friday-top-bar')).toHaveAttribute('data-ready', 'true', { timeout: 30000 });
  return { chats, posts };
}

/** Click a top-bar control, opening the bar's overflow first when the control is tucked in it. */
async function topbarClick(page: Page, selector: string, how: 'left' | 'right' = 'left') {
  const btn = page.locator(selector).first();
  await expect(btn, `The top bar has no control matching ${selector}`).toHaveCount(1);
  const tucked = await btn.evaluate(el => !!el.closest('[data-overflow="true"]'));
  if (tucked) {
    const more = page.getByRole('button', { name: 'More Friday controls' });
    if ((await more.getAttribute('aria-expanded')) !== 'true') await more.click();
  }
  await btn.click({ button: how });
}

const cameraButton = (page: Page, label: string) => page.locator(`button[aria-label="${label}"]`);

async function turnOn(page: Page) {
  await topbarClick(page, `button[aria-label="${OFF_LABEL}"]`);
}

async function waitForFrame(page: Page) {
  await expect.poll(async () => (await camState(page)).frame.length, {
    message: 'Camera mode is on but no frame was ever captured from the stream',
    timeout: 20000,
  }).toBeGreaterThan(100);
}

async function openChat(page: Page) {
  if (await page.locator('.chat-panel.open').count()) return;
  await topbarClick(page, 'button[aria-label^="Open chat with"]');
  await expect(page.locator('.chat-panel.open')).toHaveCount(1);
}

/** Type a message in the chat panel, send it with Enter, and wait for the reply. */
async function say(page: Page, chats: Sent[], text: string): Promise<Sent> {
  await openChat(page);
  const before = chats.length;
  const box = page.locator('.chat-panel.open textarea[data-chat-input]').first();
  await box.fill(text);
  await box.press('Enter');
  await expect.poll(() => chats.length, { message: `"${text}" never reached /api/chat/stream`, timeout: 20000 })
    .toBe(before + 1);
  await expect(page.locator('.chat-panel.open')).toContainText('zz-test reply ' + (before + 1), { timeout: 20000 });
  const sent = chats[before];
  expect(sent.body.message).toBe(text);
  return sent;
}

function expectOneJpegFrame(body: any) {
  expect(typeof body.image, 'The message went out with no camera frame attached').toBe('string');
  expect('screenshot' in body, 'The message carried a screenshot as well as the frame').toBe(false);
  const bytes = Buffer.from(body.image, 'base64');
  expect(bytes.length, 'The attached frame is too small to be a picture').toBeGreaterThan(500);
  expect([...bytes.subarray(0, 3)], 'The attached frame is not a JPEG').toEqual([0xff, 0xd8, 0xff]);
}

function expectNoFrame(body: any, when: string) {
  expect('image' in body, `A message sent ${when} carried a camera frame`).toBe(false);
  expect('screenshot' in body, `A message sent ${when} carried a screenshot`).toBe(false);
}

async function expectOff(page: Page) {
  await expect(cameraButton(page, OFF_LABEL), 'The camera button does not read as off').toHaveCount(1);
  await expect(cameraButton(page, ON_LABEL)).toHaveCount(0);
  await expect(page.locator('.camera-pip'), 'A camera picture is still on screen').toHaveCount(0);
  await expect(page.locator('.camera-control-panel'), 'The camera panel is still open').toHaveCount(0);
  const st = await camState(page);
  expect(st.on, 'window.fridayCamera still says Camera mode is on').toBe(false);
  expect(st.frame, 'A captured frame is still held after Camera mode went off').toBe('');
}

async function expectEveryTrackStopped(page: Page) {
  await expect.poll(async () => (await media(page)).tracks.filter(t => t.state !== 'ended'), {
    message: 'Camera mode went off but a camera or screen track is still live (the device is not released)',
    timeout: 10000,
  }).toEqual([]);
}

test('turning Camera mode on opens a stream, shows that it is on, and sends nothing by itself', async ({ page }) => {
  const { posts } = await open(page);
  expect((await media(page)).calls, 'Something opened a camera or screen before Camera mode was turned on').toEqual([]);
  expect((await camState(page)).on).toBe(false);

  await turnOn(page);

  await expect(cameraButton(page, ON_LABEL), 'The camera button does not read as on').toHaveCount(1);
  const pip = page.locator('.camera-pip[data-friday-camera-view="screen"]');
  await expect(pip, 'No live picture appeared').toBeVisible();
  await expect(pip).toContainText('LIVE');
  await expect(pip).toContainText('SCREEN');
  const panel = page.locator('.camera-control-panel');
  await expect(panel).toBeVisible();
  await expect(panel).toContainText('CAMERA MODE');
  await expect(panel).toContainText('Camera is active');
  // The default source is the screen: one screen-share request, one live video track.
  const m = await media(page);
  expect(m.calls).toEqual([{ fn: 'getDisplayMedia', video: true }]);
  expect(m.tracks).toEqual([{ kind: 'screen', state: 'live' }]);
  expect((await camState(page)).on).toBe(true);
  await expect(pip.locator('video')).toHaveJSProperty('paused', false);

  await waitForFrame(page);
  expect((await camState(page)).frame.startsWith('/9j/'), 'The captured frame is not a base64 JPEG').toBe(true);
  // Frames are captured every few seconds; none of them may leave the page while nobody types.
  await page.waitForTimeout(4000);   // longer than one capture interval: time is the behaviour under test
  const leaked = posts.filter(p => p.includes('/9j/'));
  expect(leaked.map(p => p.slice(0, 120)), 'A camera frame was sent although no message was typed').toEqual([]);
});

test('the next typed message carries exactly one frame; with Camera mode off a message carries none', async ({ page }) => {
  const { chats } = await open(page);

  const before = await say(page, chats, 'zz-test hello with the camera off');
  expectNoFrame(before.body, 'before Camera mode was turned on');

  await turnOn(page);
  await waitForFrame(page);
  const withCam = await say(page, chats, 'zz-test what can you see');
  expectOneJpegFrame(withCam.body);
  // One message, one request: the frame did not go out on a request of its own.
  expect(chats.length).toBe(2);
  expect(chats.map(c => new URL(c.url).pathname)).toEqual(['/api/chat/stream', '/api/chat/stream']);
});

for (const [way, stop] of [
  ['the × on the picture', async (page: Page) => {
    await page.locator('.camera-pip button[aria-label="Stop camera"]').first().click();
  }],
  ['Stop in the camera panel', async (page: Page) => {
    await page.locator('.camera-control-panel button', { hasText: /^Stop$/ }).click();
  }],
  ['a right-click on the camera button', async (page: Page) => {
    await topbarClick(page, `button[aria-label="${ON_LABEL}"]`, 'right');
  }],
] as const) {
  test(`turning Camera mode off with ${way} stops every track, and the next message carries no frame`, async ({ page }) => {
    const { chats } = await open(page);
    await openChat(page);
    await turnOn(page);
    await waitForFrame(page);
    // Switch to the webcam: the screen share is let go and the camera is opened.
    await page.locator('.camera-control-panel .src-btn', { hasText: /^webcam$/ }).click();
    await expect(page.locator('.camera-pip[data-friday-camera-view="webcam"]')).toBeVisible();
    await expect(page.locator('.camera-pip[data-friday-camera-view="screen"]')).toHaveCount(0);
    await expect.poll(async () => (await media(page)).tracks).toEqual([
      { kind: 'screen', state: 'ended' }, { kind: 'webcam', state: 'live' }]);
    expect((await media(page)).calls.map(c => c.fn)).toEqual(['getDisplayMedia', 'getUserMedia']);

    await stop(page);

    await expectOff(page);
    await expectEveryTrackStopped(page);
    const after = await say(page, chats, 'zz-test hello after the camera went off');
    expectNoFrame(after.body, 'after Camera mode was turned off');
  });
}

test('a refused screen share leaves Camera mode off, not stuck on', async ({ page }) => {
  const { chats } = await open(page);
  await deny(page, 'screen');
  await turnOn(page);
  await expect.poll(async () => (await media(page)).calls.length).toBe(1);
  await expectOff(page);
  expect((await media(page)).tracks).toEqual([]);
  const after = await say(page, chats, 'zz-test hello after the refusal');
  expectNoFrame(after.body, 'after the screen share was refused');
});

test('a refused webcam turns Camera mode off and lets the screen go', async ({ page }) => {
  const { chats } = await open(page);
  await turnOn(page);
  await waitForFrame(page);
  await deny(page, 'webcam');
  await page.locator('.camera-control-panel .src-btn', { hasText: /^webcam$/ }).click();
  await expect.poll(async () => (await media(page)).calls.map(c => c.fn)).toEqual(['getDisplayMedia', 'getUserMedia']);
  await expectOff(page);
  await expectEveryTrackStopped(page);
  const after = await say(page, chats, 'zz-test hello after the webcam was refused');
  expectNoFrame(after.body, 'after the webcam was refused');
});

test('with both sources and the webcam refused, the screen stays live and the webcam picture shows the error', async ({ page }) => {
  await open(page);
  await turnOn(page);
  await waitForFrame(page);
  await deny(page, 'webcam');
  await page.locator('.camera-control-panel .src-btn', { hasText: /^both$/ }).click();
  const cam = page.locator('.camera-pip[data-friday-camera-view="webcam"]');
  await expect(cam, 'The refused webcam shows no error').toContainText('NotAllowedError');
  await expect(page.locator('.camera-pip[data-friday-camera-view="screen"]')).toBeVisible();
  await expect(cameraButton(page, ON_LABEL)).toHaveCount(1);
  expect((await media(page)).tracks).toEqual([{ kind: 'screen', state: 'live' }]);
  expect((await camState(page)).on).toBe(true);
});
