/**
 * 7C — Communications Intelligence
 * Drafts emails, messages, and professional communications in Friday's user's voice.
 * Learns writing style from memory and adapts tone per recipient/context.
 */

import { ipcMain, clipboard, shell } from 'electron';
import { memoryManager } from './memory';
import { llmClient } from './llm-client';
import crypto from 'crypto';

// Late-bound trust graph import to avoid circular dependencies
let _trustGraph: any = null;
function getTrustGraph() {
  if (!_trustGraph) {
    try { _trustGraph = require('./trust-graph').trustGraph; } catch { /* not ready yet */ }
  }
  return _trustGraph;
}

interface DraftRequest {
  type: 'email' | 'message' | 'reply' | 'follow-up';
  to: string;
  subject?: string;
  context: string;        // What the user wants to communicate
  tone?: 'formal' | 'casual' | 'friendly' | 'professional' | 'urgent';
  originalMessage?: string; // For replies
  maxLength?: 'short' | 'medium' | 'long';
}

interface Draft {
  id: string;
  type: string;
  subject: string;
  body: string;
  to: string;
  tone: string;
  createdAt: number;
}

interface WritingStyleProfile {
  formalGreeting: string;
  casualGreeting: string;
  signOff: string;
  typicalLength: string;
  personalNotes: string[];
}

class CommunicationsIntelligence {
  private drafts: Draft[] = [];
  private styleProfile: WritingStyleProfile = {
    formalGreeting: 'Dear',
    casualGreeting: 'Hi',
    signOff: 'Best regards',
    typicalLength: 'medium',
    personalNotes: [],
  };

  async init(): Promise<void> {
    // Load writing style preferences from memory
    await this.loadStyleFromMemory();
    console.log('[Communications] Ready — style profile loaded');
  }

  /**
   * Infer writing style from user's stored memories and preferences
   */
  private async loadStyleFromMemory(): Promise<void> {
    try {
      const memories = memoryManager.getLongTerm();

      // Look for communication-related preferences
      for (const m of memories) {
        const fact = m.fact.toLowerCase();
        if (fact.includes('sign off') || fact.includes('signature')) {
          this.styleProfile.signOff = m.fact.split(':').pop()?.trim() || this.styleProfile.signOff;
        }
        if (fact.includes('british') || fact.includes('uk')) {
          this.styleProfile.formalGreeting = 'Dear';
          this.styleProfile.casualGreeting = 'Hi';
        }
        if (m.category === 'professional' || m.category === 'preference') {
          this.styleProfile.personalNotes.push(m.fact);
        }
      }
    } catch {
      // defaults are fine
    }
  }

  /**
   * Generate a communication draft using Claude
   */
  async generateDraft(request: DraftRequest): Promise<Draft> {
    const tone = request.tone || 'professional';
    const maxLength = request.maxLength || 'medium';

    // Resolve recipient via Trust Graph for relationship-aware drafting
    let recipientContext = '';
    if (request.to) {
      try {
        const tg = getTrustGraph();
        if (tg) {
          const resolution = tg.resolvePerson(request.to);
          if (resolution.person) {
            recipientContext = `\nAbout the recipient (from relationship intelligence):\n${tg.getContextForPerson(resolution.person.id)}`;
          }
        }
      } catch {
        // Trust Graph not ready — proceed without recipient context
      }
    }

    // Build style context
    const styleContext = this.styleProfile.personalNotes.length > 0
      ? `\nKnown about the user's style/preferences:\n${this.styleProfile.personalNotes.slice(0, 5).map((n) => `- ${n}`).join('\n')}`
      : '';

    const lengthGuide = {
      short: '2-4 sentences, very concise',
      medium: '1-2 short paragraphs',
      long: '3-4 paragraphs with detail',
    }[maxLength];

    let prompt: string;

    if (request.type === 'reply' && request.originalMessage) {
      prompt = `Draft a ${tone} reply to this message.

Original message:
---
${request.originalMessage}
---

User's intent for the reply: ${request.context}

To: ${request.to}
Tone: ${tone}
Length: ${lengthGuide}
${styleContext}

Write ONLY the reply body — no subject line, no metadata. The reply should sound natural, human, and match the user's intent. Sign off with "${this.styleProfile.signOff}" if formal.${recipientContext}`;
    } else if (request.type === 'follow-up') {
      prompt = `Draft a ${tone} follow-up ${request.subject ? 'email' : 'message'}.

Context: ${request.context}
To: ${request.to}
${request.subject ? `Subject: ${request.subject}` : ''}
Tone: ${tone}
Length: ${lengthGuide}
${styleContext}

Write ONLY the message body. Be polite but purposeful. Reference previous interaction naturally.${recipientContext}`;
    } else {
      prompt = `Draft a ${tone} ${request.type}.

Purpose: ${request.context}
To: ${request.to}
${request.subject ? `Subject: ${request.subject}` : 'Generate an appropriate subject line on the first line, prefixed with "Subject: "'}
Tone: ${tone}
Length: ${lengthGuide}
${styleContext}

Write the ${request.type} body. Make it sound natural and human, not AI-generated. ${tone === 'formal' ? `Open with "${this.styleProfile.formalGreeting}" and sign off with "${this.styleProfile.signOff}".` : `Keep it conversational.`}${recipientContext}`;
    }

    try {
      const response = await llmClient.text(prompt, { systemPrompt: 'You are a professional communications assistant. Write clear, natural-sounding messages that match the requested tone. Never include meta-commentary about the draft.', maxTokens: 1024 });

      // Parse subject if generated
      let subject = request.subject || '';
      let body = response;

      if (!request.subject && response.startsWith('Subject:')) {
        const lines = response.split('\n');
        subject = lines[0].replace('Subject:', '').trim();
        body = lines.slice(1).join('\n').trim();
      }

      const draft: Draft = {
        id: crypto.randomUUID(),
        type: request.type,
        subject,
        body,
        to: request.to,
        tone,
        createdAt: Date.now(),
      };

      this.drafts.push(draft);

      // Keep only last 20 drafts
      if (this.drafts.length > 20) {
        this.drafts = this.drafts.slice(-20);
      }

      return draft;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      throw new Error(`Failed to generate draft: ${msg}`);
    }
  }

  /**
   * Copy a draft to clipboard for pasting
   */
  copyToClipboard(draftId: string): boolean {
    const draft = this.drafts.find((d) => d.id === draftId);
    if (!draft) return false;

    const fullText = draft.subject
      ? `Subject: ${draft.subject}\n\n${draft.body}`
      : draft.body;

    clipboard.writeText(fullText);
    return true;
  }

  /**
   * Open mailto: link with the draft pre-filled
   */
  openInEmail(draftId: string): boolean {
    const draft = this.drafts.find((d) => d.id === draftId);
    if (!draft) return false;

    const mailto = `mailto:${encodeURIComponent(draft.to)}?subject=${encodeURIComponent(draft.subject)}&body=${encodeURIComponent(draft.body)}`;
    shell.openExternal(mailto);
    return true;
  }

  /**
   * Refine an existing draft with additional instructions
   */
  async refineDraft(draftId: string, instruction: string): Promise<Draft | null> {
    const original = this.drafts.find((d) => d.id === draftId);
    if (!original) return null;

    const prompt = `Refine this ${original.type} draft based on the following instruction.

Current draft:
---
${original.body}
---

Instruction: ${instruction}

Write ONLY the refined version. Maintain the same overall intent and tone unless the instruction says otherwise.`;

    try {
      const refined = await llmClient.text(prompt, { systemPrompt: 'You are a professional communications assistant. Refine the draft precisely as instructed.', maxTokens: 1024 });

      const newDraft: Draft = {
        id: crypto.randomUUID(),
        type: original.type,
        subject: original.subject,
        body: refined,
        to: original.to,
        tone: original.tone,
        createdAt: Date.now(),
      };

      this.drafts.push(newDraft);
      return newDraft;
    } catch {
      return null;
    }
  }

  /**
   * Get all recent drafts
   */
  getRecentDrafts(): Draft[] {
    return [...this.drafts].reverse();
  }

  /**
   * Get context string for system prompt
   */
  getContextString(): string {
    if (this.drafts.length === 0) return '';

    const recent = this.drafts.slice(-2);
    const lines = ['## Recent Drafts'];
    for (const d of recent) {
      lines.push(`- ${d.type} to ${d.to}: "${d.subject || d.body.slice(0, 50)}..." (${d.tone})`);
    }
    return lines.join('\n');
  }

  stop(): void {
    // Nothing to clean up
  }
}

export const communications = new CommunicationsIntelligence();

// IPC handlers
export function registerCommunicationsHandlers(): void {
  ipcMain.handle('communications:draft', async (_event, request: DraftRequest) => {
    return communications.generateDraft(request);
  });

  ipcMain.handle('communications:refine', async (_event, draftId: string, instruction: string) => {
    return communications.refineDraft(draftId, instruction);
  });

  ipcMain.handle('communications:copy', (_event, draftId: string) => {
    return communications.copyToClipboard(draftId);
  });

  ipcMain.handle('communications:open-email', (_event, draftId: string) => {
    return communications.openInEmail(draftId);
  });

  ipcMain.handle('communications:list-drafts', () => {
    return communications.getRecentDrafts();
  });
}
