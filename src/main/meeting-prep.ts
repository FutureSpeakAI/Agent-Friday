/**
 * 7B — Meeting Preparation Engine
 * Watches the calendar, generates pre-meeting briefings with:
 *  - Attendee context from memory
 *  - Recent conversation topics with each person
 *  - Relevant documents and project context
 *  - Suggested talking points
 */

import { BrowserWindow } from 'electron';
import { calendarIntegration } from './calendar';
import { memoryManager } from './memory';
import { episodicMemory } from './episodic-memory';
import { projectAwareness } from './project-awareness';
import { trustGraph } from './trust-graph';

interface CalendarEvent {
  id: string;
  summary: string;
  description: string;
  location: string;
  start: string;
  end: string;
  attendees: string[];
  organizer: string;
  hangoutLink: string;
  status: string;
  isAllDay: boolean;
}

interface MeetingBriefing {
  eventId: string;
  eventTitle: string;
  startTime: string;
  minutesUntil: number;
  attendeeContext: Array<{
    name: string;
    memories: string[];
    recentTopics: string[];
    trustProfile: string;
  }>;
  relevantProjects: string[];
  suggestedTopics: string[];
  briefingText: string;
}

class MeetingPrep {
  private briefedEvents = new Set<string>(); // Track which events we've already briefed
  private checkInterval: ReturnType<typeof setInterval> | null = null;
  private mainWindow: BrowserWindow | null = null;

  init(mainWindow: BrowserWindow): void {
    this.mainWindow = mainWindow;

    // Check for upcoming meetings every 2 minutes
    this.checkInterval = setInterval(() => {
      this.checkForUpcomingMeetings().catch((err) =>
        console.warn('[MeetingPrep] Check error:', err.message)
      );
    }, 2 * 60 * 1000);

    // Initial check after short delay
    setTimeout(() => {
      this.checkForUpcomingMeetings().catch(() => {});
    }, 10_000);
  }

  /**
   * Check if any meetings are approaching and need briefings
   */
  private async checkForUpcomingMeetings(): Promise<void> {
    if (!calendarIntegration.isAuthenticated()) return;

    const events = calendarIntegration.getUpcoming(5);
    const now = Date.now();

    for (const event of events) {
      if (event.isAllDay) continue;
      if (this.briefedEvents.has(event.id)) continue;

      const startMs = new Date(event.start).getTime();
      const minsUntil = (startMs - now) / 60000;

      // Prepare briefing 15 minutes before meeting
      if (minsUntil > 0 && minsUntil <= 15) {
        this.briefedEvents.add(event.id);
        const briefing = await this.prepareBriefing(event);
        if (briefing && this.mainWindow) {
          this.mainWindow.webContents.send('meeting:briefing', briefing);
        }
      }
    }

    // Clean up old briefed events (more than 2 hours old)
    for (const eventId of this.briefedEvents) {
      const event = events.find((e) => e.id === eventId);
      if (!event) continue;
      const endMs = new Date(event.end).getTime();
      if (now - endMs > 2 * 60 * 60 * 1000) {
        this.briefedEvents.delete(eventId);
      }
    }
  }

  /**
   * Generate a meeting briefing for a specific event
   */
  async prepareBriefing(event: CalendarEvent): Promise<MeetingBriefing | null> {
    try {
      const now = Date.now();
      const startMs = new Date(event.start).getTime();
      const minutesUntil = Math.round((startMs - now) / 60000);

      // Gather attendee context from memory
      const attendeeContext: MeetingBriefing['attendeeContext'] = [];

      for (const attendee of event.attendees.slice(0, 8)) {
        const name = attendee.split('@')[0].replace(/[._]/g, ' ');

        // Resolve person via Trust Graph for structured context
        let trustProfile = '';
        let resolvedName = name;
        try {
          const resolution = trustGraph.resolvePerson(name);
          if (resolution.person) {
            resolvedName = resolution.person.primaryName;
            trustProfile = trustGraph.getContextForPerson(resolution.person.id);
          }
        } catch {
          // Trust Graph not ready — fall through to memory-based matching
        }

        // Search memories for this person
        const memories: string[] = [];
        try {
          const allMem = memoryManager.getLongTerm();
          const lowerName = resolvedName.toLowerCase();
          const related = allMem.filter(
            (m: any) =>
              m.fact.toLowerCase().includes(lowerName) ||
              m.fact.toLowerCase().includes(name.toLowerCase()) ||
              m.category === 'relationship'
          );
          memories.push(...related.slice(0, 3).map((m: any) => m.fact));
        } catch {
          // skip
        }

        // Search episodes for recent conversations mentioning them
        const recentTopics: string[] = [];
        try {
          const episodes = await episodicMemory.search(resolvedName, 3);
          recentTopics.push(
            ...episodes.map((ep: any) => ep.summary).slice(0, 2)
          );
        } catch {
          // skip
        }

        if (memories.length > 0 || recentTopics.length > 0 || trustProfile) {
          attendeeContext.push({ name: resolvedName, memories, recentTopics, trustProfile });
        }
      }

      // Check for relevant projects
      const relevantProjects: string[] = [];
      try {
        const projects = projectAwareness.getProjects();
        // Simple heuristic: if the meeting title mentions a project name
        for (const proj of projects) {
          if (
            event.summary.toLowerCase().includes(proj.name.toLowerCase()) ||
            event.description.toLowerCase().includes(proj.name.toLowerCase())
          ) {
            relevantProjects.push(`${proj.name} (${proj.type})`);
          }
        }
      } catch {
        // skip
      }

      // Generate suggested topics based on context
      const suggestedTopics: string[] = [];
      if (event.description) {
        // Extract bullet points or key phrases from description
        const lines = event.description.split(/[\n\r]+/).filter((l) => l.trim());
        suggestedTopics.push(...lines.slice(0, 5));
      }

      // Build briefing text
      const briefingParts: string[] = [];
      briefingParts.push(`Meeting: ${event.summary}`);
      briefingParts.push(`Starting in ${minutesUntil} minutes`);

      if (event.attendees.length > 0) {
        briefingParts.push(`Attendees: ${event.attendees.join(', ')}`);
      }

      if (event.hangoutLink) {
        briefingParts.push(`Video link available`);
      }

      if (event.location) {
        briefingParts.push(`Location: ${event.location}`);
      }

      if (attendeeContext.length > 0) {
        briefingParts.push('\nAttendee context:');
        for (const ac of attendeeContext) {
          briefingParts.push(`  ${ac.name}:`);
          if (ac.trustProfile) {
            briefingParts.push(`    Trust: ${ac.trustProfile}`);
          }
          for (const m of ac.memories) {
            briefingParts.push(`    - ${m}`);
          }
          if (ac.recentTopics.length > 0) {
            briefingParts.push(`    Recent: ${ac.recentTopics.join('; ')}`);
          }
        }
      }

      if (relevantProjects.length > 0) {
        briefingParts.push(`\nRelated projects: ${relevantProjects.join(', ')}`);
      }

      const briefing: MeetingBriefing = {
        eventId: event.id,
        eventTitle: event.summary,
        startTime: event.start,
        minutesUntil,
        attendeeContext,
        relevantProjects,
        suggestedTopics,
        briefingText: briefingParts.join('\n'),
      };

      console.log(`[MeetingPrep] Briefing prepared for "${event.summary}" in ${minutesUntil}m`);
      return briefing;
    } catch (err) {
      console.warn('[MeetingPrep] Briefing error:', err);
      return null;
    }
  }

  /**
   * Context string for system prompt — shows next meeting context
   */
  getContextString(): string {
    if (!calendarIntegration.isAuthenticated()) return '';

    const next = calendarIntegration.getUpcoming(1);
    if (next.length === 0) return '';

    const event = next[0];
    const minsUntil = Math.round(
      (new Date(event.start).getTime() - Date.now()) / 60000
    );

    if (minsUntil > 60) return ''; // Only show if within the hour

    const lines = [`## Next Meeting (in ${minsUntil}m)`];
    lines.push(`Title: ${event.summary}`);
    if (event.attendees.length > 0) {
      lines.push(`Attendees: ${event.attendees.slice(0, 5).join(', ')}`);
    }
    if (event.hangoutLink) lines.push('Video link available');

    return lines.join('\n');
  }

  stop(): void {
    if (this.checkInterval) {
      clearInterval(this.checkInterval);
      this.checkInterval = null;
    }
  }
}

export const meetingPrep = new MeetingPrep();
