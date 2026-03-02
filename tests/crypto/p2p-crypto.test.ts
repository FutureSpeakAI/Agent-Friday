/**
 * Tests for P2P cryptographic protocol — Ed25519 signing, X25519 ECDH,
 * HKDF key derivation, AES-256-GCM encryption, and message signing.
 *
 * Covers security fixes:
 *   HIGH-001: deepSortKeys canonicalization (tested in deep-sort-keys.test.ts)
 *   HIGH-002: Replay protection (message ID in signable)
 *   HIGH-003: HKDF key derivation for AES from ECDH shared secret
 *   LOW-003:  8-character pairing codes
 */

import { describe, it, expect } from 'vitest';
import crypto from 'crypto';

// ── Re-implement the pure crypto helpers (they are module-private) ────
// We test the same algorithms used in agent-network.ts to validate
// correctness without importing the Electron-dependent module.

function generateSigningKeyPair(): { publicKey: string; privateKey: string } {
  const { publicKey, privateKey } = crypto.generateKeyPairSync('ed25519', {
    publicKeyEncoding: { type: 'spki', format: 'der' },
    privateKeyEncoding: { type: 'pkcs8', format: 'der' },
  });
  return {
    publicKey: publicKey.toString('base64'),
    privateKey: privateKey.toString('base64'),
  };
}

function generateExchangeKeyPair(): { publicKey: string; privateKey: string } {
  const { publicKey, privateKey } = crypto.generateKeyPairSync('x25519', {
    publicKeyEncoding: { type: 'spki', format: 'der' },
    privateKeyEncoding: { type: 'pkcs8', format: 'der' },
  });
  return {
    publicKey: publicKey.toString('base64'),
    privateKey: privateKey.toString('base64'),
  };
}

function deriveSharedSecret(ourPrivateKeyB64: string, theirPublicKeyB64: string): string {
  const privateKey = crypto.createPrivateKey({
    key: Buffer.from(ourPrivateKeyB64, 'base64'),
    format: 'der',
    type: 'pkcs8',
  });
  const publicKey = crypto.createPublicKey({
    key: Buffer.from(theirPublicKeyB64, 'base64'),
    format: 'der',
    type: 'spki',
  });
  return crypto.diffieHellman({ privateKey, publicKey }).toString('hex');
}

/** Crypto Sprint 2: Deterministic HKDF salt from both peers' exchange public keys. */
function computeHkdfSalt(ourExchangePubKeyB64: string, theirExchangePubKeyB64: string): string {
  const keys = [ourExchangePubKeyB64, theirExchangePubKeyB64].sort();
  return crypto.createHash('sha256').update(keys.join('|')).digest('hex');
}

function deriveAesKey(sharedSecretHex: string, hkdfSaltHex?: string): Buffer {
  const ikm = Buffer.from(sharedSecretHex, 'hex');
  const salt = hkdfSaltHex ? Buffer.from(hkdfSaltHex, 'hex') : Buffer.alloc(0);
  return Buffer.from(
    crypto.hkdfSync('sha256', ikm, salt, 'AgentFriday-P2P-AES256GCM-v1', 32),
  );
}

function ed25519Sign(data: string, privateKeyB64: string): string {
  const key = crypto.createPrivateKey({
    key: Buffer.from(privateKeyB64, 'base64'),
    format: 'der',
    type: 'pkcs8',
  });
  return crypto.sign(null, Buffer.from(data, 'utf8'), key).toString('hex');
}

function ed25519Verify(data: string, signatureHex: string, publicKeyB64: string): boolean {
  try {
    const key = crypto.createPublicKey({
      key: Buffer.from(publicKeyB64, 'base64'),
      format: 'der',
      type: 'spki',
    });
    return crypto.verify(null, Buffer.from(data, 'utf8'), key, Buffer.from(signatureHex, 'hex'));
  } catch {
    return false;
  }
}

function encryptPayload(payload: string, sharedSecretHex: string) {
  const key = deriveAesKey(sharedSecretHex);
  const nonce = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv('aes-256-gcm', key, nonce);
  const encrypted = Buffer.concat([cipher.update(payload, 'utf8'), cipher.final()]);
  return {
    encrypted: encrypted.toString('base64'),
    nonce: nonce.toString('base64'),
    authTag: cipher.getAuthTag().toString('base64'),
  };
}

function decryptPayload(encryptedB64: string, nonceB64: string, authTagB64: string, sharedSecretHex: string): string {
  const key = deriveAesKey(sharedSecretHex);
  const decipher = crypto.createDecipheriv('aes-256-gcm', key, Buffer.from(nonceB64, 'base64'));
  decipher.setAuthTag(Buffer.from(authTagB64, 'base64'));
  const decrypted = Buffer.concat([
    decipher.update(Buffer.from(encryptedB64, 'base64')),
    decipher.final(),
  ]);
  return decrypted.toString('utf8');
}

function deriveAgentId(signingPublicKeyB64: string): string {
  const hash = crypto.createHash('sha256').update(Buffer.from(signingPublicKeyB64, 'base64')).digest();
  return hash.subarray(0, 8).toString('hex');
}

// ── deepSortKeys for canonicalize ────────────────────────────────────

function deepSortKeys(value: unknown): unknown {
  if (value === null || value === undefined) return value;
  if (Array.isArray(value)) return value.map(deepSortKeys);
  if (typeof value === 'object') {
    const sorted: Record<string, unknown> = {};
    for (const key of Object.keys(value as Record<string, unknown>).sort()) {
      sorted[key] = deepSortKeys((value as Record<string, unknown>)[key]);
    }
    return sorted;
  }
  return value;
}

function canonicalize(obj: unknown): string {
  return JSON.stringify(deepSortKeys(obj));
}

// ═══════════════════════════════════════════════════════════════════════

describe('Ed25519 signing and verification', () => {
  const { publicKey, privateKey } = generateSigningKeyPair();

  it('generates valid Ed25519 key pair', () => {
    expect(publicKey).toBeTruthy();
    expect(privateKey).toBeTruthy();
    // DER-encoded Ed25519 public key is 44 bytes → 60 base64 chars
    expect(Buffer.from(publicKey, 'base64').length).toBe(44);
  });

  it('signs and verifies a message round-trip', () => {
    const message = 'Hello, Agent Friday!';
    const sig = ed25519Sign(message, privateKey);
    expect(ed25519Verify(message, sig, publicKey)).toBe(true);
  });

  it('rejects tampered message', () => {
    const sig = ed25519Sign('original', privateKey);
    expect(ed25519Verify('tampered', sig, publicKey)).toBe(false);
  });

  it('rejects wrong public key', () => {
    const other = generateSigningKeyPair();
    const sig = ed25519Sign('test', privateKey);
    expect(ed25519Verify('test', sig, other.publicKey)).toBe(false);
  });

  it('rejects malformed signature', () => {
    expect(ed25519Verify('test', 'deadbeef', publicKey)).toBe(false);
  });

  it('rejects malformed public key', () => {
    expect(ed25519Verify('test', ed25519Sign('test', privateKey), 'not-a-key')).toBe(false);
  });
});

describe('X25519 ECDH key agreement', () => {
  it('derives identical shared secrets from both sides', () => {
    const alice = generateExchangeKeyPair();
    const bob = generateExchangeKeyPair();

    const secretAlice = deriveSharedSecret(alice.privateKey, bob.publicKey);
    const secretBob = deriveSharedSecret(bob.privateKey, alice.publicKey);

    expect(secretAlice).toBe(secretBob);
    expect(secretAlice.length).toBe(64); // 32 bytes hex
  });

  it('derives different secrets for different peer pairs', () => {
    const alice = generateExchangeKeyPair();
    const bob = generateExchangeKeyPair();
    const carol = generateExchangeKeyPair();

    const aliceBob = deriveSharedSecret(alice.privateKey, bob.publicKey);
    const aliceCarol = deriveSharedSecret(alice.privateKey, carol.publicKey);

    expect(aliceBob).not.toBe(aliceCarol);
  });
});

describe('HKDF key derivation (HIGH-003)', () => {
  it('derives a 32-byte AES key from shared secret', () => {
    const secret = crypto.randomBytes(32).toString('hex');
    const aesKey = deriveAesKey(secret);
    expect(aesKey.length).toBe(32);
  });

  it('produces deterministic output for same input', () => {
    const secret = crypto.randomBytes(32).toString('hex');
    expect(deriveAesKey(secret)).toEqual(deriveAesKey(secret));
  });

  it('produces different keys for different secrets', () => {
    const a = crypto.randomBytes(32).toString('hex');
    const b = crypto.randomBytes(32).toString('hex');
    expect(deriveAesKey(a)).not.toEqual(deriveAesKey(b));
  });

  it('HKDF output differs from raw truncation (the old broken method)', () => {
    const secret = crypto.randomBytes(32).toString('hex');
    const hkdfKey = deriveAesKey(secret);
    const rawTruncated = Buffer.from(secret, 'hex').subarray(0, 32);
    // The whole point of HIGH-003: HKDF output ≠ raw ECDH bytes
    expect(hkdfKey.equals(rawTruncated)).toBe(false);
  });

  it('uses protocol-bound info string (prevents cross-protocol attacks)', () => {
    const secret = crypto.randomBytes(32).toString('hex');
    const ikm = Buffer.from(secret, 'hex');

    // Same IKM, different info → different key (this is the security property)
    const keyOurs = Buffer.from(
      crypto.hkdfSync('sha256', ikm, Buffer.alloc(0), 'AgentFriday-P2P-AES256GCM-v1', 32),
    );
    const keyOther = Buffer.from(
      crypto.hkdfSync('sha256', ikm, Buffer.alloc(0), 'SomeOtherProtocol-v1', 32),
    );

    expect(keyOurs.equals(keyOther)).toBe(false);
  });
});

describe('HKDF salt for P2P key derivation (Crypto Sprint 2)', () => {
  const alice = generateExchangeKeyPair();
  const bob = generateExchangeKeyPair();
  const carol = generateExchangeKeyPair();
  const sharedSecret = crypto.randomBytes(32).toString('hex');

  it('computeHkdfSalt produces a 64-char hex string (32 bytes)', () => {
    const salt = computeHkdfSalt(alice.publicKey, bob.publicKey);
    expect(salt.length).toBe(64);
    expect(/^[0-9a-f]+$/.test(salt)).toBe(true);
  });

  it('computeHkdfSalt is deterministic', () => {
    const salt1 = computeHkdfSalt(alice.publicKey, bob.publicKey);
    const salt2 = computeHkdfSalt(alice.publicKey, bob.publicKey);
    expect(salt1).toBe(salt2);
  });

  it('computeHkdfSalt is order-independent (both sides compute same value)', () => {
    const saltAlice = computeHkdfSalt(alice.publicKey, bob.publicKey);
    const saltBob = computeHkdfSalt(bob.publicKey, alice.publicKey);
    expect(saltAlice).toBe(saltBob);
  });

  it('different peer pairs produce different salts', () => {
    const saltAB = computeHkdfSalt(alice.publicKey, bob.publicKey);
    const saltAC = computeHkdfSalt(alice.publicKey, carol.publicKey);
    const saltBC = computeHkdfSalt(bob.publicKey, carol.publicKey);
    expect(saltAB).not.toBe(saltAC);
    expect(saltAB).not.toBe(saltBC);
    expect(saltAC).not.toBe(saltBC);
  });

  it('deriveAesKey with salt differs from without salt', () => {
    const salt = computeHkdfSalt(alice.publicKey, bob.publicKey);
    const keyWithSalt = deriveAesKey(sharedSecret, salt);
    const keyWithoutSalt = deriveAesKey(sharedSecret);
    expect(keyWithSalt.equals(keyWithoutSalt)).toBe(false);
  });

  it('deriveAesKey with undefined salt equals empty salt (legacy compat)', () => {
    const keyUndefined = deriveAesKey(sharedSecret, undefined);
    const keyEmpty = deriveAesKey(sharedSecret);
    expect(keyUndefined.equals(keyEmpty)).toBe(true);
  });

  it('same shared secret + different salts → different AES keys', () => {
    const saltAB = computeHkdfSalt(alice.publicKey, bob.publicKey);
    const saltAC = computeHkdfSalt(alice.publicKey, carol.publicKey);
    const keyAB = deriveAesKey(sharedSecret, saltAB);
    const keyAC = deriveAesKey(sharedSecret, saltAC);
    expect(keyAB.equals(keyAC)).toBe(false);
  });

  it('backward compat: decrypt legacy (unsalted) message when salt is available', () => {
    // Simulate a legacy peer encrypting without salt
    const legacyKey = deriveAesKey(sharedSecret); // no salt
    const nonce = crypto.randomBytes(12);
    const cipher = crypto.createCipheriv('aes-256-gcm', legacyKey, nonce);
    const encrypted = Buffer.concat([cipher.update('legacy message', 'utf8'), cipher.final()]);
    const authTag = cipher.getAuthTag();

    // New peer has a salt but should fall back to empty salt on failure
    const salt = computeHkdfSalt(alice.publicKey, bob.publicKey);
    const newKey = deriveAesKey(sharedSecret, salt);

    // Direct decryption with salted key should fail
    const decipher1 = crypto.createDecipheriv('aes-256-gcm', newKey, nonce);
    decipher1.setAuthTag(authTag);
    expect(() => {
      Buffer.concat([decipher1.update(encrypted), decipher1.final()]);
    }).toThrow();

    // Fallback to empty salt should succeed
    const fallbackKey = deriveAesKey(sharedSecret); // empty salt
    const decipher2 = crypto.createDecipheriv('aes-256-gcm', fallbackKey, nonce);
    decipher2.setAuthTag(authTag);
    const decrypted = Buffer.concat([decipher2.update(encrypted), decipher2.final()]);
    expect(decrypted.toString('utf8')).toBe('legacy message');
  });
});

describe('AES-256-GCM encryption/decryption', () => {
  const sharedSecret = crypto.randomBytes(32).toString('hex');

  it('round-trips plaintext through encrypt → decrypt', () => {
    const plaintext = 'Hello, encrypted world!';
    const { encrypted, nonce, authTag } = encryptPayload(plaintext, sharedSecret);
    const decrypted = decryptPayload(encrypted, nonce, authTag, sharedSecret);
    expect(decrypted).toBe(plaintext);
  });

  it('round-trips JSON payloads', () => {
    const payload = JSON.stringify({ task: 'search', query: 'weather in Tokyo', nested: { a: 1 } });
    const { encrypted, nonce, authTag } = encryptPayload(payload, sharedSecret);
    const decrypted = decryptPayload(encrypted, nonce, authTag, sharedSecret);
    expect(JSON.parse(decrypted)).toEqual(JSON.parse(payload));
  });

  it('round-trips unicode content', () => {
    const text = '日本語テスト 🎌 Ñoño àéîõü';
    const { encrypted, nonce, authTag } = encryptPayload(text, sharedSecret);
    expect(decryptPayload(encrypted, nonce, authTag, sharedSecret)).toBe(text);
  });

  it('round-trips empty string', () => {
    const { encrypted, nonce, authTag } = encryptPayload('', sharedSecret);
    expect(decryptPayload(encrypted, nonce, authTag, sharedSecret)).toBe('');
  });

  it('produces different ciphertext each time (unique nonce)', () => {
    const plaintext = 'same message';
    const enc1 = encryptPayload(plaintext, sharedSecret);
    const enc2 = encryptPayload(plaintext, sharedSecret);
    expect(enc1.encrypted).not.toBe(enc2.encrypted);
    expect(enc1.nonce).not.toBe(enc2.nonce);
  });

  it('rejects decryption with wrong shared secret', () => {
    const wrongSecret = crypto.randomBytes(32).toString('hex');
    const { encrypted, nonce, authTag } = encryptPayload('secret data', sharedSecret);
    expect(() => decryptPayload(encrypted, nonce, authTag, wrongSecret)).toThrow();
  });

  it('rejects tampered ciphertext (GCM auth tag verification)', () => {
    const { encrypted, nonce, authTag } = encryptPayload('important data', sharedSecret);
    // Flip a byte in the ciphertext
    const buf = Buffer.from(encrypted, 'base64');
    buf[0] ^= 0xff;
    const tampered = buf.toString('base64');
    expect(() => decryptPayload(tampered, nonce, authTag, sharedSecret)).toThrow();
  });

  it('rejects tampered auth tag', () => {
    const { encrypted, nonce, authTag } = encryptPayload('data', sharedSecret);
    const badTag = Buffer.from(authTag, 'base64');
    badTag[0] ^= 0xff;
    expect(() => decryptPayload(encrypted, nonce, badTag.toString('base64'), sharedSecret)).toThrow();
  });
});

describe('P2P message signing with replay protection (HIGH-002)', () => {
  const { publicKey, privateKey } = generateSigningKeyPair();
  const fromId = 'agent-alice';
  const toId = 'agent-bob';

  function createSignedMessage(
    fromAgentId: string,
    toAgentId: string,
    type: string,
    payload: Record<string, unknown>,
    privKey: string,
  ) {
    const id = crypto.randomUUID();
    const timestamp = Date.now();
    // HIGH-002: message ID is part of the signable
    const signable = `${id}|${fromAgentId}|${toAgentId}|${timestamp}|${type}|${canonicalize(payload)}`;
    const signature = ed25519Sign(signable, privKey);
    return { id, fromAgentId, toAgentId, timestamp, type, payload, signature, encrypted: false };
  }

  function verifyMessageSignature(
    msg: { id: string; fromAgentId: string; toAgentId: string; timestamp: number; type: string; payload: Record<string, unknown>; signature: string },
    pubKey: string,
  ): boolean {
    const signable = `${msg.id}|${msg.fromAgentId}|${msg.toAgentId}|${msg.timestamp}|${msg.type}|${canonicalize(msg.payload)}`;
    return ed25519Verify(signable, msg.signature, pubKey);
  }

  it('creates and verifies a signed message', () => {
    const msg = createSignedMessage(fromId, toId, 'ping', { hello: true }, privateKey);
    expect(verifyMessageSignature(msg, publicKey)).toBe(true);
  });

  it('message ID is included in signature (HIGH-002)', () => {
    const msg = createSignedMessage(fromId, toId, 'ping', {}, privateKey);
    // Changing the ID should invalidate the signature
    const tampered = { ...msg, id: crypto.randomUUID() };
    expect(verifyMessageSignature(tampered, publicKey)).toBe(false);
  });

  it('rejects message with tampered payload', () => {
    const msg = createSignedMessage(fromId, toId, 'task-request', { task: 'original' }, privateKey);
    const tampered = { ...msg, payload: { task: 'malicious' } };
    expect(verifyMessageSignature(tampered, publicKey)).toBe(false);
  });

  it('rejects message with tampered fromAgentId', () => {
    const msg = createSignedMessage(fromId, toId, 'ping', {}, privateKey);
    const tampered = { ...msg, fromAgentId: 'impersonator' };
    expect(verifyMessageSignature(tampered, publicKey)).toBe(false);
  });

  it('rejects message with tampered timestamp', () => {
    const msg = createSignedMessage(fromId, toId, 'ping', {}, privateKey);
    const tampered = { ...msg, timestamp: msg.timestamp + 1000 };
    expect(verifyMessageSignature(tampered, publicKey)).toBe(false);
  });

  it('rejects message with wrong signer', () => {
    const other = generateSigningKeyPair();
    const msg = createSignedMessage(fromId, toId, 'ping', {}, privateKey);
    expect(verifyMessageSignature(msg, other.publicKey)).toBe(false);
  });

  it('signature is deterministic for the same message ID', () => {
    // Since createSignedMessage generates a random ID, we test that
    // the same ID+content produces the same signature
    const id = crypto.randomUUID();
    const timestamp = Date.now();
    const payload = { key: 'value' };
    const signable = `${id}|${fromId}|${toId}|${timestamp}|ping|${canonicalize(payload)}`;
    const sig1 = ed25519Sign(signable, privateKey);
    const sig2 = ed25519Sign(signable, privateKey);
    expect(sig1).toBe(sig2);
  });
});

describe('Agent ID derivation', () => {
  it('derives a 16-char hex ID from public key', () => {
    const { publicKey } = generateSigningKeyPair();
    const agentId = deriveAgentId(publicKey);
    expect(agentId.length).toBe(16); // 8 bytes → 16 hex chars
    expect(/^[0-9a-f]+$/.test(agentId)).toBe(true);
  });

  it('is deterministic for the same public key', () => {
    const { publicKey } = generateSigningKeyPair();
    expect(deriveAgentId(publicKey)).toBe(deriveAgentId(publicKey));
  });

  it('differs for different public keys', () => {
    const a = generateSigningKeyPair();
    const b = generateSigningKeyPair();
    expect(deriveAgentId(a.publicKey)).not.toBe(deriveAgentId(b.publicKey));
  });
});

describe('Pairing code generation (LOW-003)', () => {
  const PAIRING_CODE_LENGTH = 8;
  const PAIRING_CODE_CHARS = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';

  function generatePairingCode(): string {
    const bytes = crypto.randomBytes(PAIRING_CODE_LENGTH);
    let code = '';
    for (let i = 0; i < PAIRING_CODE_LENGTH; i++) {
      code += PAIRING_CODE_CHARS[bytes[i] % PAIRING_CODE_CHARS.length];
    }
    return code;
  }

  it('generates 8-character codes (LOW-003 upgrade from 6)', () => {
    const code = generatePairingCode();
    expect(code.length).toBe(8);
  });

  it('uses only allowed characters (no I/O/0/1 confusion)', () => {
    for (let i = 0; i < 100; i++) {
      const code = generatePairingCode();
      for (const ch of code) {
        expect(PAIRING_CODE_CHARS).toContain(ch);
      }
      // Explicitly check confusing chars are absent
      expect(code).not.toMatch(/[IO01]/);
    }
  });

  it('generates unique codes', () => {
    const codes = new Set<string>();
    for (let i = 0; i < 1000; i++) {
      codes.add(generatePairingCode());
    }
    // With 32^8 = ~1.1 trillion possibilities, 1000 codes should all be unique
    expect(codes.size).toBe(1000);
  });

  it('entropy is ~40 bits (8 chars × log2(32) = 40)', () => {
    const entropyBits = PAIRING_CODE_LENGTH * Math.log2(PAIRING_CODE_CHARS.length);
    expect(entropyBits).toBe(40);
  });
});

describe('Full P2P encrypted message flow', () => {
  it('Alice → Bob: sign, encrypt, decrypt, verify', () => {
    // Generate identities
    const aliceSigning = generateSigningKeyPair();
    const aliceExchange = generateExchangeKeyPair();
    const bobSigning = generateSigningKeyPair();
    const bobExchange = generateExchangeKeyPair();

    const aliceId = deriveAgentId(aliceSigning.publicKey);
    const bobId = deriveAgentId(bobSigning.publicKey);

    // Derive shared secret (both sides agree)
    const sharedSecret = deriveSharedSecret(aliceExchange.privateKey, bobExchange.publicKey);
    const sharedSecretBob = deriveSharedSecret(bobExchange.privateKey, aliceExchange.publicKey);
    expect(sharedSecret).toBe(sharedSecretBob);

    // Alice signs a message
    const msgId = crypto.randomUUID();
    const timestamp = Date.now();
    const payload = { task: 'search', query: 'Agent Friday documentation' };
    const signable = `${msgId}|${aliceId}|${bobId}|${timestamp}|task-request|${canonicalize(payload)}`;
    const signature = ed25519Sign(signable, aliceSigning.privateKey);

    // Alice encrypts the payload
    const payloadStr = JSON.stringify(payload);
    const { encrypted, nonce, authTag } = encryptPayload(payloadStr, sharedSecret);

    // Bob decrypts
    const decryptedStr = decryptPayload(encrypted, nonce, authTag, sharedSecretBob);
    expect(JSON.parse(decryptedStr)).toEqual(payload);

    // Bob verifies Alice's signature (using the decrypted payload)
    expect(ed25519Verify(signable, signature, aliceSigning.publicKey)).toBe(true);
  });
});
