/**
 * Trust Graph IPC handlers — person resolution, trust scoring, evidence, communication logging.
 */
import { ipcMain } from 'electron';
import { trustGraph } from '../trust-graph';
import type { EvidenceType } from '../trust-graph';

export function registerTrustGraphHandlers(): void {
  // ── Person resolution ─────────────────────────────────────────────────
  ipcMain.handle('trust:lookup', (_event, name: string) => {
    return trustGraph.resolvePerson(name);
  });

  // ── Evidence ──────────────────────────────────────────────────────────
  ipcMain.handle(
    'trust:update-evidence',
    async (
      _event,
      personName: string,
      evidence: {
        type: EvidenceType;
        description: string;
        impact: number;
        domain?: string;
      },
    ) => {
      const { person } = trustGraph.resolvePerson(personName);
      if (!person) return { ok: false, error: 'Could not resolve person' };
      trustGraph.addEvidence(person.id, evidence);
      return { ok: true, personId: person.id };
    },
  );

  // ── Communication logging ─────────────────────────────────────────────
  ipcMain.handle(
    'trust:log-comm',
    async (
      _event,
      personName: string,
      event: {
        channel: string;
        direction: 'inbound' | 'outbound' | 'bidirectional';
        summary: string;
        sentiment: number;
      },
    ) => {
      const { person } = trustGraph.resolvePerson(personName);
      if (!person) return { ok: false, error: 'Could not resolve person' };
      trustGraph.logCommunication(person.id, event);
      return { ok: true, personId: person.id };
    },
  );

  // ── Alias management ──────────────────────────────────────────────────
  ipcMain.handle(
    'trust:add-alias',
    (_event, personId: string, alias: string, type: string) => {
      trustGraph.addAlias(
        personId,
        alias,
        type as 'name' | 'email' | 'handle' | 'phone' | 'nickname',
      );
      return { ok: true };
    },
  );

  // ── Queries ───────────────────────────────────────────────────────────
  ipcMain.handle('trust:get-all', () => {
    return trustGraph.getAllPersons();
  });

  ipcMain.handle('trust:get-context', (_event, personId: string) => {
    return trustGraph.getContextForPerson(personId);
  });

  ipcMain.handle('trust:get-prompt-context', () => {
    return trustGraph.getPromptContext();
  });

  ipcMain.handle('trust:find-by-domain', (_event, domain: string) => {
    return trustGraph.findByDomain(domain);
  });

  ipcMain.handle('trust:most-trusted', (_event, limit?: number) => {
    return trustGraph.getMostTrusted(limit);
  });

  ipcMain.handle('trust:recent', (_event, limit?: number) => {
    return trustGraph.getRecentInteractions(limit);
  });

  // ── Notes & relationships ─────────────────────────────────────────────
  ipcMain.handle(
    'trust:update-notes',
    (_event, personId: string, notes: string) => {
      trustGraph.updateNotes(personId, notes);
      return { ok: true };
    },
  );

  ipcMain.handle(
    'trust:link-persons',
    (_event, idA: string, idB: string, label: string) => {
      trustGraph.linkPersons(idA, idB, label);
      return { ok: true };
    },
  );
}
