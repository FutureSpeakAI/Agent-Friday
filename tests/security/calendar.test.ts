/**
 * Tests for calendar.ts — FR-6 refactor: OAuth now delegates to the shared
 * google-oauth.ts manager instead of managing its own client. Public API
 * (init/authenticate/isAuthenticated/fetchUpcomingEvents/createEvent/
 * getContextString) must behave the same as before the refactor.
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
}));

const mocks = vi.hoisted(() => ({
  isAuthenticated: vi.fn(),
  getClient: vi.fn(),
  authenticate: vi.fn(),
  requireConsent: vi.fn(),
  eventsList: vi.fn(),
  eventsInsert: vi.fn(),
}));

vi.mock('../../src/main/google-oauth', () => ({
  googleAuth: {
    isAuthenticated: mocks.isAuthenticated,
    getClient: mocks.getClient,
    authenticate: mocks.authenticate,
  },
}));

vi.mock('../../src/main/consent-gate', () => ({
  requireConsent: mocks.requireConsent,
}));

vi.mock('googleapis', () => ({
  google: {
    calendar: vi.fn(() => ({
      events: { list: mocks.eventsList, insert: mocks.eventsInsert },
    })),
  },
}));

import { calendarIntegration, registerCalendarHandlers } from '../../src/main/calendar';

function invoke(channel: string, ...args: unknown[]): unknown {
  const handler = handlers.get(channel);
  if (!handler) throw new Error(`No handler for ${channel}`);
  return handler({}, ...args);
}

describe('calendar.ts (FR-6 refactor)', () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    handlers.clear();
    calendarIntegration.stop();
    // calendarIntegration is a module singleton — force a clean, unauthenticated
    // state between tests rather than relying on stop() (which only clears the
    // poll interval, not the cached API client).
    mocks.isAuthenticated.mockReturnValue(false);
    await calendarIntegration.init();
    vi.clearAllMocks();
  });

  describe('init()', () => {
    it('leaves isAuthenticated() false when the shared auth manager is not authenticated', async () => {
      mocks.isAuthenticated.mockReturnValue(false);
      await calendarIntegration.init();
      expect(calendarIntegration.isAuthenticated()).toBe(false);
    });

    it('builds a Calendar API client and fetches events when the shared auth manager is authenticated', async () => {
      mocks.isAuthenticated.mockReturnValue(true);
      mocks.getClient.mockReturnValue({});
      mocks.eventsList.mockResolvedValue({ data: { items: [] } });

      await calendarIntegration.init();

      expect(calendarIntegration.isAuthenticated()).toBe(true);
      expect(mocks.eventsList).toHaveBeenCalled();
    });
  });

  describe('authenticate()', () => {
    it('delegates to googleAuth.authenticate() and rebuilds its own client on success', async () => {
      mocks.authenticate.mockResolvedValue(true);
      mocks.isAuthenticated.mockReturnValue(true);
      mocks.getClient.mockReturnValue({});
      mocks.eventsList.mockResolvedValue({ data: { items: [] } });

      const result = await calendarIntegration.authenticate();

      expect(mocks.authenticate).toHaveBeenCalledTimes(1);
      expect(result).toBe(true);
      expect(calendarIntegration.isAuthenticated()).toBe(true);
    });

    it('does not attempt to rebuild its client when googleAuth.authenticate() fails', async () => {
      mocks.authenticate.mockResolvedValue(false);

      const result = await calendarIntegration.authenticate();

      expect(result).toBe(false);
      // init() re-fetches events via events.list() — if authenticate() failing
      // still triggered a rebuild, this would have been called.
      expect(mocks.eventsList).not.toHaveBeenCalled();
    });
  });

  describe('createEvent()', () => {
    it('requires consent before creating an event (existing cLaw Security Fix CRITICAL-005 behavior)', async () => {
      mocks.isAuthenticated.mockReturnValue(true);
      mocks.getClient.mockReturnValue({});
      mocks.eventsList.mockResolvedValue({ data: { items: [] } });
      await calendarIntegration.init();

      mocks.requireConsent.mockResolvedValue(false);
      const result = await calendarIntegration.createEvent({
        summary: 'Test', startTime: '2026-08-12T10:00:00Z', endTime: '2026-08-12T10:30:00Z',
      });

      expect(mocks.requireConsent).toHaveBeenCalledWith('create_calendar_event', expect.any(Object));
      expect(result).toBeNull();
      expect(mocks.eventsInsert).not.toHaveBeenCalled();
    });
  });

  describe('registerCalendarHandlers()', () => {
    it('registers the expected IPC channels', () => {
      registerCalendarHandlers();
      expect(handlers.has('calendar:authenticate')).toBe(true);
      expect(handlers.has('calendar:is-authenticated')).toBe(true);
      expect(handlers.has('calendar:get-upcoming')).toBe(true);
      expect(handlers.has('calendar:get-today')).toBe(true);
      expect(handlers.has('calendar:create-event')).toBe(true);
    });

    it('calendar:is-authenticated reflects calendarIntegration state', async () => {
      registerCalendarHandlers();
      mocks.isAuthenticated.mockReturnValue(false);
      await calendarIntegration.init();
      expect(invoke('calendar:is-authenticated')).toBe(false);
    });
  });
});
