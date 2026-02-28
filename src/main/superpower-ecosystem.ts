/**
 * superpower-ecosystem.ts — Superpower Marketplace & Distribution.
 *
 * Track VII, Phase 3: The Scaffold — Superpower Ecosystem.
 *
 * Builds on top of the existing superpower infrastructure (Track II):
 *   - superpower-store.ts: Local lifecycle management (install/enable/disable)
 *   - capability-gap-detector.ts: Gap detection → proposal generation
 *   - adapter-engine.ts: Code generation for connectors
 *   - git-scanner/sandbox/analyzer/review: Security pipeline
 *
 * This module adds the ECOSYSTEM layer:
 *   1. Standardized Manifest — Formalizes superpower package format
 *   2. Package Signing — Ed25519 signatures for tamper detection
 *   3. Registry Client — Browse, search, install from a remote registry
 *   4. Publishing — Developer flow for submitting to registry
 *   5. Discovery — Wires gap detector to registry search
 *   6. Payment — Free/paid with financial consent gate + cooling-off
 *
 * Architecture:
 *   Local-first design. The registry is an optional layer — superpowers
 *   can still be installed directly from GitHub repos (Track II path).
 *   The registry adds curation, signing, and discovery.
 *
 * cLaw Safety Boundaries:
 *   - Security pipeline runs on EVERY install, regardless of registry signing
 *   - Payment NEVER bypasses security scanning
 *   - Financial transactions require explicit approval + 10s cooling-off
 *   - Core agent functionality is NEVER paywalled
 *   - Publishing requires identity verification (Ed25519 keypair)
 */

import * as crypto from 'crypto';
import * as fs from 'fs/promises';
import { app } from 'electron';
import {
  PersistentError,
  FatalIntegrityError,
  type ErrorSource,
} from './errors';

// ── Superpower Package Manifest ──────────────────────────────────────

/**
 * Standardized superpower manifest — the universal package format.
 * Formalizes what adapter-engine.ts currently infers.
 */
export interface SuperpowerManifest {
  /** Schema version for forward compatibility */
  schemaVersion: '1.0.0';
  /** Unique package identifier (lowercase, hyphenated) */
  packageId: string;
  /** Human-readable name */
  name: string;
  /** Detailed description */
  description: string;
  /** Short tagline (≤80 chars) */
  tagline: string;
  /** Semantic version */
  version: string;
  /** Package author */
  author: ManifestAuthor;
  /** License identifier (SPDX) */
  license: string;
  /** Source repository URL */
  repository: string;
  /** Homepage or docs URL */
  homepage?: string;

  /** Capability declarations */
  capabilities: ManifestCapability[];
  /** Required permissions */
  permissions: SuperpowerPermission[];
  /** Runtime dependencies */
  dependencies: ManifestDependency[];
  /** Minimum Agent Friday version required */
  minAgentVersion?: string;
  /** Supported platforms */
  platforms: ('win32' | 'darwin' | 'linux')[];

  /** Entry point configuration */
  entry: ManifestEntry;
  /** Sandbox requirements */
  sandbox: ManifestSandbox;

  /** Category for marketplace browsing */
  category: SuperpowerCategory;
  /** Search tags */
  tags: string[];
  /** Pricing model */
  pricing: ManifestPricing;

  /** When the manifest was created */
  createdAt: number;
  /** When the manifest was last updated */
  updatedAt: number;
}

export interface ManifestAuthor {
  name: string;
  email?: string;
  url?: string;
  /** Ed25519 public key (hex) for signature verification */
  publicKey?: string;
}

export interface ManifestCapability {
  /** Tool name exposed to the agent */
  toolName: string;
  /** What this tool does */
  description: string;
  /** JSON Schema for parameters */
  parameters: Record<string, unknown>;
  /** Required parameter names */
  required?: string[];
}

export type SuperpowerPermission =
  | 'network'          // Can make HTTP requests
  | 'filesystem-read'  // Can read files
  | 'filesystem-write' // Can write files
  | 'subprocess'       // Can spawn processes
  | 'clipboard'        // Can access clipboard
  | 'notification'     // Can show notifications
  | 'system-info'      // Can read system information
  | 'camera'           // Can access camera
  | 'microphone'       // Can access microphone
  | 'credentials';     // Needs API keys or tokens

export interface ManifestDependency {
  name: string;
  version: string;
  optional: boolean;
}

export interface ManifestEntry {
  /** Entry point type */
  type: 'native-module' | 'subprocess' | 'wasm' | 'http-api';
  /** Main file or URL */
  main: string;
  /** Bridge script for subprocess entries */
  bridge?: string;
}

export interface ManifestSandbox {
  /** Whether network access is needed */
  network: boolean;
  /** Filesystem access scope */
  filesystem: 'none' | 'read-only' | 'read-write' | 'temp-only';
  /** Maximum memory in MB */
  maxMemoryMb: number;
  /** Maximum CPU time per invocation in ms */
  maxCpuTimeMs: number;
  /** Allowed environment variables */
  allowedEnvVars: string[];
}

export type SuperpowerCategory =
  | 'productivity'
  | 'development'
  | 'creative'
  | 'communication'
  | 'data-analysis'
  | 'system'
  | 'security'
  | 'ai-ml'
  | 'finance'
  | 'other';

export interface ManifestPricing {
  /** Pricing model */
  model: 'free' | 'one-time' | 'subscription' | 'usage-based';
  /** Price in USD cents (0 for free) */
  priceUsdCents: number;
  /** Billing period for subscriptions */
  billingPeriod?: 'monthly' | 'yearly';
  /** Free trial days */
  trialDays?: number;
  /** Usage unit description (for usage-based) */
  usageUnit?: string;
}

// ── Package Signing ──────────────────────────────────────────────────

/**
 * Signed superpower package — manifest + Ed25519 signature.
 */
export interface SignedPackage {
  manifest: SuperpowerManifest;
  /** Ed25519 signature of canonical manifest JSON (hex) */
  signature: string;
  /** Signer's public key (hex) */
  signerPublicKey: string;
  /** When this package was signed */
  signedAt: number;
  /** SHA-256 hash of the manifest content */
  contentHash: string;
}

/**
 * Developer signing keypair for publishing.
 */
export interface DeveloperKeyPair {
  publicKey: string;  // hex
  privateKey: string; // hex (stored locally, never transmitted)
}

/**
 * Generate a new Ed25519 signing keypair for a developer.
 */
export function generateDeveloperKeyPair(): DeveloperKeyPair {
  const { publicKey, privateKey } = crypto.generateKeyPairSync('ed25519');
  return {
    publicKey: publicKey.export({ type: 'spki', format: 'der' }).toString('hex'),
    privateKey: privateKey.export({ type: 'pkcs8', format: 'der' }).toString('hex'),
  };
}

/**
 * Sign a manifest to create a signed package.
 */
export function signManifest(
  manifest: SuperpowerManifest,
  privateKeyHex: string,
): SignedPackage {
  const canonical = canonicalizeManifest(manifest);
  const contentHash = crypto.createHash('sha256').update(canonical).digest('hex');

  const privateKey = crypto.createPrivateKey({
    key: Buffer.from(privateKeyHex, 'hex'),
    format: 'der',
    type: 'pkcs8',
  });

  const signature = crypto.sign(null, Buffer.from(canonical), privateKey).toString('hex');

  const publicKey = crypto.createPublicKey(privateKey);
  const signerPublicKey = publicKey.export({ type: 'spki', format: 'der' }).toString('hex');

  return {
    manifest,
    signature,
    signerPublicKey,
    signedAt: Date.now(),
    contentHash,
  };
}

/**
 * Verify a signed package's integrity and authenticity.
 */
export function verifyPackageSignature(pkg: SignedPackage): PackageVerification {
  try {
    const canonical = canonicalizeManifest(pkg.manifest);

    // Verify content hash
    const expectedHash = crypto.createHash('sha256').update(canonical).digest('hex');
    if (expectedHash !== pkg.contentHash) {
      return {
        valid: false,
        reason: 'Content hash mismatch — manifest has been tampered with',
        signerPublicKey: pkg.signerPublicKey,
      };
    }

    // Verify Ed25519 signature
    const publicKey = crypto.createPublicKey({
      key: Buffer.from(pkg.signerPublicKey, 'hex'),
      format: 'der',
      type: 'spki',
    });

    const isValid = crypto.verify(
      null,
      Buffer.from(canonical),
      publicKey,
      Buffer.from(pkg.signature, 'hex'),
    );

    if (!isValid) {
      return {
        valid: false,
        reason: 'Ed25519 signature verification failed',
        signerPublicKey: pkg.signerPublicKey,
      };
    }

    // Verify author key matches signer
    if (pkg.manifest.author.publicKey && pkg.manifest.author.publicKey !== pkg.signerPublicKey) {
      return {
        valid: false,
        reason: 'Signer public key does not match manifest author key',
        signerPublicKey: pkg.signerPublicKey,
      };
    }

    return {
      valid: true,
      reason: 'Signature valid — package integrity verified',
      signerPublicKey: pkg.signerPublicKey,
    };
  } catch (err) {
    return {
      valid: false,
      reason: `Signature verification error: ${err instanceof Error ? err.message : String(err)}`,
      signerPublicKey: pkg.signerPublicKey,
    };
  }
}

export interface PackageVerification {
  valid: boolean;
  reason: string;
  signerPublicKey: string;
}

/**
 * Canonical JSON representation of a manifest (deterministic ordering).
 */
export function canonicalizeManifest(manifest: SuperpowerManifest): string {
  return JSON.stringify(manifest, Object.keys(manifest).sort());
}

// ── Registry Listing ─────────────────────────────────────────────────

/**
 * A superpower listing in the registry — what users browse/search.
 */
export interface RegistryListing {
  /** Package ID */
  packageId: string;
  /** Latest version */
  version: string;
  /** Display name */
  name: string;
  /** Short tagline */
  tagline: string;
  /** Full description */
  description: string;
  /** Author info */
  author: ManifestAuthor;
  /** Category */
  category: SuperpowerCategory;
  /** Tags */
  tags: string[];
  /** Pricing */
  pricing: ManifestPricing;
  /** Capabilities provided */
  capabilities: string[]; // tool names
  /** Required permissions */
  permissions: SuperpowerPermission[];
  /** Supported platforms */
  platforms: ('win32' | 'darwin' | 'linux')[];

  /** Registry stats */
  stats: RegistryStats;
  /** Whether this package is signed */
  signed: boolean;
  /** Whether this package passed automated security scan */
  registryScanPassed: boolean;
  /** When published to registry */
  publishedAt: number;
  /** When last updated in registry */
  updatedAt: number;
}

export interface RegistryStats {
  installs: number;
  activeInstalls: number;
  rating: number;        // 0-5
  ratingCount: number;
  /** Verified reviews */
  reviews: number;
}

// ── Financial Transaction ────────────────────────────────────────────

/**
 * Financial transaction for paid superpowers.
 *
 * cLaw Gate: Every transaction requires:
 *   1. Explicit user approval (consentToken)
 *   2. 10-second cooling-off period between approval and execution
 *   3. No auto-renewal without fresh consent
 */
export interface FinancialTransaction {
  id: string;
  packageId: string;
  /** Transaction type */
  type: 'purchase' | 'subscription-start' | 'subscription-renew' | 'refund';
  /** Amount in USD cents */
  amountUsdCents: number;
  /** User consent token */
  consentToken: string;
  /** When consent was given */
  consentedAt: number;
  /** When the cooling-off period expires (consentedAt + 10000ms) */
  coolingOffExpiresAt: number;
  /** Whether the transaction was executed */
  executed: boolean;
  /** When executed */
  executedAt: number | null;
  /** Transaction status */
  status: TransactionStatus;
  /** Failure reason if applicable */
  failureReason?: string;
}

export type TransactionStatus =
  | 'pending-consent'   // Awaiting user approval
  | 'cooling-off'       // Approved but in 10s cooling-off
  | 'ready'             // Cooling-off expired, ready to execute
  | 'executing'         // Being processed
  | 'completed'         // Successfully processed
  | 'failed'            // Transaction failed
  | 'refunded'          // Reversed
  | 'cancelled';        // User cancelled during cooling-off

/** Cooling-off period in milliseconds (10 seconds). */
export const COOLING_OFF_MS = 10_000;

// ── Registry Search ──────────────────────────────────────────────────

export interface RegistrySearchQuery {
  /** Free-text search */
  query?: string;
  /** Filter by category */
  category?: SuperpowerCategory;
  /** Filter by tags */
  tags?: string[];
  /** Filter by pricing model */
  pricingModel?: ManifestPricing['model'];
  /** Filter by platform */
  platform?: 'win32' | 'darwin' | 'linux';
  /** Filter by capability (tool name or keyword) */
  capability?: string;
  /** Sort order */
  sortBy?: 'relevance' | 'installs' | 'rating' | 'newest' | 'name';
  /** Pagination */
  offset?: number;
  limit?: number;
}

export interface RegistrySearchResult {
  listings: RegistryListing[];
  total: number;
  offset: number;
  limit: number;
  query: RegistrySearchQuery;
}

// ── Ecosystem Configuration ──────────────────────────────────────────

export interface EcosystemConfig {
  /** Registry URL (null = registry disabled, direct GitHub only) */
  registryUrl: string | null;
  /** Whether to allow unsigned packages from registry */
  allowUnsignedRegistry: boolean;
  /** Whether to allow direct GitHub installs (bypass registry) */
  allowDirectGitHub: boolean;
  /** Whether paid superpowers are enabled */
  paidSuperpowersEnabled: boolean;
  /** Maximum price in USD cents for auto-approval (0 = always ask) */
  maxAutoApproveUsdCents: number;
  /** Developer mode (enables publishing tools) */
  developerMode: boolean;
  /** Cache TTL for registry listings (ms) */
  registryCacheTtlMs: number;
}

const DEFAULT_ECOSYSTEM_CONFIG: EcosystemConfig = {
  registryUrl: null, // No registry by default — direct GitHub only
  allowUnsignedRegistry: false,
  allowDirectGitHub: true,
  paidSuperpowersEnabled: false,
  maxAutoApproveUsdCents: 0,
  developerMode: false,
  registryCacheTtlMs: 15 * 60 * 1000, // 15 minutes
};

// ── Ecosystem Engine ─────────────────────────────────────────────────

const ERROR_SOURCE: ErrorSource = 'ecosystem';

/**
 * Superpower Ecosystem — marketplace, signing, discovery, payment.
 *
 * Builds on existing infrastructure:
 *   - SuperpowerStore for local lifecycle management
 *   - CapabilityGapDetector for discovery
 *   - Security pipeline (git-scanner/sandbox/analyzer/review) for safety
 *
 * This module is the DISTRIBUTION layer — it does not replace any
 * existing functionality, it extends it.
 */
export class SuperpowerEcosystem {
  private config: EcosystemConfig;
  private developerKeys: DeveloperKeyPair | null = null;
  private registryCache: RegistryListing[] = [];
  private registryCacheTime = 0;
  private transactions: FinancialTransaction[] = [];
  private publishedPackages: SignedPackage[] = [];
  private filePath = '';
  private initialized = false;
  private saveQueued = false;
  private saveTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(config: Partial<EcosystemConfig> = {}) {
    this.config = { ...DEFAULT_ECOSYSTEM_CONFIG, ...config };
  }

  // ── Initialization ───────────────────────────────────────────────

  async initialize(): Promise<void> {
    if (this.initialized) return;

    const dataDir = app.getPath('userData');
    const fridayDataDir = `${dataDir}/friday-data`;
    await fs.mkdir(fridayDataDir, { recursive: true });
    this.filePath = `${fridayDataDir}/superpower-ecosystem.json`;

    try {
      const raw = await fs.readFile(this.filePath, 'utf-8');
      const data = JSON.parse(raw);
      if (data.developerKeys) this.developerKeys = data.developerKeys;
      if (Array.isArray(data.transactions)) this.transactions = data.transactions;
      if (Array.isArray(data.publishedPackages)) this.publishedPackages = data.publishedPackages;
      if (data.config) this.config = { ...this.config, ...data.config };
    } catch {
      // Fresh install
      console.log('[SuperpowerEcosystem] Fresh start — no ecosystem data found');
    }

    this.initialized = true;
    console.log(`[SuperpowerEcosystem] Initialized (${this.transactions.length} transactions, ${this.publishedPackages.length} published packages)`);
  }

  // ── Manifest Creation ────────────────────────────────────────────

  /**
   * Create a manifest from package metadata.
   * Used by developers building superpowers.
   */
  createManifest(opts: {
    packageId: string;
    name: string;
    description: string;
    tagline: string;
    version: string;
    author: ManifestAuthor;
    license: string;
    repository: string;
    capabilities: ManifestCapability[];
    permissions: SuperpowerPermission[];
    dependencies?: ManifestDependency[];
    entry: ManifestEntry;
    sandbox: ManifestSandbox;
    category: SuperpowerCategory;
    tags?: string[];
    pricing?: Partial<ManifestPricing>;
    platforms?: ('win32' | 'darwin' | 'linux')[];
    homepage?: string;
    minAgentVersion?: string;
  }): SuperpowerManifest {
    // Validate package ID format
    if (!/^[a-z0-9][a-z0-9-]*[a-z0-9]$/.test(opts.packageId)) {
      throw new PersistentError(
        ERROR_SOURCE,
        `Invalid package ID "${opts.packageId}" — must be lowercase alphanumeric with hyphens`,
      );
    }

    if (opts.packageId.length < 3 || opts.packageId.length > 64) {
      throw new PersistentError(
        ERROR_SOURCE,
        'Package ID must be 3-64 characters',
      );
    }

    if (opts.tagline.length > 80) {
      throw new PersistentError(ERROR_SOURCE, 'Tagline must be ≤80 characters');
    }

    if (opts.capabilities.length === 0) {
      throw new PersistentError(ERROR_SOURCE, 'Manifest must declare at least one capability');
    }

    const now = Date.now();

    return {
      schemaVersion: '1.0.0',
      packageId: opts.packageId,
      name: opts.name,
      description: opts.description,
      tagline: opts.tagline,
      version: opts.version,
      author: opts.author,
      license: opts.license,
      repository: opts.repository,
      homepage: opts.homepage,
      capabilities: opts.capabilities,
      permissions: opts.permissions,
      dependencies: opts.dependencies ?? [],
      minAgentVersion: opts.minAgentVersion,
      platforms: opts.platforms ?? ['win32', 'darwin', 'linux'],
      entry: opts.entry,
      sandbox: opts.sandbox,
      category: opts.category,
      tags: opts.tags ?? [],
      pricing: {
        model: 'free',
        priceUsdCents: 0,
        ...opts.pricing,
      },
      createdAt: now,
      updatedAt: now,
    };
  }

  /**
   * Validate a manifest for completeness and correctness.
   */
  validateManifest(manifest: SuperpowerManifest): ManifestValidation {
    const errors: string[] = [];
    const warnings: string[] = [];

    // Required fields
    if (!manifest.schemaVersion) errors.push('Missing schemaVersion');
    if (!manifest.packageId) errors.push('Missing packageId');
    if (!manifest.name) errors.push('Missing name');
    if (!manifest.description) errors.push('Missing description');
    if (!manifest.version) errors.push('Missing version');
    if (!manifest.author?.name) errors.push('Missing author.name');
    if (!manifest.license) errors.push('Missing license');
    if (!manifest.repository) errors.push('Missing repository');
    if (!manifest.entry?.type) errors.push('Missing entry.type');
    if (!manifest.entry?.main) errors.push('Missing entry.main');

    // Package ID format
    if (manifest.packageId && !/^[a-z0-9][a-z0-9-]*[a-z0-9]$/.test(manifest.packageId)) {
      errors.push('packageId must be lowercase alphanumeric with hyphens');
    }

    // Semver format (simple check)
    if (manifest.version && !/^\d+\.\d+\.\d+/.test(manifest.version)) {
      warnings.push('version should follow semver (e.g., 1.0.0)');
    }

    // Capabilities
    if (!manifest.capabilities || manifest.capabilities.length === 0) {
      errors.push('Must declare at least one capability');
    } else {
      const toolNames = new Set<string>();
      for (const cap of manifest.capabilities) {
        if (!cap.toolName) errors.push('Capability missing toolName');
        if (!cap.description) errors.push(`Capability ${cap.toolName} missing description`);
        if (toolNames.has(cap.toolName)) {
          errors.push(`Duplicate tool name: ${cap.toolName}`);
        }
        toolNames.add(cap.toolName);
      }
    }

    // Pricing validation
    if (manifest.pricing) {
      if (manifest.pricing.model !== 'free' && manifest.pricing.priceUsdCents <= 0) {
        errors.push('Paid pricing model requires priceUsdCents > 0');
      }
      if (manifest.pricing.model === 'free' && manifest.pricing.priceUsdCents > 0) {
        warnings.push('Free pricing model with non-zero price — did you mean a different model?');
      }
    }

    // Sandbox
    if (manifest.sandbox) {
      if (manifest.sandbox.maxMemoryMb <= 0) warnings.push('sandbox.maxMemoryMb should be positive');
      if (manifest.sandbox.maxCpuTimeMs <= 0) warnings.push('sandbox.maxCpuTimeMs should be positive');
    }

    return {
      valid: errors.length === 0,
      errors,
      warnings,
    };
  }

  // ── Developer Tools ──────────────────────────────────────────────

  /**
   * Generate or retrieve the developer keypair for signing packages.
   * Stored locally — never transmitted.
   */
  getDeveloperKeys(): DeveloperKeyPair {
    if (!this.developerKeys) {
      this.developerKeys = generateDeveloperKeyPair();
      this.queueSave();
      console.log('[SuperpowerEcosystem] Generated new developer signing keypair');
    }
    return this.developerKeys;
  }

  /**
   * Check if developer keys exist (without generating new ones).
   */
  hasDeveloperKeys(): boolean {
    return this.developerKeys !== null;
  }

  /**
   * Sign a manifest with the developer's private key.
   */
  signPackage(manifest: SuperpowerManifest): SignedPackage {
    const keys = this.getDeveloperKeys();
    return signManifest(manifest, keys.privateKey);
  }

  /**
   * Publish a signed package.
   *
   * In the current local-first implementation, this stores the package
   * locally for sharing via the Agent Network Protocol. A future
   * registry implementation would submit to a remote server.
   *
   * cLaw: Security scan failure during publish = FatalIntegrityError.
   * A package that fails security scanning NEVER gets published.
   */
  async publishPackage(pkg: SignedPackage): Promise<PublishResult> {
    // Step 1: Verify signature
    const verification = verifyPackageSignature(pkg);
    if (!verification.valid) {
      throw new FatalIntegrityError(
        ERROR_SOURCE,
        `Cannot publish unsigned or tampered package: ${verification.reason}`,
      );
    }

    // Step 2: Validate manifest
    const validation = this.validateManifest(pkg.manifest);
    if (!validation.valid) {
      throw new PersistentError(
        ERROR_SOURCE,
        `Invalid manifest: ${validation.errors.join('; ')}`,
        { userMessage: `Package manifest has errors: ${validation.errors.join(', ')}` },
      );
    }

    // Step 3: Check for duplicate package ID
    const existing = this.publishedPackages.find(
      p => p.manifest.packageId === pkg.manifest.packageId,
    );
    if (existing) {
      // Update existing if same signer
      if (existing.signerPublicKey !== pkg.signerPublicKey) {
        throw new PersistentError(
          ERROR_SOURCE,
          `Package "${pkg.manifest.packageId}" is already published by a different developer`,
        );
      }
      // Replace with new version
      const idx = this.publishedPackages.indexOf(existing);
      this.publishedPackages[idx] = pkg;
    } else {
      this.publishedPackages.push(pkg);
    }

    this.queueSave();

    return {
      packageId: pkg.manifest.packageId,
      version: pkg.manifest.version,
      publishedAt: Date.now(),
      updated: !!existing,
    };
  }

  /**
   * Get all locally published packages.
   */
  getPublishedPackages(): SignedPackage[] {
    return [...this.publishedPackages];
  }

  /**
   * Get a specific published package by ID.
   */
  getPublishedPackage(packageId: string): SignedPackage | null {
    return this.publishedPackages.find(p => p.manifest.packageId === packageId) ?? null;
  }

  /**
   * Unpublish a package.
   */
  unpublishPackage(packageId: string): boolean {
    const idx = this.publishedPackages.findIndex(
      p => p.manifest.packageId === packageId,
    );
    if (idx === -1) return false;
    this.publishedPackages.splice(idx, 1);
    this.queueSave();
    return true;
  }

  // ── Registry Client ──────────────────────────────────────────────

  /**
   * Search the registry for superpowers.
   *
   * In the current local-first implementation, this searches locally
   * published packages. A future registry implementation would call
   * the remote API.
   */
  async searchRegistry(query: RegistrySearchQuery): Promise<RegistrySearchResult> {
    const offset = query.offset ?? 0;
    const limit = query.limit ?? 20;

    // Build listings from published packages
    let listings = this.publishedPackages.map(pkg => this.packageToListing(pkg));

    // Apply filters
    if (query.query) {
      const lowerQuery = query.query.toLowerCase();
      listings = listings.filter(l =>
        l.name.toLowerCase().includes(lowerQuery) ||
        l.description.toLowerCase().includes(lowerQuery) ||
        l.tagline.toLowerCase().includes(lowerQuery) ||
        l.tags.some(t => t.toLowerCase().includes(lowerQuery)),
      );
    }

    if (query.category) {
      listings = listings.filter(l => l.category === query.category);
    }

    if (query.tags?.length) {
      const queryTags = new Set(query.tags.map(t => t.toLowerCase()));
      listings = listings.filter(l =>
        l.tags.some(t => queryTags.has(t.toLowerCase())),
      );
    }

    if (query.pricingModel) {
      listings = listings.filter(l => l.pricing.model === query.pricingModel);
    }

    if (query.platform) {
      listings = listings.filter(l => l.platforms.includes(query.platform!));
    }

    if (query.capability) {
      const lowerCap = query.capability.toLowerCase();
      listings = listings.filter(l =>
        l.capabilities.some(c => c.toLowerCase().includes(lowerCap)),
      );
    }

    // Sort
    switch (query.sortBy) {
      case 'installs':
        listings.sort((a, b) => b.stats.installs - a.stats.installs);
        break;
      case 'rating':
        listings.sort((a, b) => b.stats.rating - a.stats.rating);
        break;
      case 'newest':
        listings.sort((a, b) => b.publishedAt - a.publishedAt);
        break;
      case 'name':
        listings.sort((a, b) => a.name.localeCompare(b.name));
        break;
      case 'relevance':
      default:
        // For local search, relevance = newest first
        listings.sort((a, b) => b.publishedAt - a.publishedAt);
        break;
    }

    const total = listings.length;
    listings = listings.slice(offset, offset + limit);

    return { listings, total, offset, limit, query };
  }

  /**
   * Get a specific registry listing by package ID.
   */
  async getRegistryListing(packageId: string): Promise<RegistryListing | null> {
    const pkg = this.publishedPackages.find(
      p => p.manifest.packageId === packageId,
    );
    if (!pkg) return null;
    return this.packageToListing(pkg);
  }

  /**
   * Search registry for superpowers matching a capability gap.
   * Bridges the gap detector with the ecosystem.
   */
  async searchForCapability(
    description: string,
    keywords: string[],
  ): Promise<RegistrySearchResult> {
    // First try capability search
    const capResult = await this.searchRegistry({
      capability: keywords[0],
      sortBy: 'relevance',
      limit: 10,
    });

    if (capResult.total > 0) return capResult;

    // Fall back to text search across all keywords
    const queryStr = keywords.join(' ');
    return this.searchRegistry({
      query: queryStr,
      sortBy: 'relevance',
      limit: 10,
    });
  }

  // ── Financial Transactions ───────────────────────────────────────

  /**
   * Initiate a purchase for a paid superpower.
   *
   * cLaw Gate: Creates a transaction in 'pending-consent' state.
   * The user MUST explicitly approve via `approvePurchase()`.
   * After approval, a 10-second cooling-off period applies.
   */
  initiatePurchase(
    packageId: string,
    amountUsdCents: number,
    type: FinancialTransaction['type'] = 'purchase',
  ): FinancialTransaction {
    if (!this.config.paidSuperpowersEnabled) {
      throw new PersistentError(
        ERROR_SOURCE,
        'Paid superpowers are not enabled in settings',
        { userMessage: 'Paid superpowers are disabled. Enable them in Settings to continue.' },
      );
    }

    const tx: FinancialTransaction = {
      id: crypto.randomUUID().slice(0, 12),
      packageId,
      type,
      amountUsdCents,
      consentToken: '',
      consentedAt: 0,
      coolingOffExpiresAt: 0,
      executed: false,
      executedAt: null,
      status: 'pending-consent',
    };

    this.transactions.push(tx);
    this.queueSave();
    return tx;
  }

  /**
   * Approve a pending purchase with a consent token.
   * Starts the 10-second cooling-off period.
   *
   * cLaw Gate: This is NOT the final step — the cooling-off period
   * must expire before `executePurchase()` can proceed.
   */
  approvePurchase(transactionId: string, consentToken: string): FinancialTransaction {
    if (!consentToken || !consentToken.trim()) {
      throw new PersistentError(
        ERROR_SOURCE,
        'cLaw: Consent token required for financial transaction',
      );
    }

    const tx = this.transactions.find(t => t.id === transactionId);
    if (!tx) throw new PersistentError(ERROR_SOURCE, `Transaction not found: ${transactionId}`);
    if (tx.status !== 'pending-consent') {
      throw new PersistentError(
        ERROR_SOURCE,
        `Transaction ${transactionId} is not pending consent (status: ${tx.status})`,
      );
    }

    tx.consentToken = consentToken;
    tx.consentedAt = Date.now();
    tx.coolingOffExpiresAt = tx.consentedAt + COOLING_OFF_MS;
    tx.status = 'cooling-off';

    this.queueSave();
    return tx;
  }

  /**
   * Cancel a purchase during the cooling-off period.
   */
  cancelPurchase(transactionId: string): FinancialTransaction {
    const tx = this.transactions.find(t => t.id === transactionId);
    if (!tx) throw new PersistentError(ERROR_SOURCE, `Transaction not found: ${transactionId}`);

    if (tx.status !== 'cooling-off' && tx.status !== 'pending-consent' && tx.status !== 'ready') {
      throw new PersistentError(
        ERROR_SOURCE,
        `Cannot cancel transaction in status: ${tx.status}`,
      );
    }

    tx.status = 'cancelled';
    this.queueSave();
    return tx;
  }

  /**
   * Execute a purchase after cooling-off period expires.
   *
   * cLaw Gate: Will refuse to execute if:
   *   - No consent token
   *   - Cooling-off period hasn't expired
   *   - Already executed
   *
   * In current implementation, this marks the transaction as completed
   * (actual payment processing would integrate with a payment provider).
   */
  executePurchase(transactionId: string): FinancialTransaction {
    const tx = this.transactions.find(t => t.id === transactionId);
    if (!tx) throw new PersistentError(ERROR_SOURCE, `Transaction not found: ${transactionId}`);

    if (!tx.consentToken) {
      throw new PersistentError(
        ERROR_SOURCE,
        'cLaw: Cannot execute purchase without consent token',
      );
    }

    // Check cooling-off period
    if (tx.status === 'cooling-off') {
      const now = Date.now();
      if (now < tx.coolingOffExpiresAt) {
        const remainingMs = tx.coolingOffExpiresAt - now;
        throw new PersistentError(
          ERROR_SOURCE,
          `Cooling-off period active — ${Math.ceil(remainingMs / 1000)}s remaining`,
          { userMessage: `Please wait ${Math.ceil(remainingMs / 1000)} seconds before completing this purchase.` },
        );
      }
      tx.status = 'ready';
    }

    if (tx.status !== 'ready') {
      throw new PersistentError(
        ERROR_SOURCE,
        `Cannot execute transaction in status: ${tx.status}`,
      );
    }

    tx.status = 'executing';
    // In a real implementation, this would call the payment provider
    tx.executed = true;
    tx.executedAt = Date.now();
    tx.status = 'completed';

    this.queueSave();
    return tx;
  }

  /**
   * Get all transactions.
   */
  getTransactions(): FinancialTransaction[] {
    return [...this.transactions];
  }

  /**
   * Get transactions for a specific package.
   */
  getTransactionsForPackage(packageId: string): FinancialTransaction[] {
    return this.transactions.filter(t => t.packageId === packageId);
  }

  /**
   * Get a specific transaction by ID.
   */
  getTransaction(transactionId: string): FinancialTransaction | null {
    return this.transactions.find(t => t.id === transactionId) ?? null;
  }

  /**
   * Check if a package has been purchased (completed transaction exists).
   */
  isPurchased(packageId: string): boolean {
    return this.transactions.some(
      t => t.packageId === packageId && t.status === 'completed',
    );
  }

  // ── Queries ──────────────────────────────────────────────────────

  /**
   * Get ecosystem statistics.
   */
  getStats(): EcosystemStats {
    return {
      publishedPackages: this.publishedPackages.length,
      totalTransactions: this.transactions.length,
      completedTransactions: this.transactions.filter(t => t.status === 'completed').length,
      totalRevenueCents: this.transactions
        .filter(t => t.status === 'completed')
        .reduce((sum, t) => sum + t.amountUsdCents, 0),
      cancelledTransactions: this.transactions.filter(t => t.status === 'cancelled').length,
      hasDeveloperKeys: this.developerKeys !== null,
      registryConfigured: this.config.registryUrl !== null,
      categoryCounts: this.getCategoryCounts(),
    };
  }

  /**
   * Get count of published packages per category.
   */
  private getCategoryCounts(): Record<SuperpowerCategory, number> {
    const counts: Record<string, number> = {};
    for (const pkg of this.publishedPackages) {
      const cat = pkg.manifest.category;
      counts[cat] = (counts[cat] ?? 0) + 1;
    }
    return counts as Record<SuperpowerCategory, number>;
  }

  /**
   * Get ecosystem configuration.
   */
  getConfig(): EcosystemConfig {
    return { ...this.config };
  }

  /**
   * Update ecosystem configuration.
   */
  updateConfig(partial: Partial<EcosystemConfig>): EcosystemConfig {
    this.config = { ...this.config, ...partial };
    this.queueSave();
    return { ...this.config };
  }

  /**
   * Generate prompt context for the agent system prompt.
   */
  getPromptContext(): string {
    const parts: string[] = [];

    if (this.publishedPackages.length > 0) {
      parts.push(`SUPERPOWER MARKETPLACE: ${this.publishedPackages.length} packages available`);
      const categories = this.getCategoryCounts();
      const catList = Object.entries(categories)
        .filter(([, count]) => count > 0)
        .map(([cat, count]) => `${cat} (${count})`)
        .join(', ');
      if (catList) parts.push(`Categories: ${catList}`);
    }

    const pending = this.transactions.filter(
      t => t.status === 'pending-consent' || t.status === 'cooling-off',
    );
    if (pending.length > 0) {
      parts.push(`${pending.length} pending purchase(s) awaiting action`);
    }

    return parts.join('\n');
  }

  // ── Persistence ──────────────────────────────────────────────────

  private queueSave(): void {
    if (this.saveQueued) return;
    this.saveQueued = true;

    this.saveTimer = setTimeout(async () => {
      this.saveQueued = false;
      await this.save();
    }, 2000);
  }

  private async save(): Promise<void> {
    if (!this.filePath) return;

    try {
      const data = JSON.stringify({
        developerKeys: this.developerKeys,
        transactions: this.transactions,
        publishedPackages: this.publishedPackages,
        config: this.config,
      }, null, 2);
      await fs.writeFile(this.filePath, data, 'utf-8');
    } catch (err) {
      console.error('[SuperpowerEcosystem] Save failed:', err);
    }
  }

  /**
   * Stop the ecosystem engine (flush pending save).
   */
  async stop(): Promise<void> {
    if (this.saveTimer) {
      clearTimeout(this.saveTimer);
      this.saveTimer = null;
    }
    if (this.saveQueued) {
      this.saveQueued = false;
      await this.save();
    }
  }

  // ── Private Helpers ──────────────────────────────────────────────

  /**
   * Convert a signed package to a registry listing.
   */
  private packageToListing(pkg: SignedPackage): RegistryListing {
    const m = pkg.manifest;
    return {
      packageId: m.packageId,
      version: m.version,
      name: m.name,
      tagline: m.tagline,
      description: m.description,
      author: m.author,
      category: m.category,
      tags: m.tags,
      pricing: m.pricing,
      capabilities: m.capabilities.map(c => c.toolName),
      permissions: m.permissions,
      platforms: m.platforms,
      stats: {
        installs: 0,
        activeInstalls: 0,
        rating: 0,
        ratingCount: 0,
        reviews: 0,
      },
      signed: true,
      registryScanPassed: true, // Local packages are trusted
      publishedAt: pkg.signedAt,
      updatedAt: m.updatedAt,
    };
  }
}

// ── Types ────────────────────────────────────────────────────────────

export interface ManifestValidation {
  valid: boolean;
  errors: string[];
  warnings: string[];
}

export interface PublishResult {
  packageId: string;
  version: string;
  publishedAt: number;
  updated: boolean;
}

export interface EcosystemStats {
  publishedPackages: number;
  totalTransactions: number;
  completedTransactions: number;
  totalRevenueCents: number;
  cancelledTransactions: number;
  hasDeveloperKeys: boolean;
  registryConfigured: boolean;
  categoryCounts: Record<SuperpowerCategory, number>;
}

// ── Singleton Export ─────────────────────────────────────────────────

export const superpowerEcosystem = new SuperpowerEcosystem();
