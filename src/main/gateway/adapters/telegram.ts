/**
 * gateway/adapters/telegram.ts — Telegram Bot API adapter.
 *
 * Uses the Telegram Bot API with long polling (getUpdates).
 * Zero external dependencies — uses Node.js built-in https module,
 * following the same pattern as comms-hub.ts.
 *
 * Setup:
 *   1. Create a bot via @BotFather on Telegram
 *   2. Copy the bot token into Agent Friday settings (telegramBotToken)
 *   3. Set your Telegram user ID (telegramOwnerId) for auto owner-dm trust
 *   4. Enable the gateway
 */

import * as https from 'https';
import { ChannelAdapter, GatewayMessage, GatewayResponse } from '../types';

const TELEGRAM_API_BASE = 'https://api.telegram.org';
const POLL_TIMEOUT_SECONDS = 30;
const REQUEST_TIMEOUT_MS = 35_000; // Slightly longer than poll timeout
const MAX_MESSAGE_LENGTH = 4096;

class TelegramAdapter implements ChannelAdapter {
  id = 'telegram';
  label = 'Telegram';
  onMessage: ((msg: GatewayMessage) => void) | null = null;

  private token: string;
  private offset = 0;
  private polling = false;
  private pollAbort: AbortController | null = null;

  constructor(token: string) {
    this.token = token;
  }

  async start(): Promise<void> {
    if (!this.token) {
      throw new Error('Telegram bot token not configured');
    }

    // Verify the token by calling getMe
    const me = await this.callApi('getMe', {});
    console.log(`[Telegram] Bot started: @${me.result?.username || 'unknown'}`);

    this.polling = true;
    // Fire-and-forget the poll loop — it runs until stop() is called
    this.pollLoop().catch((err) => {
      console.error('[Telegram] Poll loop crashed:', err);
      this.polling = false;
    });
  }

  async stop(): Promise<void> {
    this.polling = false;
    if (this.pollAbort) {
      this.pollAbort.abort();
      this.pollAbort = null;
    }
    console.log('[Telegram] Adapter stopped');
  }

  isRunning(): boolean {
    return this.polling;
  }

  async sendMessage(response: GatewayResponse): Promise<void> {
    const text = response.text;

    // Split long messages at paragraph boundaries
    if (text.length > MAX_MESSAGE_LENGTH) {
      const chunks = this.splitMessage(text);
      for (const chunk of chunks) {
        await this.callApi('sendMessage', {
          chat_id: response.recipientId,
          text: chunk,
          parse_mode: 'Markdown',
        });
      }
      return;
    }

    await this.callApi('sendMessage', {
      chat_id: response.recipientId,
      text,
      parse_mode: 'Markdown',
    });
  }

  // ── Long Polling Loop ────────────────────────────────────────────

  private async pollLoop(): Promise<void> {
    while (this.polling) {
      try {
        this.pollAbort = new AbortController();

        const data = await this.callApi('getUpdates', {
          offset: this.offset,
          timeout: POLL_TIMEOUT_SECONDS,
          allowed_updates: ['message'],
        });

        if (!data.ok || !Array.isArray(data.result)) {
          console.warn('[Telegram] Unexpected getUpdates response:', data);
          await this.sleep(5000);
          continue;
        }

        for (const update of data.result) {
          this.offset = update.update_id + 1;

          // Process text messages only (skip edits, channels, etc.)
          const tgMsg = update.message;
          if (!tgMsg?.text || !tgMsg.from) continue;

          // Skip bot's own messages
          if (tgMsg.from.is_bot) continue;

          // Check for @mention in group chats
          const chatType = tgMsg.chat?.type || 'private';
          if (chatType === 'group' || chatType === 'supergroup') {
            // Only process if bot is @mentioned in the text
            // We'll check for bot username mention
            const botMentioned = tgMsg.entities?.some(
              (e: any) => e.type === 'mention'
            );
            if (!botMentioned && !tgMsg.text.toLowerCase().includes('@')) {
              continue; // Ignore non-@mention group messages
            }
          }

          const gatewayMsg: GatewayMessage = {
            id: String(tgMsg.message_id),
            channel: 'telegram',
            senderId: String(tgMsg.from.id),
            senderName: tgMsg.from.first_name + (tgMsg.from.last_name ? ` ${tgMsg.from.last_name}` : ''),
            text: tgMsg.text,
            timestamp: tgMsg.date * 1000,
            trustTier: 'public', // GatewayManager resolves the actual tier
            threadId: chatType !== 'private' ? String(tgMsg.chat.id) : undefined,
            replyToId: tgMsg.reply_to_message ? String(tgMsg.reply_to_message.message_id) : undefined,
            metadata: {
              chatType,
              chatId: String(tgMsg.chat.id),
              username: tgMsg.from.username,
            },
          };

          // For group chats, the recipientId is the chat ID (not the sender)
          if (chatType !== 'private') {
            gatewayMsg.metadata!.groupChatId = String(tgMsg.chat.id);
          }

          try {
            this.onMessage?.(gatewayMsg);
          } catch (err) {
            console.error('[Telegram] onMessage handler error:', err);
          }
        }
      } catch (err: any) {
        if (err.name === 'AbortError' || !this.polling) {
          break; // Graceful shutdown
        }
        console.warn('[Telegram] Poll error:', err?.message);
        await this.sleep(5000); // Back off on errors
      }
    }
  }

  // ── Telegram Bot API Client ──────────────────────────────────────

  private callApi(method: string, body: Record<string, unknown>): Promise<any> {
    return new Promise((resolve, reject) => {
      const url = `${TELEGRAM_API_BASE}/bot${this.token}/${method}`;
      const postData = JSON.stringify(body);
      const urlObj = new URL(url);

      const req = https.request(
        {
          hostname: urlObj.hostname,
          path: urlObj.pathname,
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Content-Length': Buffer.byteLength(postData),
          },
          timeout: REQUEST_TIMEOUT_MS,
        },
        (res) => {
          const chunks: Buffer[] = [];
          let totalBytes = 0;

          res.on('data', (chunk: Buffer) => {
            totalBytes += chunk.length;
            if (totalBytes < 1_048_576) { // 1MB safety limit
              chunks.push(chunk);
            }
          });

          res.on('end', () => {
            try {
              const text = Buffer.concat(chunks).toString('utf-8');
              const data = JSON.parse(text);
              resolve(data);
            } catch (err) {
              reject(new Error(`Telegram API parse error: ${err}`));
            }
          });
        }
      );

      req.on('error', (err) => {
        reject(err);
      });

      req.on('timeout', () => {
        req.destroy();
        // Timeouts during long polling are normal — just resolve with empty result
        if (method === 'getUpdates') {
          resolve({ ok: true, result: [] });
        } else {
          reject(new Error(`Telegram API timeout: ${method}`));
        }
      });

      req.write(postData);
      req.end();
    });
  }

  // ── Helpers ──────────────────────────────────────────────────────

  private splitMessage(text: string): string[] {
    const chunks: string[] = [];
    let remaining = text;

    while (remaining.length > MAX_MESSAGE_LENGTH) {
      // Try to split at a paragraph break
      let splitAt = remaining.lastIndexOf('\n\n', MAX_MESSAGE_LENGTH);
      if (splitAt < MAX_MESSAGE_LENGTH * 0.3) {
        // No good paragraph break — try a newline
        splitAt = remaining.lastIndexOf('\n', MAX_MESSAGE_LENGTH);
      }
      if (splitAt < MAX_MESSAGE_LENGTH * 0.3) {
        // No good newline — hard split
        splitAt = MAX_MESSAGE_LENGTH;
      }

      chunks.push(remaining.slice(0, splitAt));
      remaining = remaining.slice(splitAt).trimStart();
    }

    if (remaining.length > 0) {
      chunks.push(remaining);
    }

    return chunks;
  }

  private sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
}

/**
 * Factory function for creating a Telegram adapter.
 * Called by the gateway manager when Telegram is configured.
 */
export function createTelegramAdapter(token: string): ChannelAdapter {
  return new TelegramAdapter(token);
}
