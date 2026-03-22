import { useState, useRef, useCallback, useEffect } from 'react';
import { AudioPlaybackEngine } from '../audio/AudioPlaybackEngine';
import { SessionManager } from '../session/SessionManager';
import { IdleBehavior, type IdleTier } from '../session/IdleBehavior';

// Extracted modules
import type {
  UseGeminiLiveOptions,
  GeminiLiveState,
  GeminiRefs,
  ToolExecutionContext,
} from './gemini/types';
import { sanitizeSchema, buildFunctionDeclarations } from './gemini/tool-declarations';
import { base64ToFloat32 } from './gemini/audio-helpers';
import { executeToolCall } from './gemini/tool-executor';
import { startMicPipeline, stopMicPipeline } from './gemini/mic-pipeline';
import {
  setupSleepResumeDetection,
  setupTabFocusRecovery,
  setupMicHealthMonitor,
  setupPeriodicMemoryExtraction,
  setupAgentResultSurfacing,
  setupAmbientContextPolling,
} from './gemini/session-lifecycle';

// Re-export public types so existing imports work
export type { GeminiLiveState } from './gemini/types';

const GEMINI_WS_URL =
  'wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent';

export function useGeminiLive(options: UseGeminiLiveOptions = {}) {
  const [state, setState] = useState<GeminiLiveState>({
    isConnected: false,
    isConnecting: false,
    isListening: false,
    isSpeaking: false,
    isWebcamActive: false,
    isInCall: false,
    transcript: '',
    error: '',
    idleTier: 0,
  });

  // ── All refs bundled for passing to extracted modules ──
  const wsRef = useRef<WebSocket | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const workletNodeRef = useRef<AudioWorkletNode | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const playbackEngineRef = useRef<AudioPlaybackEngine | null>(null);
  const sessionManagerRef = useRef<SessionManager | null>(null);
  const screenFrameCleanupRef = useRef<(() => void) | null>(null);
  const micAnalyserRef = useRef<AnalyserNode | null>(null);
  const micAnalyserDataRef = useRef<Uint8Array | null>(null);
  const intentionalDisconnectRef = useRef(false);
  const wsReconnectAttemptsRef = useRef(0);
  const startListeningRef = useRef<(() => Promise<void>) | null>(null);
  const idleBehaviorRef = useRef<IdleBehavior | null>(null);
  const keepaliveRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const lastServerMessageRef = useRef<number>(Date.now());
  const responseWatchdogRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const ambientContextCacheRef = useRef<string>('');
  const stateRef = useRef(state);
  stateRef.current = state;
  const optionsRef = useRef(options);
  const apiPortRef = useRef<number | null>(null);
  const toolsRef = useRef<Array<{ name: string; description?: string; parameters?: unknown; inputSchema?: unknown }>>([]);
  const voiceNameRef = useRef<string>('Kore');
  const agentAccentRef = useRef<string>('');
  const agentNameRef = useRef<string>('');
  const mcpToolNamesRef = useRef<Set<string>>(new Set());
  const smReconnectingRef = useRef(false);
  const isAutoReconnectingRef = useRef(false);
  const setupCompleteRef = useRef(false);
  const reconnectStabilizingRef = useRef(false);
  const onboardingModeRef = useRef(false);
  const webcamStreamRef = useRef<MediaStream | null>(null);
  const webcamIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const webcamVideoRef = useRef<HTMLVideoElement | null>(null);
  const webcamCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const sendTextRef = useRef<((text: string) => void) | null>(null);
  const audioHealthTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  optionsRef.current = options;

  // Bundle refs for passing to extracted modules
  const refs: GeminiRefs = {
    wsRef, audioContextRef, streamRef, workletNodeRef, processorRef,
    playbackEngineRef, sessionManagerRef, screenFrameCleanupRef,
    micAnalyserRef, micAnalyserDataRef, intentionalDisconnectRef,
    wsReconnectAttemptsRef, startListeningRef, idleBehaviorRef,
    keepaliveRef, lastServerMessageRef, responseWatchdogRef,
    ambientContextCacheRef, stateRef, optionsRef, apiPortRef,
    toolsRef, voiceNameRef, agentAccentRef, agentNameRef,
    mcpToolNamesRef, smReconnectingRef, isAutoReconnectingRef,
    setupCompleteRef, reconnectStabilizingRef, onboardingModeRef,
    webcamStreamRef, webcamIntervalRef, webcamVideoRef, webcamCanvasRef,
    sendTextRef, audioHealthTimerRef,
  };

  // Initialize playback engine once
  if (!playbackEngineRef.current) {
    playbackEngineRef.current = new AudioPlaybackEngine();
    playbackEngineRef.current.setSpeakingCallback((speaking) => {
      setState((s) => ({ ...s, isSpeaking: speaking }));
    });
  }

  // Initialize session manager once
  if (!sessionManagerRef.current) {
    sessionManagerRef.current = new SessionManager();
    // Set agent identity for dynamic accent in conversation summaries
    window.eve.onboarding.getAgentConfig().then((config: Record<string, unknown>) => {
      if (config?.agentName && sessionManagerRef.current) {
        sessionManagerRef.current.setAgentIdentity(
          config.agentName as string,
          (config.agentAccent as string) || ''
        );
      }
      // Cache accent/name locally so reconnect never needs async IPC
      agentAccentRef.current = (config?.agentAccent as string) || '';
      agentNameRef.current = (config?.agentName as string) || '';
    }).catch(() => {});
  }

  // Initialize idle behavior once
  if (!idleBehaviorRef.current) {
    idleBehaviorRef.current = new IdleBehavior();
  }

  // --- Get the API base URL for Claude routing ---
  const getApiBase = useCallback(async () => {
    if (!apiPortRef.current) {
      try {
        apiPortRef.current = await window.eve.getApiPort();
      } catch {
        apiPortRef.current = 3333;
      }
    }
    return `http://localhost:${apiPortRef.current}`;
  }, []);

  // --- Tool execution context for tool-executor module ---
  const toolCtx: ToolExecutionContext = {
    wsRef, setupCompleteRef, playbackEngineRef,
    webcamStreamRef, webcamIntervalRef, webcamVideoRef, webcamCanvasRef,
    mcpToolNamesRef, optionsRef, setState, getApiBase,
  };

  // --- Connect to Gemini Live WebSocket ---
  const connectingRef = useRef(false);

  const connect = useCallback(
    async (
      systemInstruction: string,
      externalTools?: Array<{ name: string; description?: string; parameters?: unknown; inputSchema?: unknown }>,
      voiceName?: string,
      options?: { onboarding?: boolean }
    ): Promise<void> => {
      // Re-entry guard: prevent concurrent connect() calls from creating orphaned sockets
      if (connectingRef.current) {
        console.warn('[GeminiLive] connect() already in progress — ignoring duplicate call');
        return;
      }
      connectingRef.current = true;

      try {
      const onboardingMode = options?.onboarding ?? false;
      onboardingModeRef.current = onboardingMode;
      // Store voice name for reconnects
      if (voiceName) voiceNameRef.current = voiceName;
      const apiKey = await window.eve.getGeminiApiKey();
      if (!apiKey) {
        const msg = 'No Gemini API key configured — add one in Settings → API Keys';
        setState((s) => ({ ...s, error: msg }));
        optionsRef.current.onError?.(msg);
        throw new Error(msg);
      }

      // Close old socket with intentional flag to suppress stale onclose reconnect
      if (wsRef.current) {
        intentionalDisconnectRef.current = true;
        wsRef.current.close();
        wsRef.current = null;
      }

      // Store tools for reconnect
      if (externalTools) {
        toolsRef.current = externalTools;
      }

      intentionalDisconnectRef.current = false;
      setState((s) => ({ ...s, isConnecting: true, error: '' }));

      // ── Dynamic tool loading (skipped during onboarding to keep payload small) ──
      let browserToolDecls: Array<{ name: string; description: string; parameters: Record<string, unknown> }> = [];
      let socToolDecls: Array<{ name: string; description: string; parameters: Record<string, unknown> }> = [];
      let gitToolDecls: Array<{ name: string; description: string; parameters: Record<string, unknown> }> = [];
      let connectorToolDecls: Array<{ name: string; description: string; parameters: unknown }> = [];
      let mcpToolDecls: Array<{ name: string; description: string; parameters: unknown }> = [];
      const mcpToolNamesSet = new Set<string>();

      if (!onboardingMode) {
        // Fetch browser tool declarations from main process (Gemini-compatible format)
        try {
          browserToolDecls = await window.eve.browser.listTools();
        } catch (err) {
          console.warn('[GeminiLive] Failed to load browser tools:', err);
        }

        // Fetch SOC (Self-Operating Computer) + Browser-Use tool declarations
        try {
          socToolDecls = await window.eve.soc.listTools();
          if (socToolDecls.length > 0) {
            console.log(`[GeminiLive] Loaded ${socToolDecls.length} SOC/browser-use tools`);
          }
        } catch (err) {
          console.warn('[GeminiLive] SOC tools unavailable:', err);
        }

        // Fetch GitLoader tool declarations
        try {
          gitToolDecls = await window.eve.gitLoader.listTools();
          if (gitToolDecls.length > 0) {
            console.log(`[GeminiLive] Loaded ${gitToolDecls.length} GitLoader tools`);
          }
        } catch (err) {
          console.warn('[GeminiLive] GitLoader tools unavailable:', err);
        }

        // Load connector tools dynamically (only installed software)
        try {
          const connectorTools = await window.eve.connectors.listTools();
          connectorToolDecls = connectorTools.map((t) => ({
            name: t.name,
            description: (t.description || '').slice(0, 512),
            parameters: sanitizeSchema(t.parameters || { type: 'object', properties: {} }),
          }));
          if (connectorToolDecls.length > 0) {
            console.log(`[GeminiLive] Loaded ${connectorToolDecls.length} connector tools`);
          }
        } catch (err) {
          console.warn('[GeminiLive] Connector tools unavailable:', err);
        }

        // Load MCP tools (Desktop Commander, user-added MCP servers, etc.)
        try {
          const mcpTools = await window.eve.mcp.listTools();
          mcpToolDecls = mcpTools
            .filter((t: any) => {
              // Skip MCP tools that conflict with connector tools (connectors take priority)
              const connectorNames = new Set(connectorToolDecls.map((c) => c.name));
              return !connectorNames.has(t.name);
            })
            .map((t: any) => {
              mcpToolNamesSet.add(t.name);
              return {
                name: t.name,
                description: (t.description || '').slice(0, 512),
                parameters: sanitizeSchema(t.inputSchema || t.parameters || { type: 'object', properties: {} }),
              };
            });
          if (mcpToolDecls.length > 0) {
            console.log(`[GeminiLive] Loaded ${mcpToolDecls.length} MCP tools`);
          }
        } catch (err) {
          console.warn('[GeminiLive] MCP tools unavailable:', err);
        }
      }

      // Store MCP tool names in a ref so the execution handler can check them
      mcpToolNamesRef.current = mcpToolNamesSet;

      // Map external tools to Gemini-compatible format
      const mappedExternalTools = (externalTools || toolsRef.current).map((t) => ({
        name: t.name,
        description: (t.description || '').slice(0, 512),
        parameters: sanitizeSchema(t.parameters || t.inputSchema),
      }));

      const functionDeclarations = buildFunctionDeclarations({
        onboardingMode,
        mappedExternalTools,
        browserToolDecls,
        socToolDecls,
        gitToolDecls,
        connectorToolDecls,
        mcpToolDecls,
      });

      console.log(`[GeminiLive] Connecting with ${functionDeclarations.length} tools${onboardingMode ? ' (onboarding mode)' : ''}...`);

      return new Promise<void>((resolve, reject) => {
        // Guard: prevent mic from streaming to this WS until Gemini confirms setup
        setupCompleteRef.current = false;

        // Crypto Sprint 3 (HIGH-001): Known limitation — the browser WebSocket API
        // does NOT support custom headers (no Authorization / x-goog-api-key).
        // Google's Multimodal Live API requires ?key= for WebSocket auth.
        // Mitigations: (1) wss:// ensures TLS encryption in transit, (2) the key is
        // a scoped Google AI Studio key (not a GCP service account), (3) the URL
        // is not logged by this app. Moving to a main-process WebSocket proxy that
        // can set headers would eliminate this, but is a significant refactor.
        const ws = new WebSocket(`${GEMINI_WS_URL}?key=${apiKey}`);
        wsRef.current = ws;

        const timeout = setTimeout(() => {
          if (!ws || ws.readyState === WebSocket.CLOSED) return;
          const msg = 'Connection timed out — Gemini Live did not respond';
          console.error('[GeminiLive]', msg);
          setState((s) => ({ ...s, isConnecting: false, error: msg }));
          optionsRef.current.onError?.(msg);
          ws.close();
          reject(new Error(msg));
        }, 15000);

        ws.onopen = () => {
          console.log('[GeminiLive] WebSocket opened, sending setup...');

          const setup = {
            setup: {
              model: 'models/gemini-2.5-flash-native-audio-preview-12-2025',
              generation_config: {
                response_modalities: ['AUDIO'],
                speech_config: {
                  voice_config: {
                    prebuilt_voice_config: {
                      voice_name: voiceNameRef.current,
                    },
                  },
                },
              },
              realtime_input_config: {
                automatic_activity_detection: {
                  start_of_speech_sensitivity: 'START_SENSITIVITY_HIGH',
                  end_of_speech_sensitivity: 'END_SENSITIVITY_LOW',
                  prefix_padding_ms: 150,
                  silence_duration_ms: 500,
                },
              },
              system_instruction: {
                parts: [{ text: systemInstruction }],
              },
              tools: [{ function_declarations: functionDeclarations }],
            },
          };

          ws.send(JSON.stringify(setup));
        };

        ws.onmessage = async (event) => {
          try {
            const raw =
              typeof event.data === 'string' ? event.data : await (event.data as Blob).text();
            const data = JSON.parse(raw);

            // Catch Gemini error responses (invalid model, auth failure, etc.)
            if (data.error) {
              clearTimeout(timeout);
              const errMsg = data.error.message || data.error.status || JSON.stringify(data.error);
              console.error('[GeminiLive] Server error:', errMsg);
              setState((s) => ({ ...s, isConnecting: false, error: `Gemini error: ${errMsg}` }));
              optionsRef.current.onError?.(`Gemini error: ${errMsg}`);
              ws.close();
              reject(new Error(errMsg));
              return;
            }

            if (data.setupComplete) {
              clearTimeout(timeout);
              setupCompleteRef.current = true;
              console.log('[GeminiLive] Setup complete — ready (mic gate opened)');
              setState((s) => ({ ...s, isConnected: true, isConnecting: false, error: '', idleTier: 0 }));

              if (!smReconnectingRef.current) {
                sessionManagerRef.current?.sessionStarted();
              }
              try { window.eve.sessionHealth.sessionStarted(); } catch { /* ignored */ }
              window.eve.agentTrust.resetSession().catch(() => {});

              // Start WebSocket keepalive (every 8s)
              if (keepaliveRef.current) clearInterval(keepaliveRef.current);
              lastServerMessageRef.current = Date.now();

              const silentPcm = new ArrayBuffer(320);
              const silentB64 = btoa(String.fromCharCode(...new Uint8Array(silentPcm)));

              keepaliveRef.current = setInterval(() => {
                if (ws.readyState === WebSocket.OPEN) {
                  try {
                    ws.send(JSON.stringify({
                      realtime_input: {
                        media_chunks: [{ data: silentB64, mime_type: 'audio/pcm;rate=16000' }],
                      },
                    }));
                  } catch {
                    console.warn('[GeminiLive] Keepalive send failed — dead connection, triggering reconnect');
                    if (keepaliveRef.current) clearInterval(keepaliveRef.current);
                    keepaliveRef.current = null;
                    const sm = sessionManagerRef.current;
                    if (sm) {
                      intentionalDisconnectRef.current = true;
                      sm.requestReconnect();
                    }
                  }
                } else {
                  console.warn('[GeminiLive] Keepalive detected dead socket — readyState:', ws.readyState);
                  if (keepaliveRef.current) clearInterval(keepaliveRef.current);
                  keepaliveRef.current = null;
                }
              }, 8_000);

              // Audio health monitoring
              if (audioHealthTimerRef.current) clearInterval(audioHealthTimerRef.current);
              playbackEngineRef.current?.resetHealthCounters();
              audioHealthTimerRef.current = setInterval(() => {
                const engine = playbackEngineRef.current;
                if (!engine) return;

                const health = engine.getHealthMetrics();

                if (engine.isDegraded() && !smReconnectingRef.current && !isAutoReconnectingRef.current) {
                  console.warn('[GeminiLive] Audio degradation detected — triggering proactive reconnect', health);
                  engine.resetHealthCounters();
                  const sm = sessionManagerRef.current;
                  if (sm) {
                    intentionalDisconnectRef.current = true;
                    sm.requestReconnect();
                  }
                }

                if (health.contextState === 'suspended') {
                  engine.resumeIfSuspended();
                }
              }, 30_000);

              // Auto-start screen capture if enabled
              try {
                const settings = await window.eve.settings.get();
                if (settings.autoScreenCapture && window.eve.screenCapture && !screenFrameCleanupRef.current) {
                  await window.eve.screenCapture.start();
                  const cleanup = window.eve.screenCapture.onFrame((frame: string) => {
                    if (wsRef.current?.readyState === WebSocket.OPEN) {
                      wsRef.current.send(
                        JSON.stringify({
                          realtime_input: {
                            media_chunks: [{ data: frame, mime_type: 'image/jpeg' }],
                          },
                        })
                      );
                    }
                  });
                  screenFrameCleanupRef.current = cleanup;
                  console.log('[GeminiLive] Auto-started screen capture');
                }
              } catch (err) {
                console.warn('[GeminiLive] Auto screen capture failed:', err);
              }

              resolve();
              return;
            }

            // Server content — audio and text responses
            if (data.serverContent) {
              lastServerMessageRef.current = Date.now();
              const parts = data.serverContent.modelTurn?.parts || [];
              for (const part of parts) {
                if (part.text) {
                  setState((s) => ({ ...s, transcript: s.transcript + part.text }));
                  optionsRef.current.onTextResponse?.(part.text);
                  sessionManagerRef.current?.addEntry('assistant', part.text);
                }
                if (part.inlineData?.mimeType?.startsWith('audio/')) {
                  const pcm = base64ToFloat32(part.inlineData.data);
                  playbackEngineRef.current?.enqueue(pcm);
                }
              }

              if (data.serverContent.turnComplete) {
                setState((s) => ({ ...s, transcript: '' }));
              }

              if (data.serverContent.interrupted) {
                console.log('[GeminiLive] Server signalled interruption — flushing audio buffer');
                playbackEngineRef.current?.flush();
              }
            }

            // Handle goAway
            if (data.goAway) {
              console.warn('[GeminiLive] Received goAway — server will disconnect soon, triggering graceful reconnect');
              intentionalDisconnectRef.current = true;
              const sm = sessionManagerRef.current;
              if (sm) {
                setTimeout(() => sm.requestReconnect(), 500);
              }
            }

            // Tool calls — route via tool-executor module
            if (data.toolCall) {
              lastServerMessageRef.current = Date.now();
              const calls = data.toolCall.functionCalls || [];

              const responsePromises = calls.map(
                (fc: { id: string; name: string; args?: Record<string, unknown> }) =>
                  executeToolCall(fc, toolCtx)
              );

              const responses = await Promise.all(responsePromises);
              ws.send(JSON.stringify({ toolResponse: { functionResponses: responses } }));
            }
          } catch (err) {
            console.warn('[GeminiLive] Message parse error:', err);
          }
        };

        ws.onclose = (event) => {
          clearTimeout(timeout);
          if (keepaliveRef.current) {
            clearInterval(keepaliveRef.current);
            keepaliveRef.current = null;
          }
          const reason = event.reason || `code ${event.code}`;
          console.log('[GeminiLive] WebSocket closed:', reason, '| code:', event.code, '| wsErrorFired:', wsErrorFired);
          try { window.eve.sessionHealth.recordWsClose(event.code, reason); } catch { /* ignored */ }

          if (!smReconnectingRef.current) {
            sessionManagerRef.current?.sessionEnded();
            idleBehaviorRef.current?.stop();
          }

          const wasConnected = stateRef.current.isConnected;

          let errorMsg = '';
          if (!wasConnected) {
            if (!navigator.onLine) {
              errorMsg = 'Device appears offline — check your network connection';
            } else if (event.reason) {
              errorMsg = `Gemini Live: ${event.reason}`;
            } else if (event.code === 1006) {
              errorMsg = wsErrorFired
                ? 'Could not connect to Gemini Live — check that your API key has the Live API enabled and your network allows WebSocket connections'
                : 'Connection to Gemini Live was interrupted';
            } else if (event.code === 1008) {
              errorMsg = 'Gemini Live rejected the connection — API key may be invalid or not authorized for the Live API';
            } else if (event.code === 1009) {
              errorMsg = 'Gemini Live rejected the setup — message payload too large (too many tools or system instruction too long)';
            } else if (event.code === 1001) {
              errorMsg = 'Gemini Live service is temporarily unavailable — try again shortly';
            } else {
              errorMsg = `Gemini Live connection failed (code ${event.code})`;
            }
          }

          setState((s) => ({
            ...s,
            isConnected: false,
            isConnecting: false,
            isListening: smReconnectingRef.current ? s.isListening : (intentionalDisconnectRef.current ? false : s.isListening),
            error: wasConnected ? '' : errorMsg,
          }));

          if (!wasConnected && wsErrorFired) {
            optionsRef.current.onError?.(errorMsg);
          }

          // Auto-reconnect on unexpected disconnect
          if (wasConnected && !intentionalDisconnectRef.current && !smReconnectingRef.current && !isAutoReconnectingRef.current) {
            isAutoReconnectingRef.current = true;
            sessionManagerRef.current?.sessionEnded();

            const MAX_AUTO_RECONNECT = 15;

            const attemptReconnect = async () => {
              wsReconnectAttemptsRef.current++;
              const attempt = wsReconnectAttemptsRef.current;

              if (attempt > MAX_AUTO_RECONNECT) {
                console.warn(`[GeminiLive] All ${MAX_AUTO_RECONNECT} reconnect attempts exhausted`);
                isAutoReconnectingRef.current = false;
                setState((s) => ({ ...s, error: 'Connection lost — tap the orb to reconnect' }));
                return;
              }

              if (!navigator.onLine) {
                console.log('[GeminiLive] Network offline — waiting for connectivity...');
                setState((s) => ({ ...s, error: 'Network offline — will reconnect automatically' }));
                const onlineHandler = () => {
                  window.removeEventListener('online', onlineHandler);
                  console.log('[GeminiLive] Network back online — resuming reconnect');
                  attemptReconnect();
                };
                window.addEventListener('online', onlineHandler);
                return;
              }

              const delay = Math.min(attempt * 3000, 30000);
              console.log(`[GeminiLive] Auto-reconnect attempt ${attempt}/${MAX_AUTO_RECONNECT} in ${delay}ms`);
              setState((s) => ({
                ...s,
                error: attempt <= 5
                  ? `Reconnecting... (attempt ${attempt}/${MAX_AUTO_RECONNECT})`
                  : `Reconnecting... (attempt ${attempt}/${MAX_AUTO_RECONNECT}) — tap orb to retry now`,
              }));

              await new Promise((r) => setTimeout(r, delay));

              if (intentionalDisconnectRef.current || smReconnectingRef.current) {
                isAutoReconnectingRef.current = false;
                return;
              }

              try {
                const instruction = await window.eve.getLiveSystemInstruction();
                const conversationSummary = sessionManagerRef.current?.buildConversationSummary() || '';
                const accentDesc = agentAccentRef.current || 'American';
                const nameDesc = agentNameRef.current || 'the agent';
                const voiceAnchor = `\n\nCRITICAL: You are reconnecting mid-conversation. Maintain your ${accentDesc} accent and vocal identity EXACTLY as before. Do NOT change voice, accent, or character. You are ${nameDesc} — pick up seamlessly.`;
                const fullInstruction = conversationSummary
                  ? `${instruction}\n\n${conversationSummary}${voiceAnchor}`
                  : `${instruction}${voiceAnchor}`;
                await connect(fullInstruction, toolsRef.current, voiceNameRef.current, { onboarding: onboardingModeRef.current });
                wsReconnectAttemptsRef.current = 0;
                isAutoReconnectingRef.current = false;
                sessionManagerRef.current?.sessionStarted();
                try {
                  window.eve.sessionHealth.recordReconnect('auto-retry', true);
                  window.eve.sessionHealth.recordVoiceAnchor();
                } catch { /* ignored */ }
                if (!stateRef.current.isListening || !audioContextRef.current || audioContextRef.current.state === 'closed') {
                  console.log('[GeminiLive] Mic pipeline down after auto-reconnect — restarting');
                  startListeningRef.current?.();
                } else {
                  console.log('[GeminiLive] Mic pipeline alive through auto-reconnect — seamless');
                }
                console.log('[GeminiLive] Auto-reconnect successful');
              } catch (err) {
                console.warn(`[GeminiLive] Auto-reconnect attempt ${attempt} failed:`, err);
                try { window.eve.sessionHealth.recordReconnect('auto-retry', false); } catch { /* ignored */ }
                attemptReconnect();
              }
            };

            attemptReconnect();
          }

          reject(new Error(`WebSocket closed: ${reason}`));
        };

        let wsErrorFired = false;
        ws.onerror = (event) => {
          wsErrorFired = true;
          console.error('[GeminiLive] WebSocket error event:', event, '| Online:', navigator.onLine);
        };
      });
      } finally {
        connectingRef.current = false;
      }
    },
    [getApiBase]
  );

  // --- Send text into the Gemini session ---
  const sendTextToGemini = useCallback((text: string) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;

    wsRef.current.send(
      JSON.stringify({
        client_content: {
          turns: [{ role: 'user', parts: [{ text }] }],
          turn_complete: true,
        },
      })
    );

    if (!text.startsWith('[SYSTEM') && !text.startsWith('[IDLE')) {
      sessionManagerRef.current?.addEntry('user', text);
      idleBehaviorRef.current?.resetActivity();
      setState((s) => ({ ...s, idleTier: 0 }));
      window.eve.agentTrust.processMessage(text).catch(() => {});
    }
  }, []);

  sendTextRef.current = sendTextToGemini;

  // --- Start mic capture + screen sharing ---
  const startListening = useCallback(async () => {
    await startMicPipeline(refs, setState);
  }, []);

  startListeningRef.current = startListening;

  // --- Stop mic capture + screen sharing ---
  const stopListening = useCallback(() => {
    stopMicPipeline(refs, setState);
  }, []);

  // --- Full disconnect ---
  const disconnect = useCallback(() => {
    intentionalDisconnectRef.current = true;
    isAutoReconnectingRef.current = false;

    // Create episodic memory from this session before tearing down
    const sm = sessionManagerRef.current;
    if (sm) {
      const history = sm.getConversationHistory();
      const duration = sm.getSessionDuration();
      if (history.length >= 4 && duration >= 60) {
        const now = Date.now();
        const startTime = now - duration * 1000;
        const transcript = history.map((h: { role: string; content: string }) => ({
          role: h.role,
          text: h.content,
        }));
        window.eve.episodic
          .create(transcript, startTime, now)
          .then((episode: any) => {
            if (episode) {
              console.log(`[GeminiLive] Episodic memory created: ${episode.id.slice(0, 8)}`);
            }
          })
          .catch((err: unknown) => {
            console.warn('[GeminiLive] Episodic memory creation failed:', err);
          });
      }
    }

    stopListening();
    wsRef.current?.close();
    wsRef.current = null;
    playbackEngineRef.current?.flush();
    sessionManagerRef.current?.reset();
    idleBehaviorRef.current?.stop();
    if (keepaliveRef.current) {
      clearInterval(keepaliveRef.current);
      keepaliveRef.current = null;
    }
    if (audioHealthTimerRef.current) {
      clearInterval(audioHealthTimerRef.current);
      audioHealthTimerRef.current = null;
    }
    setState({ isConnected: false, isConnecting: false, isListening: false, isSpeaking: false, isWebcamActive: false, isInCall: false, transcript: '', error: '', idleTier: 0 });
  }, [stopListening]);

  // --- Reset idle activity ---
  const resetIdleActivity = useCallback(() => {
    idleBehaviorRef.current?.resetActivity();
    setState((s) => (s.idleTier !== 0 ? { ...s, idleTier: 0 } : s));
  }, []);

  // Wire session manager reconnect callbacks
  useEffect(() => {
    const sm = sessionManagerRef.current;
    if (!sm) return;

    sm.setCallbacks({
      getSystemInstruction: () => window.eve.getLiveSystemInstruction(),
      closeConnection: () => {
        smReconnectingRef.current = true;
        intentionalDisconnectRef.current = true;
        setupCompleteRef.current = false;
        wsRef.current?.close();
        wsRef.current = null;
        playbackEngineRef.current?.flush();
        setState((s) => (s.isSpeaking ? { ...s, isSpeaking: false } : s));
        if (keepaliveRef.current) {
          clearInterval(keepaliveRef.current);
          keepaliveRef.current = null;
        }
      },
      reconnect: async (instruction: string) => {
        intentionalDisconnectRef.current = false;
        await connect(instruction, undefined, voiceNameRef.current, { onboarding: onboardingModeRef.current });
        smReconnectingRef.current = false;

        reconnectStabilizingRef.current = true;
        setTimeout(() => {
          reconnectStabilizingRef.current = false;
        }, 5000);
      },
      startListening: async () => {
        if (!stateRef.current.isListening || !audioContextRef.current || audioContextRef.current.state === 'closed') {
          console.log('[GeminiLive] SM reconnect: mic pipeline down — restarting');
          await startListening();
        } else {
          console.log('[GeminiLive] SM reconnect: mic pipeline alive — seamless');
        }
      },
      isSpeaking: () => stateRef.current.isSpeaking,
    });
  }, [connect, startListening]);

  // Wire idle behavior callbacks
  useEffect(() => {
    const ib = idleBehaviorRef.current;
    if (!ib) return;

    ib.setCallbacks({
      sendSystemText: (text: string) => {
        sendTextToGemini(text);
        const tier = idleBehaviorRef.current?.getTier() ?? 0;
        setState((s) => ({ ...s, idleTier: tier }));
      },
      getAmbientContext: () => ambientContextCacheRef.current,
      isActive: () => stateRef.current.isConnected && stateRef.current.isListening && !reconnectStabilizingRef.current,
      isSpeaking: () => stateRef.current.isSpeaking,
    });
  }, [sendTextToGemini]);

  // Poll ambient context
  useEffect(() => {
    return setupAmbientContextPolling(refs);
  }, []);

  // Start/stop idle behavior based on listening state
  useEffect(() => {
    const ib = idleBehaviorRef.current;
    if (!ib) return;

    if (state.isConnected && state.isListening) {
      ib.start();
    } else {
      ib.stop();
      setState((s) => (s.idleTier !== 0 ? { ...s, idleTier: 0 } : s));
    }
  }, [state.isConnected, state.isListening]);

  // Reset idle timer when Friday finishes speaking
  useEffect(() => {
    if (!state.isSpeaking) {
      idleBehaviorRef.current?.resetActivity();
    }
  }, [state.isSpeaking]);

  // Proactive agent result surfacing
  useEffect(() => {
    if (!state.isConnected || !state.isListening) return;
    return setupAgentResultSurfacing(refs, sendTextToGemini);
  }, [state.isConnected, state.isListening, sendTextToGemini]);

  // System sleep/resume recovery
  useEffect(() => {
    if (!state.isConnected) return;
    return setupSleepResumeDetection(refs);
  }, [state.isConnected]);

  // Tab focus recovery
  useEffect(() => {
    if (!state.isConnected) return;
    return setupTabFocusRecovery(refs);
  }, [state.isConnected]);

  // Mic + AudioContext health monitor
  useEffect(() => {
    if (!state.isConnected || !state.isListening) return;
    return setupMicHealthMonitor(refs, startListening);
  }, [state.isConnected, state.isListening, startListening]);

  // Periodic memory extraction
  useEffect(() => {
    if (!state.isConnected) return;
    return setupPeriodicMemoryExtraction(refs);
  }, [state.isConnected]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      disconnect();
    };
  }, [disconnect]);

  /** Get current mic input level (0–1) — call from RAF loop, not React render */
  const getMicLevel = useCallback((): number => {
    const analyser = micAnalyserRef.current;
    const data = micAnalyserDataRef.current;
    if (!analyser || !data) return 0;
    const buf = data as Uint8Array<ArrayBuffer>;
    analyser.getByteFrequencyData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) {
      const v = buf[i] / 255;
      sum += v * v;
    }
    return Math.sqrt(sum / buf.length);
  }, []);

  /** Get current playback output level (0–1) — call from RAF loop, not React render */
  const getOutputLevel = useCallback((): number => {
    return playbackEngineRef.current?.getOutputLevel() ?? 0;
  }, []);

  return {
    ...state,
    connect,
    startListening,
    stopListening,
    disconnect,
    sendTextToGemini,
    getMicLevel,
    getOutputLevel,
    resetIdleActivity,
    sessionManager: sessionManagerRef.current,
  };
}
