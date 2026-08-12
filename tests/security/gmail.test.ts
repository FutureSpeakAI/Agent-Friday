/**
 * Tests for gmail.ts — FR-6 (Google Calendar/Gmail tool wiring) from
 * dev/friday-orchestrator-integrity-spec.md.
 *
 * The key security property under test: this module only ever calls
 * `users.drafts.create` — never `users.messages.send`. The mocked Gmail API
 * client below doesn't even expose a `send` method, so any code path that
 * tried to send would throw, not silently succeed.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

type IpcHandler = (...args: unknown[]) => unknown;
const handlers = new Map<string, IpcHandler>();

vi.mock('electron', () => ({
  ipcMain: {
    handle: vi.fn((channel: string, handler: IpcHandler) => {
      handlers.set(channel, handler);
    }),
  },
  shell: { openExternal: vi.fn() },
}));

const mocks = vi.hoisted(() => ({
  getClient: vi.fn(),
  messagesList: vi.fn(),
  messagesGet: vi.fn(),
  draftsCreate: vi.fn(),
}));

vi.mock('../../src/main/google-oauth', () => ({
  googleAuth: { getClient: mocks.getClient },
}));

vi.mock('googleapis', () => ({
  google: {
    gmail: vi.fn(() => ({
      users: {
        messages: { list: mocks.messagesList, get: mocks.messagesGet },
        drafts: { create: mocks.draftsCreate },
        // Deliberately no `send` — see file header.
      },
    })),
  },
}));

import { gmailIntegration, registerGmailHandlers } from '../../src/main/gmail';

function invoke(channel: string, ...args: unknown[]): unknown {
  const handler = handlers.get(channel);
  if (!handler) throw new Error(`No handler for ${channel}`);
  return handler({}, ...args);
}

describe('gmail.ts', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    handlers.clear();
  });

  describe('init()/isAuthenticated()', () => {
    it('is not authenticated when googleAuth has no client', () => {
      mocks.getClient.mockReturnValue(null);
      gmailIntegration.init();
      expect(gmailIntegration.isAuthenticated()).toBe(false);
    });

    it('is authenticated once googleAuth provides a client', () => {
      mocks.getClient.mockReturnValue({});
      gmailIntegration.init();
      expect(gmailIntegration.isAuthenticated()).toBe(true);
    });
  });

  describe('search()', () => {
    it('returns [] without calling the API when not authenticated', async () => {
      mocks.getClient.mockReturnValue(null);
      gmailIntegration.init();

      const results = await gmailIntegration.search('is:unread');
      expect(results).toEqual([]);
      expect(mocks.messagesList).not.toHaveBeenCalled();
    });

    it('maps Gmail API results to EmailSummary — subject, from, snippet, date, unread', async () => {
      mocks.getClient.mockReturnValue({});
      gmailIntegration.init();

      mocks.messagesList.mockResolvedValue({ data: { messages: [{ id: 'm1' }] } });
      mocks.messagesGet.mockResolvedValue({
        data: {
          threadId: 't1',
          snippet: 'Please pay your invoice',
          labelIds: ['UNREAD', 'INBOX'],
          payload: {
            headers: [
              { name: 'Subject', value: 'Invoice due' },
              { name: 'From', value: 'billing@example.com' },
              { name: 'Date', value: 'Mon, 12 Aug 2026 09:00:00 +0000' },
            ],
          },
        },
      });

      const results = await gmailIntegration.search('is:unread', 5);

      expect(mocks.messagesList).toHaveBeenCalledWith(
        expect.objectContaining({ userId: 'me', q: 'is:unread', maxResults: 5 }),
      );
      expect(results).toEqual([
        {
          id: 'm1',
          threadId: 't1',
          subject: 'Invoice due',
          from: 'billing@example.com',
          snippet: 'Please pay your invoice',
          date: 'Mon, 12 Aug 2026 09:00:00 +0000',
          unread: true,
        },
      ]);
    });

    it('clamps maxResults into [1, 25]', async () => {
      mocks.getClient.mockReturnValue({});
      gmailIntegration.init();
      mocks.messagesList.mockResolvedValue({ data: { messages: [] } });

      await gmailIntegration.search('x', 999);
      expect(mocks.messagesList).toHaveBeenCalledWith(expect.objectContaining({ maxResults: 25 }));

      await gmailIntegration.search('x', -5);
      expect(mocks.messagesList).toHaveBeenCalledWith(expect.objectContaining({ maxResults: 1 }));
    });

    it('returns [] when there are no matching messages', async () => {
      mocks.getClient.mockReturnValue({});
      gmailIntegration.init();
      mocks.messagesList.mockResolvedValue({ data: {} });

      const results = await gmailIntegration.search('nothing matches this');
      expect(results).toEqual([]);
      expect(mocks.messagesGet).not.toHaveBeenCalled();
    });
  });

  describe('createDraft() — draft-only, never sends', () => {
    it('returns null without calling the API when not authenticated', async () => {
      mocks.getClient.mockReturnValue(null);
      gmailIntegration.init();

      const result = await gmailIntegration.createDraft({ to: 'a@b.com', subject: 's', body: 'b' });
      expect(result).toBeNull();
      expect(mocks.draftsCreate).not.toHaveBeenCalled();
    });

    it('calls users.drafts.create (not send) with a base64url-encoded RFC 2822 message', async () => {
      mocks.getClient.mockReturnValue({});
      gmailIntegration.init();
      mocks.draftsCreate.mockResolvedValue({ data: { id: 'draft-1' } });

      const result = await gmailIntegration.createDraft({
        to: 'robb@example.com',
        subject: 'Reschedule',
        body: 'Can we move our meeting?',
      });

      expect(mocks.draftsCreate).toHaveBeenCalledTimes(1);
      const call = mocks.draftsCreate.mock.calls[0][0];
      expect(call.userId).toBe('me');
      const decoded = Buffer.from(call.requestBody.message.raw, 'base64url').toString('utf-8');
      expect(decoded).toContain('To: robb@example.com');
      expect(decoded).toContain('Subject: Reschedule');
      expect(decoded).toContain('Can we move our meeting?');

      expect(result).toEqual({
        id: 'draft-1',
        webUrl: 'https://mail.google.com/mail/u/0/#drafts?compose=draft-1',
      });
    });

    it('returns null if the API responds without a draft id', async () => {
      mocks.getClient.mockReturnValue({});
      gmailIntegration.init();
      mocks.draftsCreate.mockResolvedValue({ data: {} });

      const result = await gmailIntegration.createDraft({ to: 'a@b.com', subject: 's', body: 'b' });
      expect(result).toBeNull();
    });
  });

  describe('registerGmailHandlers()', () => {
    beforeEach(() => {
      registerGmailHandlers();
    });

    it('registers all expected IPC channels', () => {
      expect(handlers.has('gmail:is-authenticated')).toBe(true);
      expect(handlers.has('gmail:search')).toBe(true);
      expect(handlers.has('gmail:create-draft')).toBe(true);
      expect(handlers.has('gmail:open-draft')).toBe(true);
    });

    it('gmail:search returns [] when not authenticated, without touching the API', async () => {
      mocks.getClient.mockReturnValue(null);
      gmailIntegration.init();

      const result = await invoke('gmail:search', 'anything');
      expect(result).toEqual([]);
      expect(mocks.messagesList).not.toHaveBeenCalled();
    });

    it('gmail:open-draft only opens genuine mail.google.com URLs', async () => {
      const { shell } = await import('electron');
      await invoke('gmail:open-draft', 'https://mail.google.com/mail/u/0/#drafts?compose=d1');
      expect(shell.openExternal).toHaveBeenCalledWith('https://mail.google.com/mail/u/0/#drafts?compose=d1');

      vi.mocked(shell.openExternal).mockClear();
      await invoke('gmail:open-draft', 'https://evil.example.com/phish');
      expect(shell.openExternal).not.toHaveBeenCalled();
    });
  });
});
