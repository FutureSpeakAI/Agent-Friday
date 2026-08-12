/**
 * google-oauth.ts — FR-6: shared Google OAuth2 wiring for Friday's tool layer.
 *
 * One consent flow, one token, covering:
 *   - Calendar: readonly + events (calendar.ts already used this before FR-6;
 *     this module just centralizes the OAuth client so Gmail can share it)
 *   - Gmail: readonly + compose — deliberately NOT gmail.send. Outbound email
 *     stays human-gated at the OAuth scope level, not just by prompt
 *     convention: even a compromised or misused draft_email tool call cannot
 *     send anything, because the token this app holds has no send permission.
 *
 * Stephen must supply his own Google Cloud OAuth2 client credentials — this
 * module never stubs or fabricates them (see CREDENTIALS_FILE below).
 * Tokens are stored through the Sovereign Vault (vaultRead/vaultWrite),
 * matching calendar.ts's existing security posture.
 */

import { BrowserWindow } from 'electron';
import * as path from 'path';
import * as fs from 'fs';
import { google } from 'googleapis';
import { vaultRead, vaultWrite } from './vault';

export const GOOGLE_SCOPES = [
  'https://www.googleapis.com/auth/calendar.readonly',
  'https://www.googleapis.com/auth/calendar.events',
  'https://www.googleapis.com/auth/gmail.readonly',
  'https://www.googleapis.com/auth/gmail.compose',
];

const TOKEN_FILE = 'google-token.json';
const CREDENTIALS_FILE = 'google-credentials.json';

class GoogleAuthManager {
  private oauth2Client: InstanceType<typeof google.auth.OAuth2> | null = null;
  private authenticated = false;
  private dataDir: string;

  constructor() {
    this.dataDir = path.join(
      process.env.APPDATA || path.join(process.env.HOME || '', '.config'),
      'agent-friday'
    );
  }

  /** Absolute path where Stephen must place his Google Cloud OAuth2 client credentials. */
  getCredentialsPath(): string {
    return path.join(this.dataDir, CREDENTIALS_FILE);
  }

  /** Whether a credentials file has been supplied at all — distinct from whether the user has authorized. */
  hasCredentialsFile(): boolean {
    return fs.existsSync(this.getCredentialsPath());
  }

  /**
   * Load credentials + any stored token. Safe to call repeatedly (e.g. once
   * before vault unlock, once after — tokens are vault-encrypted so the
   * second call is what actually picks them up).
   */
  async init(): Promise<void> {
    if (this.oauth2Client) this.oauth2Client.removeAllListeners('tokens');
    this.oauth2Client = null;
    this.authenticated = false;

    const credPath = this.getCredentialsPath();
    if (!fs.existsSync(credPath)) {
      console.log('[GoogleAuth] No credentials file found — Google integration disabled');
      console.log(`[GoogleAuth] Place OAuth2 credentials at: ${credPath}`);
      return;
    }

    try {
      const creds = JSON.parse(fs.readFileSync(credPath, 'utf-8'));
      const { client_id, client_secret, redirect_uris } = creds.installed || creds.web || {};

      if (!client_id || !client_secret) {
        console.warn('[GoogleAuth] Invalid credentials format');
        return;
      }

      this.oauth2Client = new google.auth.OAuth2(
        client_id,
        client_secret,
        redirect_uris?.[0] || 'http://localhost:3000/oauth2callback'
      );

      const tokenPath = path.join(this.dataDir, TOKEN_FILE);
      if (fs.existsSync(tokenPath)) {
        const tokenRaw = await vaultRead(tokenPath);
        const token = JSON.parse(tokenRaw);
        this.oauth2Client.setCredentials(token);
        this.authenticated = true;

        this.oauth2Client.removeAllListeners('tokens');
        this.oauth2Client.on('tokens', async (tokens: any) => {
          if (tokens.refresh_token) {
            try {
              const existingRaw = await vaultRead(tokenPath);
              const existing = JSON.parse(existingRaw);
              await vaultWrite(tokenPath, JSON.stringify({ ...existing, ...tokens }));
            } catch (err) {
              console.warn('[GoogleAuth] Token refresh persistence failed:', err instanceof Error ? err.message : 'Unknown error');
            }
          }
        });

        console.log('[GoogleAuth] Authenticated from stored token (Calendar + Gmail)');
      } else {
        console.log('[GoogleAuth] No token found — user needs to authorize via Settings → Integrations');
      }
    } catch (err) {
      console.warn('[GoogleAuth] Init error:', err instanceof Error ? err.message : 'Unknown error');
    }
  }

  /**
   * One-time authorization step: opens a Google sign-in window covering the
   * combined Calendar + Gmail scopes. Must be initiated by Stephen — this is
   * never triggered automatically.
   */
  async authenticate(): Promise<boolean> {
    if (!this.oauth2Client) return false;

    const authUrl = this.oauth2Client.generateAuthUrl({
      access_type: 'offline',
      scope: GOOGLE_SCOPES,
      prompt: 'consent',
    });

    const authWindow = new BrowserWindow({
      width: 500,
      height: 700,
      title: 'Sign in to Google (Calendar + Gmail)',
      webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true },
    });

    authWindow.loadURL(authUrl);

    return new Promise((resolve) => {
      authWindow.webContents.once('will-redirect', async (_event, url) => {
        try {
          const urlObj = new URL(url);
          const code = urlObj.searchParams.get('code');
          if (code) {
            const { tokens } = await this.oauth2Client!.getToken(code);
            this.oauth2Client!.setCredentials(tokens);

            const tokenPath = path.join(this.dataDir, TOKEN_FILE);
            await vaultWrite(tokenPath, JSON.stringify(tokens));

            this.authenticated = true;
            console.log('[GoogleAuth] OAuth complete — Calendar + Gmail authenticated');

            authWindow.close();
            resolve(true);
          }
        } catch (err) {
          console.error('[GoogleAuth] Auth error:', err instanceof Error ? err.message : 'Unknown error');
          authWindow.close();
          resolve(false);
        }
      });

      authWindow.once('closed', () => resolve(false));
    });
  }

  isAuthenticated(): boolean {
    return this.authenticated;
  }

  /** The shared OAuth2 client for building Calendar/Gmail API clients — null until authenticated. */
  getClient(): InstanceType<typeof google.auth.OAuth2> | null {
    return this.authenticated ? this.oauth2Client : null;
  }

  stop(): void {
    if (this.oauth2Client) {
      this.oauth2Client.removeAllListeners('tokens');
    }
  }
}

export const googleAuth = new GoogleAuthManager();
