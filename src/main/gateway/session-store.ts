/**
 * gateway/session-store.ts — Per-sender conversation context for the gateway.
 *
 * Maintains a short rolling history of recent messages per sender so that
 * Claude has conversational context even across multiple gateway exchanges.
 *
 * This is distinct from the 3-tier memory system:
 * - SessionStore = ephemeral conversation buffer (last N messages per sender)
 * - Memory system = persistent facts, observations, and episodes
 */

const MAX_MESSAGES_PER_SENDER = 10;
const SESSION_EXPIRY_MS = 4 * 60 * 60 * 1000; // 4 hours

interface SessionMessage {
  role: 'user' | 'assistant';
  content: string;
  timestamp: number;
}

interface Session {
  senderId: string;
  channel: string;
  messages: SessionMessage[];
  lastActivity: number;
}

class SessionStore {
  private sessions: Map<string, Session> = new Map();

  /**
   * Get or create a session for a sender.
   */
  private getSession(channel: string, senderId: string): Session {
    const key = `${channel}:${senderId}`;
    let session = this.sessions.get(key);

    if (!session) {
      session = {
        senderId,
        channel,
        messages: [],
        lastActivity: Date.now(),
      };
      this.sessions.set(key, session);
    }

    return session;
  }

  /**
   * Add a user message to the session.
   */
  addUserMessage(channel: string, senderId: string, text: string): void {
    const session = this.getSession(channel, senderId);
    session.messages.push({
      role: 'user',
      content: text,
      timestamp: Date.now(),
    });
    this.trimSession(session);
    session.lastActivity = Date.now();
  }

  /**
   * Add an assistant response to the session.
   */
  addAssistantMessage(channel: string, senderId: string, text: string): void {
    const session = this.getSession(channel, senderId);
    session.messages.push({
      role: 'assistant',
      content: text,
      timestamp: Date.now(),
    });
    this.trimSession(session);
    session.lastActivity = Date.now();
  }

  /**
   * Get the conversation history for a sender, formatted for Claude.
   * Returns messages as {role, content} pairs.
   */
  getHistory(
    channel: string,
    senderId: string
  ): Array<{ role: 'user' | 'assistant'; content: string }> {
    const key = `${channel}:${senderId}`;
    const session = this.sessions.get(key);

    if (!session) return [];

    // Check if session is expired
    if (Date.now() - session.lastActivity > SESSION_EXPIRY_MS) {
      this.sessions.delete(key);
      return [];
    }

    return session.messages.map((m) => ({
      role: m.role,
      content: m.content,
    }));
  }

  /**
   * Clear a specific sender's session.
   */
  clearSession(channel: string, senderId: string): void {
    this.sessions.delete(`${channel}:${senderId}`);
  }

  /**
   * Prune expired sessions. Called periodically by the gateway manager.
   */
  pruneExpired(): number {
    const now = Date.now();
    let pruned = 0;

    for (const [key, session] of this.sessions) {
      if (now - session.lastActivity > SESSION_EXPIRY_MS) {
        this.sessions.delete(key);
        pruned++;
      }
    }

    return pruned;
  }

  /**
   * Get total number of active sessions.
   */
  getActiveCount(): number {
    return this.sessions.size;
  }

  // ── Private ──────────────────────────────────────────────────────

  private trimSession(session: Session): void {
    if (session.messages.length > MAX_MESSAGES_PER_SENDER) {
      session.messages = session.messages.slice(-MAX_MESSAGES_PER_SENDER);
    }
  }
}

export const sessionStore = new SessionStore();
