/**
 * VoiceStateMachine — Unit tests for the canonical voice pipeline state machine.
 *
 * Tests valid transitions, invalid transition rejection, event emissions,
 * guard registration, lifecycle hooks, health metric tracking, and cleanup.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
  VoiceStateMachine,
  type VoiceState,
  type HealthMetrics,
  type ErrorCategory,
} from '../../src/main/voice/voice-state-machine';

describe('VoiceStateMachine', () => {
  let sm: VoiceStateMachine;

  beforeEach(() => {
    VoiceStateMachine.resetInstance();
    vi.clearAllMocks();
    sm = VoiceStateMachine.getInstance();
  });

  afterEach(() => {
    VoiceStateMachine.resetInstance();
    vi.restoreAllMocks();
  });

  describe('Singleton', () => {
    it('getInstance returns the same instance', () => {
      expect(VoiceStateMachine.getInstance()).toBe(sm);
    });

    it('resetInstance clears the singleton', () => {
      const before = VoiceStateMachine.getInstance();
      VoiceStateMachine.resetInstance();
      const after = VoiceStateMachine.getInstance();
      expect(before).not.toBe(after);
    });
  });

  describe('Initial state', () => {
    it('starts in IDLE', () => {
      expect(sm.getState()).toBe('IDLE');
    });

    it('is not destroyed initially', () => {
      expect(sm.isDestroyed()).toBe(false);
    });

    it('transition log is empty', () => {
      expect(sm.getTransitionLog()).toHaveLength(0);
    });

    it('uptime is near zero initially', () => {
      expect(sm.getUptime()).toBeLessThan(100);
    });
  });

  describe('Valid transitions', () => {
    it('IDLE → REQUESTING_MIC', () => {
      expect(sm.canTransition('REQUESTING_MIC')).toBe(true);
      expect(sm.transition('REQUESTING_MIC', 'User clicked voice')).toBe(true);
      expect(sm.getState()).toBe('REQUESTING_MIC');
    });

    it('IDLE → CONNECTING_CLOUD (direct, mic already granted)', () => {
      expect(sm.canTransition('CONNECTING_CLOUD')).toBe(true);
      expect(sm.transition('CONNECTING_CLOUD', 'Mic pre-granted')).toBe(true);
      expect(sm.getState()).toBe('CONNECTING_CLOUD');
    });

    it('IDLE → CONNECTING_LOCAL (direct)', () => {
      expect(sm.transition('CONNECTING_LOCAL', 'Local voice')).toBe(true);
      expect(sm.getState()).toBe('CONNECTING_LOCAL');
    });

    it('IDLE → TEXT_FALLBACK', () => {
      expect(sm.transition('TEXT_FALLBACK', 'No voice wanted')).toBe(true);
      expect(sm.getState()).toBe('TEXT_FALLBACK');
    });

    it('REQUESTING_MIC → MIC_GRANTED', () => {
      sm.transition('REQUESTING_MIC', 'test');
      expect(sm.transition('MIC_GRANTED', 'User allowed mic')).toBe(true);
      expect(sm.getState()).toBe('MIC_GRANTED');
    });

    it('REQUESTING_MIC → MIC_DENIED', () => {
      sm.transition('REQUESTING_MIC', 'test');
      expect(sm.transition('MIC_DENIED', 'User denied mic')).toBe(true);
      expect(sm.getState()).toBe('MIC_DENIED');
    });

    it('MIC_GRANTED → CONNECTING_CLOUD', () => {
      sm.transition('REQUESTING_MIC', 'test');
      sm.transition('MIC_GRANTED', 'test');
      expect(sm.transition('CONNECTING_CLOUD', 'Cloud path chosen')).toBe(true);
    });

    it('MIC_GRANTED → CONNECTING_LOCAL', () => {
      sm.transition('REQUESTING_MIC', 'test');
      sm.transition('MIC_GRANTED', 'test');
      expect(sm.transition('CONNECTING_LOCAL', 'Local path chosen')).toBe(true);
    });

    it('CONNECTING_CLOUD → CLOUD_ACTIVE', () => {
      sm.transition('CONNECTING_CLOUD', 'test');
      expect(sm.transition('CLOUD_ACTIVE', 'Audio flowing')).toBe(true);
    });

    it('CONNECTING_CLOUD → CONNECTING_LOCAL (fallback)', () => {
      sm.transition('CONNECTING_CLOUD', 'test');
      expect(sm.transition('CONNECTING_LOCAL', 'Cloud failed, trying local')).toBe(true);
    });

    it('CLOUD_ACTIVE → CLOUD_DEGRADED', () => {
      sm.transition('CONNECTING_CLOUD', 'test');
      sm.transition('CLOUD_ACTIVE', 'test');
      expect(sm.transition('CLOUD_DEGRADED', 'Jitter detected')).toBe(true);
    });

    it('CLOUD_DEGRADED → CLOUD_ACTIVE (recovery)', () => {
      sm.transition('CONNECTING_CLOUD', 'test');
      sm.transition('CLOUD_ACTIVE', 'test');
      sm.transition('CLOUD_DEGRADED', 'test');
      expect(sm.transition('CLOUD_ACTIVE', 'Audio recovered')).toBe(true);
    });

    it('CLOUD_DEGRADED → CONNECTING_LOCAL (fallback)', () => {
      sm.transition('CONNECTING_CLOUD', 'test');
      sm.transition('CLOUD_ACTIVE', 'test');
      sm.transition('CLOUD_DEGRADED', 'test');
      expect(sm.transition('CONNECTING_LOCAL', 'Falling back')).toBe(true);
    });

    it('CONNECTING_LOCAL → LOCAL_ACTIVE', () => {
      sm.transition('CONNECTING_LOCAL', 'test');
      expect(sm.transition('LOCAL_ACTIVE', 'Pipeline flowing')).toBe(true);
    });

    it('LOCAL_ACTIVE → LOCAL_DEGRADED', () => {
      sm.transition('CONNECTING_LOCAL', 'test');
      sm.transition('LOCAL_ACTIVE', 'test');
      expect(sm.transition('LOCAL_DEGRADED', 'TTS died')).toBe(true);
    });

    it('LOCAL_DEGRADED → TEXT_FALLBACK', () => {
      sm.transition('CONNECTING_LOCAL', 'test');
      sm.transition('LOCAL_ACTIVE', 'test');
      sm.transition('LOCAL_DEGRADED', 'test');
      expect(sm.transition('TEXT_FALLBACK', 'All local components failed')).toBe(true);
    });

    it('CLOUD_ACTIVE → DISCONNECTING', () => {
      sm.transition('CONNECTING_CLOUD', 'test');
      sm.transition('CLOUD_ACTIVE', 'test');
      expect(sm.transition('DISCONNECTING', 'User stopped')).toBe(true);
    });

    it('DISCONNECTING → IDLE', () => {
      sm.transition('CONNECTING_CLOUD', 'test');
      sm.transition('CLOUD_ACTIVE', 'test');
      sm.transition('DISCONNECTING', 'test');
      expect(sm.transition('IDLE', 'Cleanup complete')).toBe(true);
    });

    it('TEXT_FALLBACK → IDLE', () => {
      sm.transition('TEXT_FALLBACK', 'test');
      expect(sm.transition('IDLE', 'User closed')).toBe(true);
    });

    it('TEXT_FALLBACK → REQUESTING_MIC (retry)', () => {
      sm.transition('TEXT_FALLBACK', 'test');
      expect(sm.transition('REQUESTING_MIC', 'User retrying voice')).toBe(true);
    });

    it('ERROR → IDLE', () => {
      sm.transition('CONNECTING_CLOUD', 'test');
      sm.transition('ERROR', 'Fatal crash');
      expect(sm.transition('IDLE', 'User dismissed error')).toBe(true);
    });

    it('ERROR → TEXT_FALLBACK', () => {
      sm.transition('CONNECTING_CLOUD', 'test');
      sm.transition('ERROR', 'Fatal crash');
      expect(sm.transition('TEXT_FALLBACK', 'Fall to text')).toBe(true);
    });
  });

  describe('ERROR reachable from any active state', () => {
    const activeStates: VoiceState[] = [
      'REQUESTING_MIC', 'MIC_GRANTED', 'CONNECTING_CLOUD', 'CLOUD_ACTIVE',
      'CLOUD_DEGRADED', 'CONNECTING_LOCAL', 'LOCAL_ACTIVE', 'LOCAL_DEGRADED',
      'TEXT_FALLBACK', 'DISCONNECTING',
    ];

    for (const state of activeStates) {
      it(`${state} → ERROR is allowed`, () => {
        // Navigate to the target state first
        const fresh = (() => {
          VoiceStateMachine.resetInstance();
          return VoiceStateMachine.getInstance();
        })();

        // Use a simple path to reach each state
        const paths: Record<string, VoiceState[]> = {
          'REQUESTING_MIC': ['REQUESTING_MIC'],
          'MIC_GRANTED': ['REQUESTING_MIC', 'MIC_GRANTED'],
          'CONNECTING_CLOUD': ['CONNECTING_CLOUD'],
          'CLOUD_ACTIVE': ['CONNECTING_CLOUD', 'CLOUD_ACTIVE'],
          'CLOUD_DEGRADED': ['CONNECTING_CLOUD', 'CLOUD_ACTIVE', 'CLOUD_DEGRADED'],
          'CONNECTING_LOCAL': ['CONNECTING_LOCAL'],
          'LOCAL_ACTIVE': ['CONNECTING_LOCAL', 'LOCAL_ACTIVE'],
          'LOCAL_DEGRADED': ['CONNECTING_LOCAL', 'LOCAL_ACTIVE', 'LOCAL_DEGRADED'],
          'TEXT_FALLBACK': ['TEXT_FALLBACK'],
          'DISCONNECTING': ['CONNECTING_CLOUD', 'CLOUD_ACTIVE', 'DISCONNECTING'],
        };

        for (const step of paths[state] ?? []) {
          fresh.transition(step, 'setup');
        }

        expect(fresh.getState()).toBe(state);
        expect(fresh.canTransition('ERROR')).toBe(true);
        expect(fresh.transition('ERROR', 'Fatal failure')).toBe(true);
        expect(fresh.getState()).toBe('ERROR');

        fresh.destroy();
      });
    }
  });

  describe('Invalid transitions', () => {
    it('IDLE → CLOUD_ACTIVE is rejected (must go through CONNECTING)', () => {
      expect(sm.canTransition('CLOUD_ACTIVE')).toBe(false);
      expect(sm.transition('CLOUD_ACTIVE', 'shortcut attempt')).toBe(false);
      expect(sm.getState()).toBe('IDLE');
    });

    it('IDLE → LOCAL_ACTIVE is rejected', () => {
      expect(sm.canTransition('LOCAL_ACTIVE')).toBe(false);
    });

    it('IDLE → DISCONNECTING is rejected', () => {
      expect(sm.canTransition('DISCONNECTING')).toBe(false);
    });

    it('CLOUD_ACTIVE → IDLE is rejected (must go through DISCONNECTING)', () => {
      sm.transition('CONNECTING_CLOUD', 'test');
      sm.transition('CLOUD_ACTIVE', 'test');
      expect(sm.canTransition('IDLE')).toBe(false);
    });

    it('IDLE → ERROR is rejected (IDLE is not in ERROR_ALWAYS_ALLOWED_FROM)', () => {
      expect(sm.canTransition('ERROR')).toBe(false);
    });

    it('self-transition is a no-op', () => {
      expect(sm.transition('IDLE', 'same state')).toBe(false);
      expect(sm.getState()).toBe('IDLE');
    });
  });

  describe('Event emissions', () => {
    it('emits state-change on successful transition', () => {
      const handler = vi.fn();
      sm.on('state-change', handler);
      sm.transition('REQUESTING_MIC', 'User clicked voice');
      expect(handler).toHaveBeenCalledOnce();
      expect(handler).toHaveBeenCalledWith({
        from: 'IDLE',
        to: 'REQUESTING_MIC',
        reason: 'User clicked voice',
      });
    });

    it('does not emit state-change on rejected transition', () => {
      const handler = vi.fn();
      sm.on('state-change', handler);
      sm.transition('CLOUD_ACTIVE', 'illegal');
      expect(handler).not.toHaveBeenCalled();
    });

    it('emitError sends error event without changing state', () => {
      sm.transition('CONNECTING_CLOUD', 'test');
      const handler = vi.fn();
      sm.on('error', handler);
      const error = new Error('Dropped frame');
      sm.emitError(error, 'audio-hardware');
      expect(handler).toHaveBeenCalledWith({
        state: 'CONNECTING_CLOUD',
        error,
        category: 'audio-hardware',
      });
      // State should NOT change
      expect(sm.getState()).toBe('CONNECTING_CLOUD');
    });
  });

  describe('Guards', () => {
    it('guard can block a transition', () => {
      const guard = vi.fn().mockReturnValue(false);
      sm.setGuard('IDLE', 'REQUESTING_MIC', guard);
      expect(sm.transition('REQUESTING_MIC', 'guarded')).toBe(false);
      expect(sm.getState()).toBe('IDLE');
      expect(guard).toHaveBeenCalledOnce();
    });

    it('guard can allow a transition', () => {
      const guard = vi.fn().mockReturnValue(true);
      sm.setGuard('IDLE', 'REQUESTING_MIC', guard);
      expect(sm.transition('REQUESTING_MIC', 'allowed')).toBe(true);
      expect(sm.getState()).toBe('REQUESTING_MIC');
    });

    it('unsubscribe removes the guard', () => {
      const guard = vi.fn().mockReturnValue(false);
      const unsub = sm.setGuard('IDLE', 'REQUESTING_MIC', guard);
      unsub();
      expect(sm.transition('REQUESTING_MIC', 'no guard')).toBe(true);
      expect(guard).not.toHaveBeenCalled();
    });

    it('canTransition does not check guards (UI can show possibilities)', () => {
      sm.setGuard('IDLE', 'REQUESTING_MIC', () => false);
      // canTransition should still return true (structural check only)
      expect(sm.canTransition('REQUESTING_MIC')).toBe(true);
    });
  });

  describe('Lifecycle hooks', () => {
    it('onEnterState fires when entering a state', () => {
      const hook = vi.fn();
      sm.onEnterState('REQUESTING_MIC', hook);
      sm.transition('REQUESTING_MIC', 'test');
      expect(hook).toHaveBeenCalledOnce();
    });

    it('onExitState fires when leaving a state', () => {
      const hook = vi.fn();
      sm.onExitState('IDLE', hook);
      sm.transition('REQUESTING_MIC', 'test');
      expect(hook).toHaveBeenCalledOnce();
    });

    it('multiple hooks on same state all fire', () => {
      const hook1 = vi.fn();
      const hook2 = vi.fn();
      sm.onEnterState('REQUESTING_MIC', hook1);
      sm.onEnterState('REQUESTING_MIC', hook2);
      sm.transition('REQUESTING_MIC', 'test');
      expect(hook1).toHaveBeenCalledOnce();
      expect(hook2).toHaveBeenCalledOnce();
    });

    it('unsubscribe removes the hook', () => {
      const hook = vi.fn();
      const unsub = sm.onEnterState('REQUESTING_MIC', hook);
      unsub();
      sm.transition('REQUESTING_MIC', 'test');
      expect(hook).not.toHaveBeenCalled();
    });

    it('hook errors do not prevent the transition', () => {
      const brokenHook = vi.fn().mockImplementation(() => {
        throw new Error('Hook crashed');
      });
      sm.onEnterState('REQUESTING_MIC', brokenHook);
      expect(sm.transition('REQUESTING_MIC', 'test')).toBe(true);
      expect(sm.getState()).toBe('REQUESTING_MIC');
      expect(brokenHook).toHaveBeenCalled();
    });
  });

  describe('Transition log', () => {
    it('records transitions', () => {
      sm.transition('REQUESTING_MIC', 'Step 1');
      sm.transition('MIC_GRANTED', 'Step 2');
      const log = sm.getTransitionLog();
      expect(log).toHaveLength(2);
      expect(log[0].from).toBe('IDLE');
      expect(log[0].to).toBe('REQUESTING_MIC');
      expect(log[0].reason).toBe('Step 1');
      expect(log[1].from).toBe('REQUESTING_MIC');
      expect(log[1].to).toBe('MIC_GRANTED');
    });

    it('does not record rejected transitions', () => {
      sm.transition('CLOUD_ACTIVE', 'illegal');
      expect(sm.getTransitionLog()).toHaveLength(0);
    });

    it('log entries have timestamps', () => {
      const before = Date.now();
      sm.transition('REQUESTING_MIC', 'test');
      const after = Date.now();
      const entry = sm.getTransitionLog()[0];
      expect(entry.at).toBeGreaterThanOrEqual(before);
      expect(entry.at).toBeLessThanOrEqual(after);
    });
  });

  describe('Health monitoring', () => {
    it('reportHealth increments consecutive healthy count', () => {
      sm.reportHealth(true);
      sm.reportHealth(true);
      sm.reportHealth(true);
      const health = sm.getHealth();
      expect(health.consecutiveHealthy).toBe(3);
      expect(health.consecutiveUnhealthy).toBe(0);
    });

    it('reportHealth increments consecutive unhealthy count', () => {
      sm.reportHealth(false);
      sm.reportHealth(false);
      const health = sm.getHealth();
      expect(health.consecutiveUnhealthy).toBe(2);
      expect(health.consecutiveHealthy).toBe(0);
    });

    it('reportHealth resets opposite counter', () => {
      sm.reportHealth(true);
      sm.reportHealth(true);
      sm.reportHealth(false); // should reset healthy to 0
      const health = sm.getHealth();
      expect(health.consecutiveHealthy).toBe(0);
      expect(health.consecutiveUnhealthy).toBe(1);
    });

    it('health counters reset on state transition', () => {
      sm.reportHealth(true);
      sm.reportHealth(true);
      sm.transition('REQUESTING_MIC', 'test');
      const health = sm.getHealth();
      expect(health.consecutiveHealthy).toBe(0);
      expect(health.consecutiveUnhealthy).toBe(0);
    });

    it('getHealth returns current uptime', () => {
      const health = sm.getHealth();
      expect(health.uptimeMs).toBeGreaterThanOrEqual(0);
    });

    it('startHealthMonitor emits health-update for active states', async () => {
      sm.transition('CONNECTING_CLOUD', 'test');
      sm.transition('CLOUD_ACTIVE', 'test');

      const handler = vi.fn();
      sm.on('health-update', handler);

      // Use a very short interval for testing
      sm.startHealthMonitor(50);

      await new Promise((resolve) => setTimeout(resolve, 120));

      sm.stopHealthMonitor();

      expect(handler).toHaveBeenCalled();
      const call = handler.mock.calls[0][0];
      expect(call.state).toBe('CLOUD_ACTIVE');
      expect(call.metrics).toHaveProperty('uptimeMs');
      expect(call.metrics).toHaveProperty('consecutiveHealthy');
      expect(call.metrics).toHaveProperty('consecutiveUnhealthy');
    });

    it('health monitor does not emit for IDLE state', async () => {
      const handler = vi.fn();
      sm.on('health-update', handler);
      sm.startHealthMonitor(50);
      await new Promise((resolve) => setTimeout(resolve, 120));
      sm.stopHealthMonitor();
      expect(handler).not.toHaveBeenCalled();
    });
  });

  describe('State timeouts', () => {
    it('resetCurrentTimeout does not throw', () => {
      expect(() => sm.resetCurrentTimeout()).not.toThrow();
    });
  });

  describe('Destruction', () => {
    it('destroyed machine rejects all transitions', () => {
      sm.destroy();
      expect(sm.isDestroyed()).toBe(true);
      expect(sm.transition('REQUESTING_MIC', 'after destroy')).toBe(false);
    });

    it('destroyed machine rejects canTransition', () => {
      sm.destroy();
      expect(sm.canTransition('REQUESTING_MIC')).toBe(false);
    });

    it('destroy is idempotent', () => {
      sm.destroy();
      expect(() => sm.destroy()).not.toThrow();
    });

    it('destroy clears transition log', () => {
      sm.transition('REQUESTING_MIC', 'test');
      sm.destroy();
      expect(sm.getTransitionLog()).toHaveLength(0);
    });
  });
});
