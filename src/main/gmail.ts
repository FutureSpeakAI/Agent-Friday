/**
 * gmail.ts — FR-6: Gmail integration for Friday's tool layer.
 *
 * Readonly search (search_email) + draft-only compose (draft_email).
 * Uses the shared google-oauth.ts manager (Calendar + Gmail, one consent
 * flow). Deliberately holds no send permission: the OAuth scope requested
 * is gmail.readonly + gmail.compose, never gmail.send, so outbound email
 * stays human-gated regardless of what any tool call asks for — Stephen
 * reviews and sends drafts himself from Gmail.
 */

import { ipcMain, shell } from 'electron';
import { google, gmail_v1 } from 'googleapis';
import { googleAuth } from './google-oauth';

export interface EmailSummary {
  id: string;
  threadId: string;
  subject: string;
  from: string;
  snippet: string;
  date: string;
  unread: boolean;
}

export interface DraftResult {
  id: string;
  webUrl: string;
}

function decodeHeader(headers: gmail_v1.Schema$MessagePartHeader[] | undefined, name: string): string {
  const header = headers?.find((h) => h.name?.toLowerCase() === name.toLowerCase());
  return header?.value || '';
}

/** Base64url-encode an RFC 2822 message for the Gmail drafts.create API. */
function buildRawMessage(to: string, subject: string, body: string): string {
  const message = [
    `To: ${to}`,
    `Subject: ${subject}`,
    'Content-Type: text/plain; charset="UTF-8"',
    '',
    body,
  ].join('\r\n');
  return Buffer.from(message).toString('base64url');
}

class GmailIntegration {
  private gmailApi: gmail_v1.Gmail | null = null;

  /** Build the Gmail API client from the shared google-oauth.ts manager. */
  init(): void {
    const client = googleAuth.getClient();
    this.gmailApi = client ? google.gmail({ version: 'v1', auth: client }) : null;
  }

  isAuthenticated(): boolean {
    return this.gmailApi !== null;
  }

  /**
   * Search email (readonly). Returns subject/from/snippet/date — never full
   * bodies, keeping tool results small and matching the "read-only" scope.
   */
  async search(query: string, maxResults = 10): Promise<EmailSummary[]> {
    if (!this.gmailApi) return [];

    const list = await this.gmailApi.users.messages.list({
      userId: 'me',
      q: query,
      maxResults: Math.min(Math.max(maxResults, 1), 25),
    });

    const messages = list.data.messages || [];
    if (messages.length === 0) return [];

    const results = await Promise.all(
      messages.map(async (m) => {
        if (!m.id) return null;
        const full = await this.gmailApi!.users.messages.get({
          userId: 'me',
          id: m.id,
          format: 'metadata',
          metadataHeaders: ['Subject', 'From', 'Date'],
        });
        const headers = full.data.payload?.headers;
        const summary: EmailSummary = {
          id: m.id,
          threadId: full.data.threadId || '',
          subject: decodeHeader(headers, 'Subject') || '(no subject)',
          from: decodeHeader(headers, 'From'),
          snippet: full.data.snippet || '',
          date: decodeHeader(headers, 'Date'),
          unread: (full.data.labelIds || []).includes('UNREAD'),
        };
        return summary;
      })
    );

    return results.filter((r): r is EmailSummary => r !== null);
  }

  /**
   * Create a Gmail draft (never sends). Returns a link Stephen can open to
   * review and send it himself — the connected OAuth token has no send scope,
   * so there is no code path in this app that can send this draft.
   */
  async createDraft(opts: { to: string; subject: string; body: string }): Promise<DraftResult | null> {
    if (!this.gmailApi) return null;

    const raw = buildRawMessage(opts.to, opts.subject, opts.body);
    const res = await this.gmailApi.users.drafts.create({
      userId: 'me',
      requestBody: { message: { raw } },
    });

    const id = res.data.id;
    if (!id) return null;

    return {
      id,
      webUrl: `https://mail.google.com/mail/u/0/#drafts?compose=${id}`,
    };
  }
}

export const gmailIntegration = new GmailIntegration();

export function registerGmailHandlers(): void {
  ipcMain.handle('gmail:is-authenticated', () => gmailIntegration.isAuthenticated());

  ipcMain.handle('gmail:search', async (_event, query: string, maxResults?: number) => {
    if (!gmailIntegration.isAuthenticated()) return [];
    return gmailIntegration.search(String(query || ''), maxResults);
  });

  ipcMain.handle('gmail:create-draft', async (_event, opts: { to: string; subject: string; body: string }) => {
    return gmailIntegration.createDraft(opts);
  });

  // Opens the draft in the user's default browser so they can review and send it themselves.
  ipcMain.handle('gmail:open-draft', async (_event, webUrl: string) => {
    if (typeof webUrl === 'string' && webUrl.startsWith('https://mail.google.com/')) {
      await shell.openExternal(webUrl);
    }
  });
}
