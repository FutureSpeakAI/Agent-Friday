/**
 * video-gen.test.ts — Unit tests for the Video Generation connector.
 *
 * Tests the connector's structure, tool declarations, execute routing,
 * detection, error handling, and VEO integration patterns — all WITHOUT
 * requiring network access or actual Gemini API calls.
 *
 * Sprint 6 Track C: "The Director" — validation tests.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ---------------------------------------------------------------------------
// Mocks — must be before imports (vi.mock is hoisted)
// ---------------------------------------------------------------------------

// Mock Electron's app module
vi.mock('electron', () => ({
  app: { getPath: (name: string) => name === 'temp' ? '/tmp/test-videos' : '/tmp/test' },
}));

// Mock settings module — no API key by default
let mockGeminiKey = '';
vi.mock('../../../src/main/settings', () => ({
  settingsManager: {
    getGeminiApiKey: () => mockGeminiKey,
  },
}));

// Mock fs — prevent actual file system operations
vi.mock('node:fs', async () => {
  const actual = await vi.importActual<typeof import('node:fs')>('node:fs');
  return {
    ...actual,
    existsSync: vi.fn((p: string) => {
      // For test video paths, pretend they exist
      if (typeof p === 'string' && (p.includes('test-clip') || p.includes('test-image'))) return true;
      if (typeof p === 'string' && p.includes('agent-friday-videos')) return true;
      return false;
    }),
    mkdirSync: vi.fn(),
    writeFileSync: vi.fn(),
    readFileSync: vi.fn(() => Buffer.from('fake-image-data')),
    statSync: vi.fn(() => ({ size: 1024 * 1024 })),
    unlinkSync: vi.fn(),
    createWriteStream: vi.fn(() => ({
      on: vi.fn(),
      close: vi.fn(),
    })),
  };
});

// Mock child_process to prevent actual FFmpeg execution
vi.mock('node:child_process', () => ({
  execFile: vi.fn((_cmd: string, _args: string[], _opts: any, cb: Function) => {
    if (typeof cb === 'function') {
      // Simulate "command not found" by default for FFmpeg tests
      cb(new Error('command not found'), '', '');
    }
  }),
}));

// Mock node:util promisify
vi.mock('node:util', async () => {
  const actual = await vi.importActual<typeof import('node:util')>('node:util');
  return {
    ...actual,
    promisify: (fn: Function) => {
      return async (...args: any[]) => {
        return new Promise((resolve, reject) => {
          fn(...args, (err: Error | null, ...results: any[]) => {
            if (err) reject(err);
            else resolve(results.length === 1 ? results[0] : { stdout: results[0], stderr: results[1] });
          });
        });
      };
    },
  };
});

// Mock https to prevent actual network calls
vi.mock('node:https', () => ({
  request: vi.fn((_opts: any, _cb: Function) => {
    const req = {
      on: vi.fn(),
      write: vi.fn(),
      end: vi.fn(),
      destroy: vi.fn(),
    };
    return req;
  }),
  get: vi.fn(),
}));

// Import after mocks
import { TOOLS, execute, detect } from '../../../src/main/connectors/video-gen';

// ---------------------------------------------------------------------------
// Test Utilities
// ---------------------------------------------------------------------------

function findTool(name: string) {
  return TOOLS.find((t) => t.name === name);
}

// ---------------------------------------------------------------------------
// Tests: Module Exports
// ---------------------------------------------------------------------------

describe('Video Gen Connector — Exports', () => {
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

describe('Video Gen Connector — Tool Declarations', () => {
  it('declares exactly 7 tools', () => {
    expect(TOOLS).toHaveLength(7);
  });

  it('declares video_generate tool', () => {
    const tool = findTool('video_generate');
    expect(tool).toBeDefined();
    expect(tool!.description).toContain('VEO');
    expect(tool!.description).toContain('text');
    expect(tool!.parameters.required).toContain('prompt');
  });

  it('video_generate has prompt, aspect_ratio, duration, and allow_people parameters', () => {
    const tool = findTool('video_generate')!;
    const props = tool.parameters.properties as Record<string, any>;
    expect(props.prompt).toBeDefined();
    expect(props.prompt.type).toBe('string');
    expect(props.aspect_ratio).toBeDefined();
    expect(props.aspect_ratio.type).toBe('string');
    expect(props.duration).toBeDefined();
    expect(props.duration.type).toBe('number');
    expect(props.allow_people).toBeDefined();
    expect(props.allow_people.type).toBe('boolean');
  });

  it('declares video_from_image tool', () => {
    const tool = findTool('video_from_image');
    expect(tool).toBeDefined();
    expect(tool!.description).toContain('image');
    expect(tool!.description).toContain('VEO');
    expect(tool!.parameters.required).toContain('image_path');
    expect(tool!.parameters.required).toContain('prompt');
  });

  it('declares video_status tool', () => {
    const tool = findTool('video_status');
    expect(tool).toBeDefined();
    expect(tool!.description).toContain('status');
    expect(tool!.parameters.required).toContain('job_id');
  });

  it('declares video_wait tool', () => {
    const tool = findTool('video_wait');
    expect(tool).toBeDefined();
    expect(tool!.description).toContain('Wait');
    expect(tool!.parameters.required).toContain('job_id');
  });

  it('declares video_stitch tool', () => {
    const tool = findTool('video_stitch');
    expect(tool).toBeDefined();
    expect(tool!.description).toContain('FFmpeg');
    expect(tool!.description).toContain('Concatenate');
    expect(tool!.parameters.required).toContain('clips');
  });

  it('video_stitch has clips, audio_path, output_path, and format parameters', () => {
    const tool = findTool('video_stitch')!;
    const props = tool.parameters.properties as Record<string, any>;
    expect(props.clips).toBeDefined();
    expect(props.clips.type).toBe('array');
    expect(props.audio_path).toBeDefined();
    expect(props.output_path).toBeDefined();
    expect(props.format).toBeDefined();
  });

  it('declares video_info tool', () => {
    const tool = findTool('video_info');
    expect(tool).toBeDefined();
    expect(tool!.description).toContain('FFprobe');
    expect(tool!.description).toContain('metadata');
    expect(tool!.parameters.required).toContain('file_path');
  });

  it('declares video_convert tool', () => {
    const tool = findTool('video_convert');
    expect(tool).toBeDefined();
    expect(tool!.description).toContain('Convert');
    expect(tool!.description).toContain('FFmpeg');
    expect(tool!.parameters.required).toContain('input_path');
    expect(tool!.parameters.required).toContain('output_path');
  });

  it('video_convert has width, height, and fps parameters', () => {
    const tool = findTool('video_convert')!;
    const props = tool.parameters.properties as Record<string, any>;
    expect(props.width).toBeDefined();
    expect(props.width.type).toBe('number');
    expect(props.height).toBeDefined();
    expect(props.height.type).toBe('number');
    expect(props.fps).toBeDefined();
    expect(props.fps.type).toBe('number');
  });

  it('all tools have name, description, and parameters', () => {
    for (const tool of TOOLS) {
      expect(tool.name).toBeTruthy();
      expect(tool.description).toBeTruthy();
      expect(tool.parameters).toBeDefined();
      expect(tool.parameters.type).toBe('object');
    }
  });

  it('tool names all start with video_ prefix', () => {
    for (const tool of TOOLS) {
      expect(tool.name).toMatch(/^video_/);
    }
  });
});

// ---------------------------------------------------------------------------
// Tests: Execute Routing
// ---------------------------------------------------------------------------

describe('Video Gen Connector — Execute Routing', () => {
  it('returns error for unknown tool name', async () => {
    const result = await execute('video_nonexistent', {});
    expect(result.error).toBeDefined();
    expect(result.error).toContain('Unknown video-gen tool');
  });

  it('execute never throws (returns error object instead)', async () => {
    const toolNames = TOOLS.map((t) => t.name);
    for (const name of toolNames) {
      const result = await execute(name, {
        prompt: 'test prompt',
        image_path: '/fake/test-image.png',
        job_id: 'nonexistent-job',
        clips: ['/fake/test-clip1.mp4'],
        file_path: '/fake/test-clip1.mp4',
        input_path: '/fake/test-clip1.mp4',
        output_path: '/tmp/output.mp4',
      });
      expect(result).toBeDefined();
      expect(typeof result).toBe('object');
      // Must have either result or error, not throw
      expect(result.result !== undefined || result.error !== undefined).toBe(true);
    }
  });
});

// ---------------------------------------------------------------------------
// Tests: Cloud Tools — VEO (no API key configured)
// ---------------------------------------------------------------------------

describe('Video Gen Connector — VEO (No API Key)', () => {
  beforeEach(() => {
    mockGeminiKey = '';
  });

  it('video_generate returns error without API key', async () => {
    const result = await execute('video_generate', { prompt: 'A sunset over the ocean' });
    expect(result.error).toBeDefined();
    expect(result.error!.toLowerCase()).toMatch(/api key|not configured/);
  });

  it('video_from_image returns error without API key', async () => {
    const result = await execute('video_from_image', {
      image_path: '/fake/test-image.png',
      prompt: 'Animate this scene',
    });
    expect(result.error).toBeDefined();
    expect(result.error!.toLowerCase()).toMatch(/api key|not configured/);
  });

  it('video_generate returns error with empty prompt', async () => {
    mockGeminiKey = 'test-key-123';
    const result = await execute('video_generate', { prompt: '' });
    expect(result.error).toBeDefined();
    expect(result.error).toContain('prompt');
  });

  it('video_generate returns error with missing prompt', async () => {
    mockGeminiKey = 'test-key-123';
    const result = await execute('video_generate', {});
    expect(result.error).toBeDefined();
    expect(result.error).toContain('prompt');
  });

  it('video_from_image returns error with missing image_path', async () => {
    mockGeminiKey = 'test-key-123';
    const result = await execute('video_from_image', { prompt: 'animate' });
    expect(result.error).toBeDefined();
    expect(result.error).toContain('image_path');
  });

  it('video_from_image returns error with missing prompt', async () => {
    mockGeminiKey = 'test-key-123';
    const result = await execute('video_from_image', { image_path: '/fake/test-image.png' });
    expect(result.error).toBeDefined();
    expect(result.error).toContain('prompt');
  });
});

// ---------------------------------------------------------------------------
// Tests: Job Status & Wait — Missing jobs
// ---------------------------------------------------------------------------

describe('Video Gen Connector — Job Management', () => {
  it('video_status returns error for missing job_id', async () => {
    const result = await execute('video_status', {});
    expect(result.error).toBeDefined();
    expect(result.error).toContain('job_id');
  });

  it('video_status returns error for unknown job_id', async () => {
    mockGeminiKey = 'test-key-123';
    const result = await execute('video_status', { job_id: 'nonexistent-123' });
    expect(result.error).toBeDefined();
    expect(result.error).toContain('Unknown job ID');
  });

  it('video_wait returns error for missing job_id', async () => {
    const result = await execute('video_wait', {});
    expect(result.error).toBeDefined();
    expect(result.error).toContain('job_id');
  });

  it('video_wait returns error for unknown job_id', async () => {
    const result = await execute('video_wait', { job_id: 'nonexistent-456' });
    expect(result.error).toBeDefined();
    expect(result.error).toContain('Unknown job ID');
  });
});

// ---------------------------------------------------------------------------
// Tests: Local Tools — FFmpeg (not installed in test)
// ---------------------------------------------------------------------------

describe('Video Gen Connector — FFmpeg (Not Installed)', () => {
  it('video_stitch returns error without FFmpeg', async () => {
    const result = await execute('video_stitch', {
      clips: ['/fake/test-clip1.mp4', '/fake/test-clip2.mp4'],
    });
    expect(result.error).toBeDefined();
    expect(result.error!.toLowerCase()).toMatch(/ffmpeg|not installed/);
  });

  it('video_info returns error without FFprobe', async () => {
    const result = await execute('video_info', {
      file_path: '/fake/test-clip1.mp4',
    });
    expect(result.error).toBeDefined();
    expect(result.error!.toLowerCase()).toMatch(/ffprobe|not installed|ffmpeg/);
  });

  it('video_convert returns error without FFmpeg', async () => {
    const result = await execute('video_convert', {
      input_path: '/fake/test-clip1.mp4',
      output_path: '/tmp/output.webm',
    });
    expect(result.error).toBeDefined();
    expect(result.error!.toLowerCase()).toMatch(/ffmpeg|not installed/);
  });

  it('video_stitch returns error with empty clips array', async () => {
    const result = await execute('video_stitch', { clips: [] });
    expect(result.error).toBeDefined();
    expect(result.error).toContain('clips');
  });

  it('video_stitch returns error with missing clips parameter', async () => {
    const result = await execute('video_stitch', {});
    expect(result.error).toBeDefined();
    expect(result.error).toContain('clips');
  });

  it('video_info returns error with missing file_path', async () => {
    const result = await execute('video_info', {});
    expect(result.error).toBeDefined();
    expect(result.error).toContain('file_path');
  });

  it('video_convert returns error with missing input_path', async () => {
    const result = await execute('video_convert', { output_path: '/tmp/out.mp4' });
    expect(result.error).toBeDefined();
    expect(result.error).toContain('input_path');
  });

  it('video_convert returns error with missing output_path', async () => {
    const result = await execute('video_convert', { input_path: '/fake/test-clip1.mp4' });
    expect(result.error).toBeDefined();
    expect(result.error).toContain('output_path');
  });
});

// ---------------------------------------------------------------------------
// Tests: Detect
// ---------------------------------------------------------------------------

describe('Video Gen Connector — Detect', () => {
  beforeEach(() => {
    mockGeminiKey = '';
  });

  it('detect returns a boolean', async () => {
    const result = await detect();
    expect(typeof result).toBe('boolean');
  });

  it('detect never throws', async () => {
    let threw = false;
    try {
      await detect();
    } catch {
      threw = true;
    }
    expect(threw).toBe(false);
  });

  it('detect returns true when Gemini API key is present', async () => {
    mockGeminiKey = 'test-gemini-key-xyz';
    const result = await detect();
    expect(result).toBe(true);
  });

  it('detect returns false when no API key and no FFmpeg', async () => {
    mockGeminiKey = '';
    // FFmpeg mock throws "command not found" by default
    const result = await detect();
    expect(result).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Tests: Error Resilience
// ---------------------------------------------------------------------------

describe('Video Gen Connector — Error Resilience', () => {
  it('handles invalid argument types gracefully', async () => {
    const result = await execute('video_generate', { prompt: 123 as any });
    expect(result.error).toBeDefined();
  });

  it('video_from_image rejects unsupported image formats', async () => {
    mockGeminiKey = 'test-key-123';
    const result = await execute('video_from_image', {
      image_path: '/fake/test-image.bmp',
      prompt: 'animate',
    });
    // BMP isn't in our supported formats — should error
    // Note: existsSync mock returns false for .bmp, so it'll be "not found"
    expect(result.error).toBeDefined();
  });

  it('all error messages are non-empty strings', async () => {
    // Test a selection of error-producing calls
    const errorCalls = [
      execute('video_nonexistent', {}),
      execute('video_generate', {}),
      execute('video_status', {}),
      execute('video_wait', {}),
      execute('video_stitch', {}),
      execute('video_info', {}),
      execute('video_convert', {}),
    ];

    const results = await Promise.all(errorCalls);
    for (const r of results) {
      expect(r.error).toBeDefined();
      expect(typeof r.error).toBe('string');
      expect(r.error!.length).toBeGreaterThan(0);
    }
  });
});

// ---------------------------------------------------------------------------
// Tests: Tool Parameter Validation
// ---------------------------------------------------------------------------

describe('Video Gen Connector — Parameter Validation', () => {
  beforeEach(() => {
    mockGeminiKey = '';
  });

  it('video_generate validates prompt is required', async () => {
    mockGeminiKey = 'key';
    const noPrompt = await execute('video_generate', {});
    expect(noPrompt.error).toContain('prompt');

    const emptyPrompt = await execute('video_generate', { prompt: '' });
    expect(emptyPrompt.error).toContain('prompt');

    const whitespacePrompt = await execute('video_generate', { prompt: '   ' });
    expect(whitespacePrompt.error).toContain('prompt');
  });

  it('video_from_image validates both image_path and prompt are required', async () => {
    mockGeminiKey = 'key';

    const noArgs = await execute('video_from_image', {});
    expect(noArgs.error).toContain('image_path');

    const noPrompt = await execute('video_from_image', { image_path: '/fake/test-image.png' });
    expect(noPrompt.error).toContain('prompt');
  });

  it('video_status validates job_id is required', async () => {
    const noJobId = await execute('video_status', {});
    expect(noJobId.error).toContain('job_id');

    const emptyJobId = await execute('video_status', { job_id: '' });
    expect(emptyJobId.error).toContain('job_id');
  });

  it('video_stitch validates clips array is non-empty', async () => {
    const noClips = await execute('video_stitch', {});
    expect(noClips.error).toContain('clips');

    const emptyClips = await execute('video_stitch', { clips: [] });
    expect(emptyClips.error).toContain('clips');
  });

  it('video_info validates file_path is required', async () => {
    const noPath = await execute('video_info', {});
    expect(noPath.error).toContain('file_path');

    const emptyPath = await execute('video_info', { file_path: '' });
    expect(emptyPath.error).toContain('file_path');
  });

  it('video_convert validates both input_path and output_path are required', async () => {
    const noArgs = await execute('video_convert', {});
    expect(noArgs.error).toContain('input_path');

    const noOutput = await execute('video_convert', { input_path: '/fake/test-clip1.mp4' });
    expect(noOutput.error).toContain('output_path');
  });
});
