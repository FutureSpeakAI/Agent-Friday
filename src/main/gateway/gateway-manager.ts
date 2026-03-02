/**
 * gateway/gateway-manager.ts — Singleton orchestrator for the messaging gateway.
 *
 * Coordinates:
 *   - Channel adapters (Telegram, Discord, Slack, etc.)
 *   - Trust engine (sender identity → capability policy)
 *   - Persona adapter (personality + channel overlays)
 *   - Claude tool loop (filtered tools + system prompt)
 *   - Session store (per-sender conversation context)
 *   - Audit log (append-only JSONL)
 *   - Memory extraction (shared 3-tier memory across channels)
 *
 * Event flow:
 *   Adapter.onMessage → handleInbound → trust check → build prompt →
 *   filter tools → runClaudeToolLoop → send response → audit + memory
 */

import Anthropic from '@anthropic-ai/sdk';
import { mcpClient } from '../mcp-client';
import { connectorRegistry } from '../connectors/registry';
import { runClaudeToolLoop } from '../server';
import { memoryManager } from '../memory';
import { buildGatewayPrompt, wrapGatewayMessage } from './persona-adapter';
import { trustEngine } from './trust-engine';
import { auditLog } from './audit-log';
import { sessionStore } from './session-store';
import {
  ChannelAdapter,
  GatewayMessage,
  GatewayResponse,
  GatewayStatus,
  TrustTier,
} from './types';

// ── Gateway Manager ──────────────────────────────────────────────────

class GatewayManager {
  private adapters: Map<string, ChannelAdapter> = new Map();
  private running = false;
  private totalMessagesHandled = 0;
  private pruneTimer: ReturnType<typeof setInterval> | null = null;

  /**
   * Initialize the gateway: trust engine + audit log.
   * Adapters are registered separately via registerAdapter().
   */
  async initialize(): Promise<void> {
    await trustEngine.initialize();
    await auditLog.initialize();

    // Periodically prune expired sessions (every 30 minutes)
    this.pruneTimer = setInterval(() => {
      const pruned = sessionStore.pruneExpired();
      if (pruned > 0) {
        console.log(`[Gateway] Pruned ${pruned} expired sessions`);
      }
    }, 30 * 60 * 1000);

    this.running = true;
    console.log('[Gateway] Manager initialized');
  }

  /**
   * Register a channel adapter and start it.
   */
  async registerAdapter(adapter: ChannelAdapter): Promise<void> {
    // Wire the adapter's message callback to our handler
    adapter.onMessage = (msg) => {
      this.handleInbound(msg).catch((err) => {
        // Crypto Sprint 16: Sanitize — gateway errors may contain P2P auth data.
        console.error(`[Gateway] Error handling ${msg.channel} message:`, err instanceof Error ? err.message : 'Unknown error');
      });
    };

    this.adapters.set(adapter.id, adapter);

    try {
      await adapter.start();
      console.log(`[Gateway] Adapter started: ${adapter.label}`);
    } catch (err: any) {
      console.error(`[Gateway] Failed to start ${adapter.label}:`, err?.message);
    }
  }

  /**
   * Stop all adapters and shut down the gateway.
   */
  async stop(): Promise<void> {
    if (this.pruneTimer) {
      clearInterval(this.pruneTimer);
      this.pruneTimer = null;
    }

    const stops = Array.from(this.adapters.values()).map(async (adapter) => {
      try {
        await adapter.stop();
        console.log(`[Gateway] Adapter stopped: ${adapter.label}`);
      } catch (err: any) {
        console.warn(`[Gateway] Error stopping ${adapter.label}:`, err?.message);
      }
    });

    await Promise.allSettled(stops);
    this.adapters.clear();
    this.running = false;

    console.log('[Gateway] Manager stopped');
  }

  /**
   * Core message handling pipeline.
   *
   * 1. Rate limit → 2. Trust resolution → 3. Policy lookup →
   * 4. Public tier → pairing flow → 5. Build prompt →
   * 6. Filter tools → 7. Wrap message → 8. Claude tool loop →
   * 9. Send response → 10. Audit + memory
   */
  private async handleInbound(msg: GatewayMessage): Promise<void> {
    const startTime = Date.now();
    const adapter = this.adapters.get(msg.channel);
    if (!adapter) return;

    // 1. Trust resolution
    const trustTier = trustEngine.resolveTrust(msg.channel, msg.senderId);
    msg.trustTier = trustTier;

    // 2. Rate limit check
    const policy = trustEngine.getPolicy(trustTier);
    if (!trustEngine.checkRateLimit(msg.senderId, policy)) {
      console.log(`[Gateway] Rate limited: ${msg.senderName} (${trustTier})`);
      return; // Silent drop — don't reveal rate limiting to potential abusers
    }

    // 3. Audit inbound
    auditLog.logInbound(msg.channel, msg.senderId, trustTier, msg.text, msg.id);

    // 4. Handle public tier (pairing flow)
    if (trustTier === 'public') {
      await this.handlePairingFlow(msg, adapter);
      return;
    }

    console.log(
      `[Gateway] ${msg.channel}/${msg.senderName} (${trustTier}): "${msg.text.slice(0, 80)}${msg.text.length > 80 ? '...' : ''}"`
    );

    try {
      // 5. Build channel-specific system prompt
      const systemPrompt = await buildGatewayPrompt(msg.channel, trustTier);

      // 6. Gather and filter tools
      const tools = await this.gatherFilteredTools(policy);

      // 7. Build conversation messages (session history + wrapped inbound)
      const wrappedText = wrapGatewayMessage(
        msg.text,
        msg.channel,
        msg.senderName,
        msg.senderId,
        trustTier
      );

      // Get session history for conversational context
      const history = sessionStore.getHistory(msg.channel, msg.senderId);
      const messages: Anthropic.MessageParam[] = [
        ...history.map((h) => ({
          role: h.role as 'user' | 'assistant',
          content: h.content,
        })),
        { role: 'user' as const, content: wrappedText },
      ];

      // Record user message in session
      sessionStore.addUserMessage(msg.channel, msg.senderId, msg.text);

      // 8. Run Claude tool loop with filtered tools and capped iterations
      const result = await runClaudeToolLoop({
        systemPrompt,
        messages,
        tools: tools.allTools,
        maxIterations: policy.maxIterations,
        browserToolNames: tools.browserToolNames,
        connectorToolNames: tools.connectorToolNames,
      });

      // 9. Send response
      const response: GatewayResponse = {
        text: result.response,
        channel: msg.channel,
        recipientId: msg.senderId,
        threadId: msg.threadId,
      };

      await adapter.sendMessage(response);

      // Record assistant message in session
      sessionStore.addAssistantMessage(msg.channel, msg.senderId, result.response);

      // 10. Audit outbound
      auditLog.logOutbound(response.channel, response.recipientId, response.text, result.toolCalls, Date.now() - startTime);

      // 11. Memory extraction (if policy allows)
      if (policy.memoryWrite) {
        memoryManager
          .extractMemories([
            { role: 'user', content: msg.text },
            { role: 'assistant', content: result.response },
          ])
          .catch((err) => {
            // Crypto Sprint 16: Sanitize — memory extraction errors may contain API data.
            console.warn('[Gateway] Memory extraction failed:', err instanceof Error ? err.message : 'Unknown error');
          });
      }

      // 12. Unified inbox capture (DLP + triage + context stream)
      try {
        const { unifiedInbox } = require('../unified-inbox');
        unifiedInbox.ingestMessage(msg);
      } catch (err: any) {
        console.warn('[Gateway] Inbox ingestion failed:', err?.message);
      }

      this.totalMessagesHandled++;
    } catch (err: any) {
      console.error(`[Gateway] Processing failed for ${msg.senderName}:`, err?.message);

      // Send error response
      try {
        await adapter.sendMessage({
          text: "Sorry, I ran into an issue processing that. Could you try again?",
          channel: msg.channel,
          recipientId: msg.senderId,
          threadId: msg.threadId,
        });
      } catch {
        // Can't even send error — adapter might be down
      }
    }
  }

  /**
   * Handle the pairing flow for unknown (public tier) senders.
   */
  private async handlePairingFlow(
    msg: GatewayMessage,
    adapter: ChannelAdapter
  ): Promise<void> {
    const code = trustEngine.generatePairingCode(
      msg.channel,
      msg.senderId,
      msg.senderName
    );

    const response: GatewayResponse = {
      text:
        `Hi ${msg.senderName}! I'm Agent Friday, a personal AI assistant.\n\n` +
        `To chat with me, you'll need to pair with my owner first. ` +
        `Please enter this code in the Agent Friday desktop app:\n\n` +
        `**${code.slice(0, 3)}-${code.slice(3)}**\n\n` +
        `This code expires in 15 minutes.`,
      channel: msg.channel,
      recipientId: msg.senderId,
    };

    try {
      await adapter.sendMessage(response);
      auditLog.logOutbound(response.channel, response.recipientId, response.text, 0, 0);
    } catch (err: any) {
      console.warn(`[Gateway] Failed to send pairing code to ${msg.senderName}:`, err?.message);
    }
  }

  /**
   * Gather all available tools and filter them by trust policy.
   * Returns tools formatted for Anthropic API + routing name sets.
   */
  private async gatherFilteredTools(policy: import('./types').TrustPolicy): Promise<{
    allTools: Anthropic.Tool[];
    browserToolNames: Set<string>;
    connectorToolNames: Set<string>;
  }> {
    // Gather MCP tools
    let mcpTools: Array<{ name: string; description?: string; inputSchema?: unknown }> = [];
    try {
      mcpTools = await mcpClient.listTools();
    } catch {
      // MCP not connected
    }

    // Gather connector tools
    let rawConnectorTools: Array<{
      name: string;
      description?: string;
      parameters: { properties?: Record<string, unknown>; required?: string[] };
    }> = [];
    try {
      rawConnectorTools = connectorRegistry.getAllTools();
    } catch {
      // Connector registry not ready
    }

    // Filter MCP tools by trust policy
    const filteredMcp = trustEngine.filterTools(
      mcpTools.map((t) => ({ name: t.name, description: t.description, inputSchema: t.inputSchema })),
      policy
    );

    // Filter connector tools by trust policy
    const filteredConnectors = trustEngine.filterTools(
      rawConnectorTools.map((t) => ({ name: t.name, description: t.description, parameters: t.parameters })),
      policy
    );

    // Format for Anthropic API
    const mcpFormatted: Anthropic.Tool[] = filteredMcp.map((t) => ({
      name: t.name,
      description: t.description || '',
      input_schema: t.inputSchema || { type: 'object' as const, properties: {} },
    })) as Anthropic.Tool[];

    const connectorFormatted: Anthropic.Tool[] = filteredConnectors.map((t) => ({
      name: t.name,
      description: t.description || '',
      input_schema: {
        type: 'object' as const,
        properties: t.parameters.properties || {},
        ...(t.parameters.required ? { required: t.parameters.required } : {}),
      },
    })) as Anthropic.Tool[];

    // Note: Browser tools are NEVER included for gateway (no desktop access for remote tiers)
    // Only the 'local' tier gets browser tools, and that's handled by server.ts directly.

    return {
      allTools: [...mcpFormatted, ...connectorFormatted],
      browserToolNames: new Set<string>(), // No browser tools for gateway
      connectorToolNames: new Set(filteredConnectors.map((t) => t.name)),
    };
  }

  // ── Proactive Messaging ────────────────────────────────────────────

  /**
   * Send a proactive message through a channel adapter.
   * Used by the connector (gateway_send_message) and scheduler (gateway_message action).
   */
  async sendProactiveMessage(
    channel: string,
    recipientId: string,
    text: string
  ): Promise<void> {
    const adapter = this.adapters.get(channel);
    if (!adapter || !adapter.isRunning()) {
      throw new Error(`Channel adapter '${channel}' not available`);
    }

    const response: GatewayResponse = {
      text,
      channel,
      recipientId,
    };

    await adapter.sendMessage(response);
    auditLog.logOutbound(response.channel, response.recipientId, response.text, 0, 0);
  }

  // ── Pairing Management (IPC) ──────────────────────────────────────

  /**
   * Approve a pairing code entered in the Electron UI.
   */
  async approvePairing(code: string, tier?: TrustTier) {
    return trustEngine.approvePairing(code, tier);
  }

  /**
   * Revoke a paired identity.
   */
  async revokePairing(identityId: string) {
    return trustEngine.revokePairing(identityId);
  }

  // ── Status ─────────────────────────────────────────────────────────

  getStatus(): GatewayStatus {
    return {
      enabled: this.running,
      channels: Array.from(this.adapters.values()).map((adapter) => ({
        id: adapter.id,
        label: adapter.label,
        running: adapter.isRunning(),
      })),
      pairedIdentities: trustEngine.getPairedIdentities().length,
      totalMessagesHandled: this.totalMessagesHandled,
    };
  }

  getPendingPairings() {
    return trustEngine.getPendingPairings();
  }

  getPairedIdentities() {
    return trustEngine.getPairedIdentities();
  }

  getActiveSessions(): number {
    return sessionStore.getActiveCount();
  }

  isRunning(): boolean {
    return this.running;
  }
}

export const gatewayManager = new GatewayManager();
