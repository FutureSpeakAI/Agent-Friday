/**
 * Shared types for the Gemini Live hook modules.
 */

import type { AudioPlaybackEngine } from '../../audio/AudioPlaybackEngine';
import type { SessionManager } from '../../session/SessionManager';
import type { IdleBehavior, IdleTier } from '../../session/IdleBehavior';

// ── Public API types ──

export interface UseGeminiLiveOptions {
  onTextResponse?: (text: string) => void;
  onClaudeUsed?: (question: string, answer: string) => void;
  onError?: (error: string) => void;
  onToolStart?: (id: string, name: string) => void;
  onToolEnd?: (id: string, name: string, success: boolean) => void;
  onAgentFinalized?: (config: Record<string, unknown>) => void;
  onPhaseChange?: (phase: 'onboarding' | 'creating' | 'normal') => void;
}

export interface GeminiLiveState {
  isConnected: boolean;
  isConnecting: boolean;
  isListening: boolean;
  isSpeaking: boolean;
  isWebcamActive: boolean;
  isInCall: boolean;
  transcript: string;
  error: string;
  idleTier: IdleTier;
}

// ── Tool execution context — passed to tool-executor ──

export interface ToolExecutionContext {
  wsRef: React.MutableRefObject<WebSocket | null>;
  setupCompleteRef: React.MutableRefObject<boolean>;
  playbackEngineRef: React.MutableRefObject<AudioPlaybackEngine | null>;
  webcamStreamRef: React.MutableRefObject<MediaStream | null>;
  webcamIntervalRef: React.MutableRefObject<ReturnType<typeof setInterval> | null>;
  webcamVideoRef: React.MutableRefObject<HTMLVideoElement | null>;
  webcamCanvasRef: React.MutableRefObject<HTMLCanvasElement | null>;
  mcpToolNamesRef: React.MutableRefObject<Set<string>>;
  optionsRef: React.MutableRefObject<UseGeminiLiveOptions>;
  setState: React.Dispatch<React.SetStateAction<GeminiLiveState>>;
  getApiBase: () => Promise<string>;
}

// ── Bundled refs interface — all React refs in the hook ──

export interface GeminiRefs {
  wsRef: React.MutableRefObject<WebSocket | null>;
  audioContextRef: React.MutableRefObject<AudioContext | null>;
  streamRef: React.MutableRefObject<MediaStream | null>;
  workletNodeRef: React.MutableRefObject<AudioWorkletNode | null>;
  processorRef: React.MutableRefObject<ScriptProcessorNode | null>;
  playbackEngineRef: React.MutableRefObject<AudioPlaybackEngine | null>;
  sessionManagerRef: React.MutableRefObject<SessionManager | null>;
  screenFrameCleanupRef: React.MutableRefObject<(() => void) | null>;
  micAnalyserRef: React.MutableRefObject<AnalyserNode | null>;
  micAnalyserDataRef: React.MutableRefObject<Uint8Array | null>;
  intentionalDisconnectRef: React.MutableRefObject<boolean>;
  wsReconnectAttemptsRef: React.MutableRefObject<number>;
  startListeningRef: React.MutableRefObject<(() => Promise<void>) | null>;
  idleBehaviorRef: React.MutableRefObject<IdleBehavior | null>;
  keepaliveRef: React.MutableRefObject<ReturnType<typeof setInterval> | null>;
  lastServerMessageRef: React.MutableRefObject<number>;
  responseWatchdogRef: React.MutableRefObject<ReturnType<typeof setInterval> | null>;
  ambientContextCacheRef: React.MutableRefObject<string>;
  stateRef: React.MutableRefObject<GeminiLiveState>;
  optionsRef: React.MutableRefObject<UseGeminiLiveOptions>;
  apiPortRef: React.MutableRefObject<number | null>;
  toolsRef: React.MutableRefObject<Array<{ name: string; description?: string; parameters?: unknown; inputSchema?: unknown }>>;
  voiceNameRef: React.MutableRefObject<string>;
  agentAccentRef: React.MutableRefObject<string>;
  agentNameRef: React.MutableRefObject<string>;
  mcpToolNamesRef: React.MutableRefObject<Set<string>>;
  smReconnectingRef: React.MutableRefObject<boolean>;
  isAutoReconnectingRef: React.MutableRefObject<boolean>;
  setupCompleteRef: React.MutableRefObject<boolean>;
  reconnectStabilizingRef: React.MutableRefObject<boolean>;
  onboardingModeRef: React.MutableRefObject<boolean>;
  webcamStreamRef: React.MutableRefObject<MediaStream | null>;
  webcamIntervalRef: React.MutableRefObject<ReturnType<typeof setInterval> | null>;
  webcamVideoRef: React.MutableRefObject<HTMLVideoElement | null>;
  webcamCanvasRef: React.MutableRefObject<HTMLCanvasElement | null>;
  sendTextRef: React.MutableRefObject<((text: string) => void) | null>;
  audioHealthTimerRef: React.MutableRefObject<ReturnType<typeof setInterval> | null>;
}

// ── External tool shape (from App.tsx / preload) ──

export interface ExternalToolDecl {
  name: string;
  description?: string;
  parameters?: unknown;
  inputSchema?: unknown;
}
