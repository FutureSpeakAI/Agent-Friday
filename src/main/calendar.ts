/**
 * 7A — Google Calendar Integration
 * OAuth2 → Calendar API for event awareness, scheduling, and proactive briefings.
 * Friday reads your calendar so she can prepare you for meetings and manage your time.
 *
 * FR-6: OAuth is now handled by the shared google-oauth.ts manager (one consent
 * flow covering Calendar + Gmail) rather than a private client here. Public API
 * of this module is unchanged so existing callers (personality.ts, meeting-prep.ts,
 * IPC handlers) don't need to change.
 */

import { ipcMain } from 'electron';
import { google, calendar_v3 } from 'googleapis';
import { requireConsent } from './consent-gate';
import { googleAuth } from './google-oauth';

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
  private calendarApi: calendar_v3.Calendar | null = null;
  private cachedEvents: CalendarEvent[] = [];
  private lastFetch = 0;
  private pollInterval: ReturnType<typeof setInterval> | null = null;

  /**
   * Build the Calendar API client from the shared google-oauth.ts manager.
   * Callers (index.ts) call googleAuth.init() before this — see startup
   * sequencing note on the exported registerCalendarHandlers().
   */
  async init(): Promise<void> {
    // Clean up any existing state to support vault-reload calls (Phase B)
    this.stop();

    if (!googleAuth.isAuthenticated()) {
      this.calendarApi = null;
      return;
    }

    const client = googleAuth.getClient();
    if (!client) {
      this.calendarApi = null;
      return;
    }

    this.calendarApi = google.calendar({ version: 'v3', auth: client });
    console.log('[Calendar] Authenticated — starting event polling');

    await this.fetchUpcomingEvents();

    this.pollInterval = setInterval(() => {
      this.fetchUpcomingEvents().catch((err) =>
        console.warn('[Calendar] Poll error:', err.message)
      );
    }, 5 * 60 * 1000);
  }

  /**
   * One-time authorization step (FR-6): delegates to the shared google-oauth
   * manager (Calendar + Gmail in one consent flow), then rebuilds this
   * module's own API client from the freshly authorized token.
   */
  async authenticate(): Promise<boolean> {
    const ok = await googleAuth.authenticate();
    if (ok) await this.init();
    return ok;
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

    // cLaw Security Fix (CRITICAL-005): Require user consent before creating calendar events.
    // Creating events with attendees sends Google Calendar invitations — an external side effect.
    const approved = await requireConsent('create_calendar_event', {
      summary: opts.summary,
      startTime: opts.startTime,
      endTime: opts.endTime,
      attendees: opts.attendees?.join(', ') || '(none)',
      location: opts.location || '(none)',
    });
    if (!approved) {
      console.log('[Calendar] Event creation denied by user');
      return null;
    }

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
    // OAuth token listener lifecycle is owned by the shared google-oauth.ts manager.
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
