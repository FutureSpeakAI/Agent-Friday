/**
 * local-conversation.ts — Main-process conversation orchestrator for
 * fully local voice conversation (both onboarding and normal sessions).
 *
 * Chains the dormant local voice pipeline together:
 *   TranscriptionPipeline (mic → Whisper STT)
 *     → LLMClient / Ollama (tool-calling chat)
 *       → SpeechSynthesisManager (TTS → audio chunks to renderer)
 *
 * Used when no Gemini API key is configured. Supports two modes:
 *   1. Onboarding — "Her"-style intake interview with 4 onboarding tools
 *   2. General — post-onboarding conversation with desktop/feature/MCP tools
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
  private transcriptCleanup: (() => void) | null = null;
  private errorCleanup: (() => void) | null = null;

  /**
   * Start a local voice conversation.
   *
   * 1. Load Whisper model + TTS engine (if not already loaded)
   * 2. Verify Ollama is reachable
   * 3. Store system prompt + tools, init message history
   * 4. Start TranscriptionPipeline (mic → VAD → Whisper → transcript events)
   * 5. If initialPrompt provided, send it as first user turn to kick off interview
   * 6. Emit 'started' so renderer dispatches gemini-audio-active
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

    console.log('[LocalConversation] Starting local voice conversation loop...');

    // ── Step 1: Initialize voice engines ──────────────────────────────
    try {
      if (!whisperProvider.isReady()) {
        console.log('[LocalConversation] Loading Whisper model...');
        await whisperProvider.loadModel();
      }
    } catch (err) {
      const msg = `Whisper model not available: ${err instanceof Error ? err.message : String(err)}`;
      console.error(`[LocalConversation] ${msg}`);
      this.emit('error', msg);
      return;
    }

    try {
      if (!ttsEngine.isReady()) {
        console.log('[LocalConversation] Loading TTS engine...');
        await ttsEngine.loadEngine();
      }
    } catch (err) {
      const msg = `TTS engine not available: ${err instanceof Error ? err.message : String(err)}`;
      console.error(`[LocalConversation] ${msg}`);
      this.emit('error', msg);
      return;
    }

    // ── Step 2: Verify Ollama is reachable ────────────────────────────
    const ollamaProvider = llmClient.getProvider('ollama');
    if (!ollamaProvider || !ollamaProvider.isAvailable()) {
      const msg = 'Ollama is not running. Start Ollama to use local voice conversation.';
      console.error(`[LocalConversation] ${msg}`);
      this.emit('error', msg);
      return;
    }

    try {
      const healthy = await ollamaProvider.checkHealth?.();
      if (!healthy) throw new Error('Health check returned false');
    } catch (err) {
      const msg = `Ollama health check failed: ${err instanceof Error ? err.message : String(err)}`;
      console.error(`[LocalConversation] ${msg}`);
      this.emit('error', msg);
      return;
    }

    // ── Step 3: Initialize conversation state ─────────────────────────
    this.systemPrompt = systemPrompt;
    this.tools = tools;
    this.messages = [];
    this.active = true;
    this.processing = false;

    // ── Step 4: Start transcription pipeline (mic → Whisper) ──────────
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
      console.error(`[LocalConversation] ${msg}`);
      this.cleanup();
      this.emit('error', msg);
      return;
    }

    // ── Step 5: Emit started event ────────────────────────────────────
    console.log('[LocalConversation] Voice loop active — Whisper + Ollama + TTS ready');
    this.emit('started');

    // ── Step 6: Send initial prompt to kick off the interview ─────────
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
    if (speechSynthesis.isSpeaking()) {
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

    // Prevent concurrent processing — wait for previous turn to finish
    if (this.processing) {
      console.log('[LocalConversation] Already processing — queuing input');
      // Simple debounce: wait and retry once
      await new Promise((r) => setTimeout(r, 500));
      if (this.processing || !this.active) return;
    }

    this.processing = true;

    try {
      // 1. Emit user transcript for UI display
      this.emit('user-transcript', text);

      // 2. Append user message to history
      this.messages.push({ role: 'user', content: text });

      // 3. Send to Ollama with tool calling
      let response = await llmClient.complete(
        {
          messages: this.messages,
          systemPrompt: this.systemPrompt,
          tools: this.tools,
          maxTokens: 2048,
          temperature: 0.7,
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
   */
  private async speakResponse(text: string): Promise<void> {
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

    // Unsubscribe from pipeline events
    if (this.transcriptCleanup) {
      this.transcriptCleanup();
      this.transcriptCleanup = null;
    }
    if (this.errorCleanup) {
      this.errorCleanup();
      this.errorCleanup = null;
    }

    // Stop the transcription pipeline (mic + Whisper)
    transcriptionPipeline.stop();

    // Stop any in-progress TTS
    speechSynthesis.stop();

    // Clear message history
    this.messages = [];
  }
}
