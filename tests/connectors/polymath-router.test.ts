/**
 * polymath-router.test.ts — Unit tests for the Polymath Creative Router.
 *
 * Tests the router's intent classification, dispatch routing, pipeline
 * management, and error handling — all WITHOUT requiring actual creative
 * backends to be running.
 *
 * Sprint 6 Track F: "The Polymath Router" — Unified creative dispatch.
 */

import { describe, it, expect } from 'vitest';

import {
  TOOLS,
  execute,
  detect,
  classifyIntent,
  suggestWorkflow,
  WORKFLOW_TEMPLATES,
} from '../../../src/main/connectors/polymath-router';

import type { CreativeDomain } from '../../../src/main/connectors/polymath-router';

// ---------------------------------------------------------------------------
// Test Utilities
// ---------------------------------------------------------------------------

function findTool(name: string) {
  return TOOLS.find((t) => t.name === name);
}

// ---------------------------------------------------------------------------
// Tests: Module Exports
// ---------------------------------------------------------------------------

describe('Polymath Router — Exports', () => {
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

  it('exports classifyIntent as a function', () => {
    expect(typeof classifyIntent).toBe('function');
  });

  it('exports suggestWorkflow as a function', () => {
    expect(typeof suggestWorkflow).toBe('function');
  });

  it('exports WORKFLOW_TEMPLATES object', () => {
    expect(typeof WORKFLOW_TEMPLATES).toBe('object');
    expect(Object.keys(WORKFLOW_TEMPLATES).length).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// Tests: Tool Declarations
// ---------------------------------------------------------------------------

describe('Polymath Router — Tool Declarations', () => {
  it('declares exactly 6 tools', () => {
    expect(TOOLS).toHaveLength(6);
  });

  it('declares polymath_capabilities tool', () => {
    const tool = findTool('polymath_capabilities');
    expect(tool).toBeDefined();
    expect(tool!.description).toContain('capabilities');
    expect(tool!.parameters.type).toBe('object');
  });

  it('declares polymath_classify tool with required prompt', () => {
    const tool = findTool('polymath_classify');
    expect(tool).toBeDefined();
    expect(tool!.description).toContain('classify');
    expect(tool!.parameters.required).toContain('prompt');
    const props = tool!.parameters.properties as Record<string, any>;
    expect(props.prompt).toBeDefined();
    expect(props.prompt.type).toBe('string');
  });

  it('declares polymath_dispatch tool with required domain and prompt', () => {
    const tool = findTool('polymath_dispatch');
    expect(tool).toBeDefined();
    expect(tool!.description).toContain('Route');
    expect(tool!.parameters.required).toContain('domain');
    expect(tool!.parameters.required).toContain('prompt');
    const props = tool!.parameters.properties as Record<string, any>;
    expect(props.domain).toBeDefined();
    expect(props.prompt).toBeDefined();
    expect(props.options).toBeDefined();
  });

  it('declares polymath_pipeline_create tool', () => {
    const tool = findTool('polymath_pipeline_create');
    expect(tool).toBeDefined();
    expect(tool!.description).toContain('pipeline');
    const props = tool!.parameters.properties as Record<string, any>;
    expect(props.template).toBeDefined();
    expect(props.steps).toBeDefined();
    expect(props.name).toBeDefined();
    expect(props.creative_brief).toBeDefined();
  });

  it('declares polymath_pipeline_status tool with required pipeline_id', () => {
    const tool = findTool('polymath_pipeline_status');
    expect(tool).toBeDefined();
    expect(tool!.parameters.required).toContain('pipeline_id');
  });

  it('declares polymath_pipeline_list tool', () => {
    const tool = findTool('polymath_pipeline_list');
    expect(tool).toBeDefined();
    expect(tool!.description).toContain('List');
  });

  it('all tools have name, description, and parameters', () => {
    for (const tool of TOOLS) {
      expect(tool.name).toBeTruthy();
      expect(tool.description).toBeTruthy();
      expect(tool.parameters).toBeDefined();
      expect(tool.parameters.type).toBe('object');
    }
  });

  it('tool names all start with polymath_ prefix', () => {
    for (const tool of TOOLS) {
      expect(tool.name).toMatch(/^polymath_/);
    }
  });
});

// ---------------------------------------------------------------------------
// Tests: Detect
// ---------------------------------------------------------------------------

describe('Polymath Router — Detect', () => {
  it('detect always returns true (meta-router is always available)', async () => {
    const result = await detect();
    expect(result).toBe(true);
  });

  it('detect never throws', async () => {
    let result: boolean;
    try {
      result = await detect();
    } catch {
      result = false;
      expect.fail('detect() should not throw');
    }
    expect(result).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Tests: Intent Classification
// ---------------------------------------------------------------------------

describe('Polymath Router — Intent Classification', () => {
  it('classifies image request', () => {
    const domains = classifyIntent('create an image of a sunset over mountains');
    expect(domains).toContain('image');
    expect(domains[0]).toBe('image');
  });

  it('classifies video request', () => {
    const domains = classifyIntent('make a short video clip of ocean waves');
    expect(domains).toContain('video');
  });

  it('classifies music request', () => {
    const domains = classifyIntent('generate a calm instrumental melody');
    expect(domains).toContain('music');
  });

  it('classifies sound effect request', () => {
    const domains = classifyIntent('create a whoosh sound effect');
    expect(domains).toContain('sfx');
  });

  it('classifies speech request', () => {
    const domains = classifyIntent('read this text aloud as narration');
    expect(domains).toContain('speech');
  });

  it('classifies podcast request', () => {
    const domains = classifyIntent('create a podcast episode about AI');
    expect(domains).toContain('podcast');
  });

  it('classifies code request', () => {
    const domains = classifyIntent('find the function that handles authentication');
    expect(domains).toContain('code');
  });

  it('classifies multi-domain request (music + video)', () => {
    const domains = classifyIntent('make a music video with animation');
    expect(domains).toContain('music');
    expect(domains).toContain('video');
    expect(domains.length).toBeGreaterThanOrEqual(2);
  });

  it('returns empty array for unrelated prompt', () => {
    const domains = classifyIntent('what is the weather today');
    expect(domains).toHaveLength(0);
  });

  it('is case insensitive', () => {
    const domains = classifyIntent('CREATE AN IMAGE OF A CAT');
    expect(domains).toContain('image');
  });

  it('scores higher domains first', () => {
    // "image" has more keyword hits than others in this prompt
    const domains = classifyIntent('render a detailed illustration and drawing of a landscape portrait');
    expect(domains[0]).toBe('image');
  });
});

// ---------------------------------------------------------------------------
// Tests: Workflow Suggestion
// ---------------------------------------------------------------------------

describe('Polymath Router — Workflow Suggestion', () => {
  it('suggests music_video for music + video domains', () => {
    const template = suggestWorkflow(['music', 'video']);
    expect(template).toBe('music_video');
  });

  it('suggests narrated_explainer for speech + video domains', () => {
    const template = suggestWorkflow(['speech', 'video']);
    expect(template).toBe('narrated_explainer');
  });

  it('suggests podcast_with_intro for podcast + music domains', () => {
    const template = suggestWorkflow(['podcast', 'music']);
    expect(template).toBe('podcast_with_intro');
  });

  it('suggests social_media_post for image + video domains', () => {
    const template = suggestWorkflow(['image', 'video']);
    expect(template).toBe('social_media_post');
  });

  it('suggests game_assets for image + sfx + music domains', () => {
    const template = suggestWorkflow(['image', 'sfx', 'music']);
    expect(template).toBe('game_assets');
  });

  it('returns null for single domain', () => {
    const template = suggestWorkflow(['image']);
    expect(template).toBeNull();
  });

  it('returns null for empty domains', () => {
    const template = suggestWorkflow([]);
    expect(template).toBeNull();
  });

  it('returns null for unmatched domain combination', () => {
    const template = suggestWorkflow(['code', 'document']);
    expect(template).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Tests: Workflow Templates
// ---------------------------------------------------------------------------

describe('Polymath Router — Workflow Templates', () => {
  it('has 5 built-in workflow templates', () => {
    expect(Object.keys(WORKFLOW_TEMPLATES)).toHaveLength(5);
  });

  it('all templates have name, description, and steps', () => {
    for (const [id, template] of Object.entries(WORKFLOW_TEMPLATES)) {
      expect(template.name).toBeTruthy();
      expect(template.description).toBeTruthy();
      expect(template.steps.length).toBeGreaterThan(0);
    }
  });

  it('all template steps have valid domains', () => {
    const validDomains: CreativeDomain[] = ['image', 'video', 'music', 'sfx', 'speech', 'podcast', 'code', 'document'];
    for (const template of Object.values(WORKFLOW_TEMPLATES)) {
      for (const step of template.steps) {
        expect(validDomains).toContain(step.domain);
        expect(step.prompt).toBeTruthy();
      }
    }
  });

  it('music_video template has 4 steps spanning music + image + video', () => {
    const template = WORKFLOW_TEMPLATES['music_video'];
    expect(template.steps).toHaveLength(4);
    const domains = new Set(template.steps.map((s) => s.domain));
    expect(domains.has('music')).toBe(true);
    expect(domains.has('image')).toBe(true);
    expect(domains.has('video')).toBe(true);
  });

  it('narrated_explainer template chains speech → image → video', () => {
    const template = WORKFLOW_TEMPLATES['narrated_explainer'];
    expect(template.steps[0].domain).toBe('speech');
    expect(template.steps[1].domain).toBe('image');
  });
});

// ---------------------------------------------------------------------------
// Tests: Execute — Capabilities
// ---------------------------------------------------------------------------

describe('Polymath Router — Capabilities', () => {
  it('returns structured capabilities report', async () => {
    const result = await execute('polymath_capabilities', {});
    expect(result.result).toBeDefined();
    expect(result.error).toBeUndefined();
    const parsed = JSON.parse(result.result!);
    expect(parsed.backends).toBeDefined();
    expect(Array.isArray(parsed.backends)).toBe(true);
    expect(parsed.workflow_templates).toBeDefined();
    expect(parsed.total_creative_tools).toBeGreaterThan(0);
    expect(parsed.supported_domains).toBeDefined();
  });

  it('capabilities include all creative backends', async () => {
    const result = await execute('polymath_capabilities', {});
    const parsed = JSON.parse(result.result!);
    const names = parsed.backends.map((b: any) => b.name);
    expect(names.some((n: string) => n.includes('ComfyUI'))).toBe(true);
    expect(names.some((n: string) => n.includes('VEO'))).toBe(true);
    expect(names.some((n: string) => n.includes('Composer') || n.includes('Audio'))).toBe(true);
    expect(names.some((n: string) => n.includes('Coding'))).toBe(true);
  });

  it('capabilities list all workflow templates', async () => {
    const result = await execute('polymath_capabilities', {});
    const parsed = JSON.parse(result.result!);
    expect(parsed.workflow_templates.length).toBe(Object.keys(WORKFLOW_TEMPLATES).length);
  });
});

// ---------------------------------------------------------------------------
// Tests: Execute — Classify
// ---------------------------------------------------------------------------

describe('Polymath Router — Classify', () => {
  it('classifies and returns routing info', async () => {
    const result = await execute('polymath_classify', { prompt: 'create a beautiful image of space' });
    expect(result.result).toBeDefined();
    const parsed = JSON.parse(result.result!);
    expect(parsed.detected_domains).toContain('image');
    expect(parsed.routing).toBeDefined();
    expect(parsed.routing[0].primary_tool).toBe('comfyui_txt2img');
  });

  it('returns error for missing prompt', async () => {
    const result = await execute('polymath_classify', {});
    expect(result.error).toBeDefined();
    expect(result.error).toContain('prompt');
  });

  it('returns error for invalid prompt type', async () => {
    const result = await execute('polymath_classify', { prompt: 123 as any });
    expect(result.error).toBeDefined();
  });

  it('detects multi-domain requests with workflow suggestion', async () => {
    const result = await execute('polymath_classify', { prompt: 'make a music video with animation' });
    const parsed = JSON.parse(result.result!);
    expect(parsed.is_multi_domain).toBe(true);
    expect(parsed.detected_domains.length).toBeGreaterThanOrEqual(2);
  });
});

// ---------------------------------------------------------------------------
// Tests: Execute — Dispatch
// ---------------------------------------------------------------------------

describe('Polymath Router — Dispatch', () => {
  it('routes image domain to comfyui_txt2img', async () => {
    const result = await execute('polymath_dispatch', { domain: 'image', prompt: 'a sunset' });
    expect(result.result).toBeDefined();
    const parsed = JSON.parse(result.result!);
    expect(parsed.primary_tool).toBe('comfyui_txt2img');
    expect(parsed.fallback_tool).toBe('openai_generate_image');
    expect(parsed.connector).toBe('comfyui');
  });

  it('routes video domain to video_generate', async () => {
    const result = await execute('polymath_dispatch', { domain: 'video', prompt: 'ocean waves' });
    const parsed = JSON.parse(result.result!);
    expect(parsed.primary_tool).toBe('video_generate');
  });

  it('routes music domain to composer_generate_music', async () => {
    const result = await execute('polymath_dispatch', { domain: 'music', prompt: 'calm piano' });
    const parsed = JSON.parse(result.result!);
    expect(parsed.primary_tool).toBe('composer_generate_music');
  });

  it('routes speech domain to composer_synthesize_speech', async () => {
    const result = await execute('polymath_dispatch', { domain: 'speech', prompt: 'hello world' });
    const parsed = JSON.parse(result.result!);
    expect(parsed.primary_tool).toBe('composer_synthesize_speech');
  });

  it('routes code domain to coding_kit_search', async () => {
    const result = await execute('polymath_dispatch', { domain: 'code', prompt: 'auth function' });
    const parsed = JSON.parse(result.result!);
    expect(parsed.primary_tool).toBe('coding_kit_search');
  });

  it('returns suggested_args for the target tool', async () => {
    const result = await execute('polymath_dispatch', {
      domain: 'image',
      prompt: 'a sunset',
      options: { width: 512, height: 512 },
    });
    const parsed = JSON.parse(result.result!);
    expect(parsed.suggested_args).toBeDefined();
    expect(parsed.suggested_args.width).toBe(512);
    expect(parsed.suggested_args.height).toBe(512);
    expect(parsed.suggested_args.prompt).toBe('a sunset');
  });

  it('returns error for unknown domain', async () => {
    const result = await execute('polymath_dispatch', { domain: 'teleportation', prompt: 'go' });
    expect(result.error).toBeDefined();
    expect(result.error).toContain('Unknown creative domain');
  });

  it('returns error for missing domain', async () => {
    const result = await execute('polymath_dispatch', { prompt: 'test' });
    expect(result.error).toBeDefined();
    expect(result.error).toContain('domain');
  });

  it('returns error for missing prompt', async () => {
    const result = await execute('polymath_dispatch', { domain: 'image' });
    expect(result.error).toBeDefined();
    expect(result.error).toContain('prompt');
  });
});

// ---------------------------------------------------------------------------
// Tests: Execute — Pipeline Create
// ---------------------------------------------------------------------------

describe('Polymath Router — Pipeline Create', () => {
  it('creates pipeline from template', async () => {
    const result = await execute('polymath_pipeline_create', { template: 'music_video' });
    expect(result.result).toBeDefined();
    const parsed = JSON.parse(result.result!);
    expect(parsed.pipeline_id).toBeDefined();
    expect(parsed.pipeline_id).toMatch(/^pipeline_/);
    expect(parsed.name).toBe('Music Video');
    expect(parsed.total_steps).toBe(4);
    expect(parsed.execution_plan).toHaveLength(4);
    expect(parsed.status).toBe('pending');
  });

  it('creates pipeline from custom steps', async () => {
    const result = await execute('polymath_pipeline_create', {
      steps: [
        { domain: 'image', prompt: 'create a logo' },
        { domain: 'video', prompt: 'animate the logo', chain_from_previous: true },
      ],
      name: 'Logo Animation',
    });
    expect(result.result).toBeDefined();
    const parsed = JSON.parse(result.result!);
    expect(parsed.pipeline_id).toBeDefined();
    expect(parsed.total_steps).toBe(2);
    expect(parsed.execution_plan[1].chains_from_previous).toBe(true);
  });

  it('applies creative_brief to all steps', async () => {
    const result = await execute('polymath_pipeline_create', {
      template: 'social_media_post',
      creative_brief: 'retro 80s aesthetic',
    });
    const parsed = JSON.parse(result.result!);
    for (const step of parsed.execution_plan) {
      expect(step.prompt).toContain('retro 80s aesthetic');
    }
  });

  it('returns error for unknown template', async () => {
    const result = await execute('polymath_pipeline_create', { template: 'nonexistent' });
    expect(result.error).toBeDefined();
    expect(result.error).toContain('Unknown workflow template');
  });

  it('returns error for missing template and steps', async () => {
    const result = await execute('polymath_pipeline_create', {});
    expect(result.error).toBeDefined();
    expect(result.error).toContain('template');
  });

  it('returns error for step with invalid domain', async () => {
    const result = await execute('polymath_pipeline_create', {
      steps: [{ domain: 'teleportation', prompt: 'go' }],
    });
    expect(result.error).toBeDefined();
    expect(result.error).toContain('invalid domain');
  });

  it('returns error for step with missing prompt', async () => {
    const result = await execute('polymath_pipeline_create', {
      steps: [{ domain: 'image' }],
    });
    expect(result.error).toBeDefined();
    expect(result.error).toContain('prompt');
  });
});

// ---------------------------------------------------------------------------
// Tests: Execute — Pipeline Status
// ---------------------------------------------------------------------------

describe('Polymath Router — Pipeline Status', () => {
  it('returns status for existing pipeline', async () => {
    // Create a pipeline first
    const createResult = await execute('polymath_pipeline_create', { template: 'game_assets' });
    const { pipeline_id } = JSON.parse(createResult.result!);

    const statusResult = await execute('polymath_pipeline_status', { pipeline_id });
    expect(statusResult.result).toBeDefined();
    const parsed = JSON.parse(statusResult.result!);
    expect(parsed.pipeline_id).toBe(pipeline_id);
    expect(parsed.status).toBe('pending');
    expect(parsed.progress_percent).toBe(0);
    expect(parsed.total_steps).toBe(3);
  });

  it('returns error for missing pipeline_id', async () => {
    const result = await execute('polymath_pipeline_status', {});
    expect(result.error).toBeDefined();
    expect(result.error).toContain('pipeline_id');
  });

  it('returns error for unknown pipeline_id', async () => {
    const result = await execute('polymath_pipeline_status', { pipeline_id: 'nonexistent_123' });
    expect(result.error).toBeDefined();
    expect(result.error).toContain('not found');
  });
});

// ---------------------------------------------------------------------------
// Tests: Execute — Pipeline List
// ---------------------------------------------------------------------------

describe('Polymath Router — Pipeline List', () => {
  it('returns list of all pipelines', async () => {
    const result = await execute('polymath_pipeline_list', {});
    expect(result.result).toBeDefined();
    const parsed = JSON.parse(result.result!);
    expect(parsed.total_pipelines).toBeGreaterThanOrEqual(0);
    expect(Array.isArray(parsed.pipelines)).toBe(true);
  });

  it('includes pipelines created in this test run', async () => {
    // Create a pipeline first
    await execute('polymath_pipeline_create', { template: 'podcast_with_intro', name: 'Test Podcast' });

    const result = await execute('polymath_pipeline_list', {});
    const parsed = JSON.parse(result.result!);
    expect(parsed.total_pipelines).toBeGreaterThanOrEqual(1);
  });
});

// ---------------------------------------------------------------------------
// Tests: Error Resilience
// ---------------------------------------------------------------------------

describe('Polymath Router — Error Resilience', () => {
  it('returns error for unknown tool name', async () => {
    const result = await execute('polymath_nonexistent', {});
    expect(result.error).toBeDefined();
    expect(result.error).toContain('Unknown polymath tool');
  });

  it('execute never throws (returns error object instead)', async () => {
    const toolNames = TOOLS.map((t) => t.name);
    for (const name of toolNames) {
      const result = await execute(name, {
        prompt: 'test',
        domain: 'image',
        pipeline_id: 'test',
      });
      expect(result).toBeDefined();
      expect(typeof result).toBe('object');
      expect(result.result !== undefined || result.error !== undefined).toBe(true);
    }
  });

  it('classify handles empty string gracefully', async () => {
    const result = await execute('polymath_classify', { prompt: '' });
    // Empty string is still a string, should return empty domains
    expect(result.error).toBeDefined();
  });

  it('dispatch with options as non-object still works', async () => {
    const result = await execute('polymath_dispatch', {
      domain: 'image',
      prompt: 'test',
      options: 'not-an-object',
    });
    // Should not crash — options gets coerced to empty
    expect(result).toBeDefined();
  });
});
