import React, { useState, useCallback, useRef, useEffect, useMemo, Suspense } from 'react';
import FridayCore, { SemanticState } from './components/FridayCore';
import DesktopViz from './components/DesktopViz';
import HudOverlay from './components/HudOverlay';
import VoiceOrb from './components/VoiceOrb';
import ChatHistory from './components/ChatHistory';
import StatusBar from './components/StatusBar';
import TextInput from './components/TextInput';
import QuickActions from './components/QuickActions';
import ConnectionOverlay from './components/ConnectionOverlay';
import AgentCreation from './components/AgentCreation';
import OnboardingWizard from './components/OnboardingWizard';
import PassphraseGate from './components/PassphraseGate';
import AgentOffice from './components/AgentOffice';
import ActionFeed, { ActionItem } from './components/ActionFeed';
import FileToast from './components/FileToast';
import { MoodProvider, useMood } from './contexts/MoodContext';
import { useGeminiLive } from './hooks/useGeminiLive';
import { AudioPlaybackEngine } from './audio/AudioPlaybackEngine';
import { useWakeWord } from './hooks/useWakeWord';
import { useDesktopEvolution } from './hooks/useDesktopEvolution';
import { useAppManager } from './hooks/useAppManager';
import { APP_REGISTRY } from './registry/app-registry';
import {
  playConnectedChime,
  playListeningPing,
  playNotificationBell,
  playDisconnectTone,
} from './audio/sound-effects';

// ── Office window detection ──────────────────────────────────────────────
// If loaded with ?office=true, render the pixel-art Agent Office instead
const isOfficeWindow = new URLSearchParams(window.location.search).get('office') === 'true';

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  model?: string;
  timestamp: number;
}

interface ConfirmationRequest {
  id: string;
  toolName: string;
  description: string;
}

interface CodeProposal {
  id: string;
  filePath: string;
  description: string;
  diff: string;
}

// ── Mood-aware wrapper components (must be children of MoodProvider) ──────────

/** Blend two hex colors at ratio t (0=a, 1=b) */
function blendHex(a: string, b: string, t: number): string {
  const ha = a.replace('#', ''), hb = b.replace('#', '');
  const r = Math.round(parseInt(ha.substring(0, 2), 16) * (1 - t) + parseInt(hb.substring(0, 2), 16) * t);
  const g = Math.round(parseInt(ha.substring(2, 4), 16) * (1 - t) + parseInt(hb.substring(2, 4), 16) * t);
  const bl = Math.round(parseInt(ha.substring(4, 6), 16) * (1 - t) + parseInt(hb.substring(4, 6), 16) * t);
  return `#${r.toString(16).padStart(2, '0')}${g.toString(16).padStart(2, '0')}${bl.toString(16).padStart(2, '0')}`;
}

const SEMANTIC_COLORS: Record<SemanticState, string> = {
  LISTENING: '#00f0ff',
  REASONING: '#8A2BE2',
  SUB_AGENTS: '#D4A574',
  EXECUTING: '#22c55e',
};

const SEMANTIC_COLORS_ALPHA: Record<SemanticState, string> = {
  LISTENING: 'rgba(0, 240, 255, 0.5)',
  REASONING: 'rgba(138, 43, 226, 0.5)',
  SUB_AGENTS: 'rgba(212, 165, 116, 0.5)',
  EXECUTING: 'rgba(34, 197, 94, 0.5)',
};

function MoodFridayCore({ getLevels, semanticState, isSpeaking, evolutionState }: {
  getLevels: () => { mic: number; output: number };
  semanticState: SemanticState;
  isSpeaking: boolean;
  evolutionState?: { sessionCount: number; primaryHue: number; secondaryHue: number; particleSpeed: number; cubeFragmentation: number; coreScale: number; dustDensity: number; glowIntensity: number } | null;
}) {
  const mood = useMood();
  return (
    <FridayCore
      getLevels={getLevels}
      semanticState={semanticState}
      isSpeaking={isSpeaking}
      moodPalette={mood.palette}
      moodIntensity={mood.intensity}
      moodTurbulence={mood.turbulence}
      evolutionState={evolutionState}
    />
  );
}

function MoodDesktopViz({ getLevels, semanticState, isSpeaking, isListening, evolutionIndex, transitionBlend }: {
  getLevels: () => { mic: number; output: number };
  semanticState: SemanticState;
  isSpeaking: boolean;
  isListening: boolean;
  evolutionIndex: number;
  transitionBlend: number;
}) {
  const mood = useMood();
  return (
    <DesktopViz
      getLevels={getLevels}
      semanticState={semanticState}
      isSpeaking={isSpeaking}
      isListening={isListening}
      moodPalette={mood.palette}
      moodIntensity={mood.intensity}
      moodTurbulence={mood.turbulence}
      evolutionIndex={evolutionIndex}
      transitionBlend={transitionBlend}
    />
  );
}

function MoodVoiceOrb({ isListening, isProcessing, isStreaming, onClick, interimTranscript, getLevels }: {
  isListening: boolean;
  isProcessing: boolean;
  isStreaming?: boolean;
  onClick: () => void;
  interimTranscript: string;
  getLevels?: () => { mic: number; output: number };
}) {
  const mood = useMood();
  return (
    <VoiceOrb
      isListening={isListening}
      isProcessing={isProcessing}
      isStreaming={isStreaming}
      onClick={onClick}
      interimTranscript={interimTranscript}
      getLevels={getLevels}
      moodPalette={mood.palette}
      moodIntensity={mood.intensity}
    />
  );
}

function MoodBrandSub({ semanticState }: { semanticState: SemanticState }) {
  const mood = useMood();
  const base = SEMANTIC_COLORS[semanticState];
  const color = mood.confidence > 0.3 ? blendHex(base, mood.palette.text, 0.35) : base;
  return (
    <div style={{ ...styles.brandSub, color }}>
      SYS.CORE // {semanticState.replace('_', '-')}
    </div>
  );
}

function MoodStatusLabel({ semanticState, statusText }: { semanticState: SemanticState; statusText: string }) {
  const mood = useMood();
  const base = SEMANTIC_COLORS[semanticState];
  const color = mood.confidence > 0.3
    ? blendHex(base, mood.palette.text, 0.25) + '80'
    : SEMANTIC_COLORS_ALPHA[semanticState];
  return (
    <div style={{ ...styles.statusLabel, color }}>
      {statusText}
    </div>
  );
}

// ── Office window shortcut ───────────────────────────────────────────────
// When loaded as the office window, skip the entire main app
function OfficeApp() {
  return (
    <div style={{ width: '100%', height: '100%', background: '#0a0e1c', overflow: 'hidden' }}>
      <AgentOffice />
    </div>
  );
}

export default function App() {
  // NOTE: Do NOT early-return before hooks — React requires hooks to be called
  // unconditionally in the same order every render. The isOfficeWindow check is
  // handled at the bottom of the component in the return statement.
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [status, setStatus] = useState('Initializing...');
  const [showQuickActions, setShowQuickActions] = useState(false);
  const appManager = useAppManager();
  const [connectionError, setConnectionError] = useState('');
  const [retryCount, setRetryCount] = useState(0);
  const [pendingConfirmation, setPendingConfirmation] = useState<ConfirmationRequest | null>(null);
  const [codeProposal, setCodeProposal] = useState<CodeProposal | null>(null);
  const [activeActions, setActiveActions] = useState<ActionItem[]>([]);
  const [wakeWordEnabled, setWakeWordEnabled] = useState(true);
  const [voiceMode, setVoiceMode] = useState(true);
  const [appPhase, setAppPhase] = useState<
    'checking' | 'passphrase-gate' | 'onboarding' | 'creating' | 'normal'
  >('checking');
  const appPhaseRef = useRef(appPhase);
  useEffect(() => { appPhaseRef.current = appPhase; }, [appPhase]);
  const [agentName, setAgentName] = useState('');
  const [evolutionState, setEvolutionState] = useState<{
    sessionCount: number; primaryHue: number; secondaryHue: number;
    particleSpeed: number; cubeFragmentation: number; coreScale: number;
    dustDensity: number; glowIntensity: number;
  } | null>(null);
  const [apiStatus, setApiStatus] = useState<{
    gemini: 'connected' | 'connecting' | 'offline' | 'no-key';
    claude: 'ready' | 'no-key';
    elevenlabs: 'ready' | 'no-key';
    openrouter: 'ready' | 'no-key';
    browser: 'ready' | 'unavailable';
  }>({ gemini: 'offline', claude: 'no-key', elevenlabs: 'no-key', openrouter: 'no-key', browser: 'unavailable' });
  const desktopEvolution = useDesktopEvolution();
  const retriesRef = useRef(0);
  const maxRetries = 3;

  // ── Local voice conversation state (fallback when no Gemini key) ──────
  const localConversationActiveRef = useRef(false);
  const [localConversationActive, setLocalConversationActive] = useState(false);
  const localConversationCleanupsRef = useRef<Array<() => void>>([]);
  const localPlaybackRef = useRef<AudioPlaybackEngine | null>(null);

  const geminiLive = useGeminiLive({
    onTextResponse: (text) => {
      setMessages((prev) => {
        const last = prev[prev.length - 1];
        if (last && last.role === 'assistant' && last.model === 'gemini-live') {
          return [...prev.slice(0, -1), { ...last, content: last.content + text }];
        }
        return [
          ...prev,
          {
            id: crypto.randomUUID(),
            role: 'assistant',
            content: text,
            model: 'gemini-live',
            timestamp: Date.now(),
          },
        ];
      });
    },
    onClaudeUsed: (question, answer) => {
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: 'assistant',
          content: `[Claude Opus consulted]\n\nQ: ${question.slice(0, 120)}${question.length > 120 ? '...' : ''}\n\nA: ${answer}`,
          model: 'claude-opus',
          timestamp: Date.now(),
        },
      ]);
    },
    onError: (error) => {
      console.error('[Agent] Gemini Live error:', error);
    },
    onAgentFinalized: (config) => {
      console.log('[Agent] Agent finalized:', config);
      const name = String(config.agentName || 'Agent');
      const voice = String(config.agentVoice || 'Kore');
      setAgentName(name);

      // If we're in the onboarding wizard, let the wizard handle the transition
      // (InterviewStep listens for this event and advances to RevealStep)
      if (appPhaseRef.current === 'onboarding') {
        window.dispatchEvent(new CustomEvent('agent-finalized', {
          detail: { agentName: name, agentVoice: voice },
        }));
        return;
      }

      // Disconnect the current session (Setup Assistant)
      geminiLive.disconnect();

      // Show the Agent Creation animation
      setAppPhase('creating');

      // After animation completes, reconnect with new voice & personality
      setTimeout(async () => {
        // Get the new system instruction (now with full personality)
        let newInstruction: string;
        try {
          newInstruction = await window.eve.getLiveSystemInstruction();
        } catch {
          newInstruction = `You are ${name}, a personal AI assistant. Be helpful and stay in character.`;
        }

        // Get the first greeting prompt
        let firstGreeting: string;
        try {
          firstGreeting = await window.eve.onboarding.getFirstGreeting();
        } catch {
          firstGreeting = '';
        }

        // Gather all tools for the new session
        let tools: Array<{ name: string; description?: string; parameters?: unknown }> = [];
        try {
          tools = await window.eve.desktop.listTools();
        } catch (e) { console.warn('[Agent] Desktop tools unavailable:', e); }

        // Feature setup tools are always available — the agent configures things
        // opportunistically during conversation, not as a forced walkthrough
        try {
          const fsToolDecls = await window.eve.featureSetup.getToolDeclarations();
          tools = [...tools, ...fsToolDecls];
        } catch (e) { console.warn('[Agent] Feature setup tools unavailable:', e); }

        try {
          await geminiLive.connect(newInstruction, tools, voice);
          // Start mic capture after connect
          try { await geminiLive.startListening(); } catch (e) { console.warn('[Agent] Mic start failed:', e); }
          setStatus('Connected');

          // Always go to normal phase — feature setup happens opportunistically
          setAppPhase('normal');

          // Send the first greeting prompt to get the agent to introduce themselves
          if (firstGreeting) {
            setTimeout(async () => {
              geminiLive.sendTextToGemini(firstGreeting);
            }, 1500);
          }
        } catch (err) {
          console.error('[Agent] Reconnect after creation failed:', err);
          setConnectionError(err instanceof Error ? err.message : String(err));
        }
      }, 5500); // Match the AgentCreation animation duration
    },
    onToolStart: (id, name) => {
      setActiveActions((prev) => [
        ...prev,
        { id, name, status: 'running', startTime: Date.now() },
      ]);
    },
    onToolEnd: (id, name, success) => {
      setActiveActions((prev) =>
        prev.map((a) =>
          a.id === id ? { ...a, status: success ? 'success' : 'error' } as ActionItem : a
        )
      );
      // Remove completed actions after 3s
      setTimeout(() => {
        setActiveActions((prev) => prev.filter((a) => a.id !== id));
      }, 3000);
    },
    onPhaseChange: (phase) => {
      console.log('[Agent] Phase change from tool:', phase);
      setAppPhase(phase);
    },
  });

  const connectToGemini = useCallback(async (identityContext?: string) => {
    setStatus('Connecting...');

    // 1. Determine if this is onboarding or a normal session
    let onboardingComplete = false;
    try {
      onboardingComplete = await window.eve.onboarding.isComplete();
    } catch {
      // Assume not complete if check fails
    }

    // 2. Gather tools — during onboarding, only load interview tools (not desktop/browser/etc.)
    //    to keep the Gemini Live setup payload small and avoid policy-violation rejections
    let tools: Array<{ name: string; description?: string; parameters?: unknown }> = [];
    if (!onboardingComplete) {
      // Onboarding: only the 4 interview tools (acknowledge, intake, transition, finalize)
      try {
        tools = await window.eve.onboarding.getToolDeclarations();
      } catch (err) {
        console.warn('[Agent] Failed to get onboarding tool declarations:', err);
      }
    } else {
      // Post-onboarding: full desktop toolkit
      try {
        tools = await window.eve.desktop.listTools();
      } catch {
        // Desktop tools unavailable — that's fine
      }
    }

    // 4. Get system instruction (personality module handles onboarding vs normal)
    let instruction: string;
    try {
      instruction = await window.eve.getLiveSystemInstruction();
    } catch {
      instruction = onboardingComplete
        ? 'You are a personal AI assistant. Keep responses concise for voice.'
        : 'You are a Setup Assistant helping configure a new AI agent. Be warm and friendly.';
    }

    // 5. Determine voice — Charon (calm male) for onboarding, configured voice for normal
    let voiceName = 'Charon'; // Calm male setup voice for onboarding ("Her" style)
    if (onboardingComplete) {
      try {
        const config = await window.eve.onboarding.getAgentConfig();
        voiceName = config.agentVoice || 'Kore';
        setAgentName(config.agentName || '');
      } catch { /* config load failed */ }
    }

    // 5b. Check available voice backends
    let hasGeminiKey = false;
    try {
      const key = await window.eve.getGeminiApiKey();
      hasGeminiKey = !!(key && typeof key === 'string' && key.trim().length > 0);
    } catch { /* no key */ }

    let ollamaHealthy = false;
    try {
      const health = await window.eve.ollama.getHealth() as any;
      ollamaHealthy = !!health?.running;
    } catch { /* Ollama not reachable */ }

    // ── 6a. LOCAL-FIRST VOICE PATH — try Ollama + Whisper + TTS first ──
    // Falls back to Gemini Live if local voice isn't available.
    // Works for BOTH onboarding (Interview step) and post-onboarding (normal use)
    const useLocalVoice = ollamaHealthy; // Local-first: always prefer local when Ollama is running
    if (useLocalVoice) {
      console.log(`[Agent] Local-first: starting local voice conversation (Whisper + Ollama + TTS) [onboarding=${!onboardingComplete}]`);

      // Clean up any previous local conversation listeners
      for (const cleanup of localConversationCleanupsRef.current) cleanup();
      localConversationCleanupsRef.current = [];

      // Set up event listeners before starting
      const cleanups: Array<() => void> = [];

      cleanups.push(
        window.eve.localConversation.onStarted(() => {
          console.log('[Agent] Local conversation started');
          setStatus('Connected (Local)');
          setConnectionError(''); // Clear any Whisper/TTS warnings — session is alive
          retriesRef.current = 0;
          setRetryCount(0);
          localConversationActiveRef.current = true;
          setLocalConversationActive(true);
          // Signal InterviewStep that voice session is live (same event as Gemini path)
          window.dispatchEvent(new Event('gemini-audio-active'));
        }),
      );

      cleanups.push(
        window.eve.localConversation.onTranscript((text: string) => {
          // Display user transcript in chat (mirrors Gemini path)
          setMessages((prev) => [
            ...prev,
            {
              id: crypto.randomUUID(),
              role: 'user' as const,
              content: text,
              timestamp: Date.now(),
            },
          ]);
        }),
      );

      cleanups.push(
        window.eve.localConversation.onResponse((text: string) => {
          // Display AI response in chat (mirrors Gemini path)
          setMessages((prev) => [
            ...prev,
            {
              id: crypto.randomUUID(),
              role: 'assistant' as const,
              content: text,
              model: 'ollama-local',
              timestamp: Date.now(),
            },
          ]);
        }),
      );

      cleanups.push(
        window.eve.localConversation.onAgentFinalized((config: Record<string, unknown>) => {
          console.log('[Agent] Local conversation — agent finalized:', config);
          const name = String(config.agentName || 'Agent');
          const voice = String(config.agentVoice || 'Kore');
          setAgentName(name);

          if (appPhaseRef.current === 'onboarding') {
            // During onboarding: dispatch event for InterviewStep → auto-advance to Reveal
            window.dispatchEvent(new CustomEvent('agent-finalized', {
              detail: { agentName: name, agentVoice: voice },
            }));
          } else {
            // Post-onboarding re-agenting: stop current session, show creation animation, reconnect
            localConversationActiveRef.current = false;
            setLocalConversationActive(false);
            window.eve.localConversation.stop().catch(() => {});
            setAppPhase('creating');
            // AgentCreation onComplete (below) handles the reconnect
          }
        }),
      );

      cleanups.push(
        window.eve.localConversation.onError((error: string) => {
          console.error('[Agent] Local conversation error:', error);
          // Non-fatal voice errors (Whisper/TTS missing) shouldn't block text mode.
          // If localConversation is still active, this is a degradation warning, not a fatal error.
          if (localConversationActiveRef.current) {
            // Session is alive — show as a status warning, not a blocking error
            setStatus(`Local voice error: ${error}`);
          } else {
            // Session failed to start entirely — show as connection error
            setConnectionError(error);
            setStatus(`Local voice error: ${error}`);
          }
        }),
      );

      // Wire local TTS audio to speakers via AudioPlaybackEngine
      if (!localPlaybackRef.current) {
        localPlaybackRef.current = new AudioPlaybackEngine();
      }
      cleanups.push(
        window.eve.voice.onPlayChunk((audio: Float32Array) => {
          localPlaybackRef.current?.enqueue(audio);
        }),
      );
      // Clean up playback engine when local conversation ends
      cleanups.push(() => {
        localPlaybackRef.current?.flush();
      });

      localConversationCleanupsRef.current = cleanups;

      // Build initial prompt based on whether we're in onboarding or normal session
      let initialPrompt: string | undefined;
      if (!onboardingComplete) {
        // Onboarding: kick off the "Her"-style intake conversation
        // No pre-selected identity — the interview discovers everything through conversation
        const contextPreamble = '';
        initialPrompt = `[SYSTEM — BEGIN ONBOARDING] The user has just arrived for their first session. Begin the intake process now. Follow your system instructions exactly — welcome them briefly, then ask the first question. As part of the conversation, discover what they'd like to name their AI agent, their preferred voice gender (male/female/neutral), and voice character (warm/sharp/deep/soft/bright).`;
      } else {
        // Post-onboarding: inject intelligence briefings if available
        try {
          const briefing = await window.eve.intelligence.getBriefing();
          if (briefing) {
            console.log('[Agent] Injecting intelligence briefings into local conversation');
            initialPrompt = briefing;
          }
        } catch (err) {
          console.warn('[Agent] Briefing check failed:', err);
        }
      }

      try {
        await window.eve.localConversation.start(instruction, tools, initialPrompt);
        // 'started' event handler above will set status + dispatch gemini-audio-active
        return;
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        console.warn('[Agent] Local voice failed, falling back to Gemini:', msg);
        // Clean up failed local conversation listeners
        for (const cleanup of localConversationCleanupsRef.current) cleanup();
        localConversationCleanupsRef.current = [];
        // Fall through to Gemini path below
        if (!hasGeminiKey) {
          // No fallback available — report the error
          setConnectionError(`Local voice unavailable: ${msg}. Add a Gemini API key for cloud voice.`);
          setStatus('No voice backend available');
          return;
        }
        setStatus('Local voice unavailable — connecting via Gemini...');
      }
    }

    // ── 6b. GEMINI CLOUD FALLBACK — WebSocket connection ─────────────
    if (!hasGeminiKey) {
      setConnectionError('No voice backend available. Install Ollama for local voice or add a Gemini API key.');
      setStatus('No voice backend');
      return;
    }
    try {
      await geminiLive.connect(instruction, tools, voiceName, { onboarding: !onboardingComplete });
      retriesRef.current = 0;
      setRetryCount(0);
      setConnectionError('');

      // Start microphone capture — this is what actually requests mic permission
      // and begins streaming audio to Gemini. Without this, the WebSocket connects
      // but no audio flows and the user never sees a mic permission prompt.
      try {
        await geminiLive.startListening();
      } catch (micErr) {
        console.warn('[Agent] Mic access failed after Gemini connect:', micErr);
        // Continue — text mode still works
      }

      setStatus('Connected');

      // Signal InterviewStep (if active) that the voice session is live
      window.dispatchEvent(new Event('gemini-audio-active'));

      // After connection stabilizes, inject context
      setTimeout(async () => {
        try {
          if (!onboardingComplete) {
            // First run — nudge Gemini to begin the "Her"-style intake process
            // The system instruction already contains the full screenplay flow
            console.log('[Agent] First run detected — starting "Her" onboarding intake');
            // No pre-selected identity — the interview discovers name, gender, voice through conversation
            geminiLive.sendTextToGemini(
              `[SYSTEM — BEGIN ONBOARDING] The user has just arrived for their first session. Begin the intake process now. Follow your system instructions exactly — welcome them briefly, then ask the first question. As part of the conversation, discover what they'd like to name their AI agent, their preferred voice gender (male/female/neutral), and voice character (warm/sharp/deep/soft/bright).`
            );
          } else {
            // Normal session — check for intelligence briefings
            const briefing = await window.eve.intelligence.getBriefing();
            if (briefing) {
              console.log('[Agent] Injecting intelligence briefings');
              geminiLive.sendTextToGemini(briefing);
            }
          }
        } catch (err) {
          console.warn('[Agent] Onboarding/briefing check failed:', err);
        }
      }, 2000);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error('[Agent] Connection failed:', msg);

      if (retriesRef.current < maxRetries) {
        retriesRef.current++;
        setRetryCount(retriesRef.current);
        const delay = retriesRef.current * 2000;
        setStatus(`Retrying... (${retriesRef.current}/${maxRetries})`);
        setTimeout(connectToGemini, delay);
      } else {
        setConnectionError(msg);
        setStatus(`Failed: ${msg}`);
      }
    }
  }, [geminiLive]);

  // ── Wrapped sendTextToGemini: routes to local conversation when active ──
  const sendText = useCallback(
    (text: string) => {
      if (localConversationActiveRef.current) {
        window.eve.localConversation.sendText(text).catch((err: unknown) => {
          console.error('[Agent] Local conversation sendText failed:', err);
        });
      } else if (geminiLive.sendTextToGemini) {
        geminiLive.sendTextToGemini(text);
      }
    },
    [geminiLive.sendTextToGemini],
  );

  // ── Clean up local conversation on unmount / phase change ──────────────
  useEffect(() => {
    return () => {
      if (localConversationActiveRef.current) {
        localConversationActiveRef.current = false;
        setLocalConversationActive(false);
        window.eve.localConversation.stop().catch(() => {});
        for (const cleanup of localConversationCleanupsRef.current) cleanup();
        localConversationCleanupsRef.current = [];
      }
    };
  }, []);

  // Load wake word setting
  useEffect(() => {
    window.eve.settings.get().then((s) => {
      setWakeWordEnabled(s.wakeWordEnabled === true);
    }).catch(() => {});
  }, []);

  // Persist chat messages to disk when they change
  useEffect(() => {
    if (messages.length === 0) return; // Don't overwrite with empty on initial render
    window.eve.chatHistory.save(messages).catch(() => {});
  }, [messages]);

  // Compute API connectivity status — real health checks, not just key existence
  useEffect(() => {
    const geminiState = geminiLive.isConnected ? 'connected' as const
      : geminiLive.isConnecting ? 'connecting' as const
      : 'offline' as const;

    // Run actual API health checks (lightweight endpoint pings)
    window.eve.settings.checkApiHealth().then((health: Record<string, string>) => {
      setApiStatus({
        gemini: geminiLive.isConnected ? 'connected' : (health.gemini as any) || 'no-key',
        claude: (health.claude as any) || 'no-key',
        openrouter: (health.openrouter as any) || 'no-key',
        elevenlabs: (health.elevenlabs as any) || 'no-key',
        browser: 'ready',
      });
    }).catch(() => {
      // Fall back to key existence check if health check IPC fails
      window.eve.settings.get().then((s) => {
        setApiStatus({
          gemini: s.hasGeminiKey ? geminiState : 'no-key',
          claude: s.hasAnthropicKey ? 'ready' as const : 'no-key',
          openrouter: s.hasOpenrouterKey ? 'ready' as const : 'no-key',
          elevenlabs: s.hasElevenLabsKey ? 'ready' as const : 'no-key',
          browser: 'ready',
        });
      }).catch(() => {});
    });
  }, [geminiLive.isConnected, geminiLive.isConnecting]);

  // Periodic API health refresh (every 60s) so beacons stay current
  useEffect(() => {
    const timer = setInterval(() => {
      window.eve.settings.checkApiHealth().then((health: Record<string, string>) => {
        setApiStatus((prev) => ({
          ...prev,
          gemini: prev.gemini === 'connected' ? 'connected' : (health.gemini as any) || prev.gemini,
          claude: (health.claude as any) || prev.claude,
          openrouter: (health.openrouter as any) || prev.openrouter,
          elevenlabs: (health.elevenlabs as any) || prev.elevenlabs,
        }));
      }).catch(() => {});
    }, 60_000);
    return () => clearInterval(timer);
  }, []);

  // Wake word detection — auto-connect when "Hey Friday" is detected while idle
  useWakeWord({
    enabled: wakeWordEnabled,
    isConnected: geminiLive.isConnected,
    onWake: useCallback(() => {
      console.log('[Agent] Wake word detected — connecting');
      connectToGemini();
    }, [connectToGemini]),
  });

  // Phase-aware initialization — replaces blind auto-connect
  useEffect(() => {
    let cancelled = false;

    (async () => {
      // Check if onboarding is already done
      let onboardingDone = false;
      try {
        onboardingDone = await window.eve.onboarding.isComplete();
      } catch (e) { console.warn('[Agent] Onboarding check failed:', e); }

      if (cancelled) return;

      // Sovereign Vault v2: Check if vault is initialized and unlocked
      let vaultInitialized = false;
      let vaultUnlocked = false;
      try {
        vaultInitialized = await window.eve.vault.isInitialized();
        vaultUnlocked = await window.eve.vault.isUnlocked();
      } catch (e) { console.warn('[Agent] Vault status check failed:', e); }

      if (cancelled) return;

      // Vault routing:
      // - Not initialized → onboarding (fresh install or reinstall)
      // - Initialized but locked → passphrase gate (returning user)
      if (!vaultInitialized) {
        setAppPhase('onboarding');
        return;
      }
      if (!vaultUnlocked) {
        setAppPhase('passphrase-gate');
        return;
      }

      if (onboardingDone) {
        // Returning user — vault is unlocked, skip gate + onboarding
        setAppPhase('normal');

        // Load agent name
        try {
          const config = await window.eve.onboarding.getAgentConfig();
          setAgentName(config.agentName || '');
        } catch (e) { console.warn('[Agent] Agent config load failed:', e); }

        // Restore chat history from last session
        try {
          const savedMessages = await window.eve.chatHistory.load();
          if (!cancelled && savedMessages.length > 0) {
            setMessages(savedMessages as ChatMessage[]);
          }
        } catch (e) { console.warn('[Agent] Chat history restore failed:', e); }

        // Load and increment personality evolution (visual uniqueness grows each session)
        try {
          const evoState = await window.eve.evolution.incrementSession();
          if (!cancelled) setEvolutionState(evoState);
        } catch (evoErr) {
          console.warn('[Agent] Evolution increment failed:', evoErr);
        }

        // Connect immediately
        connectToGemini();
        return;
      }

      // New user — go straight to onboarding wizard (handles keys, vault, identity, etc.)
      setAppPhase('onboarding');
    })();

    return () => {
      cancelled = true;
      geminiLive.disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Listen for scheduler task-fired events → inject into AI so Friday speaks them
  useEffect(() => {
    const cleanup = window.eve.scheduler.onTaskFired((task) => {
      console.log('[Agent] Task fired:', task.description);
      playNotificationBell();

      if (task.action === 'remind') {
        sendText(
          `[SYSTEM REMINDER — speak this naturally to the user] Reminder: ${task.payload}`
        );
      } else if (task.action === 'launch_app') {
        window.eve.desktop
          .callTool('launch_app', { app_name: task.payload })
          .then(() => {
            sendText(
              `[SYSTEM] I just launched ${task.payload} as scheduled. Let the user know briefly.`
            );
          })
          .catch((err) => console.warn('[Friday] Scheduled launch failed:', err));
      } else if (task.action === 'run_command') {
        window.eve.desktop
          .callTool('run_command', { command: task.payload })
          .then((result) => {
            sendText(
              `[SYSTEM] Scheduled command executed: ${task.description}. Result: ${result.result || result.error || 'Done'}`
            );
          })
          .catch((err) => console.warn('[Friday] Scheduled command failed:', err));
      }
    });

    return cleanup;
  }, [sendText]);

  // Listen for predictive suggestions → inject into AI so Friday speaks them naturally
  useEffect(() => {
    const cleanup = window.eve.predictor.onSuggestion((suggestion) => {
      console.log(`[Friday] Prediction: ${suggestion.type} (${suggestion.confidence})`);
      playNotificationBell();

      sendText(
        `[SYSTEM SUGGESTION — speak this naturally in character, keep it brief and charming] ${suggestion.message}`
      );
    });

    return cleanup;
  }, [sendText]);

  // Listen for captured notifications → inject into AI so Friday announces them naturally
  useEffect(() => {
    const cleanup = window.eve.notifications.onCaptured((notif) => {
      console.log(`[Friday] Notification captured: ${notif.app} — ${notif.title}`);

      sendText(
        `[SYSTEM NOTIFICATION from ${notif.app}] Title: ${notif.title}${notif.body ? `. Body: ${notif.body}` : ''}. Mention this naturally and briefly — don't read it out verbatim.`
      );
    });

    return cleanup;
  }, [sendText]);

  // Listen for clipboard changes → inject into AI for contextual awareness
  useEffect(() => {
    const cleanup = window.eve.clipboard.onChanged((entry) => {
      // Only inject interesting clipboard content (not empty/trivial)
      if (entry.type === 'empty') return;

      sendText(
        `[SYSTEM CLIPBOARD — ${entry.type.toUpperCase()}] User just copied: "${entry.preview}". You don't need to mention this unless it's relevant to the conversation or they ask about it.`
      );
    });

    return cleanup;
  }, [sendText]);

  // Listen for agent task completions → proactively notify Friday + mirror into ActionFeed
  useEffect(() => {
    const cleanup = window.eve.agents.onUpdate((task) => {
      // Notify AI on completion / failure
      if (task.status === 'completed' && task.result) {
        const preview = task.result.length > 300 ? task.result.slice(0, 300) + '...' : task.result;
        sendText(
          `[SYSTEM — AGENT COMPLETE] Background task "${task.description}" (${task.agentType}) just finished. Result preview: ${preview}. Mention this proactively if relevant.`
        );
      } else if (task.status === 'failed' && task.error) {
        sendText(
          `[SYSTEM — AGENT FAILED] Background task "${task.description}" failed: ${task.error}. Let the user know briefly.`
        );
      }

      // Mirror running agents into ActionFeed for visual representation
      if (task.status === 'running') {
        setActiveActions((prev) => {
          const existing = prev.find((a) => a.id === task.id);
          if (existing) {
            return prev.map((a) =>
              a.id === task.id
                ? { ...a, progress: task.progress, windowTitle: task.windowTitle }
                : a
            );
          }
          return [
            ...prev,
            {
              id: task.id,
              name: task.agentType,
              status: 'running' as const,
              startTime: task.startedAt || Date.now(),
              isAgent: true,
              description: task.description,
              progress: task.progress,
              windowTitle: task.windowTitle,
            },
          ];
        });
      }

      // Mark completed/failed/cancelled agents → fade out after 5s
      if (task.status === 'completed' || task.status === 'failed' || task.status === 'cancelled') {
        setActiveActions((prev) =>
          prev.map((a) =>
            a.id === task.id
              ? ({ ...a, status: task.status === 'completed' ? 'success' : 'error' } as ActionItem)
              : a
          )
        );
        setTimeout(() => {
          setActiveActions((prev) => prev.filter((a) => a.id !== task.id));
        }, 5000);
      }
    });

    return cleanup;
  }, [sendText]);

  // Listen for sub-agent voice delivery (ElevenLabs TTS) → play MP3 audio
  useEffect(() => {
    const cleanup = window.eve.agents.onSpeak((data) => {
      console.log(`[Agent] ${data.personaName} (${data.personaRole}) speaking — ~${Math.round(data.durationEstimate)}s`);

      // Decode base64 MP3 and play via Web Audio API
      try {
        const binaryString = atob(data.audioBase64);
        const bytes = new Uint8Array(binaryString.length);
        for (let i = 0; i < binaryString.length; i++) {
          bytes[i] = binaryString.charCodeAt(i);
        }

        const blob = new Blob([bytes.buffer], { type: data.contentType });
        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);

        // Show in action feed while speaking
        const speakId = `speak-${data.taskId}`;
        setActiveActions((prev) => [
          ...prev,
          {
            id: speakId,
            name: `${data.personaName} speaking`,
            status: 'running' as const,
            startTime: Date.now(),
            isAgent: true,
            description: data.spokenText.slice(0, 100) + (data.spokenText.length > 100 ? '...' : ''),
          },
        ]);

        audio.onended = () => {
          URL.revokeObjectURL(url);
          setActiveActions((prev) =>
            prev.map((a) =>
              a.id === speakId ? ({ ...a, status: 'success' } as ActionItem) : a
            )
          );
          setTimeout(() => {
            setActiveActions((prev) => prev.filter((a) => a.id !== speakId));
          }, 3000);
        };

        audio.onerror = () => {
          URL.revokeObjectURL(url);
          console.warn(`[Agent] Failed to play ${data.personaName}'s audio`);
          setActiveActions((prev) => prev.filter((a) => a.id !== speakId));
        };

        audio.play().catch((err) => {
          console.warn(`[Agent] Audio play failed for ${data.personaName}:`, err);
          URL.revokeObjectURL(url);
          setActiveActions((prev) => prev.filter((a) => a.id !== speakId));
        });
      } catch (err) {
        console.warn('[Agent] Failed to decode agent voice audio:', err);
      }
    });

    return cleanup;
  }, []);

  // Listen for meeting briefings → inject into AI for proactive context
  useEffect(() => {
    const cleanup = window.eve.meetingPrep.onBriefing((briefing) => {
      console.log(`[Friday] Meeting briefing: "${briefing.eventTitle}" in ${briefing.minutesUntil}m`);

      const attendeeInfo = briefing.attendeeContext
        .map((a) => {
          const parts = [a.name];
          if (a.memories.length > 0) parts.push(`(${a.memories.join('; ')})`);
          return parts.join(' ');
        })
        .join(', ');

      sendText(
        `[MEETING BRIEFING] "${briefing.eventTitle}" starts in ${briefing.minutesUntil} minutes.` +
        (attendeeInfo ? ` Attendees: ${attendeeInfo}.` : '') +
        (briefing.relevantProjects.length > 0 ? ` Related projects: ${briefing.relevantProjects.join(', ')}.` : '') +
        (briefing.suggestedTopics.length > 0 ? ` Topics: ${briefing.suggestedTopics.slice(0, 3).join(', ')}.` : '') +
        ` Mention this naturally — give the user a heads-up about the meeting and any useful context about the attendees.`
      );
    });

    return cleanup;
  }, [sendText]);

  // Record user interactions for idle detection
  useEffect(() => {
    if (!geminiLive.isListening) return;

    // When user is speaking (mic active), record interaction periodically
    const timer = setInterval(() => {
      window.eve.predictor.recordInteraction().catch(() => {});
    }, 10_000);

    return () => clearInterval(timer);
  }, [geminiLive.isListening]);

  // Listen for desktop tool confirmation requests
  useEffect(() => {
    const cleanup = window.eve.confirmation.onRequest((req) => {
      setPendingConfirmation(req);
    });
    return cleanup;
  }, []);

  const handleConfirmation = useCallback((approved: boolean) => {
    if (!pendingConfirmation) return;
    window.eve.confirmation.respond(pendingConfirmation.id, approved);
    setPendingConfirmation(null);
  }, [pendingConfirmation]);

  // Listen for self-improvement code proposals
  useEffect(() => {
    const cleanup = window.eve.selfImprove.onProposal((proposal) => {
      setCodeProposal(proposal);
    });
    return cleanup;
  }, []);

  const handleCodeProposal = useCallback((approved: boolean) => {
    if (!codeProposal) return;
    window.eve.selfImprove.respondToProposal(codeProposal.id, approved);
    setCodeProposal(null);
  }, [codeProposal]);

  // Audio cues on state transitions
  const prevConnectedRef = useRef(false);
  const prevListeningRef = useRef(false);

  useEffect(() => {
    if (geminiLive.isConnected && !prevConnectedRef.current) {
      playConnectedChime();
    }
    if (!geminiLive.isConnected && prevConnectedRef.current) {
      playDisconnectTone();
    }
    prevConnectedRef.current = geminiLive.isConnected;
  }, [geminiLive.isConnected]);

  useEffect(() => {
    if (geminiLive.isListening && !prevListeningRef.current) {
      playListeningPing();
    }
    prevListeningRef.current = geminiLive.isListening;
  }, [geminiLive.isListening]);

  // Status updates
  useEffect(() => {
    if (geminiLive.isSpeaking) {
      setStatus('Speaking...');
    } else if (geminiLive.isListening) {
      setStatus('Listening...');
    } else if (geminiLive.isConnected) {
      setStatus('Connected');
    } else if (geminiLive.error) {
      setStatus(geminiLive.error);
    }
  }, [geminiLive.isListening, geminiLive.isSpeaking, geminiLive.isConnected, geminiLive.error]);

  // Handle text message send — routes to local conversation OR Gemini
  const handleTextSend = useCallback(
    async (text: string) => {
      // Add user message to chat history immediately (optimistic)
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: 'user',
          content: text,
          timestamp: Date.now(),
        },
      ]);

      // Route through local conversation if active
      if (localConversationActiveRef.current) {
        sendText(text);
        window.eve.predictor.recordInteraction().catch(() => {});
        return;
      }

      // Auto-connect if not already connected (may start local or Gemini path)
      if (!geminiLive.isConnected && !geminiLive.isConnecting) {
        await connectToGemini();
      }

      // After connect, check again — connectToGemini may have activated local path
      if (localConversationActiveRef.current) {
        sendText(text);
      } else if (geminiLive.isConnected) {
        geminiLive.sendTextToGemini(text);
      } else {
        // No backend available — show error in chat so user isn't left hanging
        setMessages((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            role: 'assistant' as const,
            content: 'I couldn\'t connect to any backend. Please check that Ollama is running or that you have a valid API key configured in Settings.',
            timestamp: Date.now(),
          },
        ]);
      }

      // Reset idle behavior — user is active
      geminiLive.resetIdleActivity();

      // Record interaction for predictor
      window.eve.predictor.recordInteraction().catch(() => {});
    },
    [geminiLive.isConnected, geminiLive.isConnecting, geminiLive.sendTextToGemini, geminiLive.resetIdleActivity, connectToGemini, sendText]
  );

  // Keyboard: Space to mute/unmute, Tab to toggle text input
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Ctrl+K toggles quick actions palette
      if (e.ctrlKey && e.code === 'KeyK') {
        e.preventDefault();
        setShowQuickActions((s) => !s);
        return;
      }

      // Ctrl+Shift+D toggles command center dashboard
      if (e.ctrlKey && e.shiftKey && e.code === 'KeyD') {
        e.preventDefault();
        appManager.toggleApp('dashboard');
        return;
      }

      // Ctrl+Shift+M toggles memory explorer
      if (e.ctrlKey && e.shiftKey && e.code === 'KeyM') {
        e.preventDefault();
        appManager.toggleApp('memory');
        return;
      }

      // Ctrl+Shift+A toggles agent dashboard
      if (e.ctrlKey && e.shiftKey && e.code === 'KeyA') {
        e.preventDefault();
        appManager.toggleApp('agents');
        return;
      }

      // Ctrl+Shift+P toggles superpowers panel
      if (e.ctrlKey && e.shiftKey && e.code === 'KeyP') {
        e.preventDefault();
        appManager.toggleApp('superpowers');
        return;
      }

      // Ctrl+Shift+C toggles calendar
      if (e.ctrlKey && e.shiftKey && e.code === 'KeyC') {
        e.preventDefault();
        appManager.toggleApp('calendar');
        return;
      }

      // Tab handled by TextInput component directly (always visible now)

      // Space toggles mic — only in voice mode, only from body (not while typing)
      if (voiceMode && e.code === 'Space' && e.target === document.body) {
        e.preventDefault();
        geminiLive.resetIdleActivity();
        if (geminiLive.isListening) {
          geminiLive.stopListening();
        } else if (geminiLive.isConnected) {
          geminiLive.startListening();
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [voiceMode, geminiLive.isListening, geminiLive.isConnected, geminiLive.startListening, geminiLive.stopListening, geminiLive.resetIdleActivity]);

  // B2: RAF loop for audio levels — avoids re-renders, reads directly from AnalyserNodes
  const audioLevelsRef = useRef({ mic: 0, output: 0 });
  useEffect(() => {
    let rafId: number;
    const tick = () => {
      audioLevelsRef.current.mic = geminiLive.getMicLevel();
      audioLevelsRef.current.output = geminiLive.getOutputLevel();
      rafId = requestAnimationFrame(tick);
    };
    rafId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafId);
  }, [geminiLive.getMicLevel, geminiLive.getOutputLevel]);

  const getLevels = useCallback(() => audioLevelsRef.current, []);

  // ─── Semantic state for 3D scene ────────────────────────────────────────────
  const semanticState: SemanticState = useMemo(() => {
    const hasRunningAgents = activeActions.some((a) => a.status === 'running' && a.isAgent);
    const hasRunningTools = activeActions.some((a) => a.status === 'running' && !a.isAgent);
    if (hasRunningAgents) return 'SUB_AGENTS';
    if (hasRunningTools) return 'EXECUTING';
    if (geminiLive.isSpeaking || geminiLive.isConnecting) return 'REASONING';
    return 'LISTENING';
  }, [activeActions, geminiLive.isSpeaking, geminiLive.isConnecting]);

  // ─── Live clock for HUD overlay ────────────────────────────────────────────
  const [clockStr, setClockStr] = useState(() => {
    const d = new Date();
    return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true });
  });
  useEffect(() => {
    const tick = () => {
      const d = new Date();
      setClockStr(d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true }));
    };
    tick();
    const id = setInterval(tick, 10_000); // update every 10s is fine for HH:MM
    return () => clearInterval(id);
  }, []);

  const handleRetry = useCallback(() => {
    retriesRef.current = 0;
    setRetryCount(0);
    setConnectionError('');
    connectToGemini();
  }, [connectToGemini]);

  const handleOrbClick = useCallback(() => {
    // Any orb interaction = user is active
    geminiLive.resetIdleActivity();

    if (!geminiLive.isConnected && !geminiLive.isConnecting) {
      handleRetry();
      return;
    }

    if (geminiLive.isListening) {
      geminiLive.stopListening();
    } else if (geminiLive.isConnected) {
      geminiLive.startListening();
    }
  }, [geminiLive.isListening, geminiLive.isConnected, geminiLive.isConnecting, geminiLive.startListening, geminiLive.stopListening, geminiLive.resetIdleActivity, handleRetry]);

  // Office window shortcut — hooks are already called above, so this is safe.
  if (isOfficeWindow) return <OfficeApp />;

  return (
    <MoodProvider semanticState={semanticState}>
    <div style={styles.container}>
      {/* PassphraseGate — vault must be unlocked before anything else (returning users) */}
      {appPhase === 'passphrase-gate' && (
        <PassphraseGate
          onUnlocked={async () => {
            // Vault is unlocked — check if onboarding is done
            let done = false;
            try { done = await window.eve.onboarding.isComplete(); } catch {}
            if (done) {
              setAppPhase('normal');
              try {
                const config = await window.eve.onboarding.getAgentConfig();
                setAgentName(config.agentName || '');
              } catch {}
              connectToGemini();
            } else {
              setAppPhase('onboarding');
            }
          }}
        />
      )}

      {/* OnboardingWizard — cinematic first-run wizard (replaces WelcomeGate) */}
      {appPhase === 'onboarding' && (
        <OnboardingWizard
          onComplete={(name) => {
            setAgentName(name);
            setAppPhase('creating');

            // Stop local voice conversation if it was the active path
            if (localConversationActiveRef.current) {
              localConversationActiveRef.current = false;
              setLocalConversationActive(false);
              window.eve.localConversation.stop().catch(() => {});
              for (const cleanup of localConversationCleanupsRef.current) cleanup();
              localConversationCleanupsRef.current = [];
            }
          }}
          connectToGemini={connectToGemini}
          sendTextToGemini={sendText}
        />
      )}

      {/* DesktopViz — holographic 3D base layer, hidden during gate/onboarding/customizing */}
      <div style={{
        opacity: ['creating', 'normal'].includes(appPhase) ? 1 : 0,
        transition: 'opacity 2s ease-in',
        pointerEvents: ['passphrase-gate', 'onboarding', 'checking'].includes(appPhase) ? 'none' as const : 'auto' as const,
        position: 'absolute' as const,
        inset: 0,
      }}>
        <MoodDesktopViz
          getLevels={getLevels}
          semanticState={semanticState}
          isSpeaking={geminiLive.isSpeaking}
          isListening={geminiLive.isListening}
          evolutionIndex={desktopEvolution.evolutionIndex}
          transitionBlend={desktopEvolution.transitionBlend}
        />
      </div>

      {/* ─── HUD Overlays (hidden during gate/checking/onboarding) ─── */}

      {/* Minimal drag region at top — always visible for window dragging */}
      <div style={styles.dragBar} />

      {/* ─── HudOverlay — holographic HUD with API panel, app tray, evolution controls ─── */}
      {!['checking', 'passphrase-gate', 'onboarding'].includes(appPhase) && (
        <HudOverlay
          apiStatus={apiStatus}
          semanticState={semanticState}
          evolutionIndex={desktopEvolution.evolutionIndex}
          onEvolutionChange={desktopEvolution.setEvolution}
          onOpenApp={appManager.openApp}
          clockStr={clockStr}
          devMode={false}
        />
      )}

      {/* Main chat panel — front and center, hidden during gate/checking/onboarding */}
      {!['checking', 'passphrase-gate', 'onboarding'].includes(appPhase) && (
      <div style={styles.main}>
        <div style={styles.chatPanel}>
          {/* Chat messages area */}
          <div style={styles.chatMessages}>
            <ChatHistory messages={messages} />
          </div>

          {/* Voice orb — only shown when voice mode is active */}
          {voiceMode && (
            <div style={styles.voiceOrbArea}>
              <MoodVoiceOrb
                isListening={geminiLive.isListening}
                isProcessing={geminiLive.isConnecting}
                isStreaming={geminiLive.isSpeaking}
                onClick={handleOrbClick}
                interimTranscript={geminiLive.transcript || geminiLive.error}
                getLevels={getLevels}
              />
            </div>
          )}

          {/* Text input area at bottom */}
          <div style={styles.chatInputArea}>
            <TextInput
              onSend={handleTextSend}
              isConnected={geminiLive.isConnected}
              isLocalActive={localConversationActive}
            />
            {/* Voice mode toggle */}
            <button
              onClick={() => setVoiceMode((v) => !v)}
              className="hover-bright"
              aria-label={voiceMode ? 'Disable voice mode' : 'Enable voice mode'}
              title={voiceMode ? 'Voice mode ON — click to disable' : 'Enable voice mode'}
              style={{
                ...styles.voiceModeToggle,
                borderColor: voiceMode ? 'rgba(0, 240, 255, 0.4)' : 'rgba(255,255,255,0.1)',
                color: voiceMode ? '#00f0ff' : '#555568',
                background: voiceMode ? 'rgba(0, 240, 255, 0.08)' : 'rgba(255,255,255,0.03)',
              }}
            >
              {voiceMode ? '🎤 Voice On' : '🎤 Voice'}
            </button>
          </div>

          <StatusBar status={status} isWebcamActive={geminiLive.isWebcamActive} isInCall={geminiLive.isInCall} apiStatus={apiStatus} />
        </div>
      </div>
      )}

      {/* Action feed — animated tool execution indicators + agent cards */}
      <ActionFeed actions={activeActions} onOpenAgentDashboard={() => appManager.openApp('agents')} />

      {/* File modification toasts — clickable paths to open in Explorer */}
      <FileToast />

      {/* Agent creation animation overlay */}
      {appPhase === 'creating' && (
        <AgentCreation
          agentName={agentName}
          onComplete={async () => {
            // Reconnect with the agent's configured voice & personality
            try {
              const newInstruction = await window.eve.getLiveSystemInstruction();
              let voiceName = 'Kore';
              try {
                const cfg = await window.eve.onboarding.getAgentConfig();
                voiceName = String(cfg.agentVoice || 'Kore');
              } catch { /* use default */ }

              let tools: Array<{ name: string; description?: string; parameters?: unknown }> = [];
              try { tools = await window.eve.desktop.listTools(); } catch {}
              try {
                const fsTools = await window.eve.featureSetup.getToolDeclarations();
                tools = [...tools, ...fsTools];
              } catch {}

              // Check if we have a Gemini key — route accordingly
              let hasGeminiKey = false;
              try {
                const key = await window.eve.getGeminiApiKey();
                hasGeminiKey = !!(key && typeof key === 'string' && key.trim().length > 0);
              } catch { /* no key */ }

              if (!hasGeminiKey) {
                // LOCAL PATH — reconnect via local conversation
                console.log('[Agent] Post-creation: reconnecting via local conversation');

                // Stop any existing local conversation first
                if (localConversationActiveRef.current) {
                  localConversationActiveRef.current = false;
                  setLocalConversationActive(false);
                  await window.eve.localConversation.stop().catch(() => {});
                }

                setAppPhase('normal');
                // connectToGemini handles local path when no Gemini key
                await connectToGemini();

                // Send first greeting via local path after a brief delay
                try {
                  const greeting = await window.eve.onboarding.getFirstGreeting();
                  if (greeting) setTimeout(() => sendText(greeting), 1500);
                } catch {}
              } else {
                // GEMINI PATH — existing cloud reconnect
                await geminiLive.connect(newInstruction, tools, voiceName);
                // Start mic capture after connect
                try { await geminiLive.startListening(); } catch (e) { console.warn('[Agent] Mic start failed:', e); }
                setStatus('Connected');
                setAppPhase('normal');

                // First greeting
                try {
                  const greeting = await window.eve.onboarding.getFirstGreeting();
                  if (greeting) setTimeout(() => geminiLive.sendTextToGemini(greeting), 1500);
                } catch {}
              }
            } catch (err) {
              console.error('[Agent] Reconnect after creation failed:', err);
              setConnectionError(err instanceof Error ? err.message : String(err));
              setAppPhase('normal');
            }
          }}
        />
      )}

      {/* Connection error overlay */}
      <ConnectionOverlay
        error={connectionError}
        isConnecting={geminiLive.isConnecting}
        retryCount={retryCount}
        maxRetries={maxRetries}
        onRetry={handleRetry}
        onOpenSettings={() => {
          setConnectionError('');
          appManager.openApp('settings');
        }}
        isLocalMode={localConversationActive}
      />

      {/* Desktop tool confirmation toast */}
      {pendingConfirmation && (
        <div style={styles.confirmOverlay}>
          <div style={styles.confirmToast}>
            <div style={styles.confirmIcon}>⚡</div>
            <div style={styles.confirmBody}>
              <div style={styles.confirmTitle}>{agentName || 'Agent'} wants to execute:</div>
              <div style={styles.confirmDesc}>{pendingConfirmation.description}</div>
              <div style={styles.confirmTool}>{pendingConfirmation.toolName}</div>
            </div>
            <div style={styles.confirmActions}>
              <button
                onClick={() => handleConfirmation(true)}
                style={styles.confirmAllow}
              >
                Allow
              </button>
              <button
                onClick={() => handleConfirmation(false)}
                style={styles.confirmDeny}
              >
                Deny
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Self-improvement code proposal diff viewer */}
      {codeProposal && (
        <div style={styles.confirmOverlay}>
          <div style={styles.diffViewer}>
            <div style={styles.diffHeader}>
              <div style={styles.diffIcon}>🧬</div>
              <div style={styles.diffHeaderText}>
                <div style={styles.confirmTitle}>{agentName || 'Agent'} wants to modify its own code</div>
                <div style={styles.diffFilePath}>{codeProposal.filePath}</div>
              </div>
            </div>
            <div style={styles.diffDescription}>{codeProposal.description}</div>
            <div style={styles.diffContent}>
              {codeProposal.diff.split('\n').map((line, i) => {
                let lineStyle: React.CSSProperties = styles.diffLineNormal;
                if (line.startsWith('+') && !line.startsWith('+++')) {
                  lineStyle = styles.diffLineAdd;
                } else if (line.startsWith('-') && !line.startsWith('---')) {
                  lineStyle = styles.diffLineRemove;
                } else if (line.startsWith('@@')) {
                  lineStyle = styles.diffLineHunk;
                } else if (line.startsWith('---') || line.startsWith('+++')) {
                  lineStyle = styles.diffLineFile;
                }
                return (
                  <div key={i} style={lineStyle}>
                    {line}
                  </div>
                );
              })}
            </div>
            <div style={styles.confirmActions}>
              <button
                onClick={() => handleCodeProposal(true)}
                style={styles.confirmAllow}
              >
                Approve Change
              </button>
              <button
                onClick={() => handleCodeProposal(false)}
                style={styles.confirmDeny}
              >
                Deny
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Quick actions palette (Ctrl+K) */}
      <QuickActions
        visible={showQuickActions}
        onClose={() => setShowQuickActions(false)}
        onSendText={handleTextSend}
        isConnected={geminiLive.isConnected}
      />

      {/* ── App Registry — dynamic overlays for all registered apps ── */}
      {APP_REGISTRY.map((app) => (
        <Suspense key={app.id} fallback={null}>
          <app.component
            visible={appManager.isOpen(app.id)}
            onClose={() => appManager.closeApp(app.id)}
          />
        </Suspense>
      ))}
    </div>
    </MoodProvider>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    width: '100%',
    height: '100%',
    position: 'relative',
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
  },

  dragBar: {
    height: 32,
    WebkitAppRegion: 'drag' as unknown as string,
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    zIndex: 50,
  } as React.CSSProperties,
  main: {
    flex: 1,
    display: 'flex',
    overflow: 'hidden',
    paddingTop: 80,
    paddingBottom: 0,
  },
  chatPanel: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
    padding: '0 20px 0 20px',
    maxWidth: 860,
    margin: '0 auto',
    width: '100%',
  },
  chatMessages: {
    flex: 1,
    overflowY: 'auto',
    minHeight: 0,
  },
  voiceOrbArea: {
    display: 'flex',
    justifyContent: 'center',
    padding: '12px 0 4px 0',
    flexShrink: 0,
  },
  chatInputArea: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 8,
    paddingTop: 8,
    paddingBottom: 4,
    flexShrink: 0,
  },
  voiceModeToggle: {
    border: '1px solid',
    borderRadius: 20,
    padding: '5px 14px',
    fontSize: 12,
    fontWeight: 600,
    cursor: 'pointer',
    transition: 'all 0.2s',
    letterSpacing: '0.03em',
    opacity: 0.75,
  },
  center: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    position: 'relative',
    gap: 24,
  },
  confirmOverlay: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    display: 'flex',
    alignItems: 'flex-start',
    justifyContent: 'center',
    paddingTop: 80,
    zIndex: 60,
    background: 'rgba(0, 0, 0, 0.4)',
    backdropFilter: 'blur(4px)',
    animation: 'fadeIn 0.2s ease',
  },
  confirmToast: {
    background: 'rgba(6, 11, 25, 0.95)',
    border: '1px solid rgba(0, 240, 255, 0.25)',
    borderRadius: 16,
    padding: '20px 24px',
    maxWidth: 480,
    width: '90%',
    display: 'flex',
    flexDirection: 'column' as const,
    gap: 16,
    boxShadow: '0 8px 32px rgba(0, 0, 0, 0.5), 0 0 20px rgba(0, 240, 255, 0.1)',
  },
  confirmIcon: {
    fontSize: 24,
    textAlign: 'center' as const,
  },
  confirmBody: {
    display: 'flex',
    flexDirection: 'column' as const,
    gap: 8,
  },
  confirmTitle: {
    fontSize: 13,
    fontWeight: 600,
    color: '#00f0ff',
    letterSpacing: '0.03em',
    textTransform: 'uppercase' as const,
  },
  confirmDesc: {
    fontSize: 14,
    color: '#e0e0e8',
    lineHeight: '1.5',
    fontFamily: "'JetBrains Mono', monospace",
    background: 'rgba(255, 255, 255, 0.04)',
    padding: '10px 14px',
    borderRadius: 8,
    border: '1px solid rgba(255, 255, 255, 0.06)',
    wordBreak: 'break-all' as const,
  },
  confirmTool: {
    fontSize: 11,
    color: '#666680',
    fontFamily: "'JetBrains Mono', monospace",
  },
  confirmActions: {
    display: 'flex',
    gap: 10,
    justifyContent: 'flex-end',
  },
  confirmAllow: {
    background: 'rgba(0, 240, 255, 0.15)',
    border: '1px solid rgba(0, 240, 255, 0.3)',
    borderRadius: 8,
    color: '#00f0ff',
    fontSize: 13,
    fontWeight: 600,
    padding: '8px 20px',
    cursor: 'pointer',
    transition: 'all 0.15s',
  },
  confirmDeny: {
    background: 'rgba(239, 68, 68, 0.1)',
    border: '1px solid rgba(239, 68, 68, 0.25)',
    borderRadius: 8,
    color: '#ef4444',
    fontSize: 13,
    fontWeight: 600,
    padding: '8px 20px',
    cursor: 'pointer',
    transition: 'all 0.15s',
  },
  diffViewer: {
    background: 'rgba(6, 11, 25, 0.97)',
    border: '1px solid rgba(0, 240, 255, 0.2)',
    borderRadius: 16,
    padding: '20px 24px',
    maxWidth: 720,
    width: '95%',
    maxHeight: '80vh',
    display: 'flex',
    flexDirection: 'column' as const,
    gap: 14,
    boxShadow: '0 12px 48px rgba(0, 0, 0, 0.6), 0 0 30px rgba(0, 240, 255, 0.08)',
    animation: 'fadeIn 0.25s ease',
  },
  diffHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
  },
  diffIcon: {
    fontSize: 28,
  },
  diffHeaderText: {
    display: 'flex',
    flexDirection: 'column' as const,
    gap: 4,
  },
  diffFilePath: {
    fontSize: 12,
    color: '#888',
    fontFamily: "'JetBrains Mono', monospace",
  },
  diffDescription: {
    fontSize: 13,
    color: '#c0c0d0',
    lineHeight: '1.5',
    padding: '8px 12px',
    background: 'rgba(255, 255, 255, 0.03)',
    borderRadius: 8,
    border: '1px solid rgba(255, 255, 255, 0.05)',
  },
  diffContent: {
    flex: 1,
    overflowY: 'auto' as const,
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 12,
    lineHeight: '1.6',
    background: 'rgba(0, 0, 0, 0.3)',
    borderRadius: 10,
    padding: '12px 0',
    border: '1px solid rgba(255, 255, 255, 0.06)',
    maxHeight: '45vh',
  },
  diffLineNormal: {
    padding: '1px 14px',
    color: '#a0a0b0',
  } as React.CSSProperties,
  diffLineAdd: {
    padding: '1px 14px',
    color: '#4ade80',
    background: 'rgba(74, 222, 128, 0.08)',
  } as React.CSSProperties,
  diffLineRemove: {
    padding: '1px 14px',
    color: '#f87171',
    background: 'rgba(248, 113, 113, 0.08)',
  } as React.CSSProperties,
  diffLineHunk: {
    padding: '4px 14px',
    color: '#818cf8',
    fontWeight: 600,
    fontSize: 11,
    background: 'rgba(129, 140, 248, 0.06)',
    marginTop: 4,
  } as React.CSSProperties,
  diffLineFile: {
    padding: '1px 14px',
    color: '#666',
    fontStyle: 'italic' as const,
  } as React.CSSProperties,
};
