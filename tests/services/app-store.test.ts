/**
 * app-store.ts — Unit tests for the Zustand app store.
 *
 * Tests initial state, all setters (value & updater-function forms),
 * message management (add, set, appendToLastAssistant), and
 * active-action CRUD helpers.
 *
 * Zustand stores work in Node without React — we read/write state
 * synchronously via useAppStore.getState() / .setState().
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import type { ActionItem } from '../../src/renderer/components/ActionFeed';
import type {
  ChatMessage,
  ConfirmationRequest,
  CodeProposal,
  ApiStatus,
  EvolutionState,
} from '../../src/renderer/store/types';
import { useAppStore } from '../../src/renderer/store/app-store';

// ── Helpers ──────────────────────────────────────────────────────────

const get = () => useAppStore.getState();
const act = <K extends keyof ReturnType<typeof useAppStore.getState>>(
  name: K,
  ...args: ReturnType<typeof useAppStore.getState>[K] extends (...a: infer P) => unknown ? P : never
) => {
  const fn = get()[name];
  if (typeof fn !== 'function') throw new Error(`${String(name)} is not a function`);
  return (fn as Function)(...args);
};

function makeMsg(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: crypto.randomUUID(),
    role: 'user',
    content: 'hello',
    timestamp: Date.now(),
    ...overrides,
  };
}

function makeAction(overrides: Partial<ActionItem> = {}): ActionItem {
  return {
    id: crypto.randomUUID(),
    name: 'test-action',
    status: 'running',
    startTime: Date.now(),
    ...overrides,
  };
}

// ── Reset store before each test ─────────────────────────────────────

// Capture the initial state once (before any test mutates it)
const initialState = useAppStore.getState();

beforeEach(() => {
  // Reset to initial state — spread everything except the action functions
  useAppStore.setState({
    messages: [],
    status: 'Initializing...',
    showQuickActions: false,
    voiceMode: true,
    connectionError: '',
    retryCount: 0,
    pendingConfirmation: null,
    codeProposal: null,
    activeActions: [],
    wakeWordEnabled: true,
    appPhase: 'checking',
    agentName: '',
    evolutionState: null,
    apiStatus: {
      gemini: 'offline',
      claude: 'no-key',
      elevenlabs: 'no-key',
      openrouter: 'no-key',
      browser: 'unavailable',
    },
    clockStr: initialState.clockStr,
    localConversationActive: false,
  });
});

// ── Tests ────────────────────────────────────────────────────────────

describe('AppStore', () => {
  // ────────────────────────────────────────────────────────────────────
  // 1. Initial state
  // ────────────────────────────────────────────────────────────────────

  describe('initial state', () => {
    it('has empty messages array', () => {
      expect(get().messages).toEqual([]);
    });

    it('has status "Initializing..."', () => {
      expect(get().status).toBe('Initializing...');
    });

    it('has showQuickActions false', () => {
      expect(get().showQuickActions).toBe(false);
    });

    it('has voiceMode true', () => {
      expect(get().voiceMode).toBe(true);
    });

    it('has empty connectionError', () => {
      expect(get().connectionError).toBe('');
    });

    it('has retryCount 0', () => {
      expect(get().retryCount).toBe(0);
    });

    it('has pendingConfirmation null', () => {
      expect(get().pendingConfirmation).toBeNull();
    });

    it('has codeProposal null', () => {
      expect(get().codeProposal).toBeNull();
    });

    it('has empty activeActions', () => {
      expect(get().activeActions).toEqual([]);
    });

    it('has wakeWordEnabled true', () => {
      expect(get().wakeWordEnabled).toBe(true);
    });

    it('has appPhase "checking"', () => {
      expect(get().appPhase).toBe('checking');
    });

    it('has empty agentName', () => {
      expect(get().agentName).toBe('');
    });

    it('has evolutionState null', () => {
      expect(get().evolutionState).toBeNull();
    });

    it('has correct default apiStatus', () => {
      expect(get().apiStatus).toEqual({
        gemini: 'offline',
        claude: 'no-key',
        elevenlabs: 'no-key',
        openrouter: 'no-key',
        browser: 'unavailable',
      });
    });

    it('has a clockStr that looks like a time string', () => {
      // e.g. "3:45 PM" or "12:00 AM"
      expect(get().clockStr).toMatch(/\d{1,2}:\d{2}\s?(AM|PM)/i);
    });

    it('has localConversationActive false', () => {
      expect(get().localConversationActive).toBe(false);
    });
  });

  // ────────────────────────────────────────────────────────────────────
  // 2. Message management
  // ────────────────────────────────────────────────────────────────────

  describe('addMessage', () => {
    it('appends a message to the array', () => {
      const msg = makeMsg({ content: 'first' });
      get().addMessage(msg);
      expect(get().messages).toHaveLength(1);
      expect(get().messages[0]).toEqual(msg);
    });

    it('preserves existing messages when adding', () => {
      const m1 = makeMsg({ content: 'one' });
      const m2 = makeMsg({ content: 'two' });
      get().addMessage(m1);
      get().addMessage(m2);
      expect(get().messages).toHaveLength(2);
      expect(get().messages[0].content).toBe('one');
      expect(get().messages[1].content).toBe('two');
    });
  });

  describe('setMessages', () => {
    it('sets messages directly with an array', () => {
      const msgs = [makeMsg({ content: 'a' }), makeMsg({ content: 'b' })];
      get().setMessages(msgs);
      expect(get().messages).toEqual(msgs);
    });

    it('replaces all existing messages', () => {
      get().addMessage(makeMsg({ content: 'old' }));
      const fresh = [makeMsg({ content: 'new' })];
      get().setMessages(fresh);
      expect(get().messages).toHaveLength(1);
      expect(get().messages[0].content).toBe('new');
    });

    it('accepts an updater function', () => {
      const m1 = makeMsg({ content: 'keep' });
      get().addMessage(m1);
      get().setMessages((prev) => [...prev, makeMsg({ content: 'added' })]);
      expect(get().messages).toHaveLength(2);
      expect(get().messages[0].content).toBe('keep');
      expect(get().messages[1].content).toBe('added');
    });

    it('updater receives current messages', () => {
      get().addMessage(makeMsg({ content: 'x' }));
      get().addMessage(makeMsg({ content: 'y' }));
      get().setMessages((prev) => {
        expect(prev).toHaveLength(2);
        return prev.filter((m) => m.content === 'x');
      });
      expect(get().messages).toHaveLength(1);
    });
  });

  describe('appendToLastAssistant', () => {
    it('appends to the last assistant message when model matches', () => {
      const assistantMsg = makeMsg({
        role: 'assistant',
        content: 'Hello',
        model: 'gemini',
      });
      get().addMessage(assistantMsg);
      get().appendToLastAssistant(' world', 'gemini');
      expect(get().messages).toHaveLength(1);
      expect(get().messages[0].content).toBe('Hello world');
    });

    it('creates a new assistant message when no messages exist', () => {
      get().appendToLastAssistant('brand new', 'claude');
      expect(get().messages).toHaveLength(1);
      expect(get().messages[0].role).toBe('assistant');
      expect(get().messages[0].content).toBe('brand new');
      expect(get().messages[0].model).toBe('claude');
    });

    it('creates a new message when last message is from user', () => {
      get().addMessage(makeMsg({ role: 'user', content: 'question' }));
      get().appendToLastAssistant('answer', 'gemini');
      expect(get().messages).toHaveLength(2);
      expect(get().messages[1].role).toBe('assistant');
      expect(get().messages[1].content).toBe('answer');
    });

    it('creates a new message when last assistant has a different model', () => {
      get().addMessage(makeMsg({
        role: 'assistant',
        content: 'from claude',
        model: 'claude',
      }));
      get().appendToLastAssistant('from gemini', 'gemini');
      expect(get().messages).toHaveLength(2);
      expect(get().messages[1].model).toBe('gemini');
    });

    it('newly created messages have id, role, timestamp, and model', () => {
      get().appendToLastAssistant('test', 'test-model');
      const msg = get().messages[0];
      expect(msg.id).toBeDefined();
      expect(typeof msg.id).toBe('string');
      expect(msg.id.length).toBeGreaterThan(0);
      expect(msg.role).toBe('assistant');
      expect(msg.model).toBe('test-model');
      expect(msg.timestamp).toBeGreaterThan(0);
    });

    it('appends multiple chunks to the same assistant message', () => {
      get().appendToLastAssistant('chunk1', 'gemini');
      get().appendToLastAssistant(' chunk2', 'gemini');
      get().appendToLastAssistant(' chunk3', 'gemini');
      expect(get().messages).toHaveLength(1);
      expect(get().messages[0].content).toBe('chunk1 chunk2 chunk3');
    });
  });

  // ────────────────────────────────────────────────────────────────────
  // 3. Simple setters
  // ────────────────────────────────────────────────────────────────────

  describe('simple setters', () => {
    it('setStatus updates status', () => {
      get().setStatus('Ready');
      expect(get().status).toBe('Ready');
    });

    it('setConnectionError updates connectionError', () => {
      get().setConnectionError('timeout');
      expect(get().connectionError).toBe('timeout');
    });

    it('setWakeWordEnabled updates wakeWordEnabled', () => {
      expect(get().wakeWordEnabled).toBe(true);
      get().setWakeWordEnabled(false);
      expect(get().wakeWordEnabled).toBe(false);
    });

    it('setAppPhase updates appPhase', () => {
      get().setAppPhase('normal');
      expect(get().appPhase).toBe('normal');
    });

    it('setAppPhase accepts all valid phases', () => {
      const phases = ['checking', 'passphrase-gate', 'onboarding', 'creating', 'normal'] as const;
      for (const phase of phases) {
        get().setAppPhase(phase);
        expect(get().appPhase).toBe(phase);
      }
    });

    it('setAgentName updates agentName', () => {
      get().setAgentName('Friday');
      expect(get().agentName).toBe('Friday');
    });

    it('setPendingConfirmation sets and clears', () => {
      const req: ConfirmationRequest = {
        id: 'c1',
        toolName: 'delete-file',
        description: 'Delete important.txt',
      };
      get().setPendingConfirmation(req);
      expect(get().pendingConfirmation).toEqual(req);
      get().setPendingConfirmation(null);
      expect(get().pendingConfirmation).toBeNull();
    });

    it('setCodeProposal sets and clears', () => {
      const proposal: CodeProposal = {
        id: 'p1',
        filePath: '/src/index.ts',
        description: 'Add logging',
        diff: '+console.log("hi");',
      };
      get().setCodeProposal(proposal);
      expect(get().codeProposal).toEqual(proposal);
      get().setCodeProposal(null);
      expect(get().codeProposal).toBeNull();
    });

    it('setEvolutionState sets and clears', () => {
      const evo: EvolutionState = {
        sessionCount: 42,
        primaryHue: 200,
        secondaryHue: 120,
        particleSpeed: 1.5,
        cubeFragmentation: 0.3,
        coreScale: 1.0,
        dustDensity: 0.8,
        glowIntensity: 0.6,
      };
      get().setEvolutionState(evo);
      expect(get().evolutionState).toEqual(evo);
      get().setEvolutionState(null);
      expect(get().evolutionState).toBeNull();
    });

    it('setClockStr updates clockStr', () => {
      get().setClockStr('11:59 PM');
      expect(get().clockStr).toBe('11:59 PM');
    });

    it('setLocalConversationActive toggles', () => {
      get().setLocalConversationActive(true);
      expect(get().localConversationActive).toBe(true);
      get().setLocalConversationActive(false);
      expect(get().localConversationActive).toBe(false);
    });
  });

  // ────────────────────────────────────────────────────────────────────
  // 4. Function-style setters (value | updater)
  // ────────────────────────────────────────────────────────────────────

  describe('function-style setters', () => {
    it('setShowQuickActions with boolean value', () => {
      get().setShowQuickActions(true);
      expect(get().showQuickActions).toBe(true);
    });

    it('setShowQuickActions with updater function', () => {
      expect(get().showQuickActions).toBe(false);
      get().setShowQuickActions((prev) => !prev);
      expect(get().showQuickActions).toBe(true);
      get().setShowQuickActions((prev) => !prev);
      expect(get().showQuickActions).toBe(false);
    });

    it('setVoiceMode with boolean value', () => {
      get().setVoiceMode(false);
      expect(get().voiceMode).toBe(false);
    });

    it('setVoiceMode with updater function', () => {
      expect(get().voiceMode).toBe(true);
      get().setVoiceMode((prev) => !prev);
      expect(get().voiceMode).toBe(false);
    });

    it('setRetryCount with direct value', () => {
      get().setRetryCount(5);
      expect(get().retryCount).toBe(5);
    });

    it('setRetryCount with updater function', () => {
      get().setRetryCount(3);
      get().setRetryCount((prev) => prev + 1);
      expect(get().retryCount).toBe(4);
    });

    it('setActiveActions with direct value', () => {
      const actions = [makeAction({ name: 'a1' }), makeAction({ name: 'a2' })];
      get().setActiveActions(actions);
      expect(get().activeActions).toEqual(actions);
    });

    it('setActiveActions with updater function', () => {
      const a1 = makeAction({ name: 'keep' });
      const a2 = makeAction({ name: 'drop' });
      get().setActiveActions([a1, a2]);
      get().setActiveActions((prev) => prev.filter((a) => a.name === 'keep'));
      expect(get().activeActions).toHaveLength(1);
      expect(get().activeActions[0].name).toBe('keep');
    });
  });

  // ────────────────────────────────────────────────────────────────────
  // 5. Active actions CRUD
  // ────────────────────────────────────────────────────────────────────

  describe('active actions', () => {
    it('addActiveAction appends to the array', () => {
      const a1 = makeAction({ id: 'a1' });
      const a2 = makeAction({ id: 'a2' });
      get().addActiveAction(a1);
      get().addActiveAction(a2);
      expect(get().activeActions).toHaveLength(2);
      expect(get().activeActions[0].id).toBe('a1');
      expect(get().activeActions[1].id).toBe('a2');
    });

    it('updateActiveAction updates matching action by id', () => {
      const action = makeAction({ id: 'target', status: 'running', progress: 10 });
      get().addActiveAction(action);
      get().updateActiveAction('target', { status: 'success', progress: 100 });
      const updated = get().activeActions[0];
      expect(updated.status).toBe('success');
      expect(updated.progress).toBe(100);
      expect(updated.name).toBe(action.name); // unchanged fields preserved
    });

    it('updateActiveAction does not affect non-matching actions', () => {
      const a1 = makeAction({ id: 'a1', name: 'first' });
      const a2 = makeAction({ id: 'a2', name: 'second' });
      get().addActiveAction(a1);
      get().addActiveAction(a2);
      get().updateActiveAction('a1', { name: 'updated-first' });
      expect(get().activeActions[0].name).toBe('updated-first');
      expect(get().activeActions[1].name).toBe('second');
    });

    it('updateActiveAction is a no-op when id does not exist', () => {
      const action = makeAction({ id: 'exists' });
      get().addActiveAction(action);
      get().updateActiveAction('nonexistent', { status: 'error' });
      expect(get().activeActions).toHaveLength(1);
      expect(get().activeActions[0].status).toBe('running');
    });

    it('removeActiveAction removes matching action by id', () => {
      const a1 = makeAction({ id: 'a1' });
      const a2 = makeAction({ id: 'a2' });
      get().addActiveAction(a1);
      get().addActiveAction(a2);
      get().removeActiveAction('a1');
      expect(get().activeActions).toHaveLength(1);
      expect(get().activeActions[0].id).toBe('a2');
    });

    it('removeActiveAction is a no-op when id does not exist', () => {
      const action = makeAction({ id: 'only' });
      get().addActiveAction(action);
      get().removeActiveAction('ghost');
      expect(get().activeActions).toHaveLength(1);
    });

    it('removeActiveAction can empty the array', () => {
      get().addActiveAction(makeAction({ id: 'sole' }));
      get().removeActiveAction('sole');
      expect(get().activeActions).toEqual([]);
    });
  });

  // ────────────────────────────────────────────────────────────────────
  // 6. API status
  // ────────────────────────────────────────────────────────────────────

  describe('setApiStatus', () => {
    it('replaces the entire apiStatus object with a direct value', () => {
      const newStatus: ApiStatus = {
        gemini: 'connected',
        claude: 'ready',
        elevenlabs: 'ready',
        openrouter: 'ready',
        browser: 'ready',
      };
      get().setApiStatus(newStatus);
      expect(get().apiStatus).toEqual(newStatus);
    });

    it('accepts an updater function', () => {
      get().setApiStatus((prev) => ({ ...prev, gemini: 'connected' }));
      expect(get().apiStatus.gemini).toBe('connected');
      // Other fields should remain at defaults
      expect(get().apiStatus.claude).toBe('no-key');
    });

    it('updater can change multiple fields at once', () => {
      get().setApiStatus((prev) => ({
        ...prev,
        gemini: 'connecting',
        claude: 'ready',
        browser: 'ready',
      }));
      expect(get().apiStatus.gemini).toBe('connecting');
      expect(get().apiStatus.claude).toBe('ready');
      expect(get().apiStatus.browser).toBe('ready');
      expect(get().apiStatus.elevenlabs).toBe('no-key');
      expect(get().apiStatus.openrouter).toBe('no-key');
    });
  });

  // ────────────────────────────────────────────────────────────────────
  // 7. State immutability
  // ────────────────────────────────────────────────────────────────────

  describe('immutability', () => {
    it('addMessage creates a new messages array reference', () => {
      get().addMessage(makeMsg({ content: 'first' }));
      const ref1 = get().messages;
      get().addMessage(makeMsg({ content: 'second' }));
      const ref2 = get().messages;
      expect(ref1).not.toBe(ref2);
    });

    it('updateActiveAction creates a new activeActions array reference', () => {
      const action = makeAction({ id: 'x' });
      get().addActiveAction(action);
      const ref1 = get().activeActions;
      get().updateActiveAction('x', { status: 'success' });
      const ref2 = get().activeActions;
      expect(ref1).not.toBe(ref2);
    });

    it('appendToLastAssistant creates a new messages array reference', () => {
      get().addMessage(makeMsg({ role: 'assistant', content: 'hi', model: 'm' }));
      const ref1 = get().messages;
      get().appendToLastAssistant(' there', 'm');
      const ref2 = get().messages;
      expect(ref1).not.toBe(ref2);
    });
  });
});
