/* Preview bridge to Friday's original scene, tracking filter, and camera owner.
 * This document keeps one renderer alive; moving the iframe does not recreate it.
 * Camera enable is accepted only from the review shell's explicit button action.
 */
(function () {
  'use strict';
  const PARENT_ORIGIN = 'http://127.0.0.1:3193';
  const OUT = 'friday-scene';
  let parentVisible = true;
  let bridgePaused = false;
  let previousHold = false;
  let trackingBusy = false;
  let requestedTracking = false;
  let mode = 'balanced';
  let preview = false;
  let readySent = false;
  let lastStatus = '';
  let tickCount = 0;
  let stopped = false;

  const style = document.createElement('style');
  style.textContent = `html,body{margin:0!important;width:100%;height:100%;overflow:hidden!important;background:#000103!important}
    body > :not(#friday-scene-canvas):not(script):not(style):not(link){display:none!important}
    #friday-scene-canvas{position:fixed!important;inset:0!important;display:block!important;width:100%!important;height:100%!important;outline:none}`;
  document.head.appendChild(style);
  document.documentElement.dataset.fridaySceneEmbed = 'true';

  // toggleHologram awaits MediaPipe before it calls this owner. Gate the
  // actual acquisition boundary too: Off or hidden can arrive during loading.
  // The owner's acquire() also stops an in-flight getUserMedia stream when
  // wanted becomes false. Both the load race and permission-prompt race close.
  const cameraOwner = window.FridayCamera;
  if (cameraOwner) {
    const originalStart = cameraOwner.start.bind(cameraOwner);
    cameraOwner.start = function (...args) {
      if (!requestedTracking || !parentVisible || document.hidden || stopped) {
        cameraOwner.stop();
        return;
      }
      return originalStart(...args);
    };
  }

  function send(type, data, requestId) {
    if (window.parent === window || stopped) return;
    window.parent.postMessage({ source: OUT, type, ...(data || {}), ...(requestId ? { requestId } : {}) }, PARENT_ORIGIN);
  }
  function fail(message, requestId) { send('error', { message }, requestId); }
  function clamp(n, lo, hi, fallback = 0) {
    return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : fallback;
  }
  function catalog() {
    return (window.fridayVibe?.getStructures?.() || []).map((s, index) => ({ index, id: s.id, name: s.name }));
  }
  function rendererReady() {
    return !!(window.__fridayRenderer && window.fridayDebugScene?.().scene);
  }
  function snapshot() {
    const cameraState = window.FridayCamera?.state;
    const head = window.FridayTracking?.head;
    const contextLost = typeof sceneContextLost !== 'undefined' && sceneContextLost;
    return {
      ready: rendererReady(), contextLost: !!contextLost,
      scene: window.fridayVibe?.getStructure?.() || null,
      transitionProgress: clamp(typeof transitionProgress === 'number' ? transitionProgress : 1, 0, 1, 1),
      mode, preview, paused: bridgePaused, visible: parentVisible && !document.hidden,
      trackingEnabled: !!window.fridayVibe?.isHologramOn?.(), trackingRequested: requestedTracking,
      trackingBusy, faceSeen: !preview && !!head?.seen && cameraState?.status === 'live',
      camera: { status: cameraState?.status || 'off', detail: cameraState?.detail || '', wanted: !!cameraState?.wanted },
      reducedMotion: window.matchMedia('(prefers-reduced-motion: reduce)').matches,
      capabilities: { originalScene: true, previewPose: !!window.FridayTracking?.debugHead, headTracking: true, pauseRendering: true },
    };
  }
  function status(requestId, force = false) {
    const data = snapshot();
    const signature = JSON.stringify(data);
    if (force || requestId || signature !== lastStatus) { lastStatus = signature; send('status', data, requestId); }
  }
  function clearPreview() {
    preview = false;
    window.FridayTracking?.debugHead(0, 0, null);
  }
  function syncVisibility() {
    const hidden = !parentVisible || document.hidden;
    // The original render loop checks callHold before simulation and rendering.
    // Save its value so this preview does not clear somebody else's hold.
    if (hidden && !bridgePaused) {
      previousHold = typeof callHold !== 'undefined' ? callHold : false;
      if (typeof callHold !== 'undefined') callHold = true;
      bridgePaused = true;
      window.FridayCamera?.stop();
      // Hiding always revokes this embed's camera intent. Returning to the
      // scene restores rendering, but camera use needs another explicit click.
      void setTracking(false);
    } else if (!hidden && bridgePaused) {
      if (typeof callHold !== 'undefined') callHold = previousHold;
      bridgePaused = false;
    }
    status(undefined, true);
  }
  async function setTracking(enabled, requestId) {
    requestedTracking = enabled;
    clearPreview();
    if (!enabled) window.FridayCamera?.stop();
    if (trackingBusy) { status(requestId, true); return; }
    trackingBusy = true;
    status(requestId, true);
    try {
      do {
        const desired = requestedTracking;
        if (desired !== !!window.fridayVibe?.isHologramOn?.()) {
          // The public wrapper deliberately returns void. The original lexical
          // function exposes its async load result, which lets stop win a race.
          if (typeof toggleHologram !== 'function') throw new Error('Original tracking controls are unavailable.');
          await toggleHologram();
        }
        if (desired !== requestedTracking) continue;
        if (desired && !window.fridayVibe?.isHologramOn?.()) {
          requestedTracking = false;
          fail('Head tracking could not load. The original tracker requires its MediaPipe assets to be available.', requestId);
        }
        break;
      } while (true);
      if (!parentVisible || document.hidden) {
        requestedTracking = false;
        window.FridayCamera?.stop();
      }
    } catch (error) {
      requestedTracking = false;
      window.FridayCamera?.stop();
      fail(error.message || 'Head tracking could not start.', requestId);
    } finally {
      trackingBusy = false;
      status(requestId, true);
    }
  }

  window.addEventListener('message', event => {
    if (event.origin !== PARENT_ORIGIN || event.source !== window.parent) return;
    const message = event.data;
    if (!message || typeof message !== 'object' || message.source !== 'friday-remake') return;
    const requestId = typeof message.requestId === 'string' ? message.requestId.slice(0, 100) : undefined;
    switch (message.type) {
      case 'scene:catalog':
        send('catalog', { scenes: catalog() }, requestId); status(requestId, true); break;
      case 'scene:status': status(requestId, true); break;
      case 'scene:set': {
        if (!rendererReady()) return fail('The original scene is still loading.', requestId);
        const index = message.index;
        if (!Number.isInteger(index) || index < 0 || index >= catalog().length) return fail('Unknown scene selection.', requestId);
        window.fridayVibe.setStructure(index);
        status(requestId, true); break;
      }
      case 'scene:tracking':
        if (typeof message.enabled !== 'boolean') return fail('Tracking needs an explicit on or off value.', requestId);
        if (message.enabled && message.userInitiated !== true) return fail('Use the Enable head tracking button to request the camera.', requestId);
        if (message.enabled && (!parentVisible || document.hidden)) return fail('Return to the visible scene before enabling tracking.', requestId);
        void setTracking(message.enabled, requestId); break;
      case 'scene:preview-pose': {
        if (!message.pose) { clearPreview(); status(requestId, true); break; }
        if (requestedTracking || window.fridayVibe?.isHologramOn?.()) return fail('Turn head tracking off before trying the camera-free preview.', requestId);
        const { x, y, z } = message.pose;
        if (![x, y, z].every(Number.isFinite)) return fail('The preview pose must have finite x, y and z values.', requestId);
        const tk = window.FridayTracking;
        if (!tk?.debugHead) return fail('Preview pose controls are not ready.', requestId);
        const boxW = (tk.head.baseline || .18) * Math.pow(2, clamp(z, -.6, .6));
        tk.debugHead(clamp(x, -.6, .6), clamp(y, -.5, .5), boxW);
        preview = true; status(requestId); break;
      }
      case 'scene:mode': {
        const values = { flat: [0, 0, 0], quiet: [.42, .35, .25], balanced: [1, 1, .6], immersive: [1.45, 1.25, .85] };
        if (!Object.hasOwn(values, message.mode)) return fail('Unknown depth mode.', requestId);
        mode = message.mode;
        const [parallax_strength, depth_strength, holo_cues] = values[mode];
        window.FridayTracking?.apply({ parallax_strength, depth_strength, holo_cues });
        status(requestId, true); break;
      }
      case 'scene:visibility':
        if (typeof message.visible !== 'boolean') return;
        parentVisible = message.visible; syncVisibility(); break;
      case 'scene:framing': {
        if (!Number.isFinite(message.factor)) return;
        const sceneCamera = window.fridayDebugScene?.().camera;
        if (!sceneCamera) return;
        // Change the original projection rather than enlarging rendered pixels.
        sceneCamera.fov = 60 / clamp(message.factor, 1, 5, 1);
        sceneCamera.updateProjectionMatrix();
        break;
      }
    }
  });

  document.addEventListener('visibilitychange', syncVisibility);
  window.addEventListener('pagehide', () => {
    stopped = true; requestedTracking = false; clearInterval(timer); clearPreview(); window.FridayCamera?.stop();
  });
  // One sampled pose stream at 25 Hz. Coordinates are the original renderer's
  // eased head position, not an invented webcam estimate or a pointer fallback.
  const timer = setInterval(() => {
    if (stopped) return;
    if (!readySent && rendererReady()) {
      readySent = true;
      send('ready', { ...snapshot(), scenes: catalog() });
    }
    if (++tickCount % 12 === 0) status();
    if (!readySent || bridgePaused || document.hidden) return;
    const head = window.FridayTracking?.head;
    if (!head) return;
    const cameraLive = window.FridayCamera?.state.status === 'live' && window.fridayVibe?.isHologramOn?.();
    const cssStyle = document.documentElement.style;
    const cssNumber = (name, fallback = 0) => {
      const value = parseFloat(cssStyle.getPropertyValue(name));
      return Number.isFinite(value) ? value : fallback;
    };
    send('pose', {
      pose: { x: clamp(typeof currFaceX === 'number' ? currFaceX : head.x, -1, 1),
        y: clamp(typeof currFaceY === 'number' ? currFaceY : head.y, -1, 1),
        z: clamp(typeof currFaceZ === 'number' ? currFaceZ : head.z, -1.5, 1.5),
        source: preview ? 'preview' : cameraLive && head.seen ? 'camera' : 'neutral',
        seen: !!head.seen, time: performance.now(),
        css: { parX: cssNumber('--holo-par-x'), parY: cssNumber('--holo-par-y'),
          tiltX: cssNumber('--holo-tilt-x'), tiltY: cssNumber('--holo-tilt-y'),
          depth: cssNumber('--holo-depth', 1), rim: cssNumber('--holo-rim') } },
    });
  }, 40);
  syncVisibility();
})();
