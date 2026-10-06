(function (host, factory) {
    'use strict';
    const Depth = factory();
    if (typeof module === 'object' && module.exports) module.exports = Depth;
    if (host && host.document) host.FridayWorkspaceDepth = Depth;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    const GAINS = Object.freeze({ flat: 0, quiet: 0.25, balanced: 0.6, immersive: 1 });
    const SOURCES = new Set(['off', 'preview', 'camera']);
    const STALE_MS = 600;
    const EPSILON = 0.0005;
    const VARS = ['--friday-depth-x', '--friday-depth-y', '--friday-depth-z',
        '--friday-light-x', '--friday-light-y', '--friday-depth-presence'];
    const ATTRS = ['data-friday-depth-mode', 'data-friday-depth-source', 'data-friday-depth-motion'];
    const neutral = () => ({ x: 0, y: 0, z: 0 });
    const bounded = value => typeof value === 'number' && Number.isFinite(value)
        ? Math.max(-1, Math.min(1, value)) : 0;

    class FridayWorkspaceDepth {
        constructor(options = {}) {
            this.root = options.root || (typeof document !== 'undefined' ? document.documentElement : null);
            if (!this.root || !this.root.style || !this.root.ownerDocument) {
                throw new TypeError('FridayWorkspaceDepth requires a document element.');
            }
            this._doc = this.root.ownerDocument;
            this._win = this._doc.defaultView || globalThis;
            this._now = () => this._win.performance && this._win.performance.now
                ? this._win.performance.now() : Date.now();
            this._request = this._win.requestAnimationFrame
                ? callback => this._win.requestAnimationFrame(callback)
                : callback => this._win.setTimeout(() => callback(this._now()), 16);
            this._cancel = this._win.cancelAnimationFrame
                ? id => this._win.cancelAnimationFrame(id) : id => this._win.clearTimeout(id);
            this._mode = 'flat';
            this._source = 'off';
            this._pose = { ...neutral(), seen: false };
            this._current = neutral();
            this._lastPoseAt = null;
            this._lastFrameAt = null;
            this._frame = null;
            this._expiry = null;
            this._destroyed = false;
            this._paused = false;
            this._writes = new Map();
            this._originalStyles = new Map(VARS.map(name => [name, {
                value: this.root.style.getPropertyValue(name),
                priority: this.root.style.getPropertyPriority(name)
            }]));
            this._originalAttrs = new Map(ATTRS.map(name => [name, this.root.getAttribute(name)]));
            this._media = this._win.matchMedia ? this._win.matchMedia('(prefers-reduced-motion: reduce)') : null;
            this._onPreference = () => this.refresh();
            this._onVisibility = () => this.refresh();
            this._onHide = () => { this._paused = true; this.refresh(); };
            this._onShow = () => { this._paused = false; this.refresh(); };
            if (this._media) {
                if (this._media.addEventListener) this._media.addEventListener('change', this._onPreference);
                else if (this._media.addListener) this._media.addListener(this._onPreference);
            }
            this._doc.addEventListener('visibilitychange', this._onVisibility);
            this._win.addEventListener('pagehide', this._onHide);
            this._win.addEventListener('pageshow', this._onShow);
            this.refresh();
        }

        get state() {
            const stale = this._lastPoseAt === null || this._now() - this._lastPoseAt >= STALE_MS;
            return {
                mode: this._mode, source: this._source,
                pose: { ...this._current },
                seen: !!this._pose.seen && !stale && this._visible() && this._source !== 'off',
                stale, reducedMotion: this._reduced(), visible: this._visible(),
                settling: this._frame !== null, destroyed: this._destroyed
            };
        }

        setMode(mode) {
            if (this._destroyed) return this;
            if (!Object.prototype.hasOwnProperty.call(GAINS, mode)) throw new RangeError('Unknown depth mode.');
            this._mode = mode;
            return this.refresh();
        }

        setSource(source) {
            if (this._destroyed) return this;
            if (!SOURCES.has(source)) throw new RangeError('Unknown depth source.');
            if (source !== this._source) {
                this._source = source;
                this._forget();
            }
            return this.refresh();
        }

        setPose(pose = {}) {
            if (this._destroyed || !this._visible() || this._source === 'off') return this;
            pose = pose && typeof pose === 'object' ? pose : {};
            this._pose = { x: bounded(pose.x), y: bounded(pose.y), z: bounded(pose.z), seen: pose.seen === true };
            this._lastPoseAt = this._now();
            this._armExpiry();
            this._wake();
            return this;
        }

        refresh() {
            if (this._destroyed) return this;
            this.root.setAttribute('data-friday-depth-mode', this._mode);
            this.root.setAttribute('data-friday-depth-source', this._source);
            this.root.setAttribute('data-friday-depth-motion', this._reduced() ? 'reduced' : 'full');
            if (!this._visible()) this._forget();
            if (!this._canMove()) {
                this._stop();
                this._current = neutral();
                this._write();
            } else {
                this._armExpiry();
                this._wake();
            }
            return this;
        }

        destroy() {
            if (this._destroyed) return;
            this._stop();
            this._doc.removeEventListener('visibilitychange', this._onVisibility);
            this._win.removeEventListener('pagehide', this._onHide);
            this._win.removeEventListener('pageshow', this._onShow);
            if (this._media) {
                if (this._media.removeEventListener) this._media.removeEventListener('change', this._onPreference);
                else if (this._media.removeListener) this._media.removeListener(this._onPreference);
            }
            for (const [name, old] of this._originalStyles) {
                if (old.value) this.root.style.setProperty(name, old.value, old.priority);
                else this.root.style.removeProperty(name);
            }
            for (const [name, old] of this._originalAttrs) {
                if (old === null) this.root.removeAttribute(name);
                else this.root.setAttribute(name, old);
            }
            this._pose = { ...neutral(), seen: false };
            this._current = neutral();
            this._lastPoseAt = null;
            this._destroyed = true;
        }

        _visible() { return !this._paused && !this._doc.hidden && this._doc.visibilityState !== 'hidden'; }
        _reduced() { return !!(this._media && this._media.matches); }
        _canMove() { return this._visible() && !this._reduced() && this._mode !== 'flat' && this._source !== 'off'; }
        _target() {
            if (!this._canMove() || !this._pose.seen || this._lastPoseAt === null || this._now() - this._lastPoseAt >= STALE_MS) return neutral();
            const gain = GAINS[this._mode];
            return { x: this._pose.x * gain, y: this._pose.y * gain, z: this._pose.z * gain };
        }
        _forget() {
            this._pose = { ...neutral(), seen: false };
            this._lastPoseAt = null;
        }
        _stop() {
            if (this._frame !== null) this._cancel(this._frame);
            if (this._expiry !== null) this._win.clearTimeout(this._expiry);
            this._frame = this._expiry = this._lastFrameAt = null;
        }
        _armExpiry() {
            if (this._expiry !== null) this._win.clearTimeout(this._expiry);
            this._expiry = null;
            if (!this._canMove() || !this._pose.seen || this._lastPoseAt === null) return;
            const remaining = STALE_MS - (this._now() - this._lastPoseAt);
            if (remaining > 0) this._expiry = this._win.setTimeout(() => {
                this._expiry = null;
                this._wake();
            }, remaining);
        }
        _wake() {
            if (this._destroyed) return;
            if (!this._canMove()) {
                this._stop();
                this._current = neutral();
                this._write();
                return;
            }
            const target = this._target();
            const pending = ['x', 'y', 'z'].some(axis => Math.abs(target[axis] - this._current[axis]) > EPSILON);
            if (pending && this._frame === null) this._frame = this._request(time => this._step(time));
            else if (!pending && this._frame === null) { this._current = target; this._write(); }
        }
        _step(time) {
            this._frame = null;
            if (this._destroyed) return;
            if (!this._canMove()) { this.refresh(); return; }
            const elapsed = this._lastFrameAt === null ? 16 : Math.max(0, Math.min(100, time - this._lastFrameAt));
            this._lastFrameAt = time;
            const amount = 1 - Math.exp(-elapsed / 65);
            const target = this._target();
            let moving = false;
            for (const axis of ['x', 'y', 'z']) {
                this._current[axis] += (target[axis] - this._current[axis]) * amount;
                if (Math.abs(target[axis] - this._current[axis]) < EPSILON) this._current[axis] = target[axis];
                else moving = true;
            }
            this._write();
            if (moving) this._frame = this._request(next => this._step(next));
            else this._lastFrameAt = null;
        }
        _write() {
            const { x, y, z } = this._current;
            const values = {
                '--friday-depth-x': (x * 12).toFixed(3) + 'px',
                '--friday-depth-y': (y * 12).toFixed(3) + 'px',
                '--friday-depth-z': z.toFixed(4),
                '--friday-light-x': (50 + x * 25).toFixed(2) + '%',
                '--friday-light-y': (50 + y * 25).toFixed(2) + '%',
                '--friday-depth-presence': this._mode === 'flat' ? '0' : String(GAINS[this._mode])
            };
            for (const name of VARS) if (this._writes.get(name) !== values[name]) {
                this.root.style.setProperty(name, values[name]);
                this._writes.set(name, values[name]);
            }
        }
    }
    return FridayWorkspaceDepth;
});
