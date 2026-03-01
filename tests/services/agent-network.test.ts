/**
 * Agent Network Protocol — comprehensive tests (Track VII, Phase 2).
 *
 * Coverage:
 *   ✓ Pure functions: sign, verify, encrypt, decrypt, trust derivation, auto-approve
 *   ✓ Class: initialization, identity, pairing, trust, capabilities, messaging,
 *     delegation lifecycle, stats, config, prompt context, persistence
 *
 * 70+ tests covering every public API surface.
 */

import crypto from 'crypto';

// ── Electron + FS Mocks ──────────────────────────────────────────────

vi.mock('electron', () => ({
  app: { getPath: vi.fn(() => '/tmp/test-agent-net') },
}));

vi.mock('fs/promises', () => ({
  default: {
    mkdir: vi.fn().mockResolvedValue(undefined),
    readFile: vi.fn().mockRejectedValue(new Error('ENOENT')),
    writeFile: vi.fn().mockResolvedValue(undefined),
  },
}));


import {
  createSignedMessage,
  verifyMessageSignature,
  encryptMessage,
  decryptMessage,
  deriveAgentTrust,
  canAutoApprove,
  AgentNetwork,
  type AgentIdentity,
  type PairedAgent,
} from '../../src/main/agent-network';

import _fs from 'fs/promises';
const mockFs = _fs as unknown as {
  mkdir: ReturnType<typeof vi.fn>;
  readFile: ReturnType<typeof vi.fn>;
  writeFile: ReturnType<typeof vi.fn>;
};

// ── Test Crypto Helpers ──────────────────────────────────────────────

function generateTestKeys() {
  const signing = crypto.generateKeyPairSync('ed25519', {
    publicKeyEncoding: { type: 'spki', format: 'der' },
    privateKeyEncoding: { type: 'pkcs8', format: 'der' },
  });
  const exchange = crypto.generateKeyPairSync('x25519', {
    publicKeyEncoding: { type: 'spki', format: 'der' },
    privateKeyEncoding: { type: 'pkcs8', format: 'der' },
  });
  const sigPubB64 = (signing.publicKey as Buffer).toString('base64');
  const agentId = crypto.createHash('sha256')
    .update(signing.publicKey as Buffer)
    .digest()
    .subarray(0, 8)
    .toString('hex');

  return {
    agentId,
    signingPrivateKey: (signing.privateKey as Buffer).toString('base64'),
    signingPublicKey: sigPubB64,
    exchangePrivateKey: (exchange.privateKey as Buffer).toString('base64'),
    exchangePublicKey: (exchange.publicKey as Buffer).toString('base64'),
  };
}

function buildIdentity(
  keys: ReturnType<typeof generateTestKeys>,
  ownerName = 'TestOwner',
  instanceName = 'Test Agent',
): AgentIdentity {
  return {
    agentId: keys.agentId,
    signingPublicKey: keys.signingPublicKey,
    exchangePublicKey: keys.exchangePublicKey,
    ownerName,
    instanceName,
    createdAt: Date.now(),
  };
}

function deriveTestSharedSecret(ourExPrivB64: string, theirExPubB64: string): string {
  const priv = crypto.createPrivateKey({
    key: Buffer.from(ourExPrivB64, 'base64'), format: 'der', type: 'pkcs8',
  });
  const pub = crypto.createPublicKey({
    key: Buffer.from(theirExPubB64, 'base64'), format: 'der', type: 'spki',
  });
  return crypto.diffieHellman({ privateKey: priv, publicKey: pub }).toString('hex');
}

function makePeer(
  keys: ReturnType<typeof generateTestKeys>,
  overrides: Partial<PairedAgent> = {},
): PairedAgent {
  return {
    identity: buildIdentity(keys),
    pairingTimestamp: Date.now(),
    ownerPersonId: null,
    trustLevel: 0.5,
    sharedSecret: crypto.randomBytes(32).toString('hex'),
    lastSeen: Date.now(),
    status: 'paired',
    autoApproveTaskTypes: [],
    advertisedCapabilities: [],
    ...overrides,
  };
}

// ═══════════════════════════════════════════════════════════════════════
// PURE FUNCTION TESTS
// ═══════════════════════════════════════════════════════════════════════

describe('Pure Functions', () => {
  const alice = generateTestKeys();
  const bob = generateTestKeys();

  // ── createSignedMessage ────────────────────────────────────────────

  describe('createSignedMessage', () => {
    it('returns a message with correct structure', () => {
      const msg = createSignedMessage(alice.agentId, bob.agentId, 'ping', { hello: 'world' }, alice.signingPrivateKey);
      expect(msg.id).toBeDefined();
      expect(msg.fromAgentId).toBe(alice.agentId);
      expect(msg.toAgentId).toBe(bob.agentId);
      expect(msg.type).toBe('ping');
      expect(msg.payload).toEqual({ hello: 'world' });
      expect(msg.encrypted).toBe(false);
      expect(msg.signature).toBeDefined();
      expect(typeof msg.timestamp).toBe('number');
    });

    it('produces a valid Ed25519 signature', () => {
      const msg = createSignedMessage(alice.agentId, bob.agentId, 'ping', { data: 1 }, alice.signingPrivateKey);
      const valid = verifyMessageSignature(msg, alice.signingPublicKey);
      expect(valid).toBe(true);
    });

    it('produces different signatures for different payloads', () => {
      const m1 = createSignedMessage(alice.agentId, bob.agentId, 'ping', { a: 1 }, alice.signingPrivateKey);
      const m2 = createSignedMessage(alice.agentId, bob.agentId, 'ping', { a: 2 }, alice.signingPrivateKey);
      expect(m1.signature).not.toBe(m2.signature);
    });
  });

  // ── verifyMessageSignature ─────────────────────────────────────────

  describe('verifyMessageSignature', () => {
    it('returns true for a valid signature', () => {
      const msg = createSignedMessage(alice.agentId, bob.agentId, 'ping', {}, alice.signingPrivateKey);
      expect(verifyMessageSignature(msg, alice.signingPublicKey)).toBe(true);
    });

    it('returns false for tampered payload', () => {
      const msg = createSignedMessage(alice.agentId, bob.agentId, 'ping', { x: 1 }, alice.signingPrivateKey);
      msg.payload = { x: 2 }; // tamper
      expect(verifyMessageSignature(msg, alice.signingPublicKey)).toBe(false);
    });

    it('returns false for tampered fromAgentId', () => {
      const msg = createSignedMessage(alice.agentId, bob.agentId, 'ping', {}, alice.signingPrivateKey);
      msg.fromAgentId = 'tampered';
      expect(verifyMessageSignature(msg, alice.signingPublicKey)).toBe(false);
    });

    it('returns false for tampered toAgentId', () => {
      const msg = createSignedMessage(alice.agentId, bob.agentId, 'ping', {}, alice.signingPrivateKey);
      msg.toAgentId = 'tampered';
      expect(verifyMessageSignature(msg, alice.signingPublicKey)).toBe(false);
    });

    it('returns false for wrong public key', () => {
      const msg = createSignedMessage(alice.agentId, bob.agentId, 'ping', {}, alice.signingPrivateKey);
      expect(verifyMessageSignature(msg, bob.signingPublicKey)).toBe(false);
    });

    it('returns false for tampered message type', () => {
      const msg = createSignedMessage(alice.agentId, bob.agentId, 'ping', {}, alice.signingPrivateKey);
      (msg as any).type = 'pong';
      expect(verifyMessageSignature(msg, alice.signingPublicKey)).toBe(false);
    });
  });

  // ── encryptMessage / decryptMessage ────────────────────────────────

  describe('encryptMessage / decryptMessage', () => {
    const sharedSecret = crypto.randomBytes(32).toString('hex');

    it('round-trips: encrypt then decrypt returns original payload', () => {
      const msg = createSignedMessage(alice.agentId, bob.agentId, 'ping', { secret: 'data' }, alice.signingPrivateKey);
      const enc = encryptMessage(msg, sharedSecret);
      const dec = decryptMessage(enc, sharedSecret);
      expect(dec.payload).toEqual({ secret: 'data' });
    });

    it('sets encrypted flag to true', () => {
      const msg = createSignedMessage(alice.agentId, bob.agentId, 'ping', {}, alice.signingPrivateKey);
      const enc = encryptMessage(msg, sharedSecret);
      expect(enc.encrypted).toBe(true);
    });

    it('includes nonce and authTag', () => {
      const msg = createSignedMessage(alice.agentId, bob.agentId, 'ping', {}, alice.signingPrivateKey);
      const enc = encryptMessage(msg, sharedSecret);
      expect(enc.nonce).toBeDefined();
      expect(enc.authTag).toBeDefined();
    });

    it('places encrypted data in _encrypted field', () => {
      const msg = createSignedMessage(alice.agentId, bob.agentId, 'ping', { x: 1 }, alice.signingPrivateKey);
      const enc = encryptMessage(msg, sharedSecret);
      expect((enc.payload as any)._encrypted).toBeDefined();
    });

    it('decryption fails with wrong key', () => {
      const msg = createSignedMessage(alice.agentId, bob.agentId, 'ping', { x: 1 }, alice.signingPrivateKey);
      const enc = encryptMessage(msg, sharedSecret);
      const wrongKey = crypto.randomBytes(32).toString('hex');
      expect(() => decryptMessage(enc, wrongKey)).toThrow();
    });

    it('returns unencrypted message unchanged', () => {
      const msg = createSignedMessage(alice.agentId, bob.agentId, 'ping', { x: 1 }, alice.signingPrivateKey);
      const same = decryptMessage(msg, sharedSecret);
      expect(same).toEqual(msg);
    });
  });

  // ── deriveAgentTrust ───────────────────────────────────────────────

  describe('deriveAgentTrust', () => {
    it('returns 0.1 for null owner (unknown)', () => {
      expect(deriveAgentTrust(null)).toBe(0.1);
    });

    it('returns owner overall score for valid input', () => {
      expect(deriveAgentTrust({ overall: 0.75 })).toBe(0.75);
    });

    it('clamps to minimum 0.1', () => {
      expect(deriveAgentTrust({ overall: 0.02 })).toBe(0.1);
    });

    it('clamps to maximum 1.0', () => {
      expect(deriveAgentTrust({ overall: 1.5 })).toBe(1.0);
    });

    it('returns exact mid-range value', () => {
      expect(deriveAgentTrust({ overall: 0.5 })).toBe(0.5);
    });
  });

  // ── canAutoApprove ─────────────────────────────────────────────────

  describe('canAutoApprove', () => {
    const basePeer = makePeer(alice, {
      trustLevel: 0.8,
      autoApproveTaskTypes: ['research', 'summarize'],
    });

    it('returns false when trust < 0.6', () => {
      const lowTrust = { ...basePeer, trustLevel: 0.5 };
      expect(canAutoApprove(lowTrust, { description: 'research topic', requiredCapabilities: [] })).toBe(false);
    });

    it('returns false when autoApproveTaskTypes is empty', () => {
      const noTypes = { ...basePeer, autoApproveTaskTypes: [] };
      expect(canAutoApprove(noTypes, { description: 'research topic', requiredCapabilities: [] })).toBe(false);
    });

    it('returns false when task type not in standing permissions', () => {
      expect(canAutoApprove(basePeer, { description: 'deploy to production', requiredCapabilities: [] })).toBe(false);
    });

    it('returns true when all conditions met', () => {
      expect(canAutoApprove(basePeer, { description: 'research quantum computing', requiredCapabilities: [] })).toBe(true);
    });

    it('returns false when system-access capability required', () => {
      expect(canAutoApprove(basePeer, { description: 'research topic', requiredCapabilities: ['system-access'] })).toBe(false);
    });

    it('returns false when file-write capability required', () => {
      expect(canAutoApprove(basePeer, { description: 'research topic', requiredCapabilities: ['file-write'] })).toBe(false);
    });

    it('returns false when network-admin capability required', () => {
      expect(canAutoApprove(basePeer, { description: 'research topic', requiredCapabilities: ['network-admin'] })).toBe(false);
    });

    it('returns false when credential-access capability required', () => {
      expect(canAutoApprove(basePeer, { description: 'research topic', requiredCapabilities: ['credential-access'] })).toBe(false);
    });

    it('matches task type case-insensitively', () => {
      expect(canAutoApprove(basePeer, { description: 'RESEARCH quantum', requiredCapabilities: [] })).toBe(true);
    });
  });
});

// ═══════════════════════════════════════════════════════════════════════
// AGENT NETWORK CLASS TESTS
// ═══════════════════════════════════════════════════════════════════════

describe('AgentNetwork Class', () => {
  let network: AgentNetwork;

  beforeEach(async () => {
    vi.clearAllMocks();
    mockFs.readFile.mockRejectedValue(new Error('ENOENT'));
    mockFs.writeFile.mockResolvedValue(undefined);
    mockFs.mkdir.mockResolvedValue(undefined);

    network = new AgentNetwork();
    await network.initialize();
    // Clear writes from initialize() so persistence tests only see their own saves
    mockFs.writeFile.mockClear();
  });

  afterEach(async () => {
    await network.stop();
  });

  // Helper: pair a remote agent and return useful references
  function pairRemoteAgent(
    ownerTrust: { overall: number } | null = { overall: 0.8 },
    ownerName = 'RemoteOwner',
  ) {
    const remoteKeys = generateTestKeys();
    const remoteIdentity = buildIdentity(remoteKeys, ownerName, `${ownerName}'s Agent`);
    const peer = network.acceptPairing(remoteIdentity, null, ownerTrust);
    const networkIdentity = network.getIdentity()!;
    const sharedSecret = deriveTestSharedSecret(
      remoteKeys.exchangePrivateKey,
      networkIdentity.exchangePublicKey,
    );
    return { remoteKeys, remoteIdentity, peer: peer!, sharedSecret, networkIdentity };
  }

  // ── Initialization ───────────────────────────────────────────────

  describe('Initialization', () => {
    it('generates identity on first run', () => {
      expect(network.getIdentity()).not.toBeNull();
    });

    it('identity has a valid 16-char hex agentId', () => {
      const id = network.getAgentId();
      expect(id).toMatch(/^[0-9a-f]{16}$/);
    });

    it('identity has Ed25519 signing public key (base64)', () => {
      const identity = network.getIdentity()!;
      expect(identity.signingPublicKey).toBeDefined();
      expect(Buffer.from(identity.signingPublicKey, 'base64').length).toBeGreaterThan(0);
    });

    it('identity has X25519 exchange public key (base64)', () => {
      const identity = network.getIdentity()!;
      expect(identity.exchangePublicKey).toBeDefined();
      expect(Buffer.from(identity.exchangePublicKey, 'base64').length).toBeGreaterThan(0);
    });

    it('loads existing state from disk when available', async () => {
      const savedState = {
        keyPair: null,
        identity: null,
        peers: [],
        delegations: [],
        config: { enabled: true, ownerName: 'Loaded', instanceName: 'From Disk', maxPeers: 50, maxDelegationHistory: 500, pairingCodeTtlMs: 300000, maxMessageSizeBytes: 1048576 },
        messagesSent: 42,
        messagesReceived: 7,
        messageLog: [],
      };
      mockFs.readFile.mockResolvedValue(JSON.stringify(savedState));

      const n2 = new AgentNetwork();
      await n2.initialize();
      // It loaded config from disk then generated identity (keyPair was null)
      expect(n2.getConfig().ownerName).toBe('Loaded');
      const stats = n2.getStats();
      expect(stats.messagesSent).toBe(42);
      expect(stats.messagesReceived).toBe(7);
      await n2.stop();
    });
  });

  // ── Identity ─────────────────────────────────────────────────────

  describe('Identity', () => {
    it('getIdentity returns identity after init', () => {
      const identity = network.getIdentity();
      expect(identity).not.toBeNull();
      expect(identity!.ownerName).toBe('User');
      expect(identity!.instanceName).toBe('Agent Friday');
    });

    it('getAgentId returns short hex ID', () => {
      const id = network.getAgentId();
      expect(id).not.toBeNull();
      expect(typeof id).toBe('string');
    });
  });

  // ── Pairing ──────────────────────────────────────────────────────

  describe('Pairing', () => {
    it('generatePairingOffer returns a pairing code', () => {
      const offer = network.generatePairingOffer();
      expect(offer).not.toBeNull();
      expect(offer!.code).toBeDefined();
      expect(offer!.identity).toEqual(network.getIdentity());
    });

    it('pairing code is 6 characters from the safe charset', () => {
      const offer = network.generatePairingOffer()!;
      expect(offer.code).toHaveLength(6);
      expect(offer.code).toMatch(/^[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{6}$/);
    });

    it('pairing code has expiry in the future', () => {
      const offer = network.generatePairingOffer()!;
      expect(offer.expiresAt).toBeGreaterThan(Date.now());
    });

    it('getActivePairingCode returns the active code', () => {
      const offer = network.generatePairingOffer()!;
      const active = network.getActivePairingCode();
      expect(active).not.toBeNull();
      expect(active!.code).toBe(offer.code);
    });

    it('getActivePairingCode returns null when none generated', () => {
      expect(network.getActivePairingCode()).toBeNull();
    });

    it('getActivePairingCode returns null when expired', () => {
      const offer = network.generatePairingOffer()!;
      // Manually expire it by mutating expiresAt (testing internal state)
      (offer as any).expiresAt = Date.now() - 1000;
      // The code object is the same reference stored internally,
      // but getActivePairingCode checks Date.now() vs expiresAt.
      // We need to advance time or directly set.
      // Since the internal activePairingCode is the returned object, mutation works.
      const active = network.getActivePairingCode();
      expect(active).toBeNull();
    });

    it('acceptPairing creates a paired agent with ECDH shared secret', () => {
      const { peer } = pairRemoteAgent({ overall: 0.8 });
      expect(peer).not.toBeNull();
      expect(peer.status).toBe('paired');
      expect(peer.sharedSecret).toBeDefined();
      expect(peer.sharedSecret.length).toBeGreaterThan(0);
    });

    it('acceptPairing derives trust from owner scores', () => {
      const { peer } = pairRemoteAgent({ overall: 0.75 });
      expect(peer.trustLevel).toBe(0.75);
    });

    it('acceptPairing with null trust gives 0.1 trust level', () => {
      const { peer } = pairRemoteAgent(null);
      expect(peer.trustLevel).toBe(0.1);
    });

    it('acceptPairing returns existing peer if already paired', () => {
      const remoteKeys = generateTestKeys();
      const remoteIdentity = buildIdentity(remoteKeys);
      const first = network.acceptPairing(remoteIdentity, null, { overall: 0.8 });
      const second = network.acceptPairing(remoteIdentity, null, { overall: 0.9 });
      expect(second).toBe(first); // Same reference
    });

    it('acceptPairing returns null at max capacity', async () => {
      // Set max peers to 1
      network.updateConfig({ maxPeers: 1 });
      pairRemoteAgent(); // Fill the one slot
      const keys2 = generateTestKeys();
      const id2 = buildIdentity(keys2, 'Owner2');
      const result = network.acceptPairing(id2, null, { overall: 0.5 });
      expect(result).toBeNull();
    });

    it('recordInboundPairingRequest creates pending-inbound peer', () => {
      const remoteKeys = generateTestKeys();
      const remoteIdentity = buildIdentity(remoteKeys);
      const pending = network.recordInboundPairingRequest(remoteIdentity);
      expect(pending.status).toBe('pending-inbound');
      expect(pending.trustLevel).toBe(0.1);
      expect(pending.sharedSecret).toBe('');
    });

    it('recordInboundPairingRequest returns existing if duplicate', () => {
      const remoteKeys = generateTestKeys();
      const remoteIdentity = buildIdentity(remoteKeys);
      const first = network.recordInboundPairingRequest(remoteIdentity);
      const second = network.recordInboundPairingRequest(remoteIdentity);
      expect(second).toBe(first);
    });
  });

  // ── Block / Unpair ───────────────────────────────────────────────

  describe('Block / Unpair', () => {
    it('blockAgent sets status to blocked and wipes shared secret', () => {
      const { remoteIdentity } = pairRemoteAgent();
      const result = network.blockAgent(remoteIdentity.agentId);
      expect(result).toBe(true);
      const peer = network.getPeer(remoteIdentity.agentId);
      expect(peer!.status).toBe('blocked');
      expect(peer!.sharedSecret).toBe('');
      expect(peer!.autoApproveTaskTypes).toEqual([]);
    });

    it('blockAgent returns false for unknown agent', () => {
      expect(network.blockAgent('nonexistent')).toBe(false);
    });

    it('unpairAgent removes the peer entirely', () => {
      const { remoteIdentity } = pairRemoteAgent();
      expect(network.unpairAgent(remoteIdentity.agentId)).toBe(true);
      expect(network.getPeer(remoteIdentity.agentId)).toBeNull();
    });

    it('unpairAgent also removes associated delegations', () => {
      const { remoteIdentity } = pairRemoteAgent();
      // Create a delegation
      network.createDelegation(remoteIdentity.agentId, 'test task');
      expect(network.getAllDelegations().length).toBe(1);
      // Unpair removes both peer and delegations
      network.unpairAgent(remoteIdentity.agentId);
      expect(network.getAllDelegations().length).toBe(0);
    });

    it('unpairAgent returns false for unknown agent', () => {
      expect(network.unpairAgent('nonexistent')).toBe(false);
    });
  });

  // ── Peers ────────────────────────────────────────────────────────

  describe('Peers', () => {
    it('getPeer returns peer by agent ID', () => {
      const { remoteIdentity } = pairRemoteAgent();
      const peer = network.getPeer(remoteIdentity.agentId);
      expect(peer).not.toBeNull();
      expect(peer!.identity.agentId).toBe(remoteIdentity.agentId);
    });

    it('getPeer returns null for unknown', () => {
      expect(network.getPeer('unknown')).toBeNull();
    });

    it('getAllPeers returns all peers', () => {
      pairRemoteAgent({ overall: 0.8 }, 'Alice');
      pairRemoteAgent({ overall: 0.7 }, 'Bob');
      expect(network.getAllPeers().length).toBe(2);
    });

    it('getPairedPeers filters to paired status only', () => {
      pairRemoteAgent({ overall: 0.8 }, 'Alice');
      const remoteKeys = generateTestKeys();
      network.recordInboundPairingRequest(buildIdentity(remoteKeys, 'Pending'));
      expect(network.getAllPeers().length).toBe(2);
      expect(network.getPairedPeers().length).toBe(1);
    });

    it('getPendingPairingRequests returns pending-inbound only', () => {
      pairRemoteAgent();
      const k1 = generateTestKeys();
      const k2 = generateTestKeys();
      network.recordInboundPairingRequest(buildIdentity(k1, 'P1'));
      network.recordInboundPairingRequest(buildIdentity(k2, 'P2'));
      expect(network.getPendingPairingRequests().length).toBe(2);
    });
  });

  // ── Trust ────────────────────────────────────────────────────────

  describe('Trust', () => {
    it('updatePeerTrust updates the trust level', () => {
      const { remoteIdentity } = pairRemoteAgent({ overall: 0.5 });
      network.updatePeerTrust(remoteIdentity.agentId, { overall: 0.9 });
      expect(network.getPeer(remoteIdentity.agentId)!.trustLevel).toBe(0.9);
    });

    it('updatePeerTrust updates ownerPersonId when provided', () => {
      const { remoteIdentity } = pairRemoteAgent();
      network.updatePeerTrust(remoteIdentity.agentId, { overall: 0.7 }, 'person-123');
      expect(network.getPeer(remoteIdentity.agentId)!.ownerPersonId).toBe('person-123');
    });

    it('updatePeerTrust returns false for unknown agent', () => {
      expect(network.updatePeerTrust('none', { overall: 0.5 })).toBe(false);
    });
  });

  // ── Capabilities ─────────────────────────────────────────────────

  describe('Capabilities', () => {
    it('setAutoApproveTaskTypes sets standing permissions', () => {
      const { remoteIdentity } = pairRemoteAgent();
      network.setAutoApproveTaskTypes(remoteIdentity.agentId, ['research', 'summarize']);
      expect(network.getPeer(remoteIdentity.agentId)!.autoApproveTaskTypes).toEqual(['research', 'summarize']);
    });

    it('setAutoApproveTaskTypes returns false for non-paired peer', () => {
      const k = generateTestKeys();
      network.recordInboundPairingRequest(buildIdentity(k));
      expect(network.setAutoApproveTaskTypes(k.agentId, ['x'])).toBe(false);
    });

    it('updatePeerCapabilities records advertised capabilities', () => {
      const { remoteIdentity } = pairRemoteAgent();
      network.updatePeerCapabilities(remoteIdentity.agentId, ['web-search', 'code-review']);
      expect(network.getPeer(remoteIdentity.agentId)!.advertisedCapabilities).toEqual(['web-search', 'code-review']);
    });

    it('findPeersWithCapability returns matching paired peers', () => {
      const { remoteIdentity: r1 } = pairRemoteAgent({ overall: 0.8 }, 'Alice');
      const { remoteIdentity: r2 } = pairRemoteAgent({ overall: 0.7 }, 'Bob');
      network.updatePeerCapabilities(r1.agentId, ['web-search']);
      network.updatePeerCapabilities(r2.agentId, ['code-review', 'web-search']);
      const found = network.findPeersWithCapability('web-search');
      expect(found.length).toBe(2);
    });

    it('findPeersWithCapability excludes non-paired peers', () => {
      const { remoteIdentity } = pairRemoteAgent();
      network.updatePeerCapabilities(remoteIdentity.agentId, ['web-search']);
      network.blockAgent(remoteIdentity.agentId);
      expect(network.findPeersWithCapability('web-search').length).toBe(0);
    });
  });

  // ── Messaging ────────────────────────────────────────────────────

  describe('Messaging', () => {
    it('createMessage returns a signed message', () => {
      const { remoteIdentity, networkIdentity } = pairRemoteAgent();
      const msg = network.createMessage(remoteIdentity.agentId, 'ping', { test: true });
      expect(msg).not.toBeNull();
      expect(msg!.fromAgentId).toBe(networkIdentity.agentId);
      expect(msg!.toAgentId).toBe(remoteIdentity.agentId);
      expect(msg!.signature).toBeDefined();
    });

    it('createMessage encrypts for paired peer with shared secret', () => {
      const { remoteIdentity } = pairRemoteAgent();
      const msg = network.createMessage(remoteIdentity.agentId, 'ping', { x: 1 });
      expect(msg!.encrypted).toBe(true);
      expect(msg!.nonce).toBeDefined();
      expect(msg!.authTag).toBeDefined();
    });

    it('createMessage returns null for blocked peer', () => {
      const { remoteIdentity } = pairRemoteAgent();
      network.blockAgent(remoteIdentity.agentId);
      expect(network.createMessage(remoteIdentity.agentId, 'ping', {})).toBeNull();
    });

    it('createMessage returns null for unknown peer', () => {
      expect(network.createMessage('nonexistent', 'ping', {})).toBeNull();
    });

    it('processInboundMessage accepts valid signed+encrypted message', () => {
      const { remoteKeys, remoteIdentity, sharedSecret, networkIdentity } = pairRemoteAgent();

      // Create a message from the remote agent to us
      let msg = createSignedMessage(
        remoteKeys.agentId,
        networkIdentity.agentId,
        'ping',
        { hello: 'from remote' },
        remoteKeys.signingPrivateKey,
      );
      msg = encryptMessage(msg, sharedSecret);

      const result = network.processInboundMessage(msg);
      expect(result).not.toBeNull();
      expect(result!.payload).toEqual({ hello: 'from remote' });
      expect(result!.encrypted).toBe(false); // Decrypted
    });

    it('processInboundMessage rejects messages from blocked peers', () => {
      const { remoteKeys, remoteIdentity, sharedSecret, networkIdentity } = pairRemoteAgent();
      network.blockAgent(remoteIdentity.agentId);

      const msg = createSignedMessage(
        remoteKeys.agentId, networkIdentity.agentId, 'ping', {},
        remoteKeys.signingPrivateKey,
      );
      expect(network.processInboundMessage(msg)).toBeNull();
    });

    it('processInboundMessage rejects unknown sender for non-pair-request', () => {
      const unknown = generateTestKeys();
      const msg = createSignedMessage(
        unknown.agentId, network.getAgentId()!, 'ping', {},
        unknown.signingPrivateKey,
      );
      expect(network.processInboundMessage(msg)).toBeNull();
    });

    it('processInboundMessage increments messagesReceived', () => {
      const { remoteKeys, sharedSecret, networkIdentity } = pairRemoteAgent();
      const before = network.getStats().messagesReceived;

      let msg = createSignedMessage(
        remoteKeys.agentId, networkIdentity.agentId, 'pong', {},
        remoteKeys.signingPrivateKey,
      );
      msg = encryptMessage(msg, sharedSecret);
      network.processInboundMessage(msg);

      expect(network.getStats().messagesReceived).toBe(before + 1);
    });
  });

  // ── Task Delegation Lifecycle ────────────────────────────────────

  describe('Task Delegation', () => {
    it('createDelegation creates a pending-approval delegation', () => {
      const { remoteIdentity } = pairRemoteAgent();
      const d = network.createDelegation(remoteIdentity.agentId, 'research topic');
      expect(d).not.toBeNull();
      expect(d!.status).toBe('pending-approval');
      expect(d!.description).toBe('research topic');
      expect(d!.targetAgentId).toBe(remoteIdentity.agentId);
    });

    it('createDelegation returns null for non-paired target', () => {
      expect(network.createDelegation('unknown', 'task')).toBeNull();
    });

    it('createDelegation records required capabilities and deadline', () => {
      const { remoteIdentity } = pairRemoteAgent();
      const d = network.createDelegation(remoteIdentity.agentId, 'task', ['web-search'], 9999);
      expect(d!.requiredCapabilities).toEqual(['web-search']);
      expect(d!.deadline).toBe(9999);
    });

    it('handleInboundDelegation creates a delegation record', () => {
      const { remoteIdentity } = pairRemoteAgent();
      const d = network.handleInboundDelegation(
        remoteIdentity.agentId, 'del-123', 'summarize article', [], 0,
      );
      expect(d).not.toBeNull();
      expect(d!.id).toBe('del-123');
      expect(d!.status).toBe('pending-approval');
    });

    it('handleInboundDelegation auto-approves with standing permissions', () => {
      const { remoteIdentity } = pairRemoteAgent({ overall: 0.8 });
      network.setAutoApproveTaskTypes(remoteIdentity.agentId, ['research']);
      const d = network.handleInboundDelegation(
        remoteIdentity.agentId, 'del-456', 'research quantum computing', [], 0,
      );
      expect(d!.status).toBe('approved');
      expect(d!.autoApproved).toBe(true);
    });

    it('handleInboundDelegation does NOT auto-approve with sensitive capabilities', () => {
      const { remoteIdentity } = pairRemoteAgent({ overall: 0.8 });
      network.setAutoApproveTaskTypes(remoteIdentity.agentId, ['research']);
      const d = network.handleInboundDelegation(
        remoteIdentity.agentId, 'del-789', 'research with file access', ['file-write'], 0,
      );
      expect(d!.status).toBe('pending-approval');
      expect(d!.autoApproved).toBe(false);
    });

    it('approveDelegation transitions pending → approved', () => {
      const { remoteIdentity } = pairRemoteAgent();
      const d = network.createDelegation(remoteIdentity.agentId, 'task')!;
      expect(network.approveDelegation(d.id)).toBe(true);
      expect(network.getDelegation(d.id)!.status).toBe('approved');
    });

    it('approveDelegation returns false for non-pending', () => {
      const { remoteIdentity } = pairRemoteAgent();
      const d = network.createDelegation(remoteIdentity.agentId, 'task')!;
      network.approveDelegation(d.id);
      // Already approved — can't approve again
      expect(network.approveDelegation(d.id)).toBe(false);
    });

    it('rejectDelegation transitions pending → rejected', () => {
      const { remoteIdentity } = pairRemoteAgent();
      const d = network.createDelegation(remoteIdentity.agentId, 'task')!;
      expect(network.rejectDelegation(d.id)).toBe(true);
      expect(network.getDelegation(d.id)!.status).toBe('rejected');
    });

    it('startDelegation transitions approved → in-progress', () => {
      const { remoteIdentity } = pairRemoteAgent();
      const d = network.createDelegation(remoteIdentity.agentId, 'task')!;
      network.approveDelegation(d.id);
      expect(network.startDelegation(d.id)).toBe(true);
      expect(network.getDelegation(d.id)!.status).toBe('in-progress');
    });

    it('startDelegation returns false for non-approved', () => {
      const { remoteIdentity } = pairRemoteAgent();
      const d = network.createDelegation(remoteIdentity.agentId, 'task')!;
      // Still pending
      expect(network.startDelegation(d.id)).toBe(false);
    });

    it('completeDelegation transitions to completed with result', () => {
      const { remoteIdentity } = pairRemoteAgent();
      const d = network.createDelegation(remoteIdentity.agentId, 'task')!;
      network.approveDelegation(d.id);
      network.startDelegation(d.id);
      expect(network.completeDelegation(d.id, { summary: 'done' })).toBe(true);
      const completed = network.getDelegation(d.id)!;
      expect(completed.status).toBe('completed');
      expect(completed.result).toEqual({ summary: 'done' });
    });

    it('failDelegation transitions to failed with error', () => {
      const { remoteIdentity } = pairRemoteAgent();
      const d = network.createDelegation(remoteIdentity.agentId, 'task')!;
      network.approveDelegation(d.id);
      expect(network.failDelegation(d.id, 'timeout')).toBe(true);
      const failed = network.getDelegation(d.id)!;
      expect(failed.status).toBe('failed');
      expect(failed.error).toBe('timeout');
    });

    it('cancelDelegation cancels a non-terminal delegation', () => {
      const { remoteIdentity } = pairRemoteAgent();
      const d = network.createDelegation(remoteIdentity.agentId, 'task')!;
      expect(network.cancelDelegation(d.id)).toBe(true);
      expect(network.getDelegation(d.id)!.status).toBe('cancelled');
    });

    it('cancelDelegation returns false for already completed', () => {
      const { remoteIdentity } = pairRemoteAgent();
      const d = network.createDelegation(remoteIdentity.agentId, 'task')!;
      network.approveDelegation(d.id);
      network.completeDelegation(d.id, 'done');
      expect(network.cancelDelegation(d.id)).toBe(false);
    });

    it('getDelegation returns delegation by ID', () => {
      const { remoteIdentity } = pairRemoteAgent();
      const d = network.createDelegation(remoteIdentity.agentId, 'task')!;
      expect(network.getDelegation(d.id)).not.toBeNull();
      expect(network.getDelegation(d.id)!.id).toBe(d.id);
    });

    it('getDelegation returns null for unknown ID', () => {
      expect(network.getDelegation('nonexistent')).toBeNull();
    });

    it('getDelegationsForAgent filters by agent', () => {
      const { remoteIdentity: r1 } = pairRemoteAgent({ overall: 0.8 }, 'Alice');
      const { remoteIdentity: r2 } = pairRemoteAgent({ overall: 0.7 }, 'Bob');
      network.createDelegation(r1.agentId, 'task for alice');
      network.createDelegation(r2.agentId, 'task for bob');
      network.createDelegation(r1.agentId, 'another for alice');
      expect(network.getDelegationsForAgent(r1.agentId).length).toBe(2);
      expect(network.getDelegationsForAgent(r2.agentId).length).toBe(1);
    });

    it('getPendingInboundDelegations returns pending targeted at self', () => {
      const { remoteIdentity } = pairRemoteAgent();
      // Create inbound delegation targeted at us
      network.handleInboundDelegation(remoteIdentity.agentId, 'del-a', 'task a', [], 0);
      network.handleInboundDelegation(remoteIdentity.agentId, 'del-b', 'task b', [], 0);
      // Approve one
      network.approveDelegation('del-a');
      // Only del-b should be pending
      expect(network.getPendingInboundDelegations().length).toBe(1);
      expect(network.getPendingInboundDelegations()[0].id).toBe('del-b');
    });
  });

  // ── Stats ────────────────────────────────────────────────────────

  describe('Stats', () => {
    it('returns correct counts for all categories', () => {
      const { remoteIdentity: r1 } = pairRemoteAgent({ overall: 0.8 }, 'Alice');
      pairRemoteAgent({ overall: 0.7 }, 'Bob');
      const k3 = generateTestKeys();
      network.recordInboundPairingRequest(buildIdentity(k3, 'Pending'));
      const k4 = generateTestKeys();
      const blocked = buildIdentity(k4, 'Blocked');
      network.recordInboundPairingRequest(blocked);
      network.acceptPairing(blocked, null, { overall: 0.3 });
      network.blockAgent(blocked.agentId);

      // Create some delegations
      const d1 = network.createDelegation(r1.agentId, 'task 1')!;
      network.approveDelegation(d1.id);
      network.completeDelegation(d1.id, 'done');
      const d2 = network.createDelegation(r1.agentId, 'task 2')!;
      network.approveDelegation(d2.id);
      network.failDelegation(d2.id, 'error');

      const stats = network.getStats();
      expect(stats.agentId).toBe(network.getAgentId());
      expect(stats.pairedCount).toBe(2); // Alice + Bob (blocked doesn't count)
      expect(stats.blockedCount).toBe(1);
      expect(stats.pendingCount).toBe(1); // k3
      expect(stats.totalDelegations).toBe(2);
      expect(stats.successfulDelegations).toBe(1);
      expect(stats.failedDelegations).toBe(1);
    });
  });

  // ── Config ───────────────────────────────────────────────────────

  describe('Config', () => {
    it('getConfig returns current config with defaults', () => {
      const config = network.getConfig();
      expect(config.enabled).toBe(true);
      expect(config.maxPeers).toBe(50);
      expect(config.pairingCodeTtlMs).toBe(5 * 60 * 1000);
    });

    it('updateConfig merges partial config', () => {
      network.updateConfig({ maxPeers: 10, ownerName: 'New Owner' });
      const config = network.getConfig();
      expect(config.maxPeers).toBe(10);
      expect(config.ownerName).toBe('New Owner');
      expect(config.enabled).toBe(true); // unchanged
    });

    it('updateConfig updates identity names when changed', () => {
      network.updateConfig({ ownerName: 'Alice', instanceName: 'Alice Agent' });
      const identity = network.getIdentity()!;
      expect(identity.ownerName).toBe('Alice');
      expect(identity.instanceName).toBe('Alice Agent');
    });
  });

  // ── Prompt Context ───────────────────────────────────────────────

  describe('Prompt Context', () => {
    it('returns empty string when network is disabled', () => {
      network.updateConfig({ enabled: false });
      expect(network.getPromptContext()).toBe('');
    });

    it('returns empty string with no peers', () => {
      expect(network.getPromptContext()).toBe('');
    });

    it('includes network ID and peer info when peers exist', () => {
      pairRemoteAgent({ overall: 0.8 }, 'Alice');
      const ctx = network.getPromptContext();
      expect(ctx).toContain('[Agent Network]');
      expect(ctx).toContain(network.getAgentId()!);
      expect(ctx).toContain('Alice');
      expect(ctx).toContain('Connected peers: 1');
    });

    it('includes pending delegation warning', () => {
      const { remoteIdentity } = pairRemoteAgent();
      network.handleInboundDelegation(remoteIdentity.agentId, 'del-1', 'task', [], 0);
      const ctx = network.getPromptContext();
      expect(ctx).toContain('pending delegation');
    });
  });

  // ── Message Log ──────────────────────────────────────────────────

  describe('Message Log', () => {
    it('getMessageLog returns recent entries', () => {
      const { remoteIdentity } = pairRemoteAgent();
      network.createMessage(remoteIdentity.agentId, 'ping', {});
      network.createMessage(remoteIdentity.agentId, 'pong', {});
      const log = network.getMessageLog(10);
      // At least 3: pair-accept log + 2 messages
      expect(log.length).toBeGreaterThanOrEqual(3);
    });

    it('getMessageLog respects limit parameter', () => {
      const { remoteIdentity } = pairRemoteAgent();
      for (let i = 0; i < 10; i++) {
        network.createMessage(remoteIdentity.agentId, 'ping', { i });
      }
      const log = network.getMessageLog(3);
      expect(log.length).toBe(3);
    });
  });

  // ── Delegation Pruning ───────────────────────────────────────────

  describe('Delegation Pruning', () => {
    it('prunes oldest terminal delegations when over max', () => {
      network.updateConfig({ maxDelegationHistory: 5 });
      const { remoteIdentity } = pairRemoteAgent();

      // Create 8 delegations, complete 6
      for (let i = 0; i < 8; i++) {
        const d = network.createDelegation(remoteIdentity.agentId, `task ${i}`)!;
        if (i < 6) {
          network.approveDelegation(d.id);
          network.completeDelegation(d.id, `result ${i}`);
        }
      }

      // Should have pruned to maxDelegationHistory
      const all = network.getAllDelegations();
      expect(all.length).toBeLessThanOrEqual(5);
    });

    it('keeps active delegations during pruning', () => {
      network.updateConfig({ maxDelegationHistory: 3 });
      const { remoteIdentity } = pairRemoteAgent();

      // Create 5 delegations
      const active: string[] = [];
      for (let i = 0; i < 5; i++) {
        const d = network.createDelegation(remoteIdentity.agentId, `task ${i}`)!;
        if (i < 3) {
          network.approveDelegation(d.id);
          network.completeDelegation(d.id, `done`);
        } else {
          active.push(d.id); // These are still pending
        }
      }

      // Active (pending) delegations must survive
      const all = network.getAllDelegations();
      for (const id of active) {
        expect(all.some((d) => d.id === id)).toBe(true);
      }
    });
  });

  // ── Persistence ──────────────────────────────────────────────────

  describe('Persistence', () => {
    it('stop flushes pending save to disk', async () => {
      pairRemoteAgent(); // Triggers queueSave
      await network.stop();
      expect(mockFs.writeFile).toHaveBeenCalled();
      const callArgs = mockFs.writeFile.mock.calls[0];
      expect(callArgs[0]).toContain('agent-network.json');
      const saved = JSON.parse(callArgs[1] as string);
      expect(saved.peers.length).toBe(1);
    });

    it('persisted state includes identity and peers', async () => {
      const { remoteIdentity } = pairRemoteAgent();
      await network.stop();
      const callArgs = mockFs.writeFile.mock.calls[0];
      const saved = JSON.parse(callArgs[1] as string);
      expect(saved.identity).not.toBeNull();
      expect(saved.identity.agentId).toBe(network.getAgentId());
      expect(saved.peers[0].identity.agentId).toBe(remoteIdentity.agentId);
    });

    it('persisted state includes delegations and message counts', async () => {
      const { remoteIdentity } = pairRemoteAgent();
      network.createMessage(remoteIdentity.agentId, 'ping', {});
      network.createDelegation(remoteIdentity.agentId, 'task');
      await network.stop();
      const callArgs = mockFs.writeFile.mock.calls[0];
      const saved = JSON.parse(callArgs[1] as string);
      expect(saved.messagesSent).toBeGreaterThan(0);
      expect(saved.delegations.length).toBe(1);
    });
  });

  // ── ECDH Shared Secret Symmetry ──────────────────────────────────

  describe('ECDH Shared Secret', () => {
    it('both sides derive the same shared secret', () => {
      const networkIdentity = network.getIdentity()!;
      const remoteKeys = generateTestKeys();
      const remoteIdentity = buildIdentity(remoteKeys);

      // When network accepts pairing, it derives: deriveSharedSecret(ourPriv, theirPub)
      const peer = network.acceptPairing(remoteIdentity, null, { overall: 0.8 })!;

      // Remote side would derive: deriveSharedSecret(remotePriv, networkPub)
      const remoteShared = deriveTestSharedSecret(
        remoteKeys.exchangePrivateKey,
        networkIdentity.exchangePublicKey,
      );

      expect(peer.sharedSecret).toBe(remoteShared);
    });
  });

  // ── End-to-End Messaging ─────────────────────────────────────────

  describe('End-to-End Messaging', () => {
    it('full cycle: send encrypted → receive → decrypt → verify', () => {
      const { remoteKeys, sharedSecret, networkIdentity, remoteIdentity } = pairRemoteAgent();

      // Remote creates and encrypts a message to us
      let inbound = createSignedMessage(
        remoteKeys.agentId,
        networkIdentity.agentId,
        'task-response',
        { result: 'quantum computing summary', confidence: 0.95 },
        remoteKeys.signingPrivateKey,
      );
      inbound = encryptMessage(inbound, sharedSecret);

      // We process it
      const processed = network.processInboundMessage(inbound);
      expect(processed).not.toBeNull();
      expect(processed!.payload).toEqual({ result: 'quantum computing summary', confidence: 0.95 });
      expect(processed!.encrypted).toBe(false);

      // We send a reply
      const reply = network.createMessage(remoteIdentity.agentId, 'pong', { ack: true });
      expect(reply).not.toBeNull();
      expect(reply!.encrypted).toBe(true);

      // Remote decrypts our reply
      const decrypted = decryptMessage(reply!, sharedSecret);
      expect(decrypted.payload).toEqual({ ack: true });
    });

    it('tampered encrypted message is rejected', () => {
      const { remoteKeys, sharedSecret, networkIdentity } = pairRemoteAgent();

      let msg = createSignedMessage(
        remoteKeys.agentId, networkIdentity.agentId, 'ping', { x: 1 },
        remoteKeys.signingPrivateKey,
      );
      msg = encryptMessage(msg, sharedSecret);

      // Tamper with the encrypted data
      (msg.payload as any)._encrypted = 'tampered-base64-data';

      const result = network.processInboundMessage(msg);
      expect(result).toBeNull(); // Decryption or verification failed
    });
  });
});
