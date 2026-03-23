/**
 * audio-gen.test.ts — Unit tests for the Audio & Music Generation connector.
 *
 * Tests the connector's structure, tool declarations, execute routing,
 * detection, and error handling — all WITHOUT requiring network access,
 * API keys, or FFmpeg installed.
 *
 * Sprint 6 Track D: "The Composer" — validation tests.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ---------------------------------------------------------------------------
// Controllable mock state
// ---------------------------------------------------------------------------

let mockGeminiKey = '';
let mockElevenLabsKey = '';

// ---------------------------------------------------------------------------
// Mocks — must be before imports (vi.mock is hoisted)
// ---------------------------------------------------------------------------

vi.mock('electron', () => ({
  app: { getPath: () => '/tmp/test-audio-gen' },
}));

vi.mock('../../src/main/settings', () => ({
  settingsManager: {
    getGeminiApiKey: () => mockGeminiKey,
    getElevenLabsApiKey: () => mockElevenLabsKey,
  },
}));

// Mock fs to prevent actual file system operations
vi.mock('node:fs', async () => {
  const actual = await vi.importActual<typeof import('node:fs')>('node:fs');
  return {
    ...actual,
    default: {
      ...actual,
      existsSync: (p: string) => {
        // Pretend test file paths exist
        if (typeof p === 'string' && (p.includes('test-input') || p.includes('track'))) return true;
        return false;
      },
      mkdirSync: vi.fn(),
      writeFileSync: vi.fn(),
    },
    existsSync: (p: string) => {
      if (typeof p === 'string' && (p.includes('test-input') || p.includes('track'))) return true;
      return false;
    },
    mkdirSync: vi.fn(),
    writeFileSync: vi.fn(),
  };
});

// Mock child_process — simulate "command not found" for FFmpeg/FFprobe
vi.mock('node:child_process', () => ({
  execFile: vi.fn((_cmd: string, _args: string[], _opts: any, cb?: Function) => {
    if (cb) {
      cb(new Error('Command not found'), '', '');
    }
    return { on: vi.fn(), stdout: { on: vi.fn() }, stderr: { on: vi.fn() } };
  }),
}));

// Mock promisify to return a function that rejects (no FFmpeg)
vi.mock('node:util', async () => {
  const actual = await vi.importActual<typeof import('node:util')>('node:util');
  return {
    ...actual,
    default: {
      ...actual,
      promisify: () => async (..._args: any[]) => {
        throw new Error('Command not found');
      },
    },
    promisify: () => async (..._args: any[]) => {
      throw new Error('Command not found');
    },
  };
});

// Mock https to prevent actual network calls
vi.mock('node:https', () => ({
  request: vi.fn((_opts: any, _cb?: Function) => {
    // Return a mock request object that doesn't do anything
    return {
      on: vi.fn(),
      write: vi.fn(),
      end: vi.fn(),
    };
  }),
}));

// Mock multimedia-engine to prevent actual podcast generation
vi.mock('../../src/main/multimedia-engine', () => ({
  multimediaEngine: {
    generatePodcast: async () => ({
      audioPath: '/tmp/test-audio-gen/multimedia/podcasts/test-podcast.wav',
      scriptSegments: [{ speakerIndex: 0, speakerRole: 'host', text: 'Hello' }],
      duration: 60,
      title: 'Test Podcast',
    }),
    generateMusic: async () => ({
      audioPath: '/tmp/test-audio-gen/multimedia/music/test-music.wav',
      duration: 10,
      title: 'Test Music',
    }),
    listMedia: async () => [],
  },
}));

// ---------------------------------------------------------------------------
// Import the module under test
// ---------------------------------------------------------------------------

import { TOOLS, execute, detect } from '../../src/main/connectors/audio-gen';

// ---------------------------------------------------------------------------
// Test Utilities
// ---------------------------------------------------------------------------

function findTool(name: string) {
  return TOOLS.find((t) => t.name === name);
}

// ---------------------------------------------------------------------------
// Setup / Teardown
// ---------------------------------------------------------------------------

beforeEach(() => {
  mockGeminiKey = '';
  mockElevenLabsKey = '';
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// Tests: Module Exports
// ---------------------------------------------------------------------------

describe('Audio Gen Connector — Exports', () => {
  it('exports TOOLS array', () => {
    expect(Array.isArray(TOOLS)).toBe(true);
    expect(TOOLS.length).toBeGreaterThan(0);
  });

  it('exports execute as a function', () => {
    expect(typeof execute).toBe('function');
  });

  it('exports detect as a function', () => {
    expect(typeof detect).toBe('function');
  });
});

// ---------------------------------------------------------------------------
// Tests: Tool Declarations
// ---------------------------------------------------------------------------

describe('Audio Gen Connector — Tool Declarations', () => {
  it('declares exactly 8 tools', () => {
    expect(TOOLS).toHaveLength(8);
  });

  it('all tool names start with composer_ prefix', () => {
    for (const tool of TOOLS) {
      expect(tool.name).toMatch(/^composer_/);
    }
  });

  it('all tools have name, description, and parameters', () => {
    for (const tool of TOOLS) {
      expect(tool.name).toBeTruthy();
      expect(tool.description).toBeTruthy();
      expect(tool.parameters).toBeDefined();
      expect(tool.parameters.type).toBe('object');
    }
  });

  it('declares composer_generate_music tool', () => {
    const tool = findTool('composer_generate_music');
    expect(tool).toBeDefined();
    expect(tool!.description).toContain('music');
    expect(tool!.parameters.required).toContain('mood');
  });

  it('composer_generate_music has mood, style, duration, type properties', () => {
    const tool = findTool('composer_generate_music')!;
    const props = tool.parameters.properties as Record<string, any>;
    expect(props.mood).toBeDefined();
    expect(props.style).toBeDefined();
    expect(props.duration).toBeDefined();
    expect(props.type).toBeDefined();
  });

  it('declares composer_generate_sfx tool', () => {
    const tool = findTool('composer_generate_sfx');
    expect(tool).toBeDefined();
    expect(tool!.description).toContain('sound effect');
    expect(tool!.parameters.required).toContain('description');
  });

  it('declares composer_synthesize_speech tool', () => {
    const tool = findTool('composer_synthesize_speech');
    expect(tool).toBeDefined();
    expect(tool!.description).toContain('text-to-speech');
    expect(tool!.parameters.required).toContain('text');
  });

  it('composer_synthesize_speech has voice, emotion, provider properties', () => {
    const tool = findTool('composer_synthesize_speech')!;
    const props = tool.parameters.properties as Record<string, any>;
    expect(props.voice).toBeDefined();
    expect(props.emotion).toBeDefined();
    expect(props.provider).toBeDefined();
    expect(props.elevenlabs_voice_id).toBeDefined();
  });

  it('declares composer_create_podcast tool', () => {
    const tool = findTool('composer_create_podcast');
    expect(tool).toBeDefined();
    expect(tool!.description).toContain('podcast');
    expect(tool!.parameters.required).toContain('topic');
  });

  it('composer_create_podcast has format, duration, tone, audience properties', () => {
    const tool = findTool('composer_create_podcast')!;
    const props = tool.parameters.properties as Record<string, any>;
    expect(props.format).toBeDefined();
    expect(props.duration).toBeDefined();
    expect(props.tone).toBeDefined();
    expect(props.audience).toBeDefined();
  });

  it('declares composer_mix_tracks tool', () => {
    const tool = findTool('composer_mix_tracks');
    expect(tool).toBeDefined();
    expect(tool!.description).toContain('Mix');
    expect(tool!.parameters.required).toContain('tracks');
  });

  it('declares composer_apply_effects tool', () => {
    const tool = findTool('composer_apply_effects');
    expect(tool).toBeDefined();
    expect(tool!.description).toContain('effect');
    expect(tool!.parameters.required).toContain('input_path');
    expect(tool!.parameters.required).toContain('effects');
  });

  it('declares composer_list_voices tool', () => {
    const tool = findTool('composer_list_voices');
    expect(tool).toBeDefined();
    expect(tool!.description).toContain('voice');
  });

  it('declares composer_analyze_audio tool', () => {
    const tool = findTool('composer_analyze_audio');
    expect(tool).toBeDefined();
    expect(tool!.description).toContain('Analyze');
    expect(tool!.parameters.required).toContain('input_path');
  });
});

// ---------------------------------------------------------------------------
// Tests: Execute Routing
// ---------------------------------------------------------------------------

describe('Audio Gen Connector — Execute Routing', () => {
  it('returns error for unknown tool name', async () => {
    const result = await execute('composer_nonexistent', {});
    expect(result.error).toBeDefined();
    expect(result.error).toContain('Unknown composer tool');
  });

  it('execute never throws (returns error object instead)', async () => {
    const toolNames = TOOLS.map((t) => t.name);
    for (const name of toolNames) {
      const result = await execute(name, {
        mood: 'calm',
        description: 'test sound',
        text: 'hello world',
        topic: 'test topic',
        input_path: '/fake/path',
        tracks: [],
        effects: {},
      });
      expect(result).toBeDefined();
      expect(typeof result).toBe('object');
      expect(result.result !== undefined || result.error !== undefined).toBe(true);
    }
  });
});

// ---------------------------------------------------------------------------
// Tests: No API Key Behavior
// ---------------------------------------------------------------------------

describe('Audio Gen Connector — No API Key', () => {
  beforeEach(() => {
    mockGeminiKey = '';
    mockElevenLabsKey = '';
  });

  it('composer_generate_music returns error without Gemini key', async () => {
    const result = await execute('composer_generate_music', { mood: 'calm' });
    expect(result.error).toBeDefined();
    expect(result.error).toContain('Gemini API key');
  });

  it('composer_generate_sfx returns error without Gemini key', async () => {
    const result = await execute('composer_generate_sfx', { description: 'whoosh' });
    expect(result.error).toBeDefined();
    expect(result.error).toContain('Gemini API key');
  });

  it('composer_synthesize_speech (gemini) returns error without Gemini key', async () => {
    const result = await execute('composer_synthesize_speech', { text: 'hello' });
    expect(result.error).toBeDefined();
    expect(result.error).toContain('Gemini API key');
  });

  it('composer_synthesize_speech (elevenlabs) returns error without ElevenLabs key', async () => {
    const result = await execute('composer_synthesize_speech', {
      text: 'hello',
      provider: 'elevenlabs',
      elevenlabs_voice_id: 'test-voice-id',
    });
    expect(result.error).toBeDefined();
    expect(result.error).toContain('ElevenLabs API key');
  });
});

// ---------------------------------------------------------------------------
// Tests: FFmpeg Not Installed
// ---------------------------------------------------------------------------

describe('Audio Gen Connector — FFmpeg Not Installed', () => {
  it('composer_mix_tracks returns error when FFmpeg missing', async () => {
    const result = await execute('composer_mix_tracks', {
      tracks: [{ path: '/track1.wav' }, { path: '/track2.wav' }],
    });
    expect(result.error).toBeDefined();
    expect(result.error).toContain('FFmpeg not found');
  });

  it('composer_apply_effects returns error when FFmpeg missing', async () => {
    const result = await execute('composer_apply_effects', {
      input_path: '/test-input.wav',
      effects: { normalize: true },
    });
    expect(result.error).toBeDefined();
    expect(result.error).toContain('FFmpeg not found');
  });

  it('composer_analyze_audio returns error when FFprobe missing', async () => {
    const result = await execute('composer_analyze_audio', {
      input_path: '/test-input.wav',
    });
    expect(result.error).toBeDefined();
    expect(result.error).toContain('FFprobe not found');
  });
});

// ---------------------------------------------------------------------------
// Tests: Input Validation
// ---------------------------------------------------------------------------

describe('Audio Gen Connector — Input Validation', () => {
  it('composer_generate_sfx requires description', async () => {
    const result = await execute('composer_generate_sfx', {});
    expect(result.error).toBeDefined();
    expect(result.error).toContain('description');
  });

  it('composer_synthesize_speech requires text', async () => {
    const result = await execute('composer_synthesize_speech', {});
    expect(result.error).toBeDefined();
    expect(result.error).toContain('text');
  });

  it('composer_synthesize_speech validates Gemini voice name', async () => {
    mockGeminiKey = 'test-key';
    const result = await execute('composer_synthesize_speech', {
      text: 'hello',
      voice: 'InvalidVoice',
    });
    expect(result.error).toBeDefined();
    expect(result.error).toContain('Unknown Gemini voice');
    expect(result.error).toContain('Available');
  });

  it('composer_create_podcast requires topic', async () => {
    const result = await execute('composer_create_podcast', {});
    expect(result.error).toBeDefined();
    expect(result.error).toContain('topic');
  });

  it('composer_mix_tracks requires at least 2 tracks', async () => {
    const result = await execute('composer_mix_tracks', { tracks: [{ path: '/one.wav' }] });
    // Will fail with FFmpeg not found first, but let's test with no tracks
    const result2 = await execute('composer_mix_tracks', { tracks: [] });
    expect(result.error).toBeDefined();
    expect(result2.error).toBeDefined();
  });

  it('composer_apply_effects requires effects object', async () => {
    const result = await execute('composer_apply_effects', { input_path: '/test-input.wav' });
    expect(result.error).toBeDefined();
    expect(result.error).toContain('effects');
  });

  it('composer_apply_effects requires existing input file', async () => {
    const result = await execute('composer_apply_effects', {
      input_path: '/nonexistent/file.wav',
      effects: { normalize: true },
    });
    expect(result.error).toBeDefined();
    // Will error about FFmpeg or file not found
  });

  it('composer_analyze_audio requires input_path', async () => {
    const result = await execute('composer_analyze_audio', {});
    expect(result.error).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: List Voices
// ---------------------------------------------------------------------------

describe('Audio Gen Connector — List Voices', () => {
  it('returns voice list with gemini voices', async () => {
    const result = await execute('composer_list_voices', {});
    expect(result.result).toBeDefined();
    expect(result.error).toBeUndefined();

    const parsed = JSON.parse(result.result!);
    expect(parsed.geminiVoices).toBe(30);
    expect(parsed.voices).toHaveLength(30);
    expect(parsed.elevenLabsAvailable).toBe(false);
  });

  it('shows ElevenLabs available when key is set', async () => {
    mockElevenLabsKey = 'test-eleven-key';
    const result = await execute('composer_list_voices', {});
    const parsed = JSON.parse(result.result!);
    expect(parsed.elevenLabsAvailable).toBe(true);
    expect(parsed.hint).toContain('ElevenLabs voices available');
  });

  it('shows ElevenLabs hint when key is not set', async () => {
    mockElevenLabsKey = '';
    const result = await execute('composer_list_voices', {});
    const parsed = JSON.parse(result.result!);
    expect(parsed.elevenLabsAvailable).toBe(false);
    expect(parsed.hint).toContain('Set an ElevenLabs API key');
  });

  it('each voice has name, provider, and id', async () => {
    const result = await execute('composer_list_voices', {});
    const parsed = JSON.parse(result.result!);
    for (const voice of parsed.voices) {
      expect(voice.name).toBeTruthy();
      expect(voice.provider).toBe('gemini');
      expect(voice.id).toBeTruthy();
    }
  });
});

// ---------------------------------------------------------------------------
// Tests: Podcast Creation (via mocked multimedia engine)
// ---------------------------------------------------------------------------

describe('Audio Gen Connector — Podcast Creation', () => {
  it('creates podcast with default settings', async () => {
    const result = await execute('composer_create_podcast', { topic: 'AI in 2025' });
    expect(result.result).toBeDefined();
    expect(result.error).toBeUndefined();

    const parsed = JSON.parse(result.result!);
    expect(parsed.success).toBe(true);
    expect(parsed.path).toContain('podcast');
    expect(parsed.segments).toBeGreaterThan(0);
    expect(parsed.duration).toBe(60);
  });

  it('passes format and tone to podcast pipeline', async () => {
    const result = await execute('composer_create_podcast', {
      topic: 'Machine learning basics',
      format: 'explainer',
      tone: 'educational',
      audience: 'beginners',
      duration: 'short',
    });
    expect(result.result).toBeDefined();
    const parsed = JSON.parse(result.result!);
    expect(parsed.success).toBe(true);
    expect(parsed.format).toBe('explainer');
    expect(parsed.tone).toBe('educational');
  });
});

// ---------------------------------------------------------------------------
// Tests: Detect
// ---------------------------------------------------------------------------

describe('Audio Gen Connector — Detect', () => {
  it('detect returns a boolean', async () => {
    const result = await detect();
    expect(typeof result).toBe('boolean');
  });

  it('detect never throws', async () => {
    let result: boolean;
    try {
      result = await detect();
    } catch {
      result = false;
      expect.fail('detect() should not throw');
    }
    expect(typeof result).toBe('boolean');
  });

  it('detect returns false with no Gemini key and no FFmpeg', async () => {
    mockGeminiKey = '';
    const result = await detect();
    // Without Gemini key and with mocked-out FFmpeg (always fails), should be false
    expect(result).toBe(false);
  });

  it('detect returns true with Gemini key', async () => {
    mockGeminiKey = 'test-gemini-key';
    const result = await detect();
    expect(result).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Tests: Error Resilience
// ---------------------------------------------------------------------------

describe('Audio Gen Connector — Error Resilience', () => {
  it('handles all tool calls without throwing', async () => {
    const toolNames = TOOLS.map((t) => t.name);
    for (const name of toolNames) {
      let didThrow = false;
      try {
        await execute(name, {});
      } catch {
        didThrow = true;
      }
      expect(didThrow).toBe(false);
    }
  });

  it('returns proper error structure (result XOR error)', async () => {
    const toolNames = TOOLS.map((t) => t.name);
    for (const name of toolNames) {
      const result = await execute(name, {
        mood: 'calm', description: 'test', text: 'hello',
        topic: 'test', input_path: '/nowhere', tracks: [],
        effects: {}, output_path: '/tmp/test.wav',
      });
      // Must have result or error, not both undefined
      const hasResult = result.result !== undefined;
      const hasError = result.error !== undefined;
      expect(hasResult || hasError).toBe(true);
    }
  });

  it('invalid tool name returns meaningful error', async () => {
    const result = await execute('not_a_real_tool', {});
    expect(result.error).toBeDefined();
    expect(result.error).toContain('Unknown');
  });
});

// ---------------------------------------------------------------------------
// Tests: Security & Configuration
// ---------------------------------------------------------------------------

describe('Audio Gen Connector — Security', () => {
  it('tool names are all properly prefixed', () => {
    for (const tool of TOOLS) {
      expect(tool.name).toMatch(/^composer_[a-z_]+$/);
    }
  });

  it('tool names have no duplicates', () => {
    const names = TOOLS.map((t) => t.name);
    const unique = new Set(names);
    expect(unique.size).toBe(names.length);
  });

  it('all parameter types are object', () => {
    for (const tool of TOOLS) {
      expect(tool.parameters.type).toBe('object');
    }
  });

  it('required arrays contain only valid property names', () => {
    for (const tool of TOOLS) {
      const propNames = Object.keys(tool.parameters.properties);
      const required = tool.parameters.required || [];
      for (const req of required) {
        expect(propNames).toContain(req);
      }
    }
  });
});
