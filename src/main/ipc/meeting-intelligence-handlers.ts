/**
 * IPC handlers for the Meeting Intelligence engine.
 */

import { ipcMain } from 'electron';
import { meetingIntelligence, Meeting, MeetingStatus, MeetingAgent, MeetingNote } from '../meeting-intelligence';

export function registerMeetingIntelligenceHandlers(): void {
  // ── CRUD ────────────────────────────────────────────────────────

  ipcMain.handle('meeting-intel:create', (_event, opts: {
    name: string;
    description?: string;
    scheduledStart?: string;
    scheduledEnd?: string;
    attendees?: string[];
    meetingUrl?: string;
    platform?: string;
    calendarEventId?: string;
    agent?: MeetingAgent;
    tags?: string[];
    projectName?: string;
  }) => {
    return meetingIntelligence.createMeeting({
      ...opts,
      platform: opts.platform as Meeting['platform'],
    });
  });

  ipcMain.handle('meeting-intel:get', (_event, id: string) => {
    return meetingIntelligence.getMeeting(id);
  });

  ipcMain.handle('meeting-intel:list', (_event, opts?: {
    status?: MeetingStatus;
    limit?: number;
    search?: string;
  }) => {
    return meetingIntelligence.listMeetings(opts);
  });

  ipcMain.handle('meeting-intel:get-active', () => {
    return meetingIntelligence.getActiveMeeting();
  });

  ipcMain.handle('meeting-intel:update', (_event, meetingId: string, updates: {
    name?: string;
    description?: string;
    tags?: string[];
    agent?: MeetingAgent;
    projectName?: string;
  }) => {
    return meetingIntelligence.updateMeeting(meetingId, updates);
  });

  // ── Lifecycle ───────────────────────────────────────────────────

  ipcMain.handle('meeting-intel:start', async (_event, meetingId: string) => {
    return meetingIntelligence.startMeeting(meetingId);
  });

  ipcMain.handle('meeting-intel:end', async (_event, meetingId: string, opts?: {
    transcript?: string;
    recording?: string;
  }) => {
    return meetingIntelligence.endMeeting(meetingId, opts);
  });

  ipcMain.handle('meeting-intel:cancel', (_event, meetingId: string) => {
    return meetingIntelligence.cancelMeeting(meetingId);
  });

  ipcMain.handle('meeting-intel:end-active', async (_event, transcript?: string) => {
    return meetingIntelligence.endActiveMeeting(transcript);
  });

  // ── Notes ───────────────────────────────────────────────────────

  ipcMain.handle('meeting-intel:add-note', (_event, meetingId: string, note: {
    content: string;
    type?: MeetingNote['type'];
    author?: MeetingNote['author'];
  }) => {
    return meetingIntelligence.addNote(meetingId, note);
  });

  ipcMain.handle('meeting-intel:add-note-active', (_event, content: string, type?: MeetingNote['type']) => {
    return meetingIntelligence.addNoteToActive(content, type);
  });

  // ── Content ─────────────────────────────────────────────────────

  ipcMain.handle('meeting-intel:set-transcript', (_event, meetingId: string, transcript: string) => {
    return meetingIntelligence.setTranscript(meetingId, transcript);
  });

  ipcMain.handle('meeting-intel:set-summary', (_event, meetingId: string, summary: string) => {
    return meetingIntelligence.setSummary(meetingId, summary);
  });

  // ── Search & Stats ──────────────────────────────────────────────

  ipcMain.handle('meeting-intel:search', (_event, query: string, limit?: number) => {
    return meetingIntelligence.searchMeetings(query, limit);
  });

  ipcMain.handle('meeting-intel:stats', () => {
    return meetingIntelligence.getStats();
  });

  ipcMain.handle('meeting-intel:recent-summaries', (_event, count?: number) => {
    return meetingIntelligence.getRecentSummaries(count);
  });

  // ── Calendar bridge ─────────────────────────────────────────────

  ipcMain.handle('meeting-intel:from-calendar', (_event, event: {
    id: string;
    summary: string;
    description: string;
    start: string;
    end: string;
    attendees: string[];
    hangoutLink?: string;
  }) => {
    return meetingIntelligence.createFromCalendarEvent(event);
  });

  // ── Quick start from call ───────────────────────────────────────

  ipcMain.handle('meeting-intel:quick-start', (_event, meetingUrl: string, name?: string) => {
    return meetingIntelligence.quickStartFromCall(meetingUrl, name);
  });

  // ── Refresh attendee intel ──────────────────────────────────────

  ipcMain.handle('meeting-intel:refresh-intel', (_event, meetingId: string) => {
    return meetingIntelligence.refreshAttendeeIntel(meetingId);
  });

  // ── Context ─────────────────────────────────────────────────────

  ipcMain.handle('meeting-intel:get-context', () => {
    return meetingIntelligence.getContextString();
  });
}
