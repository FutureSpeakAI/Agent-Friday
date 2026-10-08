/* One scene, shared with the desktop. This controller owns only tracking it
 * acquired itself; existing head, hand, voice and call controls retain ownership. */
(function (host, factory) {
    'use strict';
    const implementation = factory();
    if (typeof module === 'object' && module.exports) module.exports = implementation;
    if (host && host.document) host.FridayHolographicWorkspace = implementation.mount(host);
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';
    const OWNER = 'workspace-depth';
    const MODES = ['flat', 'quiet', 'balanced', 'immersive'];
    const ARRANGEMENTS = ['companion', 'present', 'focus'];
    const TRACKING_DIALS = [
        { key:'parallax_strength', label:'Head movement', min:0, max:2.5, step:.05, help:'Lower values make looking around calmer. Zero stops sideways parallax.' },
        { key:'depth_strength', label:'Lean response', min:0, max:2.5, step:.05, help:'How much leaning closer changes the view. Zero keeps its size steady.' },
        { key:'head_smoothing', label:'Steadiness', min:0, max:1, step:.01, help:'Higher values smooth out small movements and camera shake.' },
        { key:'head_response', label:'Quick movement response', min:0, max:1, step:.01, help:'Lower values follow a quick lean more gently.' }
    ];

    function trackingTuner(win, notify = () => {}) {
        let pending = Promise.resolve(), revision = 0;
        const unsaved = new Map();
        const read = () => win.FridayTracking?.get?.() || {};
        const live = patch => { win.FridayTracking?.apply?.(patch); return read(); };
        function save(patch) {
            const edit = {...patch};
            // Camera distance is a browser/device binding. A full live cfg
            // must not overwrite the preserved legacy account calibration.
            delete edit.neutral_face_width;
            if (!Object.keys(edit).length) {
                const error = new Error('Use Calibrate distance for this camera.');
                notify(error.message); return Promise.reject(error);
            }
            live(edit);
            const mine = ++revision;
            Object.keys(edit).forEach(key => unsaved.set(key, mine));
            notify('Saving tracking preferences…');
            const task = pending.catch(() => {}).then(async () => {
                if (typeof win.apiFetch !== 'function') throw new Error('Settings are still loading.');
                const response = await win.apiFetch('/api/settings', { method:'POST',
                    headers:{'Content-Type':'application/json'}, body:JSON.stringify({settings:{tracking:edit}}) });
                const result = await response.json();
                if (!response.ok || result.status !== 'ok') throw new Error('Your preference could not be saved.');
                Object.keys(edit).forEach(key => { if (unsaved.get(key) === mine) unsaved.delete(key); });
                if (mine === revision) notify(unsaved.size
                    ? 'Some tracking preferences are not saved. Try those controls again.'
                    : 'Tracking preferences saved.');
                return result;
            });
            pending = task;
            return task.catch(error => {
                if (mine === revision) notify('Preview applied, but not saved. Try the control again.');
                throw error;
            });
        }
        return { read, live, save };
    }

    function cameraCalibration(win, notify = () => {}) {
        const key = 'friday_camera_calibration_v1', limit = 8;
        const validWidth = value => Number.isFinite(value) && value > .02 && value <= 2;
        const validId = value => typeof value === 'string' && value.length > 0 && value.length <= 512;
        let bindings = [], observedId, publishing = false, lastStatus = '';
        try {
            const raw = win.localStorage.getItem(key);
            const stored = raw && raw.length <= 32768 ? JSON.parse(raw) : null;
            if (stored?.version === 1 && Array.isArray(stored.cameras)) {
                for (const entry of stored.cameras.slice(-limit)) {
                    if (!validId(entry?.id) || !validWidth(entry?.width)) continue;
                    bindings = bindings.filter(item => item.id !== entry.id);
                    bindings.push({id:entry.id,width:entry.width,saved:true});
                }
            }
        } catch (_) { /* An unreadable device preference is never an account calibration. */ }
        function identity() {
            try {
                const id = win.fridayVibe?.getTrackedCameraId?.();
                return validId(id) ? id : null;
            } catch (_) { return null; }
        }
        function current() {
            const id = identity(), binding = id && bindings.find(item => item.id === id);
            const live = win.FridayCamera?.state?.status === 'live';
            const status = binding ? (binding.saved ? 'Distance calibrated for this camera in this browser.'
                : 'Calibrated for this session. This browser could not save the camera calibration.')
                : id ? 'Calibrate distance for this camera.'
                : live ? 'Camera identity is unavailable. Calibrate distance for this camera when its identity is available.'
                : 'Enable head tracking, then Calibrate distance for this camera.';
            return {id,binding,view:{verified:!!binding,saved:!!binding?.saved,width:binding?.width || 0,status}};
        }
        function publish(view, id) {
            const signature = JSON.stringify([id,view]);
            if (signature === lastStatus) return;
            lastStatus = signature;
            notify({...view});
            // Device identity remains in this browser's binding store; status
            // events expose no camera identifier to the desktop action bus.
            if (win.dispatchEvent && win.CustomEvent) win.dispatchEvent(new win.CustomEvent('friday:camera-calibration',{detail:{...view}}));
        }
        function sync() {
            const {id,view} = current(), tk = win.FridayTracking;
            if (publishing) return {...view};
            publishing = true;
            try {
                const changed = observedId !== id;
                observedId = id;
                if (tk && (changed || Number(tk.get?.().neutral_face_width || 0) !== view.width)) {
                    // Clear filter history when cameras change. This touches
                    // only the live tracker, never the legacy account value.
                    if (changed) tk.calibrate?.(view.width);
                    tk.apply?.({neutral_face_width:view.width});
                }
                publish(view,id);
            } finally { publishing = false; }
            return {...view};
        }
        function persist(next) {
            try {
                win.localStorage.setItem(key,JSON.stringify({version:1,cameras:next.map(({id,width})=>({id,width}))}));
                next.forEach(entry => { entry.saved = true; });
                return true;
            } catch (_) { return false; }
        }
        function calibrateCurrent() {
            const id = identity();
            let width;
            try { width = win.fridayVibe?.getTrackedFaceWidth?.(); } catch (_) { width = null; }
            if (!id) {
                const view = sync();
                return {ok:false,...view,status:'Camera identity is unavailable. Calibrate distance for this camera when its identity is available.'};
            }
            if (!validWidth(width) || identity() !== id || !win.FridayTracking?.calibrate) {
                sync();
                return {ok:false,saved:false,status:'No fresh face seen. Enable head tracking and look at the camera before calibrating.'};
            }
            const next = bindings.filter(entry=>entry.id !== id).concat({id,width,saved:false}).slice(-limit);
            persist(next); bindings = next;
            win.FridayTracking.calibrate(width);
            const view = sync();
            return {ok:true,...view};
        }
        function resetCurrent() {
            const id = identity();
            if (!id) return {ok:false,saved:false,status:'No identified camera is active.'};
            const next = bindings.filter(entry=>entry.id !== id), saved = persist(next);
            // A failed removal remains unsaved; do not pretend the stored
            // binding has gone or silently restore it on the next frame.
            bindings = next;
            win.FridayTracking?.calibrate?.(0); sync();
            return {ok:true,saved,status:saved?'Calibrate distance for this camera.':'Reset for this session. This browser could not save the reset.'};
        }
        return {sync,calibrateCurrent,resetCurrent,
            filterSettings:patch=>({...patch,neutral_face_width:current().view.width}),
            get state() { return {...current().view}; }};
    }

    // Enclosing scenery is authored for a camera inside a world. Framing that
    // entire world as an object exposes its carrier and dwarfs the character.
    const sceneryStates = new WeakMap(), fieldUniforms = new WeakMap();
    function prepareStageField(material) {
        let uniforms = fieldUniforms.get(material);
        if (uniforms) return uniforms;
        uniforms = { uFridayFieldStage: {value:0} };
        fieldUniforms.set(material, uniforms);
        const previous = material.onBeforeCompile;
        material.onBeforeCompile = function (shader, renderer) {
            previous?.call(this, shader, renderer);
            Object.assign(shader.uniforms, uniforms);
            shader.vertexShader = 'varying float vFridayFieldEdge;\n' + shader.vertexShader;
            shader.vertexShader = shader.vertexShader.replace('#include <begin_vertex>',
                '#include <begin_vertex>\nvFridayFieldEdge = 1.0 - smoothstep(12.0, 18.0, length(position.xz));');
            shader.fragmentShader = 'uniform float uFridayFieldStage; varying float vFridayFieldEdge;\n' + shader.fragmentShader;
            shader.fragmentShader = shader.fragmentShader.replace('#include <color_fragment>',
                '#include <color_fragment>\ndiffuseColor.a *= mix(1.0, vFridayFieldEdge, uFridayFieldStage);');
        };
        const previousKey = material.customProgramCacheKey.bind(material);
        material.customProgramCacheKey = () => previousKey() + ':friday-field-edge';
        material.needsUpdate = true;
        return uniforms;
    }
    function setStageScenery(structures, enabled) {
        for (const group of Object.values(structures || {})) {
            if (!group || sceneryStates.get(group) === enabled) continue;
            group.traverse(node => {
                if (node.userData?.fridayStageScenery) node.visible = !enabled;
                if (node.userData?.fridayStageField && node.material) prepareStageField(node.material).uFridayFieldStage.value = enabled ? 1 : 0;
            });
            sceneryStates.set(group, enabled);
        }
    }
    const pointMaterials = new WeakMap(), authoredPointSizes = new WeakMap(), pointFloors = new WeakMap();
    function prepareStagePoints(material) {
        let uniform = pointFloors.get(material);
        if (uniform) return uniform;
        uniform = {value:0}; pointFloors.set(material, uniform);
        const previous = material.onBeforeCompile, previousKey = material.customProgramCacheKey.bind(material);
        material.onBeforeCompile = function (shader, renderer) {
            previous?.call(this, shader, renderer);
            shader.uniforms.uFridayStagePointFloor = uniform;
            shader.vertexShader = 'uniform float uFridayStagePointFloor;\n' + shader.vertexShader;
            shader.vertexShader = shader.vertexShader.replace('#include <fog_vertex>',
                'gl_PointSize = max(gl_PointSize, uFridayStagePointFloor);\n#include <fog_vertex>');
        };
        material.customProgramCacheKey = () => previousKey() + ':friday-stage-points';
        material.needsUpdate = true;
        return uniform;
    }
    function scaleStagePoints(structures, factor, stagePixelRatio = 0) {
        factor = Number.isFinite(factor) ? Math.max(.5, Math.min(4, factor)) : 1;
        for (const group of Object.values(structures || {})) {
            if (!group) continue;
            let materials = pointMaterials.get(group);
            if (!materials) {
                materials = new Map();
                group.traverse(node => {
                    const list = Array.isArray(node.material) ? node.material : [node.material];
                    list.forEach(material => {
                        if (material?.isPointsMaterial && material.sizeAttenuation) {
                            materials.set(material, Number(node.userData?.fridayStagePointFloor) || 0);
                            if (!authoredPointSizes.has(material)) authoredPointSizes.set(material, material.size);
                        } else if (material?.uniforms?.uFridayStagePointFloor) {
                            materials.set(material, Number(node.userData?.fridayStagePointFloor) || 0);
                        }
                    });
                });
                pointMaterials.set(group, materials);
            }
            materials.forEach((floor, material) => {
                if (authoredPointSizes.has(material)) material.size = authoredPointSizes.get(material) * factor;
                // A distant fitted terrain still needs a visible luminous
                // core. The floor is in screen pixels and leaves Classic's
                // authored point size, texture, color and opacity untouched.
                if (floor) (material.uniforms?.uFridayStagePointFloor || prepareStagePoints(material)).value = floor * Math.max(0, stagePixelRatio);
            });
        }
    }

    function spatialLayout(area, options) {
        if (!options.enabled) return { layout: 'classic', content: { ...area }, stage: null };
        const gap = 16, content = { ...area };
        // A short landscape viewport has room beside the work, not above it.
        // Preserve useful avatar height while the work region stays scrollable.
        if (options.shortLandscape) {
            const width = Math.min(200, Math.max(80, Math.round(area.w * .27)), Math.max(1, area.w - 176));
            const stage = { x: options.rightChat ? area.x : area.x + area.w - width, y: area.y, w: width, h: area.h };
            content.w = Math.max(1, area.w-width-gap);
            if (options.rightChat) content.x += width+gap;
            return { layout: 'compact', stage, content };
        }
        if (options.compact || options.viewportWidth < 1100 || area.w < 680) {
            const height = Math.max(110, Math.min(230, Math.round(area.h * .30)));
            const stage = { x: area.x, y: area.y, w: area.w, h: Math.min(height, Math.max(1, area.h - 160)) };
            content.y += stage.h + gap; content.h = Math.max(1, content.h - stage.h - gap);
            return { layout: 'compact', stage, content };
        }
        const fraction = options.arrangement === 'present' ? .42 : options.arrangement === 'focus' ? .21 : .30;
        const width = Math.min(Math.max(220, Math.round(area.w * fraction)), Math.max(180, area.w - 440 - gap));
        const stage = { x: options.rightChat ? area.x : area.x + area.w - width, y: area.y, w: width, h: area.h };
        content.w = Math.max(1, area.w - width - gap);
        if (options.rightChat) content.x += width + gap;
        return { layout: 'wide', stage, content };
    }

    const LAYOUT_MS = 300;
    const copyLayout = layout => JSON.parse(JSON.stringify(layout));
    function separatingSides(layout) {
        const stage = layout.stageFrame || layout.stage, content = layout.content;
        if (!stage || !content) return [];
        const sides = [];
        if (stage.x + stage.w <= content.x) sides.push('left');
        if (content.x + content.w <= stage.x) sides.push('right');
        if (stage.y + stage.h <= content.y) sides.push('above');
        if (content.y + content.h <= stage.y) sides.push('below');
        return sides;
    }
    function canAnimateLayout(from, to) {
        // A common separating half-plane stays separating under interpolation.
        // A side swap or orientation change must settle before paint instead
        // of flying the character through readable content.
        const workSafe = separatingSides(from).some(side => separatingSides(to).includes(side));
        const chatSafe = !from.chat || !to.chat || separatingSides({...from,content:from.chat})
            .some(side => separatingSides({...to,content:to.chat}).includes(side));
        return from.layout === to.layout && !!from.chat === !!to.chat && workSafe && chatSafe;
    }
    function interpolateLayout(from, to, progress) {
        const p = Math.max(0, Math.min(1, progress));
        if (!canAnimateLayout(from, to)) return copyLayout(to);
        const result = { ...to };
        for (const key of ['content', 'stageFrame', 'stage', 'caption', 'chat']) {
            if (!from[key] || !to[key]) continue;
            result[key] = {};
            for (const axis of ['x', 'y', 'w', 'h']) result[key][axis] = from[key][axis] + (to[key][axis] - from[key][axis]) * p;
        }
        return result;
    }
    function createLayoutMotion(initial) {
        let current = copyLayout(initial), target = copyLayout(initial), start = null;
        let startedAt = 0, lastAt = 0, slowFrames = 0, fallbackUntil = 0;
        return {
            request(next, now, options = {}) {
                if (JSON.stringify(next) === JSON.stringify(target) && !options.immediate) return false;
                target = copyLayout(next);
                if (options.immediate || !options.enabled || now < fallbackUntil || !canAnimateLayout(current, target)) {
                    current = copyLayout(target); start = null;
                } else { start = copyLayout(current); startedAt = lastAt = now; slowFrames = 0; }
                return true;
            },
            frame(now, held = false) {
                if (!start) return current;
                if (held) { startedAt += Math.max(0, now-lastAt); lastAt = now; return current; }
                if (now-lastAt > 48) slowFrames++; else slowFrames = 0;
                lastAt = now;
                if (slowFrames >= 3) { fallbackUntil = now+5000; current = copyLayout(target); start = null; return current; }
                const t = Math.max(0, Math.min(1, (now-startedAt)/LAYOUT_MS));
                current = interpolateLayout(start, target, t*t*(3-2*t));
                if (t === 1) start = null;
                return current;
            },
            settle() { current = copyLayout(target); start = null; return current; },
            get current() { return current; }, get target() { return target; },
            get active() { return !!start; }, get fallbackUntil() { return fallbackUntil; }
        };
    }

    function stageProjection(camera, stage, width, height) {
        if (!stage || !(width > 0 && height > 0)) return;
        const sx = stage.w / width, sy = stage.h / height;
        const ox = (stage.x * 2 + stage.w) / width - 1, oy = 1 - (stage.y * 2 + stage.h) / height;
        const elements = camera.projectionMatrix.elements;
        for (let column = 0; column < 4; column++) {
            const i = column * 4;
            elements[i] = sx * elements[i] + ox * elements[i + 3];
            elements[i + 1] = sy * elements[i + 1] + oy * elements[i + 3];
        }
        camera.projectionMatrixInverse?.copy(camera.projectionMatrix).invert();
    }

    function projectedSphere(camera, sphere, width, height) {
        const c = sphere.center.clone().applyMatrix4(camera.matrixWorldInverse), r = sphere.radius, d = -c.z;
        if (!(d > r)) return null;
        const e = camera.projectionMatrix.elements, den = d * d - r * r;
        const extent = (axis, scale, offset) => {
            const spread = r * Math.sqrt(axis * axis + den);
            return [scale * (axis * d - spread) / den - offset, scale * (axis * d + spread) / den - offset];
        };
        const x = extent(c.x, e[0], e[8]), y = extent(c.y, e[5], e[9]);
        return { x:(x[0]+1)*width/2, y:(1-y[1])*height/2, w:(x[1]-x[0])*width/2, h:(y[1]-y[0])*height/2 };
    }

    function projectedPieces(camera, pieces, width, height) {
        let left = Infinity, top = Infinity, right = -Infinity, bottom = -Infinity;
        for (const piece of pieces) {
            let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity, valid = true;
            const point = piece.box.min.clone();
            for (let corner = 0; corner < 8; corner++) {
                point.set(corner & 1 ? piece.box.max.x : piece.box.min.x,
                    corner & 2 ? piece.box.max.y : piece.box.min.y,
                    corner & 4 ? piece.box.max.z : piece.box.min.z);
                point.applyMatrix4(piece.matrix).applyMatrix4(camera.matrixWorldInverse);
                if (point.z >= -camera.near) { valid = false; break; }
                point.applyMatrix4(camera.projectionMatrix);
                const x = (point.x+1)*width/2, y = (1-point.y)*height/2;
                x0 = Math.min(x0,x); y0 = Math.min(y0,y); x1 = Math.max(x1,x); y1 = Math.max(y1,y);
            }
            const sphere = piece.sphere && projectedSphere(camera,piece.sphere,width,height);
            if (!valid && !sphere) return null;
            // Both volumes contain the geometry. Their screen-space
            // intersection is tighter than either diagonal envelope alone.
            if (sphere) {
                x0 = valid ? Math.max(x0,sphere.x) : sphere.x;
                y0 = valid ? Math.max(y0,sphere.y) : sphere.y;
                x1 = valid ? Math.min(x1,sphere.x+sphere.w) : sphere.x+sphere.w;
                y1 = valid ? Math.min(y1,sphere.y+sphere.h) : sphere.y+sphere.h;
            }
            left=Math.min(left,x0);top=Math.min(top,y0);right=Math.max(right,x1);bottom=Math.max(bottom,y1);
        }
        return Number.isFinite(left) && right>left && bottom>top ? {x:left,y:top,w:right-left,h:bottom-top} : null;
    }

    function stageFill(bounds, width, height, padding, headroom = 1) {
        if (!bounds || !(bounds.w>0 && bounds.h>0)) return {scale:1,x:0,y:0};
        padding=Math.max(0,Math.min(padding,width/4,height/4));
        const scale = Math.min((width-padding*2)/bounds.w,(height-padding*2)/bounds.h) / Math.max(1, headroom);
        return {scale, x:(width/2-bounds.x-bounds.w/2)*scale, y:(height/2-bounds.y-bounds.h/2)*scale};
    }

    function ambientStageUniforms(material, stage, viewportHeight, pixelRatio) {
        const u=material?.uniforms;
        if(!u?.uStageEnabled||!u.uStageRect||!u.uStageFeather)return;
        const enabled=stage&&stage.w>0&&stage.h>0&&viewportHeight>0;
        u.uStageEnabled.value=enabled?1:0;
        if(!enabled)return;
        const ratio=Number.isFinite(pixelRatio)&&pixelRatio>0?pixelRatio:1;
        u.uStageRect.value.set(stage.x*ratio,(viewportHeight-stage.y-stage.h)*ratio,(stage.x+stage.w)*ratio,(viewportHeight-stage.y)*ratio);
        u.uStageFeather.value=Math.max(1,Math.min(18,stage.w/6,stage.h/6))*ratio;
    }

    function constrainSpatialRect(rect, area) {
        if (!area) return { ...rect };
        const w = Math.max(1, Math.min(Number.isFinite(rect.w) ? rect.w : rect.width || area.w, area.w));
        const h = Math.max(1, Math.min(Number.isFinite(rect.h) ? rect.h : rect.height || area.h, area.h));
        const x = Math.max(area.x, Math.min(Number.isFinite(rect.x) ? rect.x : area.x, area.x + area.w - w));
        const y = Math.max(area.y, Math.min(Number.isFinite(rect.y) ? rect.y : area.y, area.y + area.h - h));
        return { ...rect, x, y, w, h, ...('width' in rect ? {width:w} : {}), ...('height' in rect ? {height:h} : {}) };
    }

    function fitRadialDistance(position, target, distance) {
        if (!(Number.isFinite(distance) && distance > 0)) return position;
        const direction = position.clone().sub(target);
        if (direction.lengthSq() < 1e-12) direction.set(0,0,1);
        return position.copy(target).add(direction.normalize().multiplyScalar(distance));
    }

    function trackingSession(vibe) {
        let lease = null, sequence = 0, busy = false;
        const state = () => vibe.getHologramState ? vibe.getHologramState() : { enabled: !!vibe.isHologramOn?.() };
        const owns = () => { const live = state(); return !!lease && live.enabled && live.owner === OWNER && live.revision === lease.revision; };
        const release = () => {
            sequence++; busy = false;
            const previous = lease; lease = null;
            if (previous && vibe.releaseHologram) return vibe.releaseHologram(OWNER, previous.revision);
            return Promise.resolve(false);
        };
        return {
            get owns() { return owns(); }, get busy() { return busy; },
            get superseded() { const live = state(); return !!lease && (live.owner !== OWNER || live.revision !== lease.revision); },
            release,
            async acquire() {
                if (busy) return state();
                if (state().enabled) return state();
                if (!vibe.setHologram || !vibe.getHologramState || !vibe.releaseHologram) {
                    throw new Error('Head tracking controls are still loading. Try again in a moment.');
                }
                const attempt = ++sequence; busy = true;
                try {
                    const pending = vibe.setHologram(true, OWNER);
                    const current = state();
                    if (current.owner === OWNER) lease = { revision: current.revision };
                    const result = await pending;
                    if (attempt !== sequence) return state();
                    if (!result.enabled) throw new Error(result.error || 'Head tracking could not start.');
                    return result;
                } catch (error) {
                    if (attempt === sequence) await release();
                    throw error;
                } finally { if (attempt === sequence) busy = false; }
            }
        };
    }

    function mount(win) {
        if (win.FridayHolographicWorkspace) return win.FridayHolographicWorkspace;
        const doc = win.document, root = doc.documentElement;
        const esc = text => String(text == null ? '' : text).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
        const upper = text => text[0].toUpperCase() + text.slice(1);
        const state = { mode: 'balanced', source: 'off', arrangement: 'companion', motion: 'on', error: '', preferenceError: '', open: false };
        try {
            const preferences = JSON.parse(win.localStorage.getItem('friday_holographic_workspace_v1') || '{}');
            if (MODES.includes(preferences.mode)) state.mode = preferences.mode;
            if (ARRANGEMENTS.includes(preferences.arrangement)) state.arrangement = preferences.arrangement;
            if (preferences.motion === 'off') state.motion = 'off';
        } catch (_) { /* Display preferences are optional. Camera intent is never persisted. */ }
        let dialog = null, opener = null, depth = null, session = null, sessionVibe = null;
        let ownedPreview = null, timer = null, observer = null, destroyed = false, signature = '';
        let spatialSignature = '', pointerAt = 0, selectSerial = 0;
        let chromeObserver = null, chromeFrame = 0, chromeUntil = 0, ownedTopbar = false;
        const observedChrome = new Set(), previousTopbar = root.style.getPropertyValue('--fr-topbar-h');
        let occupancy = { layout: 'classic', content: null, stage: null };
        const layoutMotion = createLayoutMotion(occupancy), surfaces = new Map(), interactionHolds = new Set();
        let layoutFrame = 0, applyingLayout = false, viewportKey = '', activityUntil = 0, composing = false;
        const heldPointers = new Set();
        let mouseHeld = false;
        let caption = null, captionSignature = '';
        const captionNodes = new Map();
        let fitBounds = null, fitKey = '', fitAt = 0, fittedDistance = 0, projectedBounds = null;
        let trackingStatus = '';
        const tuner = trackingTuner(win, message => { trackingStatus = message; refreshTracking(); });
        const calibration = cameraCalibration(win, () => refreshTracking());
        const originalFog = new WeakMap();
        const tagged = new Map();
        const reduced = win.matchMedia('(prefers-reduced-motion: reduce)');
        const simpleStyle = () => win.FridayDisplayStyle ? win.FridayDisplayStyle.get() === 'simple' : doc.body?.classList.contains('friday-experience-enabled');

        function persist() {
            try {
                win.localStorage.setItem('friday_holographic_workspace_v1', JSON.stringify({ mode: state.mode, arrangement: state.arrangement, motion: state.motion }));
                state.preferenceError = '';
            } catch (_) { state.preferenceError = 'Applied for now. This browser could not save your layout preferences.'; }
        }
        function interactionHeld(now = win.performance.now()) {
            return composing || mouseHeld || heldPointers.size > 0 || interactionHolds.size > 0 || now < activityUntil ||
                !!(win.FridayTracking?.hand?.seen && (win.FridayHandCursor?.locked || win.FridayTracking.hand.pinching));
        }
        function holdLayout(owner) {
            const token = {owner};
            interactionHolds.add(token);
            return () => { interactionHolds.delete(token); activityUntil = win.performance.now()+180; scheduleLayout(); };
        }
        function onDirectInput(event) {
            if (!simpleStyle() && !['pointerup','pointercancel','lostpointercapture','mouseup','compositionend','blur'].includes(event.type)) return;
            if (event.type === 'compositionstart') composing = true;
            if (event.type === 'compositionend') composing = false;
            if (event.type === 'pointerdown') heldPointers.add(event.pointerId);
            if (['pointerup','pointercancel','lostpointercapture'].includes(event.type)) heldPointers.delete(event.pointerId);
            if (event.type === 'mousedown') mouseHeld = true;
            if (event.type === 'mouseup') mouseHeld = false;
            if (event.type === 'blur') { composing = mouseHeld = false; heldPointers.clear(); }
            // Focus itself is not a hold: a requested arrangement must still
            // work after a keyboard user has focused its button.
            activityUntil = win.performance.now() + (event.type === 'input' || event.type === 'keydown' ? 420 : 180);
            scheduleLayout();
        }
        function surfaceArea(kind) {
            return occupancy.stage ? (kind === 'chat' && occupancy.chat ? occupancy.chat : occupancy.content) : null;
        }
        function updateSurface(element) {
            const entry = surfaces.get(element), area = entry && surfaceArea(entry.kind);
            if (!entry || !element.isConnected) return;
            if (!area) { clearSurface(element, entry); return; }
            let request;
            try { request = entry.getRect(); } catch (_) { /* A disappearing owner cannot break other surfaces' containment. */ }
            if (!request || !['x','y','w','h'].every(key => Number.isFinite(request[key])) || request.w <= 0 || request.h <= 0) {
                const bounds = element.getBoundingClientRect();
                request = entry.lastRect || {x:bounds.x,y:bounds.y,w:Math.max(1,bounds.width),h:Math.max(1,bounds.height)};
            }
            entry.lastRect = {...request};
            const rect = constrainSpatialRect(request, area);
            element.dataset.fridaySpatialSurface = entry.kind;
            for (const axis of ['x','y','w','h']) element.style.setProperty('--friday-surface-'+axis, rect[axis]+'px');
        }
        function clearSurface(element, entry) {
            for (const axis of ['x','y','w','h']) {
                const name = '--friday-surface-'+axis, previous = entry.previous[name];
                if (previous) element.style.setProperty(name, previous); else element.style.removeProperty(name);
            }
            if (entry.marker == null) element.removeAttribute('data-friday-spatial-surface');
            else element.setAttribute('data-friday-spatial-surface', entry.marker);
        }
        function registerSurface(element, options = {}) {
            if (!element || typeof options.getRect !== 'function') return () => {};
            if (surfaces.has(element)) clearSurface(element, surfaces.get(element));
            const entry = { kind: ['window','overlay','chat'].includes(options.kind) ? options.kind : 'overlay',
                getRect: options.getRect, marker: element.getAttribute('data-friday-spatial-surface'), previous: {} };
            for (const axis of ['x','y','w','h']) entry.previous['--friday-surface-'+axis] = element.style.getPropertyValue('--friday-surface-'+axis);
            surfaces.set(element, entry); updateSurface(element);
            return () => { if (surfaces.get(element) === entry) { clearSurface(element, entry); surfaces.delete(element); } };
        }
        function cameraSession() {
            if (!session || sessionVibe !== win.fridayVibe) {
                sessionVibe = win.fridayVibe;
                if (sessionVibe) session = trackingSession(sessionVibe);
            }
            return session;
        }
        function sample() {
            const vibe = win.fridayVibe;
            if (vibe?.getWorkspaceSceneState) return vibe.getWorkspaceSceneState();
            return { ready: !!win.__fridayRenderer, scene: vibe?.getStructure?.(), transitionProgress: 1,
                hologram: vibe?.getHologramState?.() || { enabled: !!vibe?.isHologramOn?.() },
                camera: win.FridayCamera?.state || { status: 'off', wanted: false },
                head: { x: 0, y: 0, z: 0, seen: false } };
        }
        function clearPreview() {
            const tk = win.FridayTracking;
            if (ownedPreview && tk?.head.debug === ownedPreview) tk.debugHead(0, 0, null);
            ownedPreview = null;
        }
        function ensureDepth() {
            if (!simpleStyle()) return;
            if (!depth && win.FridayWorkspaceDepth) {
                depth = new win.FridayWorkspaceDepth({ root }); depth.setMode(state.mode); depth.setSource(state.source);
            }
        }
        function workspaces() {
            if (doc.body?.dataset.fridayHomeVisible === 'true') return [];
            return Array.from(doc.querySelectorAll('.fwin:not(.closing)')).filter(el => !el.closest('[hidden]'));
        }
        function activeWorkspace() {
            return workspaces().sort((a, b) => (parseFloat(win.getComputedStyle(b).zIndex) || 0) - (parseFloat(win.getComputedStyle(a).zIndex) || 0))[0];
        }
        function restoreCaption() {
            captionNodes.forEach((anchor, node) => {
                if (anchor.parentNode) { anchor.parentNode.insertBefore(node, anchor); anchor.remove(); }
            });
            captionNodes.clear();
            if (caption) { chromeObserver?.unobserve(caption); caption.remove(); caption = null; }
        }
        function reserveCaption(layout) {
            if (!layout.stage) { restoreCaption(); return layout; }
            if (!caption) {
                caption = doc.createElement('div');
                caption.className = 'friday-avatar-caption';
                caption.setAttribute('aria-label', 'Friday’s state');
                doc.body.appendChild(caption);
                chromeObserver?.observe(caption);
            }
            // Keep the native nodes and their live updates. Their previous HUD
            // stacking context cannot make text compete with workspace cards.
            for (const id of ['state-indicator', 'presence-status']) {
                const node = doc.getElementById(id);
                if (node && !captionNodes.has(node)) {
                    const anchor = doc.createComment('native avatar status');
                    node.parentNode.insertBefore(anchor, node);
                    if (id === 'state-indicator') node.setAttribute('role', 'status');
                    captionNodes.set(node, anchor); caption.appendChild(node);
                }
            }
            const frame = { ...layout.stage };
            caption.style.width = Math.min(frame.w, 420) + 'px';
            const height = Math.min(Math.max(34, Math.ceil(caption.scrollHeight) + 1), Math.max(1, frame.h - 40));
            const gap = Math.min(8, Math.max(0, frame.h - height - 1));
            const status = { x: frame.x + (frame.w - Math.min(frame.w,420))/2, y: frame.y + frame.h - height, w: Math.min(frame.w,420), h: height };
            return { ...layout, stageFrame: frame, caption: status, stage: { ...frame, h: Math.max(1, frame.h-height-gap) } };
        }
        function workspaceArea(area) {
            if (!area || destroyed) return area;
            if (applyingLayout) return occupancy.content || area;
            const rightChat = parseFloat(win.getComputedStyle(root).getPropertyValue('--fr-chat-dock')) || 0;
            const leftChat = parseFloat(win.getComputedStyle(root).getPropertyValue('--fr-chat-dock-left')) || 0;
            const enabled = simpleStyle() && doc.body.classList.contains('friday-experience-enabled') && !win.__FRIDAY_STANDALONE__ && win.__FRIDAY_CHROME__ !== 'chat';
            const compact = enabled && (win.innerWidth < 1100 || area.w < 680);
            const gutter = win.innerWidth < 760 ? 8 : 16;
            // A compact chat shares the work region beneath the stage. Its
            // former horizontal reservation must not squeeze that stage out.
            const base = compact ? { ...area, x:gutter, w:Math.max(1,win.innerWidth-gutter*2) } : { ...area };
            if (enabled) {
                const bar = doc.querySelector('.top-bar'), dock = doc.querySelector('.dock');
                const barBox = bar?.getBoundingClientRect(), dockBox = dock?.getBoundingClientRect();
                const top = barBox?.height > 0 ? Math.ceil(barBox.bottom) : area.y-12;
                const safeBottom = parseFloat(win.getComputedStyle(root).getPropertyValue('--friday-safe-bottom')) || 0;
                const floor = dockBox?.height > 0 && dockBox.bottom > 0 ? Math.min(win.innerHeight-safeBottom,Math.round(dockBox.top)) : win.innerHeight-safeBottom;
                base.y = top+12; base.h = Math.max(1,floor-base.y-12);
                const value = top+'px';
                if(root.style.getPropertyValue('--fr-topbar-h')!==value)root.style.setProperty('--fr-topbar-h',value);
                ownedTopbar = true;
                for (const el of [bar,dock]) if(el && chromeObserver && !observedChrome.has(el)){observedChrome.add(el);chromeObserver.observe(el);}
            } else if(ownedTopbar) {
                if(!doc.querySelector('.friday-responsive-topbar')){
                    if(previousTopbar)root.style.setProperty('--fr-topbar-h',previousTopbar);else root.style.removeProperty('--fr-topbar-h');
                }
                ownedTopbar=false;
            }
            const next = reserveCaption(spatialLayout(base, { enabled, compact, shortLandscape: win.innerHeight <= 500 && win.innerWidth > win.innerHeight, viewportWidth: win.innerWidth, arrangement: state.arrangement, rightChat: rightChat > 0 }));
            if (enabled && !compact && (rightChat || leftChat)) next.chat = {
                x: leftChat ? 0 : win.innerWidth-rightChat, y:base.y,
                w: leftChat || rightChat, h:base.h
            };
            // Chrome and newly opened chat already occupy their new pixels.
            // Reflow before paint rather than animate through those regions.
            const viewport = [win.innerWidth,win.innerHeight,enabled,base.x,base.y,base.w,base.h,leftChat,rightChat].join('|');
            const immediate = viewport !== viewportKey || !enabled || doc.hidden;
            viewportKey = viewport;
            const changed = layoutMotion.request(next, win.performance.now(), {
                immediate, enabled: state.motion === 'on' && !reduced.matches && !doc.hidden
            });
            if (changed) {
                occupancy = layoutMotion.current;
                applyLayout(!layoutMotion.active);
                scheduleLayout();
            }
            return occupancy.content;
        }
        function scheduleLayout() {
            if (destroyed || layoutFrame || !layoutMotion.active) return;
            layoutFrame = win.requestAnimationFrame(advanceLayout);
        }
        function advanceLayout(now) {
            layoutFrame = 0;
            if (destroyed) return;
            const next = doc.hidden ? layoutMotion.settle() : layoutMotion.frame(now, interactionHeld(now));
            if (next !== occupancy || !layoutMotion.active) {
                occupancy = next; applyLayout(!layoutMotion.active);
            }
            scheduleLayout();
        }
        function applyLayout(announce = false) {
            if (applyingLayout || !doc.body) return;
            applyingLayout = true;
            try {
                const styled = simpleStyle(), content = occupancy.content, stage = occupancy.stage;
                if (stage) doc.body.dataset.fridaySpatialLayout = occupancy.layout;
                else delete doc.body.dataset.fridaySpatialLayout;
                if (styled) doc.body.dataset.fridayLayoutMotion = layoutMotion.active ? 'moving' : state.motion;
                else delete doc.body.dataset.fridayLayoutMotion;
                const values = {
                    'content-left': stage && content ? content.x : 0,
                    'content-right': stage && content ? Math.max(0, win.innerWidth-content.x-content.w) : 0,
                    'content-top': stage && content ? content.y : 0,
                    'content-bottom': stage && content ? Math.max(0, win.innerHeight-content.y-content.h) : 0,
                    'avatar-left': stage?.x || 0, 'avatar-top': stage?.y || 0,
                    'avatar-width': stage?.w || 0, 'avatar-height': stage?.h || 0, 'avatar-gap':16
                };
                for (const [name,value] of Object.entries(values)) {
                    if (styled) root.style.setProperty('--friday-'+name,value+'px'); else root.style.removeProperty('--friday-'+name);
                }
                if (caption && occupancy.caption) {
                    const box = occupancy.caption;
                    caption.style.left=box.x+'px'; caption.style.top=box.y+'px';
                    caption.style.width=box.w+'px'; caption.style.maxHeight=box.h+'px';
                }
                surfaces.forEach((_,element) => updateSurface(element));
                const signature = JSON.stringify(occupancy);
                win.dispatchEvent(new win.CustomEvent('friday:spatial-frame',{detail:copyLayout(occupancy)}));
                if (announce && signature !== spatialSignature) {
                    spatialSignature = signature;
                    win.dispatchEvent(new win.CustomEvent('friday:spatial-layout',{detail:copyLayout(occupancy)}));
                }
            } finally { applyingLayout = false; }
        }
        function updateLayout() {
            if (destroyed || !doc.body) return;
            const styled = simpleStyle();
            const windows = workspaces();
            const present = windows.length > 0 && !win.__FRIDAY_STANDALONE__;
            if (styled) {
                doc.body.dataset.fridayHoloArrangement = state.arrangement;
                doc.body.dataset.fridayHoloWorking = String(present);
            } else {
                delete doc.body.dataset.fridayHoloArrangement; delete doc.body.dataset.fridayHoloWorking;
                // Display style changes preserve the native avatar and any live
                // camera owner. Only workspace decoration and fitting pause.
                depth?.destroy(); depth = null;
                tagged.forEach((value, el) => { if (el.getAttribute('data-depth') === 'middle') el.removeAttribute('data-depth'); }); tagged.clear();
            }
            if (typeof win.fridayDesktopArea === 'function') win.fridayDesktopArea();
            applyLayout(!layoutMotion.active);
            for (const el of styled ? doc.querySelectorAll('.fwin-body,.chat-panel,.chat-win') : []) {
                if (!tagged.has(el) && !el.hasAttribute('data-depth')) { tagged.set(el, null); el.setAttribute('data-depth', 'middle'); }
            }
        }
        function onChromeTransition(event) {
            if (!event.target?.matches?.('.dock,.top-bar')) return;
            chromeUntil=win.performance.now()+500;
            if(chromeFrame)return;
            let last=0;
            const tick=()=>{chromeFrame=0;if(destroyed||doc.hidden)return;const now=win.performance.now();if(now-last>=32){last=now;updateLayout();}if(now<chromeUntil)chromeFrame=win.requestAnimationFrame(tick);};
            chromeFrame=win.requestAnimationFrame(tick);
        }
        const frameFit = { pieces:[], scale:1, x:0, y:0, ready:false, delta:1/60, neutral:null, lean:null, structures:null };
        function fitSceneCamera(sceneState) {
            const stage = destroyed ? null : occupancy.stage;
            const { camera, scene, basePosition, targetLook, structures, keys, delta } = sceneState;
            setStageScenery(structures, !!stage);
            frameFit.structures = structures;
            camera.aspect = stage ? stage.w / stage.h : win.innerWidth / win.innerHeight;
            if (scene?.fog && !originalFog.has(scene.fog)) originalFog.set(scene.fog, scene.fog.density);
            if (!stage || !win.THREE) { fitBounds = null; projectedBounds = null; fittedDistance = 0; frameFit.pieces=[];frameFit.ready=false; camera.far = Math.max(camera.far,1000); if(scene?.fog && originalFog.has(scene.fog))scene.fog.density=originalFog.get(scene.fog); return; }
            const THREE = win.THREE, now = win.performance.now(), key = keys.join('|');
            frameFit.delta = Number.isFinite(delta) ? Math.max(0,delta) : 1/60;
            if (!fitBounds || key !== fitKey || now - fitAt > 100) {
                let union = null;
                frameFit.pieces=[];
                for (const name of keys) {
                    const group = structures[name]; if (!group) continue;
                    group.updateMatrixWorld(true);
                    const local = new THREE.Box3();
                    const breathing=1.08*Math.max(1,sceneState.rootBreath||1,Math.abs(group.scale.x),Math.abs(group.scale.y),Math.abs(group.scale.z));
                    // Compose from the native pose rather than decomposing a
                    // possibly zero-scale morph matrix.
                    const fullRoot=new THREE.Matrix4().compose(group.position,group.quaternion,new THREE.Vector3().setScalar(breathing));
                    if(group.parent)fullRoot.premultiply(group.parent.matrixWorld);
                    const addPiece=(geometry,relative,shader,radius)=>{
                        const box=geometry.boundingBox.clone();
                        const declared=Number.isFinite(radius)&&radius>0?new THREE.Sphere(new THREE.Vector3(),radius):geometry.boundingSphere;
                        if(Number.isFinite(radius)&&radius>0)box.setFromCenterAndSize(declared.center,new THREE.Vector3().setScalar(radius*2));
                        const shaderExtent=declared && box.min.equals(box.max);
                        if(shaderExtent)box.setFromCenterAndSize(declared.center,new THREE.Vector3().setScalar(declared.radius*2));
                        const matrix=new THREE.Matrix4().multiplyMatrices(fullRoot,relative);
                        const sphere=(!shader||shaderExtent||geometry.type==='SphereGeometry'||geometry.type==='IcosahedronGeometry')?declared?.clone().applyMatrix4(matrix):null;
                        frameFit.pieces.push({box,matrix,sphere});
                    };
                    group.traverseVisible(node => {
                        if (!node.geometry || node.userData.fridayStageAmbient) return;
                        // Buffer positions and instance transforms change as
                        // the native forms breathe, build and move.
                        node.geometry.computeBoundingBox();
                        if (!node.geometry.boundingBox || node.geometry.boundingBox.isEmpty()) return;
                        const framedBox = node.userData.fridayStageField
                            ? new THREE.Box3(new THREE.Vector3(-18,-8,-18), new THREE.Vector3(18,8,18))
                            : node.geometry.boundingBox;
                        const materials=Array.isArray(node.material)?node.material:[node.material];
                        const shader=materials.some(material=>material?.isShaderMaterial);
                        // Shader-owned bounds may describe vertices that do
                        // not live in the CPU position buffer.
                        if(!shader)node.geometry.computeBoundingSphere();
                        // Do not invert the morph root: its scale can be zero
                        // on the first frame of a new form.
                        const relative = node === group ? new THREE.Matrix4() : node.matrix.clone();
                        for (let parent=node.parent; node!==group && parent && parent!==group; parent=parent.parent) relative.premultiply(parent.matrix);
                        if (node.isInstancedMesh && node.getMatrixAt) {
                            const instance = new THREE.Matrix4(), combined = new THREE.Matrix4();
                            for (let index=0;index<node.count;index++) {
                                node.getMatrixAt(index,instance);
                                combined.multiplyMatrices(relative,instance);
                                local.union(node.geometry.boundingBox.clone().applyMatrix4(combined));
                                addPiece(node.geometry,combined,shader,node.userData.fridayStageRadius);
                            }
                        } else {
                            local.union(framedBox.clone().applyMatrix4(relative));
                            const framedGeometry = node.userData.fridayStageField
                                ? { boundingBox:framedBox, boundingSphere:framedBox.getBoundingSphere(new THREE.Sphere()) }
                                : node.geometry;
                            addPiece(framedGeometry,relative,shader,node.userData.fridayStageRadius);
                            // Some shader-driven points declare their extent
                            // explicitly while their CPU positions stay zero.
                            const sphere = node.geometry.boundingSphere;
                            if (sphere && node.geometry.boundingBox.min.equals(node.geometry.boundingBox.max)) {
                                local.union(new THREE.Box3().setFromCenterAndSize(sphere.center,new THREE.Vector3().setScalar(sphere.radius*2)).applyMatrix4(relative));
                            }
                        }
                    });
                    if (local.isEmpty()) continue;
                    const sphere = local.getBoundingSphere(new THREE.Sphere());
                    sphere.center.applyMatrix4(group.matrixWorld);
                    // The native morph scale is already applied above this
                    // hook. Breathing runs later, so the caller supplies its
                    // current ceiling; a small margin covers the bounds cache.
                    sphere.radius *= 1.08 * Math.max(1, sceneState.rootBreath || 1, Math.abs(group.scale.x), Math.abs(group.scale.y), Math.abs(group.scale.z));
                    if (!Number.isFinite(sphere.radius) || ![sphere.center.x,sphere.center.y,sphere.center.z].every(Number.isFinite)) continue;
                    if (!union) union = sphere;
                    else {
                        const distance = union.center.distanceTo(sphere.center);
                        if (sphere.radius >= distance + union.radius) union.copy(sphere);
                        else if (union.radius < distance + sphere.radius) {
                            const radius = (distance + union.radius + sphere.radius) / 2;
                            union.center.add(sphere.center.clone().sub(union.center).multiplyScalar((radius-union.radius)/distance));
                            union.radius = radius;
                        }
                    }
                }
                fitBounds = union;
                fitAt = now; fitKey = key;
            }
            if (!fitBounds) return;
            const radius = fitBounds.radius + fitBounds.center.distanceTo(targetLook);
            const vertical = camera.fov * Math.PI / 360;
            const horizontal = Math.atan(Math.tan(vertical) * camera.aspect);
            // Frame a fixed safe envelope for the available lean, rather than
            // moving the neutral camera to compensate for each head movement.
            // pushFace bounds face-width ratios at 2.5 before calculating z.
            const tracking = !!win.fridayVibe?.isHologramOn?.();
            const zoom = tracking ? win.FridayTracking?.headZoom?.(Math.log2(2.5)) || 1
                : Number.isFinite(sceneState.headZoom) ? sceneState.headZoom : 1;
            const glass = Number.isFinite(sceneState.glassAt) ? sceneState.glassAt : 1;
            const eyeFraction = Math.max(.1, 1-glass*(1-1/zoom));
            const required = Math.max(radius / Math.sin(Math.min(vertical, horizontal)) * 1.12, radius * 1.15 / eyeFraction);
            fittedDistance = required >= fittedDistance ? required : fittedDistance + (required - fittedDistance) * Math.min(1, delta * 2);
            // Small forms need to come closer too: retaining a distant
            // full-desktop camera would shrink them inside a narrow stage.
            fitRadialDistance(basePosition, targetLook, fittedDistance);
            camera.far = Math.max(1000, fittedDistance + radius * 4);
            // Framing changes camera distance, not the avatar material. Keep
            // the original atmospheric amount at that new viewing distance.
            if (scene?.fog && typeof originalFog.get(scene.fog) === 'number') scene.fog.density = originalFog.get(scene.fog) * Math.min(1, 22 / Math.max(22, fittedDistance));
            // Use the visible pieces in a neutral view for apparent size.
            // The large sphere remains a camera-distance safety envelope; it
            // must not decide how small a narrow or sparse form appears.
            const neutral=frameFit.neutral||(frameFit.neutral=camera.clone());
            neutral.fov=camera.fov;neutral.aspect=camera.aspect;neutral.near=camera.near;neutral.far=camera.far;
            neutral.position.copy(basePosition);neutral.lookAt(targetLook);neutral.updateMatrixWorld(true);neutral.updateProjectionMatrix();
            const raw=projectedPieces(neutral,frameFit.pieces,stage.w,stage.h);
            const pad=Math.max(8,Math.min(stage.w,stage.h)*.045);
            let desired=stageFill(raw,stage.w,stage.h,pad);
            if (tracking) {
                // Reserve the pieces' actual extent at up to ten percent
                // focal growth: nearer geometry grows faster than the focus.
                // Keep that headroom inside the FINAL usable rectangle.
                // Depth-off needs none; reduced motion needs only its gentle
                // lean. The untracked avatar retains its original full fit.
                const gain = 1 / Math.max(.1, glass + (1-glass)*zoom);
                const allowance = Math.min(1.10, Math.max(1, gain));
                const inset = Math.min(12,stage.w/8,stage.h/8);
                const active = stageFill(raw,stage.w,stage.h,inset,allowance);
                if (raw && glass > 1 && allowance > 1) {
                    const lean = frameFit.lean || (frameFit.lean = neutral.clone());
                    lean.copy(neutral);
                    const probeZoom = Math.min(zoom, (glass-1/allowance)/(glass-1));
                    const screenDist = Math.max(4, glass*basePosition.distanceTo(targetLook));
                    lean.position.addScaledVector(targetLook.clone().sub(basePosition).normalize(), screenDist*(1-1/probeZoom));
                    lean.updateMatrixWorld(true);
                    // A centered eye moves toward the fixed glass; its wider
                    // frustum divides both projection scales by the same zoom.
                    lean.projectionMatrix.elements[0] /= probeZoom;
                    lean.projectionMatrix.elements[5] /= probeZoom;
                    const grown = projectedPieces(lean,frameFit.pieces,stage.w,stage.h);
                    const limit = grown && stageFill(grown,stage.w,stage.h,inset);
                    if (limit && limit.scale < active.scale) {
                        const shrink = limit.scale/active.scale;
                        active.scale = limit.scale; active.x *= shrink; active.y *= shrink;
                    }
                }
                if (active.scale < desired.scale) desired = active;
            }
            const follow=frameFit.ready?1-Math.exp(-frameFit.delta*3):1;
            frameFit.scale+=(desired.scale-frameFit.scale)*follow;
            frameFit.x+=(desired.x-frameFit.x)*follow;frameFit.y+=(desired.y-frameFit.y)*follow;
            frameFit.ready=true;
        }
        function projectSceneStage(camera) {
            const stage = destroyed ? null : occupancy.stage;
            let pointScale = stage && frameFit.ready ? frameFit.scale * stage.h / win.innerHeight : 1;
            // Neutral framing can enlarge as well as shrink. Keep it stable
            // while the head moves, then enforce only the stage's clear edge;
            // this preserves the native lean-in and lateral parallax.
            if(stage&&frameFit.ready){
                const e=camera.projectionMatrix.elements;
                for(let column=0;column<4;column++){
                    const i=column*4;
                    e[i]=e[i]*frameFit.scale+(2*frameFit.x/stage.w)*e[i+3];
                    e[i+1]=e[i+1]*frameFit.scale-(2*frameFit.y/stage.h)*e[i+3];
                }
            }
            const raw = stage && fitBounds ? projectedPieces(camera,frameFit.pieces,stage.w,stage.h) || projectedSphere(camera, fitBounds, stage.w, stage.h) : null;
            if (raw) {
                const pad = Math.min(12, stage.w/8, stage.h/8);
                const scale = Math.min(1, (stage.w-pad*2)/raw.w, (stage.h-pad*2)/raw.h);
                pointScale *= scale;
                const x = stage.w/2+(raw.x-stage.w/2)*scale, y = stage.h/2+(raw.y-stage.h/2)*scale;
                const dx = Math.max(pad-x, Math.min(0,stage.w-pad-x-raw.w*scale));
                const dy = Math.max(pad-y, Math.min(0,stage.h-pad-y-raw.h*scale));
                const e = camera.projectionMatrix.elements;
                for (let column=0;column<4;column++) {
                    const i=column*4;
                    e[i]=e[i]*scale+(2*dx/stage.w)*e[i+3];
                    e[i+1]=e[i+1]*scale-(2*dy/stage.h)*e[i+3];
                }
            }
            stageProjection(camera, stage, win.innerWidth, win.innerHeight);
            // Point sprites do not inherit projection-matrix magnification.
            // Match their authored light to the projected terrain, then restore
            // the native sizes when Classic owns the full desktop again.
            scaleStagePoints(frameFit.structures, pointScale, stage ? win.__fridayRenderer?.getPixelRatio?.() || 1 : 0);
            if (!stage || !fitBounds || !win.THREE) { projectedBounds = null; return; }
            const bounds = projectedPieces(camera,frameFit.pieces,win.innerWidth,win.innerHeight) || projectedSphere(camera, fitBounds, win.innerWidth, win.innerHeight);
            projectedBounds = bounds ? { ...bounds, inside:bounds.x>=stage.x&&bounds.y>=stage.y&&bounds.x+bounds.w<=stage.x+stage.w&&bounds.y+bounds.h<=stage.y+stage.h, form:fitKey } : null;
        }
        function updateAmbientStage(material) {
            ambientStageUniforms(material,destroyed?null:occupancy.stage,win.innerHeight,win.__fridayRenderer?.getPixelRatio?.()||1);
        }
        function sourceDescription(current) {
            if (state.error) return state.error;
            if (!current.ready) return win.__FRIDAY_STANDALONE__ || win.__FRIDAY_CHROME__ === 'chat' ? 'Scene and head tracking live on the holographic desktop. This tab keeps your workspace open.' : 'The original scene is loading.';
            if (current.callHeld) return 'Friday is standing back for a call.';
            if (state.source === 'preview') return 'Pointer preview uses no camera. Move across the desktop to inspect depth.' + (current.handTracking ? ' Hand tracking still has camera access.' : '');
            if (state.source === 'camera') {
                if (current.hologram?.busy || session?.busy || current.camera?.wanted && current.camera.status === 'off') return 'Starting head tracking · waiting for camera permission.';
                if (current.camera?.status === 'live') return current.hologram?.faceVisible ? 'Face tracked · the workspace follows your viewpoint.' : 'Camera on · looking for a face.';
                if (current.camera?.detail) return current.camera.detail;
                return 'Head tracking is off.';
            }
            if (current.hologram?.enabled) return 'Workspace motion is off. Existing head tracking remains on.';
            if (current.camera?.wanted || current.camera?.status === 'live') return 'Workspace motion is off. Other tracking controls still have camera access.';
            return 'Camera off · Friday’s original scene remains live.';
        }
        function refreshPanel(current = sample()) {
            if (!dialog || !dialog.open) return;
            const status = dialog.querySelector('[data-holo-status]'); if (status) status.textContent = sourceDescription(current);
            const title = dialog.querySelector('[data-holo-scene-name]'); if (title) title.textContent = current.scene?.name || 'Holographic scene';
            const transition = dialog.querySelector('[data-holo-transition]');
            if (transition) transition.textContent = current.transitionProgress < .999 ? 'Transforming…' : 'Original Friday scene';
            dialog.querySelectorAll('[data-holo-mode]').forEach(el => el.setAttribute('aria-pressed', String(el.dataset.holoMode === state.mode)));
            dialog.querySelectorAll('[data-holo-source]').forEach(el => el.setAttribute('aria-pressed', String(el.dataset.holoSource === state.source)));
            dialog.querySelectorAll('[data-holo-arrangement]').forEach(el => el.setAttribute('aria-pressed', String(el.dataset.holoArrangement === state.arrangement)));
            dialog.querySelectorAll('[data-holo-motion]').forEach(el => el.setAttribute('aria-pressed', String(el.dataset.holoMotion === state.motion)));
            const preferences = dialog.querySelector('[data-holo-preference-status]');
            if (preferences) preferences.textContent = state.preferenceError || (reduced.matches ? 'Your system’s reduced motion preference is active.' : 'Layout preferences stay in this browser.');
            dialog.querySelectorAll('[data-holo-form]').forEach(el => el.setAttribute('aria-pressed', String(Number(el.dataset.holoForm) === current.scene?.index)));
            const reduce = dialog.querySelector('[data-holo-reduced]'); if (reduce) reduce.hidden = !reduced.matches;
            refreshTracking();
        }
        function refreshTracking() {
            if (!dialog?.open) return;
            const cfg = tuner.read();
            dialog.querySelectorAll('[data-holo-tracking]').forEach(input => {
                const key = input.dataset.holoTracking;
                if (doc.activeElement !== input) input.value = Number.isFinite(Number(cfg[key])) ? cfg[key] : win.FridayTracking?.DEFAULTS?.[key] || 0;
                const value = dialog.querySelector('[data-holo-tracking-value="' + key + '"]');
                if (value) value.textContent = Number(input.value).toFixed(2);
                input.disabled = !win.FridayTracking;
            });
            const status = dialog.querySelector('[data-holo-tracking-status]');
            if (status) status.textContent = trackingStatus || 'Applies to the avatar in both display styles. Your existing preferences are kept.';
            const distance = dialog.querySelector('[data-holo-calibration-status]');
            if (distance) distance.textContent = calibration.state.status;
        }
        function calibrateDistance() {
            const result = calibration.calibrateCurrent();
            trackingStatus = result.ok && result.saved ? '' : result.status;
            refreshTracking(); return result;
        }
        async function setSource(source, userInitiated) {
            if (!['off', 'preview', 'camera'].includes(source) || destroyed) return;
            if (source !== 'off' && !userInitiated) return;
            const serial = ++selectSerial;
            state.error = ''; clearPreview();
            const tracking = cameraSession();
            if (source !== 'off' && !sample().ready) { state.error = win.__FRIDAY_STANDALONE__ || win.__FRIDAY_CHROME__ === 'chat' ? 'Open the holographic desktop to use the scene and tracking controls.' : 'The original scene is still loading. Try again in a moment.'; refreshPanel(); return; }
            if (source === 'camera' && win.FridayTracking?.head.debug) { state.error = 'Finish the existing tracking preview before enabling the camera.'; refreshPanel(); return; }
            if (source === 'camera' && (doc.hidden || sample().callHeld)) { state.error = 'Return to the desktop after the call to enable head tracking.'; refreshPanel(); return; }
            state.source = source; ensureDepth(); depth?.setSource(source);
            try {
                if (source === 'camera') {
                    if (!tracking) throw new Error('The scene is still loading.');
                    await tracking.acquire();
                } else {
                    await tracking?.release();
                    if (source === 'preview' && win.fridayVibe?.isHologramOn?.()) throw new Error('Head tracking is already on in the original controls. Turn it off there before trying pointer preview.');
                    if (source === 'preview' && win.FridayTracking?.head.debug) throw new Error('Another tracking preview is active. Finish it before starting this one.');
                }
            } catch (error) {
                if (serial !== selectSerial) return;
                state.error = error.message; state.source = 'off'; depth?.setSource('off');
            }
            if (serial === selectSerial) refreshPanel();
        }
        function setMode(mode) {
            if (!MODES.includes(mode)) return;
            state.mode = mode; ensureDepth(); depth?.setMode(mode); persist(); refreshPanel();
        }
        function setArrangement(arrangement) {
            if (!ARRANGEMENTS.includes(arrangement)) return;
            state.arrangement = arrangement; persist(); updateLayout();
            const active = activeWorkspace(); const id = active?.querySelector('[data-fwin-max]')?.dataset.fwinMax;
            if (id) win.dispatchEvent(new CustomEvent('friday:fwin-max', { detail: { workspace: id, max: true } }));
            refreshPanel();
        }
        function setMotion(value) {
            if (value !== 'on' && value !== 'off') return;
            state.motion = value; persist();
            if (value === 'off') { occupancy = layoutMotion.settle(); applyLayout(true); }
            updateLayout(); refreshPanel();
        }
        function resetLayout() {
            state.arrangement = 'companion'; state.motion = 'on';
            persist(); updateLayout(); refreshPanel();
        }
        function close() {
            if (!dialog) return;
            state.open = false; dialog.close();
            delete doc.body.dataset.fridayHoloStudio;
            if (opener?.isConnected) opener.focus();
        }
        function open() {
            if (destroyed) return;
            ensureDepth(); cameraSession(); opener = doc.activeElement;
            if (dialog?.open) { dialog.querySelector('button')?.focus(); return; }
            if (!dialog) {
                dialog = doc.createElement('dialog'); dialog.className = 'fr-holo-dialog'; dialog.setAttribute('aria-labelledby', 'fr-holo-title');
                dialog.addEventListener('click', event => {
                    const button = event.target.closest('button'); if (!button || !dialog.contains(button)) return;
                    if (button.hasAttribute('data-holo-close')) close();
                    if (button.hasAttribute('data-holo-details')) { close(); win.dispatchEvent(new CustomEvent('friday:scene-details')); }
                    if (button.hasAttribute('data-holo-calibrate')) void calibrateDistance();
                    if (button.dataset.holoMode) setMode(button.dataset.holoMode);
                    if (button.dataset.holoSource) void setSource(button.dataset.holoSource, event.isTrusted);
                    if (button.dataset.holoArrangement) setArrangement(button.dataset.holoArrangement);
                    if (button.dataset.holoMotion) setMotion(button.dataset.holoMotion);
                    if (button.hasAttribute('data-holo-reset-layout')) resetLayout();
                    if (button.dataset.holoForm !== undefined) {
                        const index = Number(button.dataset.holoForm), scenes = win.fridayVibe?.getStructures?.() || [];
                        if (Number.isInteger(index) && index >= 0 && index < scenes.length && sample().ready) { win.fridayVibe.setStructure(index); refreshPanel(); }
                    }
                });
                dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
                dialog.addEventListener('input', event => {
                    const key = event.target.dataset?.holoTracking;
                    if (!TRACKING_DIALS.some(dial => dial.key === key)) return;
                    tuner.live({[key]:Number(event.target.value)});
                    trackingStatus = 'Previewing · release the control to save.';
                    refreshTracking();
                });
                dialog.addEventListener('change', event => {
                    const key = event.target.dataset?.holoTracking;
                    if (!TRACKING_DIALS.some(dial => dial.key === key)) return;
                    void tuner.save({[key]:Number(event.target.value)}).catch(() => {});
                });
                dialog.addEventListener('keydown', event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); } });
                doc.body.appendChild(dialog);
            }
            const scenes = win.fridayVibe?.getStructures?.() || [];
            dialog.innerHTML = `<header class="fr-holo-header"><div><span class="fr-holo-eyebrow">AGENT FRIDAY™</span><h2 id="fr-holo-title">Scene & depth</h2></div><button data-holo-close aria-label="Close Scene and depth">×</button></header>
                <div class="fr-holo-content"><section class="fr-holo-collection" aria-label="Avatar collection"><div class="fr-holo-scene-heading"><small data-holo-transition>Original Friday scene</small><h3 data-holo-scene-name></h3><p>Your holographic desktop stays alive as you work.</p></div><div class="fr-holo-forms">${scenes.map((scene, index) => `<button data-holo-form="${index}" aria-pressed="false"><span>${String(index + 1).padStart(2, '0')}</span><strong>${esc(scene.name)}</strong></button>`).join('') || '<p>Scene collection is loading. Reopen this panel in a moment.</p>'}</div></section>
                <aside class="fr-holo-controls"><section><h3>Workspace depth</h3><div class="fr-holo-segment">${MODES.map(mode => `<button data-holo-mode="${mode}" aria-pressed="false">${upper(mode)}</button>`).join('')}</div><p>Depth changes the light and layers around your work. Text and controls stay steady.</p></section>
                <section><h3>Motion source</h3><div class="fr-holo-sources"><button data-holo-source="off">Off</button><button data-holo-source="preview">Pointer preview</button><button data-holo-source="camera">Enable head tracking</button></div><p>Pointer preview uses no camera. Head tracking requests camera access through Friday’s original tracker.</p><div class="fr-holo-status" role="status" data-holo-status></div><p data-holo-reduced hidden>Reduced motion is on. Moving workspace decorations remain still.</p></section>
                <section><h3>Head tracking comfort</h3><p>These controls change the avatar’s response. Workspace depth above changes only the surrounding layers.</p><div class="fr-holo-tracking">${TRACKING_DIALS.map(dial => `<label><span>${dial.label}<output data-holo-tracking-value="${dial.key}"></output></span><input type="range" data-holo-tracking="${dial.key}" aria-label="${dial.label}" min="${dial.min}" max="${dial.max}" step="${dial.step}"><small>${dial.help}</small></label>`).join('')}</div><p>Sit at your usual distance, then set the starting point for leaning in and out. Distance calibration stays with this camera in this browser.</p><button data-holo-calibrate>Calibrate distance</button><p data-holo-calibration-status></p><p role="status" data-holo-tracking-status></p></section>
                <section><h3>Friday’s place in your work</h3><div class="fr-holo-arrangements">${[['companion','Companion','A place beside the work'],['present','Present','A generous holographic stage'],['focus','Focus','More room for the workspace']].map(([value, title, description]) => `<button data-holo-arrangement="${value}" aria-pressed="false"><strong>${title}</strong><small>${description}</small></button>`).join('')}</div><p>Applies to desktop workspaces. Your floating window sizes are kept for Restore.</p></section>
                <section><h3>Arrangement motion</h3><div class="fr-holo-segment"><button data-holo-motion="on" aria-pressed="false">Gentle</button><button data-holo-motion="off" aria-pressed="false">Off</button></div><p>Friday and the workspace move together. Motion pauses while you type, drag or target a control by hand. Camera tracking has its own controls above.</p><button data-holo-reset-layout>Reset layout</button><p role="status" data-holo-preference-status></p></section>${!win.__FRIDAY_STANDALONE__ && win.__FRIDAY_CHROME__ !== 'chat' ? '<section class="fr-holo-advanced"><button data-holo-details>More scene settings ↗</button><p>Evolution, hand tracking, timelapse and the original scene controls.</p></section>' : ''}</aside></div>`;
            state.open = true; doc.body.dataset.fridayHoloStudio = 'true';
            dialog.showModal(); refreshPanel(); dialog.querySelector('[data-holo-close]')?.focus();
        }
        function onPointer(event) {
            if (state.source !== 'preview' || doc.hidden || reduced.matches || win.performance.now() - pointerAt < 40) return;
            const current = sample();
            if (current.hologram?.enabled || current.callHeld) { void setSource('off', false); return; }
            const tk = win.FridayTracking; if (!tk?.debugHead) return;
            pointerAt = win.performance.now();
            tk.debugHead((event.clientX / win.innerWidth - .5) * .8, (event.clientY / win.innerHeight - .5) * .55, tk.head.baseline || .18);
            ownedPreview = tk.head.debug;
        }
        function leavePointer() { clearPreview(); }
        function onVisibility() {
            if (doc.hidden) {
                clearPreview(); void setSource('off', false); mouseHeld = composing = false; heldPointers.clear();
                occupancy = layoutMotion.settle(); applyLayout(true);
            }
        }
        function poll() {
            if (destroyed || doc.hidden) return;
            calibration.sync();
            ensureDepth();
            const nextCaption = ['state-indicator','presence-status'].map(id => {
                const node = doc.getElementById(id); return node ? node.textContent + ':' + node.style.display : '';
            }).join('|');
            if (nextCaption !== captionSignature) { captionSignature = nextCaption; updateLayout(); }
            const current = sample();
            if (state.source === 'camera' && session?.superseded) { state.source = 'off'; depth?.setSource('off'); void session.release(); }
            if (state.source === 'camera' && !current.hologram?.enabled && !session?.busy) { state.source = 'off'; depth?.setSource('off'); }
            if (state.source === 'preview' && current.hologram?.enabled) { clearPreview(); state.source = 'off'; depth?.setSource('off'); }
            const head = current.head || {};
            if (state.source !== 'off') depth?.setPose({ ...head, seen: state.source === 'preview' ? !!ownedPreview : !!current.hologram?.faceVisible && current.camera?.status === 'live' && !!head.seen });
            const next = JSON.stringify([current.scene, current.transitionProgress < .999, current.hologram, current.camera, !!head.seen, state.source, state.error]);
            if (next !== signature) { signature = next; refreshPanel(current); }
        }
        function destroy() {
            if (destroyed) return;
            destroyed = true; selectSerial++; clearPreview(); void session?.release();
            restoreCaption();
            win.clearInterval(timer); observer?.disconnect(); chromeObserver?.disconnect(); win.cancelAnimationFrame(chromeFrame); win.cancelAnimationFrame(layoutFrame); depth?.destroy(); dialog?.remove();
            surfaces.forEach((entry,element) => clearSurface(element,entry)); surfaces.clear(); interactionHolds.clear();
            for (const name of ['pointerdown','pointerup','pointercancel','lostpointercapture','mousedown','mouseup','keydown','input','compositionstart','compositionend']) doc.removeEventListener(name,onDirectInput,true);
            win.removeEventListener('blur',onDirectInput);
            doc.removeEventListener('pointermove', onPointer); doc.removeEventListener('pointerleave', leavePointer); doc.removeEventListener('visibilitychange', onVisibility);
            doc.removeEventListener('transitionrun',onChromeTransition,true); doc.removeEventListener('transitionend',onChromeTransition,true);
            win.removeEventListener('resize', updateLayout); win.removeEventListener('friday:surface-changed', updateLayout); win.removeEventListener('friday:chat-dock', updateLayout); win.removeEventListener('friday:display-style', onDisplayStyle); win.removeEventListener('pagehide', destroy); reduced.removeEventListener?.('change', refreshReduced);
            win.removeEventListener('friday:tracking-settings', refreshTracking);
            tagged.forEach((value, el) => { if (el.getAttribute('data-depth') === 'middle') el.removeAttribute('data-depth'); }); tagged.clear();
            delete doc.body.dataset.fridayHoloArrangement; delete doc.body.dataset.fridayHoloWorking; delete doc.body.dataset.fridayHoloStudio; delete doc.body.dataset.fridaySpatialLayout; delete doc.body.dataset.fridayLayoutMotion;
            for (const name of ['content-left','content-right','content-top','content-bottom','avatar-left','avatar-top','avatar-width','avatar-height','avatar-gap']) root.style.removeProperty('--friday-'+name);
            if(ownedTopbar){if(previousTopbar)root.style.setProperty('--fr-topbar-h',previousTopbar);else root.style.removeProperty('--fr-topbar-h');ownedTopbar=false;}
            occupancy={layout:'classic',content:null,stage:null};projectedBounds=null;
        }
        function refreshReduced() {
            if (reduced.matches) { clearPreview(); occupancy = layoutMotion.settle(); applyLayout(true); }
            refreshPanel();
        }
        function onDisplayStyle() {
            if (!simpleStyle() && state.open) close();
            updateLayout(); ensureDepth();
        }
        function start() {
            if (destroyed || timer !== null) return;
            if(win.ResizeObserver)chromeObserver=new win.ResizeObserver(updateLayout);
            ensureDepth(); cameraSession(); calibration.sync(); updateLayout();
            timer = win.setInterval(poll, 40);
            observer = new win.MutationObserver(updateLayout); observer.observe(doc.getElementById('ui-root') || doc.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['hidden'] });
            doc.addEventListener('pointermove', onPointer, { passive: true }); doc.addEventListener('pointerleave', leavePointer); doc.addEventListener('visibilitychange', onVisibility);
            doc.addEventListener('transitionrun',onChromeTransition,true); doc.addEventListener('transitionend',onChromeTransition,true);
            for (const name of ['pointerdown','pointerup','pointercancel','lostpointercapture','mousedown','mouseup','keydown','input','compositionstart','compositionend']) doc.addEventListener(name,onDirectInput,true);
            win.addEventListener('blur',onDirectInput);
            win.addEventListener('resize', updateLayout); win.addEventListener('friday:surface-changed', updateLayout); win.addEventListener('friday:chat-dock', updateLayout); win.addEventListener('friday:display-style', onDisplayStyle); win.addEventListener('pagehide', destroy); reduced.addEventListener?.('change', refreshReduced);
            win.addEventListener('friday:tracking-settings', refreshTracking);
        }
        if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', start, { once: true }); else start();
        // The outer factory publishes the API after mount returns. Native
        // settings filtering can then resolve a restored binding immediately,
        // even if the existing camera was live before this module loaded.
        win.queueMicrotask?.(() => { if (!destroyed) calibration.sync(); });
        return { open, close, setMode, setArrangement, setMotion, resetLayout, holdLayout, registerSurface, updateSurface, cameraCalibration:calibration,
            workspaceArea, fitSceneCamera, projectSceneStage, updateAmbientStage,
            constrainRect: rect => constrainSpatialRect(rect, occupancy.stage ? occupancy.content : null),
            getOverlayRect(rect, padding = 8) {
                const area = occupancy.stage && occupancy.content;
                if (!area) return {...rect};
                const pad = Math.max(0,Math.min(Number(padding)||0,area.w/4,area.h/4));
                return constrainSpatialRect(rect,{x:area.x+pad,y:area.y+pad,w:area.w-pad*2,h:area.h-pad*2});
            },
            destroy, get stageRect() { return occupancy.stage ? { ...occupancy.stage } : null; },
            get layoutRect() { return copyLayout(occupancy); },
            get state() { return { ...state, moving:layoutMotion.active, layoutHeld:interactionHeld(), performanceFallback:win.performance.now()<layoutMotion.fallbackUntil,
                ownsTracking: !!session?.owns, spatial: copyLayout(occupancy), projectedAvatarBounds: projectedBounds ? {...projectedBounds} : null }; } };
    }
    return { trackingSession, trackingTuner, cameraCalibration, TRACKING_DIALS, setStageScenery, prepareStageField, prepareStagePoints, scaleStagePoints,
        spatialLayout, separatingSides, canAnimateLayout, interpolateLayout, createLayoutMotion, LAYOUT_MS,
        stageProjection, projectedSphere, projectedPieces, stageFill, ambientStageUniforms, constrainSpatialRect, fitRadialDistance, mount };
});
