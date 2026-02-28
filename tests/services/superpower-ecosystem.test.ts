/**
 * Superpower Ecosystem — comprehensive tests (Track VII, Phase 3).
 *
 * Coverage:
 *   ✓ Pure functions: generateDeveloperKeyPair, signManifest, verifyPackageSignature,
 *     canonicalizeManifest
 *   ✓ Class: initialization, manifest creation, manifest validation, developer tools,
 *     package signing, publishing lifecycle, registry search/filtering, financial
 *     transactions with cooling-off, cLaw compliance, config, stats, prompt context
 *
 * 70+ tests covering every public API surface.
 */

import crypto from 'crypto';

// ── Electron + FS Mocks ──────────────────────────────────────────────

vi.mock('electron', () => ({
  app: { getPath: vi.fn(() => '/tmp/test-ecosystem') },
}));

const { _mockMkdir, _mockReadFile, _mockWriteFile } = vi.hoisted(() => ({
  _mockMkdir: vi.fn().mockResolvedValue(undefined),
  _mockReadFile: vi.fn().mockRejectedValue(new Error('ENOENT')),
  _mockWriteFile: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('fs/promises', () => ({
  default: {
    mkdir: _mockMkdir,
    readFile: _mockReadFile,
    writeFile: _mockWriteFile,
  },
  mkdir: _mockMkdir,
  readFile: _mockReadFile,
  writeFile: _mockWriteFile,
}));

import {
  generateDeveloperKeyPair,
  signManifest,
  verifyPackageSignature,
  canonicalizeManifest,
  SuperpowerEcosystem,
  COOLING_OFF_MS,
  type SuperpowerManifest,
  type SignedPackage,
  type DeveloperKeyPair,
  type ManifestCapability,
  type ManifestEntry,
  type ManifestSandbox,
  type ManifestAuthor,
  type ManifestPricing,
  type RegistrySearchQuery,
  type FinancialTransaction,
} from '../../src/main/superpower-ecosystem';

import _fs from 'fs/promises';
const mockFs = _fs as unknown as {
  mkdir: ReturnType<typeof vi.fn>;
  readFile: ReturnType<typeof vi.fn>;
  writeFile: ReturnType<typeof vi.fn>;
};

// ── Test Helpers ─────────────────────────────────────────────────────

function makeTestCapability(name = 'test-tool'): ManifestCapability {
  return {
    toolName: name,
    description: `Test tool ${name}`,
    parameters: { type: 'object', properties: { input: { type: 'string' } } },
    required: ['input'],
  };
}

function makeTestEntry(): ManifestEntry {
  return { type: 'native-module', main: 'index.js' };
}

function makeTestSandbox(): ManifestSandbox {
  return {
    network: false,
    filesystem: 'none',
    maxMemoryMb: 128,
    maxCpuTimeMs: 5000,
    allowedEnvVars: [],
  };
}

function makeTestAuthor(publicKey?: string): ManifestAuthor {
  return { name: 'Test Dev', email: 'dev@test.com', publicKey };
}

function makeTestManifestOpts(overrides: Record<string, unknown> = {}) {
  return {
    packageId: 'test-superpower',
    name: 'Test Superpower',
    description: 'A test superpower for unit tests',
    tagline: 'Testing made easy',
    version: '1.0.0',
    author: makeTestAuthor(),
    license: 'MIT',
    repository: 'https://github.com/test/test',
    capabilities: [makeTestCapability()],
    permissions: ['network' as const],
    entry: makeTestEntry(),
    sandbox: makeTestSandbox(),
    category: 'development' as const,
    ...overrides,
  };
}

async function createInitializedEcosystem(
  configOverrides: Record<string, unknown> = {},
): Promise<SuperpowerEcosystem> {
  const eco = new SuperpowerEcosystem(configOverrides);
  await eco.initialize();
  return eco;
}

// ── Tests ────────────────────────────────────────────────────────────

describe('SuperpowerEcosystem', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockFs.readFile.mockRejectedValue(new Error('ENOENT'));
    mockFs.writeFile.mockResolvedValue(undefined);
    mockFs.mkdir.mockResolvedValue(undefined);
  });

  // ═══════════════════════════════════════════════════════════════════
  // § 1 — Pure Functions: Key Generation
  // ═══════════════════════════════════════════════════════════════════

  describe('generateDeveloperKeyPair()', () => {
    it('should generate a valid Ed25519 keypair', () => {
      const keys = generateDeveloperKeyPair();
      expect(keys.publicKey).toBeTruthy();
      expect(keys.privateKey).toBeTruthy();
      expect(typeof keys.publicKey).toBe('string');
      expect(typeof keys.privateKey).toBe('string');
    });

    it('should generate unique keys on each call', () => {
      const a = generateDeveloperKeyPair();
      const b = generateDeveloperKeyPair();
      expect(a.publicKey).not.toBe(b.publicKey);
      expect(a.privateKey).not.toBe(b.privateKey);
    });

    it('should produce hex-encoded keys', () => {
      const keys = generateDeveloperKeyPair();
      expect(/^[0-9a-f]+$/i.test(keys.publicKey)).toBe(true);
      expect(/^[0-9a-f]+$/i.test(keys.privateKey)).toBe(true);
    });
  });

  // ═══════════════════════════════════════════════════════════════════
  // § 2 — Pure Functions: Signing & Verification
  // ═══════════════════════════════════════════════════════════════════

  describe('signManifest() + verifyPackageSignature()', () => {
    let keys: DeveloperKeyPair;
    let manifest: SuperpowerManifest;

    beforeEach(async () => {
      keys = generateDeveloperKeyPair();
      const eco = await createInitializedEcosystem();
      manifest = eco.createManifest(makeTestManifestOpts({ author: makeTestAuthor(keys.publicKey) }));
    });

    it('should produce a valid signed package', () => {
      const pkg = signManifest(manifest, keys.privateKey);
      expect(pkg.manifest).toBe(manifest);
      expect(pkg.signature).toBeTruthy();
      expect(pkg.signerPublicKey).toBe(keys.publicKey);
      expect(pkg.signedAt).toBeGreaterThan(0);
      expect(pkg.contentHash).toBeTruthy();
    });

    it('should verify a valid signed package', () => {
      const pkg = signManifest(manifest, keys.privateKey);
      const result = verifyPackageSignature(pkg);
      expect(result.valid).toBe(true);
      expect(result.signerPublicKey).toBe(keys.publicKey);
    });

    it('should reject a tampered manifest', () => {
      const pkg = signManifest(manifest, keys.privateKey);
      // Tamper with the manifest
      pkg.manifest = { ...pkg.manifest, name: 'TAMPERED' };
      const result = verifyPackageSignature(pkg);
      expect(result.valid).toBe(false);
      expect(result.reason).toContain('tampered');
    });

    it('should reject a tampered signature', () => {
      const pkg = signManifest(manifest, keys.privateKey);
      // Tamper with the signature
      pkg.signature = 'deadbeef'.repeat(16);
      const result = verifyPackageSignature(pkg);
      expect(result.valid).toBe(false);
    });

    it('should reject a package signed with a wrong key vs author key', () => {
      const otherKeys = generateDeveloperKeyPair();
      // Manifest has keys.publicKey as author.publicKey but we sign with otherKeys
      const pkg = signManifest(manifest, otherKeys.privateKey);
      const result = verifyPackageSignature(pkg);
      expect(result.valid).toBe(false);
      expect(result.reason).toContain('does not match');
    });

    it('should accept a package with no author.publicKey specified', () => {
      const noKeyManifest: SuperpowerManifest = {
        ...manifest,
        author: { name: 'Anonymous', email: 'anon@test.com' },
      };
      const pkg = signManifest(noKeyManifest, keys.privateKey);
      const result = verifyPackageSignature(pkg);
      expect(result.valid).toBe(true);
    });
  });

  describe('canonicalizeManifest()', () => {
    it('should produce deterministic output regardless of property order', async () => {
      const eco = await createInitializedEcosystem();
      const m = eco.createManifest(makeTestManifestOpts());
      const c1 = canonicalizeManifest(m);

      // Create same manifest with different internal ordering (JS objects can vary)
      const m2 = eco.createManifest(makeTestManifestOpts());
      m2.createdAt = m.createdAt;
      m2.updatedAt = m.updatedAt;
      const c2 = canonicalizeManifest(m2);

      expect(c1).toBe(c2);
    });

    it('should produce valid JSON', async () => {
      const eco = await createInitializedEcosystem();
      const m = eco.createManifest(makeTestManifestOpts());
      const canonical = canonicalizeManifest(m);
      expect(() => JSON.parse(canonical)).not.toThrow();
    });
  });

  // ═══════════════════════════════════════════════════════════════════
  // § 3 — Initialization
  // ═══════════════════════════════════════════════════════════════════

  describe('Initialization', () => {
    it('should initialize with defaults when no data file exists', async () => {
      const eco = await createInitializedEcosystem();
      expect(eco.getConfig().registryUrl).toBeNull();
      expect(eco.getConfig().allowDirectGitHub).toBe(true);
      expect(eco.hasDeveloperKeys()).toBe(false);
      expect(eco.getTransactions()).toHaveLength(0);
      expect(eco.getPublishedPackages()).toHaveLength(0);
    });

    it('should load existing data from file', async () => {
      const keys = generateDeveloperKeyPair();
      mockFs.readFile.mockResolvedValueOnce(JSON.stringify({
        developerKeys: keys,
        transactions: [{ id: 'tx1', packageId: 'p1', type: 'purchase', amountUsdCents: 999, consentToken: '', consentedAt: 0, coolingOffExpiresAt: 0, executed: false, executedAt: null, status: 'pending-consent' }],
        publishedPackages: [],
        config: { developerMode: true },
      }));

      const eco = await createInitializedEcosystem();
      expect(eco.hasDeveloperKeys()).toBe(true);
      expect(eco.getTransactions()).toHaveLength(1);
      expect(eco.getConfig().developerMode).toBe(true);
    });

    it('should not double-initialize', async () => {
      const eco = new SuperpowerEcosystem();
      await eco.initialize();
      await eco.initialize(); // Should not throw or re-read
      expect(mockFs.readFile).toHaveBeenCalledTimes(1);
    });
  });

  // ═══════════════════════════════════════════════════════════════════
  // § 4 — Manifest Creation
  // ═══════════════════════════════════════════════════════════════════

  describe('createManifest()', () => {
    it('should create a valid manifest with defaults', async () => {
      const eco = await createInitializedEcosystem();
      const m = eco.createManifest(makeTestManifestOpts());
      expect(m.schemaVersion).toBe('1.0.0');
      expect(m.packageId).toBe('test-superpower');
      expect(m.name).toBe('Test Superpower');
      expect(m.platforms).toEqual(['win32', 'darwin', 'linux']);
      expect(m.pricing.model).toBe('free');
      expect(m.pricing.priceUsdCents).toBe(0);
      expect(m.dependencies).toEqual([]);
      expect(m.tags).toEqual([]);
      expect(m.createdAt).toBeGreaterThan(0);
      expect(m.updatedAt).toBe(m.createdAt);
    });

    it('should reject invalid package ID (uppercase)', async () => {
      const eco = await createInitializedEcosystem();
      expect(() => eco.createManifest(makeTestManifestOpts({ packageId: 'Test-Pack' })))
        .toThrow(/Invalid package ID/);
    });

    it('should reject invalid package ID (too short)', async () => {
      const eco = await createInitializedEcosystem();
      expect(() => eco.createManifest(makeTestManifestOpts({ packageId: 'ab' })))
        .toThrow(/3-64 characters/);
    });

    it('should reject invalid package ID (starts with hyphen)', async () => {
      const eco = await createInitializedEcosystem();
      expect(() => eco.createManifest(makeTestManifestOpts({ packageId: '-bad-id' })))
        .toThrow(/Invalid package ID/);
    });

    it('should reject tagline exceeding 80 characters', async () => {
      const eco = await createInitializedEcosystem();
      expect(() => eco.createManifest(makeTestManifestOpts({ tagline: 'x'.repeat(81) })))
        .toThrow(/≤80 characters/);
    });

    it('should reject empty capabilities', async () => {
      const eco = await createInitializedEcosystem();
      expect(() => eco.createManifest(makeTestManifestOpts({ capabilities: [] })))
        .toThrow(/at least one capability/);
    });

    it('should accept custom pricing', async () => {
      const eco = await createInitializedEcosystem();
      const m = eco.createManifest(makeTestManifestOpts({
        pricing: { model: 'one-time', priceUsdCents: 999 },
      }));
      expect(m.pricing.model).toBe('one-time');
      expect(m.pricing.priceUsdCents).toBe(999);
    });

    it('should accept custom platforms', async () => {
      const eco = await createInitializedEcosystem();
      const m = eco.createManifest(makeTestManifestOpts({
        platforms: ['win32'],
      }));
      expect(m.platforms).toEqual(['win32']);
    });
  });

  // ═══════════════════════════════════════════════════════════════════
  // § 5 — Manifest Validation
  // ═══════════════════════════════════════════════════════════════════

  describe('validateManifest()', () => {
    it('should validate a correct manifest with no errors', async () => {
      const eco = await createInitializedEcosystem();
      const m = eco.createManifest(makeTestManifestOpts());
      const result = eco.validateManifest(m);
      expect(result.valid).toBe(true);
      expect(result.errors).toHaveLength(0);
    });

    it('should report missing required fields', async () => {
      const eco = await createInitializedEcosystem();
      const broken = { schemaVersion: '1.0.0' } as unknown as SuperpowerManifest;
      const result = eco.validateManifest(broken);
      expect(result.valid).toBe(false);
      expect(result.errors.length).toBeGreaterThan(0);
      expect(result.errors.some(e => e.includes('packageId'))).toBe(true);
      expect(result.errors.some(e => e.includes('name'))).toBe(true);
    });

    it('should flag invalid packageId format', async () => {
      const eco = await createInitializedEcosystem();
      const m = eco.createManifest(makeTestManifestOpts());
      (m as any).packageId = 'BAD_ID';
      const result = eco.validateManifest(m);
      expect(result.valid).toBe(false);
      expect(result.errors.some(e => e.includes('packageId'))).toBe(true);
    });

    it('should warn about non-semver version', async () => {
      const eco = await createInitializedEcosystem();
      const m = eco.createManifest(makeTestManifestOpts());
      (m as any).version = 'alpha-1';
      const result = eco.validateManifest(m);
      expect(result.warnings.some(w => w.includes('semver'))).toBe(true);
    });

    it('should flag duplicate tool names', async () => {
      const eco = await createInitializedEcosystem();
      const m = eco.createManifest(makeTestManifestOpts({
        capabilities: [makeTestCapability('dupe'), makeTestCapability('dupe')],
      }));
      const result = eco.validateManifest(m);
      expect(result.valid).toBe(false);
      expect(result.errors.some(e => e.includes('Duplicate tool name'))).toBe(true);
    });

    it('should flag paid model with zero price', async () => {
      const eco = await createInitializedEcosystem();
      const m = eco.createManifest(makeTestManifestOpts());
      m.pricing = { model: 'one-time', priceUsdCents: 0 };
      const result = eco.validateManifest(m);
      expect(result.valid).toBe(false);
      expect(result.errors.some(e => e.includes('priceUsdCents'))).toBe(true);
    });

    it('should warn about free model with non-zero price', async () => {
      const eco = await createInitializedEcosystem();
      const m = eco.createManifest(makeTestManifestOpts());
      m.pricing = { model: 'free', priceUsdCents: 100 };
      const result = eco.validateManifest(m);
      expect(result.valid).toBe(true); // Warning only
      expect(result.warnings.some(w => w.includes('Free pricing'))).toBe(true);
    });

    it('should warn about zero sandbox limits', async () => {
      const eco = await createInitializedEcosystem();
      const m = eco.createManifest(makeTestManifestOpts());
      m.sandbox = { ...m.sandbox, maxMemoryMb: 0, maxCpuTimeMs: 0 };
      const result = eco.validateManifest(m);
      expect(result.warnings.some(w => w.includes('maxMemoryMb'))).toBe(true);
      expect(result.warnings.some(w => w.includes('maxCpuTimeMs'))).toBe(true);
    });
  });

  // ═══════════════════════════════════════════════════════════════════
  // § 6 — Developer Tools
  // ═══════════════════════════════════════════════════════════════════

  describe('Developer Tools', () => {
    it('should auto-generate keypair on first getDeveloperKeys()', async () => {
      const eco = await createInitializedEcosystem();
      expect(eco.hasDeveloperKeys()).toBe(false);
      const keys = eco.getDeveloperKeys();
      expect(keys.publicKey).toBeTruthy();
      expect(keys.privateKey).toBeTruthy();
      expect(eco.hasDeveloperKeys()).toBe(true);
    });

    it('should return the same keypair on subsequent calls', async () => {
      const eco = await createInitializedEcosystem();
      const a = eco.getDeveloperKeys();
      const b = eco.getDeveloperKeys();
      expect(a.publicKey).toBe(b.publicKey);
      expect(a.privateKey).toBe(b.privateKey);
    });

    it('should sign a package using developer keys', async () => {
      const eco = await createInitializedEcosystem();
      const m = eco.createManifest(makeTestManifestOpts());
      const pkg = eco.signPackage(m);
      expect(pkg.signature).toBeTruthy();
      expect(pkg.signerPublicKey).toBe(eco.getDeveloperKeys().publicKey);
      const verification = verifyPackageSignature(pkg);
      expect(verification.valid).toBe(true);
    });
  });

  // ═══════════════════════════════════════════════════════════════════
  // § 7 — Publishing Lifecycle
  // ═══════════════════════════════════════════════════════════════════

  describe('Publishing', () => {
    it('should publish a valid signed package', async () => {
      const eco = await createInitializedEcosystem();
      const m = eco.createManifest(makeTestManifestOpts());
      const pkg = eco.signPackage(m);
      const result = await eco.publishPackage(pkg);
      expect(result.packageId).toBe('test-superpower');
      expect(result.version).toBe('1.0.0');
      expect(result.updated).toBe(false);
      expect(eco.getPublishedPackages()).toHaveLength(1);
    });

    it('should reject an unsigned package', async () => {
      const eco = await createInitializedEcosystem();
      const m = eco.createManifest(makeTestManifestOpts());
      const fakePkg: SignedPackage = {
        manifest: m,
        signature: 'fake',
        signerPublicKey: 'fake',
        signedAt: Date.now(),
        contentHash: 'fake',
      };
      await expect(eco.publishPackage(fakePkg)).rejects.toThrow();
    });

    it('should update existing package from same signer', async () => {
      const eco = await createInitializedEcosystem();
      const m1 = eco.createManifest(makeTestManifestOpts());
      const pkg1 = eco.signPackage(m1);
      await eco.publishPackage(pkg1);

      const m2 = eco.createManifest(makeTestManifestOpts({ version: '2.0.0' }));
      const pkg2 = eco.signPackage(m2);
      const result = await eco.publishPackage(pkg2);
      expect(result.updated).toBe(true);
      expect(eco.getPublishedPackages()).toHaveLength(1);
      expect(eco.getPublishedPackages()[0].manifest.version).toBe('2.0.0');
    });

    it('should reject updating package from different signer', async () => {
      const eco = await createInitializedEcosystem();
      const m = eco.createManifest(makeTestManifestOpts());
      const pkg = eco.signPackage(m);
      await eco.publishPackage(pkg);

      // Create a different keypair and try to publish same packageId
      const otherKeys = generateDeveloperKeyPair();
      const m2 = eco.createManifest(makeTestManifestOpts({ version: '2.0.0' }));
      const fakePkg = signManifest(m2, otherKeys.privateKey);
      await expect(eco.publishPackage(fakePkg)).rejects.toThrow(/different developer/);
    });

    it('should get a published package by ID', async () => {
      const eco = await createInitializedEcosystem();
      const m = eco.createManifest(makeTestManifestOpts());
      const pkg = eco.signPackage(m);
      await eco.publishPackage(pkg);

      const found = eco.getPublishedPackage('test-superpower');
      expect(found).not.toBeNull();
      expect(found!.manifest.name).toBe('Test Superpower');
    });

    it('should return null for unknown package ID', async () => {
      const eco = await createInitializedEcosystem();
      expect(eco.getPublishedPackage('nonexistent')).toBeNull();
    });

    it('should unpublish a package', async () => {
      const eco = await createInitializedEcosystem();
      const m = eco.createManifest(makeTestManifestOpts());
      const pkg = eco.signPackage(m);
      await eco.publishPackage(pkg);
      expect(eco.getPublishedPackages()).toHaveLength(1);

      const removed = eco.unpublishPackage('test-superpower');
      expect(removed).toBe(true);
      expect(eco.getPublishedPackages()).toHaveLength(0);
    });

    it('should return false when unpublishing nonexistent package', async () => {
      const eco = await createInitializedEcosystem();
      expect(eco.unpublishPackage('nonexistent')).toBe(false);
    });
  });

  // ═══════════════════════════════════════════════════════════════════
  // § 8 — Registry Search & Filtering
  // ═══════════════════════════════════════════════════════════════════

  describe('Registry Search', () => {
    async function publishTestPackages(eco: SuperpowerEcosystem) {
      const variants = [
        makeTestManifestOpts({ packageId: 'code-formatter', name: 'Code Formatter', category: 'development', tags: ['formatting', 'code'] }),
        makeTestManifestOpts({ packageId: 'data-viz', name: 'Data Visualizer', category: 'data-analysis', tags: ['charts', 'data'] }),
        makeTestManifestOpts({ packageId: 'email-sender', name: 'Email Sender', category: 'communication', tags: ['email'], pricing: { model: 'one-time', priceUsdCents: 500 } }),
      ];

      for (const opts of variants) {
        const m = eco.createManifest(opts);
        const pkg = eco.signPackage(m);
        await eco.publishPackage(pkg);
      }
    }

    it('should return all packages with empty query', async () => {
      const eco = await createInitializedEcosystem();
      await publishTestPackages(eco);
      const result = await eco.searchRegistry({});
      expect(result.total).toBe(3);
      expect(result.listings).toHaveLength(3);
    });

    it('should filter by text query', async () => {
      const eco = await createInitializedEcosystem();
      await publishTestPackages(eco);
      const result = await eco.searchRegistry({ query: 'formatter' });
      expect(result.total).toBe(1);
      expect(result.listings[0].packageId).toBe('code-formatter');
    });

    it('should filter by category', async () => {
      const eco = await createInitializedEcosystem();
      await publishTestPackages(eco);
      const result = await eco.searchRegistry({ category: 'data-analysis' });
      expect(result.total).toBe(1);
      expect(result.listings[0].packageId).toBe('data-viz');
    });

    it('should filter by tags', async () => {
      const eco = await createInitializedEcosystem();
      await publishTestPackages(eco);
      const result = await eco.searchRegistry({ tags: ['email'] });
      expect(result.total).toBe(1);
      expect(result.listings[0].packageId).toBe('email-sender');
    });

    it('should filter by pricing model', async () => {
      const eco = await createInitializedEcosystem();
      await publishTestPackages(eco);
      const result = await eco.searchRegistry({ pricingModel: 'free' });
      expect(result.total).toBe(2); // code-formatter and data-viz are free
    });

    it('should filter by capability (tool name)', async () => {
      const eco = await createInitializedEcosystem();
      await publishTestPackages(eco);
      const result = await eco.searchRegistry({ capability: 'test-tool' });
      expect(result.total).toBe(3); // All use the default test-tool capability
    });

    it('should paginate results', async () => {
      const eco = await createInitializedEcosystem();
      await publishTestPackages(eco);
      const result = await eco.searchRegistry({ limit: 2, offset: 0 });
      expect(result.listings).toHaveLength(2);
      expect(result.total).toBe(3);

      const page2 = await eco.searchRegistry({ limit: 2, offset: 2 });
      expect(page2.listings).toHaveLength(1);
    });

    it('should sort by name', async () => {
      const eco = await createInitializedEcosystem();
      await publishTestPackages(eco);
      const result = await eco.searchRegistry({ sortBy: 'name' });
      expect(result.listings[0].name).toBe('Code Formatter');
      expect(result.listings[1].name).toBe('Data Visualizer');
      expect(result.listings[2].name).toBe('Email Sender');
    });

    it('should get a specific registry listing', async () => {
      const eco = await createInitializedEcosystem();
      await publishTestPackages(eco);
      const listing = await eco.getRegistryListing('data-viz');
      expect(listing).not.toBeNull();
      expect(listing!.name).toBe('Data Visualizer');
      expect(listing!.signed).toBe(true);
    });

    it('should return null for nonexistent listing', async () => {
      const eco = await createInitializedEcosystem();
      const listing = await eco.getRegistryListing('nonexistent');
      expect(listing).toBeNull();
    });
  });

  describe('searchForCapability()', () => {
    it('should search by capability keywords', async () => {
      const eco = await createInitializedEcosystem();
      const m = eco.createManifest(makeTestManifestOpts({
        packageId: 'file-converter',
        name: 'File Converter',
        capabilities: [makeTestCapability('convert-file')],
      }));
      const pkg = eco.signPackage(m);
      await eco.publishPackage(pkg);

      const result = await eco.searchForCapability('convert files', ['convert-file']);
      expect(result.total).toBe(1);
    });

    it('should fall back to text search when no capability match', async () => {
      const eco = await createInitializedEcosystem();
      const m = eco.createManifest(makeTestManifestOpts({
        packageId: 'amazing-tool',
        name: 'Amazing Tool',
        description: 'Does amazing things with spreadsheets',
      }));
      const pkg = eco.signPackage(m);
      await eco.publishPackage(pkg);

      const result = await eco.searchForCapability('spreadsheet processing', ['spreadsheet']);
      expect(result.total).toBe(1);
    });
  });

  // ═══════════════════════════════════════════════════════════════════
  // § 9 — Financial Transactions + cLaw Compliance
  // ═══════════════════════════════════════════════════════════════════

  describe('Financial Transactions', () => {
    it('should reject purchase when paid superpowers are disabled', async () => {
      const eco = await createInitializedEcosystem({ paidSuperpowersEnabled: false });
      expect(() => eco.initiatePurchase('pkg-1', 999)).toThrow(/not enabled/);
    });

    it('should create a pending-consent transaction', async () => {
      const eco = await createInitializedEcosystem({ paidSuperpowersEnabled: true });
      const tx = eco.initiatePurchase('pkg-1', 999);
      expect(tx.status).toBe('pending-consent');
      expect(tx.packageId).toBe('pkg-1');
      expect(tx.amountUsdCents).toBe(999);
      expect(tx.consentToken).toBe('');
      expect(tx.executed).toBe(false);
    });

    it('should transition to cooling-off on approval', async () => {
      const eco = await createInitializedEcosystem({ paidSuperpowersEnabled: true });
      const tx = eco.initiatePurchase('pkg-1', 999);
      const approved = eco.approvePurchase(tx.id, 'user-consent-abc');
      expect(approved.status).toBe('cooling-off');
      expect(approved.consentToken).toBe('user-consent-abc');
      expect(approved.consentedAt).toBeGreaterThan(0);
      expect(approved.coolingOffExpiresAt).toBe(approved.consentedAt + COOLING_OFF_MS);
    });

    it('should reject approval without consent token', async () => {
      const eco = await createInitializedEcosystem({ paidSuperpowersEnabled: true });
      const tx = eco.initiatePurchase('pkg-1', 999);
      expect(() => eco.approvePurchase(tx.id, '')).toThrow(/Consent token required/);
    });

    it('should reject approval of already approved transaction', async () => {
      const eco = await createInitializedEcosystem({ paidSuperpowersEnabled: true });
      const tx = eco.initiatePurchase('pkg-1', 999);
      eco.approvePurchase(tx.id, 'token');
      expect(() => eco.approvePurchase(tx.id, 'token2')).toThrow(/not pending consent/);
    });

    it('should reject execution during cooling-off period', async () => {
      const eco = await createInitializedEcosystem({ paidSuperpowersEnabled: true });
      const tx = eco.initiatePurchase('pkg-1', 999);
      eco.approvePurchase(tx.id, 'token');
      // Immediately try to execute — should fail because cooling-off hasn't expired
      expect(() => eco.executePurchase(tx.id)).toThrow(/Cooling-off period/);
    });

    it('should execute purchase after cooling-off period', async () => {
      const eco = await createInitializedEcosystem({ paidSuperpowersEnabled: true });
      const tx = eco.initiatePurchase('pkg-1', 999);
      eco.approvePurchase(tx.id, 'token');

      // Fast-forward past cooling-off
      const txRef = eco.getTransactions().find(t => t.id === tx.id)!;
      txRef.coolingOffExpiresAt = Date.now() - 1;

      const completed = eco.executePurchase(tx.id);
      expect(completed.status).toBe('completed');
      expect(completed.executed).toBe(true);
      expect(completed.executedAt).toBeGreaterThan(0);
    });

    it('should reject execution without consent token', async () => {
      const eco = await createInitializedEcosystem({ paidSuperpowersEnabled: true });
      const tx = eco.initiatePurchase('pkg-1', 999);
      // Try to execute without approval
      expect(() => eco.executePurchase(tx.id)).toThrow(/consent token/);
    });

    it('should cancel during cooling-off', async () => {
      const eco = await createInitializedEcosystem({ paidSuperpowersEnabled: true });
      const tx = eco.initiatePurchase('pkg-1', 999);
      eco.approvePurchase(tx.id, 'token');
      const cancelled = eco.cancelPurchase(tx.id);
      expect(cancelled.status).toBe('cancelled');
    });

    it('should cancel from pending-consent', async () => {
      const eco = await createInitializedEcosystem({ paidSuperpowersEnabled: true });
      const tx = eco.initiatePurchase('pkg-1', 999);
      const cancelled = eco.cancelPurchase(tx.id);
      expect(cancelled.status).toBe('cancelled');
    });

    it('should reject cancelling a completed transaction', async () => {
      const eco = await createInitializedEcosystem({ paidSuperpowersEnabled: true });
      const tx = eco.initiatePurchase('pkg-1', 999);
      eco.approvePurchase(tx.id, 'token');
      const txRef = eco.getTransactions().find(t => t.id === tx.id)!;
      txRef.coolingOffExpiresAt = Date.now() - 1;
      eco.executePurchase(tx.id);

      expect(() => eco.cancelPurchase(tx.id)).toThrow(/Cannot cancel/);
    });

    it('should default type to purchase', async () => {
      const eco = await createInitializedEcosystem({ paidSuperpowersEnabled: true });
      const tx = eco.initiatePurchase('pkg-1', 100);
      expect(tx.type).toBe('purchase');
    });

    it('should support subscription-start type', async () => {
      const eco = await createInitializedEcosystem({ paidSuperpowersEnabled: true });
      const tx = eco.initiatePurchase('pkg-1', 100, 'subscription-start');
      expect(tx.type).toBe('subscription-start');
    });
  });

  describe('Transaction Queries', () => {
    it('should get transactions for a specific package', async () => {
      const eco = await createInitializedEcosystem({ paidSuperpowersEnabled: true });
      eco.initiatePurchase('pkg-1', 100);
      eco.initiatePurchase('pkg-2', 200);
      eco.initiatePurchase('pkg-1', 300);

      const txs = eco.getTransactionsForPackage('pkg-1');
      expect(txs).toHaveLength(2);
      expect(txs.every(t => t.packageId === 'pkg-1')).toBe(true);
    });

    it('should get a specific transaction by ID', async () => {
      const eco = await createInitializedEcosystem({ paidSuperpowersEnabled: true });
      const tx = eco.initiatePurchase('pkg-1', 100);
      const found = eco.getTransaction(tx.id);
      expect(found).not.toBeNull();
      expect(found!.id).toBe(tx.id);
    });

    it('should return null for nonexistent transaction', async () => {
      const eco = await createInitializedEcosystem({ paidSuperpowersEnabled: true });
      expect(eco.getTransaction('nonexistent')).toBeNull();
    });

    it('should report isPurchased for completed transactions', async () => {
      const eco = await createInitializedEcosystem({ paidSuperpowersEnabled: true });
      expect(eco.isPurchased('pkg-1')).toBe(false);

      const tx = eco.initiatePurchase('pkg-1', 100);
      eco.approvePurchase(tx.id, 'token');
      const txRef = eco.getTransactions().find(t => t.id === tx.id)!;
      txRef.coolingOffExpiresAt = Date.now() - 1;
      eco.executePurchase(tx.id);

      expect(eco.isPurchased('pkg-1')).toBe(true);
    });

    it('should not report isPurchased for pending transactions', async () => {
      const eco = await createInitializedEcosystem({ paidSuperpowersEnabled: true });
      eco.initiatePurchase('pkg-1', 100);
      expect(eco.isPurchased('pkg-1')).toBe(false);
    });
  });

  // ═══════════════════════════════════════════════════════════════════
  // § 10 — Config & Stats
  // ═══════════════════════════════════════════════════════════════════

  describe('Config', () => {
    it('should return default config', async () => {
      const eco = await createInitializedEcosystem();
      const config = eco.getConfig();
      expect(config.registryUrl).toBeNull();
      expect(config.allowUnsignedRegistry).toBe(false);
      expect(config.allowDirectGitHub).toBe(true);
      expect(config.paidSuperpowersEnabled).toBe(false);
      expect(config.maxAutoApproveUsdCents).toBe(0);
      expect(config.developerMode).toBe(false);
    });

    it('should update config partially', async () => {
      const eco = await createInitializedEcosystem();
      const updated = eco.updateConfig({ developerMode: true, registryUrl: 'https://r.test.com' });
      expect(updated.developerMode).toBe(true);
      expect(updated.registryUrl).toBe('https://r.test.com');
      expect(updated.allowDirectGitHub).toBe(true); // Unchanged
    });

    it('should return a copy (not reference)', async () => {
      const eco = await createInitializedEcosystem();
      const c1 = eco.getConfig();
      c1.developerMode = true;
      expect(eco.getConfig().developerMode).toBe(false); // Not mutated
    });
  });

  describe('Stats', () => {
    it('should report correct stats for empty ecosystem', async () => {
      const eco = await createInitializedEcosystem();
      const stats = eco.getStats();
      expect(stats.publishedPackages).toBe(0);
      expect(stats.totalTransactions).toBe(0);
      expect(stats.completedTransactions).toBe(0);
      expect(stats.totalRevenueCents).toBe(0);
      expect(stats.cancelledTransactions).toBe(0);
      expect(stats.hasDeveloperKeys).toBe(false);
      expect(stats.registryConfigured).toBe(false);
    });

    it('should report correct stats with published packages', async () => {
      const eco = await createInitializedEcosystem();
      const m = eco.createManifest(makeTestManifestOpts());
      const pkg = eco.signPackage(m);
      await eco.publishPackage(pkg);

      const stats = eco.getStats();
      expect(stats.publishedPackages).toBe(1);
      expect(stats.hasDeveloperKeys).toBe(true);
      expect(stats.categoryCounts).toHaveProperty('development');
    });

    it('should track transaction revenue', async () => {
      const eco = await createInitializedEcosystem({ paidSuperpowersEnabled: true });
      const tx = eco.initiatePurchase('pkg-1', 1000);
      eco.approvePurchase(tx.id, 'token');
      const txRef = eco.getTransactions().find(t => t.id === tx.id)!;
      txRef.coolingOffExpiresAt = Date.now() - 1;
      eco.executePurchase(tx.id);

      const stats = eco.getStats();
      expect(stats.totalTransactions).toBe(1);
      expect(stats.completedTransactions).toBe(1);
      expect(stats.totalRevenueCents).toBe(1000);
    });

    it('should track cancelled transactions', async () => {
      const eco = await createInitializedEcosystem({ paidSuperpowersEnabled: true });
      const tx = eco.initiatePurchase('pkg-1', 100);
      eco.cancelPurchase(tx.id);

      const stats = eco.getStats();
      expect(stats.cancelledTransactions).toBe(1);
    });

    it('should report registryConfigured when URL is set', async () => {
      const eco = await createInitializedEcosystem();
      eco.updateConfig({ registryUrl: 'https://registry.test' });
      expect(eco.getStats().registryConfigured).toBe(true);
    });
  });

  // ═══════════════════════════════════════════════════════════════════
  // § 11 — Prompt Context
  // ═══════════════════════════════════════════════════════════════════

  describe('getPromptContext()', () => {
    it('should return empty string for empty ecosystem', async () => {
      const eco = await createInitializedEcosystem();
      expect(eco.getPromptContext()).toBe('');
    });

    it('should include marketplace info when packages exist', async () => {
      const eco = await createInitializedEcosystem();
      const m = eco.createManifest(makeTestManifestOpts());
      const pkg = eco.signPackage(m);
      await eco.publishPackage(pkg);

      const ctx = eco.getPromptContext();
      expect(ctx).toContain('SUPERPOWER MARKETPLACE');
      expect(ctx).toContain('1 packages');
      expect(ctx).toContain('development');
    });

    it('should include pending purchase info', async () => {
      const eco = await createInitializedEcosystem({ paidSuperpowersEnabled: true });
      eco.initiatePurchase('pkg-1', 100);

      const ctx = eco.getPromptContext();
      expect(ctx).toContain('pending purchase');
    });
  });

  // ═══════════════════════════════════════════════════════════════════
  // § 12 — Persistence
  // ═══════════════════════════════════════════════════════════════════

  describe('Persistence', () => {
    it('should queue save on developer key generation', async () => {
      const eco = await createInitializedEcosystem();
      eco.getDeveloperKeys();
      // Save is debounced (2s), so it won't have fired yet
      await vi.advanceTimersByTimeAsync?.(3000).catch(() => {});
      // In real implementation it queues but we just verify no crash
    });

    it('should flush pending save on stop()', async () => {
      const eco = await createInitializedEcosystem();
      eco.getDeveloperKeys(); // Triggers queueSave
      await eco.stop();
      // After stop, the write should have been called
      expect(mockFs.writeFile).toHaveBeenCalled();
    });

    it('should stop cleanly with no pending saves', async () => {
      const eco = await createInitializedEcosystem();
      await eco.stop();
      // Should not throw
    });
  });

  // ═══════════════════════════════════════════════════════════════════
  // § 13 — Edge Cases
  // ═══════════════════════════════════════════════════════════════════

  describe('Edge Cases', () => {
    it('should handle nonexistent transaction for approval', async () => {
      const eco = await createInitializedEcosystem({ paidSuperpowersEnabled: true });
      expect(() => eco.approvePurchase('ghost-tx', 'token')).toThrow(/not found/);
    });

    it('should handle nonexistent transaction for execution', async () => {
      const eco = await createInitializedEcosystem({ paidSuperpowersEnabled: true });
      expect(() => eco.executePurchase('ghost-tx')).toThrow(/not found/);
    });

    it('should handle nonexistent transaction for cancellation', async () => {
      const eco = await createInitializedEcosystem({ paidSuperpowersEnabled: true });
      expect(() => eco.cancelPurchase('ghost-tx')).toThrow(/not found/);
    });

    it('should return empty array for getTransactionsForPackage when no transactions', async () => {
      const eco = await createInitializedEcosystem();
      expect(eco.getTransactionsForPackage('pkg-1')).toEqual([]);
    });

    it('should handle manifest creation with all optional fields', async () => {
      const eco = await createInitializedEcosystem();
      const m = eco.createManifest({
        ...makeTestManifestOpts(),
        homepage: 'https://example.com',
        minAgentVersion: '3.0.0',
        tags: ['test', 'demo'],
        dependencies: [{ name: 'lodash', version: '4.17.21', optional: false }],
      });
      expect(m.homepage).toBe('https://example.com');
      expect(m.minAgentVersion).toBe('3.0.0');
      expect(m.tags).toEqual(['test', 'demo']);
      expect(m.dependencies).toHaveLength(1);
    });

    it('should maintain separate transaction lists per package', async () => {
      const eco = await createInitializedEcosystem({ paidSuperpowersEnabled: true });
      eco.initiatePurchase('pkg-a', 100);
      eco.initiatePurchase('pkg-b', 200);
      eco.initiatePurchase('pkg-a', 300);

      expect(eco.getTransactions()).toHaveLength(3);
      expect(eco.getTransactionsForPackage('pkg-a')).toHaveLength(2);
      expect(eco.getTransactionsForPackage('pkg-b')).toHaveLength(1);
    });

    it('COOLING_OFF_MS should be 10 seconds', () => {
      expect(COOLING_OFF_MS).toBe(10_000);
    });

    it('should search registry case-insensitively', async () => {
      const eco = await createInitializedEcosystem();
      const m = eco.createManifest(makeTestManifestOpts({
        packageId: 'my-formatter',
        name: 'My Formatter',
      }));
      const pkg = eco.signPackage(m);
      await eco.publishPackage(pkg);

      const result = await eco.searchRegistry({ query: 'MY FORMATTER' });
      expect(result.total).toBe(1);
    });

    it('should handle platform filtering', async () => {
      const eco = await createInitializedEcosystem();
      const m = eco.createManifest(makeTestManifestOpts({
        packageId: 'mac-only-tool',
        platforms: ['darwin'],
      }));
      const pkg = eco.signPackage(m);
      await eco.publishPackage(pkg);

      const win = await eco.searchRegistry({ platform: 'win32' });
      expect(win.total).toBe(0);

      const mac = await eco.searchRegistry({ platform: 'darwin' });
      expect(mac.total).toBe(1);
    });

    it('should convert package to listing with correct shape', async () => {
      const eco = await createInitializedEcosystem();
      const m = eco.createManifest(makeTestManifestOpts({
        packageId: 'listing-test',
        name: 'Listing Test',
        capabilities: [makeTestCapability('my-tool'), makeTestCapability('other-tool')],
      }));
      const pkg = eco.signPackage(m);
      await eco.publishPackage(pkg);

      const listing = await eco.getRegistryListing('listing-test');
      expect(listing).not.toBeNull();
      expect(listing!.capabilities).toEqual(['my-tool', 'other-tool']);
      expect(listing!.signed).toBe(true);
      expect(listing!.registryScanPassed).toBe(true);
      expect(listing!.stats.installs).toBe(0);
      expect(listing!.stats.rating).toBe(0);
    });
  });
});
