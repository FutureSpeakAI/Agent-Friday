/**
 * Track B, Phase 1: "The Workbench" — ToolRegistry Test Suite
 *
 * Tests the tool registration and discovery system that catalogs
 * OS-level capabilities for LLM function calling.
 *
 * Validation Criteria:
 *   1. register(definition, handler) adds a tool with its execution handler
 *   2. getDefinitions() returns all registered ToolDefinition[] for LLM context
 *   3. resolve(toolName) returns the handler function, or throws for unknown tools
 *   4. Each tool definition includes safetyLevel: 'read-only' | 'write' | 'destructive'
 *   5. getDefinitions({ safetyLevel }) filters by safety level
 *   6. At least 5 tools registered at startup
 *   7. Tool definitions conform to ToolDefinition type from llm-client.ts
 *   8. toolRegistry is a singleton export
 *   9. Duplicate tool name registration throws
 */
import { describe, it, expect, beforeEach } from 'vitest';

import {
  ToolRegistry,
  toolRegistry,
  type ToolRegistryDefinition,
  type ToolHandler,
} from '../../src/main/tool-registry';

// ── Helpers ───────────────────────────────────────────────────────────

function makeToolDef(overrides: Partial<ToolRegistryDefinition> = {}): ToolRegistryDefinition {
  return {
    name: 'test_tool',
    description: 'A test tool',
    input_schema: { type: 'object', properties: {} },
    safetyLevel: 'read-only',
    ...overrides,
  };
}

const noopHandler: ToolHandler = async () => 'ok';

// ── Test Suite ─────────────────────────────────────────────────────────

describe('ToolRegistry — Track B Phase 1', () => {
  let registry: ToolRegistry;

  beforeEach(() => {
    registry = new ToolRegistry();
  });

  // ── Criterion 1: register() adds a tool ────────────────────────────

  describe('Criterion 1: register(definition, handler)', () => {
    it('should register a tool successfully', () => {
      registry.register(makeToolDef(), noopHandler);
      expect(registry.getDefinitions()).toHaveLength(1);
    });

    it('should store the handler for later resolution', () => {
      const handler: ToolHandler = async (input) => `received: ${JSON.stringify(input)}`;
      registry.register(makeToolDef({ name: 'my_tool' }), handler);
      expect(registry.resolve('my_tool')).toBe(handler);
    });
  });

  // ── Criterion 2: getDefinitions() returns ToolDefinition[] ─────────

  describe('Criterion 2: getDefinitions() returns all tools', () => {
    it('should return empty array when no tools registered', () => {
      expect(registry.getDefinitions()).toEqual([]);
    });

    it('should return all registered tools', () => {
      registry.register(makeToolDef({ name: 'tool_a' }), noopHandler);
      registry.register(makeToolDef({ name: 'tool_b' }), noopHandler);
      registry.register(makeToolDef({ name: 'tool_c' }), noopHandler);

      const defs = registry.getDefinitions();
      expect(defs).toHaveLength(3);
      expect(defs.map(d => d.name)).toEqual(['tool_a', 'tool_b', 'tool_c']);
    });

    it('should return definitions conforming to ToolDefinition shape', () => {
      registry.register(makeToolDef({
        name: 'file_search',
        description: 'Search files',
        input_schema: {
          type: 'object',
          properties: { query: { type: 'string' } },
          required: ['query'],
        },
      }), noopHandler);

      const def = registry.getDefinitions()[0];
      expect(def).toHaveProperty('name', 'file_search');
      expect(def).toHaveProperty('description', 'Search files');
      expect(def).toHaveProperty('input_schema');
    });
  });

  // ── Criterion 3: resolve() returns handler or throws ───────────────

  describe('Criterion 3: resolve(toolName)', () => {
    it('should return the handler function for a registered tool', () => {
      const handler: ToolHandler = async () => 'result';
      registry.register(makeToolDef({ name: 'search' }), handler);
      expect(registry.resolve('search')).toBe(handler);
    });

    it('should throw for an unknown tool name', () => {
      expect(() => registry.resolve('nonexistent')).toThrow();
    });

    it('should throw with a descriptive error message', () => {
      expect(() => registry.resolve('ghost_tool')).toThrow(/ghost_tool/);
    });
  });

  // ── Criterion 4: safetyLevel on each definition ────────────────────

  describe('Criterion 4: safetyLevel classification', () => {
    it('should store safetyLevel on each tool definition', () => {
      registry.register(makeToolDef({ name: 'reader', safetyLevel: 'read-only' }), noopHandler);
      registry.register(makeToolDef({ name: 'writer', safetyLevel: 'write' }), noopHandler);
      registry.register(makeToolDef({ name: 'deleter', safetyLevel: 'destructive' }), noopHandler);

      const defs = registry.getDefinitions();
      expect(defs[0].safetyLevel).toBe('read-only');
      expect(defs[1].safetyLevel).toBe('write');
      expect(defs[2].safetyLevel).toBe('destructive');
    });
  });

  // ── Criterion 5: getDefinitions({ safetyLevel }) filters ───────────

  describe('Criterion 5: filter by safetyLevel', () => {
    it('should return only tools matching the specified safety level', () => {
      registry.register(makeToolDef({ name: 'reader_1', safetyLevel: 'read-only' }), noopHandler);
      registry.register(makeToolDef({ name: 'writer_1', safetyLevel: 'write' }), noopHandler);
      registry.register(makeToolDef({ name: 'reader_2', safetyLevel: 'read-only' }), noopHandler);
      registry.register(makeToolDef({ name: 'danger_1', safetyLevel: 'destructive' }), noopHandler);

      const readOnly = registry.getDefinitions({ safetyLevel: 'read-only' });
      expect(readOnly).toHaveLength(2);
      expect(readOnly.every(d => d.safetyLevel === 'read-only')).toBe(true);

      const writeOnly = registry.getDefinitions({ safetyLevel: 'write' });
      expect(writeOnly).toHaveLength(1);
      expect(writeOnly[0].name).toBe('writer_1');
    });

    it('should return all tools when no filter specified', () => {
      registry.register(makeToolDef({ name: 'a', safetyLevel: 'read-only' }), noopHandler);
      registry.register(makeToolDef({ name: 'b', safetyLevel: 'write' }), noopHandler);
      expect(registry.getDefinitions()).toHaveLength(2);
    });
  });

  // ── Criterion 6: 5+ startup tools ──────────────────────────────────

  describe('Criterion 6: startup tool registration', () => {
    it('should have at least 5 tools registered in the singleton', () => {
      expect(toolRegistry.getDefinitions().length).toBeGreaterThanOrEqual(5);
    });

    it('should include file_search, list_directory, system_stats, system_processes, weather_current', () => {
      const names = toolRegistry.getDefinitions().map(d => d.name);
      expect(names).toContain('file_search');
      expect(names).toContain('list_directory');
      expect(names).toContain('system_stats');
      expect(names).toContain('system_processes');
      expect(names).toContain('weather_current');
    });
  });

  // ── Criterion 7: conforms to ToolDefinition ────────────────────────

  describe('Criterion 7: ToolDefinition conformance', () => {
    it('should produce definitions with name and input_schema', () => {
      const defs = toolRegistry.getDefinitions();
      for (const def of defs) {
        expect(def.name).toBeTypeOf('string');
        expect(def.name.length).toBeGreaterThan(0);
        expect(def).toHaveProperty('input_schema');
      }
    });

    it('should produce definitions with description', () => {
      const defs = toolRegistry.getDefinitions();
      for (const def of defs) {
        expect(def.description).toBeTypeOf('string');
        expect(def.description!.length).toBeGreaterThan(0);
      }
    });
  });

  // ── Criterion 8: singleton export ──────────────────────────────────

  describe('Criterion 8: singleton export', () => {
    it('should export toolRegistry as a ToolRegistry instance', () => {
      expect(toolRegistry).toBeInstanceOf(ToolRegistry);
    });
  });

  // ── Criterion 9: duplicate registration throws ─────────────────────

  describe('Criterion 9: duplicate tool name throws', () => {
    it('should throw when registering a tool with a name that already exists', () => {
      registry.register(makeToolDef({ name: 'duplicate_tool' }), noopHandler);
      expect(() => {
        registry.register(makeToolDef({ name: 'duplicate_tool' }), noopHandler);
      }).toThrow(/duplicate_tool/);
    });

    it('should not add the duplicate tool', () => {
      registry.register(makeToolDef({ name: 'only_one' }), noopHandler);
      try {
        registry.register(makeToolDef({ name: 'only_one' }), noopHandler);
      } catch { /* expected */ }
      expect(registry.getDefinitions()).toHaveLength(1);
    });
  });
});
