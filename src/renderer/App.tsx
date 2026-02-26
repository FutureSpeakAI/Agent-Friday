import React, { useState, useCallback, useRef, useEffect, useMemo } from 'react';
import NexusCore, { SemanticState } from './components/NexusCore';
import VoiceOrb from './components/VoiceOrb';
import ChatHistory from './components/ChatHistory';
import StatusBar from './components/StatusBar';
import TextInput from './components/TextInput';
import Settings from './components/Settings';
import AgentDashboard from './components/AgentDashboard';
import Dashboard from './components/Dashboard';
import QuickActions from './components/QuickActions';
import MemoryExplorer from './components/MemoryExplorer';
import ConnectionOverlay from './components/ConnectionOverlay';
import AgentCreation from './components/AgentCreation';
import WelcomeGate from './components/WelcomeGate';
import ActionFeed, { ActionItem } from './components/ActionFeed';
import { MoodProvider, useMood } from './contexts/MoodContext';
import { useGeminiLive } from './hooks/useGeminiLive';
import { useWakeWord } from './hooks/useWakeWord';
import {
  playConnectedChime,
  playListeningPing,
  playNotificationBell,
  playDisconnectTone,
} from './audio/sound-effects';

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

function MoodNexusCore({ getLevels, semanticState, isSpeaking, evolutionState }: {
  getLevels: () => { mic: number; output: number };
  semanticState: SemanticState;
  isSpeaking: boolean;
  evolutionState?: { sessionCount: number; primaryHue: number; secondaryHue: number; particleSpeed: number; cubeFragmentation: number; coreScale: number; dustDensity: number; glowIntensity: number } | null;
}) {
  const mood = useMood();
  return (
    <NexusCore
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

export default function App() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [status, setStatus] = useState('Initializing...');
  const [showSidebar, setShowSidebar] = useState(false);
  const [showTextInput, setShowTextInput] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [showAgentDashboard, setShowAgentDashboard] = useState(false);
  const [showDashboard, setShowDashboard] = useState(false);
  const [showQuickActions, setShowQuickActions] = useState(false);
  const [showMemoryExplorer, setShowMemoryExplorer] = useState(false);
  const [connectionError, setConnectionError] = useState('');
  const [retryCount, setRetryCount] = useState(0);
  const [pendingConfirmation, setPendingConfirmation] = useState<ConfirmationRequest | null>(null);
  const [codeProposal, setCodeProposal] = useState<CodeProposal | null>(null);
  const [activeActions, setActiveActions] = useState<ActionItem[]>([]);
  const [wakeWordEnabled, setWakeWordEnabled] = useState(true);
  const [appPhase, setAppPhase] = useState<
    'checking' | 'gate' | 'onboarding' | 'customizing' | 'creating' | 'feature-setup' | 'normal'
  >('checking');
  const [agentName, setAgentName] = useState('');
  const [evolutionState, setEvolutionState] = useState<{
    sessionCount: number; primaryHue: number; secondaryHue: number;
    particleSpeed: number; cubeFragmentation: number; coreScale: number;
    dustDensity: number; glowIntensity: number;
  } | null>(null);
  const retriesRef = useRef(0);
  const maxRetries = 3;

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

        // Check if feature setup is already done
        let featureSetupDone = false;
        try {
          featureSetupDone = await window.eve.featureSetup.isComplete();
        } catch {}

        // Gather all tools for the new session
        let tools: Array<{ name: string; description?: string; parameters?: unknown }> = [];
        try {
          tools = await window.eve.desktop.listTools();
        } catch {}

        // If feature setup pending, inject the feature setup tool
        if (!featureSetupDone) {
          try {
            const fsToolDecl = await window.eve.featureSetup.getToolDeclaration();
            tools = [...tools, fsToolDecl];
          } catch {}
        }

        try {
          await geminiLive.connect(newInstruction, tools, voice);
          setStatus('Connected — Listening');
          geminiLive.startListening();

          // Transition to feature setup or normal
          setAppPhase(featureSetupDone ? 'normal' : 'feature-setup');

          // Send the first greeting prompt to get the agent to introduce themselves
          if (firstGreeting) {
            setTimeout(async () => {
              geminiLive.sendTextToGemini(firstGreeting);

              // After greeting, if feature setup is needed, send the first step prompt
              if (!featureSetupDone) {
                setTimeout(async () => {
                  try {
                    const step = await window.eve.featureSetup.getCurrentStep();
                    if (step) {
                      const prompt = await window.eve.featureSetup.getPrompt(step);
                      geminiLive.sendTextToGemini(prompt);
                    }
                  } catch {}
                }, 8000);
              }
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

  const connectToGemini = useCallback(async () => {
    setStatus('Connecting...');

    // 1. Determine if this is onboarding or a normal session
    let onboardingComplete = false;
    try {
      onboardingComplete = await window.eve.onboarding.isComplete();
    } catch {
      // Assume not complete if check fails
    }

    // 2. Gather desktop tools
    let tools: Array<{ name: string; description?: string; parameters?: unknown }> = [];
    try {
      tools = await window.eve.desktop.listTools();
    } catch {
      // Desktop tools unavailable — that's fine
    }

    // 3. If onboarding, inject onboarding + intake tool declarations
    if (!onboardingComplete) {
      try {
        const onboardingTool = await window.eve.onboarding.getToolDeclaration();
        tools = [...tools, onboardingTool];
      } catch (err) {
        console.warn('[Agent] Failed to get onboarding tool declaration:', err);
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
      } catch {}
    }

    // 6. Connect!
    try {
      await geminiLive.connect(instruction, tools, voiceName);
      retriesRef.current = 0;
      setRetryCount(0);
      setConnectionError('');

      // Auto-start listening immediately — hands-free experience
      setStatus('Connected — Listening');
      geminiLive.startListening();

      // After connection stabilizes, inject context
      setTimeout(async () => {
        try {
          if (!onboardingComplete) {
            // First run — nudge Gemini to begin onboarding conversation
            console.log('[Agent] First run detected — starting onboarding');
            geminiLive.sendTextToGemini(
              '[SYSTEM — FIRST RUN] This is the user\'s first time. Begin the onboarding conversation. Greet them warmly and start getting to know them.'
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

  // Load wake word setting
  useEffect(() => {
    window.eve.settings.get().then((s) => {
      setWakeWordEnabled(s.wakeWordEnabled !== false);
    }).catch(() => {});
  }, []);

  // Wake word detection — auto-connect when "Hey EVE" is detected while idle
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
      } catch {}

      if (cancelled) return;

      if (onboardingDone) {
        // Returning user — skip gate + onboarding, go straight to normal
        setAppPhase('normal');

        // Load agent name
        try {
          const config = await window.eve.onboarding.getAgentConfig();
          setAgentName(config.agentName || '');
        } catch {}

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

      // New user — check if API keys exist
      let hasKeys = false;
      try {
        const settings = await window.eve.settings.get();
        hasKeys = !!settings.hasGeminiKey && !!settings.hasAnthropicKey;
      } catch {}

      if (cancelled) return;

      if (!hasKeys) {
        // No keys — show the WelcomeGate
        setAppPhase('gate');
      } else {
        // Keys exist but onboarding not done — start onboarding
        setAppPhase('onboarding');
        connectToGemini();
      }
    })();

    return () => {
      cancelled = true;
      geminiLive.disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Listen for scheduler task-fired events → inject into Gemini so EVE speaks them
  useEffect(() => {
    const cleanup = window.eve.scheduler.onTaskFired((task) => {
      console.log('[Agent] Task fired:', task.description);
      playNotificationBell();

      if (task.action === 'remind' && geminiLive.sendTextToGemini) {
        geminiLive.sendTextToGemini(
          `[SYSTEM REMINDER — speak this naturally to the user] Reminder: ${task.payload}`
        );
      } else if (task.action === 'launch_app') {
        window.eve.desktop
          .callTool('launch_app', { app_name: task.payload })
          .then(() => {
            geminiLive.sendTextToGemini?.(
              `[SYSTEM] I just launched ${task.payload} as scheduled. Let the user know briefly.`
            );
          })
          .catch((err) => console.warn('[Friday] Scheduled launch failed:', err));
      } else if (task.action === 'run_command') {
        window.eve.desktop
          .callTool('run_command', { command: task.payload })
          .then((result) => {
            geminiLive.sendTextToGemini?.(
              `[SYSTEM] Scheduled command executed: ${task.description}. Result: ${result.result || result.error || 'Done'}`
            );
          })
          .catch((err) => console.warn('[Friday] Scheduled command failed:', err));
      }
    });

    return cleanup;
  }, [geminiLive.sendTextToGemini]);

  // Listen for predictive suggestions → inject into Gemini so EVE speaks them naturally
  useEffect(() => {
    const cleanup = window.eve.predictor.onSuggestion((suggestion) => {
      console.log(`[Friday] Prediction: ${suggestion.type} (${suggestion.confidence})`);
      playNotificationBell();

      if (geminiLive.sendTextToGemini) {
        geminiLive.sendTextToGemini(
          `[SYSTEM SUGGESTION — speak this naturally in character, keep it brief and charming] ${suggestion.message}`
        );
      }
    });

    return cleanup;
  }, [geminiLive.sendTextToGemini]);

  // Listen for captured notifications → inject into Gemini so EVE announces them naturally
  useEffect(() => {
    const cleanup = window.eve.notifications.onCaptured((notif) => {
      console.log(`[Friday] Notification captured: ${notif.app} — ${notif.title}`);

      if (geminiLive.sendTextToGemini) {
        geminiLive.sendTextToGemini(
          `[SYSTEM NOTIFICATION from ${notif.app}] Title: ${notif.title}${notif.body ? `. Body: ${notif.body}` : ''}. Mention this naturally and briefly — don't read it out verbatim.`
        );
      }
    });

    return cleanup;
  }, [geminiLive.sendTextToGemini]);

  // Listen for clipboard changes → inject into Gemini for contextual awareness
  useEffect(() => {
    const cleanup = window.eve.clipboard.onChanged((entry) => {
      if (!geminiLive.sendTextToGemini) return;

      // Only inject interesting clipboard content (not empty/trivial)
      if (entry.type === 'empty') return;

      geminiLive.sendTextToGemini(
        `[SYSTEM CLIPBOARD — ${entry.type.toUpperCase()}] User just copied: "${entry.preview}". You don't need to mention this unless it's relevant to the conversation or they ask about it.`
      );
    });

    return cleanup;
  }, [geminiLive.sendTextToGemini]);

  // Listen for agent task completions → proactively notify EVE + mirror into ActionFeed
  useEffect(() => {
    const cleanup = window.eve.agents.onUpdate((task) => {
      // Notify Gemini on completion / failure
      if (task.status === 'completed' && task.result && geminiLive.sendTextToGemini) {
        const preview = task.result.length > 300 ? task.result.slice(0, 300) + '...' : task.result;
        geminiLive.sendTextToGemini(
          `[SYSTEM — AGENT COMPLETE] Background task "${task.description}" (${task.agentType}) just finished. Result preview: ${preview}. Mention this proactively if relevant.`
        );
      } else if (task.status === 'failed' && task.error && geminiLive.sendTextToGemini) {
        geminiLive.sendTextToGemini(
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
  }, [geminiLive.sendTextToGemini]);

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

  // Listen for meeting briefings → inject into Gemini for proactive context
  useEffect(() => {
    const cleanup = window.eve.meetingPrep.onBriefing((briefing) => {
      console.log(`[Friday] Meeting briefing: "${briefing.eventTitle}" in ${briefing.minutesUntil}m`);

      if (geminiLive.sendTextToGemini) {
        const attendeeInfo = briefing.attendeeContext
          .map((a) => {
            const parts = [a.name];
            if (a.memories.length > 0) parts.push(`(${a.memories.join('; ')})`);
            return parts.join(' ');
          })
          .join(', ');

        geminiLive.sendTextToGemini(
          `[MEETING BRIEFING] "${briefing.eventTitle}" starts in ${briefing.minutesUntil} minutes.` +
          (attendeeInfo ? ` Attendees: ${attendeeInfo}.` : '') +
          (briefing.relevantProjects.length > 0 ? ` Related projects: ${briefing.relevantProjects.join(', ')}.` : '') +
          (briefing.suggestedTopics.length > 0 ? ` Topics: ${briefing.suggestedTopics.slice(0, 3).join(', ')}.` : '') +
          ` Mention this naturally — give the user a heads-up about the meeting and any useful context about the attendees.`
        );
      }
    });

    return cleanup;
  }, [geminiLive.sendTextToGemini]);

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

  // Handle text message send
  const handleTextSend = useCallback(
    (text: string) => {
      if (!geminiLive.isConnected) return;

      // Add user message to chat history
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: 'user',
          content: text,
          timestamp: Date.now(),
        },
      ]);

      // Send to Gemini
      geminiLive.sendTextToGemini(text);

      // Reset idle behavior — user is active
      geminiLive.resetIdleActivity();

      // Record interaction for predictor
      window.eve.predictor.recordInteraction().catch(() => {});
    },
    [geminiLive.isConnected, geminiLive.sendTextToGemini, geminiLive.resetIdleActivity]
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
        setShowDashboard((s) => !s);
        return;
      }

      // Ctrl+Shift+M toggles memory explorer
      if (e.ctrlKey && e.shiftKey && e.code === 'KeyM') {
        e.preventDefault();
        setShowMemoryExplorer((s) => !s);
        return;
      }

      // Ctrl+Shift+A toggles agent dashboard
      if (e.ctrlKey && e.shiftKey && e.code === 'KeyA') {
        e.preventDefault();
        setShowAgentDashboard((s) => !s);
        return;
      }

      // Tab toggles text input mode (only when not already typing in an input)
      if (e.code === 'Tab' && e.target === document.body) {
        e.preventDefault();
        setShowTextInput((s) => !s);
        return;
      }

      // Space toggles mic (only from body — not while typing)
      if (e.code === 'Space' && e.target === document.body) {
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
  }, [geminiLive.isListening, geminiLive.isConnected, geminiLive.startListening, geminiLive.stopListening, geminiLive.resetIdleActivity]);

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

  return (
    <MoodProvider semanticState={semanticState}>
    <div style={styles.container}>
      {/* WelcomeGate — shown when API keys are missing */}
      {appPhase === 'gate' && (
        <WelcomeGate
          onKeysReady={() => {
            setAppPhase('onboarding');
            connectToGemini();
          }}
        />
      )}

      {/* NexusCore — hidden during gate/onboarding/customizing, revealed during creating */}
      <div style={{
        opacity: ['creating', 'feature-setup', 'normal'].includes(appPhase) ? 1 : 0,
        transition: 'opacity 2s ease-in',
        pointerEvents: ['gate', 'onboarding', 'customizing', 'checking'].includes(appPhase) ? 'none' as const : 'auto' as const,
        position: 'absolute' as const,
        inset: 0,
      }}>
        <MoodNexusCore
          getLevels={getLevels}
          semanticState={semanticState}
          isSpeaking={geminiLive.isSpeaking}
          evolutionState={evolutionState}
        />
      </div>

      {/* ─── HUD Overlays (hidden during gate/checking/onboarding) ─── */}

      {/* Minimal drag region at top — always visible for window dragging */}
      <div style={styles.dragBar} />

      {!['checking', 'gate'].includes(appPhase) && (<>
      {/* Brand badge — top-left */}
      <div style={styles.brandBadge}>
        <div style={styles.brandTitle}>AGENT FRIDAY</div>
        <MoodBrandSub semanticState={semanticState} />
      </div>

      {/* Clock — top-center */}
      <div style={styles.clockOverlay}>{clockStr}</div>

      {/* Status label — bottom-center, above StatusBar */}
      <MoodStatusLabel
        semanticState={semanticState}
        statusText={`GEMINI LIVE // ${geminiLive.isConnected
          ? (geminiLive.isSpeaking ? 'STREAMING RESPONSE' : geminiLive.isListening ? 'AWAITING AUDIO' : 'CONNECTED')
          : geminiLive.isConnecting ? 'ESTABLISHING LINK' : 'OFFLINE'}`}
      />

      {/* Sidebar toggle */}
      <button
        onClick={() => setShowSidebar((s) => !s)}
        className="hover-bright"
        style={{
          ...styles.sidebarToggle,
          opacity: showSidebar ? 0.8 : 0.4,
        }}
        title={showSidebar ? 'Hide chat log' : 'Show chat log'}
      >
        {showSidebar ? '\u2190' : '\u2261'}
      </button>

      {/* Command center button */}
      <button
        onClick={() => setShowDashboard(true)}
        className="hover-bright"
        style={styles.dashboardBtn}
        title="Command Center (Ctrl+Shift+D)"
      >
        ◈
      </button>

      {/* Agent dashboard button */}
      <button
        onClick={() => setShowAgentDashboard(true)}
        className="hover-bright"
        style={styles.agentsBtn}
        title="Background Agents (Ctrl+Shift+A)"
      >
        ⚡
      </button>

      {/* Settings gear */}
      <button
        onClick={() => setShowSettings(true)}
        className="hover-bright"
        style={styles.settingsBtn}
        title="Settings"
      >
        ⚙
      </button>
      </>)}

      {/* Voice orb + text input — hidden during gate/checking */}
      {!['checking', 'gate'].includes(appPhase) && (
      <div style={styles.main}>
        {/* Center — the orb is the entire interface */}
        <div style={styles.center}>
          <MoodVoiceOrb
            isListening={geminiLive.isListening}
            isProcessing={geminiLive.isConnecting}
            isStreaming={geminiLive.isSpeaking}
            onClick={handleOrbClick}
            interimTranscript={geminiLive.transcript || geminiLive.error}
            getLevels={getLevels}
          />
          <TextInput
            visible={showTextInput}
            onSend={handleTextSend}
            onClose={() => setShowTextInput(false)}
          />
          <StatusBar status={status} isWebcamActive={geminiLive.isWebcamActive} isInCall={geminiLive.isInCall} />
        </div>
      </div>
      )}

      {/* Animated sidebar overlay */}
      <div
        className={`sidebar-backdrop${showSidebar ? ' visible' : ''}`}
        onClick={() => setShowSidebar(false)}
      />
      <div className={`sidebar-panel${showSidebar ? ' open' : ''}`}>
        <ChatHistory messages={messages} />
      </div>

      {/* Action feed — animated tool execution indicators + agent cards */}
      <ActionFeed actions={activeActions} onOpenAgentDashboard={() => setShowAgentDashboard(true)} />

      {/* Agent creation animation overlay */}
      {appPhase === 'creating' && (
        <AgentCreation agentName={agentName} />
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
          setShowSettings(true);
        }}
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

      {/* Command center dashboard overlay */}
      <Dashboard visible={showDashboard} onClose={() => setShowDashboard(false)} />

      {/* Memory explorer overlay */}
      <MemoryExplorer visible={showMemoryExplorer} onClose={() => setShowMemoryExplorer(false)} />

      {/* Agent dashboard overlay */}
      <AgentDashboard visible={showAgentDashboard} onClose={() => setShowAgentDashboard(false)} />

      {/* Settings overlay */}
      <Settings visible={showSettings} onClose={() => setShowSettings(false)} />
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

  // ─── HUD overlay styles ─────────────────────────────────────────────────────
  brandBadge: {
    position: 'absolute',
    top: 48,
    left: 20,
    zIndex: 30,
    pointerEvents: 'none',
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
  },
  brandTitle: {
    fontSize: 13,
    fontWeight: 800,
    letterSpacing: '0.22em',
    color: 'rgba(255, 255, 255, 0.55)',
    textTransform: 'uppercase' as const,
    fontFamily: "'Inter', 'Segoe UI', sans-serif",
  },
  brandSub: {
    fontSize: 10,
    fontWeight: 600,
    letterSpacing: '0.15em',
    fontFamily: "'Fira Code', 'JetBrains Mono', monospace",
    transition: 'color 0.6s ease',
  },
  clockOverlay: {
    position: 'absolute',
    top: 44,
    left: '50%',
    transform: 'translateX(-50%)',
    zIndex: 30,
    pointerEvents: 'none',
    fontSize: 14,
    fontWeight: 300,
    letterSpacing: '0.08em',
    color: 'rgba(255, 255, 255, 0.35)',
    fontFamily: "'Inter', 'Segoe UI', sans-serif",
  },
  statusLabel: {
    position: 'absolute',
    bottom: 48,
    left: '50%',
    transform: 'translateX(-50%)',
    zIndex: 30,
    pointerEvents: 'none',
    fontSize: 10,
    fontWeight: 600,
    letterSpacing: '0.18em',
    fontFamily: "'Fira Code', 'JetBrains Mono', monospace",
    transition: 'color 0.6s ease',
    whiteSpace: 'nowrap',
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
  sidebarToggle: {
    position: 'absolute',
    top: 40,
    left: 12,
    zIndex: 40,
    background: 'rgba(255,255,255,0.06)',
    border: '1px solid rgba(255,255,255,0.1)',
    borderRadius: 6,
    color: '#e0e0e8',
    fontSize: 18,
    width: 32,
    height: 32,
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    transition: 'opacity 0.2s',
  },
  main: {
    flex: 1,
    display: 'flex',
    overflow: 'hidden',
  },
  dashboardBtn: {
    position: 'absolute',
    top: 40,
    right: 88,
    zIndex: 40,
    background: 'rgba(255,255,255,0.06)',
    border: '1px solid rgba(255,255,255,0.1)',
    borderRadius: 6,
    color: '#00f0ff',
    fontSize: 14,
    width: 32,
    height: 32,
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    opacity: 0.35,
    transition: 'opacity 0.2s',
  },
  agentsBtn: {
    position: 'absolute',
    top: 40,
    right: 50,
    zIndex: 40,
    background: 'rgba(255,255,255,0.06)',
    border: '1px solid rgba(255,255,255,0.1)',
    borderRadius: 6,
    color: '#e0e0e8',
    fontSize: 14,
    width: 32,
    height: 32,
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    opacity: 0.3,
    transition: 'opacity 0.2s',
  },
  settingsBtn: {
    position: 'absolute',
    top: 40,
    right: 12,
    zIndex: 40,
    background: 'rgba(255,255,255,0.06)',
    border: '1px solid rgba(255,255,255,0.1)',
    borderRadius: 6,
    color: '#e0e0e8',
    fontSize: 16,
    width: 32,
    height: 32,
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    opacity: 0.3,
    transition: 'opacity 0.2s',
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
