/**
 * gateway/gateway-connector.ts — Connector module for the messaging gateway.
 *
 * Registers as a standard connector in the registry, giving Claude tools
 * to send proactive messages through gateway channels (Telegram, Discord, etc.)
 * and query gateway status.
 *
 * This enables agent-initiated outreach: "Send Steve a Telegram message saying
 * I'll be 10 minutes late" or scheduled daily summaries via the scheduler.
 *
 * Exports:
 *   TOOLS   — tool declarations array
 *   execute — async tool dispatcher
 *   detect  — returns true when gateway is enabled
 */

import { gatewayManager } from './gateway-manager';

// ── Types ──────────────────────────────────────────────────────────────

interface ToolDeclaration {
  name: string;
  description: string;
  parameters: {
    type: string;
    properties: Record<string, unknown>;
    required?: string[];
  };
}

interface ToolResult {
  result?: string;
  error?: string;
}

// ── Tool Declarations ──────────────────────────────────────────────────

export const TOOLS: ToolDeclaration[] = [
  {
    name: 'gateway_send_message',
    description:
      'Send a message through the messaging gateway to a specific channel and recipient. ' +
      'Use this for proactive outreach — e.g. "Send Steve a Telegram message", ' +
      '"Message the team on Discord", or "Send a daily summary to my Telegram". ' +
      'The recipient must be a paired identity (known contact) or the owner.',
    parameters: {
      type: 'object',
      properties: {
        channel: {
          type: 'string',
          description:
            'Channel to send through: "telegram", "discord", or "slack". ' +
            'Must match an active gateway adapter.',
        },
        recipient_id: {
          type: 'string',
          description:
            'Channel-specific recipient ID (e.g. Telegram user ID or chat ID). ' +
            'Use gateway_list_channels to discover available recipients.',
        },
        text: {
          type: 'string',
          description: 'The message text to send. Supports basic Markdown formatting.',
        },
      },
      required: ['channel', 'recipient_id', 'text'],
    },
  },
  {
    name: 'gateway_list_channels',
    description:
      'List all active messaging gateway channels and their status. ' +
      'Shows which channels are connected (Telegram, Discord, etc.), ' +
      'the number of paired identities, and message statistics. ' +
      'Use this to check what channels are available before sending messages.',
    parameters: {
      type: 'object',
      properties: {},
    },
  },
  {
    name: 'gateway_list_contacts',
    description:
      'List all paired contacts (identities) across gateway channels. ' +
      'Shows each contact\'s name, channel, trust tier, and channel-specific ID. ' +
      'Use this to look up a recipient ID before sending a proactive message.',
    parameters: {
      type: 'object',
      properties: {
        channel: {
          type: 'string',
          description: 'Optional: filter contacts to a specific channel (e.g. "telegram").',
        },
      },
    },
  },
];

// ── Detection ──────────────────────────────────────────────────────────

/**
 * Detection function — returns true when the gateway is enabled and running.
 * The connector only appears in Claude's tool list when the gateway is active.
 */
export async function detect(): Promise<boolean> {
  return gatewayManager.isRunning();
}

// ── Tool Execution ─────────────────────────────────────────────────────

export async function execute(
  toolName: string,
  args: Record<string, unknown>
): Promise<ToolResult> {
  switch (toolName) {
    case 'gateway_send_message':
      return handleSendMessage(args);
    case 'gateway_list_channels':
      return handleListChannels();
    case 'gateway_list_contacts':
      return handleListContacts(args);
    default:
      return { error: `Unknown gateway tool: ${toolName}` };
  }
}

// ── Handlers ───────────────────────────────────────────────────────────

async function handleSendMessage(args: Record<string, unknown>): Promise<ToolResult> {
  const channel = String(args.channel || '').trim();
  const recipientId = String(args.recipient_id || '').trim();
  const text = String(args.text || '').trim();

  if (!channel) return { error: 'Missing required parameter: channel' };
  if (!recipientId) return { error: 'Missing required parameter: recipient_id' };
  if (!text) return { error: 'Missing required parameter: text' };

  try {
    await gatewayManager.sendProactiveMessage(channel, recipientId, text);
    return {
      result: `Message sent successfully via ${channel} to ${recipientId} (${text.length} chars)`,
    };
  } catch (err: any) {
    return { error: `Failed to send message: ${err?.message || String(err)}` };
  }
}

async function handleListChannels(): Promise<ToolResult> {
  const status = gatewayManager.getStatus();

  const lines: string[] = [
    `Gateway: ${status.enabled ? 'ENABLED' : 'DISABLED'}`,
    `Paired identities: ${status.pairedIdentities}`,
    `Total messages handled: ${status.totalMessagesHandled}`,
    `Active sessions: ${gatewayManager.getActiveSessions()}`,
    '',
    'Channels:',
  ];

  if (status.channels.length === 0) {
    lines.push('  (no channels configured)');
  } else {
    for (const ch of status.channels) {
      const statusIcon = ch.running ? '✓' : '✗';
      lines.push(`  ${statusIcon} ${ch.label} (${ch.id}) — ${ch.running ? 'running' : 'stopped'}`);
    }
  }

  return { result: lines.join('\n') };
}

async function handleListContacts(args: Record<string, unknown>): Promise<ToolResult> {
  const channelFilter = args.channel ? String(args.channel).trim() : undefined;

  let identities = gatewayManager.getPairedIdentities();
  if (channelFilter) {
    identities = identities.filter((id: any) => id.channel === channelFilter);
  }

  if (identities.length === 0) {
    return {
      result: channelFilter
        ? `No paired contacts on ${channelFilter}`
        : 'No paired contacts. Contacts pair by sending a message and entering the pairing code in the desktop app.',
    };
  }

  const lines: string[] = [`Paired contacts${channelFilter ? ` (${channelFilter})` : ''}:`];
  for (const identity of identities) {
    lines.push(
      `  • ${identity.name} — ${identity.channel} (ID: ${identity.senderId}, tier: ${identity.tier})`
    );
  }

  return { result: lines.join('\n') };
}
