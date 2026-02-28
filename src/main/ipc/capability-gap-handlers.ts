/**
 * Capability Gap Detector IPC handlers — expose gap detection, proposal
 * lifecycle, and status queries to the renderer process.
 *
 * Track II, Phase 5: Self-Directed Capability Acquisition.
 *
 * cLaw Gate: Proposals are PRESENTED but never auto-installed. Every
 * installation decision flows through the user via the superpower UI.
 */
import { ipcMain } from 'electron';
import { capabilityGapDetector } from '../capability-gap-detector';
import type { GapDetectorState } from '../capability-gap-detector';

export function registerCapabilityGapHandlers(): void {
  // ── Record a capability gap ────────────────────────────────────────
  ipcMain.handle(
    'capability-gaps:record',
    (_event, taskDescription: string) => {
      if (!taskDescription || typeof taskDescription !== 'string') {
        throw new Error('capability-gaps:record requires a string taskDescription');
      }
      return capabilityGapDetector.recordGap(taskDescription);
    },
  );

  // ── Get top capability gaps ────────────────────────────────────────
  ipcMain.handle('capability-gaps:top', (_event, limit?: number) => {
    return capabilityGapDetector.getTopGaps(
      typeof limit === 'number' ? limit : undefined,
    );
  });

  // ── Get a single gap by ID ────────────────────────────────────────
  ipcMain.handle('capability-gaps:get', (_event, gapId: string) => {
    if (!gapId || typeof gapId !== 'string') {
      throw new Error('capability-gaps:get requires a string gapId');
    }
    return capabilityGapDetector.getGap(gapId);
  });

  // ── Generate acquisition proposals ────────────────────────────────
  ipcMain.handle('capability-gaps:generate-proposals', () => {
    return capabilityGapDetector.generateProposals();
  });

  // ── Get pending proposals ─────────────────────────────────────────
  ipcMain.handle('capability-gaps:pending-proposals', () => {
    return capabilityGapDetector.getPendingProposals();
  });

  // ── Get accepted proposals ────────────────────────────────────────
  ipcMain.handle('capability-gaps:accepted-proposals', () => {
    return capabilityGapDetector.getAcceptedProposals();
  });

  // ── Get a single proposal by ID ───────────────────────────────────
  ipcMain.handle('capability-gaps:get-proposal', (_event, proposalId: string) => {
    if (!proposalId || typeof proposalId !== 'string') {
      throw new Error('capability-gaps:get-proposal requires a string proposalId');
    }
    return capabilityGapDetector.getProposal(proposalId);
  });

  // ── Present a proposal to the user ────────────────────────────────
  ipcMain.handle('capability-gaps:present', (_event, proposalId: string) => {
    if (!proposalId || typeof proposalId !== 'string') {
      throw new Error('capability-gaps:present requires a string proposalId');
    }
    return capabilityGapDetector.presentProposal(proposalId);
  });

  // ── Accept a proposal (marks as accepted; does NOT auto-install) ──
  ipcMain.handle('capability-gaps:accept', (_event, proposalId: string) => {
    if (!proposalId || typeof proposalId !== 'string') {
      throw new Error('capability-gaps:accept requires a string proposalId');
    }
    return capabilityGapDetector.acceptProposal(proposalId);
  });

  // ── Decline a proposal ────────────────────────────────────────────
  ipcMain.handle('capability-gaps:decline', (_event, proposalId: string) => {
    if (!proposalId || typeof proposalId !== 'string') {
      throw new Error('capability-gaps:decline requires a string proposalId');
    }
    return capabilityGapDetector.declineProposal(proposalId);
  });

  // ── Mark a proposal as installed (after user-confirmed install) ───
  ipcMain.handle('capability-gaps:mark-installed', (_event, proposalId: string) => {
    if (!proposalId || typeof proposalId !== 'string') {
      throw new Error('capability-gaps:mark-installed requires a string proposalId');
    }
    return capabilityGapDetector.markInstalled(proposalId);
  });

  // ── Get prompt context (for system prompt injection) ──────────────
  ipcMain.handle('capability-gaps:prompt-context', () => {
    return capabilityGapDetector.getPromptContext();
  });

  // ── Get status summary ────────────────────────────────────────────
  ipcMain.handle('capability-gaps:status', () => {
    return capabilityGapDetector.getStatus();
  });

  // ── Export / Import (persistence) ─────────────────────────────────
  ipcMain.handle('capability-gaps:export', () => {
    return capabilityGapDetector.export();
  });

  ipcMain.handle('capability-gaps:import', (_event, data: unknown) => {
    if (!data || typeof data !== 'object') {
      throw new Error('capability-gaps:import requires a data object');
    }
    capabilityGapDetector.import(data as GapDetectorState);
  });

  // ── Maintenance: prune old gaps ───────────────────────────────────
  ipcMain.handle('capability-gaps:prune', () => {
    capabilityGapDetector.prune();
  });
}
