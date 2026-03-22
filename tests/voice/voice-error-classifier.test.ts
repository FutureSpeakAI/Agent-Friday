/**
 * VoiceErrorClassifier — Unit tests for error classification in the voice pipeline.
 *
 * Verifies that raw errors from WebSocket, getUserMedia, Ollama, and audio
 * subsystems are classified into actionable, user-facing ClassifiedError objects
 * with correct categories, recovery actions, and transience flags.
 */

import { describe, it, expect } from 'vitest';
import {
  classifyVoiceError,
  type ClassifiedError,
  type VoiceErrorCategory,
  type ClassificationContext,
} from '../../src/main/voice/voice-error-classifier';

// ── Helpers ────────────────────────────────────────────────────────────────

function assertClassifiedShape(result: ClassifiedError): void {
  expect(result).toHaveProperty('category');
  expect(result).toHaveProperty('userMessage');
  expect(result).toHaveProperty('recoveryAction');
  expect(result).toHaveProperty('technicalDetail');
  expect(result).toHaveProperty('isTransient');
  expect(typeof result.category).toBe('string');
  expect(typeof result.userMessage).toBe('string');
  expect(typeof result.recoveryAction).toBe('string');
  expect(typeof result.technicalDetail).toBe('string');
  expect(typeof result.isTransient).toBe('boolean');
  // userMessage should never be empty
  expect(result.userMessage.length).toBeGreaterThan(0);
  expect(result.recoveryAction.length).toBeGreaterThan(0);
}

// ── Tests ──────────────────────────────────────────────────────────────────

describe('VoiceErrorClassifier', () => {
  describe('ClassifiedError shape', () => {
    it('always returns a well-formed ClassifiedError object', () => {
      const result = classifyVoiceError(new Error('something broke'));
      assertClassifiedShape(result);
    });

    it('includes a recovery button when applicable', () => {
      const result = classifyVoiceError(new Error('invalid api key'));
      assertClassifiedShape(result);
      expect(result.recoveryButton).toBeDefined();
      expect(result.recoveryButton!.label).toBeTruthy();
      expect(result.recoveryButton!.action).toBeTruthy();
    });
  });

  describe('WebSocket close code classification', () => {
    it('code 1006 → network-unreachable (transient)', () => {
      const result = classifyVoiceError(
        new Error('Connection lost'),
        { wsCloseCode: 1006 },
      );
      expect(result.category).toBe('network-unreachable');
      expect(result.isTransient).toBe(true);
    });

    it('code 1008 → api-key-invalid when key exists', () => {
      const result = classifyVoiceError(
        new Error('Policy violation'),
        { wsCloseCode: 1008, hasGeminiKey: true },
      );
      expect(result.category).toBe('api-key-invalid');
      expect(result.isTransient).toBe(false);
      expect(result.recoveryButton?.action).toBe('open-settings');
    });

    it('code 1008 → api-key-missing when no key', () => {
      const result = classifyVoiceError(
        new Error('Policy violation'),
        { wsCloseCode: 1008, hasGeminiKey: false },
      );
      expect(result.category).toBe('api-key-missing');
      expect(result.isTransient).toBe(false);
    });

    it('code 1013 → gemini-rate-limit (transient)', () => {
      const result = classifyVoiceError(
        new Error('Try again later'),
        { wsCloseCode: 1013 },
      );
      expect(result.category).toBe('gemini-rate-limit');
      expect(result.isTransient).toBe(true);
    });

    it('code 1001 → gemini-server-error (transient)', () => {
      const result = classifyVoiceError(
        new Error('Going away'),
        { wsCloseCode: 1001 },
      );
      expect(result.category).toBe('gemini-server-error');
      expect(result.isTransient).toBe(true);
    });

    it('code 1011 → gemini-server-error', () => {
      const result = classifyVoiceError(
        new Error('Unexpected condition'),
        { wsCloseCode: 1011 },
      );
      expect(result.category).toBe('gemini-server-error');
    });

    it('code 1000 (normal) falls through to other classification', () => {
      const result = classifyVoiceError(
        new Error('Normal closure'),
        { wsCloseCode: 1000 },
      );
      // 1000 is not an error — should fall through to message-based classification
      expect(result.category).not.toBe('network-unreachable');
    });

    it('code 4xxx with auth message → api-key-invalid', () => {
      const result = classifyVoiceError(
        new Error('Authentication required'),
        { wsCloseCode: 4001 },
      );
      expect(result.category).toBe('api-key-invalid');
    });

    it('code 1014/1015 → network-unreachable', () => {
      const r1 = classifyVoiceError(new Error('TLS error'), { wsCloseCode: 1014 });
      const r2 = classifyVoiceError(new Error('TLS error'), { wsCloseCode: 1015 });
      expect(r1.category).toBe('network-unreachable');
      expect(r2.category).toBe('network-unreachable');
    });
  });

  describe('DOMException (getUserMedia) classification', () => {
    it('NotAllowedError → mic-denied', () => {
      const err = new Error('Permission denied');
      err.name = 'NotAllowedError';
      const result = classifyVoiceError(err);
      expect(result.category).toBe('mic-denied');
      expect(result.isTransient).toBe(false);
      expect(result.recoveryButton?.action).toBe('open-system-prefs');
    });

    it('PermissionDeniedError → mic-denied', () => {
      const err = new Error('Permission denied');
      err.name = 'PermissionDeniedError';
      const result = classifyVoiceError(err);
      expect(result.category).toBe('mic-denied');
    });

    it('NotFoundError → mic-unavailable', () => {
      const err = new Error('No device found');
      err.name = 'NotFoundError';
      const result = classifyVoiceError(err);
      expect(result.category).toBe('mic-unavailable');
      expect(result.isTransient).toBe(false);
    });

    it('NotReadableError → mic-unavailable', () => {
      const err = new Error('Could not start source');
      err.name = 'NotReadableError';
      const result = classifyVoiceError(err);
      expect(result.category).toBe('mic-unavailable');
    });

    it('OverconstrainedError → mic-unavailable', () => {
      const err = new Error('Constraints not satisfiable');
      err.name = 'OverconstrainedError';
      const result = classifyVoiceError(err);
      expect(result.category).toBe('mic-unavailable');
    });
  });

  describe('HTTP status code classification', () => {
    it('401 → api-key-invalid when key exists', () => {
      const result = classifyVoiceError(
        new Error('Unauthorized'),
        { httpStatus: 401, hasGeminiKey: true },
      );
      expect(result.category).toBe('api-key-invalid');
      expect(result.isTransient).toBe(false);
    });

    it('401 → api-key-missing when no key', () => {
      const result = classifyVoiceError(
        new Error('Unauthorized'),
        { httpStatus: 401, hasGeminiKey: false },
      );
      expect(result.category).toBe('api-key-missing');
    });

    it('403 → api-key-invalid', () => {
      const result = classifyVoiceError(
        new Error('Forbidden'),
        { httpStatus: 403, hasGeminiKey: true },
      );
      expect(result.category).toBe('api-key-invalid');
    });

    it('429 → gemini-rate-limit (transient)', () => {
      const result = classifyVoiceError(
        new Error('Rate limited'),
        { httpStatus: 429 },
      );
      expect(result.category).toBe('gemini-rate-limit');
      expect(result.isTransient).toBe(true);
    });

    it('500 → gemini-server-error (transient)', () => {
      const result = classifyVoiceError(
        new Error('Internal server error'),
        { httpStatus: 500 },
      );
      expect(result.category).toBe('gemini-server-error');
      expect(result.isTransient).toBe(true);
    });

    it('503 → gemini-server-error (transient)', () => {
      const result = classifyVoiceError(
        new Error('Service unavailable'),
        { httpStatus: 503 },
      );
      expect(result.category).toBe('gemini-server-error');
      expect(result.isTransient).toBe(true);
    });
  });

  describe('Ollama error classification', () => {
    it('ECONNREFUSED on local path → ollama-unreachable', () => {
      const result = classifyVoiceError(
        new Error('connect ECONNREFUSED 127.0.0.1:11434'),
        { voicePath: 'local' },
      );
      expect(result.category).toBe('ollama-unreachable');
      expect(result.isTransient).toBe(false);
    });

    it('"ollama" + "model not found" → model-not-downloaded', () => {
      const result = classifyVoiceError(
        new Error('Ollama error: model "llama3.1:8b" not found, pull it first'),
      );
      expect(result.category).toBe('model-not-downloaded');
      expect(result.isTransient).toBe(false);
      expect(result.recoveryButton?.action).toBe('pull-model');
    });

    it('"ollama" + "not running" → ollama-unreachable', () => {
      const result = classifyVoiceError(
        new Error('Ollama is not running'),
      );
      expect(result.category).toBe('ollama-unreachable');
    });

    it('context.ollamaHealthy=false on local path → ollama-unreachable', () => {
      const result = classifyVoiceError(
        new Error('Something went wrong'),
        { voicePath: 'local', ollamaHealthy: false },
      );
      expect(result.category).toBe('ollama-unreachable');
    });
  });

  describe('Whisper/TTS/AudioContext classification', () => {
    it('"whisper" in message → whisper-load-failed', () => {
      const result = classifyVoiceError(new Error('Whisper model failed to load'));
      expect(result.category).toBe('whisper-load-failed');
      expect(result.isTransient).toBe(false);
    });

    it('"transcription" in message → whisper-load-failed', () => {
      const result = classifyVoiceError(new Error('Transcription engine crashed'));
      expect(result.category).toBe('whisper-load-failed');
    });

    it('"kokoro" in message → tts-load-failed', () => {
      const result = classifyVoiceError(new Error('Kokoro model corrupted'));
      expect(result.category).toBe('tts-load-failed');
      expect(result.recoveryButton?.action).toBe('switch-to-text');
    });

    it('"piper" in message → tts-load-failed', () => {
      const result = classifyVoiceError(new Error('Piper engine not found'));
      expect(result.category).toBe('tts-load-failed');
    });

    it('"AudioContext" in message → audio-context-dead', () => {
      const result = classifyVoiceError(new Error('The AudioContext was not allowed to start'));
      expect(result.category).toBe('audio-context-dead');
      expect(result.isTransient).toBe(true);
    });
  });

  describe('Network error classification', () => {
    it('ENOTFOUND → network-unreachable (transient)', () => {
      const result = classifyVoiceError(new Error('getaddrinfo ENOTFOUND generativelanguage.googleapis.com'));
      expect(result.category).toBe('network-unreachable');
      expect(result.isTransient).toBe(true);
    });

    it('ETIMEDOUT → network-timeout (transient)', () => {
      const result = classifyVoiceError(new Error('connect ETIMEDOUT 142.250.80.42:443'));
      expect(result.category).toBe('network-timeout');
      expect(result.isTransient).toBe(true);
    });

    it('"fetch failed" → network-unreachable', () => {
      const result = classifyVoiceError(new Error('fetch failed'));
      expect(result.category).toBe('network-unreachable');
    });

    it('"socket hang up" → network-unreachable', () => {
      const result = classifyVoiceError(new Error('socket hang up'));
      expect(result.category).toBe('network-unreachable');
    });
  });

  describe('API key pattern classification', () => {
    it('"api key" in message → api-key-invalid', () => {
      const result = classifyVoiceError(new Error('Invalid API key provided'));
      expect(result.category).toBe('api-key-invalid');
    });

    it('"api_key" in message + hasGeminiKey=false → api-key-missing', () => {
      const result = classifyVoiceError(
        new Error('No api_key provided'),
        { hasGeminiKey: false },
      );
      expect(result.category).toBe('api-key-missing');
    });
  });

  describe('Context-based inference', () => {
    it('no key + cloud path → api-key-missing', () => {
      const result = classifyVoiceError(
        new Error('Connection failed'),
        { hasGeminiKey: false, voicePath: 'cloud' },
      );
      expect(result.category).toBe('api-key-missing');
    });

    it('unhealthy ollama + local path → ollama-unreachable', () => {
      const result = classifyVoiceError(
        new Error('Some vague error'),
        { ollamaHealthy: false, voicePath: 'local' },
      );
      expect(result.category).toBe('ollama-unreachable');
    });
  });

  describe('Edge cases', () => {
    it('null input → unknown category', () => {
      const result = classifyVoiceError(null);
      assertClassifiedShape(result);
      expect(result.category).toBe('unknown');
    });

    it('undefined input → unknown category', () => {
      const result = classifyVoiceError(undefined);
      assertClassifiedShape(result);
      expect(result.category).toBe('unknown');
    });

    it('string input is normalized to Error', () => {
      const result = classifyVoiceError('Whisper model failed');
      assertClassifiedShape(result);
      expect(result.category).toBe('whisper-load-failed');
    });

    it('plain object with message property is normalized', () => {
      const result = classifyVoiceError({ message: 'Ollama model not found', name: 'Error' });
      assertClassifiedShape(result);
      expect(result.category).toBe('model-not-downloaded');
    });

    it('empty error message → unknown', () => {
      const result = classifyVoiceError(new Error(''));
      assertClassifiedShape(result);
      expect(result.category).toBe('unknown');
    });

    it('number input → unknown', () => {
      const result = classifyVoiceError(42);
      assertClassifiedShape(result);
    });

    it('unknown category is transient (optimistic)', () => {
      const result = classifyVoiceError(new Error('completely novel failure'));
      expect(result.category).toBe('unknown');
      expect(result.isTransient).toBe(true);
    });

    it('unknown category includes context in technicalDetail', () => {
      const ctx: ClassificationContext = {
        voiceState: 'CLOUD_ACTIVE',
        voicePath: 'cloud',
        hasGeminiKey: true,
      };
      const result = classifyVoiceError(new Error('novel error'), ctx);
      expect(result.technicalDetail).toContain('state=CLOUD_ACTIVE');
      expect(result.technicalDetail).toContain('path=cloud');
    });
  });

  describe('Transience flags', () => {
    const transientCategories: VoiceErrorCategory[] = [
      'network-unreachable',
      'network-timeout',
      'gemini-rate-limit',
      'gemini-server-error',
      'audio-context-dead',
      'unknown',
    ];

    const persistentCategories: VoiceErrorCategory[] = [
      'api-key-invalid',
      'api-key-missing',
      'mic-denied',
      'mic-unavailable',
      'model-not-downloaded',
      'ollama-unreachable',
      'whisper-load-failed',
      'tts-load-failed',
    ];

    for (const cat of transientCategories) {
      it(`${cat} is transient`, () => {
        // Create an error that will trigger each category
        const errorMap: Record<string, [unknown, ClassificationContext?]> = {
          'network-unreachable': [new Error('ENOTFOUND')],
          'network-timeout': [new Error('ETIMEDOUT')],
          'gemini-rate-limit': [new Error('rate limited'), { httpStatus: 429 }],
          'gemini-server-error': [new Error('server error'), { httpStatus: 500 }],
          'audio-context-dead': [new Error('AudioContext was not allowed')],
          'unknown': [new Error('completely unknown failure')],
        };

        const [err, ctx] = errorMap[cat] ?? [new Error(cat)];
        const result = classifyVoiceError(err, ctx);
        expect(result.category).toBe(cat);
        expect(result.isTransient).toBe(true);
      });
    }

    for (const cat of persistentCategories) {
      it(`${cat} is persistent`, () => {
        const errorMap: Record<string, [unknown, ClassificationContext?]> = {
          'api-key-invalid': [new Error('Invalid API key')],
          'api-key-missing': [new Error('api key missing'), { hasGeminiKey: false }],
          'mic-denied': [Object.assign(new Error('denied'), { name: 'NotAllowedError' })],
          'mic-unavailable': [Object.assign(new Error('not found'), { name: 'NotFoundError' })],
          'model-not-downloaded': [new Error('Ollama model not found')],
          'ollama-unreachable': [new Error('Ollama is not running')],
          'whisper-load-failed': [new Error('Whisper model failed')],
          'tts-load-failed': [new Error('Kokoro engine crashed')],
        };

        const [err, ctx] = errorMap[cat] ?? [new Error(cat)];
        const result = classifyVoiceError(err, ctx);
        expect(result.category).toBe(cat);
        expect(result.isTransient).toBe(false);
      });
    }
  });
});
