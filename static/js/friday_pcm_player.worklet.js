// THE one PCM player for every streaming voice path.
//
// Streamed speech used to be played by creating an AudioBufferSourceNode per
// arriving chunk and scheduling it against a running cursor. That is the
// construct behind two separate defects:
//
//   * Chrome 152 sometimes replaced the start of a chunk with a single
//     128-sample render block repeated about a hundred times - the dial-up
//     screech people reported on Gemini Live. Fixed in Chrome 153.
//   * Independently of any browser bug, every chunk boundary is a resampler
//     reset and a scheduling rounding error. Measured on Chrome 153 with a
//     pure 440 Hz sine: 34 discontinuities in six seconds with the context
//     pinned to 24 kHz and 38 unpinned, the waveform stepping by up to 0.62
//     where the signal itself can only move 0.035. That is the grain you hear
//     building over a long reply, and no browser version fixes it.
//
// So: ONE node, always running, fed by a ring buffer, resampling continuously
// with the fractional read cursor carried across chunks. Underruns emit clean
// silence and re-prime rather than clicking. Measured the same way, this path
// produces zero discontinuities at either context rate.
//
// Loaded by index.html (the desktop UI) and static/live/friday_live.html (the
// FRIDAY LIVE PWA) from this one file, so the two cannot drift apart.
// Messages in:  {type:'samples', data:Float32Array} | {type:'flush'}
// Messages out: {type:'active'} | {type:'drained'}

class FridayPCMPlayer extends AudioWorkletProcessor {
  constructor(o){
    super();
    this.srcRate = (o.processorOptions && o.processorOptions.srcRate) || 24000;
    this.step = this.srcRate / sampleRate;     // src samples consumed per output sample
    // Ring sized for the WHOLE of even a long read-aloud: Gemini streams a
    // response faster than realtime, so the buffer legitimately holds the entire
    // pending utterance (a 90s answer arrives in ~30s → ~60s buffered). Consumed
    // at exactly realtime, it drains to zero between turns. 180s covers any
    // realistic single turn without the overflow guard ever truncating it.
    this.size = Math.ceil(this.srcRate * 180); // 180s ring at source rate
    this.ring = new Float32Array(this.size);
    this.writeIdx = 0;                          // absolute samples written
    this.readIdx = 0;                           // absolute fractional read cursor
    this.lastPlaying = false;
    // Jitter buffer: Gemini streams 24kHz PCM in faster-than-realtime bursts
    // over a jittery network. Playing the instant the first sample lands makes
    // the ring underrun on the next network gap — each underrun is a click, and
    // over a long call the accumulated clicks read as progressive rasp. So we
    // PRIME: hold ~120ms before starting, and re-prime after any underrun, so
    // playback only ever runs off a cushioned buffer.
    this.prefill = Math.max(1, Math.round(this.srcRate * 0.12));   // 120ms cushion
    // Anti-wrap guard ONLY (not latency management): if the producer somehow
    // gets within a hair of lapping the read pointer — a >~3min continuous
    // monologue delivered faster than realtime — fast-forward the reader to
    // drop the oldest audio rather than let the write pointer overwrite unread
    // samples (silent corruption = rasp). Sits just under the ring so it NEVER
    // fires on a normal multi-second turn (which must buffer fully, not be
    // truncated). A dropped 3-minutes-ago sample is inaudible; a lapped pointer
    // is not.
    this.maxLag = this.size - Math.round(this.srcRate * 2);        // ~178s, anti-wrap only
    this.priming = true;                        // waiting for the initial cushion
    // Running out of audio mid-waveform and dropping straight to zero is a
    // STEP, and a step is a click. 4ms is far too short to hear as a fade and
    // long enough to remove the edge entirely.
    this.fadeLen = Math.max(1, Math.round(sampleRate * 0.004));
    this.fadeLeft = 0;
    this.last = 0;
    this.port.onmessage = (e) => {
      const m = e.data;
      if (m.type === 'samples') {
        const f = m.data;
        for (let i = 0; i < f.length; i++) { this.ring[this.writeIdx % this.size] = f[i]; this.writeIdx++; }
        // Overflow guard: keep the producer at most maxLag ahead of the reader.
        const lag = this.writeIdx - Math.floor(this.readIdx);
        if (lag > this.maxLag) this.readIdx = this.writeIdx - this.maxLag;
      } else if (m.type === 'flush') {
        this.readIdx = this.writeIdx;            // drop everything pending
        this.priming = true;                      // re-cushion before next audio
        this.fadeLeft = this.fadeLen;             // ramp the tail out, do not clip it
      }
    };
  }
  _avail(){ return this.writeIdx - Math.floor(this.readIdx); }     // src samples ahead of read
  process(inputs, outputs) {
    const out = outputs[0][0];
    if (!out) return true;
    if (this.priming) {
      // Still building the cushion — emit silence until we have prefill samples,
      // so we never start into an immediate underrun.
      // Finish any fade first. The underrun branch below sets priming, so
      // without this the very next render quantum would slam the ramp to zero
      // and put back the click the ramp exists to remove.
      if (this._avail() < this.prefill) {
        for (let i = 0; i < out.length; i++) {
          if (this.fadeLeft > 0) { this.fadeLeft--; out[i] = this.last * (this.fadeLeft / this.fadeLen); }
          else { out[i] = 0; this.last = 0; }
        }
        return true;
      }
      this.priming = false;
    }
    for (let i = 0; i < out.length; i++) {
      const base = Math.floor(this.readIdx);
      if (base + 1 < this.writeIdx) {
        const frac = this.readIdx - base;
        const s0 = this.ring[base % this.size];
        const s1 = this.ring[(base + 1) % this.size];
        out[i] = s0 * (1 - frac) + s1 * frac;
        this.readIdx += this.step;
        this.last = out[i];
        this.fadeLeft = this.fadeLen;            // armed, in case the next sample is missing
      } else {
        // Underrun: fade the last value out rather than stepping to zero. A
        // hard drop from mid-waveform is a click at the end of every turn and
        // again at every network stutter.
        if (this.fadeLeft > 0) { this.fadeLeft--; out[i] = this.last * (this.fadeLeft / this.fadeLen); }
        else { out[i] = 0; this.last = 0; }
        this.priming = true;                     // re-cushion before resuming
      }
    }
    const playing = !this.priming && (Math.floor(this.readIdx) + 1) < this.writeIdx;
    if (playing !== this.lastPlaying) { this.lastPlaying = playing; this.port.postMessage({ type: playing ? 'active' : 'drained' }); }
    return true;
  }
}
registerProcessor('friday-pcm-player', FridayPCMPlayer);
