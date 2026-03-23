/**
 * comfyui.test.ts — Unit tests for the ComfyUI connector.
 *
 * Tests the connector's structure, tool declarations, execute routing,
 * model registry, and error handling — all WITHOUT requiring a running
 * ComfyUI instance. Network calls are tested by verifying error handling
 * when ComfyUI is unreachable.
 *
 * Sprint 6 Track B Phase 1: "The Canvas" — validation tests.
 * Sprint 6 Track B Phase 2: Workflow Templates & Model Management tests.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import * as http from 'node:http';

// We import directly from the source module
import {
  TOOLS,
  execute,
  detect,
  // Phase 2 exports
  classifyModel,
  resolveSettings,
  MODEL_PROFILES,
  type ModelArchitecture,
  type QualityTier,
  type ModelProfile,
} from '../../src/main/connectors/comfyui';

// ---------------------------------------------------------------------------
// Test Utilities
// ---------------------------------------------------------------------------

/**
 * Helper to find a tool by name in the TOOLS array.
 */
function findTool(name: string) {
  return TOOLS.find((t) => t.name === name);
}

// ---------------------------------------------------------------------------
// Tests: Module Exports
// ---------------------------------------------------------------------------

describe('ComfyUI Connector — Exports', () => {
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

describe('ComfyUI Connector — Tool Declarations', () => {
  it('declares exactly 5 tools', () => {
    expect(TOOLS).toHaveLength(5);
  });

  it('declares comfyui_txt2img tool', () => {
    const tool = findTool('comfyui_txt2img');
    expect(tool).toBeDefined();
    expect(tool!.description).toContain('text prompt');
    expect(tool!.parameters.type).toBe('object');
    expect(tool!.parameters.required).toContain('prompt');
  });

  it('declares comfyui_img2img tool', () => {
    const tool = findTool('comfyui_img2img');
    expect(tool).toBeDefined();
    expect(tool!.description).toContain('existing image');
    expect(tool!.parameters.required).toContain('prompt');
    expect(tool!.parameters.required).toContain('image_path');
  });

  it('declares comfyui_list_models tool', () => {
    const tool = findTool('comfyui_list_models');
    expect(tool).toBeDefined();
    expect(tool!.description).toContain('checkpoint models');
  });

  it('declares comfyui_get_queue tool', () => {
    const tool = findTool('comfyui_get_queue');
    expect(tool).toBeDefined();
    expect(tool!.description).toContain('queue');
  });

  it('declares comfyui_system_info tool', () => {
    const tool = findTool('comfyui_system_info');
    expect(tool).toBeDefined();
    expect(tool!.description).toContain('GPU');
    expect(tool!.description).toContain('VRAM');
    expect(tool!.parameters.type).toBe('object');
  });

  it('all tools have name, description, and parameters', () => {
    for (const tool of TOOLS) {
      expect(tool.name).toBeTruthy();
      expect(tool.description).toBeTruthy();
      expect(tool.parameters).toBeDefined();
      expect(tool.parameters.type).toBe('object');
    }
  });

  it('tool names all start with comfyui_ prefix', () => {
    for (const tool of TOOLS) {
      expect(tool.name).toMatch(/^comfyui_/);
    }
  });

  it('txt2img has width, height, steps, cfg, seed, model parameters', () => {
    const tool = findTool('comfyui_txt2img')!;
    const props = tool.parameters.properties as Record<string, unknown>;
    expect(props.width).toBeDefined();
    expect(props.height).toBeDefined();
    expect(props.steps).toBeDefined();
    expect(props.cfg).toBeDefined();
    expect(props.seed).toBeDefined();
    expect(props.model).toBeDefined();
    expect(props.negative_prompt).toBeDefined();
    expect(props.sampler).toBeDefined();
    expect(props.scheduler).toBeDefined();
  });

  it('img2img has denoise parameter', () => {
    const tool = findTool('comfyui_img2img')!;
    const props = tool.parameters.properties as Record<string, unknown>;
    expect(props.denoise).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: Execute Dispatcher
// ---------------------------------------------------------------------------

describe('ComfyUI Connector — Execute Routing', () => {
  it('returns error for unknown tool name', async () => {
    const result = await execute('comfyui_nonexistent', {});
    expect(result.error).toBeDefined();
    expect(result.error).toContain('Unknown ComfyUI tool');
  });

  it('execute never throws (returns error object instead)', async () => {
    // All tool calls should return {error} when ComfyUI is not running,
    // not throw exceptions.
    const toolNames = TOOLS.map((t) => t.name);
    for (const name of toolNames) {
      const result = await execute(name, { prompt: 'test', image_path: '/test.png' });
      // Should either succeed (unlikely without ComfyUI) or return error object
      expect(result).toBeDefined();
      expect(typeof result).toBe('object');
      // Must have either result or error, not throw
      expect(result.result !== undefined || result.error !== undefined).toBe(true);
    }
  });

  it('txt2img returns error when prompt is missing', async () => {
    const result = await execute('comfyui_txt2img', {});
    expect(result.error).toBeDefined();
    expect(result.error).toContain('prompt');
  });

  it('img2img returns error when prompt is missing', async () => {
    const result = await execute('comfyui_img2img', { image_path: '/test.png' });
    expect(result.error).toBeDefined();
    expect(result.error).toContain('prompt');
  });

  it('img2img returns error when image_path is missing', async () => {
    const result = await execute('comfyui_img2img', { prompt: 'test' });
    expect(result.error).toBeDefined();
    expect(result.error).toContain('image_path');
  });
});

// ---------------------------------------------------------------------------
// Tests: Detect
// ---------------------------------------------------------------------------

describe('ComfyUI Connector — Detect', () => {
  it('returns false when ComfyUI is not running', async () => {
    // ComfyUI is almost certainly not running on the test machine at 127.0.0.1:8188
    const available = await detect();
    expect(available).toBe(false);
  });

  it('detect never throws', async () => {
    // detect() must always return a boolean, never throw
    let result: boolean;
    try {
      result = await detect();
    } catch {
      result = false;
      // If detect threw, that's a bug
      expect.fail('detect() should not throw');
    }
    expect(typeof result).toBe('boolean');
  });
});

// ---------------------------------------------------------------------------
// Tests: Error Resilience
// ---------------------------------------------------------------------------

describe('ComfyUI Connector — Error Resilience', () => {
  it('txt2img gracefully handles ComfyUI being offline', async () => {
    const result = await execute('comfyui_txt2img', { prompt: 'a beautiful sunset' });
    expect(result.error).toBeDefined();
    // Should mention connection failure, not crash
    expect(result.error!.length).toBeGreaterThan(0);
  });

  it('list_models gracefully handles ComfyUI being offline', async () => {
    const result = await execute('comfyui_list_models', {});
    expect(result.error).toBeDefined();
    expect(result.error!.length).toBeGreaterThan(0);
  });

  it('get_queue gracefully handles ComfyUI being offline', async () => {
    const result = await execute('comfyui_get_queue', {});
    expect(result.error).toBeDefined();
    expect(result.error!.length).toBeGreaterThan(0);
  });

  it('system_info gracefully handles ComfyUI being offline', async () => {
    const result = await execute('comfyui_system_info', {});
    expect(result.error).toBeDefined();
    expect(result.error!.length).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// Tests: Model Registry — Phase 2
// ---------------------------------------------------------------------------

describe('ComfyUI Connector — Model Profiles (Phase 2)', () => {
  it('MODEL_PROFILES has entries for all 6 architectures', () => {
    const expected: ModelArchitecture[] = ['sd15', 'sdxl', 'sd3', 'turbo', 'flux', 'unknown'];
    for (const arch of expected) {
      expect(MODEL_PROFILES[arch]).toBeDefined();
      expect(MODEL_PROFILES[arch].architecture).toBe(arch);
    }
  });

  it('each profile has all required fields', () => {
    for (const profile of Object.values(MODEL_PROFILES)) {
      expect(profile.defaultWidth).toBeGreaterThan(0);
      expect(profile.defaultHeight).toBeGreaterThan(0);
      expect(profile.defaultSteps).toBeGreaterThan(0);
      expect(profile.defaultCfg).toBeGreaterThan(0);
      expect(profile.defaultSampler).toBeTruthy();
      expect(profile.defaultScheduler).toBeTruthy();
      expect(profile.estimatedVramMB).toBeGreaterThan(0);
      expect(profile.description).toBeTruthy();
    }
  });

  it('SDXL/SD3/Flux default to 1024×1024', () => {
    expect(MODEL_PROFILES.sdxl.defaultWidth).toBe(1024);
    expect(MODEL_PROFILES.sdxl.defaultHeight).toBe(1024);
    expect(MODEL_PROFILES.sd3.defaultWidth).toBe(1024);
    expect(MODEL_PROFILES.sd3.defaultHeight).toBe(1024);
    expect(MODEL_PROFILES.flux.defaultWidth).toBe(1024);
    expect(MODEL_PROFILES.flux.defaultHeight).toBe(1024);
  });

  it('SD 1.5 and Turbo default to 512×512', () => {
    expect(MODEL_PROFILES.sd15.defaultWidth).toBe(512);
    expect(MODEL_PROFILES.sd15.defaultHeight).toBe(512);
    expect(MODEL_PROFILES.turbo.defaultWidth).toBe(512);
    expect(MODEL_PROFILES.turbo.defaultHeight).toBe(512);
  });

  it('Turbo has very few default steps (fast tier)', () => {
    expect(MODEL_PROFILES.turbo.defaultSteps).toBeLessThanOrEqual(6);
    expect(MODEL_PROFILES.turbo.qualityTier).toBe('fast');
  });

  it('quality tier assignments are correct', () => {
    expect(MODEL_PROFILES.turbo.qualityTier).toBe('fast');
    expect(MODEL_PROFILES.sd15.qualityTier).toBe('balanced');
    expect(MODEL_PROFILES.unknown.qualityTier).toBe('balanced');
    expect(MODEL_PROFILES.sdxl.qualityTier).toBe('quality');
    expect(MODEL_PROFILES.sd3.qualityTier).toBe('quality');
    expect(MODEL_PROFILES.flux.qualityTier).toBe('quality');
  });
});

// ---------------------------------------------------------------------------
// Tests: Model Classification — Phase 2
// ---------------------------------------------------------------------------

describe('ComfyUI Connector — classifyModel (Phase 2)', () => {
  it('classifies SDXL models', () => {
    expect(classifyModel('sd_xl_base_1.0.safetensors')).toBe('sdxl');
    expect(classifyModel('sdxl_vae_fp16.safetensors')).toBe('sdxl');
    expect(classifyModel('juggernautXL.safetensors')).toBe('sdxl');
    expect(classifyModel('xl-base-v1.safetensors')).toBe('sdxl');
    expect(classifyModel('xl_base_1.0.safetensors')).toBe('sdxl');
  });

  it('classifies SD 1.5 models', () => {
    expect(classifyModel('v1-5-pruned-emaonly.safetensors')).toBe('sd15');
    expect(classifyModel('sd_v1.5.safetensors')).toBe('sd15');
    expect(classifyModel('dreamshaper_8.safetensors')).toBe('sd15');
    expect(classifyModel('realisticVisionV60.safetensors')).toBe('sd15');
    expect(classifyModel('deliberate_v6.safetensors')).toBe('sd15');
    expect(classifyModel('revAnimated_v122.safetensors')).toBe('sd15');
  });

  it('classifies Turbo/Lightning models', () => {
    expect(classifyModel('sd_xl_turbo_1.0.safetensors')).toBe('turbo');
    expect(classifyModel('sdxl_lightning_4step.safetensors')).toBe('turbo');
    expect(classifyModel('lcm_lora_sd15.safetensors')).toBe('turbo');
  });

  it('classifies Flux models', () => {
    expect(classifyModel('flux1-dev-fp8.safetensors')).toBe('flux');
    expect(classifyModel('FLUX.1-schnell.safetensors')).toBe('flux');
  });

  it('classifies SD3 models', () => {
    expect(classifyModel('sd3_medium_incl_clips.safetensors')).toBe('sd3');
    expect(classifyModel('stable-diffusion-3-medium.safetensors')).toBe('sd3');
    expect(classifyModel('sd_3_large.safetensors')).toBe('sd3');
  });

  it('returns unknown for unrecognized filenames', () => {
    expect(classifyModel('custom_model.safetensors')).toBe('unknown');
    expect(classifyModel('my_fancy_model_v2.ckpt')).toBe('unknown');
    expect(classifyModel('')).toBe('unknown');
  });

  it('Turbo detection takes priority over SDXL/SD15 patterns', () => {
    // A model named "sdxl_turbo" should be turbo, not sdxl
    expect(classifyModel('sdxl_turbo_1.0.safetensors')).toBe('turbo');
    // A model named "sd15_lightning" should be turbo, not sd15
    expect(classifyModel('sd1.5_lightning_4step.safetensors')).toBe('turbo');
  });

  it('is case-insensitive', () => {
    expect(classifyModel('SDXL_BASE.safetensors')).toBe('sdxl');
    expect(classifyModel('SD3_MEDIUM.safetensors')).toBe('sd3');
    expect(classifyModel('FLUX_DEV.safetensors')).toBe('flux');
    expect(classifyModel('TURBO_V1.safetensors')).toBe('turbo');
  });
});

// ---------------------------------------------------------------------------
// Tests: Resolve Settings — Phase 2
// ---------------------------------------------------------------------------

describe('ComfyUI Connector — resolveSettings (Phase 2)', () => {
  it('uses model profile defaults when no overrides given', () => {
    const settings = resolveSettings('sd_xl_base_1.0.safetensors', {});
    expect(settings.width).toBe(1024);
    expect(settings.height).toBe(1024);
    expect(settings.steps).toBe(25);
    expect(settings.cfg).toBe(7);
    expect(settings.sampler).toBe('dpmpp_2m');
    expect(settings.scheduler).toBe('karras');
    expect(settings.profile.architecture).toBe('sdxl');
  });

  it('user overrides take priority over profile defaults', () => {
    const settings = resolveSettings('sd_xl_base_1.0.safetensors', {
      width: 768,
      height: 768,
      steps: 10,
      cfg: 3,
      sampler: 'euler',
      scheduler: 'normal',
    });
    expect(settings.width).toBe(768);
    expect(settings.height).toBe(768);
    expect(settings.steps).toBe(10);
    expect(settings.cfg).toBe(3);
    expect(settings.sampler).toBe('euler');
    expect(settings.scheduler).toBe('normal');
    // Profile still correctly identified
    expect(settings.profile.architecture).toBe('sdxl');
  });

  it('partial overrides merge with defaults', () => {
    // Only override width — everything else from profile
    const settings = resolveSettings('dreamshaper_8.safetensors', {
      width: 768,
    });
    expect(settings.width).toBe(768);          // overridden
    expect(settings.height).toBe(512);          // from sd15 profile
    expect(settings.steps).toBe(20);            // from sd15 profile
    expect(settings.cfg).toBe(7);               // from sd15 profile
    expect(settings.profile.architecture).toBe('sd15');
  });

  it('Turbo model defaults to 4 steps and low CFG', () => {
    const settings = resolveSettings('sdxl_turbo_1.0.safetensors', {});
    expect(settings.steps).toBe(4);
    expect(settings.cfg).toBe(1.5);
    expect(settings.profile.qualityTier).toBe('fast');
  });

  it('unknown model falls back to SD 1.5 defaults', () => {
    const settings = resolveSettings('mysterious_model.safetensors', {});
    expect(settings.width).toBe(512);
    expect(settings.height).toBe(512);
    expect(settings.steps).toBe(20);
    expect(settings.profile.architecture).toBe('unknown');
  });

  it('empty model string uses unknown profile', () => {
    const settings = resolveSettings('', {});
    expect(settings.profile.architecture).toBe('unknown');
    expect(settings.width).toBe(512);
    expect(settings.height).toBe(512);
  });

  it('returns the full ModelProfile in the profile field', () => {
    const settings = resolveSettings('flux1-dev.safetensors', {});
    expect(settings.profile).toBeDefined();
    expect(settings.profile.architecture).toBe('flux');
    expect(settings.profile.description).toContain('Flux');
    expect(settings.profile.estimatedVramMB).toBeGreaterThan(0);
  });
});
