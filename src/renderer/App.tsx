import React, { useCallback, useRef, useEffect, useMemo, Suspense } from 'react';
import { SemanticState } from './components/FridayCore';
import HudOverlay from './components/HudOverlay';
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
import { MoodProvider } from './contexts/MoodContext';
import { MoodDesktopViz, MoodVoiceOrb } from './components/MoodWrappers';
import { useGeminiLive } from './hooks/useGeminiLive';
import { AudioPlaybackEngine } from './audio/AudioPlaybackEngine';
import { useWakeWord } from './hooks/useWakeWord';
import { useVoiceState } from './hooks/useVoiceState';
import { useDesktopEvolution } from './hooks/useDesktopEvolution';
import { useAppManager } from './hooks/useAppManager';
import { APP_REGISTRY } from './registry/app-registry';
import { playConnectedChime, playListeningPing, playDisconnectTone } from './audio/sound-effects';
import { useAppStore } from './store';
import type { ChatMessage } from './store';
import { useIPCListeners } from './hooks/useIPCListeners';
import { useKeyboardShortcuts } from './hooks/useKeyboardShortcuts';
import { useAudioLevels } from './hooks/useAudioLevels';
import { useLocalMicCapture } from './hooks/useLocalMicCapture';

// Re-export ChatMessage for backward compatibility
export type { ChatMessage } from './store';

// ── Office window detection ──────────────────────────────────────────────
// If loaded with ?office=true, render the pixel-art Agent Office instead
const isOfficeWindow = new URLSearchParams(window.location.search).get('office') === 'true';

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

  // ── Zustand store ──────────────────────────────────────────────────────
  const messages = useAppStore((s) => s.messages);
  const setMessages = useAppStore((s) => s.setMessages);
  const status = useAppStore((s) => s.status);
  const setStatus = useAppStore((s) => s.setStatus);
  const showQuickActions = useAppStore((s) => s.showQuickActions);
  const setShowQuickActions = useAppStore((s) => s.setShowQuickActions);
  const connectionError = useAppStore((s) => s.connectionError);
  const setConnectionError = useAppStore((s) => s.setConnectionError);
  const retryCount = useAppStore((s) => s.retryCount);
  const setRetryCount = useAppStore((s) => s.setRetryCount);
  const pendingConfirmation = useAppStore((s) => s.pendingConfirmation);
  const setPendingConfirmation = useAppStore((s) => s.setPendingConfirmation);
  const codeProposal = useAppStore((s) => s.codeProposal);
  const setCodeProposal = useAppStore((s) => s.setCodeProposal);
  const activeActions = useAppStore((s) => s.activeActions);
  const setActiveActions = useAppStore((s) => s.setActiveActions);
  const wakeWordEnabled = useAppStore((s) => s.wakeWordEnabled);
  const setWakeWordEnabled = useAppStore((s) => s.setWakeWordEnabled);
  const voiceMode = useAppStore((s) => s.voiceMode);
  const setVoiceMode = useAppStore((s) => s.setVoiceMode);
  const appPhase = useAppStore((s) => s.appPhase);
  const setAppPhase = useAppStore((s) => s.setAppPhase);
  const agentName = useAppStore((s) => s.agentName);
  const setAgentName = useAppStore((s) => s.setAgentName);
  const evolutionState = useAppStore((s) => s.evolutionState);
  const setEvolutionState = useAppStore((s) => s.setEvolutionState);
  const apiStatus = useAppStore((s) => s.apiStatus);
  const setApiStatus = useAppStore((s) => s.setApiStatus);
  const clockStr = useAppStore((s) => s.clockStr);
  const setClockStr = useAppStore((s) => s.setClockStr);
  const localConversationActive = useAppStore((s) => s.localConversationActive);
  const setLocalConversationActive = useAppStore((s) => s.setLocalConversationActive);

  const appManager = useAppManager();
  const appPhaseRef = useRef(appPhase);
  useEffect(() => { appPhaseRef.current = appPhase; }, [appPhase]);
  const desktopEvolution = useDesktopEvolution();
  const retriesRef = useRef(0);
  const maxRetries = 3;

  // ── Voice State Machine integration (Track 6) ───────────────────────
  // Provides canonical voice pipeline state from the main-process state machine.
  // Components can consume voiceState.isActive / isConnecting / isDegraded
  // instead of tracking scattered booleans.
  const voiceState = useVoiceState();

  // ── Local voice conversation state (fallback when no Gemini key) ──────
  const localConversationActiveRef = useRef(false);
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
      if (appPhaseRef.current === 'onboarding') {
        window.dispatchEvent(new CustomEvent('interview-ai-response', { detail: { text, streaming: true } }));
      }
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
    setConnectionError('');

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

    // ── Track 6: Set path priorities via VoiceFallbackManager ──────────
    // The fallback manager tracks priorities for future use (e.g. mid-session
    // failover). Actual connection + renderer-side listener setup is handled
    // by the legacy code below, which properly wires onTranscript, onResponse,
    // AudioPlaybackEngine, localConversationActiveRef, and geminiLive.connect().
    try {
      if (window.eve.voiceFallback) {
        if (!onboardingComplete) {
          await window.eve.voiceFallback.setPathPriority('local', 0).catch(() => {});
        }
      }
    } catch {
      // VoiceFallbackManager not available — no-op
    }

    // ── Legacy path (pre-Track 6 behavior) ────────────────────────────
    // This code path is kept for graceful degradation when the new voice
    // components haven't been initialized yet.

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
          if (appPhaseRef.current === 'onboarding') {
            window.dispatchEvent(new CustomEvent('interview-user-transcript', { detail: { text } }));
            window.dispatchEvent(new CustomEvent('interview-processing-state', { detail: { state: 'thinking' } }));
          }
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
          if (appPhaseRef.current === 'onboarding') {
            window.dispatchEvent(new CustomEvent('interview-ai-response', { detail: { text } }));
            window.dispatchEvent(new CustomEvent('interview-processing-state', { detail: { state: 'speaking' } }));
            setTimeout(() => {
              if (appPhaseRef.current === 'onboarding') {
                window.dispatchEvent(new CustomEvent('interview-processing-state', { detail: { state: 'listening' } }));
              }
            }, 2000);
          }
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
            // LLM errors must appear in chat so the user knows what happened
            if (error.startsWith('LLM error:')) {
              setMessages((prev) => [
                ...prev,
                {
                  id: crypto.randomUUID(),
                  role: 'assistant' as const,
                  content: `⚠ ${error}`,
                  timestamp: Date.now(),
                },
              ]);
            }
          } else {
            // Session failed to start entirely — show as connection error
            setConnectionError(error);
            setStatus(`Local voice error: ${error}`);
          }
        }),
      );

      // Instant barge-in: flush renderer audio when VAD detects user speaking
      cleanups.push(
        window.eve.localConversation.onBargeIn(() => {
          console.log('[Agent] Barge-in — flushing audio playback');
          localPlaybackRef.current?.flush();
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

        // Check if TTS actually loaded — if not during onboarding, fall back to
        // Gemini for full voice. During normal use, text-only local is fine.
        let ttsReady = false;
        try { ttsReady = await window.eve.voice.tts.isReady(); } catch { /* not available */ }

        if (!ttsReady && !onboardingComplete && hasGeminiKey) {
          // Onboarding needs voice — fall back to Gemini Live
          console.warn('[Agent] Local TTS unavailable during onboarding — falling back to Gemini Live');
          try { await window.eve.localConversation.stop(); } catch { /* best effort */ }
          localConversationActiveRef.current = false;
          setLocalConversationActive(false);
          for (const cleanup of localConversationCleanupsRef.current) cleanup();
          localConversationCleanupsRef.current = [];
          setStatus('Local TTS unavailable — connecting via Gemini...');
          // Fall through to Gemini path below
        } else {
          // Local conversation is running — always keep it alive for text mode
          localConversationActiveRef.current = true;
          setLocalConversationActive(true);
          setStatus(ttsReady ? 'Connected (Local)' : 'Connected (Local — text mode)');
          setConnectionError('');
          retriesRef.current = 0;
          setRetryCount(0);
          window.dispatchEvent(new Event('gemini-audio-active'));
          return;
        }
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
          window.dispatchEvent(new CustomEvent('interview-connection-failed', {
            detail: { message: `Local voice unavailable: ${msg}. Add a Gemini API key for cloud voice.` },
          }));
          return;
        }
        setStatus('Local voice unavailable — connecting via Gemini...');
      }
    }

    // ── 6b. GEMINI CLOUD FALLBACK — WebSocket connection ─────────────
    if (!hasGeminiKey) {
      setConnectionError('No voice backend available. Install Ollama for local voice or add a Gemini API key.');
      setStatus('No voice backend');
      window.dispatchEvent(new CustomEvent('interview-connection-failed', {
        detail: { message: 'No voice backend available. Install Ollama for local voice or add a Gemini API key.' },
      }));
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

  // ── Wrapped sendText: routes to local conversation when active ──
  const sendText = useCallback(
    (text: string) => {
      if (appPhaseRef.current === 'onboarding') {
        window.dispatchEvent(new CustomEvent('interview-user-transcript', { detail: { text } }));
        window.dispatchEvent(new CustomEvent('interview-processing-state', { detail: { state: 'thinking' } }));
      }
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
    window.eve.settings.checkApiHealth().then((raw) => {
      const health = raw as Record<string, string>;
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
    // eslint-disable-next-line react-hooks/exhaustive-deps — init-only, runs once on mount
  }, []);

  // ── IPC event listeners (extracted to hook) ──────────────────────────
  useIPCListeners(sendText);

  // Record user interactions for idle detection
  useEffect(() => {
    if (!geminiLive.isListening) return;

    // When user is speaking (mic active), record interaction periodically
    const timer = setInterval(() => {
      window.eve.predictor.recordInteraction().catch(() => {});
    }, 10_000);

    return () => clearInterval(timer);
  }, [geminiLive.isListening]);

  const handleConfirmation = useCallback((approved: boolean) => {
    if (!pendingConfirmation) return;
    window.eve.confirmation.respond(pendingConfirmation.id, approved);
    setPendingConfirmation(null);
  }, [pendingConfirmation, setPendingConfirmation]);

  const handleCodeProposal = useCallback((approved: boolean) => {
    if (!codeProposal) return;
    window.eve.selfImprove.respondToProposal(codeProposal.id, approved);
    setCodeProposal(null);
  }, [codeProposal, setCodeProposal]);

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

  // Status updates — merge Gemini Live state with Voice State Machine
  useEffect(() => {
    // Voice State Machine takes priority when it reports active/degraded/error states
    if (voiceState.isActive && !geminiLive.isConnected && !localConversationActive) {
      // State machine says active but legacy state disagrees — trust the machine
      setStatus('Connected');
    } else if (voiceState.isDegraded) {
      setStatus('Voice connection degraded');
    } else if (voiceState.state === 'TEXT_FALLBACK') {
      setStatus('Text mode — voice unavailable');
    } else if (voiceState.state === 'ERROR') {
      setConnectionError('Voice pipeline error');
    } else if (geminiLive.isSpeaking) {
      setStatus('Speaking...');
    } else if (geminiLive.isListening) {
      setStatus('Listening...');
    } else if (geminiLive.isConnected) {
      setStatus('Connected');
    } else if (localConversationActive) {
      setStatus('Connected (Local)');
    } else if (geminiLive.error) {
      setStatus(geminiLive.error);
    }
  }, [geminiLive.isListening, geminiLive.isSpeaking, geminiLive.isConnected, geminiLive.error, voiceState.state, voiceState.isActive, voiceState.isDegraded, localConversationActive]);

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

      try {
        // Route through local conversation if active
        if (localConversationActiveRef.current) {
          sendText(text);
          window.eve.predictor.recordInteraction().catch(() => {});
          return;
        }

        // Auto-connect if not already connected.
        // Wait for in-progress connection too — don't fall through to the error
        // branch while the initial connectToGemini() from mount is still running.
        if (!geminiLive.isConnected && !localConversationActiveRef.current) {
          if (geminiLive.isConnecting) {
            // Connection already in progress — wait for it to settle (up to 30s)
            await new Promise<void>((resolve) => {
              const interval = setInterval(() => {
                if (geminiLive.isConnected || localConversationActiveRef.current || !geminiLive.isConnecting) {
                  clearInterval(interval);
                  resolve();
                }
              }, 200);
              setTimeout(() => { clearInterval(interval); resolve(); }, 30_000);
            });
          } else {
            await connectToGemini();
          }
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
      } catch (err) {
        // Catch-all: ensure the user always sees feedback
        const msg = err instanceof Error ? err.message : String(err);
        console.error('[Agent] handleTextSend error:', msg);
        setMessages((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            role: 'assistant' as const,
            content: `Something went wrong: ${msg}. Check that Ollama is running or try again.`,
            timestamp: Date.now(),
          },
        ]);
      }

      // Record interaction for predictor
      window.eve.predictor.recordInteraction().catch(() => {});
    },
    [geminiLive.isConnected, geminiLive.isConnecting, geminiLive.sendTextToGemini, geminiLive.resetIdleActivity, connectToGemini, sendText]
  );

  // ── Keyboard shortcuts (extracted to hook) ──────────────────────────
  useKeyboardShortcuts(appManager, geminiLive);

  // ── Local mic capture for Whisper STT pipeline ────────────────────────
  // Bridges renderer getUserMedia → main process AudioCapture via IPC.
  // Activates when main process sends voice:start-capture (during local conversation).
  useLocalMicCapture();

  // ── Audio levels RAF loop (extracted to hook) ──────────────────────────
  const getLevels = useAudioLevels(geminiLive.getMicLevel, geminiLive.getOutputLevel);

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
  useEffect(() => {
    const tick = () => {
      const d = new Date();
      setClockStr(d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true }));
    };
    tick();
    const id = setInterval(tick, 10_000); // update every 10s is fine for HH:MM
    return () => clearInterval(id);
  }, [setClockStr]);

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

            // Restore default voice path priorities after onboarding
            window.eve.voiceFallback?.setPathPriority?.('local', 2).catch(() => {});
            window.eve.voiceFallback?.setPathPriority?.('cloud', 1).catch(() => {});

            // Stop local voice conversation if it was the active path
            if (localConversationActiveRef.current) {
              localConversationActiveRef.current = false;
              setLocalConversationActive(false);
              window.eve.localConversation.stop().catch(() => {});
              for (const cleanup of localConversationCleanupsRef.current) cleanup();
              localConversationCleanupsRef.current = [];
            }
          }}
          connectVoice={connectToGemini}
          sendText={sendText}
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
