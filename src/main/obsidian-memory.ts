/**
 * obsidian-memory.ts — Markdown file I/O for Obsidian vault integration.
 *
 * Reads and writes EVE memory entries as Markdown files with YAML frontmatter.
 * Folder structure inside the vault:
 *   EVE/
 *     memories/        — long-term confirmed facts
 *     observations/    — medium-term patterns with confidence
 *
 * Each file has YAML frontmatter for metadata and a body with the content.
 * Obsidian's graph view connects notes via tags and wikilinks.
 */

import fs from 'fs/promises';
import path from 'path';
import type { LongTermEntry, MediumTermEntry } from './memory';

// ── Folder names inside the vault ──────────────────────────────────────────

const EVE_ROOT = 'EVE';
const MEMORIES_DIR = 'memories';
const OBSERVATIONS_DIR = 'observations';

// ── Helpers ────────────────────────────────────────────────────────────────

/** Convert a string to a safe, readable filename (max 60 chars). */
function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, '')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 60);
}

/** Build a simple YAML frontmatter block from a flat object. */
function buildFrontmatter(data: Record<string, unknown>): string {
  const lines = ['---'];
  for (const [key, value] of Object.entries(data)) {
    if (Array.isArray(value)) {
      lines.push(`${key}: [${value.map((v) => String(v)).join(', ')}]`);
    } else if (typeof value === 'string') {
      // Quote strings that contain special chars
      const needsQuotes = /[:#\[\]{}|>*&!%@`]/.test(String(value));
      lines.push(`${key}: ${needsQuotes ? `"${String(value).replace(/"/g, '\\"')}"` : value}`);
    } else {
      lines.push(`${key}: ${String(value)}`);
    }
  }
  lines.push('---');
  return lines.join('\n');
}

/** Parse YAML frontmatter from a Markdown string. Returns { meta, body }. */
function parseFrontmatter(content: string): { meta: Record<string, string>; body: string } {
  const meta: Record<string, string> = {};
  const match = content.match(/^---\n([\s\S]*?)\n---\n?([\s\S]*)$/);
  if (!match) return { meta, body: content };

  const yamlBlock = match[1];
  const body = match[2].trim();

  for (const line of yamlBlock.split('\n')) {
    const colonIdx = line.indexOf(':');
    if (colonIdx === -1) continue;
    const key = line.slice(0, colonIdx).trim();
    let value = line.slice(colonIdx + 1).trim();
    // Strip surrounding quotes
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    meta[key] = value;
  }

  return { meta, body };
}

/** Format a timestamp to ISO date string. */
function toISO(ts: number): string {
  return new Date(ts).toISOString();
}

/** Parse an ISO date string to timestamp. Falls back to 0. */
function fromISO(iso: string): number {
  const t = Date.parse(iso);
  return isNaN(t) ? 0 : t;
}

// ── Public API ─────────────────────────────────────────────────────────────

/** Create the EVE folder structure inside the vault if it doesn't exist. */
export async function ensureVaultStructure(vaultPath: string): Promise<void> {
  await fs.mkdir(path.join(vaultPath, EVE_ROOT, MEMORIES_DIR), { recursive: true });
  await fs.mkdir(path.join(vaultPath, EVE_ROOT, OBSERVATIONS_DIR), { recursive: true });
}

/** Write a long-term memory entry as a Markdown note. */
export async function writeLongTermNote(vaultPath: string, entry: LongTermEntry): Promise<void> {
  const dir = path.join(vaultPath, EVE_ROOT, MEMORIES_DIR);
  const slug = slugify(entry.fact);
  const filename = `${slug}-${entry.id.slice(0, 8)}.md`;

  const frontmatter = buildFrontmatter({
    id: entry.id,
    category: entry.category,
    confirmed: entry.confirmed,
    source: entry.source,
    created: toISO(entry.createdAt),
    tags: ['eve-memory', entry.category],
  });

  const body = `# ${entry.fact}\n`;
  const content = `${frontmatter}\n${body}`;

  await fs.writeFile(path.join(dir, filename), content, 'utf-8');
}

/** Write a medium-term observation entry as a Markdown note. */
export async function writeMediumTermNote(vaultPath: string, entry: MediumTermEntry): Promise<void> {
  const dir = path.join(vaultPath, EVE_ROOT, OBSERVATIONS_DIR);
  const slug = slugify(entry.observation);
  const filename = `${slug}-${entry.id.slice(0, 8)}.md`;

  const frontmatter = buildFrontmatter({
    id: entry.id,
    category: entry.category,
    confidence: entry.confidence,
    occurrences: entry.occurrences,
    first_observed: toISO(entry.firstObserved),
    last_reinforced: toISO(entry.lastReinforced),
    tags: ['eve-observation', entry.category],
  });

  const body = `# ${entry.observation}\n`;
  const content = `${frontmatter}\n${body}`;

  await fs.writeFile(path.join(dir, filename), content, 'utf-8');
}

/** Read all long-term memory notes from the vault. */
export async function readLongTermNotes(vaultPath: string): Promise<LongTermEntry[]> {
  const dir = path.join(vaultPath, EVE_ROOT, MEMORIES_DIR);
  const entries: LongTermEntry[] = [];

  try {
    const files = await fs.readdir(dir);
    for (const file of files) {
      if (!file.endsWith('.md')) continue;
      try {
        const raw = await fs.readFile(path.join(dir, file), 'utf-8');
        const { meta, body } = parseFrontmatter(raw);

        // Extract the fact from the first heading or body
        const headingMatch = body.match(/^#\s+(.+)/m);
        const fact = headingMatch ? headingMatch[1].trim() : body.split('\n')[0].trim();

        if (!meta.id || !fact) continue;

        entries.push({
          id: meta.id,
          fact,
          category: (meta.category as LongTermEntry['category']) || 'identity',
          confirmed: meta.confirmed === 'true',
          createdAt: fromISO(meta.created || ''),
          source: (meta.source as LongTermEntry['source']) || 'extracted',
        });
      } catch {
        // Skip unreadable files
      }
    }
  } catch {
    // Directory doesn't exist yet
  }

  return entries;
}

/** Read all medium-term observation notes from the vault. */
export async function readMediumTermNotes(vaultPath: string): Promise<MediumTermEntry[]> {
  const dir = path.join(vaultPath, EVE_ROOT, OBSERVATIONS_DIR);
  const entries: MediumTermEntry[] = [];

  try {
    const files = await fs.readdir(dir);
    for (const file of files) {
      if (!file.endsWith('.md')) continue;
      try {
        const raw = await fs.readFile(path.join(dir, file), 'utf-8');
        const { meta, body } = parseFrontmatter(raw);

        const headingMatch = body.match(/^#\s+(.+)/m);
        const observation = headingMatch ? headingMatch[1].trim() : body.split('\n')[0].trim();

        if (!meta.id || !observation) continue;

        entries.push({
          id: meta.id,
          observation,
          category: (meta.category as MediumTermEntry['category']) || 'pattern',
          confidence: parseFloat(meta.confidence) || 0.5,
          firstObserved: fromISO(meta.first_observed || ''),
          lastReinforced: fromISO(meta.last_reinforced || ''),
          occurrences: parseInt(meta.occurrences, 10) || 1,
        });
      } catch {
        // Skip unreadable files
      }
    }
  } catch {
    // Directory doesn't exist yet
  }

  return entries;
}

/** Delete a note by ID from the given tier folder. */
export async function deleteNote(
  vaultPath: string,
  tier: 'memories' | 'observations',
  id: string
): Promise<boolean> {
  const dir = path.join(vaultPath, EVE_ROOT, tier);

  try {
    const files = await fs.readdir(dir);
    for (const file of files) {
      if (!file.endsWith('.md')) continue;
      // Quick check: ID is embedded in filename as last 8 chars before .md
      if (file.includes(id.slice(0, 8))) {
        // Verify by reading frontmatter
        const raw = await fs.readFile(path.join(dir, file), 'utf-8');
        const { meta } = parseFrontmatter(raw);
        if (meta.id === id) {
          await fs.unlink(path.join(dir, file));
          return true;
        }
      }
    }
  } catch {
    // Directory doesn't exist
  }

  return false;
}

/** Sync all long-term entries to vault (bulk write for migration). */
export async function syncLongTermToVault(vaultPath: string, entries: LongTermEntry[]): Promise<void> {
  await ensureVaultStructure(vaultPath);
  for (const entry of entries) {
    await writeLongTermNote(vaultPath, entry);
  }
}

/** Sync all medium-term entries to vault (bulk write for migration). */
export async function syncMediumTermToVault(vaultPath: string, entries: MediumTermEntry[]): Promise<void> {
  await ensureVaultStructure(vaultPath);
  for (const entry of entries) {
    await writeMediumTermNote(vaultPath, entry);
  }
}
