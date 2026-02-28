/**
 * sound-effects.ts — Synthesised audio cues using Web Audio API.
 * No asset files needed — all tones are generated from oscillators.
 */

let ctx: AudioContext | null = null;

function getContext(): AudioContext {
  if (!ctx) ctx = new AudioContext();
  if (ctx.state === 'suspended') ctx.resume();
  return ctx;
}

/** Short ascending chime — played on connection */
export function playConnectedChime(): void {
  const ac = getContext();
  const now = ac.currentTime;

  // Two-note ascending: C5 → E5
  [523.25, 659.25].forEach((freq, i) => {
    const osc = ac.createOscillator();
    const gain = ac.createGain();
    osc.type = 'sine';
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0, now + i * 0.12);
    gain.gain.linearRampToValueAtTime(0.15, now + i * 0.12 + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.001, now + i * 0.12 + 0.25);
    osc.connect(gain).connect(ac.destination);
    osc.start(now + i * 0.12);
    osc.stop(now + i * 0.12 + 0.3);
  });
}

/** Soft ping — played when mic starts listening */
export function playListeningPing(): void {
  const ac = getContext();
  const now = ac.currentTime;

  const osc = ac.createOscillator();
  const gain = ac.createGain();
  osc.type = 'sine';
  osc.frequency.value = 880; // A5
  gain.gain.setValueAtTime(0, now);
  gain.gain.linearRampToValueAtTime(0.1, now + 0.01);
  gain.gain.exponentialRampToValueAtTime(0.001, now + 0.15);
  osc.connect(gain).connect(ac.destination);
  osc.start(now);
  osc.stop(now + 0.2);
}

/** Low subtle tone — played when Friday starts "thinking" (tool call in progress) */
export function playThinkingTone(): void {
  const ac = getContext();
  const now = ac.currentTime;

  const osc = ac.createOscillator();
  const gain = ac.createGain();
  osc.type = 'triangle';
  osc.frequency.value = 330; // E4
  gain.gain.setValueAtTime(0, now);
  gain.gain.linearRampToValueAtTime(0.06, now + 0.05);
  gain.gain.setValueAtTime(0.06, now + 0.3);
  gain.gain.exponentialRampToValueAtTime(0.001, now + 0.5);
  osc.connect(gain).connect(ac.destination);
  osc.start(now);
  osc.stop(now + 0.55);
}

/** Notification bell — two-tone chime for reminders/predictions */
export function playNotificationBell(): void {
  const ac = getContext();
  const now = ac.currentTime;

  // G5 → C6 (bright, attention-getting)
  [783.99, 1046.5].forEach((freq, i) => {
    const osc = ac.createOscillator();
    const gain = ac.createGain();
    osc.type = 'sine';
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0, now + i * 0.1);
    gain.gain.linearRampToValueAtTime(0.12, now + i * 0.1 + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.001, now + i * 0.1 + 0.35);
    osc.connect(gain).connect(ac.destination);
    osc.start(now + i * 0.1);
    osc.stop(now + i * 0.1 + 0.4);
  });
}

/** Descending tone — played on disconnect */
export function playDisconnectTone(): void {
  const ac = getContext();
  const now = ac.currentTime;

  // E5 → C5 (descending)
  [659.25, 523.25].forEach((freq, i) => {
    const osc = ac.createOscillator();
    const gain = ac.createGain();
    osc.type = 'sine';
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0, now + i * 0.12);
    gain.gain.linearRampToValueAtTime(0.1, now + i * 0.12 + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.001, now + i * 0.12 + 0.25);
    osc.connect(gain).connect(ac.destination);
    osc.start(now + i * 0.12);
    osc.stop(now + i * 0.12 + 0.3);
  });
}
