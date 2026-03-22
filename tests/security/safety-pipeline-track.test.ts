/**
 * Track B, Phase 2: "The Guardrails" — SafetyPipeline Test Suite
 *
 * Tests the safety gate that sits between LLM tool calls and
 * actual tool execution. Decisions: approved | denied | pending.
 *
 * Validation Criteria:
 *   1. evaluate(toolCall) returns SafetyDecision with status
 *   2. Read-only tools auto-approved
 *   3. Write tools return 'pending' with confirmation prompt
 *   4. Destructive tools return 'pending' with warning message
 *   5. Unknown tool names return 'denied'
 *   6. confirm(decisionId) upgrades pending to approved
 *   7. deny(decisionId) upgrades pending to denied
 *   8. Pending decisions expire after 60s → auto-deny
 *   9. getPolicy() returns current safety policy
 *  10. Decisions include the original tool call
 *  11. safetyPipeline is a singleton export
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ── Hoisted mocks ─────────────────────────────────────────────────────

const mocks = vi.hoisted(() => {
  const tools = new Map<string, { definition: { name: string; safetyLevel: string }; handler: () => Promise<string> }>();

  return {
    tools,
    resolve: vi.fn((name: string) => {
      const entry = tools.get(name);
      if (!entry) throw new Error(`Unknown tool "${name}"`);
      return entry.handler;
    }),
    getDefinitions: vi.fn(() => Array.from(tools.values()).map(t => t.definition)),
  };
});

vi.mock('../../src/main/tool-registry', () => ({
  toolRegistry: {
    resolve: mocks.resolve,
    getDefinitions: mocks.getDefinitions,
  },
  ToolRegistry: class {},
}));

import {
  SafetyPipeline,
  safetyPipeline,
  type SafetyDecision,
} from '../../src/main/safety-pipeline';
import type { ToolCall } from '../../src/main/llm-client';

// ── Helpers ───────────────────────────────────────────────────────────

function registerMockTool(name: string, safetyLevel: string): void {
  mocks.tools.set(name, {
    definition: { name, safetyLevel },
    handler: async () => 'mock result',
  });
}

function makeToolCall(name: string, input: unknown = {}): ToolCall {
  return { id: `tc-${Date.now()}`, type: 'tool_use', name, input };
}

// ── Test Suite ─────────────────────────────────────────────────────────

describe('SafetyPipeline — Track B Phase 2', () => {
  let pipeline: SafetyPipeline;

  beforeEach(() => {
    vi.clearAllMocks();
    mocks.tools.clear();
    registerMockTool('file_search', 'read-only');
    registerMockTool('write_file', 'write');
    registerMockTool('delete_file', 'destructive');
    pipeline = new SafetyPipeline();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  // ── Criterion 1: evaluate() returns SafetyDecision ─────────────────

  describe('Criterion 1: evaluate returns SafetyDecision', () => {
    it('should return a decision with status, id, and toolCall', () => {
      const tc = makeToolCall('file_search');
      const decision = pipeline.evaluate(tc);
      expect(decision.status).toBeTypeOf('string');
      expect(['approved', 'denied', 'pending']).toContain(decision.status);
      expect(decision.id).toBeTypeOf('string');
      expect(decision.toolCall).toBe(tc);
    });
  });

  // ── Criterion 2: read-only auto-approved ───────────────────────────

  describe('Criterion 2: read-only tools auto-approved', () => {
    it('should auto-approve read-only tools', () => {
      const decision = pipeline.evaluate(makeToolCall('file_search'));
      expect(decision.status).toBe('approved');
    });
  });

  // ── Criterion 3: write tools return pending ────────────────────────

  describe('Criterion 3: write tools return pending', () => {
    it('should return pending for write tools', () => {
      const decision = pipeline.evaluate(makeToolCall('write_file'));
      expect(decision.status).toBe('pending');
    });

    it('should include a confirmation prompt', () => {
      const decision = pipeline.evaluate(makeToolCall('write_file'));
      expect(decision.message).toBeTypeOf('string');
      expect(decision.message!.length).toBeGreaterThan(0);
    });
  });

  // ── Criterion 4: destructive tools return pending with warning ─────

  describe('Criterion 4: destructive tools return pending with warning', () => {
    it('should return pending for destructive tools', () => {
      const decision = pipeline.evaluate(makeToolCall('delete_file'));
      expect(decision.status).toBe('pending');
    });

    it('should include a warning message', () => {
      const decision = pipeline.evaluate(makeToolCall('delete_file'));
      expect(decision.message).toBeTypeOf('string');
      expect(decision.message!.toLowerCase()).toMatch(/warning|destructive|danger/);
    });
  });

  // ── Criterion 5: unknown tools denied ──────────────────────────────

  describe('Criterion 5: unknown tools denied', () => {
    it('should deny unknown tool names', () => {
      const decision = pipeline.evaluate(makeToolCall('nonexistent_tool'));
      expect(decision.status).toBe('denied');
    });

    it('should include reason mentioning unknown tool', () => {
      const decision = pipeline.evaluate(makeToolCall('ghost'));
      expect(decision.reason).toBeTypeOf('string');
      expect(decision.reason!.toLowerCase()).toMatch(/unknown/);
    });
  });

  // ── Criterion 6: confirm() upgrades pending → approved ─────────────

  describe('Criterion 6: confirm() upgrades pending to approved', () => {
    it('should upgrade a pending decision to approved', () => {
      const decision = pipeline.evaluate(makeToolCall('write_file'));
      expect(decision.status).toBe('pending');

      const confirmed = pipeline.confirm(decision.id);
      expect(confirmed).toBe(true);
      expect(pipeline.getDecision(decision.id)?.status).toBe('approved');
    });

    it('should return false for non-existent decision', () => {
      expect(pipeline.confirm('fake-id')).toBe(false);
    });
  });

  // ── Criterion 7: deny() upgrades pending → denied ──────────────────

  describe('Criterion 7: deny() upgrades pending to denied', () => {
    it('should upgrade a pending decision to denied', () => {
      const decision = pipeline.evaluate(makeToolCall('delete_file'));
      expect(decision.status).toBe('pending');

      const denied = pipeline.deny(decision.id);
      expect(denied).toBe(true);
      expect(pipeline.getDecision(decision.id)?.status).toBe('denied');
    });

    it('should return false for non-existent decision', () => {
      expect(pipeline.deny('fake-id')).toBe(false);
    });
  });

  // ── Criterion 8: pending decisions expire after 60s ────────────────

  describe('Criterion 8: pending decisions expire', () => {
    it('should auto-deny pending decisions after 60 seconds', () => {
      vi.useFakeTimers();

      const decision = pipeline.evaluate(makeToolCall('write_file'));
      expect(decision.status).toBe('pending');

      vi.advanceTimersByTime(61_000);

      expect(pipeline.getDecision(decision.id)?.status).toBe('denied');
    });

    it('should not expire already-confirmed decisions', () => {
      vi.useFakeTimers();

      const decision = pipeline.evaluate(makeToolCall('write_file'));
      pipeline.confirm(decision.id);

      vi.advanceTimersByTime(61_000);

      expect(pipeline.getDecision(decision.id)?.status).toBe('approved');
    });
  });

  // ── Criterion 9: getPolicy() returns safety policy ─────────────────

  describe('Criterion 9: getPolicy() inspection', () => {
    it('should return the current safety policy', () => {
      const policy = pipeline.getPolicy();
      expect(policy).toBeDefined();
      expect(policy).toHaveProperty('autoApprove');
      expect(policy).toHaveProperty('pendingTimeoutMs');
    });

    it('should show read-only as auto-approved', () => {
      const policy = pipeline.getPolicy();
      expect(policy.autoApprove).toContain('read-only');
    });
  });

  // ── Criterion 10: decisions include original tool call ─────────────

  describe('Criterion 10: decisions include original tool call', () => {
    it('should carry the original tool call in the decision', () => {
      const tc = makeToolCall('file_search', { query: 'test' });
      const decision = pipeline.evaluate(tc);
      expect(decision.toolCall).toBe(tc);
      expect(decision.toolCall.name).toBe('file_search');
      expect(decision.toolCall.input).toEqual({ query: 'test' });
    });
  });

  // ── Criterion 11: singleton export ─────────────────────────────────

  describe('Criterion 11: singleton export', () => {
    it('should export safetyPipeline as a SafetyPipeline instance', () => {
      expect(safetyPipeline).toBeInstanceOf(SafetyPipeline);
    });
  });
});
