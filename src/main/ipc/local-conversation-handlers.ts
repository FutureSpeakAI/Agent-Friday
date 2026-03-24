/**
 * local-conversation-handlers.ts — IPC bridge between the renderer and
 * the main-process LocalConversation orchestrator.
 *
 * Handles:
 *   local-conversation:start   → Start the local voice conversation loop
 *   local-conversation:send    → Send typed text into the conversation
 *   local-conversation:stop    → Stop the conversation and clean up
 *
 * Forwards LocalConversation events to the renderer:
 *   local-conversation:event:started         → Voice loop initialized
 *   local-conversation:event:transcript      → User speech transcribed
 *   local-conversation:event:response        → AI response text
 *   local-conversation:event:agent-finalized → Agent identity saved (auto-advance)
 *   local-conversation:event:error           → Error message
 */

import { ipcMain, type BrowserWindow } from 'electron';
import { LocalConversation } from '../local-conversation';
import { assertString } from './validate';
import type { ToolDefinition } from '../llm-client';
import type { AgentConfig } from '../settings';

export interface LocalConversationHandlerDeps {
  getMainWindow: () => BrowserWindow | null;
}

export function registerLocalConversationHandlers(
  deps: LocalConversationHandlerDeps,
): void {
  const conversation = new LocalConversation();

  // Helper to safely send events to renderer
  const sendToRenderer = (channel: string, ...args: unknown[]) => {
    const win = deps.getMainWindow();
    if (win && !win.isDestroyed()) {
      win.webContents.send(channel, ...args);
    }
  };

  // ── Forward conversation events to renderer ───────────────────────

  conversation.on('started', () => {
    sendToRenderer('local-conversation:event:started');
  });

  conversation.on('user-transcript', (text: string) => {
    sendToRenderer('local-conversation:event:transcript', text);
  });

  conversation.on('ai-response', (text: string) => {
    sendToRenderer('local-conversation:event:response', text);
  });

  conversation.on('agent-finalized', (config: AgentConfig) => {
    sendToRenderer('local-conversation:event:agent-finalized', config);
  });

  conversation.on('ai-response-chunk', (text: string) => {
    sendToRenderer('local-conversation:event:response-chunk', text);
  });

  conversation.on('tool-start', (info: { id: string; name: string }) => {
    sendToRenderer('local-conversation:event:tool-start', info);
  });

  conversation.on('tool-end', (info: { id: string; name: string; success: boolean }) => {
    sendToRenderer('local-conversation:event:tool-end', info);
  });

  conversation.on('error', (error: string) => {
    sendToRenderer('local-conversation:event:error', error);
  });

  conversation.on('barge-in', () => {
    sendToRenderer('local-conversation:event:barge-in');
  });

  // ── IPC handlers ──────────────────────────────────────────────────

  ipcMain.handle(
    'local-conversation:start',
    async (
      _event,
      systemPrompt: string,
      toolDefs: unknown[],
      initialPrompt?: string,
    ) => {
      try {
        // Convert onboarding tool format to LLM ToolDefinition format
        const tools: ToolDefinition[] = (toolDefs || []).map((t: unknown) => {
          const td = t as { name: string; description?: string; parameters?: Record<string, unknown> };
          return {
            type: 'function' as const,
            name: td.name,
            description: td.description,
            function: {
              name: td.name,
              description: td.description,
              parameters: td.parameters,
            },
          };
        });

        await conversation.start(systemPrompt, tools, initialPrompt);
        return { ok: true };
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        console.error('[LocalConversation IPC] start failed:', msg);
        return { ok: false, error: msg };
      }
    },
  );

  ipcMain.handle(
    'local-conversation:send',
    async (_event, text: string) => {
      assertString(text, 'local-conversation:send text', 10_000);
      try {
        await conversation.sendText(text);
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        console.error('[LocalConversation IPC] send failed:', msg);
        throw new Error(`Failed to send message: ${msg}`);
      }
    },
  );

  ipcMain.handle('local-conversation:stop', () => {
    conversation.stop();
  });
}
