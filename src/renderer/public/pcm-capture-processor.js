/**
 * AudioWorkletProcessor for mic capture.
 * Accumulates 4096 Float32 samples, converts to Int16 PCM, posts via MessagePort.
 * Runs on the audio thread — zero main-thread jank.
 */
class PCMCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buffer = new Float32Array(4096);
    this._offset = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;

    const channel = input[0];
    let i = 0;

    while (i < channel.length) {
      const remaining = this._buffer.length - this._offset;
      const toCopy = Math.min(remaining, channel.length - i);

      this._buffer.set(channel.subarray(i, i + toCopy), this._offset);
      this._offset += toCopy;
      i += toCopy;

      if (this._offset >= this._buffer.length) {
        // Convert Float32 → Int16
        const i16 = new Int16Array(this._buffer.length);
        for (let j = 0; j < this._buffer.length; j++) {
          const s = Math.max(-1, Math.min(1, this._buffer[j]));
          i16[j] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }

        this.port.postMessage(i16.buffer, [i16.buffer]);
        this._buffer = new Float32Array(4096);
        this._offset = 0;
      }
    }

    return true;
  }
}

registerProcessor('pcm-capture-processor', PCMCaptureProcessor);
