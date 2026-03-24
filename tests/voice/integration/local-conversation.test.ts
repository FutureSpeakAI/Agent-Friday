/**
 * LocalConversation — Unit tests for the main-process local conversation loop.
 *
 * Tests initialization, pending input queue, event emissions, voice/TTS
 * degradation, tool execution, stop/cleanup, and Ollama health checks.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

// ── Mocks (hoisted before imports) ─────────────────────────────────────────

/**
 * Helper: convert a { content, toolCalls } response into an async generator
 * that mimics the llmClient.stream() behavior (text chunks + final done chunk).
 */
function mockStreamFromResponse(response: { content: string | null; toolCalls: any[] }) {
  return async function* () {
    const text = response.content || '';
    if (text) {
      // Emit text as a single chunk (tests don't need per-token granularity)
      yield { text, done: false };
    }
    // Emit tool calls if any
    if (response.toolCalls?.length) {
      for (const tc of response.toolCalls) {
        yield { toolCall: tc, done: false };
      }
    }
    // Final chunk with assembled fullResponse
    yield {
      done: true,
      fullResponse: {
        content: text,
        toolCalls: response.toolCalls || [],
        usage: { inputTokens: 0, outputTokens: 0 },
        model: 'test-model',
        provider: 'ollama',
        stopReason: response.toolCalls?.length ? 'tool_use' : 'end_turn',
        latencyMs: 10,
      },
    };
  };
}

const mocks = vi.hoisted(() => ({
  // LLM — stream() is the primary API used by LocalConversation
  llmStream: vi.fn(),
  getProvider: vi.fn(),

  // Whisper
  whisperIsReady: vi.fn().mockReturnValue(false),
  whisperLoadModel: vi.fn().mockResolvedValue(undefined),

  // TTS
  ttsIsReady: vi.fn().mockReturnValue(false),
  ttsLoadEngine: vi.fn().mockResolvedValue(undefined),

  // Speech Synthesis
  speechSpeak: vi.fn().mockResolvedValue(undefined),
  speechStop: vi.fn(),
  speechIsSpeaking: vi.fn().mockReturnValue(false),

  // Transcription Pipeline
  pipelineStart: vi.fn().mockResolvedValue(undefined),
  pipelineStop: vi.fn(),
  pipelineOn: vi.fn().mockReturnValue(() => {}),

  // Other dependencies
  generatePsychologicalProfile: vi.fn().mockResolvedValue({}),
  settingsManager: {
    setSetting: vi.fn().mockResolvedValue(undefined),
    saveAgentConfig: vi.fn().mockResolvedValue(undefined),
  },
  initializeFeatureSetup: vi.fn().mockReturnValue({}),
  advanceFeatureStep: vi.fn().mockResolvedValue(null),
  ensureProfileOnDisk: vi.fn().mockResolvedValue(undefined),
  callDesktopTool: vi.fn().mockRejectedValue(new Error('Not found')),
  mcpClient: {
    isConnected: vi.fn().mockReturnValue(false),
    callTool: vi.fn(),
  },
  calendarAuthenticate: vi.fn().mockResolvedValue(false),
}));

vi.mock('../../../src/main/llm-client', () => ({
  llmClient: {
    stream: mocks.llmStream,
    getProvider: mocks.getProvider,
  },
}));

vi.mock('../../../src/main/voice/transcription-pipeline', () => ({
  transcriptionPipeline: {
    start: mocks.pipelineStart,
    stop: mocks.pipelineStop,
    on: mocks.pipelineOn,
  },
}));

vi.mock('../../../src/main/voice/whisper-provider', () => ({
  whisperProvider: {
    isReady: mocks.whisperIsReady,
    loadModel: mocks.whisperLoadModel,
  },
}));

vi.mock('../../../src/main/voice/tts-engine', () => ({
  ttsEngine: {
    isReady: mocks.ttsIsReady,
    loadEngine: mocks.ttsLoadEngine,
  },
}));

vi.mock('../../../src/main/voice/speech-synthesis', () => ({
  speechSynthesis: {
    speak: mocks.speechSpeak,
    stop: mocks.speechStop,
    isSpeaking: mocks.speechIsSpeaking,
  },
}));

vi.mock('../../../src/main/psychological-profile', () => ({
  generatePsychologicalProfile: mocks.generatePsychologicalProfile,
}));

vi.mock('../../../src/main/settings', () => ({
  settingsManager: mocks.settingsManager,
}));

vi.mock('../../../src/main/feature-setup', () => ({
  initializeFeatureSetup: mocks.initializeFeatureSetup,
  advanceFeatureStep: mocks.advanceFeatureStep,
}));

vi.mock('../../../src/main/friday-profile', () => ({
  ensureProfileOnDisk: mocks.ensureProfileOnDisk,
}));

vi.mock('../../../src/main/desktop-tools', () => ({
  callDesktopTool: mocks.callDesktopTool,
}));

vi.mock('../../../src/main/mcp-client', () => ({
  mcpClient: mocks.mcpClient,
}));

vi.mock('../../../src/main/calendar', () => ({
  calendarIntegration: {
    authenticate: mocks.calendarAuthenticate,
  },
}));

import { LocalConversation } from '../../../../../src/main/local-conversation';

// ── Helpers ────────────────────────────────────────────────────────────────

function mockOllamaHealthy(): void {
  mocks.getProvider.mockReturnValue({
    checkHealth: vi.fn().mockResolvedValue(true),
  });
}

function mockOllamaUnhealthy(): void {
  mocks.getProvider.mockReturnValue({
    checkHealth: vi.fn().mockResolvedValue(false),
  });
}

function mockOllamaNotRegistered(): void {
  mocks.getProvider.mockReturnValue(null);
}

// ── Tests ──────────────────────────────────────────────────────────────────

describe('LocalConversation', () => {
  let conv: LocalConversation;

  beforeEach(() => {
    vi.clearAllMocks();
    conv = new LocalConversation();
    // Prevent Node.js EventEmitter from throwing on unhandled 'error' events.
    // LocalConversation emits 'error' when Whisper/TTS degrade gracefully,
    // which is expected in text-only mode tests. Without this, the emit throws.
    conv.on('error', () => {});
    mockOllamaHealthy();
    // Default: no whisper, no TTS — text-only mode
    mocks.whisperIsReady.mockReturnValue(false);
    mocks.whisperLoadModel.mockRejectedValue(new Error('No Whisper model'));
    mocks.ttsIsReady.mockReturnValue(false);
    mocks.ttsLoadEngine.mockRejectedValue(new Error('No TTS model'));
    mocks.llmStream.mockImplementation(mockStreamFromResponse({ content: 'Hello from AI', toolCalls: [] }));
  });

  afterEach(() => {
    conv.stop();
    vi.restoreAllMocks();
  });

  describe('Constructor / Initial State', () => {
    it('isActive returns false before start', () => {
      expect(conv.isActive()).toBe(false);
    });
  });

  describe('start()', () => {
    it('throws when Ollama provider is not registered', async () => {
      mockOllamaNotRegistered();
      await expect(conv.start('System prompt', [])).rejects.toThrow(/Ollama provider not registered/);
      expect(conv.isActive()).toBe(false);
    });

    it('throws when Ollama is not healthy', async () => {
      mockOllamaUnhealthy();
      await expect(conv.start('System prompt', [])).rejects.toThrow(/Ollama is not running/);
    });

    it('starts successfully in text-only mode (no Whisper, no TTS)', async () => {
      await conv.start('System prompt', []);
      expect(conv.isActive()).toBe(true);
    });

    it('emits "started" event', async () => {
      const handler = vi.fn();
      conv.on('started', handler);
      await conv.start('System prompt', []);
      expect(handler).toHaveBeenCalledOnce();
    });

    it('ignores second start() call while active', async () => {
      await conv.start('System prompt', []);
      // Second call should not throw
      await conv.start('Another prompt', []);
      expect(conv.isActive()).toBe(true);
    });

    it('starts with Whisper when model is ready', async () => {
      mocks.whisperIsReady.mockReturnValue(true);
      await conv.start('System prompt', []);
      expect(conv.isActive()).toBe(true);
      // Pipeline should have been started for voice mode
      expect(mocks.pipelineStart).toHaveBeenCalled();
    });

    it('degrades gracefully when Whisper load fails', async () => {
      mocks.whisperIsReady.mockReturnValue(false);
      mocks.whisperLoadModel.mockRejectedValue(new Error('Model missing'));
      const errorHandler = vi.fn();
      conv.on('error', errorHandler);
      await conv.start('System prompt', []);
      // Should still be active (text-only mode)
      expect(conv.isActive()).toBe(true);
      // Should have emitted an error about Whisper
      expect(errorHandler).toHaveBeenCalledWith(
        expect.stringContaining('Whisper'),
      );
    });

    it('degrades gracefully when TTS load fails', async () => {
      mocks.ttsIsReady.mockReturnValue(false);
      mocks.ttsLoadEngine.mockRejectedValue(new Error('TTS missing'));
      await conv.start('System prompt', []);
      // Should still be active
      expect(conv.isActive()).toBe(true);
    });

    it('sends initial prompt when provided', async () => {
      await conv.start('System prompt', [], 'Hello!');
      // Wait for async processUserInput to complete
      await vi.waitFor(() => {
        expect(mocks.llmStream).toHaveBeenCalled();
      });
      const call = mocks.llmStream.mock.calls[0][0];
      expect(call.messages).toEqual(
        expect.arrayContaining([
          expect.objectContaining({ role: 'user', content: 'Hello!' }),
        ]),
      );
    });
  });

  describe('sendText()', () => {
    it('sends text to Ollama and emits ai-response (but not user-transcript)', async () => {
      await conv.start('System prompt', []);
      const transcriptHandler = vi.fn();
      const responseHandler = vi.fn();
      conv.on('user-transcript', transcriptHandler);
      conv.on('ai-response', responseHandler);

      await conv.sendText('What is the weather?');

      // sendText passes skipTranscriptEmit: true — renderer already added the user message
      expect(transcriptHandler).not.toHaveBeenCalled();
      expect(responseHandler).toHaveBeenCalledWith('Hello from AI');
    });

    it('ignores sendText when not active', async () => {
      await conv.sendText('Ignored text');
      expect(mocks.llmStream).not.toHaveBeenCalled();
    });

    it('emits error on LLM failure', async () => {
      await conv.start('System prompt', []);
      mocks.llmStream.mockImplementationOnce(async function* () {
        throw new Error('Ollama crashed');
      });
      const errorHandler = vi.fn();
      conv.on('error', errorHandler);
      await conv.sendText('Trigger error');
      expect(errorHandler).toHaveBeenCalledWith(expect.stringContaining('LLM error'));
    });

    it('emits fallback message on empty LLM response', async () => {
      await conv.start('System prompt', []);
      mocks.llmStream.mockImplementationOnce(mockStreamFromResponse({ content: '', toolCalls: [] }));
      const responseHandler = vi.fn();
      conv.on('ai-response', responseHandler);
      await conv.sendText('Empty response trigger');
      expect(responseHandler).toHaveBeenCalledWith(
        expect.stringContaining('No response'),
      );
    });
  });

  describe('Pending inputs queue', () => {
    it('queues input that arrives during processing', async () => {
      await conv.start('System prompt', []);

      // Make the first call slow using an async generator that waits on a promise
      let resolveFirst!: () => void;
      const gate = new Promise<void>((resolve) => { resolveFirst = resolve; });
      mocks.llmStream
        .mockImplementationOnce(async function* () {
          await gate;
          yield { text: 'First response', done: false };
          yield { done: true, fullResponse: { content: 'First response', toolCalls: [], usage: { inputTokens: 0, outputTokens: 0 }, model: 'test', provider: 'ollama', stopReason: 'end_turn', latencyMs: 10 } };
        })
        .mockImplementation(mockStreamFromResponse({ content: 'Second response', toolCalls: [] }));

      const responseHandler = vi.fn();
      conv.on('ai-response', responseHandler);

      // Start first input (will block on gate)
      const firstPromise = conv.sendText('First input');

      // Send second input while first is processing — should be queued
      await conv.sendText('Second input');

      // Resolve first call
      resolveFirst();
      await firstPromise;

      // Wait for both responses (not just llmStream calls — stream consumption is async)
      await vi.waitFor(() => {
        expect(responseHandler).toHaveBeenCalledTimes(2);
      });
    });

    it('queue is drained sequentially, not dropped', async () => {
      await conv.start('System prompt', []);

      let resolveFirst!: () => void;
      const gate = new Promise<void>((resolve) => { resolveFirst = resolve; });
      mocks.llmStream
        .mockImplementationOnce(async function* () {
          await gate;
          yield { text: 'R1', done: false };
          yield { done: true, fullResponse: { content: 'R1', toolCalls: [], usage: { inputTokens: 0, outputTokens: 0 }, model: 'test', provider: 'ollama', stopReason: 'end_turn', latencyMs: 10 } };
        })
        .mockImplementationOnce(mockStreamFromResponse({ content: 'R2', toolCalls: [] }))
        .mockImplementationOnce(mockStreamFromResponse({ content: 'R3', toolCalls: [] }));

      const transcriptHandler = vi.fn();
      const responseHandler = vi.fn();
      conv.on('user-transcript', transcriptHandler);
      conv.on('ai-response', responseHandler);

      const p1 = conv.sendText('A');
      await conv.sendText('B');
      await conv.sendText('C');

      resolveFirst();
      await p1;

      // Wait for all three responses to complete (stream consumption is async)
      await vi.waitFor(() => {
        expect(responseHandler).toHaveBeenCalledTimes(3);
      });

      // sendText passes skipTranscriptEmit — only queued-then-drained inputs emit transcripts
      expect(transcriptHandler).toHaveBeenCalledTimes(2);
      expect(transcriptHandler).toHaveBeenNthCalledWith(1, 'B');
      expect(transcriptHandler).toHaveBeenNthCalledWith(2, 'C');
    });
  });

  describe('Tool calling', () => {
    it('handles tool calls in a loop', async () => {
      await conv.start('System prompt', [
        { name: 'acknowledge_introduction', description: 'Ack', parameters: {} },
      ]);

      // First stream returns a tool call
      mocks.llmStream
        .mockImplementationOnce(mockStreamFromResponse({
          content: '',
          toolCalls: [{
            id: 'tc-1',
            name: 'acknowledge_introduction',
            input: { user_response: 'OK' },
          }],
        }))
        // Second stream (after tool result) returns final response
        .mockImplementationOnce(mockStreamFromResponse({
          content: 'Great, let us continue!',
          toolCalls: [],
        }));

      const responseHandler = vi.fn();
      conv.on('ai-response', responseHandler);

      await conv.sendText('I understand');

      // Should have called stream twice (initial + after tool result)
      expect(mocks.llmStream).toHaveBeenCalledTimes(2);
      expect(responseHandler).toHaveBeenCalledWith('Great, let us continue!');
    });

    it('limits tool iterations to prevent infinite loops', async () => {
      await conv.start('System prompt', []);

      // Always return a tool call — should be capped at 5 iterations
      mocks.llmStream.mockImplementation(mockStreamFromResponse({
        content: '',
        toolCalls: [{
          id: 'tc-loop',
          name: 'unknown_tool',
          input: {},
        }],
      }));

      await conv.sendText('Infinite loop trigger');

      // Should cap at MAX_TOOL_ITERATIONS (5) + 1 initial = 6 total calls
      expect(mocks.llmStream).toHaveBeenCalledTimes(6);
    });
  });

  describe('TTS integration', () => {
    it('speaks response when TTS is available', async () => {
      mocks.ttsIsReady.mockReturnValue(true);
      mocks.ttsLoadEngine.mockResolvedValue(undefined);
      await conv.start('System prompt', []);
      await conv.sendText('Speak to me');
      expect(mocks.speechSpeak).toHaveBeenCalledWith('Hello from AI');
    });

    it('skips TTS when not available', async () => {
      // Default: TTS load fails
      await conv.start('System prompt', []);
      await conv.sendText('No TTS');
      expect(mocks.speechSpeak).not.toHaveBeenCalled();
    });

    it('TTS failure is non-fatal', async () => {
      mocks.ttsIsReady.mockReturnValue(true);
      mocks.ttsLoadEngine.mockResolvedValue(undefined);
      mocks.speechSpeak.mockRejectedValueOnce(new Error('TTS crashed'));
      await conv.start('System prompt', []);

      const errorHandler = vi.fn();
      conv.on('error', errorHandler);

      // Should not throw — TTS errors are caught
      await conv.sendText('Trigger TTS error');
      // The AI response should still be emitted
      expect(conv.isActive()).toBe(true);
    });
  });

  describe('stop()', () => {
    it('sets active to false', async () => {
      await conv.start('System prompt', []);
      expect(conv.isActive()).toBe(true);
      conv.stop();
      expect(conv.isActive()).toBe(false);
    });

    it('stops transcription pipeline when voice was active', async () => {
      mocks.whisperIsReady.mockReturnValue(true);
      await conv.start('System prompt', []);
      conv.stop();
      expect(mocks.pipelineStop).toHaveBeenCalled();
    });

    it('stops speech synthesis when TTS was active', async () => {
      mocks.ttsIsReady.mockReturnValue(true);
      mocks.ttsLoadEngine.mockResolvedValue(undefined);
      await conv.start('System prompt', []);
      conv.stop();
      expect(mocks.speechStop).toHaveBeenCalled();
    });

    it('clears pending inputs', async () => {
      await conv.start('System prompt', []);

      // Set up slow processing
      let resolveFirst!: () => void;
      const gate = new Promise<void>((resolve) => { resolveFirst = resolve; });
      mocks.llmStream.mockImplementationOnce(async function* () {
        await gate;
        yield { text: 'Done', done: false };
        yield { done: true, fullResponse: { content: 'Done', toolCalls: [], usage: { inputTokens: 0, outputTokens: 0 }, model: 'test', provider: 'ollama', stopReason: 'end_turn', latencyMs: 10 } };
      });

      const p = conv.sendText('Processing');
      await conv.sendText('Queued');

      conv.stop();
      resolveFirst();
      await p;

      // After stop, no further processing should happen
      expect(conv.isActive()).toBe(false);
    });

    it('stop is idempotent (safe to call twice)', async () => {
      await conv.start('System prompt', []);
      conv.stop();
      expect(() => conv.stop()).not.toThrow();
    });

    it('stop is safe when not started', () => {
      expect(() => conv.stop()).not.toThrow();
    });
  });

  describe('Onboarding tool execution', () => {
    it('acknowledge_introduction returns ack string', async () => {
      await conv.start('System prompt', []);

      mocks.llmStream
        .mockImplementationOnce(mockStreamFromResponse({
          content: '',
          toolCalls: [{
            id: 'tc-ack',
            name: 'acknowledge_introduction',
            input: { user_response: 'Got it' },
          }],
        }))
        .mockImplementationOnce(mockStreamFromResponse({ content: 'Continuing', toolCalls: [] }));

      await conv.sendText('I understand');
      expect(mocks.llmStream).toHaveBeenCalledTimes(2);

      // Check that the tool result was added to messages
      const secondCall = mocks.llmStream.mock.calls[1][0];
      const toolMessage = secondCall.messages.find(
        (m: any) => m.role === 'tool' && m.name === 'acknowledge_introduction',
      );
      expect(toolMessage).toBeDefined();
      expect(toolMessage.content).toContain('Trust introduction acknowledged');
    });

    it('finalize_agent_identity emits agent-finalized', async () => {
      await conv.start('System prompt', []);
      const handler = vi.fn();
      conv.on('agent-finalized', handler);

      mocks.llmStream
        .mockImplementationOnce(mockStreamFromResponse({
          content: '',
          toolCalls: [{
            id: 'tc-fin',
            name: 'finalize_agent_identity',
            input: {
              agent_name: 'Friday',
              voice_name: 'Kore',
              gender: 'female',
              user_name: 'Alex',
            },
          }],
        }))
        .mockImplementationOnce(mockStreamFromResponse({ content: 'All done!', toolCalls: [] }));

      await conv.sendText('Finalize please');

      expect(handler).toHaveBeenCalledWith(
        expect.objectContaining({
          agentName: 'Friday',
          userName: 'Alex',
          onboardingComplete: true,
        }),
      );
      expect(mocks.settingsManager.saveAgentConfig).toHaveBeenCalled();
      expect(mocks.ensureProfileOnDisk).toHaveBeenCalled();
    });
  });
});
