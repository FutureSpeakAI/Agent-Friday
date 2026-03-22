import { create } from 'zustand';
import type { ActionItem } from '../components/ActionFeed';
import type {
  ChatMessage,
  ConfirmationRequest,
  CodeProposal,
  ApiStatus,
  AppPhase,
  EvolutionState,
} from './types';

// ── App Store — all top-level useState from App.tsx ──────────────────────────

export interface AppState {
  // Chat
  messages: ChatMessage[];
  status: string;

  // UI toggles
  showQuickActions: boolean;
  voiceMode: boolean;

  // Connection
  connectionError: string;
  retryCount: number;

  // Confirmations & proposals
  pendingConfirmation: ConfirmationRequest | null;
  codeProposal: CodeProposal | null;

  // Active tool / agent actions
  activeActions: ActionItem[];

  // Voice
  wakeWordEnabled: boolean;

  // App lifecycle
  appPhase: AppPhase;
  agentName: string;

  // Evolution
  evolutionState: EvolutionState | null;

  // API status
  apiStatus: ApiStatus;

  // Live clock string
  clockStr: string;

  // Local conversation
  localConversationActive: boolean;
}

export interface AppActions {
  // ── Setters ──────────────────────────────────────────────────────────────
  setMessages: (messages: ChatMessage[] | ((prev: ChatMessage[]) => ChatMessage[])) => void;
  setStatus: (status: string) => void;
  setShowQuickActions: (show: boolean | ((prev: boolean) => boolean)) => void;
  setVoiceMode: (mode: boolean | ((prev: boolean) => boolean)) => void;
  setConnectionError: (error: string) => void;
  setRetryCount: (count: number | ((prev: number) => number)) => void;
  setPendingConfirmation: (req: ConfirmationRequest | null) => void;
  setCodeProposal: (proposal: CodeProposal | null) => void;
  setActiveActions: (actions: ActionItem[] | ((prev: ActionItem[]) => ActionItem[])) => void;
  setWakeWordEnabled: (enabled: boolean) => void;
  setAppPhase: (phase: AppPhase) => void;
  setAgentName: (name: string) => void;
  setEvolutionState: (state: EvolutionState | null) => void;
  setApiStatus: (status: ApiStatus | ((prev: ApiStatus) => ApiStatus)) => void;
  setClockStr: (str: string) => void;
  setLocalConversationActive: (active: boolean) => void;

  // ── Convenience actions ──────────────────────────────────────────────────
  addMessage: (msg: ChatMessage) => void;
  appendToLastAssistant: (text: string, model: string) => void;
  addActiveAction: (action: ActionItem) => void;
  updateActiveAction: (id: string, updates: Partial<ActionItem>) => void;
  removeActiveAction: (id: string) => void;
}

function initClockStr(): string {
  const d = new Date();
  return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true });
}

export const useAppStore = create<AppState & AppActions>((set) => ({
  // ── Initial state (matches App.tsx useState defaults exactly) ────────────
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
  clockStr: initClockStr(),
  localConversationActive: false,

  // ── Setters ──────────────────────────────────────────────────────────────
  setMessages: (messagesOrUpdater) =>
    set((state) => ({
      messages:
        typeof messagesOrUpdater === 'function'
          ? messagesOrUpdater(state.messages)
          : messagesOrUpdater,
    })),

  setStatus: (status) => set({ status }),

  setShowQuickActions: (showOrUpdater) =>
    set((state) => ({
      showQuickActions:
        typeof showOrUpdater === 'function'
          ? showOrUpdater(state.showQuickActions)
          : showOrUpdater,
    })),

  setVoiceMode: (modeOrUpdater) =>
    set((state) => ({
      voiceMode:
        typeof modeOrUpdater === 'function'
          ? modeOrUpdater(state.voiceMode)
          : modeOrUpdater,
    })),

  setConnectionError: (connectionError) => set({ connectionError }),

  setRetryCount: (countOrUpdater) =>
    set((state) => ({
      retryCount:
        typeof countOrUpdater === 'function'
          ? countOrUpdater(state.retryCount)
          : countOrUpdater,
    })),

  setPendingConfirmation: (pendingConfirmation) => set({ pendingConfirmation }),

  setCodeProposal: (codeProposal) => set({ codeProposal }),

  setActiveActions: (actionsOrUpdater) =>
    set((state) => ({
      activeActions:
        typeof actionsOrUpdater === 'function'
          ? actionsOrUpdater(state.activeActions)
          : actionsOrUpdater,
    })),

  setWakeWordEnabled: (wakeWordEnabled) => set({ wakeWordEnabled }),

  setAppPhase: (appPhase) => set({ appPhase }),

  setAgentName: (agentName) => set({ agentName }),

  setEvolutionState: (evolutionState) => set({ evolutionState }),

  setApiStatus: (statusOrUpdater) =>
    set((state) => ({
      apiStatus:
        typeof statusOrUpdater === 'function'
          ? statusOrUpdater(state.apiStatus)
          : statusOrUpdater,
    })),

  setClockStr: (clockStr) => set({ clockStr }),

  setLocalConversationActive: (localConversationActive) => set({ localConversationActive }),

  // ── Convenience actions ──────────────────────────────────────────────────
  addMessage: (msg) =>
    set((state) => ({ messages: [...state.messages, msg] })),

  appendToLastAssistant: (text, model) =>
    set((state) => {
      const last = state.messages[state.messages.length - 1];
      if (last && last.role === 'assistant' && last.model === model) {
        return {
          messages: [
            ...state.messages.slice(0, -1),
            { ...last, content: last.content + text },
          ],
        };
      }
      return {
        messages: [
          ...state.messages,
          {
            id: crypto.randomUUID(),
            role: 'assistant' as const,
            content: text,
            model,
            timestamp: Date.now(),
          },
        ],
      };
    }),

  addActiveAction: (action) =>
    set((state) => ({ activeActions: [...state.activeActions, action] })),

  updateActiveAction: (id, updates) =>
    set((state) => ({
      activeActions: state.activeActions.map((a) =>
        a.id === id ? { ...a, ...updates } : a
      ),
    })),

  removeActiveAction: (id) =>
    set((state) => ({
      activeActions: state.activeActions.filter((a) => a.id !== id),
    })),
}));
