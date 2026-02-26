/**
 * 7A — Google Calendar Integration
 * OAuth2 → Calendar API for event awareness, scheduling, and proactive briefings.
 * EVE reads your calendar so she can prepare you for meetings and manage your time.
 */

import { BrowserWindow, ipcMain } from 'electron';
import * as path from 'path';
import * as fs from 'fs';
import { google, calendar_v3 } from 'googleapis';

const SCOPES = ['https://www.googleapis.com/auth/calendar.readonly', 'https://www.googleapis.com/auth/calendar.events'];
const TOKEN_FILE = 'google-calendar-token.json';
const CREDENTIALS_FILE = 'google-calendar-credentials.json';

interface CalendarEvent {
  id: string;
  summary: string;
  description: string;
  location: string;
  start: string; // ISO
  end: string;   // ISO
  attendees: string[];
  organizer: string;
  hangoutLink: string;
  status: string;
  isAllDay: boolean;
}

class CalendarIntegration {
  private oauth2Client: InstanceType<typeof google.auth.OAuth2> | null = null;
  private calendarApi: calendar_v3.Calendar | null = null;
  private dataDir: string;
  private cachedEvents: CalendarEvent[] = [];
  private lastFetch = 0;
  private pollInterval: ReturnType<typeof setInterval> | null = null;

  constructor() {
    this.dataDir = path.join(
      process.env.APPDATA || path.join(process.env.HOME || '', '.config'),
      'nexus-os'
    );
  }

  async init(): Promise<void> {
    const credPath = path.join(this.dataDir, CREDENTIALS_FILE);
    if (!fs.existsSync(credPath)) {
      console.log('[Calendar] No credentials file found — calendar disabled');
      console.log(`[Calendar] Place OAuth2 credentials at: ${credPath}`);
      return;
    }

    try {
      const creds = JSON.parse(fs.readFileSync(credPath, 'utf-8'));
      const { client_id, client_secret, redirect_uris } = creds.installed || creds.web || {};

      if (!client_id || !client_secret) {
        console.warn('[Calendar] Invalid credentials format');
        return;
      }

      this.oauth2Client = new google.auth.OAuth2(
        client_id,
        client_secret,
        redirect_uris?.[0] || 'http://localhost:3000/oauth2callback'
      );

      // Try to load saved token
      const tokenPath = path.join(this.dataDir, TOKEN_FILE);
      if (fs.existsSync(tokenPath)) {
        const token = JSON.parse(fs.readFileSync(tokenPath, 'utf-8'));
        this.oauth2Client.setCredentials(token);

        // Set up automatic token refresh
        this.oauth2Client.on('tokens', (tokens: any) => {
          if (tokens.refresh_token) {
            const existing = JSON.parse(fs.readFileSync(tokenPath, 'utf-8'));
            fs.writeFileSync(tokenPath, JSON.stringify({ ...existing, ...tokens }));
          }
        });

        this.calendarApi = google.calendar({ version: 'v3', auth: this.oauth2Client });
        console.log('[Calendar] Authenticated — starting event polling');

        // Initial fetch
        await this.fetchUpcomingEvents();

        // Poll every 5 minutes
        this.pollInterval = setInterval(() => {
          this.fetchUpcomingEvents().catch((err) =>
            console.warn('[Calendar] Poll error:', err.message)
          );
        }, 5 * 60 * 1000);
      } else {
        console.log('[Calendar] No token found — user needs to authenticate via settings');
      }
    } catch (err) {
      console.warn('[Calendar] Init error:', err);
    }
  }

  /**
   * Start OAuth flow — opens browser window for user to sign in
   */
  async authenticate(): Promise<boolean> {
    if (!this.oauth2Client) return false;

    const authUrl = this.oauth2Client.generateAuthUrl({
      access_type: 'offline',
      scope: SCOPES,
      prompt: 'consent',
    });

    // Open in a BrowserWindow
    const authWindow = new BrowserWindow({
      width: 500,
      height: 700,
      title: 'Sign in to Google Calendar',
      webPreferences: { nodeIntegration: false, contextIsolation: true },
    });

    authWindow.loadURL(authUrl);

    return new Promise((resolve) => {
      authWindow.webContents.on('will-redirect', async (_event, url) => {
        try {
          const urlObj = new URL(url);
          const code = urlObj.searchParams.get('code');
          if (code) {
            const { tokens } = await this.oauth2Client!.getToken(code);
            this.oauth2Client!.setCredentials(tokens);

            // Save token
            const tokenPath = path.join(this.dataDir, TOKEN_FILE);
            fs.writeFileSync(tokenPath, JSON.stringify(tokens));

            this.calendarApi = google.calendar({ version: 'v3', auth: this.oauth2Client! });
            console.log('[Calendar] OAuth complete — authenticated');

            authWindow.close();
            await this.fetchUpcomingEvents();
            resolve(true);
          }
        } catch (err) {
          console.error('[Calendar] Auth error:', err);
          authWindow.close();
          resolve(false);
        }
      });

      authWindow.on('closed', () => resolve(false));
    });
  }

  /**
   * Fetch events for the next 24 hours
   */
  async fetchUpcomingEvents(): Promise<CalendarEvent[]> {
    if (!this.calendarApi) return [];

    try {
      const now = new Date();
      const tomorrow = new Date(now.getTime() + 24 * 60 * 60 * 1000);

      const res = await this.calendarApi.events.list({
        calendarId: 'primary',
        timeMin: now.toISOString(),
        timeMax: tomorrow.toISOString(),
        maxResults: 20,
        singleEvents: true,
        orderBy: 'startTime',
      });

      const events: CalendarEvent[] = (res.data.items || []).map((e: any) => ({
        id: e.id || '',
        summary: e.summary || '(No title)',
        description: e.description || '',
        location: e.location || '',
        start: e.start?.dateTime || e.start?.date || '',
        end: e.end?.dateTime || e.end?.date || '',
        attendees: (e.attendees || []).map((a: any) => a.email || a.displayName || '').filter(Boolean),
        organizer: e.organizer?.email || '',
        hangoutLink: e.hangoutLink || e.conferenceData?.entryPoints?.[0]?.uri || '',
        status: e.status || 'confirmed',
        isAllDay: !e.start?.dateTime,
      }));

      this.cachedEvents = events;
      this.lastFetch = Date.now();
      return events;
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn('[Calendar] Fetch error:', msg);
      return this.cachedEvents;
    }
  }

  /**
   * Get the next N upcoming events
   */
  getUpcoming(count = 5): CalendarEvent[] {
    const now = Date.now();
    return this.cachedEvents
      .filter((e) => new Date(e.start).getTime() > now)
      .slice(0, count);
  }

  /**
   * Get today's full schedule
   */
  getTodaySchedule(): CalendarEvent[] {
    return [...this.cachedEvents];
  }

  /**
   * Create a new calendar event
   */
  async createEvent(opts: {
    summary: string;
    description?: string;
    startTime: string; // ISO
    endTime: string;   // ISO
    attendees?: string[];
    location?: string;
  }): Promise<CalendarEvent | null> {
    if (!this.calendarApi) return null;

    try {
      const res = await this.calendarApi.events.insert({
        calendarId: 'primary',
        requestBody: {
          summary: opts.summary,
          description: opts.description,
          location: opts.location,
          start: { dateTime: opts.startTime },
          end: { dateTime: opts.endTime },
          attendees: opts.attendees?.map((email) => ({ email })),
        },
      });

      const e = res.data;
      const created: CalendarEvent = {
        id: e.id || '',
        summary: e.summary || '',
        description: e.description || '',
        location: e.location || '',
        start: e.start?.dateTime || e.start?.date || '',
        end: e.end?.dateTime || e.end?.date || '',
        attendees: (e.attendees || []).map((a: any) => a.email || '').filter(Boolean),
        organizer: e.organizer?.email || '',
        hangoutLink: e.hangoutLink || '',
        status: e.status || 'confirmed',
        isAllDay: !e.start?.dateTime,
      };

      // Refresh cache
      await this.fetchUpcomingEvents();
      return created;
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error('[Calendar] Create error:', msg);
      return null;
    }
  }

  /**
   * Build context string for system prompt injection
   */
  getContextString(): string {
    if (this.cachedEvents.length === 0) return '';

    const now = new Date();
    const upcoming = this.getUpcoming(3);
    if (upcoming.length === 0) return '';

    const lines = ['## Calendar — Upcoming Events'];
    for (const e of upcoming) {
      const start = new Date(e.start);
      const minsUntil = Math.round((start.getTime() - now.getTime()) / 60000);
      const timeStr = start.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

      let line = `- ${timeStr} (in ${minsUntil}m): ${e.summary}`;
      if (e.attendees.length > 0) line += ` [${e.attendees.length} attendees]`;
      if (e.hangoutLink) line += ' [has video link]';
      if (e.location) line += ` @ ${e.location}`;
      lines.push(line);
    }

    return lines.join('\n');
  }

  isAuthenticated(): boolean {
    return this.calendarApi !== null;
  }

  stop(): void {
    if (this.pollInterval) {
      clearInterval(this.pollInterval);
      this.pollInterval = null;
    }
  }
}

export const calendarIntegration = new CalendarIntegration();

// IPC handlers
export function registerCalendarHandlers(): void {
  ipcMain.handle('calendar:authenticate', async () => {
    return calendarIntegration.authenticate();
  });

  ipcMain.handle('calendar:is-authenticated', () => {
    return calendarIntegration.isAuthenticated();
  });

  ipcMain.handle('calendar:get-upcoming', async (_event, count?: number) => {
    if (!calendarIntegration.isAuthenticated()) {
      return [];
    }
    // Refresh if stale (>2 min)
    const stale = Date.now() - (calendarIntegration as any).lastFetch > 2 * 60 * 1000;
    if (stale) await calendarIntegration.fetchUpcomingEvents();
    return calendarIntegration.getUpcoming(count);
  });

  ipcMain.handle('calendar:get-today', async () => {
    if (!calendarIntegration.isAuthenticated()) return [];
    return calendarIntegration.getTodaySchedule();
  });

  ipcMain.handle('calendar:create-event', async (_event, opts) => {
    return calendarIntegration.createEvent(opts);
  });
}
