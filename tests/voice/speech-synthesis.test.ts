/**
 * SpeechSynthesisManager -- Unit tests for utterance queue, interrupts,
 * pause/resume, and audio output coordination.
 *
 * Sprint 4 K.3: "The Utterance" -- SpeechSynthesis
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';

// -- Hoisted mocks (vi.hoisted pattern) --------------------------------------

const mocks = vi.hoisted(() => {
  return {
    ttsEngine: {
      isReady: vi.fn(() => true),
      synthesize: vi.fn(async (text: string) => new Float32Array(text.length * 100)),
    },
    voiceProfileManager: {
      getActiveProfile: vi.fn(() => ({
        id: 'default',
        name: 'Default',
        voiceId: 'default',
        speed: 1.0,
        pitch: 0.0,
        volume: 1.0,
        isDefault: true,
      })),
    },
    sendMock: vi.fn(),
  };
});

vi.mock('../../../src/main/voice/tts-engine', () => ({
  ttsEngine: mocks.ttsEngine,
}));

vi.mock('../../../src/main/voice/voice-profile-manager', () => ({
  voiceProfileManager: mocks.voiceProfileManager,
}));

vi.mock('electron', () => ({
  BrowserWindow: {
    getAllWindows: vi.fn(() => [{
      webContents: { send: mocks.sendMock },
    }]),
  },
}));

import {
  SpeechSynthesisManager,
} from '../../../src/main/voice/speech-synthesis';

// -- Helpers ------------------------------------------------------------------

/** Flush microtask queue to let async processing complete. */
function flushAsync(ms = 10): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// -- Tests --------------------------------------------------------------------

describe('SpeechSynthesisManager', () => {
  let synth: SpeechSynthesisManager;

  beforeEach(() => {
    SpeechSynthesisManager.resetInstance();
    synth = SpeechSynthesisManager.getInstance();
    vi.clearAllMocks();
    mocks.ttsEngine.synthesize.mockImplementation(
      async (text: string) => new Float32Array(text.length * 100),
    );
  });

  // 1. speak(text) queues text and begins synthesis if idle
  it('speak(text) queues text and begins synthesis if idle', async () => {
    let resolveIt: (v: Float32Array) => void;
    mocks.ttsEngine.synthesize.mockImplementationOnce(
      () => new Promise<Float32Array>((r) => { resolveIt = r; }),
    );

    const promise = synth.speak('Hello world');
    await flushAsync();
    expect(synth.isSpeaking()).toBe(true);
    expect(mocks.ttsEngine.synthesize).toHaveBeenCalled();

    resolveIt!(new Float32Array(100));
    await promise;
  });

  // 2. Multiple speak() calls queue utterances in FIFO order
  it('multiple speak() calls queue utterances in FIFO order', async () => {
    const order: string[] = [];
    mocks.ttsEngine.synthesize.mockImplementation(async (text: string) => {
      order.push(text);
      return new Float32Array(100);
    });

    const p1 = synth.speak('First');
    const p2 = synth.speak('Second');
    const p3 = synth.speak('Third');

    await Promise.all([p1, p2, p3]);

    expect(order[0]).toBe('First');
    expect(order[1]).toBe('Second');
    expect(order[2]).toBe('Third');
  });

  // 3. speakImmediate(text) interrupts current speech and plays new text
  it('speakImmediate(text) interrupts current and plays new text', async () => {
    const events: string[] = [];
    synth.on('interrupted', () => events.push('interrupted'));
    synth.on('utterance-start', (ev) => events.push('start:' + ev.text));

    let resolveFirst: (v: Float32Array) => void;
    mocks.ttsEngine.synthesize.mockImplementationOnce(
      () => new Promise<Float32Array>((r) => { resolveFirst = r; }),
    );

    const p1 = synth.speak('Long speech');
    await flushAsync();

    mocks.ttsEngine.synthesize.mockImplementation(
      async () => new Float32Array(100),
    );
    const p2 = synth.speakImmediate('Urgent message');

    resolveFirst!(new Float32Array(100));
    await flushAsync();

    await p2;

    expect(events).toContain('interrupted');
    expect(synth.getQueueLength()).toBe(0);
  });

  // 4. stop() halts current playback and clears the queue
  it('stop() halts current playback and clears the queue', async () => {
    let resolveFirst: (v: Float32Array) => void;
    mocks.ttsEngine.synthesize.mockImplementationOnce(
      () => new Promise<Float32Array>((r) => { resolveFirst = r; }),
    );

    synth.speak('First');
    synth.speak('Second');
    synth.speak('Third');
    await flushAsync();

    expect(synth.getQueueLength()).toBe(2);

    synth.stop();

    expect(synth.isSpeaking()).toBe(false);
    expect(synth.getQueueLength()).toBe(0);

    resolveFirst!(new Float32Array(100));
    await flushAsync();
  });

  // 5. pause() suspends, resume() continues from pause point
  it('pause() suspends processing and resume() continues', async () => {
    let callCount = 0;
    const resolvers: Array<(v: Float32Array) => void> = [];

    mocks.ttsEngine.synthesize.mockImplementation((text: string) => {
      return new Promise<Float32Array>((resolve) => {
        callCount++;
        resolvers.push(resolve);
      });
    });

    synth.speak('Alpha');
    synth.speak('Beta');
    await flushAsync();

    // Alpha is being synthesized
    expect(callCount).toBe(1);

    // Pause BEFORE resolving Alpha so Beta does not start
    synth.pause();

    // Now resolve Alpha
    resolvers[0](new Float32Array(100));
    await flushAsync();

    // Beta should not have started because we are paused
    const countBeforePause = callCount;
    expect(countBeforePause).toBe(1);

    // Resume -- Beta should now be picked up
    synth.resume();
    await flushAsync();

    expect(callCount).toBeGreaterThan(countBeforePause);

    // Cleanup: resolve Beta
    if (resolvers[1]) resolvers[1](new Float32Array(100));
    await flushAsync();
  });

  // 6. isSpeaking() reflects actual state
  it('isSpeaking() reflects actual state', async () => {
    expect(synth.isSpeaking()).toBe(false);

    let resolveIt: (v: Float32Array) => void;
    mocks.ttsEngine.synthesize.mockImplementationOnce(
      () => new Promise<Float32Array>((r) => { resolveIt = r; }),
    );

    const p = synth.speak('Test');
    await flushAsync();
    expect(synth.isSpeaking()).toBe(true);

    resolveIt!(new Float32Array(100));
    await p;
    await flushAsync();
    expect(synth.isSpeaking()).toBe(false);
  });

  // 7. utterance-start and utterance-end events fire at correct times
  it('utterance-start and utterance-end events fire correctly', async () => {
    const events: string[] = [];
    synth.on('utterance-start', (ev) => events.push('start:' + ev.text));
    synth.on('utterance-end', (ev) => events.push('end:' + ev.text));

    await synth.speak('Hello');

    expect(events).toEqual(['start:Hello', 'end:Hello']);
  });

  // 8. queue-empty fires when last queued utterance completes
  it('queue-empty fires when last queued utterance completes', async () => {
    let queueEmptyFired = false;
    synth.on('queue-empty', () => { queueEmptyFired = true; });

    await synth.speak('One');
    expect(queueEmptyFired).toBe(true);
  });

  // 9. Long text is chunked at sentence boundaries for streaming playback
  it('long text is chunked at sentence boundaries', async () => {
    const chunks: string[] = [];
    mocks.ttsEngine.synthesize.mockImplementation(async (text: string) => {
      chunks.push(text);
      return new Float32Array(100);
    });

    await synth.speak('First sentence. Second sentence! Third sentence? Done.');

    expect(chunks.length).toBe(4);
    expect(chunks[0]).toBe('First sentence.');
    expect(chunks[1]).toBe('Second sentence!');
    expect(chunks[2]).toBe('Third sentence?');
    expect(chunks[3]).toBe('Done.');
  });

  // 10. Queue max depth is enforced (5 utterances, oldest dropped)
  it('queue max depth of 5 is enforced, oldest dropped', async () => {
    let resolveFirst: (v: Float32Array) => void;
    mocks.ttsEngine.synthesize.mockImplementationOnce(
      () => new Promise<Float32Array>((r) => { resolveFirst = r; }),
    );

    mocks.ttsEngine.synthesize.mockImplementation(
      async () => new Float32Array(100),
    );

    synth.speak('Active');
    await flushAsync();

    synth.speak('Q1');
    synth.speak('Q2');
    synth.speak('Q3');
    synth.speak('Q4');
    synth.speak('Q5');
    synth.speak('Q6');

    expect(synth.getQueueLength()).toBe(5);

    resolveFirst!(new Float32Array(100));
    await flushAsync(100);
  });
});
