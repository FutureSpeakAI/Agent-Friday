/**
 * Tests for capability-gap-handlers.ts — IPC layer for Phase 5: Self-Directed
 * Capability Acquisition. Validates input validation, handler registration,
 * and correct delegation to the CapabilityGapDetector singleton.
 *
 * cLaw Gate assertion: proposals are never auto-installed via IPC.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

// ── Mock electron ────────────────────────────────────────────────────
const handlers = new Map<string, (...args: unknown[]) => unknown>();
vi.mock('electron', () => ({
  ipcMain: {
    handle: vi.fn((channel: string, handler: (...args: unknown[]) => unknown) => {
      handlers.set(channel, handler);
    }),
  },
}));

// ── Mock capability gap detector ─────────────────────────────────────
vi.mock('../../src/main/capability-gap-detector', () => ({
  capabilityGapDetector: {
    recordGap: vi.fn().mockReturnValue({ id: 'gap-1', category: 'image-processing', hitCount: 1 }),
    getTopGaps: vi.fn().mockReturnValue([]),
    getGap: vi.fn().mockReturnValue(null),
    generateProposals: vi.fn().mockReturnValue([]),
    getPendingProposals: vi.fn().mockReturnValue([]),
    getAcceptedProposals: vi.fn().mockReturnValue([]),
    getProposal: vi.fn().mockReturnValue(null),
    presentProposal: vi.fn().mockReturnValue(true),
    acceptProposal: vi.fn().mockReturnValue(true),
    declineProposal: vi.fn().mockReturnValue(true),
    markInstalled: vi.fn().mockReturnValue(true),
    getPromptContext: vi.fn().mockReturnValue(''),
    getStatus: vi.fn().mockReturnValue({ totalGaps: 0, solvableGaps: 0, impossibleGaps: 0, totalProposals: 0, pendingProposals: 0, acceptedProposals: 0, declinedProposals: 0, installedProposals: 0 }),
    export: vi.fn().mockReturnValue({ gaps: [], proposals: [] }),
    import: vi.fn(),
    prune: vi.fn(),
  },
}));

import { capabilityGapDetector } from '../../src/main/capability-gap-detector';
const mockDetector = vi.mocked(capabilityGapDetector);

import { registerCapabilityGapHandlers } from '../../src/main/ipc/capability-gap-handlers';

// ── Helper ───────────────────────────────────────────────────────────
function invoke(channel: string, ...args: unknown[]): unknown {
  const handler = handlers.get(channel);
  if (!handler) throw new Error(`No handler for ${channel}`);
  return handler({}, ...args);
}

describe('Capability Gap Handlers — Phase 5 IPC', () => {
  beforeEach(() => {
    handlers.clear();
    vi.clearAllMocks();
    registerCapabilityGapHandlers();
  });

  // ── Handler Registration ─────────────────────────────────────────
  describe('Handler Registration', () => {
    it('registers all expected IPC channels', () => {
      const expected = [
        'capability-gaps:record',
        'capability-gaps:top',
        'capability-gaps:get',
        'capability-gaps:generate-proposals',
        'capability-gaps:pending-proposals',
        'capability-gaps:accepted-proposals',
        'capability-gaps:get-proposal',
        'capability-gaps:present',
        'capability-gaps:accept',
        'capability-gaps:decline',
        'capability-gaps:mark-installed',
        'capability-gaps:prompt-context',
        'capability-gaps:status',
        'capability-gaps:export',
        'capability-gaps:import',
        'capability-gaps:prune',
      ];
      for (const channel of expected) {
        expect(handlers.has(channel), `Missing handler for ${channel}`).toBe(true);
      }
    });

    it('registers exactly 16 handlers', () => {
      expect(handlers.size).toBe(16);
    });
  });

  // ── Record Gap ──────────────────────────────────────────────────
  describe('Record Gap', () => {
    it('delegates to capabilityGapDetector.recordGap', () => {
      const result = invoke('capability-gaps:record', 'convert HEIC to PNG');
      expect(mockDetector.recordGap).toHaveBeenCalledWith('convert HEIC to PNG');
      expect(result).toEqual({ id: 'gap-1', category: 'image-processing', hitCount: 1 });
    });

    it('throws on empty task description', () => {
      expect(() => invoke('capability-gaps:record', '')).toThrow('requires a string taskDescription');
    });

    it('throws on non-string task description', () => {
      expect(() => invoke('capability-gaps:record', 42)).toThrow('requires a string taskDescription');
    });

    it('throws on missing task description', () => {
      expect(() => invoke('capability-gaps:record')).toThrow('requires a string taskDescription');
    });
  });

  // ── Top Gaps ───────────────────────────────────────────────────
  describe('Top Gaps', () => {
    it('delegates with optional limit', () => {
      invoke('capability-gaps:top', 5);
      expect(mockDetector.getTopGaps).toHaveBeenCalledWith(5);
    });

    it('defaults to undefined limit when not provided', () => {
      invoke('capability-gaps:top');
      expect(mockDetector.getTopGaps).toHaveBeenCalledWith(undefined);
    });
  });

  // ── Get Gap ────────────────────────────────────────────────────
  describe('Get Gap', () => {
    it('delegates to getGap', () => {
      invoke('capability-gaps:get', 'gap-123');
      expect(mockDetector.getGap).toHaveBeenCalledWith('gap-123');
    });

    it('throws on empty gapId', () => {
      expect(() => invoke('capability-gaps:get', '')).toThrow('requires a string gapId');
    });
  });

  // ── Proposal Generation ────────────────────────────────────────
  describe('Proposal Generation', () => {
    it('delegates to generateProposals', () => {
      invoke('capability-gaps:generate-proposals');
      expect(mockDetector.generateProposals).toHaveBeenCalled();
    });
  });

  // ── Pending / Accepted Proposals ───────────────────────────────
  describe('Proposal Queries', () => {
    it('gets pending proposals', () => {
      invoke('capability-gaps:pending-proposals');
      expect(mockDetector.getPendingProposals).toHaveBeenCalled();
    });

    it('gets accepted proposals', () => {
      invoke('capability-gaps:accepted-proposals');
      expect(mockDetector.getAcceptedProposals).toHaveBeenCalled();
    });

    it('gets a single proposal by ID', () => {
      invoke('capability-gaps:get-proposal', 'prop-1');
      expect(mockDetector.getProposal).toHaveBeenCalledWith('prop-1');
    });

    it('throws on empty proposal ID for get-proposal', () => {
      expect(() => invoke('capability-gaps:get-proposal', '')).toThrow('requires a string proposalId');
    });
  });

  // ── Proposal Lifecycle ─────────────────────────────────────────
  describe('Proposal Lifecycle', () => {
    it('presents a proposal', () => {
      invoke('capability-gaps:present', 'prop-1');
      expect(mockDetector.presentProposal).toHaveBeenCalledWith('prop-1');
    });

    it('accepts a proposal', () => {
      invoke('capability-gaps:accept', 'prop-1');
      expect(mockDetector.acceptProposal).toHaveBeenCalledWith('prop-1');
    });

    it('declines a proposal', () => {
      invoke('capability-gaps:decline', 'prop-1');
      expect(mockDetector.declineProposal).toHaveBeenCalledWith('prop-1');
    });

    it('marks a proposal as installed', () => {
      invoke('capability-gaps:mark-installed', 'prop-1');
      expect(mockDetector.markInstalled).toHaveBeenCalledWith('prop-1');
    });
  });

  // ── Input Validation for Lifecycle ─────────────────────────────
  describe('Lifecycle Input Validation', () => {
    const lifecycleChannels = [
      'capability-gaps:present',
      'capability-gaps:accept',
      'capability-gaps:decline',
      'capability-gaps:mark-installed',
    ];

    for (const channel of lifecycleChannels) {
      it(`${channel} throws on empty proposalId`, () => {
        expect(() => invoke(channel, '')).toThrow('requires a string proposalId');
      });

      it(`${channel} throws on non-string proposalId`, () => {
        expect(() => invoke(channel, 123)).toThrow('requires a string proposalId');
      });
    }
  });

  // ── Prompt Context ─────────────────────────────────────────────
  describe('Prompt Context', () => {
    it('delegates to getPromptContext', () => {
      invoke('capability-gaps:prompt-context');
      expect(mockDetector.getPromptContext).toHaveBeenCalled();
    });
  });

  // ── Status ─────────────────────────────────────────────────────
  describe('Status', () => {
    it('delegates to getStatus', () => {
      invoke('capability-gaps:status');
      expect(mockDetector.getStatus).toHaveBeenCalled();
    });
  });

  // ── Export / Import ────────────────────────────────────────────
  describe('Export / Import', () => {
    it('delegates export', () => {
      const result = invoke('capability-gaps:export');
      expect(mockDetector.export).toHaveBeenCalled();
      expect(result).toEqual({ gaps: [], proposals: [] });
    });

    it('delegates import with valid data', () => {
      invoke('capability-gaps:import', { gaps: [], proposals: [] });
      expect(mockDetector.import).toHaveBeenCalledWith({ gaps: [], proposals: [] });
    });

    it('throws on null data for import', () => {
      expect(() => invoke('capability-gaps:import', null)).toThrow('requires a data object');
    });

    it('throws on non-object data for import', () => {
      expect(() => invoke('capability-gaps:import', 'not-an-object')).toThrow('requires a data object');
    });
  });

  // ── Maintenance ────────────────────────────────────────────────
  describe('Maintenance', () => {
    it('delegates prune', () => {
      invoke('capability-gaps:prune');
      expect(mockDetector.prune).toHaveBeenCalled();
    });
  });

  // ── cLaw Gate: No Auto-Install ─────────────────────────────────
  describe('cLaw Gate: No Auto-Install via IPC', () => {
    it('accept does NOT call install — only marks status', () => {
      invoke('capability-gaps:accept', 'prop-1');
      // acceptProposal is called, but there's no install() in the handler
      expect(mockDetector.acceptProposal).toHaveBeenCalledWith('prop-1');
      // Verify no install-related methods exist on the handler
      const allChannels = Array.from(handlers.keys());
      expect(allChannels).not.toContain('capability-gaps:auto-install');
      expect(allChannels).not.toContain('capability-gaps:force-install');
    });

    it('markInstalled requires explicit call — not triggered by accept', () => {
      invoke('capability-gaps:accept', 'prop-1');
      expect(mockDetector.markInstalled).not.toHaveBeenCalled();
    });

    it('all state-modifying channels require explicit proposalId', () => {
      // These channels mutate proposal state and must validate their ID argument
      const modifyingChannels = [
        'capability-gaps:present',
        'capability-gaps:accept',
        'capability-gaps:decline',
        'capability-gaps:mark-installed',
      ];
      for (const channel of modifyingChannels) {
        expect(() => invoke(channel, ''), `${channel} should reject empty ID`).toThrow();
      }
    });
  });
});
