/**
 * 7C — Meeting Intelligence Engine
 *
 * Full meeting lifecycle management inspired by Meet AI Platform architecture.
 * Integrates with existing meeting-prep.ts (pre-meeting), call-integration.ts
 * (during-meeting), and adds post-meeting transcription + summarization.
 *
 * Lifecycle:  upcoming → active → processing → completed | cancelled
 *
 * Features:
 *  - Meeting lifecycle state machine with timestamps
 *  - Agent-meeting binding (custom AI instructions per meeting)
 *  - Live note-taking during active meetings
 *  - Post-meeting transcription (Whisper) + summarization (Claude/OpenRouter)
 *  - Meeting history with full-text search
 *  - Context injection into system prompt
 *  - Trust Graph integration for attendee intelligence
 */

import { app } from 'electron';
import fs from 'fs/promises';
import path from 'path';
import crypto from 'crypto';
import { meetingPrep } from './meeting-prep';
import { trustGraph } from './trust-graph';
import { memoryManager } from './memory';

// ── Data Model ──────────────────────────────────────────────────────

export type MeetingStatus = 'upcoming' | 'active' | 'processing' | 'completed' | 'cancelled';

export interface MeetingAgent {
  name: string;             // Agent persona name for this meeting
  instructions: string;     // Custom system prompt / behavioral instructions
}

export interface MeetingNote {
  id: string;
  timestamp: number;
  author: 'user' | 'agent' | 'auto';
  content: string;
  type: 'note' | 'action-item' | 'decision' | 'question' | 'insight';
}

export interface AttendeeIntel {
  name: string;
  email?: string;
  trustScore?: number;
  trustProfile?: string;
  domains?: string[];
  recentTopics?: string[];
  memories?: string[];
}

export interface Meeting {
  id: string;
  name: string;
  description: string;
  status: MeetingStatus;
  calendarEventId?: string;

  // Agent binding
  agent?: MeetingAgent;

  // Attendees
  attendees: string[];
  attendeeIntel: AttendeeIntel[];

  // Lifecycle timestamps
  createdAt: number;
  scheduledStart?: string;       // ISO datetime
  scheduledEnd?: string;         // ISO datetime
  startedAt?: number;
  endedAt?: number;

  // Meeting link
  meetingUrl?: string;
  platform?: 'google-meet' | 'zoom' | 'teams' | 'other';

  // Content
  notes: MeetingNote[];
  transcript?: string;           // Full transcript text
  transcriptUrl?: string;        // Path to local transcript file
  recording?: string;            // Path to local recording file
  summary?: string;              // AI-generated summary
  actionItems?: string[];        // Extracted action items
  keyDecisions?: string[];       // Extracted decisions

  // Pre-meeting briefing (from meeting-prep.ts)
  briefingText?: string;

  // Tags & metadata
  tags: string[];
  projectName?: string;
}

export interface MeetingIntelConfig {
  maxMeetings: number;           // Max stored meetings, default 200
  autoSummarize: boolean;        // Auto-summarize when meeting ends, default true
  autoExtractActions: boolean;   // Auto-extract action items, default true
  transcriptionEnabled: boolean; // Enable Whisper transcription, default true
  retentionDays: number;         // How long to keep old meetings, default 90
}

const DEFAULT_CONFIG: MeetingIntelConfig = {
  maxMeetings: 200,
  autoSummarize: true,
  autoExtractActions: true,
  transcriptionEnabled: true,
  retentionDays: 90,
};

// ── Meeting Intelligence Engine ─────────────────────────────────────

class MeetingIntelligence {
  private meetings: Meeting[] = [];
  private config: MeetingIntelConfig = { ...DEFAULT_CONFIG };
  private filePath = '';
  private saveQueue: Promise<void> = Promise.resolve();
  private activeMeetingId: string | null = null;

  async initialize(): Promise<void> {
    const userDataDir = app.getPath('userData');
    this.filePath = path.join(userDataDir, 'meetings.json');

    try {
      const raw = await fs.readFile(this.filePath, 'utf-8');
      const data = JSON.parse(raw);
      this.meetings = data.meetings || [];
      if (data.config) {
        this.config = { ...DEFAULT_CONFIG, ...data.config };
      }
      console.log(`[MeetingIntel] Loaded ${this.meetings.length} meetings`);
    } catch {
      this.meetings = [];
      console.log('[MeetingIntel] Starting fresh — no existing meetings data');
    }

    // Prune old completed meetings beyond retention
    this.pruneOldMeetings();

    // Recover any meetings stuck in 'active' or 'processing' (app crash recovery)
    for (const m of this.meetings) {
      if (m.status === 'active' && m.startedAt) {
        const hoursActive = (Date.now() - m.startedAt) / (60 * 60 * 1000);
        if (hoursActive > 8) {
          // Meeting has been "active" for 8+ hours — likely a crash. Auto-complete it.
          m.status = 'completed';
          m.endedAt = m.startedAt + 60 * 60 * 1000; // Assume 1 hour
          console.log(`[MeetingIntel] Recovered stale active meeting: "${m.name}"`);
        }
      }
      if (m.status === 'processing') {
        // Processing state with no result — just mark completed
        m.status = 'completed';
        console.log(`[MeetingIntel] Recovered stuck processing meeting: "${m.name}"`);
      }
    }

    await this.save();
  }

  // ── CRUD Operations ─────────────────────────────────────────────

  /**
   * Create a new meeting. Can be linked to a calendar event.
   */
  createMeeting(opts: {
    name: string;
    description?: string;
    scheduledStart?: string;
    scheduledEnd?: string;
    attendees?: string[];
    meetingUrl?: string;
    platform?: Meeting['platform'];
    calendarEventId?: string;
    agent?: MeetingAgent;
    tags?: string[];
    projectName?: string;
  }): Meeting {
    const id = crypto.randomUUID().slice(0, 12);

    // Build attendee intelligence from Trust Graph
    const attendeeIntel: AttendeeIntel[] = [];
    for (const attendee of (opts.attendees || []).slice(0, 15)) {
      const intel = this.buildAttendeeIntel(attendee);
      attendeeIntel.push(intel);
    }

    const meeting: Meeting = {
      id,
      name: opts.name,
      description: opts.description || '',
      status: 'upcoming',
      calendarEventId: opts.calendarEventId,
      agent: opts.agent,
      attendees: opts.attendees || [],
      attendeeIntel,
      createdAt: Date.now(),
      scheduledStart: opts.scheduledStart,
      scheduledEnd: opts.scheduledEnd,
      meetingUrl: opts.meetingUrl,
      platform: opts.platform || this.detectPlatform(opts.meetingUrl),
      notes: [],
      tags: opts.tags || [],
      projectName: opts.projectName,
    };

    this.meetings.unshift(meeting);

    // Enforce max meetings
    if (this.meetings.length > this.config.maxMeetings) {
      this.meetings = this.meetings.slice(0, this.config.maxMeetings);
    }

    this.save();
    console.log(`[MeetingIntel] Created meeting "${meeting.name}" (${id})`);
    return meeting;
  }

  /**
   * Get a meeting by ID.
   */
  getMeeting(id: string): Meeting | null {
    return this.meetings.find((m) => m.id === id) || null;
  }

  /**
   * Get the currently active meeting.
   */
  getActiveMeeting(): Meeting | null {
    if (!this.activeMeetingId) return null;
    return this.getMeeting(this.activeMeetingId);
  }

  /**
   * List meetings with optional filters.
   */
  listMeetings(opts?: {
    status?: MeetingStatus;
    limit?: number;
    search?: string;
  }): Meeting[] {
    let results = [...this.meetings];

    if (opts?.status) {
      results = results.filter((m) => m.status === opts.status);
    }

    if (opts?.search) {
      const q = opts.search.toLowerCase();
      results = results.filter(
        (m) =>
          m.name.toLowerCase().includes(q) ||
          m.description.toLowerCase().includes(q) ||
          m.summary?.toLowerCase().includes(q) ||
          m.tags.some((t) => t.toLowerCase().includes(q)) ||
          m.attendees.some((a) => a.toLowerCase().includes(q))
      );
    }

    return results.slice(0, opts?.limit || 50);
  }

  // ── Lifecycle State Machine ─────────────────────────────────────

  /**
   * Transition: upcoming → active
   * Called when the user starts/joins a meeting.
   */
  async startMeeting(meetingId: string): Promise<Meeting | null> {
    const meeting = this.getMeeting(meetingId);
    if (!meeting) return null;
    if (meeting.status !== 'upcoming') {
      console.warn(`[MeetingIntel] Cannot start meeting "${meeting.name}" — status is ${meeting.status}`);
      return meeting;
    }

    meeting.status = 'active';
    meeting.startedAt = Date.now();
    this.activeMeetingId = meetingId;

    // Generate pre-meeting briefing if we have calendar data
    if (meeting.calendarEventId || meeting.attendees.length > 0) {
      try {
        const briefing = await meetingPrep.prepareBriefing({
          id: meeting.calendarEventId || meeting.id,
          summary: meeting.name,
          description: meeting.description,
          location: '',
          start: meeting.scheduledStart || new Date().toISOString(),
          end: meeting.scheduledEnd || new Date(Date.now() + 3600000).toISOString(),
          attendees: meeting.attendees,
          organizer: '',
          hangoutLink: meeting.meetingUrl || '',
          status: 'confirmed',
          isAllDay: false,
        });
        if (briefing) {
          meeting.briefingText = briefing.briefingText;
        }
      } catch (err) {
        console.warn('[MeetingIntel] Briefing generation failed:', err);
      }
    }

    // Auto-add a start note
    meeting.notes.push({
      id: crypto.randomUUID().slice(0, 8),
      timestamp: Date.now(),
      author: 'auto',
      content: `Meeting started at ${new Date().toLocaleTimeString()}`,
      type: 'note',
    });

    await this.save();
    console.log(`[MeetingIntel] Meeting "${meeting.name}" is now ACTIVE`);
    return meeting;
  }

  /**
   * Transition: active → processing → completed
   * Called when the meeting ends.
   */
  async endMeeting(meetingId: string, opts?: {
    transcript?: string;
    recording?: string;
  }): Promise<Meeting | null> {
    const meeting = this.getMeeting(meetingId);
    if (!meeting) return null;
    if (meeting.status !== 'active') {
      console.warn(`[MeetingIntel] Cannot end meeting "${meeting.name}" — status is ${meeting.status}`);
      return meeting;
    }

    meeting.status = 'processing';
    meeting.endedAt = Date.now();

    if (opts?.transcript) {
      meeting.transcript = opts.transcript;
    }
    if (opts?.recording) {
      meeting.recording = opts.recording;
    }

    // Auto-add an end note
    const durationMins = meeting.startedAt
      ? Math.round((Date.now() - meeting.startedAt) / 60000)
      : 0;
    meeting.notes.push({
      id: crypto.randomUUID().slice(0, 8),
      timestamp: Date.now(),
      author: 'auto',
      content: `Meeting ended after ${durationMins} minutes`,
      type: 'note',
    });

    // Clear active meeting
    if (this.activeMeetingId === meetingId) {
      this.activeMeetingId = null;
    }

    await this.save();
    console.log(`[MeetingIntel] Meeting "${meeting.name}" moved to PROCESSING (${durationMins}m)`);

    // Run post-meeting processing asynchronously
    this.postMeetingProcessing(meeting).catch((err) => {
      console.warn('[MeetingIntel] Post-meeting processing error:', err);
    });

    return meeting;
  }

  /**
   * Transition: upcoming → cancelled
   */
  cancelMeeting(meetingId: string): Meeting | null {
    const meeting = this.getMeeting(meetingId);
    if (!meeting) return null;
    if (meeting.status !== 'upcoming') {
      console.warn(`[MeetingIntel] Cannot cancel meeting "${meeting.name}" — status is ${meeting.status}`);
      return meeting;
    }

    meeting.status = 'cancelled';
    if (this.activeMeetingId === meetingId) {
      this.activeMeetingId = null;
    }

    this.save();
    console.log(`[MeetingIntel] Meeting "${meeting.name}" CANCELLED`);
    return meeting;
  }

  // ── Notes & Live Capture ────────────────────────────────────────

  /**
   * Add a note to a meeting (typically the active one).
   */
  addNote(meetingId: string, note: {
    content: string;
    type?: MeetingNote['type'];
    author?: MeetingNote['author'];
  }): MeetingNote | null {
    const meeting = this.getMeeting(meetingId);
    if (!meeting) return null;

    const entry: MeetingNote = {
      id: crypto.randomUUID().slice(0, 8),
      timestamp: Date.now(),
      author: note.author || 'agent',
      content: note.content,
      type: note.type || 'note',
    };

    meeting.notes.push(entry);
    this.save();
    return entry;
  }

  /**
   * Add a note to the currently active meeting (convenience for Gemini tools).
   */
  addNoteToActive(content: string, type?: MeetingNote['type']): MeetingNote | null {
    if (!this.activeMeetingId) return null;
    return this.addNote(this.activeMeetingId, { content, type, author: 'agent' });
  }

  // ── Post-Meeting Processing ─────────────────────────────────────

  /**
   * Runs after a meeting ends: summarize, extract action items, store in memory.
   */
  private async postMeetingProcessing(meeting: Meeting): Promise<void> {
    const hasContent =
      meeting.transcript ||
      meeting.notes.length > 1 || // More than just the auto start/end notes
      meeting.briefingText;

    if (!hasContent) {
      // Nothing to process — just mark as completed
      meeting.status = 'completed';
      await this.save();
      return;
    }

    // Build content block for summarization
    const contentParts: string[] = [];

    contentParts.push(`Meeting: ${meeting.name}`);
    if (meeting.description) {
      contentParts.push(`Description: ${meeting.description}`);
    }
    contentParts.push(`Attendees: ${meeting.attendees.join(', ') || 'unknown'}`);

    if (meeting.startedAt && meeting.endedAt) {
      const durationMins = Math.round((meeting.endedAt - meeting.startedAt) / 60000);
      contentParts.push(`Duration: ${durationMins} minutes`);
    }

    // Include notes
    const humanNotes = meeting.notes.filter((n) => n.author !== 'auto');
    if (humanNotes.length > 0) {
      contentParts.push('\n--- Notes ---');
      for (const note of humanNotes) {
        const prefix = note.type !== 'note' ? `[${note.type.toUpperCase()}] ` : '';
        contentParts.push(`${prefix}${note.content}`);
      }
    }

    // Include transcript snippet (first 3000 chars to avoid token blow-up)
    if (meeting.transcript) {
      const snippet = meeting.transcript.slice(0, 3000);
      contentParts.push(`\n--- Transcript (partial) ---\n${snippet}`);
    }

    const contentBlock = contentParts.join('\n');

    // Summarize using Claude/OpenRouter via memory extraction pipeline
    try {
      if (this.config.autoSummarize) {
        const summary = await this.generateSummary(contentBlock);
        if (summary) {
          meeting.summary = summary.text;
          meeting.actionItems = summary.actionItems;
          meeting.keyDecisions = summary.decisions;
        }
      }
    } catch (err) {
      console.warn('[MeetingIntel] Summary generation failed:', err);
    }

    // Store key facts from meeting in long-term memory
    try {
      if (meeting.summary) {
        const fact = `Meeting "${meeting.name}" on ${new Date(meeting.startedAt || meeting.createdAt).toLocaleDateString()}: ${meeting.summary.slice(0, 200)}`;
        await memoryManager.addImmediateMemory(fact, 'professional');
      }
      if (meeting.actionItems && meeting.actionItems.length > 0) {
        const actionFact = `Action items from "${meeting.name}": ${meeting.actionItems.slice(0, 5).join('; ')}`;
        await memoryManager.addImmediateMemory(actionFact, 'professional');
      }
    } catch {
      // Memory storage non-critical
    }

    // Log interactions with attendees in Trust Graph
    try {
      for (const attendee of meeting.attendees.slice(0, 10)) {
        const name = attendee.split('@')[0].replace(/[._]/g, ' ');
        const resolution = trustGraph.resolvePerson(name);
        if (resolution.person) {
          trustGraph.logCommunication(resolution.person.id, {
            channel: 'meeting',
            direction: 'bidirectional' as const,
            summary: `Meeting: ${meeting.name}`,
            sentiment: 0.1, // Neutral-positive default for meeting attendance
          });
        }
      }
    } catch {
      // Trust Graph integration non-critical
    }

    meeting.status = 'completed';
    await this.save();
    console.log(`[MeetingIntel] Post-processing complete for "${meeting.name}"`);
  }

  /**
   * Generate meeting summary using Claude/OpenRouter via fetch to our local API.
   */
  private async generateSummary(content: string): Promise<{
    text: string;
    actionItems: string[];
    decisions: string[];
  } | null> {
    try {
      // Use the local Anthropic/OpenRouter API via the existing connector system
      const { settingsManager } = await import('./settings');
      const settings = settingsManager.get();

      // Determine which API to use
      let apiKey = '';
      let apiUrl = '';
      let model = '';

      if (settings.openrouterApiKey) {
        apiKey = settings.openrouterApiKey;
        apiUrl = 'https://openrouter.ai/api/v1/chat/completions';
        model = settings.openrouterModel || 'anthropic/claude-sonnet-4';
      } else if (settings.anthropicApiKey) {
        apiKey = settings.anthropicApiKey;
        apiUrl = 'https://api.anthropic.com/v1/messages';
        model = 'claude-sonnet-4-20250514';
      }

      if (!apiKey) {
        console.log('[MeetingIntel] No API key available for summary generation');
        return null;
      }

      const summaryPrompt = `Summarize this meeting concisely. Extract action items and key decisions.

${content}

Respond in this exact JSON format:
{
  "summary": "2-4 sentence meeting summary",
  "actionItems": ["action 1", "action 2"],
  "decisions": ["decision 1", "decision 2"]
}`;

      if (apiUrl.includes('openrouter')) {
        const resp = await fetch(apiUrl, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${apiKey}`,
            'HTTP-Referer': 'https://agentfriday.ai',
            'X-Title': 'Agent Friday',
          },
          body: JSON.stringify({
            model,
            messages: [{ role: 'user', content: summaryPrompt }],
            max_tokens: 800,
            temperature: 0.3,
          }),
        });
        const data = await resp.json();
        const text = data.choices?.[0]?.message?.content || '';
        return this.parseSummaryResponse(text);
      } else {
        const resp = await fetch(apiUrl, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'x-api-key': apiKey,
            'anthropic-version': '2023-06-01',
          },
          body: JSON.stringify({
            model,
            max_tokens: 800,
            temperature: 0.3,
            messages: [{ role: 'user', content: summaryPrompt }],
          }),
        });
        const data = await resp.json();
        const text = data.content?.[0]?.text || '';
        return this.parseSummaryResponse(text);
      }
    } catch (err) {
      console.warn('[MeetingIntel] Summary API call failed:', err);
      return null;
    }
  }

  private parseSummaryResponse(text: string): {
    text: string;
    actionItems: string[];
    decisions: string[];
  } | null {
    try {
      // Try to extract JSON from response
      const jsonMatch = text.match(/\{[\s\S]*\}/);
      if (jsonMatch) {
        const parsed = JSON.parse(jsonMatch[0]);
        return {
          text: parsed.summary || parsed.text || text.slice(0, 500),
          actionItems: parsed.actionItems || parsed.action_items || [],
          decisions: parsed.decisions || parsed.key_decisions || [],
        };
      }
      // Fallback — use the raw text as summary
      return { text: text.slice(0, 500), actionItems: [], decisions: [] };
    } catch {
      return { text: text.slice(0, 500), actionItems: [], decisions: [] };
    }
  }

  // ── Attendee Intelligence ───────────────────────────────────────

  private buildAttendeeIntel(attendee: string): AttendeeIntel {
    const name = attendee.includes('@')
      ? attendee.split('@')[0].replace(/[._]/g, ' ')
      : attendee;
    const isEmail = attendee.includes('@');

    const intel: AttendeeIntel = { name, email: isEmail ? attendee : undefined };

    try {
      const resolution = trustGraph.resolvePerson(name);
      if (resolution.person) {
        intel.name = resolution.person.primaryName;
        intel.trustScore = resolution.person.trust.overall;
        intel.domains = resolution.person.domains;
        intel.trustProfile = trustGraph.getContextForPerson(resolution.person.id);
      }
    } catch {
      // Trust Graph not available
    }

    // Pull memories about this person
    try {
      const allMem = memoryManager.getLongTerm();
      const lowerName = name.toLowerCase();
      const related = allMem.filter(
        (m: any) => m.fact.toLowerCase().includes(lowerName)
      );
      intel.memories = related.slice(0, 3).map((m: any) => m.fact);
    } catch {
      // Memory not available
    }

    return intel;
  }

  /**
   * Refresh attendee intelligence for a meeting (e.g., when Trust Graph updates).
   */
  refreshAttendeeIntel(meetingId: string): Meeting | null {
    const meeting = this.getMeeting(meetingId);
    if (!meeting) return null;

    meeting.attendeeIntel = meeting.attendees.map((a) => this.buildAttendeeIntel(a));
    this.save();
    return meeting;
  }

  // ── Platform Detection ──────────────────────────────────────────

  private detectPlatform(url?: string): Meeting['platform'] {
    if (!url) return undefined;
    const lower = url.toLowerCase();
    if (lower.includes('meet.google.com')) return 'google-meet';
    if (lower.includes('zoom.us') || lower.includes('zoom.com')) return 'zoom';
    if (lower.includes('teams.microsoft.com') || lower.includes('teams.live.com')) return 'teams';
    return 'other';
  }

  // ── Calendar Sync ───────────────────────────────────────────────

  /**
   * Create a meeting from a calendar event (bridge with meeting-prep.ts).
   */
  createFromCalendarEvent(event: {
    id: string;
    summary: string;
    description: string;
    start: string;
    end: string;
    attendees: string[];
    hangoutLink?: string;
  }): Meeting {
    // Check if we already have this meeting
    const existing = this.meetings.find((m) => m.calendarEventId === event.id);
    if (existing) return existing;

    return this.createMeeting({
      name: event.summary,
      description: event.description,
      scheduledStart: event.start,
      scheduledEnd: event.end,
      attendees: event.attendees,
      meetingUrl: event.hangoutLink,
      calendarEventId: event.id,
    });
  }

  // ── Context String for System Prompt ────────────────────────────

  /**
   * Returns context for system prompt injection — active meeting info.
   */
  getContextString(): string {
    const active = this.getActiveMeeting();
    if (!active) return '';

    const parts: string[] = ['## Active Meeting Intelligence'];
    parts.push(`Meeting: ${active.name}`);

    if (active.startedAt) {
      const minsActive = Math.round((Date.now() - active.startedAt) / 60000);
      parts.push(`Duration: ${minsActive} minutes`);
    }

    if (active.attendees.length > 0) {
      parts.push(`Attendees: ${active.attendees.slice(0, 5).join(', ')}`);
    }

    if (active.agent) {
      parts.push(`Meeting Agent: ${active.agent.name}`);
      parts.push(`Instructions: ${active.agent.instructions}`);
    }

    // Include any notes
    const recentNotes = active.notes
      .filter((n) => n.author !== 'auto')
      .slice(-5);
    if (recentNotes.length > 0) {
      parts.push('\nRecent notes:');
      for (const note of recentNotes) {
        const prefix = note.type !== 'note' ? `[${note.type}] ` : '';
        parts.push(`  - ${prefix}${note.content}`);
      }
    }

    // Include attendee intel
    const topIntel = active.attendeeIntel.filter((a) => a.trustProfile).slice(0, 3);
    if (topIntel.length > 0) {
      parts.push('\nAttendee intel:');
      for (const intel of topIntel) {
        parts.push(`  ${intel.name}: trust ${((intel.trustScore || 0.5) * 100).toFixed(0)}%`);
        if (intel.domains && intel.domains.length > 0) {
          parts.push(`    Expertise: ${intel.domains.join(', ')}`);
        }
      }
    }

    if (active.briefingText) {
      parts.push(`\nBriefing: ${active.briefingText.slice(0, 300)}`);
    }

    return parts.join('\n');
  }

  // ── Stats & Search ──────────────────────────────────────────────

  getStats(): {
    total: number;
    upcoming: number;
    active: number;
    completed: number;
    cancelled: number;
    totalNotes: number;
    avgDurationMins: number;
  } {
    const completed = this.meetings.filter((m) => m.status === 'completed');
    const durations = completed
      .filter((m) => m.startedAt && m.endedAt)
      .map((m) => (m.endedAt! - m.startedAt!) / 60000);
    const avgDuration = durations.length > 0
      ? Math.round(durations.reduce((a, b) => a + b, 0) / durations.length)
      : 0;

    return {
      total: this.meetings.length,
      upcoming: this.meetings.filter((m) => m.status === 'upcoming').length,
      active: this.meetings.filter((m) => m.status === 'active').length,
      completed: completed.length,
      cancelled: this.meetings.filter((m) => m.status === 'cancelled').length,
      totalNotes: this.meetings.reduce((sum, m) => sum + m.notes.length, 0),
      avgDurationMins: avgDuration,
    };
  }

  /**
   * Search meeting history by query text.
   */
  searchMeetings(query: string, limit = 10): Meeting[] {
    const q = query.toLowerCase();
    return this.meetings
      .filter((m) => {
        const searchable = [
          m.name,
          m.description,
          m.summary || '',
          m.transcript || '',
          ...m.tags,
          ...m.attendees,
          ...m.notes.map((n) => n.content),
          ...(m.actionItems || []),
          ...(m.keyDecisions || []),
        ].join(' ').toLowerCase();
        return searchable.includes(q);
      })
      .slice(0, limit);
  }

  /**
   * Get recent meetings with summaries (for Gemini tool response).
   */
  getRecentSummaries(count = 5): Array<{
    id: string;
    name: string;
    date: string;
    status: MeetingStatus;
    summary: string;
    actionItems: string[];
    attendeeCount: number;
  }> {
    return this.meetings
      .filter((m) => m.status === 'completed' || m.status === 'active')
      .slice(0, count)
      .map((m) => ({
        id: m.id,
        name: m.name,
        date: new Date(m.startedAt || m.createdAt).toLocaleDateString(),
        status: m.status,
        summary: m.summary || 'No summary available',
        actionItems: m.actionItems || [],
        attendeeCount: m.attendees.length,
      }));
  }

  // ── Quick start from call mode ──────────────────────────────────

  /**
   * Quick-start a meeting from call integration (when Gemini join_meeting fires).
   */
  quickStartFromCall(meetingUrl: string, name?: string): Meeting {
    // Check if there's already an upcoming meeting with this URL
    const existing = this.meetings.find(
      (m) => m.meetingUrl === meetingUrl && m.status === 'upcoming'
    );
    if (existing) {
      this.startMeeting(existing.id);
      return existing;
    }

    // Create and immediately start
    const meeting = this.createMeeting({
      name: name || `Meeting at ${new Date().toLocaleTimeString()}`,
      meetingUrl,
      platform: this.detectPlatform(meetingUrl),
    });
    this.startMeeting(meeting.id);
    return meeting;
  }

  /**
   * End the active meeting (convenience for when call mode exits).
   */
  async endActiveMeeting(transcript?: string): Promise<Meeting | null> {
    if (!this.activeMeetingId) return null;
    return this.endMeeting(this.activeMeetingId, { transcript });
  }

  // ── Update operations ───────────────────────────────────────────

  updateMeeting(meetingId: string, updates: Partial<Pick<
    Meeting, 'name' | 'description' | 'tags' | 'agent' | 'projectName'
  >>): Meeting | null {
    const meeting = this.getMeeting(meetingId);
    if (!meeting) return null;

    if (updates.name !== undefined) meeting.name = updates.name;
    if (updates.description !== undefined) meeting.description = updates.description;
    if (updates.tags !== undefined) meeting.tags = updates.tags;
    if (updates.agent !== undefined) meeting.agent = updates.agent;
    if (updates.projectName !== undefined) meeting.projectName = updates.projectName;

    this.save();
    return meeting;
  }

  /**
   * Set transcript text for a meeting (from Whisper or manual paste).
   */
  setTranscript(meetingId: string, transcript: string): Meeting | null {
    const meeting = this.getMeeting(meetingId);
    if (!meeting) return null;
    meeting.transcript = transcript;
    this.save();
    return meeting;
  }

  /**
   * Set summary manually (override auto-generated).
   */
  setSummary(meetingId: string, summary: string): Meeting | null {
    const meeting = this.getMeeting(meetingId);
    if (!meeting) return null;
    meeting.summary = summary;
    this.save();
    return meeting;
  }

  // ── Maintenance ─────────────────────────────────────────────────

  private pruneOldMeetings(): void {
    const cutoff = Date.now() - this.config.retentionDays * 24 * 60 * 60 * 1000;
    const before = this.meetings.length;
    this.meetings = this.meetings.filter((m) => {
      if (m.status === 'upcoming' || m.status === 'active') return true;
      return m.createdAt > cutoff;
    });
    if (this.meetings.length < before) {
      console.log(`[MeetingIntel] Pruned ${before - this.meetings.length} old meetings`);
    }
  }

  // ── Persistence ─────────────────────────────────────────────────

  private async save(): Promise<void> {
    this.saveQueue = this.saveQueue.then(async () => {
      try {
        const data = JSON.stringify({ meetings: this.meetings, config: this.config }, null, 2);
        await fs.writeFile(this.filePath, data, 'utf-8');
      } catch (err) {
        console.warn('[MeetingIntel] Save failed:', err);
      }
    });
    await this.saveQueue;
  }

  stop(): void {
    // Cleanup — end any active meeting gracefully
    if (this.activeMeetingId) {
      const meeting = this.getMeeting(this.activeMeetingId);
      if (meeting && meeting.status === 'active') {
        meeting.endedAt = Date.now();
        meeting.status = 'completed';
        meeting.notes.push({
          id: crypto.randomUUID().slice(0, 8),
          timestamp: Date.now(),
          author: 'auto',
          content: 'Meeting ended (app closing)',
          type: 'note',
        });
      }
      this.activeMeetingId = null;
    }
    // Final sync save
    const data = JSON.stringify({ meetings: this.meetings, config: this.config }, null, 2);
    const fsSync = require('fs');
    try {
      fsSync.writeFileSync(this.filePath, data, 'utf-8');
    } catch {
      // Best effort on shutdown
    }
  }
}

export const meetingIntelligence = new MeetingIntelligence();
