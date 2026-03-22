/**
 * local-conversation.ts — Main-process conversation orchestrator for
 * fully local voice conversation (both onboarding and normal sessions).
 *
 * Chains the dormant local voice pipeline together:
 *   TranscriptionPipeline (mic → Whisper STT)
 *     → LLMClient / Ollama (tool-calling chat)
 *       → SpeechSynthesisManager (TTS → audio chunks to renderer)
 *
 * Used when no Gemini API key is configured. Supports three modes:
 *   1. Full voice — Whisper STT + Ollama + TTS (mic + speaker)
 *   2. Text + TTS — Ollama + TTS only (no mic, text input + spoken replies)
 *   3. Text-only — Ollama only (no mic, no speaker, text in/out)
 *
 * Whisper and TTS are optional — if models are missing, the conversation
 * degrades gracefully to text-only mode rather than failing entirely.
 *
 * Emits the same events the renderer expects
 * (started, transcript, response, agent-finalized, error).
 *
 * Runs entirely in the main process because:
 *   - llmClient/OllamaProvider are main-process singletons (no chat IPC)
 *   - TranscriptionPipeline is main-process (AudioCapture → Whisper)
 *   - SpeechSynthesisManager is main-process (TTS → audio chunks via IPC)
 */

import { EventEmitter } from 'node:events';
import { llmClient, type ChatMessage, type ToolDefinition, type ToolCall } from './llm-client';
import { transcriptionPipeline, type TranscriptEvent } from './voice/transcription-pipeline';
import { whisperProvider } from './voice/whisper-provider';
import { ttsEngine } from './voice/tts-engine';
import { speechSynthesis } from './voice/speech-synthesis';
import { generatePsychologicalProfile } from './psychological-profile';
import { settingsManager, type AgentConfig, type IntakeResponses, type FeatureSetupStep } from './settings';
import { initializeFeatureSetup, advanceFeatureStep } from './feature-setup';
import { ensureProfileOnDisk } from './friday-profile';
import { callDesktopTool } from './desktop-tools';
import { mcpClient } from './mcp-client';
import { calendarIntegration } from './calendar';

// ── Types ─────────────────────────────────────────────────────────────

export type LocalConversationEvent =
  | 'started'
  | 'user-transcript'
  | 'ai-response'
  | 'agent-finalized'
  | 'error';

// ── LocalConversation ─────────────────────────────────────────────────

export class LocalConversation extends EventEmitter {
  private messages: ChatMessage[] = [];
  private systemPrompt = '';
  private tools: ToolDefinition[] = [];
  private active = false;
  private processing = false;
  private pendingInputs: string[] = [];
  private transcriptCleanup: (() => void) | null = null;
  private errorCleanup: (() => void) | null = null;

  /** Whether Whisper STT is available (mic → text) */
  private voiceAvailable = false;
  /** Whether local TTS is available (text → speaker) */
  private ttsAvailable = false;

  /**
   * Start a local conversation.
   *
   * Voice components (Whisper STT, TTS) are optional — if unavailable,
   * the conversation degrades gracefully to text-only mode rather than
   * failing entirely. Only Ollama is required.
   *
   * 1. Verify Ollama is reachable (required — abort if not)
   * 2. Try loading Whisper model (optional — degrade to text input)
   * 3. Try loading TTS engine (optional — degrade to text output)
   * 4. Start TranscriptionPipeline if Whisper loaded
   * 5. Emit 'started' so renderer dispatches gemini-audio-active
   * 6. If initialPrompt provided, send it as first user turn
   */
  async start(
    systemPrompt: string,
    tools: ToolDefinition[],
    initialPrompt?: string,
  ): Promise<void> {
    if (this.active) {
      console.warn('[LocalConversation] Already active — ignoring start()');
      return;
    }

    console.log('[LocalConversation] Starting local conversation loop...');

    // ── Step 1: Verify Ollama is reachable (the only hard requirement) ─
    // Skip cached isAvailable() — do a fresh HTTP health check to avoid
    // race conditions where the cache hasn't been populated yet.
    const ollamaProvider = llmClient.getProvider('ollama');
    if (!ollamaProvider) {
      const msg = 'Ollama provider not registered. Ensure Ollama is configured.';
      console.error(`[LocalConversation] ${msg}`);
      throw new Error(msg);
    }

    try {
      const healthy = await ollamaProvider.checkHealth?.();
      if (!healthy) throw new Error('Ollama is not responding');
    } catch (err) {
      const msg = `Ollama is not running: ${err instanceof Error ? err.message : String(err)}. Start Ollama to use local conversation.`;
      console.error(`[LocalConversation] ${msg}`);
      throw new Error(msg);
    }

    // ── Step 2: Try loading Whisper (optional — text-only fallback) ───
    this.voiceAvailable = false;
    try {
      if (!whisperProvider.isReady()) {
        console.log('[LocalConversation] Loading Whisper model...');
        await whisperProvider.loadModel();
      }
      this.voiceAvailable = true;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[LocalConversation] Whisper not available — text input only: ${msg}`);
      this.emit('error', `Local voice error: Whisper model not available: ${msg}. Download the ggml-tiny.bin model first.`);
      // Continue — text input still works via sendText()
    }

    // ── Step 3: Try loading TTS (optional — silent text fallback) ─────
    this.ttsAvailable = false;
    try {
      if (!ttsEngine.isReady()) {
        console.log('[LocalConversation] Loading TTS engine...');
        await ttsEngine.loadEngine();
      }
      this.ttsAvailable = true;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[LocalConversation] TTS not available — text output only: ${msg}`);
      // Continue — responses are still displayed as text in the UI
    }

    // ── Step 4: Initialize conversation state ─────────────────────────
    this.systemPrompt = systemPrompt;
    this.tools = tools;
    this.messages = [];
    this.active = true;
    this.processing = false;

    // ── Step 5: Start transcription pipeline only if Whisper is ready ──
    if (this.voiceAvailable) {
      this.transcriptCleanup = transcriptionPipeline.on('transcript', (payload) => {
        const event = payload as TranscriptEvent;
        if (event.text && event.text.trim().length > 0) {
          void this.onUserSpeech(event.text.trim());
        }
      });

      this.errorCleanup = transcriptionPipeline.on('error', (payload) => {
        const err = payload instanceof Error ? payload : new Error(String(payload));
        console.error(`[LocalConversation] Pipeline error: ${err.message}`);
        this.emit('error', `Voice pipeline error: ${err.message}`);
      });

      try {
        await transcriptionPipeline.start();
      } catch (err) {
        const msg = `Failed to start voice pipeline: ${err instanceof Error ? err.message : String(err)}`;
        console.warn(`[LocalConversation] ${msg} — continuing in text-only mode`);
        this.voiceAvailable = false;
        // Don't abort — text mode still works
      }
    }

    // ── Step 6: Emit started event ────────────────────────────────────
    const mode = this.voiceAvailable
      ? (this.ttsAvailable ? 'Whisper + Ollama + TTS' : 'Whisper + Ollama (no TTS)')
      : (this.ttsAvailable ? 'Ollama + TTS (text input only)' : 'Ollama only (text mode)');
    console.log(`[LocalConversation] Active — ${mode}`);
    this.emit('started');

    // ── Step 7: Send initial prompt to kick off the interview ─────────
    if (initialPrompt) {
      void this.processUserInput(initialPrompt);
    }
  }

  /**
   * Send text input directly (for typed text fallback from InterviewStep).
   */
  async sendText(text: string): Promise<void> {
    if (!this.active) {
      console.warn('[LocalConversation] Not active — ignoring sendText()');
      return;
    }
    await this.processUserInput(text);
  }

  /**
   * Stop the conversation and clean up all resources.
   */
  stop(): void {
    if (!this.active) return;
    console.log('[LocalConversation] Stopping local voice conversation');
    this.cleanup();
  }

  /**
   * Whether the conversation loop is currently active.
   */
  isActive(): boolean {
    return this.active;
  }

  // ── Private: Input handling ───────────────────────────────────────────

  /**
   * Called when TranscriptionPipeline emits a transcript (user finished speaking).
   * Implements barge-in: if TTS is currently speaking, stop it immediately.
   */
  private async onUserSpeech(text: string): Promise<void> {
    // Barge-in: interrupt TTS if it's currently speaking
    if (this.ttsAvailable && speechSynthesis.isSpeaking()) {
      console.log('[LocalConversation] Barge-in detected — stopping TTS');
      speechSynthesis.stop();
    }

    await this.processUserInput(text);
  }

  /**
   * Core conversation turn: user text → Ollama → tool handling → TTS.
   *
   * Serialized: if a previous turn is still processing, this queues behind it.
   * (The TranscriptionPipeline already serializes transcripts via its queue.)
   */
  private async processUserInput(text: string): Promise<void> {
    if (!this.active) return;

    // Queue input if currently processing — never drop user speech
    if (this.processing) {
      console.log('[LocalConversation] Queuing input while processing:', text.slice(0, 50));
      this.pendingInputs.push(text);
      return;
    }

    this.processing = true;

    try {
      // 1. Emit user transcript for UI display
      this.emit('user-transcript', text);

      // 2. Append user message to history
      this.messages.push({ role: 'user', content: text });

      // 3. Send to Ollama with tool calling (90s timeout prevents indefinite hangs)
      let response = await llmClient.complete(
        {
          messages: this.messages,
          systemPrompt: this.systemPrompt,
          tools: this.tools,
          maxTokens: 2048,
          temperature: 0.7,
          signal: AbortSignal.timeout(90_000),
        },
        'ollama',
      );

      // 4. Handle tool calls in a loop (tool use → result → re-complete)
      let iterations = 0;
      const MAX_TOOL_ITERATIONS = 5;

      while (
        response.toolCalls &&
        response.toolCalls.length > 0 &&
        iterations < MAX_TOOL_ITERATIONS
      ) {
        iterations++;

        // Append assistant message with tool calls
        this.messages.push({
          role: 'assistant',
          content: response.content || null,
          tool_calls: response.toolCalls,
        });

        // Execute each tool call
        for (const tc of response.toolCalls) {
          console.log(`[LocalConversation] Executing tool: ${tc.name}`);
          const result = await this.executeToolCall(tc);

          // Append tool result to messages
          this.messages.push({
            role: 'tool',
            content: result,
            tool_call_id: tc.id,
            name: tc.name,
          });
        }

        // Re-complete with tool results
        response = await llmClient.complete(
          {
            messages: this.messages,
            systemPrompt: this.systemPrompt,
            tools: this.tools,
            maxTokens: 2048,
            temperature: 0.7,
            signal: AbortSignal.timeout(90_000),
          },
          'ollama',
        );
      }

      // 5. Append final assistant response
      const responseText = response.content || '';
      this.messages.push({ role: 'assistant', content: responseText });

      // 6. Emit AI response for UI display
      if (responseText) {
        this.emit('ai-response', responseText);
      } else {
        console.warn('[LocalConversation] Ollama returned empty response');
        this.emit('ai-response', '(No response from model — try rephrasing or check that your Ollama model is loaded)');
      }

      // 7. Speak the response via TTS
      if (responseText && this.active) {
        await this.speakResponse(responseText);
      }
    } catch (err) {
      const msg = `LLM error: ${err instanceof Error ? err.message : String(err)}`;
      console.error(`[LocalConversation] ${msg}`);
      this.emit('error', msg);
    } finally {
      this.processing = false;
      // Drain any queued inputs that arrived during processing
      if (this.pendingInputs.length > 0 && this.active) {
        const next = this.pendingInputs.shift()!;
        console.log('[LocalConversation] Processing queued input:', next.slice(0, 50));
        void this.processUserInput(next);
      }
    }
  }

  // ── Private: Tool execution ───────────────────────────────────────────

  /**
   * Execute a tool call directly in the main process.
   *
   * Handles three tiers:
   *   1. Onboarding tools — hard-coded intake/customization/finalize flows
   *   2. Feature setup tools — calendar auth, API key save, Obsidian, screen capture
   *   3. Desktop + MCP tools — callDesktopTool() first, then mcpClient fallback
   *
   * All calls bypass IPC entirely — this runs in main process.
   */
  private async executeToolCall(tc: ToolCall): Promise<string> {
    const args = (tc.input ?? {}) as Record<string, unknown>;

    switch (tc.name) {
      // ── Onboarding tools ──────────────────────────────────────────────

      case 'acknowledge_introduction': {
        const userResponse = String(args.user_response || '');
        console.log(`[LocalConversation] Tool: acknowledge_introduction — "${userResponse}"`);
        return 'Trust introduction acknowledged. The user understands the system and is ready for setup.';
      }

      case 'save_intake_responses': {
        const responses: IntakeResponses = {
          voicePreference: String(args.voice_preference || ''),
          socialDescription: String(args.social_description || ''),
          motherRelationship: String(args.mother_relationship || ''),
        };

        console.log('[LocalConversation] Tool: save_intake_responses — generating psych profile');

        try {
          const profile = await generatePsychologicalProfile(responses);
          await settingsManager.setSetting('psychologicalProfile', profile);
          console.log('[LocalConversation] Psychological profile saved');
        } catch (err) {
          // Non-fatal — profile generation has its own default fallback
          console.warn(
            '[LocalConversation] Psych profile generation warning:',
            err instanceof Error ? err.message : String(err),
          );
        }

        return 'Intake responses saved and psychological profile generated.';
      }

      case 'transition_to_customization': {
        console.log('[LocalConversation] Tool: transition_to_customization');
        return 'Acknowledged. Continue with the personal intake questions.';
      }

      case 'finalize_agent_identity': {
        const agentConfig: AgentConfig = {
          agentName: String(args.agent_name || 'Friday'),
          agentVoice: String(args.voice_name || 'Kore'),
          agentGender: String(args.gender || 'female'),
          agentAccent: String(args.accent || ''),
          agentBackstory: String(args.backstory || ''),
          agentTraits: Array.isArray(args.personality_traits)
            ? (args.personality_traits as string[])
            : [],
          agentIdentityLine: String(args.identity_line || ''),
          userName: String(args.user_name || ''),
          onboardingComplete: true,
        };

        console.log(
          `[LocalConversation] Tool: finalize_agent_identity — ${agentConfig.agentName} for ${agentConfig.userName}`,
        );

        try {
          // Save agent config (same as onboarding:finalize-agent IPC handler)
          await settingsManager.saveAgentConfig(agentConfig);

          // Initialize feature setup
          const featureState = initializeFeatureSetup();
          await settingsManager.setSetting('featureSetupState', featureState);
          await settingsManager.setSetting('featureSetupComplete', true);

          // Write friday-profile.md to disk
          ensureProfileOnDisk().catch((err) => {
            console.warn(
              '[LocalConversation] Profile rewrite failed:',
              err instanceof Error ? err.message : 'Unknown error',
            );
          });

          console.log('[LocalConversation] Agent identity saved — emitting agent-finalized');
        } catch (err) {
          console.error(
            '[LocalConversation] Failed to save agent config:',
            err instanceof Error ? err.message : String(err),
          );
        }

        // Emit agent-finalized so App.tsx dispatches the CustomEvent
        // and InterviewStep auto-advances to Reveal
        this.emit('agent-finalized', agentConfig);

        return `Agent identity saved. ${agentConfig.agentName} is being created now.`;
      }

      // ── Feature setup tools ───────────────────────────────────────────

      case 'mark_feature_setup_step': {
        const stepId = String(args.step_id || '') as FeatureSetupStep;
        const action = String(args.action || 'complete') as 'complete' | 'skip';
        console.log(`[LocalConversation] Tool: mark_feature_setup_step — ${stepId} → ${action}`);
        try {
          const newState = await advanceFeatureStep(stepId, action);
          return newState
            ? `Feature step "${stepId}" marked as ${action}. Progress: ${JSON.stringify(newState)}`
            : `Feature step "${stepId}" not found or already handled.`;
        } catch (err) {
          return `Failed to mark feature step: ${err instanceof Error ? err.message : String(err)}`;
        }
      }

      case 'start_calendar_auth': {
        console.log('[LocalConversation] Tool: start_calendar_auth — initiating OAuth flow');
        try {
          const success = await calendarIntegration.authenticate();
          if (success) {
            await advanceFeatureStep('calendar' as FeatureSetupStep, 'complete');
            return 'Calendar authentication successful! Google Calendar is now connected.';
          }
          return 'Calendar authentication was cancelled or failed. The user can try again later.';
        } catch (err) {
          return `Calendar auth error: ${err instanceof Error ? err.message : String(err)}`;
        }
      }

      case 'save_api_key': {
        const service = String(args.service || '').toLowerCase();
        const key = String(args.api_key || args.key || '');
        console.log(`[LocalConversation] Tool: save_api_key — ${service}`);

        if (!key) return 'No API key provided.';

        const keyMap: Record<string, string> = {
          perplexity: 'perplexityApiKey',
          firecrawl: 'firecrawlApiKey',
          openai: 'openaiApiKey',
          elevenlabs: 'elevenLabsApiKey',
        };

        const settingKey = keyMap[service];
        if (!settingKey) return `Unknown service "${service}". Supported: perplexity, firecrawl, openai, elevenlabs.`;

        try {
          await settingsManager.setSetting(settingKey, key);
          return `API key for ${service} saved successfully.`;
        } catch (err) {
          return `Failed to save API key: ${err instanceof Error ? err.message : String(err)}`;
        }
      }

      case 'set_obsidian_vault_path': {
        const vaultPath = String(args.vault_path || args.path || '');
        console.log(`[LocalConversation] Tool: set_obsidian_vault_path — ${vaultPath}`);

        if (!vaultPath) return 'No vault path provided.';

        try {
          await settingsManager.setSetting('obsidianVaultPath', vaultPath);
          await advanceFeatureStep('obsidian' as FeatureSetupStep, 'complete');
          return `Obsidian vault path set to: ${vaultPath}`;
        } catch (err) {
          return `Failed to set vault path: ${err instanceof Error ? err.message : String(err)}`;
        }
      }

      case 'toggle_screen_capture': {
        const enabled = args.enabled === true || args.enabled === 'true';
        console.log(`[LocalConversation] Tool: toggle_screen_capture — ${enabled}`);

        try {
          await settingsManager.setSetting('autoScreenCapture', enabled);
          await advanceFeatureStep('screen_capture' as FeatureSetupStep, enabled ? 'complete' : 'skip');
          return `Screen capture ${enabled ? 'enabled' : 'disabled'}.`;
        } catch (err) {
          return `Failed to toggle screen capture: ${err instanceof Error ? err.message : String(err)}`;
        }
      }

      // ── Desktop tools + MCP tools (general-purpose) ────────────────────

      default: {
        // Try desktop tools first (built-in OS-level tools)
        try {
          const result = await callDesktopTool(tc.name, args);
          if (result.error) {
            console.warn(`[LocalConversation] Desktop tool ${tc.name} error: ${result.error}`);
          }
          return result.result || result.error || 'Tool executed (no output).';
        } catch {
          // Desktop tool not found — fall through to MCP
        }

        // Try MCP tools (user-configured external tool servers)
        if (mcpClient.isConnected()) {
          try {
            const mcpResult = await mcpClient.callTool(tc.name, args);
            // MCP returns content array — extract text
            if (Array.isArray(mcpResult)) {
              return mcpResult
                .map((item: unknown) => {
                  const entry = item as { type?: string; text?: string };
                  return entry.type === 'text' ? entry.text || '' : JSON.stringify(entry);
                })
                .join('\n');
            }
            return typeof mcpResult === 'string' ? mcpResult : JSON.stringify(mcpResult);
          } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            console.warn(`[LocalConversation] MCP tool ${tc.name} failed: ${msg}`);
            return `Tool "${tc.name}" failed: ${msg}`;
          }
        }

        console.warn(`[LocalConversation] Unknown tool: ${tc.name}`);
        return `Tool "${tc.name}" is not available.`;
      }
    }
  }

  // ── Private: TTS ──────────────────────────────────────────────────────

  /**
   * Speak a response via the SpeechSynthesisManager.
   * The manager handles sentence chunking and sends audio to renderer via IPC.
   * Skipped silently if TTS is not available (text-only mode).
   */
  private async speakResponse(text: string): Promise<void> {
    if (!this.ttsAvailable) return; // text-only mode — skip TTS
    try {
      await speechSynthesis.speak(text);
    } catch (err) {
      // TTS failures are non-fatal — the text is still displayed in the UI
      console.warn(
        '[LocalConversation] TTS speak failed:',
        err instanceof Error ? err.message : String(err),
      );
    }
  }

  // ── Private: Cleanup ──────────────────────────────────────────────────

  private cleanup(): void {
    this.active = false;
    this.processing = false;
    this.pendingInputs = [];

    // Unsubscribe from pipeline events
    if (this.transcriptCleanup) {
      this.transcriptCleanup();
      this.transcriptCleanup = null;
    }
    if (this.errorCleanup) {
      this.errorCleanup();
      this.errorCleanup = null;
    }

    // Stop the transcription pipeline (mic + Whisper) — only if voice was active
    if (this.voiceAvailable) {
      transcriptionPipeline.stop();
    }

    // Stop any in-progress TTS — only if TTS was active
    if (this.ttsAvailable) {
      speechSynthesis.stop();
    }

    this.voiceAvailable = false;
    this.ttsAvailable = false;

    // Clear message history
    this.messages = [];
  }
}
