/**
 * SessionManager — handles Gemini Live session timeout (~10 min) with
 * seamless reconnection and rolling conversation context.
 *
 * KEY DESIGN: The mic pipeline NEVER stops during reconnects. Only the
 * WebSocket is closed and re-opened. Mic/screen/webcam audio callbacks
 * all reference wsRef (a mutable React ref), so they automatically
 * switch to the new WebSocket without any teardown or restart.
 *
 * Reconnect flow:
 *   1. Wait for agent to finish speaking (no mid-sentence cuts)
 *   2. Pre-fetch system instruction while still connected
 *   3. Build conversation summary while still connected
 *   4. Close old WebSocket (mic frames silently dropped ~1-2s)
 *   5. Open new WebSocket (mic auto-switches on setupComplete)
 *   6. User perceives no gap
 */

export interface SessionCallbacks {
  /** Pre-fetch system instruction (called while still connected) */
  getSystemInstruction: () => Promise<string>;
  /** Close the WebSocket + flush audio (mic stays alive) */
  closeConnection: () => void;
  /** Re-establish WebSocket connection with new instruction + tools */
  reconnect: (instruction: string) => Promise<void>;
  /** Resume mic capture only if it somehow stopped (safety net) */
  startListening: () => Promise<void>;
  /** Check if agent is currently speaking (reconnect waits for silence) */
  isSpeaking: () => boolean;
}

interface ConversationEntry {
  role: 'user' | 'assistant';
  text: string;
  timestamp: number;
}

interface AgentIdentity {
  agentName: string;
  agentAccent: string;
}

export class SessionManager {
  private startTime = 0;
  private conversation: ConversationEntry[] = [];
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private callbacks: SessionCallbacks | null = null;
  private isReconnecting = false;
  private agentIdentity: AgentIdentity = { agentName: 'the agent', agentAccent: 'your natural' };
  private totalReconnects = 0;

  /** Gemini Live session timeout — conservative estimate (ms).
   *  Google's BidiGenerateContent drops sessions at ~10 min.
   *  7 min gives a safe buffer. */
  private readonly SESSION_TIMEOUT = 7 * 60 * 1000; // 7 min
  /** How long before the hard timeout to trigger reconnect (ms) */
  private readonly RECONNECT_BEFORE = 90 * 1000; // 90s before → reconnect at ~5.5 min
  /** Max conversation entries to keep in rolling buffer */
  private readonly MAX_ENTRIES = 60;

  setCallbacks(cb: SessionCallbacks) {
    this.callbacks = cb;
  }

  /** Set the agent identity for dynamic accent/name in conversation summaries */
  setAgentIdentity(name: string, accent: string) {
    this.agentIdentity = { agentName: name || 'the agent', agentAccent: accent || 'your natural' };
  }

  /** Call when a new session starts */
  sessionStarted() {
    // CRITICAL: Always clear existing timer first to prevent duplicate timers
    // (previous bug: reassigning this.reconnectTimer leaked the old setTimeout)
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }

    this.startTime = Date.now();
    this.isReconnecting = false;

    // Schedule reconnect before hard timeout
    const reconnectIn = this.SESSION_TIMEOUT - this.RECONNECT_BEFORE;
    this.reconnectTimer = setTimeout(() => {
      this.triggerReconnect();
    }, reconnectIn);

    console.log(
      `[SessionManager] Session started (reconnects so far: ${this.totalReconnects}). ` +
      `Next reconnect in ${Math.round(reconnectIn / 1000)}s`
    );
  }

  /** Call when session ends (manual disconnect, error, etc.) */
  sessionEnded() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  /** Log a conversation entry for context rollover */
  addEntry(role: 'user' | 'assistant', text: string) {
    this.conversation.push({ role, text, timestamp: Date.now() });

    // Keep bounded
    if (this.conversation.length > this.MAX_ENTRIES) {
      this.conversation = this.conversation.slice(-this.MAX_ENTRIES);
    }
  }

  /** Get accumulated conversation text for memory extraction */
  getConversationHistory(): Array<{ role: string; content: string }> {
    return this.conversation.map((e) => ({ role: e.role, content: e.text }));
  }

  /** Build a rolling summary of the conversation for context continuity */
  buildConversationSummary(): string {
    if (this.conversation.length === 0) return '';

    const { agentName, agentAccent } = this.agentIdentity;

    // Take last ~15 exchanges — enough for continuity without overwhelming Gemini on reconnect
    // (Trimmed from 25/6000 to 15/3000 to ensure fast first-response after reconnect)
    const recent = this.conversation.slice(-15);
    const lines: string[] = [];
    let totalChars = 0;
    const MAX_SUMMARY_CHARS = 3000; // Reduced from 6000 — smaller summary = faster first response
    for (const e of recent) {
      const prefix = e.role === 'user' ? 'User' : agentName;
      // Truncate entries to keep summary compact
      const text = e.text.length > 150 ? e.text.slice(0, 150) + '...' : e.text;
      const line = `${prefix}: ${text}`;
      if (totalChars + line.length > MAX_SUMMARY_CHARS) break;
      lines.push(line);
      totalChars += line.length;
    }

    return [
      '--- CONVERSATION CONTEXT (continuation of previous session) ---',
      'This is a seamless continuation. Do NOT re-greet the user or introduce yourself again.',
      `CRITICAL: Maintain your ${agentAccent} accent exactly as before. Do NOT reset your voice. You are ${agentName}, mid-conversation — same person, same accent, same warmth. Pick up naturally where you left off.`,
      `Session has been running continuously. This is reconnect #${this.totalReconnects + 1} — the user should not notice any change.`,
      'Recent conversation:',
      ...lines,
      '--- END CONTEXT ---',
    ].join('\n');
  }

  /** Get session duration in seconds */
  getSessionDuration(): number {
    if (!this.startTime) return 0;
    return Math.round((Date.now() - this.startTime) / 1000);
  }

  /** Public trigger for goAway / external reconnect requests */
  requestReconnect() {
    this.triggerReconnect();
  }

  private async triggerReconnect(attempt = 1): Promise<void> {
    const MAX_RECONNECT_RETRIES = 5;
    if (!this.callbacks || (this.isReconnecting && attempt === 1)) return;
    this.isReconnecting = true;

    console.log(
      `[SessionManager] Triggering reconnect at ${this.getSessionDuration()}s ` +
      `(attempt ${attempt}/${MAX_RECONNECT_RETRIES}, total so far: ${this.totalReconnects})`
    );

    try {
      // STEP 0: Wait for agent to finish speaking — avoids cutting off mid-sentence
      if (this.callbacks.isSpeaking()) {
        console.log('[SessionManager] Agent is speaking — waiting for silence before reconnect');
        let waitedMs = 0;
        while (this.callbacks.isSpeaking() && waitedMs < 15000) {
          await new Promise((r) => setTimeout(r, 500));
          waitedMs += 500;
        }
        if (waitedMs >= 15000) {
          console.warn('[SessionManager] Speech wait timeout (15s) — proceeding with reconnect');
        } else {
          console.log(`[SessionManager] Agent finished speaking after ${waitedMs}ms`);
        }
      }

      // STEP 1: Pre-fetch instruction WHILE STILL CONNECTED (reduces gap)
      const baseInstruction = await this.callbacks.getSystemInstruction();

      // STEP 2: Build conversation summary WHILE STILL CONNECTED
      const summary = this.buildConversationSummary();
      const { agentName, agentAccent } = this.agentIdentity;
      const voiceAnchor = `\n\nCRITICAL: You are reconnecting mid-conversation. Maintain your ${agentAccent} accent and vocal identity EXACTLY as before. Do NOT change voice, accent, or character. You are ${agentName} — pick up seamlessly.`;
      const fullInstruction = summary
        ? `${baseInstruction}\n\n${summary}${voiceAnchor}`
        : `${baseInstruction}${voiceAnchor}`;

      // STEP 3: Close old WebSocket — mic pipeline stays alive
      //   Mic frames are silently dropped during the brief gap (~1-2s)
      //   because they check wsRef.current?.readyState === WebSocket.OPEN
      this.callbacks.closeConnection();

      // STEP 4: Open new WebSocket — mic auto-switches when setup completes
      await this.callbacks.reconnect(fullInstruction);

      // STEP 5: Safety net — only restart mic if the pipeline somehow died
      await this.callbacks.startListening();

      // STEP 6: Track and restart session timer
      this.totalReconnects++;
      this.sessionStarted();

      console.log(`[SessionManager] Seamless reconnect #${this.totalReconnects} complete`);
    } catch (err) {
      console.error(`[SessionManager] Reconnect attempt ${attempt} failed:`, err);

      if (attempt < MAX_RECONNECT_RETRIES) {
        const delay = attempt * 2000;
        console.log(`[SessionManager] Retrying in ${delay}ms...`);
        await new Promise((r) => setTimeout(r, delay));
        return this.triggerReconnect(attempt + 1);
      } else {
        console.error('[SessionManager] All reconnect attempts exhausted');
        this.isReconnecting = false;
      }
    }
  }

  /** Reset everything */
  reset() {
    this.sessionEnded();
    this.conversation = [];
    this.startTime = 0;
    this.isReconnecting = false;
    this.totalReconnects = 0;
  }
}
