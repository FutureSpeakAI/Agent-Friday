/**
 * Tests for google-oauth.ts — FR-6 shared Google OAuth2 manager (Calendar + Gmail).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const mocks = vi.hoisted(() => ({
  existsSync: vi.fn(),
  readFileSync: vi.fn(),
  vaultRead: vi.fn(),
  vaultWrite: vi.fn(),
  oauth2ClientInstances: [] as any[],
}));

vi.mock('fs', () => ({
  existsSync: mocks.existsSync,
  readFileSync: mocks.readFileSync,
}));

vi.mock('electron', () => ({
  BrowserWindow: vi.fn(),
}));

vi.mock('../../src/main/vault', () => ({
  vaultRead: mocks.vaultRead,
  vaultWrite: mocks.vaultWrite,
}));

function makeFakeOAuth2Client() {
  const listeners: Record<string, Array<(...a: any[]) => void>> = {};
  const client = {
    credentials: {},
    setCredentials: vi.fn((creds: unknown) => { client.credentials = creds as any; }),
    generateAuthUrl: vi.fn(() => 'https://accounts.google.com/o/oauth2/auth?mock=1'),
    getToken: vi.fn(),
    on: vi.fn((event: string, cb: (...a: any[]) => void) => {
      listeners[event] = listeners[event] || [];
      listeners[event].push(cb);
    }),
    removeAllListeners: vi.fn((event: string) => { delete listeners[event]; }),
    __emit: (event: string, ...args: unknown[]) => (listeners[event] || []).forEach((cb) => cb(...args)),
  };
  mocks.oauth2ClientInstances.push(client);
  return client;
}

vi.mock('googleapis', () => ({
  google: {
    auth: {
      OAuth2: vi.fn(function (this: any) {
        const c = makeFakeOAuth2Client();
        return Object.assign(this, c);
      }),
    },
  },
}));

import { googleAuth, GOOGLE_SCOPES } from '../../src/main/google-oauth';

describe('google-oauth.ts', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.oauth2ClientInstances.length = 0;
  });

  describe('getCredentialsPath()/hasCredentialsFile()', () => {
    it('points at google-credentials.json under the agent-friday data dir', () => {
      expect(googleAuth.getCredentialsPath()).toMatch(/agent-friday[\\/]google-credentials\.json$/);
    });

    it('reflects fs.existsSync for the credentials path', () => {
      mocks.existsSync.mockReturnValue(true);
      expect(googleAuth.hasCredentialsFile()).toBe(true);
      mocks.existsSync.mockReturnValue(false);
      expect(googleAuth.hasCredentialsFile()).toBe(false);
    });
  });

  describe('init()', () => {
    it('leaves isAuthenticated() false when no credentials file exists', async () => {
      mocks.existsSync.mockReturnValue(false);
      await googleAuth.init();
      expect(googleAuth.isAuthenticated()).toBe(false);
      expect(googleAuth.getClient()).toBeNull();
    });

    it('leaves isAuthenticated() false when a credentials file exists but no token file does', async () => {
      mocks.existsSync.mockImplementation((p: string) => String(p).includes('google-credentials.json'));
      mocks.readFileSync.mockReturnValue(JSON.stringify({
        installed: { client_id: 'id', client_secret: 'secret', redirect_uris: ['http://localhost/cb'] },
      }));

      await googleAuth.init();

      expect(googleAuth.isAuthenticated()).toBe(false);
      expect(googleAuth.getClient()).toBeNull();
    });

    it('becomes authenticated when both credentials and a stored token exist', async () => {
      mocks.existsSync.mockReturnValue(true);
      mocks.readFileSync.mockReturnValue(JSON.stringify({
        installed: { client_id: 'id', client_secret: 'secret', redirect_uris: ['http://localhost/cb'] },
      }));
      mocks.vaultRead.mockResolvedValue(JSON.stringify({ access_token: 'tok', refresh_token: 'rt' }));

      await googleAuth.init();

      expect(googleAuth.isAuthenticated()).toBe(true);
      expect(googleAuth.getClient()).not.toBeNull();
    });

    it('logs a warning and stays unauthenticated when the credentials file is malformed (missing client_id)', async () => {
      mocks.existsSync.mockImplementation((p: string) => String(p).includes('google-credentials.json'));
      mocks.readFileSync.mockReturnValue(JSON.stringify({ installed: {} }));

      await googleAuth.init();

      expect(googleAuth.isAuthenticated()).toBe(false);
    });
  });

  describe('authenticate()', () => {
    it('returns false immediately if init() was never called (no client)', async () => {
      mocks.existsSync.mockReturnValue(false);
      await googleAuth.init();
      const result = await googleAuth.authenticate();
      expect(result).toBe(false);
    });

    it('requests all four combined scopes (calendar.readonly, calendar.events, gmail.readonly, gmail.compose)', () => {
      expect(GOOGLE_SCOPES).toEqual(
        expect.arrayContaining([
          'https://www.googleapis.com/auth/calendar.readonly',
          'https://www.googleapis.com/auth/calendar.events',
          'https://www.googleapis.com/auth/gmail.readonly',
          'https://www.googleapis.com/auth/gmail.compose',
        ]),
      );
    });

    it('never requests gmail.send — outbound email stays human-gated at the OAuth scope level', () => {
      expect(GOOGLE_SCOPES).not.toContain('https://www.googleapis.com/auth/gmail.send');
    });
  });
});
