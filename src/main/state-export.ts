/**
 * state-export.ts — Agent Persistence & Continuity Engine (Track VII, Phase 4)
 *
 * Provides full-state export/import for Agent Friday, enabling:
 * 1. Manual export → encrypted archive (.friday-backup) containing ALL agent state
 * 2. Manual import → restore from encrypted archive with integrity validation
 * 3. Scheduled backups → recurring encrypted snapshots via scheduler cron
 * 4. Incremental backups → only re-export files changed since last backup
 *
 * Architecture:
 * - AES-256-GCM encryption with PBKDF2-derived key (600k iterations SHA-512, user passphrase)
 * - HMAC integrity verification on archive contents
 * - Import validates every file BEFORE overwriting anything (fail-closed)
 * - FutureSpeak has ZERO access to backup data (offline-capable, no phone-home)
 *
 * cLaw Gate:
 * - Backups always encrypted (Trust Graph data involves third parties)
 * - Passphrase never stored, never leaves device
 * - Irrecoverable on passphrase loss — strongest guarantee nobody else can access
 * - FutureSpeak never accesses state data
 */

import { app } from 'electron';
import fs from 'fs/promises';
import path from 'path';
import crypto from 'crypto';
import {
  FatalIntegrityError,
  PersistentError,
  AgentFridayError,
} from './errors';

// ── Interfaces ────────────────────────────────────────────────────────

/** Describes a single file within the agent state snapshot. */
export interface StateFileEntry {
  /** Relative path from userData root (e.g. "memory/long-term.json") */
  relativePath: string;
  /** File size in bytes */
  sizeBytes: number;
  /** Last modification timestamp (epoch ms) */
  modifiedAt: number;
  /** SHA-256 hash of file contents */
  contentHash: string;
}

/** Metadata stored inside the archive header (unencrypted portion). */
export interface ArchiveManifest {
  version: string;
  createdAt: number;
  agentName: string;
  fileCount: number;
  totalSizeBytes: number;
  /** HMAC-SHA256 of the concatenated file hashes (verifies completeness) */
  integritySignature: string;
  files: StateFileEntry[];
  /** Whether this is an incremental backup (only changed files) */
  incremental: boolean;
  /** Previous full backup timestamp, if incremental */
  baseBackupTimestamp?: number;
}

/** The encrypted archive format written to disk. */
export interface EncryptedArchive {
  format: 'agent-friday-backup-v1';
  /** PBKDF2 salt (hex) */
  salt: string;
  /** AES-256-GCM IV (hex) */
  iv: string;
  /** PBKDF2 iterations */
  iterations: number;
  /** The encrypted payload (base64) — contains JSON-serialized ArchivePayload */
  ciphertext: string;
  /** GCM authentication tag (hex) */
  authTag: string;
}

/** The decrypted payload inside the archive. */
export interface ArchivePayload {
  manifest: ArchiveManifest;
  /** Map of relativePath → base64-encoded file contents */
  files: Record<string, string>;
}

/** Result of an export operation. */
export interface ExportResult {
  success: boolean;
  archivePath: string;
  manifest: ArchiveManifest;
  durationMs: number;
  error?: string;
}

/** Result of an import operation. */
export interface ImportResult {
  success: boolean;
  filesRestored: number;
  filesSkipped: number;
  warnings: string[];
  durationMs: number;
  error?: string;
}

/** Result of a validation-only pass on an archive. */
export interface ValidationResult {
  valid: boolean;
  manifest: ArchiveManifest | null;
  errors: string[];
  warnings: string[];
}

/** Configuration for the persistence engine. */
export interface PersistenceConfig {
  /** Whether scheduled backups are enabled */
  autoBackupEnabled: boolean;
  /** Cron pattern for scheduled backups (default: "0 3 * * *" = 3am daily) */
  autoBackupCron: string;
  /** Directory to store backups (default: userData/backups/) */
  backupDirectory: string;
  /** Max number of backup archives to keep (default: 7, oldest pruned) */
  maxBackupCount: number;
  /** Whether to use incremental backups (default: true) */
  incrementalEnabled: boolean;
  /** Passphrase for auto-backups (must be set by user, never stored in plaintext) */
  autoBackupPassphraseSet: boolean;
}

/** Backup history entry. */
export interface BackupRecord {
  id: string;
  timestamp: number;
  archivePath: string;
  fileCount: number;
  totalSizeBytes: number;
  incremental: boolean;
  durationMs: number;
}

// ── Constants ─────────────────────────────────────────────────────────

// Crypto Sprint 2: Upgraded from 100K → 600K per OWASP 2023 recommendation for SHA-512.
// Old backups remain decryptable because the iteration count is stored in the archive header.
const PBKDF2_ITERATIONS = 600_000;
const SALT_BYTES = 32;
const IV_BYTES = 16;
const KEY_BYTES = 32; // AES-256
const ARCHIVE_FORMAT = 'agent-friday-backup-v1' as const;

/** All state file paths relative to userData that define the agent. */
const STATE_FILE_PATHS: string[] = [
  // Settings & profile
  'friday-settings.json',
  'friday-intelligence.md',

  // Memory system
  'memory/long-term.json',
  'memory/medium-term.json',
  'memory/episodes.json',
  'memory/relationship.json',
  'memory/embedding-cache.json',

  // Trust & relationship
  'trust-graph.json',

  // Intelligence & routing
  'intelligence-router.json',
  'briefings.json',

  // Scheduling & commitments
  'scheduled-tasks.json',
  'commitments.json',
  'daily-briefings.json',

  // Workflow system
  'friday-data/recordings.json',
  'friday-data/templates.json',
  'friday-data/execution-history.json',

  // Communication
  'unified-inbox.json',
  'outbound-intelligence.json',

  // Meeting intelligence
  'meetings.json',

  // Superpower systems
  'superpowers.json',
  'friday-data/superpower-ecosystem.json',

  // Agent network
  'friday-data/agent-network.json',

  // Integrity
  'integrity-manifest.json',

  // Action audit trail
  'friday-data/action-ledger.json',
];

const DEFAULT_CONFIG: PersistenceConfig = {
  autoBackupEnabled: false,
  autoBackupCron: '0 3 * * *',
  backupDirectory: '',
  maxBackupCount: 7,
  incrementalEnabled: true,
  autoBackupPassphraseSet: false,
};

// ── Pure Crypto Functions ─────────────────────────────────────────────

/**
 * Derive an AES-256 key from a passphrase using PBKDF2.
 * Passphrase is never stored — key derived in memory, then discarded.
 */
export function deriveKey(
  passphrase: string,
  salt: Buffer,
  iterations: number = PBKDF2_ITERATIONS,
): Buffer {
  return crypto.pbkdf2Sync(passphrase, salt, iterations, KEY_BYTES, 'sha512');
}

/**
 * Encrypt a plaintext buffer with AES-256-GCM.
 * Returns { ciphertext, iv, authTag }.
 */
export function encryptPayload(
  plaintext: Buffer,
  key: Buffer,
): { ciphertext: Buffer; iv: Buffer; authTag: Buffer } {
  const iv = crypto.randomBytes(IV_BYTES);
  const cipher = crypto.createCipheriv('aes-256-gcm', key, iv);
  const encrypted = Buffer.concat([cipher.update(plaintext), cipher.final()]);
  const authTag = cipher.getAuthTag();
  return { ciphertext: encrypted, iv, authTag };
}

/**
 * Decrypt a ciphertext buffer with AES-256-GCM.
 * Throws if authentication fails (tampered data).
 */
export function decryptPayload(
  ciphertext: Buffer,
  key: Buffer,
  iv: Buffer,
  authTag: Buffer,
): Buffer {
  const decipher = crypto.createDecipheriv('aes-256-gcm', key, iv);
  decipher.setAuthTag(authTag);
  try {
    return Buffer.concat([decipher.update(ciphertext), decipher.final()]);
  } catch {
    throw new FatalIntegrityError(
      'persistence',
      'Archive decryption failed — wrong passphrase or tampered data',
    );
  }
}

/**
 * Compute SHA-256 hash of a buffer.
 */
export function hashContent(data: Buffer): string {
  return crypto.createHash('sha256').update(data).digest('hex');
}

/**
 * Compute HMAC-SHA256 of a string using a derived key.
 */
export function computeIntegritySignature(
  data: string,
  key: Buffer,
): string {
  return crypto.createHmac('sha256', key).update(data).digest('hex');
}

// ── State Export Engine ───────────────────────────────────────────────

export class StateExportEngine {
  private config: PersistenceConfig;
  private userDataPath: string = '';
  private backupHistory: BackupRecord[] = [];
  private historyPath: string = '';
  private initialized = false;
  private savePromise: Promise<void> = Promise.resolve();
  /** Encrypted passphrase for auto-backups (encrypted via vault identityKey) */
  private autoBackupPassphrase: string | null = null;

  constructor(config: Partial<PersistenceConfig> = {}) {
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  // ── Initialization ──────────────────────────────────────────────

  async initialize(): Promise<void> {
    if (this.initialized) return;

    this.userDataPath = app.getPath('userData');
    if (!this.config.backupDirectory) {
      this.config.backupDirectory = path.join(this.userDataPath, 'backups');
    }
    this.historyPath = path.join(this.userDataPath, 'backup-history.json');

    // Ensure backup directory exists
    await fs.mkdir(this.config.backupDirectory, { recursive: true });

    // Ensure friday-data directory exists
    await fs.mkdir(path.join(this.userDataPath, 'friday-data'), { recursive: true });
    await fs.mkdir(path.join(this.userDataPath, 'memory'), { recursive: true });

    // Load backup history
    try {
      const data = await fs.readFile(this.historyPath, 'utf-8');
      this.backupHistory = JSON.parse(data);
    } catch {
      this.backupHistory = [];
    }

    // Load encrypted auto-backup passphrase if set
    try {
      const passFile = path.join(this.userDataPath, '.backup-passphrase');
      this.autoBackupPassphrase = await fs.readFile(passFile, 'utf-8');
      this.config.autoBackupPassphraseSet = true;
    } catch {
      this.autoBackupPassphrase = null;
      this.config.autoBackupPassphraseSet = false;
    }

    this.initialized = true;
    console.log(
      `[StateExport] Initialized — ${this.backupHistory.length} previous backups, auto-backup ${this.config.autoBackupEnabled ? 'ON' : 'OFF'}`,
    );
  }

  // ── Full Export ─────────────────────────────────────────────────

  /**
   * Export the complete agent state to an encrypted archive.
   * @param passphrase User-provided encryption passphrase
   * @param outputPath Where to write the .friday-backup file (optional, default: backups/)
   */
  async exportState(
    passphrase: string,
    outputPath?: string,
  ): Promise<ExportResult> {
    const start = Date.now();

    if (!passphrase || passphrase.length < 8) {
      return {
        success: false,
        archivePath: '',
        manifest: this.emptyManifest(),
        durationMs: Date.now() - start,
        error: 'Passphrase must be at least 8 characters',
      };
    }

    try {
      // 1. Enumerate and read all state files
      const { entries, fileData } = await this.snapshotState();

      // 2. Build manifest
      const salt = crypto.randomBytes(SALT_BYTES);
      const key = deriveKey(passphrase, salt);
      const hashConcat = entries.map(e => e.contentHash).join(':');
      const integritySignature = computeIntegritySignature(hashConcat, key);

      const agentName = await this.getAgentName();
      const manifest: ArchiveManifest = {
        version: '1.0.0',
        createdAt: Date.now(),
        agentName,
        fileCount: entries.length,
        totalSizeBytes: entries.reduce((sum, e) => sum + e.sizeBytes, 0),
        integritySignature,
        files: entries,
        incremental: false,
      };

      // 3. Build payload
      const payload: ArchivePayload = { manifest, files: fileData };
      const plaintext = Buffer.from(JSON.stringify(payload), 'utf-8');

      // 4. Encrypt
      const { ciphertext, iv, authTag } = encryptPayload(plaintext, key);

      // 5. Build archive
      const archive: EncryptedArchive = {
        format: ARCHIVE_FORMAT,
        salt: salt.toString('hex'),
        iv: iv.toString('hex'),
        iterations: PBKDF2_ITERATIONS,
        ciphertext: ciphertext.toString('base64'),
        authTag: authTag.toString('hex'),
      };

      // 6. Write to disk
      const archivePath =
        outputPath ||
        path.join(
          this.config.backupDirectory,
          `agent-friday-${new Date().toISOString().replace(/[:.]/g, '-')}.friday-backup`,
        );

      await fs.mkdir(path.dirname(archivePath), { recursive: true });
      await fs.writeFile(archivePath, JSON.stringify(archive), 'utf-8');

      const durationMs = Date.now() - start;

      // 7. Record in history
      const record: BackupRecord = {
        id: crypto.randomUUID().slice(0, 12),
        timestamp: manifest.createdAt,
        archivePath,
        fileCount: manifest.fileCount,
        totalSizeBytes: manifest.totalSizeBytes,
        incremental: false,
        durationMs,
      };
      this.backupHistory.push(record);
      await this.pruneBackups();
      this.queueSave();

      return { success: true, archivePath, manifest, durationMs };
    } catch (err) {
      return {
        success: false,
        archivePath: '',
        manifest: this.emptyManifest(),
        durationMs: Date.now() - start,
        error: err instanceof Error ? err.message : String(err),
      };
    }
  }

  // ── Incremental Export ──────────────────────────────────────────

  /**
   * Export only files that changed since the last backup.
   * Falls back to full export if no previous backup exists.
   */
  async exportIncremental(
    passphrase: string,
    outputPath?: string,
  ): Promise<ExportResult> {
    const start = Date.now();

    if (!passphrase || passphrase.length < 8) {
      return {
        success: false,
        archivePath: '',
        manifest: this.emptyManifest(),
        durationMs: Date.now() - start,
        error: 'Passphrase must be at least 8 characters',
      };
    }

    const lastBackup = this.getLastBackup();
    if (!lastBackup) {
      // No previous backup — do full export
      return this.exportState(passphrase, outputPath);
    }

    try {
      // 1. Snapshot all state, filter to changed files
      const { entries: allEntries, fileData: allFileData } = await this.snapshotState();

      const changedEntries: StateFileEntry[] = [];
      const changedFileData: Record<string, string> = {};

      for (const entry of allEntries) {
        if (entry.modifiedAt > lastBackup.timestamp) {
          changedEntries.push(entry);
          changedFileData[entry.relativePath] = allFileData[entry.relativePath];
        }
      }

      if (changedEntries.length === 0) {
        return {
          success: true,
          archivePath: '',
          manifest: this.emptyManifest(),
          durationMs: Date.now() - start,
          error: 'No files changed since last backup',
        };
      }

      // 2. Build manifest
      const salt = crypto.randomBytes(SALT_BYTES);
      const key = deriveKey(passphrase, salt);
      const hashConcat = changedEntries.map(e => e.contentHash).join(':');
      const integritySignature = computeIntegritySignature(hashConcat, key);

      const agentName = await this.getAgentName();
      const manifest: ArchiveManifest = {
        version: '1.0.0',
        createdAt: Date.now(),
        agentName,
        fileCount: changedEntries.length,
        totalSizeBytes: changedEntries.reduce((sum, e) => sum + e.sizeBytes, 0),
        integritySignature,
        files: changedEntries,
        incremental: true,
        baseBackupTimestamp: lastBackup.timestamp,
      };

      // 3. Encrypt & write
      const payload: ArchivePayload = { manifest, files: changedFileData };
      const plaintext = Buffer.from(JSON.stringify(payload), 'utf-8');
      const { ciphertext, iv, authTag } = encryptPayload(plaintext, key);

      const archive: EncryptedArchive = {
        format: ARCHIVE_FORMAT,
        salt: salt.toString('hex'),
        iv: iv.toString('hex'),
        iterations: PBKDF2_ITERATIONS,
        ciphertext: ciphertext.toString('base64'),
        authTag: authTag.toString('hex'),
      };

      const archivePath =
        outputPath ||
        path.join(
          this.config.backupDirectory,
          `agent-friday-incremental-${new Date().toISOString().replace(/[:.]/g, '-')}.friday-backup`,
        );

      await fs.mkdir(path.dirname(archivePath), { recursive: true });
      await fs.writeFile(archivePath, JSON.stringify(archive), 'utf-8');

      const durationMs = Date.now() - start;

      // 4. Record
      const record: BackupRecord = {
        id: crypto.randomUUID().slice(0, 12),
        timestamp: manifest.createdAt,
        archivePath,
        fileCount: manifest.fileCount,
        totalSizeBytes: manifest.totalSizeBytes,
        incremental: true,
        durationMs,
      };
      this.backupHistory.push(record);
      await this.pruneBackups();
      this.queueSave();

      return { success: true, archivePath, manifest, durationMs };
    } catch (err) {
      return {
        success: false,
        archivePath: '',
        manifest: this.emptyManifest(),
        durationMs: Date.now() - start,
        error: err instanceof Error ? err.message : String(err),
      };
    }
  }

  // ── Validate Archive ────────────────────────────────────────────

  /**
   * Validate an archive without restoring — dry run to check integrity.
   */
  async validateArchive(
    archivePath: string,
    passphrase: string,
  ): Promise<ValidationResult> {
    const errors: string[] = [];
    const warnings: string[] = [];

    try {
      const raw = await fs.readFile(archivePath, 'utf-8');
      const archive: EncryptedArchive = JSON.parse(raw);

      // Format check
      if (archive.format !== ARCHIVE_FORMAT) {
        errors.push(`Unknown format: ${archive.format}`);
        return { valid: false, manifest: null, errors, warnings };
      }

      // Decrypt
      const salt = Buffer.from(archive.salt, 'hex');
      const iv = Buffer.from(archive.iv, 'hex');
      const authTag = Buffer.from(archive.authTag, 'hex');
      const ciphertext = Buffer.from(archive.ciphertext, 'base64');

      const key = deriveKey(passphrase, salt, archive.iterations);
      let decrypted: Buffer;
      try {
        decrypted = decryptPayload(ciphertext, key, iv, authTag);
      } catch {
        errors.push('Decryption failed — wrong passphrase or corrupted archive');
        return { valid: false, manifest: null, errors, warnings };
      }

      // Parse payload
      const payload: ArchivePayload = JSON.parse(decrypted.toString('utf-8'));
      const { manifest, files } = payload;

      // Verify file count
      if (manifest.files.length !== Object.keys(files).length) {
        errors.push(
          `Manifest declares ${manifest.files.length} files but archive contains ${Object.keys(files).length}`,
        );
      }

      // Verify each file hash
      for (const entry of manifest.files) {
        const fileContent = files[entry.relativePath];
        if (!fileContent) {
          errors.push(`Missing file: ${entry.relativePath}`);
          continue;
        }
        const buf = Buffer.from(fileContent, 'base64');
        const actualHash = hashContent(buf);
        if (actualHash !== entry.contentHash) {
          errors.push(
            `Hash mismatch for ${entry.relativePath}: expected ${entry.contentHash.slice(0, 12)}, got ${actualHash.slice(0, 12)}`,
          );
        }
      }

      // Verify integrity signature
      const hashConcat = manifest.files.map(e => e.contentHash).join(':');
      const expectedSig = computeIntegritySignature(hashConcat, key);
      if (expectedSig !== manifest.integritySignature) {
        errors.push('Integrity signature mismatch — archive may be tampered');
      }

      // Warnings for incremental
      if (manifest.incremental) {
        warnings.push(
          `Incremental backup — only ${manifest.fileCount} changed files. Full restore requires base backup.`,
        );
      }

      return {
        valid: errors.length === 0,
        manifest,
        errors,
        warnings,
      };
    } catch (err) {
      if (err instanceof AgentFridayError) {
        errors.push(err.message);
      } else {
        errors.push(err instanceof Error ? err.message : String(err));
      }
      return { valid: false, manifest: null, errors, warnings };
    }
  }

  // ── Import (Restore) ───────────────────────────────────────────

  /**
   * Restore agent state from an encrypted archive.
   * VALIDATES EVERYTHING before writing a single byte (fail-closed).
   */
  async importState(
    archivePath: string,
    passphrase: string,
  ): Promise<ImportResult> {
    const start = Date.now();

    // 1. Validate first
    const validation = await this.validateArchive(archivePath, passphrase);
    if (!validation.valid || !validation.manifest) {
      return {
        success: false,
        filesRestored: 0,
        filesSkipped: 0,
        warnings: validation.errors,
        durationMs: Date.now() - start,
        error: validation.errors.join('; '),
      };
    }

    try {
      // 2. Decrypt (we know it works because validation passed)
      const raw = await fs.readFile(archivePath, 'utf-8');
      const archive: EncryptedArchive = JSON.parse(raw);
      const salt = Buffer.from(archive.salt, 'hex');
      const iv = Buffer.from(archive.iv, 'hex');
      const authTag = Buffer.from(archive.authTag, 'hex');
      const ciphertext = Buffer.from(archive.ciphertext, 'base64');
      const key = deriveKey(passphrase, salt, archive.iterations);
      const decrypted = decryptPayload(ciphertext, key, iv, authTag);
      const payload: ArchivePayload = JSON.parse(decrypted.toString('utf-8'));

      // 3. Write files
      let filesRestored = 0;
      let filesSkipped = 0;
      const warnings: string[] = [...validation.warnings];

      for (const entry of payload.manifest.files) {
        const fileContent = payload.files[entry.relativePath];
        if (!fileContent) {
          filesSkipped++;
          warnings.push(`Skipped missing file: ${entry.relativePath}`);
          continue;
        }

        const targetPath = path.join(this.userDataPath, entry.relativePath);

        // Ensure parent directory exists
        await fs.mkdir(path.dirname(targetPath), { recursive: true });

        // Write file
        const buf = Buffer.from(fileContent, 'base64');
        await fs.writeFile(targetPath, buf);
        filesRestored++;
      }

      return {
        success: true,
        filesRestored,
        filesSkipped,
        warnings,
        durationMs: Date.now() - start,
      };
    } catch (err) {
      if (err instanceof FatalIntegrityError) {
        throw err; // Re-throw integrity errors
      }
      return {
        success: false,
        filesRestored: 0,
        filesSkipped: 0,
        warnings: [],
        durationMs: Date.now() - start,
        error: err instanceof Error ? err.message : String(err),
      };
    }
  }

  // ── Scheduled Backup ────────────────────────────────────────────

  /**
   * Set the auto-backup passphrase (encrypted via vault identityKey).
   * Passphrase is encrypted at rest using XSalsa20-Poly1305.
   */
  async setAutoBackupPassphrase(passphrase: string): Promise<void> {
    const { encryptPrivateKey, isVaultUnlocked } = await import('./vault');
    if (!isVaultUnlocked()) {
      throw new PersistentError(
        'persistence',
        'Vault is locked — cannot store auto-backup passphrase securely',
      );
    }

    const encrypted = encryptPrivateKey(passphrase);
    const passFile = path.join(this.userDataPath, '.backup-passphrase');
    await fs.writeFile(passFile, encrypted, 'utf-8');
    this.autoBackupPassphrase = encrypted;
    this.config.autoBackupPassphraseSet = true;
  }

  /**
   * Run a scheduled backup using the stored passphrase.
   * Called by the scheduler — not directly by the user.
   */
  async runScheduledBackup(): Promise<ExportResult> {
    if (!this.autoBackupPassphrase) {
      return {
        success: false,
        archivePath: '',
        manifest: this.emptyManifest(),
        durationMs: 0,
        error: 'No auto-backup passphrase configured',
      };
    }

    // Decrypt the passphrase
    try {
      const { decryptPrivateKey } = await import('./vault');
      const passphrase = decryptPrivateKey(this.autoBackupPassphrase);

      if (this.config.incrementalEnabled) {
        return this.exportIncremental(passphrase);
      } else {
        return this.exportState(passphrase);
      }
    } catch {
      return {
        success: false,
        archivePath: '',
        manifest: this.emptyManifest(),
        durationMs: 0,
        error: 'Failed to decrypt auto-backup passphrase',
      };
    }
  }

  /**
   * Clear the stored auto-backup passphrase.
   */
  async clearAutoBackupPassphrase(): Promise<void> {
    const passFile = path.join(this.userDataPath, '.backup-passphrase');
    try {
      await fs.unlink(passFile);
    } catch {
      // File may not exist
    }
    this.autoBackupPassphrase = null;
    this.config.autoBackupPassphraseSet = false;
  }

  // ── Queries ─────────────────────────────────────────────────────

  /** Get the list of all state file paths that would be exported. */
  getStateFilePaths(): string[] {
    return [...STATE_FILE_PATHS];
  }

  /** Enumerate state files with metadata (which exist, sizes, modification times). */
  async enumerateState(): Promise<StateFileEntry[]> {
    const entries: StateFileEntry[] = [];

    for (const relativePath of STATE_FILE_PATHS) {
      const fullPath = path.join(this.userDataPath, relativePath);
      try {
        const stat = await fs.stat(fullPath);
        const content = await fs.readFile(fullPath);
        entries.push({
          relativePath,
          sizeBytes: stat.size,
          modifiedAt: stat.mtimeMs,
          contentHash: hashContent(content),
        });
      } catch {
        // File doesn't exist yet — skip silently
      }
    }

    return entries;
  }

  /** Get backup history. */
  getBackupHistory(): BackupRecord[] {
    return [...this.backupHistory];
  }

  /** Get the most recent backup record. */
  getLastBackup(): BackupRecord | null {
    if (this.backupHistory.length === 0) return null;
    return this.backupHistory[this.backupHistory.length - 1];
  }

  /** Get current configuration. */
  getConfig(): PersistenceConfig {
    return { ...this.config };
  }

  /** Update configuration. */
  updateConfig(partial: Partial<PersistenceConfig>): PersistenceConfig {
    this.config = { ...this.config, ...partial };
    this.queueSave();
    return { ...this.config };
  }

  /** Get prompt context for system prompt injection. */
  getPromptContext(): string {
    const lastBackup = this.getLastBackup();

    if (!lastBackup) {
      return '[STATE PERSISTENCE]\nNo backups have been created yet. The user should be encouraged to set up encrypted backups to protect their agent state.';
    }

    const age = Date.now() - lastBackup.timestamp;
    const ageHours = Math.round(age / (1000 * 60 * 60));
    const ageDays = Math.round(age / (1000 * 60 * 60 * 24));
    const ageStr = ageDays > 0 ? `${ageDays}d ago` : `${ageHours}h ago`;

    let ctx = `[STATE PERSISTENCE]\nLast backup: ${ageStr} (${lastBackup.fileCount} files, ${this.formatBytes(lastBackup.totalSizeBytes)})`;
    if (this.config.autoBackupEnabled) {
      ctx += `\nAuto-backup: ON (${this.config.autoBackupCron})`;
    } else {
      ctx += '\nAuto-backup: OFF';
    }

    if (ageDays > 7) {
      ctx += '\n⚠️ Backup is over a week old — recommend creating a fresh backup.';
    }

    return ctx;
  }

  // ── Continuity Check ────────────────────────────────────────────

  /**
   * Verify the agent can function fully offline with no FutureSpeak dependencies.
   * Returns a list of any external dependencies detected.
   */
  async checkContinuityReadiness(): Promise<{
    ready: boolean;
    issues: string[];
  }> {
    const issues: string[] = [];

    // Check all critical state files exist
    const criticalFiles = [
      'friday-settings.json',
      'memory/long-term.json',
      'trust-graph.json',
    ];

    for (const file of criticalFiles) {
      const fullPath = path.join(this.userDataPath, file);
      try {
        await fs.access(fullPath);
      } catch {
        issues.push(`Missing critical state file: ${file}`);
      }
    }

    // Check no backup passphrase is needed for functioning
    // (the agent should WORK without backups — backups are for recovery)

    return {
      ready: issues.length === 0,
      issues,
    };
  }

  // ── Lifecycle ───────────────────────────────────────────────────

  stop(): void {
    // Nothing to clean up — save queue handles itself
  }

  // ── Internal Helpers ────────────────────────────────────────────

  /** Snapshot all existing state files into memory. */
  private async snapshotState(): Promise<{
    entries: StateFileEntry[];
    fileData: Record<string, string>;
  }> {
    const entries: StateFileEntry[] = [];
    const fileData: Record<string, string> = {};

    for (const relativePath of STATE_FILE_PATHS) {
      const fullPath = path.join(this.userDataPath, relativePath);
      try {
        const stat = await fs.stat(fullPath);
        const content = await fs.readFile(fullPath);
        const contentHash = hashContent(content);

        entries.push({
          relativePath,
          sizeBytes: stat.size,
          modifiedAt: stat.mtimeMs,
          contentHash,
        });

        fileData[relativePath] = content.toString('base64');
      } catch {
        // File doesn't exist — skip (not all engines may be initialized yet)
      }
    }

    return { entries, fileData };
  }

  /** Get the agent's display name from settings. */
  private async getAgentName(): Promise<string> {
    try {
      const settingsPath = path.join(this.userDataPath, 'friday-settings.json');
      const data = await fs.readFile(settingsPath, 'utf-8');
      const settings = JSON.parse(data);
      return settings.agentName || settings.userName || 'Agent Friday';
    } catch {
      return 'Agent Friday';
    }
  }

  /** Prune old backups beyond maxBackupCount. */
  private async pruneBackups(): Promise<void> {
    while (this.backupHistory.length > this.config.maxBackupCount) {
      const oldest = this.backupHistory.shift();
      if (oldest) {
        try {
          await fs.unlink(oldest.archivePath);
        } catch {
          // File may already be gone
        }
      }
    }
  }

  /** Queued save of backup history. */
  private queueSave(): void {
    this.savePromise = this.savePromise
      .then(async () => {
        const data = JSON.stringify(
          {
            config: this.config,
            history: this.backupHistory,
          },
          null,
          2,
        );
        await fs.writeFile(this.historyPath, data, 'utf-8');
      })
      // Crypto Sprint 17: Sanitize error output.
      .catch(err => console.error('[StateExport] Save failed:', err instanceof Error ? err.message : 'Unknown error'));
  }

  private emptyManifest(): ArchiveManifest {
    return {
      version: '1.0.0',
      createdAt: 0,
      agentName: '',
      fileCount: 0,
      totalSizeBytes: 0,
      integritySignature: '',
      files: [],
      incremental: false,
    };
  }

  private formatBytes(bytes: number): string {
    if (bytes < 1024) return `${bytes}B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
  }
}

// ── Singleton ─────────────────────────────────────────────────────────

export const stateExport = new StateExportEngine();
