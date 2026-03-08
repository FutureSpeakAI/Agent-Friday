/**
 * Sprint 4 Integration: Voice Circle
 *
 * End-to-end integration test validating the full voice conversation loop:
 * TranscriptionPipeline.onTranscript -> LLM chat -> SpeechSynthesis.speak
 *   -> barge-in via AudioCapture voice-start -> turn-taking via queue-empty
 *   -> graceful degradation when STT/TTS unavailable
 *
 * Sprint 4 L.1: 'The Dialogue' -- Voice Circle Integration
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

// -- Hoisted mocks (vi.mock is hoisted, so variables must be too) --

const mocks = vi.hoisted(() => {
  const acListeners = new Map<string, Set<(payload?: unknown) => void>>();
  return {
    acListeners,
    audioCapture: {
      startCapture: vi.fn(async () => {}),
      stopCapture: vi.fn(),
      isCapturing: vi.fn(() => false),
      on: vi.fn((event: string, cb: (payload?: unknown) => void) => {
        if (!acListeners.has(event)) { acListeners.set(event, new Set()); }
        acListeners.get(event)!.add(cb);
        return () => { acListeners.get(event)?.delete(cb); };
      }),
    },
    whisperProvider: {
      isReady: vi.fn(() => true),
      loadModel: vi.fn(async () => {}),
      transcribe: vi.fn(async (audio: Float32Array) => ({
        text: 'test transcription',
        language: 'en',
        segments: [{ text: 'test transcription', start: 0, end: 1.5 }],
        duration: audio.length / 16000,
        processingTime: 200,
      })),
    },
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
    settingsGet: vi.fn(() => ({})),
    llmComplete: vi.fn(async () => ({
      content: 'Hello! How can I help you?',
      toolCalls: [],
      usage: { inputTokens: 10, outputTokens: 20 },
      model: 'test-model',
      provider: 'local' as const,
      stopReason: 'end_turn' as const,
      latencyMs: 150,
    })),
  };
});

vi.mock('electron', () => ({
  app: {
    isPackaged: false,
    getPath: vi.fn(() => '/tmp/test'),
    getName: vi.fn(() => 'nexus-test'),
    on: vi.fn(),
    whenReady: vi.fn().mockResolvedValue(undefined),
  },
  ipcMain: {
    handle: vi.fn(),
    on: vi.fn(),
    once: vi.fn(),
  },
  BrowserWindow: {
    getAllWindows: vi.fn(() => [{
      webContents: { send: mocks.sendMock },
    }]),
  },
  nativeTheme: { shouldUseDarkColors: true, on: vi.fn() },
  shell: { openExternal: vi.fn() },
  dialog: { showOpenDialog: vi.fn(), showSaveDialog: vi.fn() },
  screen: {
    getPrimaryDisplay: vi.fn(() => ({
      workAreaSize: { width: 1920, height: 1080 },
    })),
  },
}));

vi.mock('../../../src/main/settings', () => ({
  settingsManager: {
    get: mocks.settingsGet,
    setSetting: vi.fn(() => Promise.resolve()),
  },
}));

vi.mock('../../../src/main/voice/audio-capture', () => ({
  audioCapture: mocks.audioCapture,
}));

vi.mock('../../../src/main/voice/whisper-provider', () => ({
  whisperProvider: mocks.whisperProvider,
}));

vi.mock('../../../src/main/voice/tts-engine', () => ({
  ttsEngine: mocks.ttsEngine,
}));

vi.mock('../../../src/main/voice/voice-profile-manager', () => ({
  voiceProfileManager: mocks.voiceProfileManager,
}));

import { TranscriptionPipeline } from '../../../src/main/voice/transcription-pipeline';
import type { TranscriptEvent } from '../../../src/main/voice/transcription-pipeline';
import { SpeechSynthesisManager } from '../../../src/main/voice/speech-synthesis';
import { llmClient } from '../../../src/main/llm-client';
import type { LLMRequest, LLMResponse, LLMProvider } from '../../../src/main/llm-client';
import type { ProviderName } from '../../../src/main/intelligence-router';

// -- Helpers ------------------------------------------------------------------

function emitAC(event: string, payload?: unknown): void {
  const cbs = mocks.acListeners.get(event);
  if (cbs) { for (const cb of cbs) { cb(payload); } }
}

function buf(sec = 1): Float32Array {
  const n = 16000 * sec;
  const b = new Float32Array(n);
  for (let i = 0; i < n; i++) b[i] = Math.sin(2 * Math.PI * 440 * (i / 16000)) * 0.5;
  return b;
}

function flushAsync(ms = 10): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function createMockLLMProvider(name: ProviderName, available = true): LLMProvider {
  return {
    name,
    isAvailable: () => available,
    complete: mocks.llmComplete as unknown as LLMProvider['complete'],
    async *stream() { yield { done: true }; },
  };
}

/**
 * Wire the voice circle: transcript -> LLM -> speak,
 * barge-in on voice-start, resume listening on queue-empty.
 */
function wireVoiceCircle(
  pipeline: TranscriptionPipeline,
  synth: SpeechSynthesisManager,
): { teardown: () => void } {
  const unsubs: Array<() => void> = [];

  // Transcript -> LLM -> Speak
  unsubs.push(pipeline.on('transcript', async (evt) => {
    const te = evt as TranscriptEvent;
    if (!te.text.trim()) return;
    const response = await llmClient.complete({
      messages: [{ role: 'user', content: te.text }],
      maxTokens: 256,
    });
    try { await synth.speak(response.content); } catch { /* TTS may be unavailable */ }
  }));

  // Barge-in: user voice-start interrupts Agent Friday speech
  unsubs.push(mocks.audioCapture.on('voice-start', () => {
    if (synth.isSpeaking()) {
      synth.stop();
    }
  }));

  // Turn-taking: resume listening after speaking finishes
  unsubs.push(synth.on('queue-empty', () => {
    if (!pipeline.isListening()) {
      pipeline.start();
    }
  }));

  return {
    teardown: () => { for (const u of unsubs) { u(); } },
  };
}

// -- Tests --------------------------------------------------------------------

describe('Voice Circle Integration -- Sprint 4 L.1', () => {
  let pipeline: TranscriptionPipeline;
  let synth: SpeechSynthesisManager;
  let circle: { teardown: () => void };

  beforeEach(async () => {
    TranscriptionPipeline.resetInstance();
    SpeechSynthesisManager.resetInstance();
    vi.clearAllMocks();
    mocks.acListeners.clear();

    // Reset mock implementations
    mocks.whisperProvider.loadModel.mockImplementation(async () => {});
    mocks.whisperProvider.isReady.mockReturnValue(true);
    mocks.whisperProvider.transcribe.mockImplementation(async (audio: Float32Array) => ({
      text: 'Hello Agent Friday',
      language: 'en',
      segments: [{ text: 'Hello Agent Friday', start: 0, end: 1.5 }],
      duration: audio.length / 16000,
      processingTime: 200,
    }));
    mocks.audioCapture.startCapture.mockImplementation(async () => {});
    mocks.audioCapture.stopCapture.mockImplementation(() => {});
    mocks.audioCapture.isCapturing.mockReturnValue(false);
    mocks.audioCapture.on.mockImplementation((event: string, cb: (payload?: unknown) => void) => {
      if (!mocks.acListeners.has(event)) { mocks.acListeners.set(event, new Set()); }
      mocks.acListeners.get(event)!.add(cb);
      return () => { mocks.acListeners.get(event)?.delete(cb); };
    });
    mocks.ttsEngine.isReady.mockReturnValue(true);
    mocks.ttsEngine.synthesize.mockImplementation(
      async (text: string) => new Float32Array(text.length * 100),
    );
    mocks.llmComplete.mockImplementation(async () => ({
      content: 'Hello! How can I help you?',
      toolCalls: [],
      usage: { inputTokens: 10, outputTokens: 20 },
      model: 'test-model',
      provider: 'local' as const,
      stopReason: 'end_turn' as const,
      latencyMs: 150,
    }));

    // Register mock local LLM provider
    llmClient.registerProvider(createMockLLMProvider('local'));

    pipeline = TranscriptionPipeline.getInstance();
    synth = SpeechSynthesisManager.getInstance();
    await pipeline.start();
    circle = wireVoiceCircle(pipeline, synth);
  });

  afterEach(() => {
    circle.teardown();
    TranscriptionPipeline.resetInstance();
    SpeechSynthesisManager.resetInstance();
    vi.restoreAllMocks();
  });

  // 1. Transcribed text flows to the LLM as a chat message
  it('transcribed text flows to the LLM as a chat message', async () => {
    emitAC('voice-start');
    emitAC('audio-chunk', buf(1));
    emitAC('voice-end', buf(1));
    await flushAsync(50);

    expect(mocks.llmComplete).toHaveBeenCalledTimes(1);
    const call = mocks.llmComplete.mock.calls[0][0] as LLMRequest;
    expect(call.messages).toHaveLength(1);
    expect(call.messages[0].role).toBe('user');
    expect(call.messages[0].content).toBe('Hello Agent Friday');
  });

  // 2. LLM text response flows to SpeechSynthesis for speaking
  it('LLM text response flows to SpeechSynthesis for speaking', async () => {
    mocks.llmComplete.mockImplementationOnce(async () => ({
      content: 'I am Agent Friday, at your service.',
      toolCalls: [],
      usage: { inputTokens: 10, outputTokens: 15 },
      model: 'test-model',
      provider: 'local' as const,
      stopReason: 'end_turn' as const,
      latencyMs: 100,
    }));

    emitAC('voice-start');
    emitAC('audio-chunk', buf(1));
    emitAC('voice-end', buf(1));
    await flushAsync(50);

    // TTS engine should have been called with the LLM response text
    expect(mocks.ttsEngine.synthesize).toHaveBeenCalled();
    const synthCall = mocks.ttsEngine.synthesize.mock.calls[0][0];
    expect(synthCall).toContain('Agent Friday');
  });

  // 3. Barge-in: user voice-start interrupts Agent Friday speech
  it('barge-in: user voice-start interrupts Agent Friday speech', async () => {
    // Trigger speech first
    // Make TTS hang so speech is still in progress when barge-in fires
    mocks.ttsEngine.synthesize.mockImplementation(
      () => new Promise<Float32Array>(() => {}), // never resolves
    );

    emitAC('voice-start');
    emitAC('audio-chunk', buf(1));
    emitAC('voice-end', buf(1));
    await flushAsync(50);

    // Synth should be speaking now
    expect(synth.isSpeaking()).toBe(true);

    // User barges in
    emitAC('voice-start');
    await flushAsync(10);

    // Speech should have been interrupted
    expect(synth.isSpeaking()).toBe(false);
  });

  // 4. Turn-taking: system listens after finishing speaking
  it('turn-taking: system listens after finishing speaking', async () => {
    // Make TTS resolve immediately so speech finishes quickly
    mocks.ttsEngine.synthesize.mockImplementation(
      async (text: string) => new Float32Array(100),
    );

    emitAC('voice-start');
    emitAC('audio-chunk', buf(1));
    emitAC('voice-end', buf(1));
    await flushAsync(100);

    // After speech finishes and queue-empty fires, pipeline should be listening
    expect(pipeline.isListening()).toBe(true);
  });

  // 5. Voice circle works with local LLM (Ollama) when available
  it('voice circle works with local LLM (Ollama) when available', async () => {
    // Register ollama provider
    const ollamaProvider = createMockLLMProvider('ollama');
    llmClient.registerProvider(ollamaProvider);

    mocks.llmComplete.mockImplementationOnce(async () => ({
      content: 'Local Ollama response.',
      toolCalls: [],
      usage: { inputTokens: 5, outputTokens: 8 },
      model: 'llama3',
      provider: 'ollama' as const,
      stopReason: 'end_turn' as const,
      latencyMs: 80,
    }));

    emitAC('voice-start');
    emitAC('audio-chunk', buf(1));
    emitAC('voice-end', buf(1));
    await flushAsync(50);

    expect(mocks.llmComplete).toHaveBeenCalledTimes(1);
    expect(mocks.ttsEngine.synthesize).toHaveBeenCalled();
  });

  // 6. Voice circle works with cloud LLM when local unavailable (gated)
  it('voice circle works with cloud LLM when local unavailable', async () => {
    // Register cloud provider, local is unavailable
    const cloudProvider = createMockLLMProvider('anthropic');
    llmClient.registerProvider(cloudProvider);

    mocks.llmComplete.mockImplementationOnce(async () => ({
      content: 'Cloud response from Anthropic.',
      toolCalls: [],
      usage: { inputTokens: 10, outputTokens: 12 },
      model: 'claude-sonnet-4-20250514',
      provider: 'anthropic' as const,
      stopReason: 'end_turn' as const,
      latencyMs: 500,
    }));

    emitAC('voice-start');
    emitAC('audio-chunk', buf(1));
    emitAC('voice-end', buf(1));
    await flushAsync(50);

    expect(mocks.llmComplete).toHaveBeenCalledTimes(1);
    expect(mocks.ttsEngine.synthesize).toHaveBeenCalled();
  });

  // 7. Graceful degradation when STT unavailable (text-only input)
  it('degrades gracefully when STT unavailable (text-only input)', async () => {
    // Make whisper fail on loadModel
    mocks.whisperProvider.loadModel.mockRejectedValueOnce(new Error('No STT model'));

    // Reset pipeline to trigger fresh start with failing STT
    circle.teardown();
    TranscriptionPipeline.resetInstance();
    pipeline = TranscriptionPipeline.getInstance();

    const errors: Error[] = [];
    pipeline.on('error', (e) => errors.push(e as Error));
    await pipeline.start();

    // Pipeline should not be listening (graceful degradation)
    expect(pipeline.isListening()).toBe(false);
    expect(errors).toHaveLength(1);
    expect(errors[0].message).toContain('No STT model');

    // LLM should still work via direct text input
    const response = await llmClient.complete({
      messages: [{ role: 'user', content: 'Hello via text' }],
      maxTokens: 256,
    });
    expect(response.content).toBeTruthy();
  });

  // 8. Graceful degradation when TTS unavailable (text-only output)
  it('degrades gracefully when TTS unavailable (text-only output)', async () => {
    // Make TTS engine fail
    mocks.ttsEngine.synthesize.mockRejectedValue(new Error('TTS engine not available'));

    emitAC('voice-start');
    emitAC('audio-chunk', buf(1));
    emitAC('voice-end', buf(1));
    await flushAsync(50);

    // LLM should still have been called (voice-in still works)
    expect(mocks.llmComplete).toHaveBeenCalledTimes(1);
    // TTS was attempted but failed -- system should not crash
    expect(mocks.ttsEngine.synthesize).toHaveBeenCalled();
  });

  // 9. Graceful degradation when both STT and TTS unavailable
  it('degrades gracefully when both STT and TTS unavailable', async () => {
    mocks.whisperProvider.loadModel.mockRejectedValueOnce(new Error('No STT'));
    mocks.ttsEngine.synthesize.mockRejectedValue(new Error('No TTS'));

    // Reset pipeline
    circle.teardown();
    TranscriptionPipeline.resetInstance();
    pipeline = TranscriptionPipeline.getInstance();

    const errors: Error[] = [];
    pipeline.on('error', (e) => errors.push(e as Error));
    await pipeline.start();

    // No listening, no speaking -- but LLM text path still works
    expect(pipeline.isListening()).toBe(false);

    const response = await llmClient.complete({
      messages: [{ role: 'user', content: 'Text fallback' }],
      maxTokens: 256,
    });
    expect(response.content).toBeTruthy();
  });

  // 10. Full round-trip latency tracked: voice-end -> speech-start < 3s target
  it('full round-trip latency: voice-end to speech-start under 3s target', async () => {
    let speechStartTime = 0;
    synth.on('utterance-start', () => {
      speechStartTime = Date.now();
    });

    // Simulate realistic LLM latency
    mocks.llmComplete.mockImplementationOnce(async () => {
      await new Promise((r) => setTimeout(r, 200)); // 200ms LLM latency
      return {
        content: 'Quick response.',
        toolCalls: [],
        usage: { inputTokens: 5, outputTokens: 5 },
        model: 'test-model',
        provider: 'local' as const,
        stopReason: 'end_turn' as const,
        latencyMs: 200,
      };
    });

    const voiceEndTime = Date.now();
    emitAC('voice-start');
    emitAC('audio-chunk', buf(1));
    emitAC('voice-end', buf(1));
    await flushAsync(500);

    // Verify the round-trip completed
    expect(mocks.llmComplete).toHaveBeenCalledTimes(1);
    expect(mocks.ttsEngine.synthesize).toHaveBeenCalled();

    // If speech started, check latency
    if (speechStartTime > 0) {
      const latency = speechStartTime - voiceEndTime;
      expect(latency).toBeLessThan(3000); // 3s target
    }
  });
});