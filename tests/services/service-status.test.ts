/**
 * Service Status — Tests for graceful degradation tracking.
 *
 * Validates:
 *   1. Services transition through online → degraded → offline
 *   2. System mode reflects worst-case service state
 *   3. Offline capabilities are correctly reported
 *   4. Recovery (markOnline) resets failure state
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { ServiceStatusManager } from '../../src/main/service-status';

// Use a fresh instance for each test (not the singleton)
function createManager() {
  // We need to instantiate a fresh manager. Import the class.
  // Since the module exports a singleton, we construct via the class pattern.
  return new ServiceStatusManager();
}

describe('Service Status — Graceful Degradation', () => {
  let manager: ServiceStatusManager;

  beforeEach(() => {
    manager = createManager();
  });

  describe('service state transitions', () => {
    it('should default to online for unknown services', () => {
      expect(manager.getServiceState('gemini')).toBe('online');
    });

    it('should mark service as online', () => {
      manager.register('gemini');
      manager.markOnline('gemini');
      expect(manager.getServiceState('gemini')).toBe('online');
    });

    it('should transition to DEGRADED after 1 failure', () => {
      manager.markFailed('gemini', 'connection lost');
      expect(manager.getServiceState('gemini')).toBe('degraded');
    });

    it('should transition to OFFLINE after 3 consecutive failures', () => {
      manager.markFailed('gemini', 'connection lost 1');
      manager.markFailed('gemini', 'connection lost 2');
      manager.markFailed('gemini', 'connection lost 3');
      expect(manager.getServiceState('gemini')).toBe('offline');
    });

    it('should RECOVER to online when markOnline called', () => {
      manager.markFailed('gemini', 'fail 1');
      manager.markFailed('gemini', 'fail 2');
      manager.markFailed('gemini', 'fail 3');
      expect(manager.getServiceState('gemini')).toBe('offline');

      manager.markOnline('gemini');
      expect(manager.getServiceState('gemini')).toBe('online');
    });

    it('should reset consecutive failures on recovery', () => {
      manager.markFailed('claude', 'fail 1');
      manager.markFailed('claude', 'fail 2');
      manager.markOnline('claude');
      // After recovery, 1 failure should be degraded (not offline)
      manager.markFailed('claude', 'fail again');
      expect(manager.getServiceState('claude')).toBe('degraded');
    });
  });

  describe('system mode', () => {
    it('should report FULL mode when all services are online', () => {
      manager.markOnline('gemini');
      manager.markOnline('claude');
      const status = manager.getSystemStatus();
      expect(status.mode).toBe('full');
    });

    it('should report DEGRADED mode when any service is degraded', () => {
      manager.markOnline('gemini');
      manager.markFailed('claude', 'timeout');
      const status = manager.getSystemStatus();
      expect(status.mode).toBe('degraded');
    });

    it('should report OFFLINE mode when any service is offline', () => {
      manager.markOnline('gemini');
      manager.markFailed('claude', 'fail 1');
      manager.markFailed('claude', 'fail 2');
      manager.markFailed('claude', 'fail 3');
      const status = manager.getSystemStatus();
      expect(status.mode).toBe('offline');
    });

    it('should report OFFLINE mode even if other services are online', () => {
      manager.markOnline('gemini');
      manager.markOnline('openrouter');
      manager.markFailed('mcp', 'crash 1');
      manager.markFailed('mcp', 'crash 2');
      manager.markFailed('mcp', 'crash 3');
      const status = manager.getSystemStatus();
      expect(status.mode).toBe('offline');
    });
  });

  describe('offline capabilities', () => {
    it('should always include local capabilities in offlineCapabilities', () => {
      const status = manager.getSystemStatus();
      expect(status.offlineCapabilities).toContain('Memory browsing (local files)');
      expect(status.offlineCapabilities).toContain('Settings management');
      expect(status.offlineCapabilities).toContain('Integrity monitoring');
    });

    it('should list unavailable capabilities when services are offline', () => {
      manager.markFailed('gemini', 'fail 1');
      manager.markFailed('gemini', 'fail 2');
      manager.markFailed('gemini', 'fail 3');
      const status = manager.getSystemStatus();
      expect(status.unavailableCapabilities).toContain('Voice conversations');
      expect(status.unavailableCapabilities).toContain('Real-time audio');
    });

    it('should not list capabilities as unavailable when service is only degraded', () => {
      manager.markFailed('claude', 'timeout once');
      const status = manager.getSystemStatus();
      expect(status.unavailableCapabilities).not.toContain('Agent orchestration');
    });
  });

  describe('summary', () => {
    it('should report "All systems operational" when full', () => {
      manager.markOnline('gemini');
      expect(manager.getSummary()).toContain('All systems operational');
    });

    it('should report offline services in summary', () => {
      manager.markFailed('gemini', 'fail 1');
      manager.markFailed('gemini', 'fail 2');
      manager.markFailed('gemini', 'fail 3');
      const summary = manager.getSummary();
      expect(summary).toContain('gemini');
      expect(summary).toContain('Offline');
    });

    it('should mention available capabilities in degraded summary', () => {
      manager.markFailed('claude', 'fail 1');
      manager.markFailed('claude', 'fail 2');
      manager.markFailed('claude', 'fail 3');
      const summary = manager.getSummary();
      expect(summary).toContain('Available');
    });
  });
});
