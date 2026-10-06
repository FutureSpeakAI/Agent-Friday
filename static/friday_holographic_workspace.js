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

    function spatialLayout(area, options) {
        if (!options.enabled) return { layout: 'classic', content: { ...area }, stage: null };
        const gap = 16, content = { ...area };
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
        const state = { mode: 'balanced', source: 'off', arrangement: 'companion', error: '', open: false };
        try {
            const preferences = JSON.parse(win.localStorage.getItem('friday_holographic_workspace_v1') || '{}');
            if (MODES.includes(preferences.mode)) state.mode = preferences.mode;
            if (ARRANGEMENTS.includes(preferences.arrangement)) state.arrangement = preferences.arrangement;
        } catch (_) { /* Display preferences are optional. Camera intent is never persisted. */ }
        let dialog = null, opener = null, depth = null, session = null, sessionVibe = null;
        let ownedPreview = null, timer = null, observer = null, destroyed = false, signature = '';
        let spatialSignature = '', pointerAt = 0, selectSerial = 0;
        let chromeObserver = null, chromeFrame = 0, chromeUntil = 0, ownedTopbar = false;
        const observedChrome = new Set(), previousTopbar = root.style.getPropertyValue('--fr-topbar-h');
        let occupancy = { layout: 'classic', content: null, stage: null };
        let fitBounds = null, fitKey = '', fitAt = 0, fittedDistance = 0, projectedBounds = null;
        const originalFog = new WeakMap();
        const tagged = new Map();
        const reduced = win.matchMedia('(prefers-reduced-motion: reduce)');

        function persist() {
            try { win.localStorage.setItem('friday_holographic_workspace_v1', JSON.stringify({ mode: state.mode, arrangement: state.arrangement })); } catch (_) {}
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
        function workspaceArea(area) {
            if (!area || destroyed) return area;
            const rightChat = parseFloat(win.getComputedStyle(root).getPropertyValue('--fr-chat-dock')) || 0;
            const enabled = doc.body.classList.contains('friday-experience-enabled') && !win.__FRIDAY_STANDALONE__ && win.__FRIDAY_CHROME__ !== 'chat';
            const compact = enabled && (win.innerWidth < 1100 || area.w < 680);
            const gutter = win.innerWidth < 760 ? 8 : 16;
            // A compact chat shares the work region beneath the stage. Its
            // former horizontal reservation must not squeeze that stage out.
            const base = compact ? { ...area, x:gutter, w:Math.max(1,win.innerWidth-gutter*2) } : { ...area };
            if (enabled) {
                const bar = doc.querySelector('.top-bar'), dock = doc.querySelector('.dock');
                const barBox = bar?.getBoundingClientRect(), dockBox = dock?.getBoundingClientRect();
                const top = barBox?.height > 0 ? Math.ceil(barBox.bottom) : area.y-12;
                const floor = dockBox?.height > 0 && dockBox.bottom > 0 ? Math.min(win.innerHeight,Math.round(dockBox.top)) : win.innerHeight;
                base.y = top+12; base.h = Math.max(1,floor-base.y-12);
                const value = top+'px';
                if(root.style.getPropertyValue('--fr-topbar-h')!==value)root.style.setProperty('--fr-topbar-h',value);
                ownedTopbar = true;
                for (const el of [bar,dock]) if(el && chromeObserver && !observedChrome.has(el)){observedChrome.add(el);chromeObserver.observe(el);}
            } else if(ownedTopbar) {
                if(previousTopbar)root.style.setProperty('--fr-topbar-h',previousTopbar);else root.style.removeProperty('--fr-topbar-h');
                ownedTopbar=false;
            }
            occupancy = spatialLayout(base, { enabled, compact, viewportWidth: win.innerWidth, arrangement: state.arrangement, rightChat: rightChat > 0 });
            return occupancy.content;
        }
        function updateLayout() {
            if (destroyed || !doc.body) return;
            const windows = workspaces();
            const present = windows.length > 0 && !win.__FRIDAY_STANDALONE__;
            doc.body.dataset.fridayHoloArrangement = state.arrangement;
            doc.body.dataset.fridayHoloWorking = String(present);
            if (typeof win.fridayDesktopArea === 'function') win.fridayDesktopArea();
            const content = occupancy.content, stage = occupancy.stage;
            if (stage) doc.body.dataset.fridaySpatialLayout = occupancy.layout;
            else delete doc.body.dataset.fridaySpatialLayout;
            const values = {
                '--friday-content-left': stage && content ? content.x : 0,
                '--friday-content-right': stage && content ? Math.max(0, win.innerWidth - content.x - content.w) : 0,
                '--friday-content-top': stage && content ? content.y : 0,
                '--friday-content-bottom': stage && content ? Math.max(0, win.innerHeight - content.y - content.h) : 0,
                '--friday-avatar-left': stage?.x || 0, '--friday-avatar-top': stage?.y || 0,
                '--friday-avatar-width': stage?.w || 0, '--friday-avatar-height': stage?.h || 0,
                '--friday-avatar-gap': 16,
            };
            Object.entries(values).forEach(([name,value]) => root.style.setProperty(name, value + 'px'));
            const spatialNext=JSON.stringify(occupancy);
            if(spatialNext!==spatialSignature){spatialSignature=spatialNext;win.dispatchEvent(new win.CustomEvent('friday:spatial-layout',{detail:JSON.parse(spatialNext)}));}
            for (const el of doc.querySelectorAll('.fwin-body,.chat-panel,.chat-win')) {
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
        function fitSceneCamera(sceneState) {
            const stage = destroyed ? null : occupancy.stage;
            const { camera, scene, basePosition, targetLook, structures, keys, delta } = sceneState;
            camera.aspect = stage ? stage.w / stage.h : win.innerWidth / win.innerHeight;
            if (scene?.fog && !originalFog.has(scene.fog)) originalFog.set(scene.fog, scene.fog.density);
            if (!stage || !win.THREE) { fitBounds = null; projectedBounds = null; fittedDistance = 0; camera.far = Math.max(camera.far,1000); if(scene?.fog && originalFog.has(scene.fog))scene.fog.density=originalFog.get(scene.fog); return; }
            const THREE = win.THREE, now = win.performance.now(), key = keys.join('|');
            if (!fitBounds || key !== fitKey || now - fitAt > 500) {
                let union = null;
                for (const name of keys) {
                    const group = structures[name]; if (!group) continue;
                    group.updateMatrixWorld(true);
                    const local = new THREE.Box3();
                    group.traverseVisible(node => {
                        if (!node.geometry) return;
                        // Buffer positions and instance transforms change as
                        // the native forms breathe, build and move.
                        node.geometry.computeBoundingBox();
                        if (!node.geometry.boundingBox || node.geometry.boundingBox.isEmpty()) return;
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
                            }
                        } else {
                            local.union(node.geometry.boundingBox.clone().applyMatrix4(relative));
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
            // The stage projection handles the actual head pose each frame;
            // neutral framing therefore needs only a modest breathing margin.
            const zoom = Number.isFinite(sceneState.headZoom) ? sceneState.headZoom : 1;
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
        }
        function projectSceneStage(camera) {
            const stage = destroyed ? null : occupancy.stage;
            // Fit the current projected sphere, including off-axis head pose,
            // before placing it in the full-resolution viewport. A uniform
            // projection adjustment preserves shape and leaves the glass
            // camera's angle intact while keeping the avatar clear of UI.
            const raw = stage && fitBounds ? projectedSphere(camera, fitBounds, stage.w, stage.h) : null;
            if (raw) {
                const pad = Math.min(12, stage.w/8, stage.h/8);
                const scale = Math.min(1, (stage.w-pad*2)/raw.w, (stage.h-pad*2)/raw.h);
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
            if (!stage || !fitBounds || !win.THREE) { projectedBounds = null; return; }
            const bounds = projectedSphere(camera, fitBounds, win.innerWidth, win.innerHeight);
            projectedBounds = bounds ? { ...bounds, inside:bounds.x>=stage.x&&bounds.y>=stage.y&&bounds.x+bounds.w<=stage.x+stage.w&&bounds.y+bounds.h<=stage.y+stage.h, form:fitKey } : null;
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
            dialog.querySelectorAll('[data-holo-form]').forEach(el => el.setAttribute('aria-pressed', String(Number(el.dataset.holoForm) === current.scene?.index)));
            const reduce = dialog.querySelector('[data-holo-reduced]'); if (reduce) reduce.hidden = !reduced.matches;
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
                    if (button.dataset.holoMode) setMode(button.dataset.holoMode);
                    if (button.dataset.holoSource) void setSource(button.dataset.holoSource, event.isTrusted);
                    if (button.dataset.holoArrangement) setArrangement(button.dataset.holoArrangement);
                    if (button.dataset.holoForm !== undefined) {
                        const index = Number(button.dataset.holoForm), scenes = win.fridayVibe?.getStructures?.() || [];
                        if (Number.isInteger(index) && index >= 0 && index < scenes.length && sample().ready) { win.fridayVibe.setStructure(index); refreshPanel(); }
                    }
                });
                dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
                dialog.addEventListener('keydown', event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); } });
                doc.body.appendChild(dialog);
            }
            const scenes = win.fridayVibe?.getStructures?.() || [];
            dialog.innerHTML = `<header class="fr-holo-header"><div><span class="fr-holo-eyebrow">AGENT FRIDAY™</span><h2 id="fr-holo-title">Scene & depth</h2></div><button data-holo-close aria-label="Close Scene and depth">×</button></header>
                <div class="fr-holo-content"><section class="fr-holo-collection" aria-label="Avatar collection"><div class="fr-holo-scene-heading"><small data-holo-transition>Original Friday scene</small><h3 data-holo-scene-name></h3><p>Your holographic desktop stays alive as you work.</p></div><div class="fr-holo-forms">${scenes.map((scene, index) => `<button data-holo-form="${index}" aria-pressed="false"><span>${String(index + 1).padStart(2, '0')}</span><strong>${esc(scene.name)}</strong></button>`).join('') || '<p>Scene collection is loading. Reopen this panel in a moment.</p>'}</div></section>
                <aside class="fr-holo-controls"><section><h3>Workspace depth</h3><div class="fr-holo-segment">${MODES.map(mode => `<button data-holo-mode="${mode}" aria-pressed="false">${upper(mode)}</button>`).join('')}</div><p>Depth changes the light and layers around your work. Text and controls stay steady.</p></section>
                <section><h3>Motion source</h3><div class="fr-holo-sources"><button data-holo-source="off">Off</button><button data-holo-source="preview">Pointer preview</button><button data-holo-source="camera">Enable head tracking</button></div><p>Pointer preview uses no camera. Head tracking requests camera access through Friday’s original tracker.</p><div class="fr-holo-status" role="status" data-holo-status></div><p data-holo-reduced hidden>Reduced motion is on. Moving workspace decorations remain still.</p></section>
                <section><h3>Friday’s place in your work</h3><div class="fr-holo-arrangements">${[['companion','Companion','A place beside the work'],['present','Present','A generous holographic stage'],['focus','Focus','More room for the workspace']].map(([value, title, description]) => `<button data-holo-arrangement="${value}" aria-pressed="false"><strong>${title}</strong><small>${description}</small></button>`).join('')}</div><p>Applies to desktop workspaces. Your floating window sizes are kept for Restore.</p></section>${!win.__FRIDAY_STANDALONE__ && win.__FRIDAY_CHROME__ !== 'chat' ? '<section class="fr-holo-advanced"><button data-holo-details>More scene settings ↗</button><p>Evolution, hand tracking, timelapse and the original scene controls.</p></section>' : ''}</aside></div>`;
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
        function onVisibility() { if (doc.hidden) { clearPreview(); void setSource('off', false); } }
        function poll() {
            if (destroyed || doc.hidden) return;
            ensureDepth();
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
            win.clearInterval(timer); observer?.disconnect(); chromeObserver?.disconnect(); win.cancelAnimationFrame(chromeFrame); depth?.destroy(); dialog?.remove();
            doc.removeEventListener('pointermove', onPointer); doc.removeEventListener('pointerleave', leavePointer); doc.removeEventListener('visibilitychange', onVisibility);
            doc.removeEventListener('transitionrun',onChromeTransition,true); doc.removeEventListener('transitionend',onChromeTransition,true);
            win.removeEventListener('resize', updateLayout); win.removeEventListener('friday:surface-changed', updateLayout); win.removeEventListener('friday:chat-dock', updateLayout); win.removeEventListener('pagehide', destroy); reduced.removeEventListener?.('change', refreshReduced);
            tagged.forEach((value, el) => { if (el.getAttribute('data-depth') === 'middle') el.removeAttribute('data-depth'); }); tagged.clear();
            delete doc.body.dataset.fridayHoloArrangement; delete doc.body.dataset.fridayHoloWorking; delete doc.body.dataset.fridayHoloStudio; delete doc.body.dataset.fridaySpatialLayout;
            for (const name of ['content-left','content-right','content-top','content-bottom','avatar-left','avatar-top','avatar-width','avatar-height','avatar-gap']) root.style.removeProperty('--friday-'+name);
            if(ownedTopbar){if(previousTopbar)root.style.setProperty('--fr-topbar-h',previousTopbar);else root.style.removeProperty('--fr-topbar-h');ownedTopbar=false;}
            occupancy={layout:'classic',content:null,stage:null};projectedBounds=null;
        }
        function refreshReduced() { if (reduced.matches) clearPreview(); refreshPanel(); }
        function start() {
            if (destroyed || timer !== null) return;
            if(win.ResizeObserver)chromeObserver=new win.ResizeObserver(updateLayout);
            ensureDepth(); cameraSession(); updateLayout();
            timer = win.setInterval(poll, 40);
            observer = new win.MutationObserver(updateLayout); observer.observe(doc.getElementById('ui-root') || doc.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['hidden'] });
            doc.addEventListener('pointermove', onPointer, { passive: true }); doc.addEventListener('pointerleave', leavePointer); doc.addEventListener('visibilitychange', onVisibility);
            doc.addEventListener('transitionrun',onChromeTransition,true); doc.addEventListener('transitionend',onChromeTransition,true);
            win.addEventListener('resize', updateLayout); win.addEventListener('friday:surface-changed', updateLayout); win.addEventListener('friday:chat-dock', updateLayout); win.addEventListener('pagehide', destroy); reduced.addEventListener?.('change', refreshReduced);
        }
        if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', start, { once: true }); else start();
        return { open, close, setMode, setArrangement, workspaceArea, fitSceneCamera, projectSceneStage, constrainRect: rect => constrainSpatialRect(rect, occupancy.stage ? occupancy.content : null), destroy, get stageRect() { return occupancy.stage ? { ...occupancy.stage } : null; }, get state() { return { ...state, ownsTracking: !!session?.owns, spatial: JSON.parse(JSON.stringify(occupancy)), projectedAvatarBounds: projectedBounds ? {...projectedBounds} : null }; } };
    }
    return { trackingSession, spatialLayout, stageProjection, projectedSphere, constrainSpatialRect, fitRadialDistance, mount };
});
