/**
 * cloud-gate.test.ts - Tests for CloudGate consent system (Phase H.2).
 *
 * Validates the singleton lifecycle, policy storage, IPC consent flow,
 * stats tracking, and sovereign-first behavior when no renderer is available.
 *
 * Sprint 3 H.2: The Threshold - CloudGate
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

// -- Hoisted mocks (vi.mock is hoisted, so variables must be too) -------------

const mocks = vi.hoisted(() => ({
  ipcOnce: vi.fn(),
  ipcRemoveListener: vi.fn(),
  settingsGet: vi.fn(() => ({})),
  setSetting: vi.fn(() => Promise.resolve()),
}));

// -- Mock electron (ipcMain + BrowserWindow) ----------------------------------

vi.mock('electron', () => ({
  ipcMain: {
    once: mocks.ipcOnce,
    removeListener: mocks.ipcRemoveListener,
  },
}));

// -- Mock settingsManager -----------------------------------------------------

vi.mock('../../src/main/settings', () => ({
  settingsManager: {
    get: mocks.settingsGet,
    setSetting: mocks.setSetting,
  },
}));

// -- Import under test (after mocks) -----------------------------------------

import {
  CloudGate,
  cloudGate,
  type TaskCategory,
  type PolicyScope,
  type EscalationContext,
  type GateDecision,
  type GatePolicy,
  type EscalationStats,
} from '../../src/main/cloud-gate';
import type { ConfidenceResult } from '../../src/main/confidence-assessor';

// -- Helpers ------------------------------------------------------------------

function makeContext(overrides: Partial<EscalationContext> = {}): EscalationContext {
  return {
    taskCategory: 'code',
    confidence: { score: 0.3, signals: [], escalate: true } as ConfidenceResult,
    promptPreview: 'Write a function that sorts an array of integers using quicksort',
    targetProvider: 'anthropic',
    ...overrides,
  };
}

function makeMockWindow() {
  return {
    webContents: {
      send: vi.fn(),
    },
  } as any;
}

// -- Tests --------------------------------------------------------------------

describe('CloudGate', () => {
  let gate: CloudGate;

  beforeEach(() => {
    CloudGate.resetInstance();
    gate = CloudGate.getInstance();
    vi.clearAllMocks();
    mocks.settingsGet.mockReturnValue({});
  });

  afterEach(() => {
    CloudGate.resetInstance();
  });

  // Test 1: Singleton with start()/stop() lifecycle
  it('is a singleton with start()/stop() lifecycle', () => {
    const a = CloudGate.getInstance();
    const b = CloudGate.getInstance();
    expect(a).toBe(b);

    gate.start();
    expect(gate.getStats()).toEqual({ localDelivered: 0, escalatedAllowed: 0, escalatedDenied: 0 });
    gate.stop();
    expect(gate.getStats()).toEqual({ localDelivered: 0, escalatedAllowed: 0, escalatedDenied: 0 });
  });

  // Test 2: requestEscalation returns Promise<GateDecision> with allowed boolean
  it('requestEscalation returns Promise<GateDecision> with allowed boolean', async () => {
    gate.start(); // no mainWindow = no renderer
    const context = makeContext();
    const decision = await gate.requestEscalation(context);

    expect(decision).toHaveProperty('allowed');
    expect(decision).toHaveProperty('reason');
    expect(typeof decision.allowed).toBe('boolean');
  });

  // Test 3: When no policy exists, gate emits IPC event to renderer
  it('emits IPC cloud-gate:request-consent when no policy exists', async () => {
    const mockWindow = makeMockWindow();
    gate.start(mockWindow);
    const context = makeContext();

    // Set up mocks.ipcOnce to capture the callback and simulate user response
    mocks.ipcOnce.mockImplementation((channel: string, callback: Function) => {
      // Simulate user allowing with session scope
      setTimeout(() => callback({}, { decision: 'allow', scope: 'session' }), 0);
    });

    const decision = await gate.requestEscalation(context);

    expect(mockWindow.webContents.send).toHaveBeenCalledWith(
      'cloud-gate:request-consent',
      expect.objectContaining({
        taskCategory: 'code',
        targetProvider: 'anthropic',
        responseChannel: expect.stringContaining('cloud-gate:consent-response:code:'),
      }),
    );
    expect(decision.allowed).toBe(true);
    expect(decision.reason).toBe('user-allow');
  });

  // Test 4: When policy exists with allow, returns allowed=true without IPC
  it('returns allowed=true from existing allow policy without IPC', async () => {
    gate.start(makeMockWindow());
    gate.setPolicy('code', 'allow', 'session');

    const context = makeContext();
    const decision = await gate.requestEscalation(context);

    expect(decision).toEqual({ allowed: true, reason: 'policy-allow' });
    // IPC should NOT have been called
    expect(mocks.ipcOnce).not.toHaveBeenCalled();
  });

  // Test 5: When policy exists with deny, returns allowed=false without IPC
  it('returns allowed=false from existing deny policy without IPC', async () => {
    gate.start(makeMockWindow());
    gate.setPolicy('code', 'deny', 'session');

    const context = makeContext();
    const decision = await gate.requestEscalation(context);

    expect(decision).toEqual({ allowed: false, reason: 'policy-deny' });
    expect(mocks.ipcOnce).not.toHaveBeenCalled();
  });

  // Test 6: once policy is consumed after single use
  it('once policy is consumed after single use', async () => {
    gate.start(makeMockWindow());
    gate.setPolicy('code', 'allow', 'once');

    // First use: policy applies
    const first = await gate.requestEscalation(makeContext());
    expect(first).toEqual({ allowed: true, reason: 'policy-allow' });

    // Second use: policy consumed, verify policy is gone
    expect(gate.getPolicy('code')).toBeNull();
  });

  // Test 7: session policy persists until stop() is called
  it('session policy persists until stop() is called', async () => {
    gate.start(makeMockWindow());
    gate.setPolicy('analysis', 'allow', 'session');

    // Policy persists across multiple checks
    const first = await gate.requestEscalation(makeContext({ taskCategory: 'analysis' }));
    expect(first).toEqual({ allowed: true, reason: 'policy-allow' });

    const second = await gate.requestEscalation(makeContext({ taskCategory: 'analysis' }));
    expect(second).toEqual({ allowed: true, reason: 'policy-allow' });

    expect(gate.getPolicy('analysis')).not.toBeNull();

    // stop() clears session policies
    gate.stop();
    expect(gate.getPolicy('analysis')).toBeNull();
  });

  // Test 8: always policy persists to disk (mock settings)
  it('always policy persists to disk via settingsManager', () => {
    gate.start();
    gate.setPolicy('creative', 'allow', 'always');

    // Verify setSetting was called with the policy
    expect(mocks.setSetting).toHaveBeenCalledWith(
      'cloudGatePolicies',
      expect.objectContaining({
        creative: expect.objectContaining({
          decision: 'allow',
          scope: 'always',
        }),
      }),
    );
  });

  // Test 9: getStats returns count of local, escalated, denied decisions
  it('getStats returns accurate counts of decisions', async () => {
    gate.start(makeMockWindow());

    // Set up some policies
    gate.setPolicy('code', 'allow', 'session');
    gate.setPolicy('chat', 'deny', 'session');

    // Make some escalation requests
    await gate.requestEscalation(makeContext({ taskCategory: 'code' }));
    await gate.requestEscalation(makeContext({ taskCategory: 'chat' }));
    await gate.requestEscalation(makeContext({ taskCategory: 'code' }));

    // Also test incrementStat for local delivery tracking
    gate.incrementStat('localDelivered');
    gate.incrementStat('localDelivered');

    const stats = gate.getStats();
    expect(stats.escalatedAllowed).toBe(2);
    expect(stats.escalatedDenied).toBe(1);
    expect(stats.localDelivered).toBe(2);

    // Verify stats is a copy (not a reference)
    stats.localDelivered = 999;
    expect(gate.getStats().localDelivered).toBe(2);
  });

  // Test 10: No renderer available returns denied with no-renderer reason
  it('returns denied with no-renderer reason when no mainWindow', async () => {
    gate.start(); // No mainWindow passed
    const context = makeContext();
    const decision = await gate.requestEscalation(context);

    expect(decision).toEqual({ allowed: false, reason: 'no-renderer' });
    expect(gate.getStats().escalatedDenied).toBe(1);
  });

  // Bonus: loads persisted always-policies on start
  it('loads persisted always-policies from settings on start', async () => {
    mocks.settingsGet.mockReturnValue({
      cloudGatePolicies: {
        code: { decision: 'allow', scope: 'always', createdAt: 1000 },
        chat: { decision: 'deny', scope: 'always', createdAt: 2000 },
      },
    });

    gate.start();

    const codePolicy = gate.getPolicy('code');
    expect(codePolicy).toEqual({ decision: 'allow', scope: 'always', createdAt: 1000 });

    const chatPolicy = gate.getPolicy('chat');
    expect(chatPolicy).toEqual({ decision: 'deny', scope: 'always', createdAt: 2000 });

    // Verify escalation uses persisted policy
    const decision = await gate.requestEscalation(makeContext({ taskCategory: 'code' }));
    expect(decision).toEqual({ allowed: true, reason: 'policy-allow' });
  });
});
