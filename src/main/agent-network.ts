/**
 * Track VII, Phase 2: Agent Network Protocol
 *
 * Enables peer-to-peer communication between Agent Friday instances.
 * Each instance has a sovereign cryptographic identity (Ed25519 + X25519):
 *   - Ed25519: sign/verify messages (prove identity)
 *   - X25519: key agreement → AES-256-GCM (end-to-end encryption)
 *
 * Trust is NON-TRANSITIVE. An agent's trustworthiness derives from its
 * owner's Trust Graph credibility scores — not from the agent itself.
 * Sarah trusts Tom ≠ you trust Tom's agent.
 *
 * cLaw Gate: Agent communication NEVER bypasses user consent.
 * All messages are logged. Delegation requires explicit approval or
 * pre-configured standing permissions. Agents facilitate human
 * communication, not create autonomous channels humans don't control.
 */

import crypto from 'crypto';
import { app } from 'electron';
import path from 'path';
import fs from 'fs/promises';

// Late-bound vault import — avoids circular deps and allows test mocking
interface VaultIO { vaultRead: (p: string) => Promise<string>; vaultWrite: (p: string, c: string) => Promise<void> }
let _vault: VaultIO | null = null;
function getVault(): VaultIO {
  if (!_vault) {
    try {
      _vault = require('./vault');
    } catch {
      // Vault not available — use raw fs fallback
      _vault = {
        vaultRead: async (p: string) => (await fs.readFile(p, 'utf-8')),
        vaultWrite: async (p: string, c: string) => { await fs.writeFile(p, c, 'utf-8'); },
      };
    }
  }
  return _vault!;
}

// ── Types ─────────────────────────────────────────────────────────────

export interface AgentIdentity {
  /** Short hex ID derived from public key (first 8 bytes) */
  agentId: string;
  /** Ed25519 public key for signing/verification (base64) */
  signingPublicKey: string;
  /** X25519 public key for key agreement/encryption (base64) */
  exchangePublicKey: string;
  /** Human-readable owner name */
  ownerName: string;
  /** Human-readable instance name (e.g. "Sarah's Agent Friday") */
  instanceName: string;
  /** When this identity was created */
  createdAt: number;
}

export interface AgentKeyPair {
  /** Ed25519 private key (base64, never leaves device) */
  signingPrivateKey: string;
  /** Ed25519 public key (base64) */
  signingPublicKey: string;
  /** X25519 private key (base64, never leaves device) */
  exchangePrivateKey: string;
  /** X25519 public key (base64) */
  exchangePublicKey: string;
}

export interface PairedAgent {
  /** The remote agent's public identity */
  identity: AgentIdentity;
  /** When pairing was established */
  pairingTimestamp: number;
  /** Trust Graph person ID for the remote owner (null if unknown) */
  ownerPersonId: string | null;
  /** Derived trust level from owner's Trust Graph scores (0-1) */
  trustLevel: number;
  /** ECDH shared secret for AES-256-GCM encryption (hex) */
  sharedSecret: string;
  /** Last known activity timestamp */
  lastSeen: number;
  /** Pairing lifecycle state */
  status: PeerStatus;
  /** Standing permissions: auto-approve these task types */
  autoApproveTaskTypes: string[];
  /** Capabilities this agent has advertised */
  advertisedCapabilities: string[];
}

export type PeerStatus = 'pending-inbound' | 'pending-outbound' | 'paired' | 'blocked';

export type AgentMessageType =
  | 'pair-request'            // Initiate pairing
  | 'pair-accept'             // Accept pairing
  | 'pair-reject'             // Reject pairing
  | 'capability-advertise'    // Share installed capabilities
  | 'task-request'            // Delegate a task
  | 'task-response'           // Return task results
  | 'task-status-update'      // Intermediate status
  | 'ping'                    // Keepalive / presence check
  | 'pong'                    // Keepalive response
  | 'file-transfer-request'   // Request to send a file
  | 'file-transfer-response'  // Accept/reject file transfer
  | 'file-transfer-chunk';    // File data chunk

export interface AgentMessage {
  /** Unique message ID */
  id: string;
  /** Sender's agent ID */
  fromAgentId: string;
  /** Recipient's agent ID */
  toAgentId: string;
  /** When the message was created */
  timestamp: number;
  /** Message type */
  type: AgentMessageType;
  /** Message payload (encrypted if channel supports it) */
  payload: Record<string, unknown>;
  /** Ed25519 signature over the canonical message (hex) */
  signature: string;
  /** Whether the payload was encrypted */
  encrypted: boolean;
  /** Nonce for AES-GCM (base64, present only if encrypted) */
  nonce?: string;
  /** Auth tag for AES-GCM (base64, present only if encrypted) */
  authTag?: string;
  /** cLaw attestation — proves this agent operates under valid Fundamental Laws */
  clawAttestation?: import('./claw-attestation').ClawAttestation;
}

export type DelegationStatus =
  | 'pending-approval'    // Waiting for owner to approve
  | 'approved'            // Owner approved, executing
  | 'rejected'            // Owner rejected
  | 'in-progress'         // Actively executing
  | 'completed'           // Finished successfully
  | 'failed'              // Finished with error
  | 'cancelled';          // Cancelled by requester

export interface TaskDelegation {
  /** Unique delegation ID */
  id: string;
  /** Which agent requested this task */
  requestingAgentId: string;
  /** Which agent is being asked to do it */
  targetAgentId: string;
  /** Human-readable task description */
  description: string;
  /** Capabilities required to fulfill this task */
  requiredCapabilities: string[];
  /** Optional deadline (0 = no deadline) */
  deadline: number;
  /** Current status */
  status: DelegationStatus;
  /** Task result (null until completed) */
  result: unknown | null;
  /** Error message if failed */
  error: string | null;
  /** Whether this was auto-approved via standing permissions */
  autoApproved: boolean;
  /** Creation time */
  createdAt: number;
  /** Last update time */
  updatedAt: number;
}

export interface PairingCode {
  /** 6-character alphanumeric code for out-of-band pairing */
  code: string;
  /** Our identity (shared with the pairing partner) */
  identity: AgentIdentity;
  /** When this code expires */
  expiresAt: number;
}

export interface AgentNetworkConfig {
  /** Whether the agent network is enabled */
  enabled: boolean;
  /** Human-readable owner name (used in identity) */
  ownerName: string;
  /** Human-readable instance name */
  instanceName: string;
  /** Maximum paired agents */
  maxPeers: number;
  /** Maximum delegation history entries */
  maxDelegationHistory: number;
  /** Pairing code validity in ms (default: 5 minutes) */
  pairingCodeTtlMs: number;
  /** Maximum inbound message size in bytes */
  maxMessageSizeBytes: number;
}

export interface AgentNetworkStats {
  /** Our agent ID */
  agentId: string | null;
  /** Number of paired agents */
  pairedCount: number;
  /** Number of pending pairing requests */
  pendingCount: number;
  /** Number of blocked agents */
  blockedCount: number;
  /** Total messages sent */
  messagesSent: number;
  /** Total messages received */
  messagesReceived: number;
  /** Total delegations (all statuses) */
  totalDelegations: number;
  /** Delegations completed successfully */
  successfulDelegations: number;
  /** Delegations that failed */
  failedDelegations: number;
}

// ── Constants ─────────────────────────────────────────────────────────

const SAVE_DEBOUNCE_MS = 2000;
const DEFAULT_CONFIG: AgentNetworkConfig = {
  enabled: true,
  ownerName: 'User',
  instanceName: 'Agent Friday',
  maxPeers: 50,
  maxDelegationHistory: 500,
  pairingCodeTtlMs: 5 * 60 * 1000, // 5 minutes
  maxMessageSizeBytes: 1024 * 1024,  // 1MB
};
const MAX_MESSAGE_LOG = 200;
const PAIRING_CODE_LENGTH = 6;
const PAIRING_CODE_CHARS = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'; // No 0/O/1/I confusion

// ── Crypto Helpers ────────────────────────────────────────────────────

/** Generate an Ed25519 key pair for message signing. */
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

/** Generate an X25519 key pair for ECDH key agreement. */
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

/** Derive a shared secret via ECDH (X25519). */
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
  const secret = crypto.diffieHellman({ privateKey, publicKey });
  return secret.toString('hex');
}

/** Sign data with Ed25519 private key. */
function ed25519Sign(data: string, privateKeyB64: string): string {
  const key = crypto.createPrivateKey({
    key: Buffer.from(privateKeyB64, 'base64'),
    format: 'der',
    type: 'pkcs8',
  });
  const signature = crypto.sign(null, Buffer.from(data, 'utf8'), key);
  return signature.toString('hex');
}

/** Verify data with Ed25519 public key. */
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

/** Encrypt payload with AES-256-GCM using shared secret. */
function encryptPayload(payload: string, sharedSecretHex: string): { encrypted: string; nonce: string; authTag: string } {
  const key = Buffer.from(sharedSecretHex, 'hex').subarray(0, 32); // Take first 32 bytes
  const nonce = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv('aes-256-gcm', key, nonce);
  const encrypted = Buffer.concat([cipher.update(payload, 'utf8'), cipher.final()]);
  const authTag = cipher.getAuthTag();
  return {
    encrypted: encrypted.toString('base64'),
    nonce: nonce.toString('base64'),
    authTag: authTag.toString('base64'),
  };
}

/** Decrypt payload with AES-256-GCM using shared secret. */
function decryptPayload(encryptedB64: string, nonceB64: string, authTagB64: string, sharedSecretHex: string): string {
  const key = Buffer.from(sharedSecretHex, 'hex').subarray(0, 32);
  const decipher = crypto.createDecipheriv('aes-256-gcm', key, Buffer.from(nonceB64, 'base64'));
  decipher.setAuthTag(Buffer.from(authTagB64, 'base64'));
  const decrypted = Buffer.concat([
    decipher.update(Buffer.from(encryptedB64, 'base64')),
    decipher.final(),
  ]);
  return decrypted.toString('utf8');
}

/** Derive a short agent ID from the signing public key. */
function deriveAgentId(signingPublicKeyB64: string): string {
  const hash = crypto.createHash('sha256').update(Buffer.from(signingPublicKeyB64, 'base64')).digest();
  return hash.subarray(0, 8).toString('hex');
}

/** Generate a random pairing code. */
function generatePairingCode(): string {
  const bytes = crypto.randomBytes(PAIRING_CODE_LENGTH);
  let code = '';
  for (let i = 0; i < PAIRING_CODE_LENGTH; i++) {
    code += PAIRING_CODE_CHARS[bytes[i] % PAIRING_CODE_CHARS.length];
  }
  return code;
}

/** Canonical JSON for signing (sorted keys, deterministic). */
function canonicalize(obj: Record<string, unknown>): string {
  return JSON.stringify(obj, Object.keys(obj).sort());
}

// ── Persistence State ─────────────────────────────────────────────────

interface AgentNetworkState {
  keyPair: AgentKeyPair | null;
  identity: AgentIdentity | null;
  peers: PairedAgent[];
  delegations: TaskDelegation[];
  config: AgentNetworkConfig;
  messagesSent: number;
  messagesReceived: number;
  messageLog: Array<{ id: string; type: AgentMessageType; from: string; to: string; timestamp: number; direction: 'inbound' | 'outbound' }>;
}

// ═══════════════════════════════════════════════════════════════════════
// PURE FUNCTIONS (exported for testing)
// ═══════════════════════════════════════════════════════════════════════

/**
 * Create a signed message from components.
 * The signature covers: fromAgentId + toAgentId + timestamp + type + canonical payload.
 */
export function createSignedMessage(
  fromAgentId: string,
  toAgentId: string,
  type: AgentMessageType,
  payload: Record<string, unknown>,
  privateKey: string,
): AgentMessage {
  const id = crypto.randomUUID();
  const timestamp = Date.now();

  const signable = `${fromAgentId}|${toAgentId}|${timestamp}|${type}|${canonicalize(payload)}`;
  const signature = ed25519Sign(signable, privateKey);

  return {
    id,
    fromAgentId,
    toAgentId,
    timestamp,
    type,
    payload,
    signature,
    encrypted: false,
  };
}

/**
 * Verify a message's Ed25519 signature against the sender's public key.
 */
export function verifyMessageSignature(message: AgentMessage, senderPublicKey: string): boolean {
  const signable = `${message.fromAgentId}|${message.toAgentId}|${message.timestamp}|${message.type}|${canonicalize(message.payload)}`;
  return ed25519Verify(signable, message.signature, senderPublicKey);
}

/**
 * Encrypt a message's payload using the shared secret with the recipient.
 * Returns a new message with encrypted payload.
 */
export function encryptMessage(message: AgentMessage, sharedSecretHex: string): AgentMessage {
  const payloadStr = JSON.stringify(message.payload);
  const { encrypted, nonce, authTag } = encryptPayload(payloadStr, sharedSecretHex);
  return {
    ...message,
    payload: { _encrypted: encrypted },
    encrypted: true,
    nonce,
    authTag,
  };
}

/**
 * Decrypt a message's payload using the shared secret.
 * Returns a new message with decrypted payload.
 */
export function decryptMessage(message: AgentMessage, sharedSecretHex: string): AgentMessage {
  if (!message.encrypted || !message.nonce || !message.authTag) return message;
  const encryptedData = (message.payload as Record<string, string>)._encrypted;
  if (!encryptedData) return message;

  const decrypted = decryptPayload(encryptedData, message.nonce, message.authTag, sharedSecretHex);
  return {
    ...message,
    payload: JSON.parse(decrypted),
    encrypted: false,
    nonce: undefined,
    authTag: undefined,
  };
}

/**
 * Derive an agent's trust level from its owner's Trust Graph scores.
 * Trust is non-transitive: agent trust = owner's overall trust score.
 * Unknown owners get a trust floor of 0.1 (minimal, requires approval).
 */
export function deriveAgentTrust(
  ownerTrustScores: { overall: number } | null,
): number {
  if (!ownerTrustScores) return 0.1; // Unknown owner → minimal trust
  return Math.max(0.1, Math.min(1.0, ownerTrustScores.overall));
}

/**
 * Determine whether a task delegation can be auto-approved based on
 * standing permissions and trust level.
 *
 * Requirements for auto-approval:
 * 1. Trust level >= 0.6 (at least moderate trust)
 * 2. Task type is in the agent's autoApproveTaskTypes list
 * 3. No sensitive capabilities required (vision, audio, system-access)
 */
export function canAutoApprove(
  peer: PairedAgent,
  delegation: Pick<TaskDelegation, 'description' | 'requiredCapabilities'>,
): boolean {
  if (peer.trustLevel < 0.6) return false;
  if (peer.autoApproveTaskTypes.length === 0) return false;

  // Check if the task type matches standing permissions
  const taskLower = delegation.description.toLowerCase();
  const hasStandingPermission = peer.autoApproveTaskTypes.some(
    (type) => taskLower.includes(type.toLowerCase()),
  );
  if (!hasStandingPermission) return false;

  // Sensitive capabilities always require manual approval
  const sensitiveCapabilities = ['system-access', 'file-write', 'network-admin', 'credential-access'];
  const requiresSensitive = delegation.requiredCapabilities.some(
    (cap) => sensitiveCapabilities.includes(cap),
  );
  if (requiresSensitive) return false;

  return true;
}

// ═══════════════════════════════════════════════════════════════════════
// AGENT NETWORK ENGINE (Singleton)
// ═══════════════════════════════════════════════════════════════════════

export class AgentNetwork {
  private state: AgentNetworkState;
  private dataDir = '';
  private saveTimer: ReturnType<typeof setTimeout> | null = null;
  private activePairingCode: PairingCode | null = null;

  constructor() {
    this.state = {
      keyPair: null,
      identity: null,
      peers: [],
      delegations: [],
      config: { ...DEFAULT_CONFIG },
      messagesSent: 0,
      messagesReceived: 0,
      messageLog: [],
    };
  }

  // ── Initialization ────────────────────────────────────────────────

  async initialize(): Promise<void> {
    this.dataDir = path.join(app.getPath('userData'), 'friday-data');
    await fs.mkdir(this.dataDir, { recursive: true });
    await this.load();

    // Generate identity on first run
    if (!this.state.keyPair) {
      await this.generateIdentity();
      await this.save(); // Flush immediately — vault init depends on these keys
    }

    const peerCount = this.state.peers.filter((p) => p.status === 'paired').length;
    const delCount = this.state.delegations.length;
    console.log(
      `[AgentNetwork] Initialized — ID: ${this.state.identity?.agentId ?? 'none'}, ` +
      `${peerCount} peers, ${delCount} delegations`,
    );
  }

  /** Generate a new cryptographic identity. */
  private async generateIdentity(): Promise<void> {
    const signing = generateSigningKeyPair();
    const exchange = generateExchangeKeyPair();

    const keyPair: AgentKeyPair = {
      signingPrivateKey: signing.privateKey,
      signingPublicKey: signing.publicKey,
      exchangePrivateKey: exchange.privateKey,
      exchangePublicKey: exchange.publicKey,
    };

    const agentId = deriveAgentId(signing.publicKey);

    const identity: AgentIdentity = {
      agentId,
      signingPublicKey: signing.publicKey,
      exchangePublicKey: exchange.publicKey,
      ownerName: this.state.config.ownerName,
      instanceName: this.state.config.instanceName,
      createdAt: Date.now(),
    };

    this.state.keyPair = keyPair;
    this.state.identity = identity;
    this.queueSave();
  }

  // ── Identity ──────────────────────────────────────────────────────

  /** Get this agent's public identity. */
  getIdentity(): AgentIdentity | null {
    return this.state.identity;
  }

  /** Get agent ID (short hex). */
  getAgentId(): string | null {
    return this.state.identity?.agentId ?? null;
  }

  /** Get the Ed25519 signing private key (base64). Used by Sovereign Vault for key derivation. */
  getSigningPrivateKey(): string | null {
    return this.state.keyPair?.signingPrivateKey ?? null;
  }

  // ── Pairing ───────────────────────────────────────────────────────

  /** Generate a pairing code for out-of-band exchange. */
  generatePairingOffer(): PairingCode | null {
    if (!this.state.identity) return null;

    const code = generatePairingCode();
    this.activePairingCode = {
      code,
      identity: this.state.identity,
      expiresAt: Date.now() + this.state.config.pairingCodeTtlMs,
    };
    return this.activePairingCode;
  }

  /** Get the active pairing code (null if none or expired). */
  getActivePairingCode(): PairingCode | null {
    if (!this.activePairingCode) return null;
    if (Date.now() > this.activePairingCode.expiresAt) {
      this.activePairingCode = null;
      return null;
    }
    return this.activePairingCode;
  }

  /**
   * Accept a pairing request from a remote agent.
   * This is called when the user approves an inbound pairing request.
   */
  acceptPairing(remoteIdentity: AgentIdentity, ownerPersonId: string | null, ownerTrust: { overall: number } | null): PairedAgent | null {
    if (!this.state.keyPair || !this.state.identity) return null;

    // Check if already paired
    const existing = this.state.peers.find((p) => p.identity.agentId === remoteIdentity.agentId);
    if (existing && existing.status === 'paired') return existing;

    // Derive shared secret via ECDH
    const sharedSecret = deriveSharedSecret(
      this.state.keyPair.exchangePrivateKey,
      remoteIdentity.exchangePublicKey,
    );

    const trustLevel = deriveAgentTrust(ownerTrust);

    const peer: PairedAgent = {
      identity: remoteIdentity,
      pairingTimestamp: Date.now(),
      ownerPersonId,
      trustLevel,
      sharedSecret,
      lastSeen: Date.now(),
      status: 'paired',
      autoApproveTaskTypes: [],
      advertisedCapabilities: [],
    };

    // Replace existing pending entry or add new
    const idx = this.state.peers.findIndex((p) => p.identity.agentId === remoteIdentity.agentId);
    if (idx >= 0) {
      this.state.peers[idx] = peer;
    } else {
      if (this.state.peers.length >= this.state.config.maxPeers) return null; // At capacity
      this.state.peers.push(peer);
    }

    this.logMessage({
      id: crypto.randomUUID(),
      type: 'pair-accept',
      from: this.state.identity.agentId,
      to: remoteIdentity.agentId,
      timestamp: Date.now(),
      direction: 'outbound',
    });

    this.queueSave();
    return peer;
  }

  /**
   * Record an inbound pairing request (pending user approval).
   */
  recordInboundPairingRequest(remoteIdentity: AgentIdentity): PairedAgent {
    const existing = this.state.peers.find((p) => p.identity.agentId === remoteIdentity.agentId);
    if (existing) return existing;

    const peer: PairedAgent = {
      identity: remoteIdentity,
      pairingTimestamp: Date.now(),
      ownerPersonId: null,
      trustLevel: 0.1,
      sharedSecret: '',
      lastSeen: Date.now(),
      status: 'pending-inbound',
      autoApproveTaskTypes: [],
      advertisedCapabilities: [],
    };
    this.state.peers.push(peer);
    this.queueSave();
    return peer;
  }

  /** Block an agent (reject all future communication). */
  blockAgent(agentId: string): boolean {
    const peer = this.state.peers.find((p) => p.identity.agentId === agentId);
    if (!peer) return false;
    peer.status = 'blocked';
    peer.sharedSecret = ''; // Wipe shared secret
    peer.autoApproveTaskTypes = [];
    this.queueSave();
    return true;
  }

  /** Unpair an agent (remove entirely). */
  unpairAgent(agentId: string): boolean {
    const idx = this.state.peers.findIndex((p) => p.identity.agentId === agentId);
    if (idx < 0) return false;
    this.state.peers.splice(idx, 1);
    // Also remove delegations involving this agent
    this.state.delegations = this.state.delegations.filter(
      (d) => d.requestingAgentId !== agentId && d.targetAgentId !== agentId,
    );
    this.queueSave();
    return true;
  }

  /** Get a peer by agent ID. */
  getPeer(agentId: string): PairedAgent | null {
    return this.state.peers.find((p) => p.identity.agentId === agentId) ?? null;
  }

  /** Get all peers. */
  getAllPeers(): PairedAgent[] {
    return [...this.state.peers];
  }

  /** Get only paired (active) peers. */
  getPairedPeers(): PairedAgent[] {
    return this.state.peers.filter((p) => p.status === 'paired');
  }

  /** Get pending inbound pairing requests (awaiting user approval). */
  getPendingPairingRequests(): PairedAgent[] {
    return this.state.peers.filter((p) => p.status === 'pending-inbound');
  }

  // ── Trust ─────────────────────────────────────────────────────────

  /**
   * Update a peer's trust level based on its owner's Trust Graph scores.
   * Called when Trust Graph scores change or periodically.
   */
  updatePeerTrust(agentId: string, ownerTrust: { overall: number } | null, ownerPersonId?: string): boolean {
    const peer = this.state.peers.find((p) => p.identity.agentId === agentId);
    if (!peer) return false;
    peer.trustLevel = deriveAgentTrust(ownerTrust);
    if (ownerPersonId !== undefined) peer.ownerPersonId = ownerPersonId;
    this.queueSave();
    return true;
  }

  /** Set standing permissions for auto-approval on a peer. */
  setAutoApproveTaskTypes(agentId: string, taskTypes: string[]): boolean {
    const peer = this.state.peers.find((p) => p.identity.agentId === agentId);
    if (!peer || peer.status !== 'paired') return false;
    peer.autoApproveTaskTypes = [...taskTypes];
    this.queueSave();
    return true;
  }

  // ── Capabilities ──────────────────────────────────────────────────

  /** Record capabilities advertised by a remote agent. */
  updatePeerCapabilities(agentId: string, capabilities: string[]): boolean {
    const peer = this.state.peers.find((p) => p.identity.agentId === agentId);
    if (!peer) return false;
    peer.advertisedCapabilities = [...capabilities];
    peer.lastSeen = Date.now();
    this.queueSave();
    return true;
  }

  /** Find peers that advertise a specific capability. */
  findPeersWithCapability(capability: string): PairedAgent[] {
    return this.state.peers.filter(
      (p) => p.status === 'paired' && p.advertisedCapabilities.includes(capability),
    );
  }

  // ── Messaging ─────────────────────────────────────────────────────

  /**
   * Create a signed (and optionally encrypted) message to a peer.
   */
  createMessage(
    toAgentId: string,
    type: AgentMessageType,
    payload: Record<string, unknown>,
  ): AgentMessage | null {
    if (!this.state.keyPair || !this.state.identity) return null;

    const peer = this.state.peers.find((p) => p.identity.agentId === toAgentId);
    if (!peer || peer.status === 'blocked') return null;

    // Create signed message
    let message = createSignedMessage(
      this.state.identity.agentId,
      toAgentId,
      type,
      payload,
      this.state.keyPair.signingPrivateKey,
    );

    // Attach cLaw attestation — proves this agent operates under valid Fundamental Laws
    try {
      const { generateAttestation } = require('./claw-attestation');
      message.clawAttestation = generateAttestation(
        this.state.keyPair.signingPrivateKey,
        this.state.keyPair.signingPublicKey,
      );
    } catch (err) {
      console.warn('[AgentNetwork/cLaw] Failed to generate attestation:', err);
      // Continue without attestation — peer will flag but not silently drop
    }

    // Encrypt if we have a shared secret (paired peer)
    if (peer.sharedSecret) {
      message = encryptMessage(message, peer.sharedSecret);
    }

    this.state.messagesSent++;
    this.logMessage({
      id: message.id,
      type,
      from: this.state.identity.agentId,
      to: toAgentId,
      timestamp: message.timestamp,
      direction: 'outbound',
    });
    this.queueSave();

    return message;
  }

  /**
   * Process an inbound message. Decrypts if needed, verifies signature.
   * Returns the decrypted message or null if verification fails.
   */
  processInboundMessage(message: AgentMessage): AgentMessage | null {
    const peer = this.state.peers.find((p) => p.identity.agentId === message.fromAgentId);

    // Unknown sender — only allow pair-request messages
    if (!peer && message.type !== 'pair-request') return null;

    // Blocked peer — reject everything
    if (peer?.status === 'blocked') return null;

    // Decrypt if encrypted and we have a shared secret
    let processed = message;
    if (message.encrypted && peer?.sharedSecret) {
      try {
        processed = decryptMessage(message, peer.sharedSecret);
      } catch {
        return null; // Decryption failed
      }
    }

    // For pair-request, verify using the identity in the payload
    if (message.type === 'pair-request') {
      const senderIdentity = (processed.payload as { identity?: AgentIdentity }).identity;
      if (!senderIdentity) return null;
      // Reconstruct original signable from unencrypted payload
      const signable = `${message.fromAgentId}|${message.toAgentId}|${message.timestamp}|${message.type}|${canonicalize(processed.payload)}`;
      if (!ed25519Verify(signable, message.signature, senderIdentity.signingPublicKey)) return null;
    } else if (peer) {
      // Verify signature using known peer public key
      if (!verifyMessageSignature(processed, peer.identity.signingPublicKey)) return null;
    } else {
      return null; // Unknown sender, not a pair-request
    }

    // ── cLaw Attestation Verification ──────────────────────────────
    // Verify the peer operates under valid Fundamental Laws.
    // Failed attestation flags the message but does NOT silently drop it —
    // the user is informed and can manually override via trust overrides.
    try {
      const { verifyAttestation, hasUserOverride } = require('./claw-attestation');
      const attestationResult = verifyAttestation(
        processed.clawAttestation,
        peer?.identity?.signingPublicKey,
      );

      if (!attestationResult.valid) {
        const senderId = message.fromAgentId;
        if (hasUserOverride(senderId)) {
          console.warn(
            `[AgentNetwork/cLaw] Attestation failed for ${senderId} (${attestationResult.code}: ${attestationResult.reason}) — USER OVERRIDE ACTIVE, allowing`,
          );
        } else {
          console.warn(
            `[AgentNetwork/cLaw] Attestation failed for ${senderId} (${attestationResult.code}: ${attestationResult.reason}) — message flagged`,
          );
          // Attach attestation failure info to payload so the UI can inform the user
          (processed.payload as Record<string, unknown>).__clawAttestationFailed = true;
          (processed.payload as Record<string, unknown>).__clawAttestationReason = attestationResult.reason;
          (processed.payload as Record<string, unknown>).__clawAttestationCode = attestationResult.code;
        }
      }
    } catch (err) {
      console.warn('[AgentNetwork/cLaw] Attestation verification error:', err);
    }

    // Update last seen
    if (peer) {
      peer.lastSeen = Date.now();
    }

    this.state.messagesReceived++;
    this.logMessage({
      id: message.id,
      type: message.type,
      from: message.fromAgentId,
      to: message.toAgentId,
      timestamp: message.timestamp,
      direction: 'inbound',
    });
    this.queueSave();

    return processed;
  }

  // ── Task Delegation ───────────────────────────────────────────────

  /**
   * Create a task delegation request to a remote agent.
   * The delegation is recorded locally and a message should be sent to the peer.
   */
  createDelegation(
    targetAgentId: string,
    description: string,
    requiredCapabilities: string[] = [],
    deadline = 0,
  ): TaskDelegation | null {
    if (!this.state.identity) return null;

    const peer = this.state.peers.find((p) => p.identity.agentId === targetAgentId);
    if (!peer || peer.status !== 'paired') return null;

    const delegation: TaskDelegation = {
      id: crypto.randomUUID(),
      requestingAgentId: this.state.identity.agentId,
      targetAgentId,
      description,
      requiredCapabilities,
      deadline,
      status: 'pending-approval',
      result: null,
      error: null,
      autoApproved: false,
      createdAt: Date.now(),
      updatedAt: Date.now(),
    };

    this.state.delegations.push(delegation);
    this.pruneDelegations();
    this.queueSave();
    return delegation;
  }

  /**
   * Handle an inbound delegation request. Checks auto-approval standing
   * permissions. Returns the delegation object with status.
   */
  handleInboundDelegation(
    requestingAgentId: string,
    delegationId: string,
    description: string,
    requiredCapabilities: string[],
    deadline: number,
  ): TaskDelegation | null {
    if (!this.state.identity) return null;

    const peer = this.state.peers.find((p) => p.identity.agentId === requestingAgentId);
    if (!peer || peer.status !== 'paired') return null;

    const delegation: TaskDelegation = {
      id: delegationId,
      requestingAgentId,
      targetAgentId: this.state.identity.agentId,
      description,
      requiredCapabilities,
      deadline,
      status: 'pending-approval',
      result: null,
      error: null,
      autoApproved: false,
      createdAt: Date.now(),
      updatedAt: Date.now(),
    };

    // Check standing permissions for auto-approval
    if (canAutoApprove(peer, delegation)) {
      delegation.status = 'approved';
      delegation.autoApproved = true;
    }

    this.state.delegations.push(delegation);
    this.pruneDelegations();
    this.queueSave();
    return delegation;
  }

  /** Approve a pending delegation (user action). */
  approveDelegation(delegationId: string): boolean {
    const d = this.state.delegations.find((x) => x.id === delegationId);
    if (!d || d.status !== 'pending-approval') return false;
    d.status = 'approved';
    d.updatedAt = Date.now();
    this.queueSave();
    return true;
  }

  /** Reject a pending delegation (user action). */
  rejectDelegation(delegationId: string): boolean {
    const d = this.state.delegations.find((x) => x.id === delegationId);
    if (!d || d.status !== 'pending-approval') return false;
    d.status = 'rejected';
    d.updatedAt = Date.now();
    this.queueSave();
    return true;
  }

  /** Mark a delegation as in-progress. */
  startDelegation(delegationId: string): boolean {
    const d = this.state.delegations.find((x) => x.id === delegationId);
    if (!d || d.status !== 'approved') return false;
    d.status = 'in-progress';
    d.updatedAt = Date.now();
    this.queueSave();
    return true;
  }

  /** Complete a delegation with results. */
  completeDelegation(delegationId: string, result: unknown): boolean {
    const d = this.state.delegations.find((x) => x.id === delegationId);
    if (!d || (d.status !== 'approved' && d.status !== 'in-progress')) return false;
    d.status = 'completed';
    d.result = result;
    d.updatedAt = Date.now();
    this.queueSave();
    return true;
  }

  /** Fail a delegation with error. */
  failDelegation(delegationId: string, error: string): boolean {
    const d = this.state.delegations.find((x) => x.id === delegationId);
    if (!d || (d.status !== 'approved' && d.status !== 'in-progress')) return false;
    d.status = 'failed';
    d.error = error;
    d.updatedAt = Date.now();
    this.queueSave();
    return true;
  }

  /** Cancel a delegation (requester action). */
  cancelDelegation(delegationId: string): boolean {
    const d = this.state.delegations.find((x) => x.id === delegationId);
    if (!d || d.status === 'completed' || d.status === 'failed' || d.status === 'cancelled') return false;
    d.status = 'cancelled';
    d.updatedAt = Date.now();
    this.queueSave();
    return true;
  }

  /** Get a delegation by ID. */
  getDelegation(delegationId: string): TaskDelegation | null {
    return this.state.delegations.find((d) => d.id === delegationId) ?? null;
  }

  /** Get all delegations. */
  getAllDelegations(): TaskDelegation[] {
    return [...this.state.delegations];
  }

  /** Get delegations involving a specific agent. */
  getDelegationsForAgent(agentId: string): TaskDelegation[] {
    return this.state.delegations.filter(
      (d) => d.requestingAgentId === agentId || d.targetAgentId === agentId,
    );
  }

  /** Get pending inbound delegations (awaiting user approval). */
  getPendingInboundDelegations(): TaskDelegation[] {
    if (!this.state.identity) return [];
    const myId = this.state.identity.agentId;
    return this.state.delegations.filter(
      (d) => d.targetAgentId === myId && d.status === 'pending-approval',
    );
  }

  // ── Stats ─────────────────────────────────────────────────────────

  getStats(): AgentNetworkStats {
    const delegations = this.state.delegations;
    return {
      agentId: this.state.identity?.agentId ?? null,
      pairedCount: this.state.peers.filter((p) => p.status === 'paired').length,
      pendingCount: this.state.peers.filter((p) => p.status === 'pending-inbound' || p.status === 'pending-outbound').length,
      blockedCount: this.state.peers.filter((p) => p.status === 'blocked').length,
      messagesSent: this.state.messagesSent,
      messagesReceived: this.state.messagesReceived,
      totalDelegations: delegations.length,
      successfulDelegations: delegations.filter((d) => d.status === 'completed').length,
      failedDelegations: delegations.filter((d) => d.status === 'failed').length,
    };
  }

  // ── Config ────────────────────────────────────────────────────────

  getConfig(): AgentNetworkConfig {
    return { ...this.state.config };
  }

  updateConfig(partial: Partial<AgentNetworkConfig>): AgentNetworkConfig {
    Object.assign(this.state.config, partial);
    // Update identity names if changed
    if (this.state.identity && (partial.ownerName || partial.instanceName)) {
      if (partial.ownerName) this.state.identity.ownerName = partial.ownerName;
      if (partial.instanceName) this.state.identity.instanceName = partial.instanceName;
    }
    this.queueSave();
    return { ...this.state.config };
  }

  // ── Message Log ───────────────────────────────────────────────────

  getMessageLog(limit = 50): AgentNetworkState['messageLog'] {
    return this.state.messageLog.slice(-limit);
  }

  private logMessage(entry: AgentNetworkState['messageLog'][0]): void {
    this.state.messageLog.push(entry);
    if (this.state.messageLog.length > MAX_MESSAGE_LOG) {
      this.state.messageLog = this.state.messageLog.slice(-MAX_MESSAGE_LOG);
    }
  }

  // ── Context Generation ────────────────────────────────────────────

  /**
   * Generate a context string for system prompt injection.
   * Tells the model about available peers and recent delegation activity.
   */
  getPromptContext(): string {
    if (!this.state.config.enabled || !this.state.identity) return '';

    const paired = this.getPairedPeers();
    if (paired.length === 0) return '';

    const lines: string[] = ['[Agent Network]'];
    lines.push(`Your network ID: ${this.state.identity.agentId}`);
    lines.push(`Connected peers: ${paired.length}`);

    for (const peer of paired.slice(0, 5)) {
      const caps = peer.advertisedCapabilities.length > 0
        ? ` | capabilities: ${peer.advertisedCapabilities.slice(0, 3).join(', ')}`
        : '';
      lines.push(`  • ${peer.identity.instanceName} (${peer.identity.ownerName}) — trust: ${peer.trustLevel.toFixed(2)}${caps}`);
    }

    const pending = this.getPendingInboundDelegations();
    if (pending.length > 0) {
      lines.push(`⚠ ${pending.length} pending delegation request(s) awaiting approval`);
    }

    return lines.join('\n');
  }

  // ── Persistence ───────────────────────────────────────────────────

  private async load(): Promise<void> {
    try {
      const filePath = path.join(this.dataDir, 'agent-network.json');
      // Vault-aware read: decrypts if vault is unlocked, falls back to plaintext
      const { vaultRead } = getVault();
      const raw = await vaultRead(filePath);
      const data = JSON.parse(raw);
      if (data.keyPair) this.state.keyPair = data.keyPair;
      if (data.identity) this.state.identity = data.identity;
      if (Array.isArray(data.peers)) this.state.peers = data.peers;
      if (Array.isArray(data.delegations)) this.state.delegations = data.delegations;
      if (data.config) Object.assign(this.state.config, data.config);
      if (typeof data.messagesSent === 'number') this.state.messagesSent = data.messagesSent;
      if (typeof data.messagesReceived === 'number') this.state.messagesReceived = data.messagesReceived;
      if (Array.isArray(data.messageLog)) this.state.messageLog = data.messageLog;
    } catch {
      // No saved state — will generate identity on first run
    }
  }

  private async save(): Promise<void> {
    try {
      const filePath = path.join(this.dataDir, 'agent-network.json');
      // Vault-aware write: encrypts if vault is unlocked, falls back to plaintext
      const { vaultWrite } = getVault();
      await vaultWrite(filePath, JSON.stringify(this.state, null, 2));
    } catch (err) {
      console.error('[AgentNetwork] Save failed:', err);
    }
  }

  private queueSave(): void {
    if (this.saveTimer) clearTimeout(this.saveTimer);
    this.saveTimer = setTimeout(() => this.save(), SAVE_DEBOUNCE_MS);
  }

  private pruneDelegations(): void {
    const max = this.state.config.maxDelegationHistory;
    if (this.state.delegations.length > max) {
      // Remove oldest completed/failed/cancelled delegations first
      const terminal = this.state.delegations.filter(
        (d) => d.status === 'completed' || d.status === 'failed' || d.status === 'cancelled',
      );
      const active = this.state.delegations.filter(
        (d) => d.status !== 'completed' && d.status !== 'failed' && d.status !== 'cancelled',
      );
      // Keep all active + most recent terminal
      const keptTerminal = terminal
        .sort((a, b) => b.updatedAt - a.updatedAt)
        .slice(0, max - active.length);
      this.state.delegations = [...active, ...keptTerminal];
    }
  }

  /** Flush pending saves and clean up. */
  async stop(): Promise<void> {
    if (this.saveTimer) {
      clearTimeout(this.saveTimer);
      this.saveTimer = null;
    }
    await this.save();
  }
}

// ── Singleton Export ────────────────────────────────────────────────

export const agentNetwork = new AgentNetwork();
