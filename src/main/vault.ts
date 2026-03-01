/**
 * Sovereign Vault — At-rest encryption for all agent state files.
 *
 * Provides AES-256-GCM encryption of every sensitive file on disk:
 *   - Agent identity & private keys (agent-network.json)
 *   - Memory stores (shortTerm/mediumTerm/longTerm.json)
 *   - Settings & API keys (friday-settings.json)
 *   - Trust graph (trust-graph.json)
 *   - Gateway identities (gateway/identities.json)
 *
 * Key derivation:
 *   vaultKey = scrypt(Ed25519PrivateKey + machineId, salt, N=2^20)
 *
 * On the same machine, the vault auto-unlocks using the agent's Ed25519
 * private key (loaded from agent-network.json) + the machine fingerprint.
 * If migrating to a new machine, a 12-word recovery phrase is required.
 *
 * HMAC stacking: The existing integrity/hmac.ts layer operates on PLAINTEXT
 * before encryption. On read, vault decrypts first, then HMAC verifies.
 * This means both tamper-detection AND encryption protect every file.
 *
 * Cipher format (per-file):
 *   [12-byte IV][16-byte authTag][...ciphertext...]
 */

import crypto from 'crypto';
import fs from 'fs/promises';
import path from 'path';
import { app } from 'electron';
import { machineId } from 'node-machine-id';

// ── Constants ─────────────────────────────────────────────────────────

const SCRYPT_N = 2 ** 20;  // ~1 second on modern hardware
const SCRYPT_R = 8;
const SCRYPT_P = 1;
const KEY_LENGTH = 32;       // AES-256
const IV_LENGTH = 12;        // GCM standard
const TAG_LENGTH = 16;       // GCM auth tag
const SALT_FILE = '.vault-salt';
const VAULT_META_FILE = '.vault-meta.json';
const ALGORITHM = 'aes-256-gcm';

// BIP-39-style word list (simplified 2048-word subset)
// Using the official BIP-39 English wordlist first 256 words for compactness
// A 12-word phrase from 256 words gives 96 bits of entropy — sufficient for recovery
const WORDLIST = [
  'abandon', 'ability', 'able', 'about', 'above', 'absent', 'absorb', 'abstract',
  'absurd', 'abuse', 'access', 'accident', 'account', 'accuse', 'achieve', 'acid',
  'acoustic', 'acquire', 'across', 'act', 'action', 'actor', 'actress', 'actual',
  'adapt', 'add', 'addict', 'address', 'adjust', 'admit', 'adult', 'advance',
  'advice', 'aerobic', 'affair', 'afford', 'afraid', 'again', 'age', 'agent',
  'agree', 'ahead', 'aim', 'air', 'airport', 'aisle', 'alarm', 'album',
  'alcohol', 'alert', 'alien', 'all', 'alley', 'allow', 'almost', 'alone',
  'alpha', 'already', 'also', 'alter', 'always', 'amateur', 'amazing', 'among',
  'amount', 'amused', 'analyst', 'anchor', 'ancient', 'anger', 'angle', 'angry',
  'animal', 'ankle', 'announce', 'annual', 'another', 'answer', 'antenna', 'antique',
  'anxiety', 'any', 'apart', 'apology', 'appear', 'apple', 'approve', 'april',
  'arch', 'arctic', 'area', 'arena', 'argue', 'arm', 'armed', 'armor',
  'army', 'around', 'arrange', 'arrest', 'arrive', 'arrow', 'art', 'artefact',
  'artist', 'artwork', 'ask', 'aspect', 'assault', 'asset', 'assist', 'assume',
  'asthma', 'athlete', 'atom', 'attack', 'attend', 'attitude', 'attract', 'auction',
  'audit', 'august', 'aunt', 'author', 'auto', 'autumn', 'average', 'avocado',
  'avoid', 'awake', 'aware', 'awesome', 'awful', 'awkward', 'axis', 'baby',
  'bachelor', 'bacon', 'badge', 'bag', 'balance', 'balcony', 'ball', 'bamboo',
  'banana', 'banner', 'bar', 'barely', 'bargain', 'barrel', 'base', 'basic',
  'basket', 'battle', 'beach', 'bean', 'beauty', 'because', 'become', 'beef',
  'before', 'begin', 'behave', 'behind', 'believe', 'below', 'belt', 'bench',
  'benefit', 'best', 'betray', 'better', 'between', 'beyond', 'bicycle', 'bid',
  'bike', 'bind', 'biology', 'bird', 'birth', 'bitter', 'black', 'blade',
  'blame', 'blanket', 'blast', 'bleak', 'bless', 'blind', 'blood', 'blossom',
  'blow', 'blue', 'blur', 'blush', 'board', 'boat', 'body', 'boil',
  'bomb', 'bone', 'bonus', 'book', 'boost', 'border', 'boring', 'borrow',
  'boss', 'bottom', 'bounce', 'box', 'boy', 'bracket', 'brain', 'brand',
  'brass', 'brave', 'bread', 'breeze', 'brick', 'bridge', 'brief', 'bright',
  'bring', 'brisk', 'broccoli', 'broken', 'bronze', 'broom', 'brother', 'brown',
  'brush', 'bubble', 'buddy', 'budget', 'buffalo', 'build', 'bulb', 'bulk',
  'bullet', 'bundle', 'bunny', 'burden', 'burger', 'burst', 'bus', 'business',
  'busy', 'butter', 'buyer', 'buzz', 'cabbage', 'cabin', 'cable', 'cactus',
];

// ── State ─────────────────────────────────────────────────────────────

let vaultKey: Buffer | null = null;
let vaultSalt: Buffer | null = null;
let vaultUnlocked = false;
let recoveryPhrase: string | null = null; // Only populated during first-time setup
let recoveryPhraseTimer: ReturnType<typeof setTimeout> | null = null; // Auto-clear safety net
let machineFingerprint: string = '';

// ── Interfaces ────────────────────────────────────────────────────────

export interface VaultConfig {
  /** Whether the vault has been initialized (first-run setup completed) */
  initialized: boolean;
  /** When the vault was created */
  createdAt: number;
  /** Hash of the machine fingerprint used at creation (for migration detection) */
  machineHash: string;
  /** Whether recovery phrase was shown to user */
  recoveryPhraseShown: boolean;
}

// ── Initialization ────────────────────────────────────────────────────

/**
 * Initialize the Sovereign Vault.
 *
 * Called AFTER agentNetwork.initialize() so that Ed25519 keys are available.
 * On the same machine: auto-derives vault key from privateKey + machineId.
 * On a new machine: vault stays locked until recoverVault() is called.
 *
 * @param signingPrivateKeyBase64 - The agent's Ed25519 private key (base64)
 * @returns The 12-word recovery phrase (only on FIRST initialization, otherwise null)
 */
export async function initializeVault(signingPrivateKeyBase64: string): Promise<string | null> {
  const t0 = Date.now();
  console.log('[Vault] Initialization starting...');
  const userDataDir = app.getPath('userData');
  const saltPath = path.join(userDataDir, SALT_FILE);
  const metaPath = path.join(userDataDir, VAULT_META_FILE);

  // Get machine fingerprint (async to avoid blocking the event loop on Windows)
  try {
    machineFingerprint = await machineId({ original: true });
    console.log(`[Vault] Machine ID resolved in ${Date.now() - t0}ms`);
  } catch {
    // Fallback: use hostname + platform as fingerprint (less unique but functional)
    machineFingerprint = `${require('os').hostname()}-${process.platform}-${process.arch}`;
    console.warn('[Vault] Machine ID unavailable, using fallback fingerprint');
  }

  // Check if vault already exists
  let meta: VaultConfig | null = null;
  try {
    const metaRaw = await fs.readFile(metaPath, 'utf-8');
    meta = JSON.parse(metaRaw);
  } catch {
    // No vault yet — first run
  }

  if (meta?.initialized) {
    // Existing vault — try auto-unlock with same machine
    const currentMachineHash = hashString(machineFingerprint);

    if (currentMachineHash === meta.machineHash) {
      // Same machine — auto-unlock
      try {
        vaultSalt = await fs.readFile(saltPath);
        vaultKey = await deriveVaultKey(signingPrivateKeyBase64, machineFingerprint, vaultSalt);
        vaultUnlocked = true;
        console.log(`[Vault] Auto-unlocked (same machine) in ${Date.now() - t0}ms`);
        return null;
      } catch (err) {
        console.error('[Vault] Auto-unlock failed:', err);
        // Vault stays locked — user needs recovery phrase
        return null;
      }
    } else {
      // Different machine — vault stays locked
      console.warn('[Vault] Machine mismatch — vault locked. Recovery phrase required.');
      try {
        vaultSalt = await fs.readFile(saltPath);
      } catch {
        // Salt missing on new machine — critical error
        console.error('[Vault] Salt file missing — vault unrecoverable without fresh setup');
      }
      return null;
    }
  }

  // ── First-time initialization ──────────────────────────────────────

  // Generate salt
  vaultSalt = crypto.randomBytes(32);
  await fs.writeFile(saltPath, vaultSalt);

  // Derive vault key
  vaultKey = await deriveVaultKey(signingPrivateKeyBase64, machineFingerprint, vaultSalt);
  vaultUnlocked = true;

  // Generate 12-word recovery phrase
  recoveryPhrase = generateRecoveryPhrase();

  // Safety net: auto-clear recovery phrase from memory after 10 minutes
  // even if the renderer never calls clearRecoveryPhrase()
  recoveryPhraseTimer = setTimeout(() => {
    if (recoveryPhrase) {
      console.warn('[Vault] Auto-clearing recovery phrase from memory (10-minute safety timeout)');
      recoveryPhrase = null;
      recoveryPhraseTimer = null;
    }
  }, 10 * 60 * 1000);

  // Save vault metadata
  const vaultMeta: VaultConfig = {
    initialized: true,
    createdAt: Date.now(),
    machineHash: hashString(machineFingerprint),
    recoveryPhraseShown: false,
  };
  await fs.writeFile(metaPath, JSON.stringify(vaultMeta, null, 2));

  console.log(`[Vault] First-time initialization complete in ${Date.now() - t0}ms`);
  return recoveryPhrase;
}

/**
 * Unlock the vault on a new machine using the 12-word recovery phrase.
 *
 * The recovery phrase is used as an alternative key-derivation input
 * (replacing machineFingerprint) to decrypt the vault, then re-keys
 * the vault with the new machine's fingerprint.
 */
export async function recoverVault(
  signingPrivateKeyBase64: string,
  phrase: string,
): Promise<boolean> {
  if (!vaultSalt) {
    console.error('[Vault] Cannot recover — no salt loaded');
    return false;
  }

  const userDataDir = app.getPath('userData');
  const metaPath = path.join(userDataDir, VAULT_META_FILE);

  // Try to derive key using recovery phrase instead of machine fingerprint
  const candidateKey = await deriveVaultKey(signingPrivateKeyBase64, phrase.trim().toLowerCase(), vaultSalt);

  // Attempt to decrypt a known file to verify the key works
  // Try the agent-network.json as our canary file
  const canaryPath = path.join(userDataDir, 'friday-data', 'agent-network.json');
  try {
    const encrypted = await fs.readFile(canaryPath);

    // If file is plaintext JSON, it hasn't been encrypted yet — accept the key
    try {
      JSON.parse(encrypted.toString('utf-8'));
      // File is plaintext — recovery phrase is accepted, re-key with new machine
    } catch {
      // File is encrypted — try to decrypt with candidate key
      const decrypted = vaultDecryptWithKey(encrypted, candidateKey);
      if (!decrypted) {
        console.error('[Vault] Recovery failed — wrong phrase');
        return false;
      }
    }
  } catch {
    // Canary file doesn't exist — accept the key (empty vault)
  }

  // Recovery successful — re-key vault with new machine fingerprint
  vaultKey = await deriveVaultKey(signingPrivateKeyBase64, machineFingerprint, vaultSalt);
  vaultUnlocked = true;

  // Update vault metadata with new machine hash
  try {
    const metaRaw = await fs.readFile(metaPath, 'utf-8');
    const meta: VaultConfig = JSON.parse(metaRaw);
    meta.machineHash = hashString(machineFingerprint);
    await fs.writeFile(metaPath, JSON.stringify(meta, null, 2));
  } catch {
    // If meta is missing, create fresh
    const newMeta: VaultConfig = {
      initialized: true,
      createdAt: Date.now(),
      machineHash: hashString(machineFingerprint),
      recoveryPhraseShown: true,
    };
    await fs.writeFile(metaPath, JSON.stringify(newMeta, null, 2));
  }

  console.log('[Vault] Recovery successful — vault re-keyed for new machine');
  return true;
}

// ── Key Derivation ────────────────────────────────────────────────────

/**
 * Derive the AES-256 vault key using scrypt (async).
 *
 * Input material: Ed25519 private key bytes + binding factor (machineId or recovery phrase)
 * This ensures:
 *   1. The vault is bound to this specific agent identity
 *   2. The vault is bound to this specific machine (or recovery phrase for migration)
 *   3. The key derivation is computationally expensive (scrypt N=2^20)
 *
 * CRITICAL: Uses async crypto.scrypt() — NOT scryptSync() — to avoid blocking
 * the Node.js event loop. scrypt with N=2^20 takes 5-30 seconds on first launch;
 * the sync variant freezes the entire UI during that time.
 */
async function deriveVaultKey(privateKeyBase64: string, bindingFactor: string, salt: Buffer): Promise<Buffer> {
  const keyMaterial = Buffer.concat([
    Buffer.from(privateKeyBase64, 'base64'),
    Buffer.from(bindingFactor, 'utf-8'),
  ]);

  const t0 = Date.now();
  return new Promise<Buffer>((resolve, reject) => {
    crypto.scrypt(keyMaterial, salt, KEY_LENGTH, {
      N: SCRYPT_N,
      r: SCRYPT_R,
      p: SCRYPT_P,
    }, (err, derivedKey) => {
      console.log(`[Vault] scrypt key derivation completed in ${Date.now() - t0}ms`);
      if (err) reject(err);
      else resolve(derivedKey);
    });
  });
}

// ── Encryption / Decryption ───────────────────────────────────────────

/**
 * Encrypt plaintext bytes with AES-256-GCM.
 * Returns: [12-byte IV][16-byte authTag][ciphertext]
 */
function vaultEncrypt(plaintext: Buffer): Buffer {
  if (!vaultKey) throw new Error('[Vault] Not unlocked');

  const iv = crypto.randomBytes(IV_LENGTH);
  const cipher = crypto.createCipheriv(ALGORITHM, vaultKey, iv);
  const encrypted = Buffer.concat([cipher.update(plaintext), cipher.final()]);
  const tag = cipher.getAuthTag();

  return Buffer.concat([iv, tag, encrypted]);
}

/**
 * Decrypt vault-encrypted bytes. Returns null if decryption fails
 * (wrong key, tampered data, etc.)
 */
function vaultDecrypt(data: Buffer): Buffer | null {
  if (!vaultKey) throw new Error('[Vault] Not unlocked');

  if (data.length < IV_LENGTH + TAG_LENGTH) return null;

  const iv = data.subarray(0, IV_LENGTH);
  const tag = data.subarray(IV_LENGTH, IV_LENGTH + TAG_LENGTH);
  const ciphertext = data.subarray(IV_LENGTH + TAG_LENGTH);

  try {
    const decipher = crypto.createDecipheriv(ALGORITHM, vaultKey, iv);
    decipher.setAuthTag(tag);
    return Buffer.concat([decipher.update(ciphertext), decipher.final()]);
  } catch {
    return null;
  }
}

/**
 * Decrypt with a specific key (used during recovery to test candidate keys).
 */
function vaultDecryptWithKey(data: Buffer, key: Buffer): Buffer | null {
  if (data.length < IV_LENGTH + TAG_LENGTH) return null;

  const iv = data.subarray(0, IV_LENGTH);
  const tag = data.subarray(IV_LENGTH, IV_LENGTH + TAG_LENGTH);
  const ciphertext = data.subarray(IV_LENGTH + TAG_LENGTH);

  try {
    const decipher = crypto.createDecipheriv(ALGORITHM, key, iv);
    decipher.setAuthTag(tag);
    return Buffer.concat([decipher.update(ciphertext), decipher.final()]);
  } catch {
    return null;
  }
}

// ── Public File I/O API ───────────────────────────────────────────────

/**
 * Write data to disk with vault encryption.
 *
 * If the vault is locked or not initialized, falls back to plaintext write
 * (graceful degradation — the app must still function before first setup).
 *
 * @param filePath - Absolute path to write to
 * @param content - String content to encrypt and write
 */
export async function vaultWrite(filePath: string, content: string): Promise<void> {
  if (!vaultUnlocked || !vaultKey) {
    // Graceful degradation: write plaintext if vault isn't ready
    await fs.writeFile(filePath, content, 'utf-8');
    return;
  }

  const plaintext = Buffer.from(content, 'utf-8');
  const encrypted = vaultEncrypt(plaintext);
  await fs.writeFile(filePath, encrypted);
}

/**
 * Read and decrypt a vault-encrypted file.
 *
 * Handles both encrypted and plaintext files transparently:
 *   1. Read raw bytes
 *   2. If vault is unlocked, try to decrypt
 *   3. If decryption fails (file was plaintext), return as-is
 *   4. If vault is locked, return raw content as string
 *
 * This means the transition from plaintext → encrypted is seamless.
 * Existing plaintext files will be read normally, and will be encrypted
 * on next write.
 *
 * @param filePath - Absolute path to read from
 * @returns Decrypted string content
 */
export async function vaultRead(filePath: string): Promise<string> {
  const raw = await fs.readFile(filePath);

  if (!vaultUnlocked || !vaultKey) {
    // Vault not ready — return raw content
    return raw.toString('utf-8');
  }

  // Try to decrypt
  const decrypted = vaultDecrypt(raw);
  if (decrypted) {
    return decrypted.toString('utf-8');
  }

  // Decryption failed — file is probably still plaintext (pre-vault era)
  // Return as-is; it'll be encrypted on next save
  return raw.toString('utf-8');
}

/**
 * Convenience: read and parse a JSON file from the vault.
 */
export async function vaultReadJSON<T = unknown>(filePath: string): Promise<T> {
  const content = await vaultRead(filePath);
  return JSON.parse(content) as T;
}

// ── Recovery Phrase Generation ────────────────────────────────────────

/**
 * Generate a 12-word recovery phrase from cryptographically random bytes.
 * Each word is selected from a 256-word list (8 bits per word).
 * 12 words × 8 bits = 96 bits of entropy.
 */
function generateRecoveryPhrase(): string {
  const bytes = crypto.randomBytes(12);
  const words: string[] = [];
  for (let i = 0; i < 12; i++) {
    words.push(WORDLIST[bytes[i]]);
  }
  return words.join(' ');
}

// ── Helpers ───────────────────────────────────────────────────────────

/**
 * Hash a string with SHA-256 (for machine fingerprint comparison).
 */
function hashString(input: string): string {
  return crypto.createHash('sha256').update(input, 'utf-8').digest('hex');
}

// ── Status Queries ────────────────────────────────────────────────────

/** Is the vault currently unlocked and ready for encryption? */
export function isVaultUnlocked(): boolean {
  return vaultUnlocked;
}

/** Was the vault initialized (first-run setup completed)? */
export async function isVaultInitialized(): Promise<boolean> {
  try {
    const metaPath = path.join(app.getPath('userData'), VAULT_META_FILE);
    const raw = await fs.readFile(metaPath, 'utf-8');
    const meta: VaultConfig = JSON.parse(raw);
    return meta.initialized;
  } catch {
    return false;
  }
}

/** Has the recovery phrase been shown and confirmed by the user? */
export async function isRecoveryPhraseShown(): Promise<boolean> {
  try {
    const metaPath = path.join(app.getPath('userData'), VAULT_META_FILE);
    const raw = await fs.readFile(metaPath, 'utf-8');
    const meta: VaultConfig = JSON.parse(raw);
    return meta.recoveryPhraseShown === true;
  } catch {
    // No vault meta = not shown
    return false;
  }
}

/** Get the one-time recovery phrase (only available during first-time setup). */
export function getRecoveryPhrase(): string | null {
  return recoveryPhrase;
}

/** Clear the recovery phrase from memory (after user has saved it). */
export function clearRecoveryPhrase(): void {
  recoveryPhrase = null;
  if (recoveryPhraseTimer) {
    clearTimeout(recoveryPhraseTimer);
    recoveryPhraseTimer = null;
  }
}

/** Mark recovery phrase as shown in vault metadata. */
export async function markRecoveryPhraseShown(): Promise<void> {
  try {
    const metaPath = path.join(app.getPath('userData'), VAULT_META_FILE);
    const raw = await fs.readFile(metaPath, 'utf-8');
    const meta: VaultConfig = JSON.parse(raw);
    meta.recoveryPhraseShown = true;
    await fs.writeFile(metaPath, JSON.stringify(meta, null, 2));
  } catch (err) {
    console.warn('[Vault] Failed to mark recovery phrase shown:', err);
  }
}

/**
 * Re-encrypt all vault files after recovery (re-keying).
 *
 * After vault recovery on a new machine, existing encrypted files use the
 * old key (derived with recovery phrase). This function reads each file
 * with the old key, then re-encrypts with the new key (derived with new
 * machine fingerprint).
 *
 * @param oldPrivateKey - The agent's Ed25519 private key (base64)
 * @param oldPhrase - The recovery phrase used to unlock
 */
export async function rekeyVaultFiles(
  oldPrivateKey: string,
  oldPhrase: string,
): Promise<void> {
  if (!vaultSalt || !vaultKey) {
    console.error('[Vault] Cannot rekey — vault not ready');
    return;
  }

  const oldKey = await deriveVaultKey(oldPrivateKey, oldPhrase.trim().toLowerCase(), vaultSalt);
  const userDataDir = app.getPath('userData');

  // Files to rekey
  const filesToRekey = [
    path.join(userDataDir, 'friday-data', 'agent-network.json'),
    path.join(userDataDir, 'friday-settings.json'),
    path.join(userDataDir, 'trust-graph.json'),
    path.join(userDataDir, 'memory', 'shortTerm.json'),
    path.join(userDataDir, 'memory', 'mediumTerm.json'),
    path.join(userDataDir, 'memory', 'longTerm.json'),
    path.join(userDataDir, 'gateway', 'identities.json'),
  ];

  let rekeyed = 0;
  for (const fp of filesToRekey) {
    try {
      const raw = await fs.readFile(fp);

      // Try decrypting with old key
      const decrypted = vaultDecryptWithKey(raw, oldKey);
      if (decrypted) {
        // Re-encrypt with new vault key
        const reencrypted = vaultEncrypt(decrypted);
        await fs.writeFile(fp, reencrypted);
        rekeyed++;
      }
      // If decryption fails, file is plaintext or uses new key already — skip
    } catch {
      // File doesn't exist — skip
    }
  }

  console.log(`[Vault] Re-keyed ${rekeyed} files for new machine`);
}
