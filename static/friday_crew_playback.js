/* One tagged playback stream for the host and every Crew specialist. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.FridayCrewPlayback = api;
})(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';
  const RATE = 24000, MAX_SAMPLES = RATE * 300, MAX_BYTES = 4 * 1024 * 1024;
  function normalize(buffer) {
    if (!buffer || !Number.isFinite(buffer.duration) || buffer.duration > 300 || buffer.numberOfChannels < 1) throw new Error('Speech exceeds the playback limit.');
    const length = Math.ceil(buffer.length * RATE / buffer.sampleRate);
    if (!length || length > MAX_SAMPLES) throw new Error('Speech is empty or too long.');
    const out = new Float32Array(length), channels = [];
    for (let c = 0; c < buffer.numberOfChannels; c++) channels.push(buffer.getChannelData(c));
    for (let i = 0; i < length; i++) {
      const p = i * buffer.sampleRate / RATE, a = Math.min(Math.floor(p), buffer.length - 1), b = Math.min(a + 1, buffer.length - 1), f = p - a;
      let value = 0;
      for (const channel of channels) value += channel[a] * (1 - f) + channel[b] * f;
      out[i] = Math.max(-1, Math.min(1, value / channels.length));
    }
    return out;
  }
  class Coordinator {
    constructor(options) { this.options = options; this.session = null; this.active = null; this.generation = 0; this.blocked = false; this.closed = false; this.finished = new Set(); }
    emit(status, extra) { this.options.onState?.(Object.assign({status, session_id:this.session?.session_id, conversation_id:this.session?.conversation_id}, this.active?.meta || {}, extra || {})); }
    post(message, transfer) { this.options.port.postMessage(message, transfer || []); }
    ack(status, item = this.active) {
      if (!item || item.terminal) return;
      if (['finished','interrupted','failed'].includes(status)) { item.terminal = true; this.finished.add(item.meta.utterance_id); }
      this.options.send({type:'crew_playback',session_id:item.meta.session_id,epoch:item.meta.epoch,utterance_id:item.meta.utterance_id,status,played_samples:Math.floor(item.played || 0),...(item.total == null ? {} : {total_samples:item.total})});
    }
    invalidate(status = 'interrupted', message) {
      this.generation++;
      const item = this.active;
      if (item) { this.ack(status, item); this.emit(status, message ? {message} : {}); }
      this.active = null;
      this.post({type:'flush'});
    }
    quiet() { this.blocked = true; this.invalidate(); }
    close() { this.invalidate(); this.closed = true; this.session = null; this.emit('disconnected'); }
    fail(message) { this.invalidate('failed', message); this.options.onError?.(message); }
    matches(m) { return !this.closed && this.session && m.session_id === this.session.session_id && m.conversation_id === this.session.conversation_id && m.epoch === this.session.epoch; }
    receive(m) {
      if (!m || typeof m.type !== 'string' || !m.type.startsWith('crew_')) return false;
      if (this.closed) return true;
      if (m.type === 'crew_session') {
        if (!m.session_id || !m.conversation_id || !Number.isInteger(m.epoch)) return true;
        this.invalidate(); this.finished.clear(); this.session = {session_id:m.session_id,conversation_id:m.conversation_id,epoch:m.epoch}; this.blocked = false; this.emit('listening'); return true;
      }
      if (m.type === 'crew_interrupted') {
        if (this.session && m.session_id === this.session.session_id && m.conversation_id === this.session.conversation_id && Number.isInteger(m.epoch) && m.epoch > this.session.epoch) {
          this.invalidate(); this.finished.clear(); this.session.epoch = m.epoch; this.blocked = false; this.emit('listening');
        }
        return true;
      }
      if (!this.matches(m) || this.blocked) return true;
      if (m.type === 'crew_speech_error' && (!m.utterance_id || m.utterance_id !== this.active?.meta.utterance_id)) {
        if (m.utterance_id && this.finished.has(m.utterance_id)) return true;
        const message=String(m.message || 'Crew voice is unavailable.');
        if(m.utterance_id)this.ack('failed',{meta:m,played:0});
        this.options.onState?.({...m,status:'failed',message}); this.options.onError?.(message); return true;
      }
      if (m.type === 'crew_speech_start') {
        if (!m.utterance_id || !m.speaker_id || this.finished.has(m.utterance_id)) return true;
        if (this.active) { if (this.active.meta.utterance_id === m.utterance_id) return true; this.fail('A second speaker arrived before the room released its voice.'); }
        this.active = {meta:{...m,text:String(m.text || '').slice(0,5000)},next:0,bytes:0,chunks:[],played:0,samples:0,ended:false};
        if (!['pcm_s16le','audio/mpeg','audio/wav'].includes(m.encoding) || m.sample_rate !== RATE) { this.fail('Unsupported Crew speech format.'); return true; }
        this.post({type:'crew_begin',utterance_id:m.utterance_id,epoch:m.epoch}); this.emit('preparing'); return true;
      }
      const item = this.active;
      if (!item || m.utterance_id !== item.meta.utterance_id) return true;
      if (m.type === 'crew_speech_error') { this.fail(String(m.message || 'Voice provider could not speak.')); return true; }
      if (m.type === 'crew_speech_text') { item.meta.text = (m.append ? item.meta.text + String(m.text || '') : String(m.text || '')).slice(0,5000); this.emit(item.started ? 'speaking' : 'preparing'); return true; }
      if (m.type === 'crew_audio') {
        if (item.ended) return true;
        if (!Number.isInteger(m.seq) || m.seq < 0) { this.fail('Invalid speech sequence.'); return true; }
        if (m.seq < item.next) return true;
        if (m.seq !== item.next) { this.fail('A speech segment was lost.'); return true; }
        try {
          if (typeof m.data !== 'string' || m.data.length > 87384) throw new Error('Speech segment is too large.');
          const raw = atob(m.data), bytes = Uint8Array.from(raw, c => c.charCodeAt(0));
          if (bytes.length > 65536) throw new Error('Speech segment is too large.');
          item.next++; item.bytes += bytes.length;
          if (item.meta.encoding === 'pcm_s16le') {
            if (bytes.length % 2) throw new Error('Invalid PCM speech segment.');
            const view = new DataView(bytes.buffer), pcm = new Float32Array(bytes.length / 2);
            for (let i = 0; i < pcm.length; i++) pcm[i] = view.getInt16(i * 2, true) / 32768;
            item.samples += pcm.length;
            if (item.samples > MAX_SAMPLES) throw new Error('Speech exceeds five minutes.');
            this.post({type:'samples',utterance_id:m.utterance_id,epoch:m.epoch,data:pcm},[pcm.buffer]);
          } else {
            if (item.bytes > MAX_BYTES) throw new Error('Encoded speech exceeds the size limit.');
            item.chunks.push(bytes);
          }
        } catch (e) { this.fail(e.message); }
      } else if (m.type === 'crew_speech_end' && !item.ended) {
        item.ended = true;
        if (item.meta.encoding === 'pcm_s16le') {
          if (!Number.isInteger(m.total_samples) || m.total_samples !== item.samples) this.fail('Speech ended with an incomplete sample count.');
          else { item.total = item.samples; this.post({type:'crew_end',utterance_id:m.utterance_id,epoch:m.epoch,total_samples:item.total}); }
        } else this.decode(item);
      }
      return true;
    }
    async decode(item) {
      const generation = this.generation;
      try {
        const bytes = new Uint8Array(item.bytes); let offset = 0;
        for (const chunk of item.chunks) { bytes.set(chunk, offset); offset += chunk.length; }
        item.chunks = [];
        const buffer = await this.options.audioContext.decodeAudioData(bytes.buffer);
        if (this.closed || generation !== this.generation || this.active !== item) return;
        const pcm = normalize(buffer); item.total = pcm.length;
        this.post({type:'samples',utterance_id:item.meta.utterance_id,epoch:item.meta.epoch,data:pcm},[pcm.buffer]);
        this.post({type:'crew_end',utterance_id:item.meta.utterance_id,epoch:item.meta.epoch,total_samples:item.total});
      } catch (e) { if (generation === this.generation && this.active === item) this.fail('Could not decode Crew speech: ' + e.message); }
    }
    worklet(m) {
      if (!m?.type?.startsWith('crew_')) return false;
      const item = this.active;
      if (!item || item.meta.utterance_id !== m.utterance_id || item.meta.epoch !== m.epoch) return true;
      item.played = Math.max(item.played, Math.min(Number(m.played_samples) || 0, MAX_SAMPLES));
      if (m.type === 'crew_started') { item.started = true; this.ack('started'); this.emit('speaking'); }
      else if (m.type === 'crew_progress') this.ack('progress');
      else if (m.type === 'crew_finished') {
        if (!item.ended || item.total !== item.played) this.fail('Speech playback ended before its sealed sample count.');
        else { this.ack('finished'); this.emit('finished'); this.active = null; }
      } else if (m.type === 'crew_failed') this.fail(m.message || 'Crew playback failed.');
      return true;
    }
  }
  return {Coordinator, normalize, RATE, MAX_SAMPLES};
});
