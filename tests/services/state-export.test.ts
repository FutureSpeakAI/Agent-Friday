/**
 * State Export Engine — comprehensive tests (Track VII, Phase 4).
 *
 * Coverage:
 *   ✓ Pure functions: deriveKey, encryptPayload, decryptPayload, hashContent,
 *     computeIntegritySignature
 *   ✓ Class: initialization, full export, incremental export, archive validation,
 *     import/restore (fail-closed), scheduled backup, configuration, backup history,
 *     prompt context, continuity readiness, cLaw compliance, passphrase rejection,
 *     tamper detection, file enumeration, state file paths, prune logic
 *
 * 70+ tests covering every public API surface.
 */

import crypto from 'crypto';

// ── Electron + FS Mocks ──────────────────────────────────────────────

vi.mock('electron', () => ({
  app: { getPath: vi.fn(() => '/tmp/test-persistence') },
  safeStorage: {
    isEncryptionAvailable: vi.fn(() => true),
    encryptString: vi.fn((s: string) => Buffer.from(`enc:${s}`)),
    decryptString: vi.fn((buf: Buffer) => buf.toString().replace('enc:', '')),
  },
}));

vi.mock('fs/promises', () => ({
  default: {
    mkdir: vi.fn().mockResolvedValue(undefined),
    readFile: vi.fn().mockRejectedValue(new Error('ENOENT')),
    writeFile: vi.fn().mockResolvedValue(undefined),
    stat: vi.fn().mockRejectedValue(new Error('ENOENT')),
    unlink: vi.fn().mockResolvedValue(undefined),
    access: vi.fn().mockRejectedValue(new Error('ENOENT')),
  },
}));

import {
  deriveKey,
  encryptPayload,
  decryptPayload,
  hashContent,
  computeIntegritySignature,
  StateExportEngine,
  type StateFileEntry,
  type ArchiveManifest,
  type EncryptedArchive,
  type ArchivePayload,
  type ExportResult,
  type ImportResult,
  type ValidationResult,
  type PersistenceConfig,
  type BackupRecord,
} from '../../src/main/state-export';

import _fs from 'fs/promises';
const mockFs = _fs as unknown as {
  mkdir: ReturnType<typeof vi.fn>;
  readFile: ReturnType<typeof vi.fn>;
  writeFile: ReturnType<typeof vi.fn>;
  stat: ReturnType<typeof vi.fn>;
  unlink: ReturnType<typeof vi.fn>;
  access: ReturnType<typeof vi.fn>;
};

// ── Test Helpers ─────────────────────────────────────────────────────

const TEST_PASSPHRASE = 'test-passphrase-min8chars';
const SHORT_PASSPHRASE = 'short';

/** Create a minimal valid archive payload for testing. */
function buildTestArchive(
  passphrase: string,
  files: Record<string, string> = { 'friday-settings.json': '{"test":true}' },
  opts: { incremental?: boolean; baseBackupTimestamp?: number } = {},
): { archiveJson: string; manifest: ArchiveManifest } {
  const salt = crypto.randomBytes(32);
  const key = deriveKey(passphrase, salt);

  const fileEntries: StateFileEntry[] = [];
  const fileData: Record<string, string> = {};

  for (const [relPath, content] of Object.entries(files)) {
    const buf = Buffer.from(content, 'utf-8');
    const b64 = buf.toString('base64');
    fileEntries.push({
      relativePath: relPath,
      sizeBytes: buf.length,
      modifiedAt: Date.now(),
      contentHash: hashContent(buf),
    });
    fileData[relPath] = b64;
  }

  const hashConcat = fileEntries.map(e => e.contentHash).join(':');
  const integritySignature = computeIntegritySignature(hashConcat, key);

  const manifest: ArchiveManifest = {
    version: '1.0.0',
    createdAt: Date.now(),
    agentName: 'Test Agent',
    fileCount: fileEntries.length,
    totalSizeBytes: fileEntries.reduce((s, e) => s + e.sizeBytes, 0),
    integritySignature,
    files: fileEntries,
    incremental: opts.incremental || false,
    baseBackupTimestamp: opts.baseBackupTimestamp,
  };

  const payload: ArchivePayload = { manifest, files: fileData };
  const plaintext = Buffer.from(JSON.stringify(payload), 'utf-8');
  const { ciphertext, iv, authTag } = encryptPayload(plaintext, key);

  const archive: EncryptedArchive = {
    format: 'agent-friday-backup-v1',
    salt: salt.toString('hex'),
    iv: iv.toString('hex'),
    iterations: 600_000, // Crypto Sprint 2: upgraded from 100K
    ciphertext: ciphertext.toString('base64'),
    authTag: authTag.toString('hex'),
  };

  return { archiveJson: JSON.stringify(archive), manifest };
}

/** Create and initialize a fresh engine for testing. */
async function createEngine(
  configOverrides: Partial<PersistenceConfig> = {},
): Promise<StateExportEngine> {
  const engine = new StateExportEngine(configOverrides);
  await engine.initialize();
  return engine;
}

/** Set up mockFs to serve specific state files. */
function mockStateFiles(files: Record<string, string>): void {
  const now = Date.now();
  mockFs.stat.mockImplementation(async (p: string) => {
    for (const [rel, content] of Object.entries(files)) {
      if (p.endsWith(rel.replace(/\//g, '\\')) || p.endsWith(rel)) {
        return { size: Buffer.from(content).length, mtimeMs: now };
      }
    }
    throw new Error('ENOENT');
  });

  mockFs.readFile.mockImplementation(async (p: string, encoding?: string) => {
    // Handle backup history load
    if (p.toString().includes('backup-history.json')) throw new Error('ENOENT');
    // Handle passphrase file load
    if (p.toString().includes('.backup-passphrase')) throw new Error('ENOENT');

    for (const [rel, content] of Object.entries(files)) {
      if (p.toString().endsWith(rel.replace(/\//g, '\\')) || p.toString().endsWith(rel)) {
        if (encoding === 'utf-8') return content;
        return Buffer.from(content);
      }
    }
    throw new Error('ENOENT');
  });
}

// ── Lifecycle ────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks();
  // Default: no files exist
  mockFs.readFile.mockRejectedValue(new Error('ENOENT'));
  mockFs.stat.mockRejectedValue(new Error('ENOENT'));
  mockFs.mkdir.mockResolvedValue(undefined);
  mockFs.writeFile.mockResolvedValue(undefined);
  mockFs.unlink.mockResolvedValue(undefined);
  mockFs.access.mockRejectedValue(new Error('ENOENT'));
});

// =====================================================================
// §1 — Pure Crypto Functions
// =====================================================================

describe('deriveKey', () => {
  it('returns a 32-byte Buffer', () => {
    const salt = crypto.randomBytes(32);
    const key = deriveKey('test-passphrase', salt);
    expect(key).toBeInstanceOf(Buffer);
    expect(key.length).toBe(32);
  });

  it('same passphrase + salt = same key', () => {
    const salt = crypto.randomBytes(32);
    const k1 = deriveKey('my-pass', salt);
    const k2 = deriveKey('my-pass', salt);
    expect(k1.equals(k2)).toBe(true);
  });

  it('different passphrases produce different keys', () => {
    const salt = crypto.randomBytes(32);
    const k1 = deriveKey('pass-a', salt);
    const k2 = deriveKey('pass-b', salt);
    expect(k1.equals(k2)).toBe(false);
  });

  it('different salts produce different keys', () => {
    const s1 = crypto.randomBytes(32);
    const s2 = crypto.randomBytes(32);
    const k1 = deriveKey('same-pass', s1);
    const k2 = deriveKey('same-pass', s2);
    expect(k1.equals(k2)).toBe(false);
  });

  it('respects iteration count parameter', () => {
    const salt = crypto.randomBytes(32);
    const k1 = deriveKey('pass', salt, 1000);
    const k2 = deriveKey('pass', salt, 2000);
    expect(k1.equals(k2)).toBe(false);
  });
});

describe('encryptPayload / decryptPayload', () => {
  it('round-trips plaintext through encrypt → decrypt', () => {
    const key = crypto.randomBytes(32);
    const original = Buffer.from('Hello Agent Friday!');
    const { ciphertext, iv, authTag } = encryptPayload(original, key);
    const decrypted = decryptPayload(ciphertext, key, iv, authTag);
    expect(decrypted.toString()).toBe('Hello Agent Friday!');
  });

  it('produces different ciphertext on each call (random IV)', () => {
    const key = crypto.randomBytes(32);
    const msg = Buffer.from('deterministic test');
    const e1 = encryptPayload(msg, key);
    const e2 = encryptPayload(msg, key);
    expect(e1.iv.equals(e2.iv)).toBe(false);
    expect(e1.ciphertext.equals(e2.ciphertext)).toBe(false);
  });

  it('decryption with wrong key throws FatalIntegrityError', () => {
    const key1 = crypto.randomBytes(32);
    const key2 = crypto.randomBytes(32);
    const msg = Buffer.from('secret');
    const { ciphertext, iv, authTag } = encryptPayload(msg, key1);
    expect(() => decryptPayload(ciphertext, key2, iv, authTag)).toThrow(
      /decryption failed/i,
    );
  });

  it('decryption with tampered ciphertext throws', () => {
    const key = crypto.randomBytes(32);
    const msg = Buffer.from('tamper test');
    const { ciphertext, iv, authTag } = encryptPayload(msg, key);
    ciphertext[0] ^= 0xff; // flip a byte
    expect(() => decryptPayload(ciphertext, key, iv, authTag)).toThrow();
  });

  it('decryption with tampered authTag throws', () => {
    const key = crypto.randomBytes(32);
    const msg = Buffer.from('auth test');
    const { ciphertext, iv, authTag } = encryptPayload(msg, key);
    authTag[0] ^= 0xff;
    expect(() => decryptPayload(ciphertext, key, iv, authTag)).toThrow();
  });

  it('handles empty plaintext', () => {
    const key = crypto.randomBytes(32);
    const original = Buffer.alloc(0);
    const { ciphertext, iv, authTag } = encryptPayload(original, key);
    const decrypted = decryptPayload(ciphertext, key, iv, authTag);
    expect(decrypted.length).toBe(0);
  });

  it('handles large plaintext (1MB)', () => {
    const key = crypto.randomBytes(32);
    const original = crypto.randomBytes(1024 * 1024);
    const { ciphertext, iv, authTag } = encryptPayload(original, key);
    const decrypted = decryptPayload(ciphertext, key, iv, authTag);
    expect(decrypted.equals(original)).toBe(true);
  });
});

describe('hashContent', () => {
  it('returns a 64-character hex string (SHA-256)', () => {
    const hash = hashContent(Buffer.from('test'));
    expect(hash).toMatch(/^[0-9a-f]{64}$/);
  });

  it('identical content produces identical hash', () => {
    const h1 = hashContent(Buffer.from('same'));
    const h2 = hashContent(Buffer.from('same'));
    expect(h1).toBe(h2);
  });

  it('different content produces different hash', () => {
    const h1 = hashContent(Buffer.from('one'));
    const h2 = hashContent(Buffer.from('two'));
    expect(h1).not.toBe(h2);
  });
});

describe('computeIntegritySignature', () => {
  it('returns a 64-character hex HMAC', () => {
    const key = crypto.randomBytes(32);
    const sig = computeIntegritySignature('test-data', key);
    expect(sig).toMatch(/^[0-9a-f]{64}$/);
  });

  it('same data + key = same signature', () => {
    const key = crypto.randomBytes(32);
    const s1 = computeIntegritySignature('data', key);
    const s2 = computeIntegritySignature('data', key);
    expect(s1).toBe(s2);
  });

  it('different keys produce different signatures', () => {
    const k1 = crypto.randomBytes(32);
    const k2 = crypto.randomBytes(32);
    const s1 = computeIntegritySignature('data', k1);
    const s2 = computeIntegritySignature('data', k2);
    expect(s1).not.toBe(s2);
  });

  it('different data produces different signatures', () => {
    const key = crypto.randomBytes(32);
    const s1 = computeIntegritySignature('alpha', key);
    const s2 = computeIntegritySignature('beta', key);
    expect(s1).not.toBe(s2);
  });
});

// =====================================================================
// §2 — Initialization
// =====================================================================

describe('StateExportEngine — initialization', () => {
  it('initializes with default config', async () => {
    const engine = await createEngine();
    const config = engine.getConfig();
    expect(config.autoBackupEnabled).toBe(false);
    expect(config.autoBackupCron).toBe('0 3 * * *');
    expect(config.maxBackupCount).toBe(7);
    expect(config.incrementalEnabled).toBe(true);
    expect(config.autoBackupPassphraseSet).toBe(false);
  });

  it('creates backup, friday-data, and memory directories', async () => {
    await createEngine();
    expect(mockFs.mkdir).toHaveBeenCalledWith(
      expect.stringContaining('backups'),
      { recursive: true },
    );
    expect(mockFs.mkdir).toHaveBeenCalledWith(
      expect.stringContaining('friday-data'),
      { recursive: true },
    );
    expect(mockFs.mkdir).toHaveBeenCalledWith(
      expect.stringContaining('memory'),
      { recursive: true },
    );
  });

  it('loads backup history from disk if present', async () => {
    const history = [
      { id: 'bk-1', timestamp: 1000, archivePath: '/x', fileCount: 5, totalSizeBytes: 100, incremental: false, durationMs: 50 },
    ];
    mockFs.readFile.mockImplementation(async (p: string) => {
      if (p.toString().includes('backup-history.json')) return JSON.stringify(history);
      throw new Error('ENOENT');
    });

    const engine = await createEngine();
    expect(engine.getBackupHistory()).toHaveLength(1);
    expect(engine.getBackupHistory()[0].id).toBe('bk-1');
  });

  it('starts with empty history if file not found', async () => {
    const engine = await createEngine();
    expect(engine.getBackupHistory()).toHaveLength(0);
  });

  it('is idempotent (second call is no-op)', async () => {
    const engine = new StateExportEngine();
    await engine.initialize();
    const callsBefore = mockFs.mkdir.mock.calls.length;
    await engine.initialize();
    expect(mockFs.mkdir.mock.calls.length).toBe(callsBefore);
  });

  it('accepts custom config overrides', async () => {
    const engine = await createEngine({ maxBackupCount: 3, autoBackupEnabled: true });
    const config = engine.getConfig();
    expect(config.maxBackupCount).toBe(3);
    expect(config.autoBackupEnabled).toBe(true);
  });
});

// =====================================================================
// §3 — State File Paths & Enumeration
// =====================================================================

describe('StateExportEngine — state file paths', () => {
  it('returns a non-empty list of state file paths', async () => {
    const engine = await createEngine();
    const paths = engine.getStateFilePaths();
    expect(paths.length).toBeGreaterThan(20);
  });

  it('includes critical files (settings, memory, trust-graph)', async () => {
    const engine = await createEngine();
    const paths = engine.getStateFilePaths();
    expect(paths).toContain('friday-settings.json');
    expect(paths).toContain('memory/long-term.json');
    expect(paths).toContain('trust-graph.json');
  });

  it('does NOT include context-graph (in-memory only)', async () => {
    const engine = await createEngine();
    const paths = engine.getStateFilePaths();
    const hasContextGraph = paths.some(p => p.includes('context-graph'));
    expect(hasContextGraph).toBe(false);
  });

  it('does NOT include context-stream (in-memory only)', async () => {
    const engine = await createEngine();
    const paths = engine.getStateFilePaths();
    const hasContextStream = paths.some(p => p.includes('context-stream'));
    expect(hasContextStream).toBe(false);
  });

  it('returns a defensive copy (mutations do not affect internal state)', async () => {
    const engine = await createEngine();
    const paths1 = engine.getStateFilePaths();
    paths1.push('fake-file.json');
    const paths2 = engine.getStateFilePaths();
    expect(paths2).not.toContain('fake-file.json');
  });
});

describe('StateExportEngine — enumerateState', () => {
  it('returns entries only for existing files', async () => {
    mockStateFiles({
      'friday-settings.json': '{"name":"test"}',
      'memory/long-term.json': '[]',
    });
    const engine = await createEngine();
    const entries = await engine.enumerateState();
    expect(entries.length).toBe(2);
    expect(entries[0].relativePath).toBe('friday-settings.json');
    expect(entries[1].relativePath).toBe('memory/long-term.json');
  });

  it('entries contain valid content hashes', async () => {
    mockStateFiles({ 'friday-settings.json': '{"test":true}' });
    const engine = await createEngine();
    const entries = await engine.enumerateState();
    expect(entries[0].contentHash).toMatch(/^[0-9a-f]{64}$/);
  });

  it('returns empty array when no state files exist', async () => {
    const engine = await createEngine();
    const entries = await engine.enumerateState();
    expect(entries).toHaveLength(0);
  });
});

// =====================================================================
// §4 — Full Export
// =====================================================================

describe('StateExportEngine — exportState', () => {
  it('rejects passphrase shorter than 8 characters', async () => {
    const engine = await createEngine();
    const result = await engine.exportState(SHORT_PASSPHRASE);
    expect(result.success).toBe(false);
    expect(result.error).toMatch(/at least 8 characters/);
  });

  it('rejects empty passphrase', async () => {
    const engine = await createEngine();
    const result = await engine.exportState('');
    expect(result.success).toBe(false);
    expect(result.error).toMatch(/at least 8 characters/);
  });

  it('exports successfully with valid passphrase', async () => {
    mockStateFiles({
      'friday-settings.json': '{"agentName":"Friday"}',
      'memory/long-term.json': '[{"fact":"test"}]',
    });
    const engine = await createEngine();
    const result = await engine.exportState(TEST_PASSPHRASE, '/tmp/out.friday-backup');
    expect(result.success).toBe(true);
    expect(result.manifest.fileCount).toBe(2);
    expect(result.durationMs).toBeGreaterThanOrEqual(0);
  });

  it('writes archive to the specified output path', async () => {
    mockStateFiles({ 'friday-settings.json': '{}' });
    const engine = await createEngine();
    await engine.exportState(TEST_PASSPHRASE, '/tmp/my-backup.friday-backup');
    expect(mockFs.writeFile).toHaveBeenCalledWith(
      '/tmp/my-backup.friday-backup',
      expect.any(String),
      'utf-8',
    );
  });

  it('archive JSON has correct format field', async () => {
    mockStateFiles({ 'friday-settings.json': '{}' });
    const engine = await createEngine();
    await engine.exportState(TEST_PASSPHRASE, '/tmp/format-test.friday-backup');

    const writeCall = mockFs.writeFile.mock.calls.find(
      (c: string[]) => c[0] === '/tmp/format-test.friday-backup',
    );
    expect(writeCall).toBeDefined();
    const archive: EncryptedArchive = JSON.parse(writeCall![1] as string);
    expect(archive.format).toBe('agent-friday-backup-v1');
    expect(archive.iterations).toBe(600_000); // Crypto Sprint 2: upgraded from 100K
  });

  it('records backup in history', async () => {
    mockStateFiles({ 'friday-settings.json': '{}' });
    const engine = await createEngine();
    await engine.exportState(TEST_PASSPHRASE, '/tmp/hist.friday-backup');
    const history = engine.getBackupHistory();
    expect(history).toHaveLength(1);
    expect(history[0].incremental).toBe(false);
    expect(history[0].archivePath).toBe('/tmp/hist.friday-backup');
  });

  it('manifest.incremental is false for full exports', async () => {
    mockStateFiles({ 'friday-settings.json': '{}' });
    const engine = await createEngine();
    const result = await engine.exportState(TEST_PASSPHRASE, '/tmp/full.friday-backup');
    expect(result.manifest.incremental).toBe(false);
  });

  it('reads agent name from settings', async () => {
    mockStateFiles({ 'friday-settings.json': '{"agentName":"MyAgent"}' });
    const engine = await createEngine();
    const result = await engine.exportState(TEST_PASSPHRASE, '/tmp/name.friday-backup');
    expect(result.manifest.agentName).toBe('MyAgent');
  });

  it('defaults agent name to Agent Friday if settings unreadable', async () => {
    // No files exist
    const engine = await createEngine();
    const result = await engine.exportState(TEST_PASSPHRASE, '/tmp/noname.friday-backup');
    // Even though export succeeds with 0 files, agent name still resolves
    expect(result.manifest.agentName).toBe('Agent Friday');
  });
});

// =====================================================================
// §5 — Archive Validation
// =====================================================================

describe('StateExportEngine — validateArchive', () => {
  it('validates a correctly-built archive', async () => {
    const { archiveJson } = buildTestArchive(TEST_PASSPHRASE, {
      'friday-settings.json': '{"ok":true}',
    });

    mockFs.readFile.mockImplementation(async (p: string) => {
      if (p.toString().includes('backup-history')) throw new Error('ENOENT');
      if (p.toString().includes('.backup-passphrase')) throw new Error('ENOENT');
      return archiveJson;
    });

    const engine = await createEngine();
    const result = await engine.validateArchive('/tmp/good.friday-backup', TEST_PASSPHRASE);
    expect(result.valid).toBe(true);
    expect(result.errors).toHaveLength(0);
    expect(result.manifest).not.toBeNull();
    expect(result.manifest!.fileCount).toBe(1);
  });

  it('rejects archive with wrong passphrase', async () => {
    const { archiveJson } = buildTestArchive(TEST_PASSPHRASE);

    mockFs.readFile.mockImplementation(async (p: string) => {
      if (p.toString().includes('backup-history')) throw new Error('ENOENT');
      if (p.toString().includes('.backup-passphrase')) throw new Error('ENOENT');
      return archiveJson;
    });

    const engine = await createEngine();
    const result = await engine.validateArchive('/tmp/wrong.friday-backup', 'wrong-password-here');
    expect(result.valid).toBe(false);
    expect(result.errors[0]).toMatch(/decryption failed|wrong passphrase/i);
  });

  it('rejects archive with tampered content hash', async () => {
    const { archiveJson } = buildTestArchive(TEST_PASSPHRASE, {
      'friday-settings.json': '{"test":1}',
    });

    // We need to tamper after encryption... Instead, build a custom malformed archive.
    // The simplest way: build a valid archive, decrypt, tamper the hash, re-encrypt
    const archive: EncryptedArchive = JSON.parse(archiveJson);
    const salt = Buffer.from(archive.salt, 'hex');
    const key = deriveKey(TEST_PASSPHRASE, salt, archive.iterations);
    const iv = Buffer.from(archive.iv, 'hex');
    const authTag = Buffer.from(archive.authTag, 'hex');
    const ct = Buffer.from(archive.ciphertext, 'base64');
    const decrypted = decryptPayload(ct, key, iv, authTag);
    const payload: ArchivePayload = JSON.parse(decrypted.toString());

    // Tamper the hash in manifest
    payload.manifest.files[0].contentHash = 'deadbeef'.repeat(8);

    // Re-encrypt with same key
    const newPlain = Buffer.from(JSON.stringify(payload));
    const enc = encryptPayload(newPlain, key);
    const tamperedArchive: EncryptedArchive = {
      ...archive,
      iv: enc.iv.toString('hex'),
      ciphertext: enc.ciphertext.toString('base64'),
      authTag: enc.authTag.toString('hex'),
    };

    mockFs.readFile.mockImplementation(async (p: string) => {
      if (p.toString().includes('backup-history')) throw new Error('ENOENT');
      if (p.toString().includes('.backup-passphrase')) throw new Error('ENOENT');
      return JSON.stringify(tamperedArchive);
    });

    const engine = await createEngine();
    const result = await engine.validateArchive('/tmp/tampered.friday-backup', TEST_PASSPHRASE);
    expect(result.valid).toBe(false);
    expect(result.errors.some(e => /hash mismatch|integrity/i.test(e))).toBe(true);
  });

  it('rejects unknown archive format', async () => {
    const fakeArchive = { format: 'unknown-format-v99', salt: '', iv: '', iterations: 1, ciphertext: '', authTag: '' };
    mockFs.readFile.mockImplementation(async (p: string) => {
      if (p.toString().includes('backup-history')) throw new Error('ENOENT');
      if (p.toString().includes('.backup-passphrase')) throw new Error('ENOENT');
      return JSON.stringify(fakeArchive);
    });

    const engine = await createEngine();
    const result = await engine.validateArchive('/tmp/bad-format.friday-backup', TEST_PASSPHRASE);
    expect(result.valid).toBe(false);
    expect(result.errors[0]).toMatch(/unknown format/i);
  });

  it('warns for incremental backups', async () => {
    const { archiveJson } = buildTestArchive(
      TEST_PASSPHRASE,
      { 'friday-settings.json': '{}' },
      { incremental: true, baseBackupTimestamp: 1000 },
    );

    mockFs.readFile.mockImplementation(async (p: string) => {
      if (p.toString().includes('backup-history')) throw new Error('ENOENT');
      if (p.toString().includes('.backup-passphrase')) throw new Error('ENOENT');
      return archiveJson;
    });

    const engine = await createEngine();
    const result = await engine.validateArchive('/tmp/inc.friday-backup', TEST_PASSPHRASE);
    expect(result.valid).toBe(true);
    expect(result.warnings.some(w => /incremental/i.test(w))).toBe(true);
  });

  it('reports missing files in archive', async () => {
    // Build archive where manifest declares a file but payload doesn't have it
    const salt = crypto.randomBytes(32);
    const key = deriveKey(TEST_PASSPHRASE, salt);
    const manifest: ArchiveManifest = {
      version: '1.0.0',
      createdAt: Date.now(),
      agentName: 'Test',
      fileCount: 1,
      totalSizeBytes: 10,
      integritySignature: computeIntegritySignature('abc', key),
      files: [{ relativePath: 'ghost.json', sizeBytes: 10, modifiedAt: Date.now(), contentHash: 'abc' }],
      incremental: false,
    };
    const payload: ArchivePayload = { manifest, files: {} }; // empty — file missing
    const plaintext = Buffer.from(JSON.stringify(payload));
    const enc = encryptPayload(plaintext, key);
    const archive: EncryptedArchive = {
      format: 'agent-friday-backup-v1',
      salt: salt.toString('hex'),
      iv: enc.iv.toString('hex'),
      iterations: 600_000, // Crypto Sprint 2: upgraded from 100K
      ciphertext: enc.ciphertext.toString('base64'),
      authTag: enc.authTag.toString('hex'),
    };

    mockFs.readFile.mockImplementation(async (p: string) => {
      if (p.toString().includes('backup-history')) throw new Error('ENOENT');
      if (p.toString().includes('.backup-passphrase')) throw new Error('ENOENT');
      return JSON.stringify(archive);
    });

    const engine = await createEngine();
    const result = await engine.validateArchive('/tmp/missing.friday-backup', TEST_PASSPHRASE);
    expect(result.valid).toBe(false);
    expect(result.errors.some(e => /missing file/i.test(e))).toBe(true);
  });
});

// =====================================================================
// §6 — Import / Restore (Fail-Closed)
// =====================================================================

describe('StateExportEngine — importState', () => {
  it('imports a valid archive successfully', async () => {
    const { archiveJson } = buildTestArchive(TEST_PASSPHRASE, {
      'friday-settings.json': '{"imported":true}',
      'memory/long-term.json': '[]',
    });

    mockFs.readFile.mockImplementation(async (p: string) => {
      if (p.toString().includes('backup-history')) throw new Error('ENOENT');
      if (p.toString().includes('.backup-passphrase')) throw new Error('ENOENT');
      return archiveJson;
    });

    const engine = await createEngine();
    const result = await engine.importState('/tmp/import.friday-backup', TEST_PASSPHRASE);
    expect(result.success).toBe(true);
    expect(result.filesRestored).toBe(2);
    expect(result.filesSkipped).toBe(0);
  });

  it('writes restored files to correct paths', async () => {
    const { archiveJson } = buildTestArchive(TEST_PASSPHRASE, {
      'friday-settings.json': '{"restored":true}',
    });

    mockFs.readFile.mockImplementation(async () => archiveJson);
    const engine = await createEngine();
    await engine.importState('/tmp/restore.friday-backup', TEST_PASSPHRASE);

    // Should have written the restored file
    const writeCalls = mockFs.writeFile.mock.calls;
    const restoreCall = writeCalls.find(
      (c: unknown[]) => typeof c[0] === 'string' && (c[0] as string).includes('friday-settings.json'),
    );
    expect(restoreCall).toBeDefined();
  });

  it('creates parent directories for nested files', async () => {
    const { archiveJson } = buildTestArchive(TEST_PASSPHRASE, {
      'memory/long-term.json': '[]',
    });

    mockFs.readFile.mockImplementation(async () => archiveJson);
    const engine = await createEngine();
    await engine.importState('/tmp/nested.friday-backup', TEST_PASSPHRASE);

    // mkdir should be called for memory/ parent dir
    const mkdirCalls = mockFs.mkdir.mock.calls.map((c: unknown[]) => c[0]);
    const hasMemoryDir = mkdirCalls.some((p: string) =>
      p.includes('memory'),
    );
    expect(hasMemoryDir).toBe(true);
  });

  it('refuses import with wrong passphrase (fail-closed)', async () => {
    const { archiveJson } = buildTestArchive(TEST_PASSPHRASE, {
      'friday-settings.json': '{"secret":"data"}',
    });

    mockFs.readFile.mockImplementation(async (p: string) => {
      if (p.toString().includes('backup-history')) throw new Error('ENOENT');
      if (p.toString().includes('.backup-passphrase')) throw new Error('ENOENT');
      return archiveJson;
    });

    const engine = await createEngine();
    const result = await engine.importState('/tmp/bad-pass.friday-backup', 'wrong-passphrase-123');
    expect(result.success).toBe(false);
    expect(result.filesRestored).toBe(0);
    expect(result.error).toMatch(/decryption failed|wrong passphrase/i);
  });

  it('writes ZERO bytes if validation fails', async () => {
    const fakeArchive = { format: 'agent-friday-backup-v1', salt: 'aa', iv: 'bb', iterations: 1, ciphertext: 'cc', authTag: 'dd' };
    mockFs.readFile.mockImplementation(async (p: string) => {
      if (p.toString().includes('backup-history')) throw new Error('ENOENT');
      if (p.toString().includes('.backup-passphrase')) throw new Error('ENOENT');
      return JSON.stringify(fakeArchive);
    });

    const engine = await createEngine();
    const result = await engine.importState('/tmp/corrupt.friday-backup', TEST_PASSPHRASE);
    expect(result.success).toBe(false);

    // No state files should have been written (only backup-history/mkdir are allowed)
    const writeCalls = mockFs.writeFile.mock.calls.filter(
      (c: unknown[]) => typeof c[0] === 'string' && !(c[0] as string).includes('backup-history'),
    );
    expect(writeCalls.length).toBe(0);
  });
});

// =====================================================================
// §7 — Incremental Export
// =====================================================================

describe('StateExportEngine — exportIncremental', () => {
  it('falls back to full export when no previous backup exists', async () => {
    mockStateFiles({ 'friday-settings.json': '{}' });
    const engine = await createEngine();
    const result = await engine.exportIncremental(TEST_PASSPHRASE, '/tmp/inc-full.friday-backup');
    expect(result.success).toBe(true);
    // Falls back to full — manifest.incremental should be false
    expect(result.manifest.incremental).toBe(false);
  });

  it('rejects short passphrase', async () => {
    const engine = await createEngine();
    const result = await engine.exportIncremental(SHORT_PASSPHRASE);
    expect(result.success).toBe(false);
    expect(result.error).toMatch(/at least 8 characters/);
  });

  it('only includes files modified since last backup', async () => {
    const oldTimestamp = Date.now() - 100_000;
    const newTimestamp = Date.now();

    // First, seed a backup in history
    mockStateFiles({ 'friday-settings.json': '{}' });
    const engine = await createEngine();
    await engine.exportState(TEST_PASSPHRASE, '/tmp/base.friday-backup');

    // Now mock files: one old (before backup), one new (after backup)
    const backupTs = engine.getLastBackup()!.timestamp;

    mockFs.stat.mockImplementation(async (p: string) => {
      if (p.toString().includes('friday-settings.json')) {
        return { size: 2, mtimeMs: backupTs - 10000 }; // OLD — unchanged
      }
      if (p.toString().includes('long-term.json')) {
        return { size: 10, mtimeMs: backupTs + 5000 }; // NEW — changed
      }
      throw new Error('ENOENT');
    });
    mockFs.readFile.mockImplementation(async (p: string, enc?: string) => {
      if (p.toString().includes('backup-history')) throw new Error('ENOENT');
      if (p.toString().includes('.backup-passphrase')) throw new Error('ENOENT');
      if (p.toString().includes('friday-settings.json')) {
        return enc === 'utf-8' ? '{}' : Buffer.from('{}');
      }
      if (p.toString().includes('long-term.json')) {
        return enc === 'utf-8' ? '["new"]' : Buffer.from('["new"]');
      }
      throw new Error('ENOENT');
    });

    const result = await engine.exportIncremental(TEST_PASSPHRASE, '/tmp/inc.friday-backup');
    expect(result.success).toBe(true);
    expect(result.manifest.incremental).toBe(true);
    expect(result.manifest.fileCount).toBe(1); // Only the changed file
    expect(result.manifest.files[0].relativePath).toBe('memory/long-term.json');
  });

  it('reports no changes if all files are older than last backup', async () => {
    mockStateFiles({ 'friday-settings.json': '{}' });
    const engine = await createEngine();
    await engine.exportState(TEST_PASSPHRASE, '/tmp/base2.friday-backup');

    // Now all files have mtimeMs BEFORE the backup
    const backupTs = engine.getLastBackup()!.timestamp;
    mockFs.stat.mockImplementation(async (p: string) => {
      if (p.toString().includes('friday-settings.json')) {
        return { size: 2, mtimeMs: backupTs - 50000 };
      }
      throw new Error('ENOENT');
    });
    mockFs.readFile.mockImplementation(async (p: string) => {
      if (p.toString().includes('backup-history')) throw new Error('ENOENT');
      if (p.toString().includes('.backup-passphrase')) throw new Error('ENOENT');
      if (p.toString().includes('friday-settings.json')) return Buffer.from('{}');
      throw new Error('ENOENT');
    });

    const result = await engine.exportIncremental(TEST_PASSPHRASE, '/tmp/inc-none.friday-backup');
    expect(result.success).toBe(true);
    expect(result.error).toMatch(/no files changed/i);
  });
});

// =====================================================================
// §8 — Backup History & Pruning
// =====================================================================

describe('StateExportEngine — backup history', () => {
  it('getLastBackup returns null with no history', async () => {
    const engine = await createEngine();
    expect(engine.getLastBackup()).toBeNull();
  });

  it('getLastBackup returns most recent record', async () => {
    mockStateFiles({ 'friday-settings.json': '{}' });
    const engine = await createEngine();

    await engine.exportState(TEST_PASSPHRASE, '/tmp/b1.friday-backup');
    await engine.exportState(TEST_PASSPHRASE, '/tmp/b2.friday-backup');

    const last = engine.getLastBackup();
    expect(last).not.toBeNull();
    expect(last!.archivePath).toBe('/tmp/b2.friday-backup');
  });

  it('returns defensive copy of history', async () => {
    mockStateFiles({ 'friday-settings.json': '{}' });
    const engine = await createEngine();
    await engine.exportState(TEST_PASSPHRASE, '/tmp/def.friday-backup');

    const h1 = engine.getBackupHistory();
    h1.push({} as BackupRecord);
    const h2 = engine.getBackupHistory();
    expect(h2.length).toBe(1);
  });

  it('prunes oldest backups when exceeding maxBackupCount', async () => {
    mockStateFiles({ 'friday-settings.json': '{}' });
    const engine = await createEngine({ maxBackupCount: 2 });

    await engine.exportState(TEST_PASSPHRASE, '/tmp/p1.friday-backup');
    await engine.exportState(TEST_PASSPHRASE, '/tmp/p2.friday-backup');
    await engine.exportState(TEST_PASSPHRASE, '/tmp/p3.friday-backup');

    const history = engine.getBackupHistory();
    expect(history.length).toBeLessThanOrEqual(2);
    // Oldest (p1) should have been pruned
    expect(history.some(h => h.archivePath === '/tmp/p1.friday-backup')).toBe(false);
  });

  it('deletes pruned archive files from disk', async () => {
    mockStateFiles({ 'friday-settings.json': '{}' });
    const engine = await createEngine({ maxBackupCount: 1 });

    await engine.exportState(TEST_PASSPHRASE, '/tmp/old.friday-backup');
    await engine.exportState(TEST_PASSPHRASE, '/tmp/new.friday-backup');

    expect(mockFs.unlink).toHaveBeenCalledWith('/tmp/old.friday-backup');
  });
});

// =====================================================================
// §9 — Configuration
// =====================================================================

describe('StateExportEngine — config', () => {
  it('getConfig returns a copy (not reference)', async () => {
    const engine = await createEngine();
    const c1 = engine.getConfig();
    c1.maxBackupCount = 999;
    const c2 = engine.getConfig();
    expect(c2.maxBackupCount).toBe(7);
  });

  it('updateConfig merges partial config', async () => {
    const engine = await createEngine();
    const updated = engine.updateConfig({ autoBackupEnabled: true, maxBackupCount: 3 });
    expect(updated.autoBackupEnabled).toBe(true);
    expect(updated.maxBackupCount).toBe(3);
    expect(updated.incrementalEnabled).toBe(true); // unchanged
  });

  it('updateConfig triggers save', async () => {
    const engine = await createEngine();
    engine.updateConfig({ autoBackupEnabled: true });
    // Give the queue a tick to flush
    await new Promise(r => setTimeout(r, 50));
    expect(mockFs.writeFile).toHaveBeenCalledWith(
      expect.stringContaining('backup-history.json'),
      expect.any(String),
      'utf-8',
    );
  });
});

// =====================================================================
// §10 — Prompt Context
// =====================================================================

describe('StateExportEngine — getPromptContext', () => {
  it('returns encouragement message when no backups exist', async () => {
    const engine = await createEngine();
    const ctx = engine.getPromptContext();
    expect(ctx).toContain('No backups');
    expect(ctx).toContain('[STATE PERSISTENCE]');
  });

  it('includes time-since-last-backup after an export', async () => {
    mockStateFiles({ 'friday-settings.json': '{}' });
    const engine = await createEngine();
    await engine.exportState(TEST_PASSPHRASE, '/tmp/ctx.friday-backup');

    const ctx = engine.getPromptContext();
    expect(ctx).toContain('Last backup:');
    expect(ctx).toContain('1 files');
  });

  it('shows auto-backup status', async () => {
    const engine = await createEngine({ autoBackupEnabled: true });
    mockStateFiles({ 'friday-settings.json': '{}' });
    await engine.exportState(TEST_PASSPHRASE, '/tmp/auto-ctx.friday-backup');

    const ctx = engine.getPromptContext();
    expect(ctx).toContain('Auto-backup: ON');
  });

  it('shows warning for backups older than 7 days', async () => {
    const engine = await createEngine();
    // Manually inject an old backup record
    const oldRecord: BackupRecord = {
      id: 'old-1',
      timestamp: Date.now() - 8 * 24 * 60 * 60 * 1000, // 8 days ago
      archivePath: '/tmp/old.friday-backup',
      fileCount: 5,
      totalSizeBytes: 1000,
      incremental: false,
      durationMs: 100,
    };
    // Hack: access private via any
    (engine as any).backupHistory.push(oldRecord);

    const ctx = engine.getPromptContext();
    expect(ctx).toContain('over a week old');
  });
});

// =====================================================================
// §11 — Continuity Readiness
// =====================================================================

describe('StateExportEngine — checkContinuityReadiness', () => {
  it('reports NOT ready when critical files missing', async () => {
    // Default: all files throw ENOENT
    const engine = await createEngine();
    const { ready, issues } = await engine.checkContinuityReadiness();
    expect(ready).toBe(false);
    expect(issues.length).toBeGreaterThan(0);
    expect(issues.some(i => i.includes('friday-settings.json'))).toBe(true);
  });

  it('reports ready when critical files exist', async () => {
    mockFs.access.mockResolvedValue(undefined);
    const engine = await createEngine();
    const { ready, issues } = await engine.checkContinuityReadiness();
    expect(ready).toBe(true);
    expect(issues).toHaveLength(0);
  });

  it('lists specific missing critical files', async () => {
    // Only settings exists
    mockFs.access.mockImplementation(async (p: string) => {
      if (p.toString().includes('friday-settings.json')) return undefined;
      throw new Error('ENOENT');
    });

    const engine = await createEngine();
    const { ready, issues } = await engine.checkContinuityReadiness();
    expect(ready).toBe(false);
    expect(issues.some(i => i.includes('long-term.json'))).toBe(true);
    expect(issues.some(i => i.includes('trust-graph.json'))).toBe(true);
    expect(issues.some(i => i.includes('friday-settings.json'))).toBe(false);
  });
});

// =====================================================================
// §12 — Scheduled Backup
// =====================================================================

describe('StateExportEngine — scheduled backups', () => {
  it('runScheduledBackup fails without stored passphrase', async () => {
    const engine = await createEngine();
    const result = await engine.runScheduledBackup();
    expect(result.success).toBe(false);
    expect(result.error).toMatch(/no auto-backup passphrase/i);
  });

  it('clearAutoBackupPassphrase removes passphrase file', async () => {
    const engine = await createEngine();
    await engine.clearAutoBackupPassphrase();
    expect(mockFs.unlink).toHaveBeenCalledWith(
      expect.stringContaining('.backup-passphrase'),
    );
    expect(engine.getConfig().autoBackupPassphraseSet).toBe(false);
  });
});

// =====================================================================
// §13 — cLaw Compliance
// =====================================================================

describe('StateExportEngine — cLaw compliance', () => {
  it('archives are ALWAYS encrypted (no plaintext export)', async () => {
    mockStateFiles({ 'friday-settings.json': '{"private":"data"}' });
    const engine = await createEngine();
    await engine.exportState(TEST_PASSPHRASE, '/tmp/claw.friday-backup');

    const writeCall = mockFs.writeFile.mock.calls.find(
      (c: string[]) => c[0] === '/tmp/claw.friday-backup',
    );
    const archiveStr = writeCall![1] as string;
    // Should NOT contain the plaintext anywhere
    expect(archiveStr).not.toContain('"private"');
    expect(archiveStr).not.toContain('"data"');
    // Should contain the encrypted format marker
    expect(archiveStr).toContain('agent-friday-backup-v1');
    expect(archiveStr).toContain('ciphertext');
  });

  it('wrong passphrase returns zero data (no partial leaks)', async () => {
    const { archiveJson } = buildTestArchive(TEST_PASSPHRASE, {
      'friday-settings.json': '{"sensitive":"info"}',
    });

    mockFs.readFile.mockImplementation(async (p: string) => {
      if (p.toString().includes('backup-history')) throw new Error('ENOENT');
      if (p.toString().includes('.backup-passphrase')) throw new Error('ENOENT');
      return archiveJson;
    });

    const engine = await createEngine();
    const result = await engine.importState('/tmp/claw2.friday-backup', 'bad-password-attempt');
    expect(result.success).toBe(false);
    expect(result.filesRestored).toBe(0);
  });

  it('state file list excludes in-memory-only engines', async () => {
    const engine = await createEngine();
    const paths = engine.getStateFilePaths();
    // context-graph and context-stream are in-memory only
    for (const p of paths) {
      expect(p).not.toMatch(/context-graph/);
      expect(p).not.toMatch(/context-stream/);
    }
  });

  it('FutureSpeak has zero access: no external URLs in archive', async () => {
    mockStateFiles({ 'friday-settings.json': '{}' });
    const engine = await createEngine();
    await engine.exportState(TEST_PASSPHRASE, '/tmp/offline.friday-backup');

    const writeCall = mockFs.writeFile.mock.calls.find(
      (c: string[]) => c[0] === '/tmp/offline.friday-backup',
    );
    const archiveStr = writeCall![1] as string;
    expect(archiveStr).not.toMatch(/https?:\/\//);
  });
});

// =====================================================================
// §14 — Round-Trip Integration (Export → Validate → Import)
// =====================================================================

describe('StateExportEngine — round-trip', () => {
  it('export → validate → import round-trips cleanly', async () => {
    const testFiles = {
      'friday-settings.json': '{"agentName":"RoundTrip","theme":"dark"}',
      'memory/long-term.json': '[{"fact":"I like tests","confidence":0.9}]',
      'trust-graph.json': '{"persons":[]}',
    };

    // Phase 1: Export
    mockStateFiles(testFiles);
    const engine = await createEngine();
    const exportResult = await engine.exportState(TEST_PASSPHRASE, '/tmp/roundtrip.friday-backup');
    expect(exportResult.success).toBe(true);
    expect(exportResult.manifest.fileCount).toBe(3);

    // Capture the written archive
    const writeCall = mockFs.writeFile.mock.calls.find(
      (c: string[]) => c[0] === '/tmp/roundtrip.friday-backup',
    );
    const archiveJson = writeCall![1] as string;

    // Phase 2: Validate
    mockFs.readFile.mockImplementation(async (p: string) => {
      if (p.toString().includes('backup-history')) throw new Error('ENOENT');
      if (p.toString().includes('.backup-passphrase')) throw new Error('ENOENT');
      return archiveJson;
    });

    const engine2 = await createEngine();
    const validation = await engine2.validateArchive('/tmp/roundtrip.friday-backup', TEST_PASSPHRASE);
    expect(validation.valid).toBe(true);
    expect(validation.errors).toHaveLength(0);

    // Phase 3: Import
    const importResult = await engine2.importState('/tmp/roundtrip.friday-backup', TEST_PASSPHRASE);
    expect(importResult.success).toBe(true);
    expect(importResult.filesRestored).toBe(3);
  });
});

// =====================================================================
// §15 — Lifecycle
// =====================================================================

describe('StateExportEngine — lifecycle', () => {
  it('stop() does not throw', async () => {
    const engine = await createEngine();
    expect(() => engine.stop()).not.toThrow();
  });
});
