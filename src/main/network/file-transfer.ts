/**
 * network/file-transfer.ts — Trusted File Transfer Protocol
 *
 * Enables trust-gated, chunked, SHA-256-verified file transfers between
 * paired Agent Friday instances. Files are split into 512 KB chunks,
 * each individually hashed, with a final whole-file SHA-256 integrity check.
 *
 * Trust Thresholds (based on PairedAgent.trustLevel 0-1):
 *   ≥ 0.7  → auto-accept (user can configure)
 *   0.3-0.7 → prompt user for approval
 *   < 0.3  → auto-reject (too untrusted)
 *
 * Safety:
 *   - 50 MB maximum file size (configurable)
 *   - Dangerous extensions blocked (.exe, .bat, .cmd, .ps1, .scr, .vbs, .msi, .dll)
 *   - All transfers logged for audit
 *   - Incomplete transfers cleaned up after 10 minutes
 *   - User can cancel any in-progress transfer
 *
 * cLaw alignment:
 *   - Second Law: User explicitly approves or has standing auto-accept rules
 *   - Third Law: Self-protection via size limits, extension blocking, integrity checks
 *   - First Law: No file transfer can harm users — dangerous files are blocked
 */

import crypto from 'crypto';
import fs from 'fs/promises';
import path from 'path';
import { app } from 'electron';

// ── Constants ─────────────────────────────────────────────────────────

/** Maximum file size in bytes (50 MB) */
const MAX_FILE_SIZE = 50 * 1024 * 1024;

/** Chunk size in bytes (512 KB) */
const CHUNK_SIZE = 512 * 1024;

/** Transfer timeout in milliseconds (10 minutes) */
const TRANSFER_TIMEOUT_MS = 10 * 60 * 1000;

/** Trust level above which transfers are auto-accepted */
const AUTO_ACCEPT_TRUST = 0.7;

/** Trust level below which transfers are auto-rejected */
const AUTO_REJECT_TRUST = 0.3;

/** Dangerous file extensions that are always blocked */
const BLOCKED_EXTENSIONS = new Set([
  '.exe', '.bat', '.cmd', '.ps1', '.scr', '.vbs', '.vbe',
  '.msi', '.dll', '.sys', '.com', '.pif', '.hta', '.cpl',
  '.inf', '.reg', '.ws', '.wsf', '.wsc', '.wsh', '.msp',
  '.mst', '.jse', '.lnk', '.appref-ms',
]);

// ── Types ─────────────────────────────────────────────────────────────

export interface FileTransferRequest {
  /** Unique transfer ID */
  transferId: string;
  /** Original file name */
  fileName: string;
  /** File size in bytes */
  fileSize: number;
  /** MIME type (best guess) */
  mimeType: string;
  /** SHA-256 hash of the complete file */
  fileHash: string;
  /** Total number of chunks */
  totalChunks: number;
  /** Optional description from the sender */
  description?: string;
}

export interface FileTransferResponse {
  /** Transfer ID this responds to */
  transferId: string;
  /** Whether the transfer is accepted */
  accepted: boolean;
  /** Reason for rejection (null if accepted) */
  reason?: string;
}

export interface FileTransferChunk {
  /** Transfer ID this chunk belongs to */
  transferId: string;
  /** Zero-based chunk index */
  chunkIndex: number;
  /** Total number of chunks */
  totalChunks: number;
  /** Base64-encoded chunk data */
  data: string;
  /** SHA-256 hash of this chunk's raw bytes */
  chunkHash: string;
}

export type TransferStatus =
  | 'pending-approval'   // Waiting for recipient approval
  | 'approved'           // Approved, waiting for chunks
  | 'in-progress'        // Receiving chunks
  | 'completed'          // All chunks received, verified
  | 'failed'             // Hash mismatch or error
  | 'rejected'           // Explicitly rejected
  | 'cancelled'          // Cancelled by either party
  | 'timed-out';         // Exceeded timeout

export type TransferDirection = 'inbound' | 'outbound';

export interface FileTransfer {
  /** Unique transfer ID */
  transferId: string;
  /** Direction from our perspective */
  direction: TransferDirection;
  /** Remote agent ID */
  remoteAgentId: string;
  /** Remote agent name (for display) */
  remoteAgentName: string;
  /** File name */
  fileName: string;
  /** File size in bytes */
  fileSize: number;
  /** MIME type */
  mimeType: string;
  /** SHA-256 of complete file */
  fileHash: string;
  /** Total chunks expected */
  totalChunks: number;
  /** Chunks received so far (indices) */
  receivedChunks: Set<number>;
  /** Assembled file data (Buffer array, indexed by chunk) */
  chunkBuffers: (Buffer | null)[];
  /** Current status */
  status: TransferStatus;
  /** When the transfer was initiated */
  startedAt: number;
  /** When the transfer completed/failed */
  completedAt: number | null;
  /** Description from sender */
  description?: string;
  /** Local file path (set after successful completion) */
  localPath?: string;
  /** Error message if failed */
  error?: string;
}

export interface TransferAuditEntry {
  transferId: string;
  direction: TransferDirection;
  remoteAgentId: string;
  remoteAgentName: string;
  fileName: string;
  fileSize: number;
  status: TransferStatus;
  startedAt: number;
  completedAt: number | null;
  error?: string;
}

export interface TrustDecision {
  action: 'auto-accept' | 'prompt' | 'auto-reject';
  trustLevel: number;
  reason: string;
}

// ── File Transfer Engine ──────────────────────────────────────────────

class FileTransferEngine {
  private transfers: Map<string, FileTransfer> = new Map();
  private auditLog: TransferAuditEntry[] = [];
  private transfersDir = '';
  private auditPath = '';
  private cleanupInterval: ReturnType<typeof setInterval> | null = null;

  async initialize(): Promise<void> {
    this.transfersDir = path.join(app.getPath('userData'), 'file-transfers');
    await fs.mkdir(this.transfersDir, { recursive: true });
    this.auditPath = path.join(app.getPath('userData'), 'file-transfer-audit.json');

    // Load audit log
    try {
      const { vaultRead } = require('../vault');
      const data = await vaultRead(this.auditPath);
      this.auditLog = JSON.parse(data);
    } catch {
      this.auditLog = [];
    }

    // Start cleanup timer for stale transfers
    this.cleanupInterval = setInterval(() => this.cleanupStaleTransfers(), 60_000);
    console.log('[FileTransfer] Initialized');
  }

  shutdown(): void {
    if (this.cleanupInterval) {
      clearInterval(this.cleanupInterval);
      this.cleanupInterval = null;
    }
  }

  // ── Outbound: Send a file ──────────────────────────────────────────

  /**
   * Prepare a file for transfer. Reads the file, computes hash, splits into chunks.
   * Returns the transfer request to send to the peer, or null if the file is invalid.
   */
  async prepareOutboundTransfer(
    filePath: string,
    remoteAgentId: string,
    remoteAgentName: string,
    description?: string,
  ): Promise<{ request: FileTransferRequest; transfer: FileTransfer } | { error: string }> {
    // Validate file exists
    let stat;
    try {
      stat = await fs.stat(filePath);
    } catch {
      return { error: `File not found: ${filePath}` };
    }

    if (!stat.isFile()) {
      return { error: 'Path is not a file' };
    }

    // Check size
    if (stat.size > MAX_FILE_SIZE) {
      return { error: `File too large: ${(stat.size / 1024 / 1024).toFixed(1)} MB (max ${MAX_FILE_SIZE / 1024 / 1024} MB)` };
    }

    if (stat.size === 0) {
      return { error: 'File is empty' };
    }

    // Check extension
    const ext = path.extname(filePath).toLowerCase();
    if (BLOCKED_EXTENSIONS.has(ext)) {
      return { error: `Blocked file extension: ${ext}` };
    }

    // Read file and compute hash
    const fileData = await fs.readFile(filePath);
    const fileHash = crypto.createHash('sha256').update(fileData).digest('hex');
    const fileName = path.basename(filePath);
    const totalChunks = Math.ceil(fileData.length / CHUNK_SIZE);

    const transferId = crypto.randomUUID();
    const mimeType = guessMimeType(ext);

    const transfer: FileTransfer = {
      transferId,
      direction: 'outbound',
      remoteAgentId,
      remoteAgentName,
      fileName,
      fileSize: fileData.length,
      mimeType,
      fileHash,
      totalChunks,
      receivedChunks: new Set(),
      chunkBuffers: [],
      status: 'pending-approval',
      startedAt: Date.now(),
      completedAt: null,
      description,
    };

    // Pre-split into chunks and store in the transfer
    for (let i = 0; i < totalChunks; i++) {
      const start = i * CHUNK_SIZE;
      const end = Math.min(start + CHUNK_SIZE, fileData.length);
      transfer.chunkBuffers.push(fileData.subarray(start, end) as Buffer);
    }

    this.transfers.set(transferId, transfer);

    const request: FileTransferRequest = {
      transferId,
      fileName,
      fileSize: fileData.length,
      mimeType,
      fileHash,
      totalChunks,
      description,
    };

    return { request, transfer };
  }

  /**
   * Get the next chunk to send for an outbound transfer.
   * Returns null if all chunks have been sent or transfer is invalid.
   */
  getOutboundChunk(transferId: string, chunkIndex: number): FileTransferChunk | null {
    const transfer = this.transfers.get(transferId);
    if (!transfer || transfer.direction !== 'outbound') return null;
    if (chunkIndex < 0 || chunkIndex >= transfer.totalChunks) return null;

    const chunkData = transfer.chunkBuffers[chunkIndex];
    if (!chunkData) return null;

    const chunkHash = crypto.createHash('sha256').update(chunkData).digest('hex');

    return {
      transferId,
      chunkIndex,
      totalChunks: transfer.totalChunks,
      data: chunkData.toString('base64'),
      chunkHash,
    };
  }

  /**
   * Get all chunks for an outbound transfer (for small files sent in one burst).
   */
  getAllOutboundChunks(transferId: string): FileTransferChunk[] {
    const chunks: FileTransferChunk[] = [];
    const transfer = this.transfers.get(transferId);
    if (!transfer || transfer.direction !== 'outbound') return chunks;

    for (let i = 0; i < transfer.totalChunks; i++) {
      const chunk = this.getOutboundChunk(transferId, i);
      if (chunk) chunks.push(chunk);
    }
    return chunks;
  }

  /**
   * Handle a transfer response from the remote peer.
   */
  handleTransferResponse(response: FileTransferResponse): void {
    const transfer = this.transfers.get(response.transferId);
    if (!transfer || transfer.direction !== 'outbound') return;

    if (response.accepted) {
      transfer.status = 'approved';
    } else {
      transfer.status = 'rejected';
      transfer.completedAt = Date.now();
      transfer.error = response.reason || 'Rejected by recipient';
      this.auditTransfer(transfer);
    }
  }

  /**
   * Mark an outbound transfer as complete (all chunks acknowledged).
   */
  completeOutboundTransfer(transferId: string): void {
    const transfer = this.transfers.get(transferId);
    if (!transfer || transfer.direction !== 'outbound') return;
    transfer.status = 'completed';
    transfer.completedAt = Date.now();
    // Free chunk buffers
    transfer.chunkBuffers = [];
    this.auditTransfer(transfer);
  }

  // ── Inbound: Receive a file ────────────────────────────────────────

  /**
   * Evaluate trust and decide whether to accept an inbound file transfer.
   */
  evaluateTransferRequest(
    request: FileTransferRequest,
    senderTrustLevel: number,
  ): TrustDecision {
    // Check extension
    const ext = path.extname(request.fileName).toLowerCase();
    if (BLOCKED_EXTENSIONS.has(ext)) {
      return {
        action: 'auto-reject',
        trustLevel: senderTrustLevel,
        reason: `Blocked file extension: ${ext}`,
      };
    }

    // Check size
    if (request.fileSize > MAX_FILE_SIZE) {
      return {
        action: 'auto-reject',
        trustLevel: senderTrustLevel,
        reason: `File too large: ${(request.fileSize / 1024 / 1024).toFixed(1)} MB (max ${MAX_FILE_SIZE / 1024 / 1024} MB)`,
      };
    }

    // Trust-based decision
    if (senderTrustLevel >= AUTO_ACCEPT_TRUST) {
      return {
        action: 'auto-accept',
        trustLevel: senderTrustLevel,
        reason: `Trust level ${(senderTrustLevel * 100).toFixed(0)}% exceeds auto-accept threshold`,
      };
    }

    if (senderTrustLevel < AUTO_REJECT_TRUST) {
      return {
        action: 'auto-reject',
        trustLevel: senderTrustLevel,
        reason: `Trust level ${(senderTrustLevel * 100).toFixed(0)}% below minimum threshold`,
      };
    }

    return {
      action: 'prompt',
      trustLevel: senderTrustLevel,
      reason: `Trust level ${(senderTrustLevel * 100).toFixed(0)}% — requires user approval`,
    };
  }

  /**
   * Accept an inbound transfer request. Creates the transfer tracking state.
   */
  acceptInboundTransfer(
    request: FileTransferRequest,
    remoteAgentId: string,
    remoteAgentName: string,
  ): FileTransfer {
    const transfer: FileTransfer = {
      transferId: request.transferId,
      direction: 'inbound',
      remoteAgentId,
      remoteAgentName,
      fileName: request.fileName,
      fileSize: request.fileSize,
      mimeType: request.mimeType,
      fileHash: request.fileHash,
      totalChunks: request.totalChunks,
      receivedChunks: new Set(),
      chunkBuffers: new Array(request.totalChunks).fill(null),
      status: 'in-progress',
      startedAt: Date.now(),
      completedAt: null,
      description: request.description,
    };

    this.transfers.set(request.transferId, transfer);
    return transfer;
  }

  /**
   * Process an inbound file chunk. Verifies chunk hash and stores it.
   * Returns true if the chunk was valid and accepted.
   */
  processInboundChunk(chunk: FileTransferChunk): { valid: boolean; complete: boolean; error?: string } {
    const transfer = this.transfers.get(chunk.transferId);
    if (!transfer || transfer.direction !== 'inbound') {
      return { valid: false, complete: false, error: 'Unknown transfer' };
    }

    if (transfer.status !== 'in-progress' && transfer.status !== 'approved') {
      return { valid: false, complete: false, error: `Transfer status is ${transfer.status}` };
    }

    if (chunk.chunkIndex < 0 || chunk.chunkIndex >= transfer.totalChunks) {
      return { valid: false, complete: false, error: `Invalid chunk index: ${chunk.chunkIndex}` };
    }

    // Decode and verify chunk hash
    const chunkData = Buffer.from(chunk.data, 'base64');
    const computedHash = crypto.createHash('sha256').update(chunkData).digest('hex');

    if (computedHash !== chunk.chunkHash) {
      return {
        valid: false,
        complete: false,
        error: `Chunk ${chunk.chunkIndex} hash mismatch: expected ${chunk.chunkHash.slice(0, 12)}..., got ${computedHash.slice(0, 12)}...`,
      };
    }

    // Store chunk
    transfer.chunkBuffers[chunk.chunkIndex] = chunkData;
    transfer.receivedChunks.add(chunk.chunkIndex);
    transfer.status = 'in-progress';

    // Check if all chunks received
    if (transfer.receivedChunks.size === transfer.totalChunks) {
      return { valid: true, complete: true };
    }

    return { valid: true, complete: false };
  }

  /**
   * Finalize an inbound transfer: assemble all chunks, verify whole-file hash,
   * and save to disk.
   */
  async finalizeInboundTransfer(transferId: string): Promise<{ success: boolean; localPath?: string; error?: string }> {
    const transfer = this.transfers.get(transferId);
    if (!transfer || transfer.direction !== 'inbound') {
      return { success: false, error: 'Unknown transfer' };
    }

    if (transfer.receivedChunks.size !== transfer.totalChunks) {
      return { success: false, error: `Missing chunks: received ${transfer.receivedChunks.size}/${transfer.totalChunks}` };
    }

    // Assemble file
    const chunks = transfer.chunkBuffers.filter((b): b is Buffer => b !== null);
    if (chunks.length !== transfer.totalChunks) {
      transfer.status = 'failed';
      transfer.error = 'Chunk assembly failed — missing buffers';
      transfer.completedAt = Date.now();
      this.auditTransfer(transfer);
      return { success: false, error: transfer.error };
    }

    const assembled = Buffer.concat(chunks);

    // Verify whole-file SHA-256
    const computedHash = crypto.createHash('sha256').update(assembled).digest('hex');
    if (computedHash !== transfer.fileHash) {
      transfer.status = 'failed';
      transfer.error = `File hash mismatch: expected ${transfer.fileHash.slice(0, 12)}..., got ${computedHash.slice(0, 12)}...`;
      transfer.completedAt = Date.now();
      this.auditTransfer(transfer);
      return { success: false, error: transfer.error };
    }

    // Sanitize filename and save
    // Crypto Sprint 3 (MEDIUM-003): Files are encrypted at rest using vault encryption.
    // This prevents an OS-level admin from reading received files directly from disk.
    const safeName = sanitizeFileName(transfer.fileName);
    const timestamp = Date.now();
    const savePath = path.join(this.transfersDir, `${timestamp}_${safeName}`);

    try {
      const { vaultWriteBinary } = require('../vault');
      await vaultWriteBinary(savePath, assembled);
    } catch (err: any) {
      transfer.status = 'failed';
      transfer.error = `Failed to save file: ${err?.message || 'unknown'}`;
      transfer.completedAt = Date.now();
      this.auditTransfer(transfer);
      return { success: false, error: transfer.error };
    }

    transfer.status = 'completed';
    transfer.completedAt = Date.now();
    transfer.localPath = savePath;
    // Free buffers
    transfer.chunkBuffers = [];
    this.auditTransfer(transfer);

    console.log(`[FileTransfer] Completed inbound transfer: ${safeName} (${(transfer.fileSize / 1024).toFixed(1)} KB) from ${transfer.remoteAgentName}`);
    return { success: true, localPath: savePath };
  }

  // ── Transfer Management ────────────────────────────────────────────

  /**
   * Cancel a transfer (either direction).
   */
  cancelTransfer(transferId: string): boolean {
    const transfer = this.transfers.get(transferId);
    if (!transfer) return false;
    if (transfer.status === 'completed' || transfer.status === 'failed') return false;

    transfer.status = 'cancelled';
    transfer.completedAt = Date.now();
    transfer.chunkBuffers = [];
    this.auditTransfer(transfer);
    return true;
  }

  /**
   * Get a transfer by ID.
   */
  getTransfer(transferId: string): FileTransfer | undefined {
    return this.transfers.get(transferId);
  }

  /**
   * Get all active (non-terminal) transfers.
   */
  getActiveTransfers(): FileTransfer[] {
    return Array.from(this.transfers.values()).filter(
      (t) => !['completed', 'failed', 'rejected', 'cancelled', 'timed-out'].includes(t.status),
    );
  }

  /**
   * Get transfer progress as a fraction (0-1).
   */
  getTransferProgress(transferId: string): number {
    const transfer = this.transfers.get(transferId);
    if (!transfer) return 0;
    if (transfer.totalChunks === 0) return 0;
    return transfer.receivedChunks.size / transfer.totalChunks;
  }

  /**
   * Read a completed transfer's file content (decrypted from vault).
   *
   * Crypto Sprint 3 (MEDIUM-003): Received files are now vault-encrypted at rest.
   * This method transparently decrypts them. Falls back to raw read if vault
   * is locked or file is plaintext (pre-encryption transfers).
   *
   * @param transferId - Transfer ID or local file path
   * @returns Decrypted file buffer, or null if not found
   */
  async readTransferredFile(transferId: string): Promise<Buffer | null> {
    // Crypto Sprint 4 (HIGH-PATH-001): Only accept transfer IDs from the internal map.
    // Previously this accepted arbitrary file paths, enabling path traversal attacks
    // where a compromised renderer could read any file on disk via crafted paths.
    const transfer = this.transfers.get(transferId);
    if (!transfer?.localPath) return null;

    const filePath = transfer.localPath;

    // Defense-in-depth: verify the resolved path is inside the transfers directory
    const resolved = path.resolve(filePath);
    const transfersBase = path.resolve(this.transfersDir);
    if (!resolved.startsWith(transfersBase + path.sep) && resolved !== transfersBase) {
      console.warn(`[FileTransfer] ⚠ Path traversal blocked: ${path.basename(filePath)} escapes transfers directory`);
      return null;
    }

    try {
      const { vaultReadBinary } = require('../vault');
      return await vaultReadBinary(filePath);
    } catch {
      // Fallback to raw read if vault module unavailable
      return fs.readFile(filePath);
    }
  }

  /**
   * Get the audit log.
   */
  getAuditLog(): TransferAuditEntry[] {
    return [...this.auditLog];
  }

  // ── Private Helpers ────────────────────────────────────────────────

  private auditTransfer(transfer: FileTransfer): void {
    this.auditLog.push({
      transferId: transfer.transferId,
      direction: transfer.direction,
      remoteAgentId: transfer.remoteAgentId,
      remoteAgentName: transfer.remoteAgentName,
      fileName: transfer.fileName,
      fileSize: transfer.fileSize,
      status: transfer.status,
      startedAt: transfer.startedAt,
      completedAt: transfer.completedAt,
      error: transfer.error,
    });

    // Cap audit log at 500 entries
    if (this.auditLog.length > 500) {
      this.auditLog = this.auditLog.slice(-400);
    }

    this.saveAuditLog().catch((err) =>
      // Crypto Sprint 17: Sanitize error output.
      console.warn('[FileTransfer] Failed to save audit log:', err instanceof Error ? err.message : 'Unknown error'),
    );
  }

  private async saveAuditLog(): Promise<void> {
    try {
      const { vaultWrite } = require('../vault');
      await vaultWrite(this.auditPath, JSON.stringify(this.auditLog, null, 2));
    } catch (err) {
      console.warn('[FileTransfer] Failed to save audit log:', err instanceof Error ? err.message : 'Unknown error');
    }
  }

  private cleanupStaleTransfers(): void {
    const now = Date.now();
    for (const [id, transfer] of this.transfers) {
      // Clean up completed/failed transfers older than 1 hour (free memory)
      if (
        ['completed', 'failed', 'rejected', 'cancelled', 'timed-out'].includes(transfer.status) &&
        transfer.completedAt &&
        now - transfer.completedAt > 60 * 60 * 1000
      ) {
        this.transfers.delete(id);
        continue;
      }

      // Time out active transfers
      if (
        ['pending-approval', 'approved', 'in-progress'].includes(transfer.status) &&
        now - transfer.startedAt > TRANSFER_TIMEOUT_MS
      ) {
        transfer.status = 'timed-out';
        transfer.completedAt = now;
        transfer.error = 'Transfer timed out';
        transfer.chunkBuffers = [];
        this.auditTransfer(transfer);
        console.warn(`[FileTransfer] Transfer ${id} timed out`);
      }
    }
  }
}

// ── Utility Functions ─────────────────────────────────────────────────

/**
 * Sanitize a file name for safe local storage.
 * Removes path separators, null bytes, and other dangerous characters.
 */
// Crypto Sprint 4 (MEDIUM-SANITIZE): Windows reserved device names.
// Writing to these names causes I/O errors or device access on Windows.
const WINDOWS_RESERVED = new Set([
  'CON', 'PRN', 'AUX', 'NUL',
  'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
  'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9',
]);

function sanitizeFileName(name: string): string {
  // Remove path separators and null bytes
  let safe = name.replace(/[/\\:\0]/g, '_');
  // Remove leading dots (hidden files on Unix)
  safe = safe.replace(/^\.+/, '');
  // Collapse multiple underscores
  safe = safe.replace(/_+/g, '_');
  // Truncate to reasonable length
  if (safe.length > 200) {
    const ext = path.extname(safe);
    safe = safe.slice(0, 200 - ext.length) + ext;
  }
  // Crypto Sprint 4: Block Windows reserved device names (CON, PRN, NUL, COM1-9, LPT1-9).
  // A P2P peer could send a file named "CON" which causes I/O errors on Windows.
  const nameWithoutExt = safe.replace(/\.[^.]*$/, '').toUpperCase();
  if (WINDOWS_RESERVED.has(nameWithoutExt)) {
    safe = `_${safe}`;
  }
  // Fallback
  if (!safe || safe === '_') safe = 'unnamed_file';
  return safe;
}

/**
 * Guess MIME type from file extension.
 */
function guessMimeType(ext: string): string {
  const mimeMap: Record<string, string> = {
    '.txt': 'text/plain',
    '.md': 'text/markdown',
    '.html': 'text/html',
    '.css': 'text/css',
    '.js': 'application/javascript',
    '.ts': 'application/typescript',
    '.json': 'application/json',
    '.xml': 'application/xml',
    '.csv': 'text/csv',
    '.pdf': 'application/pdf',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.webp': 'image/webp',
    '.svg': 'image/svg+xml',
    '.mp3': 'audio/mpeg',
    '.wav': 'audio/wav',
    '.mp4': 'video/mp4',
    '.webm': 'video/webm',
    '.zip': 'application/zip',
    '.tar': 'application/x-tar',
    '.gz': 'application/gzip',
    '.7z': 'application/x-7z-compressed',
    '.doc': 'application/msword',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.xls': 'application/vnd.ms-excel',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.ppt': 'application/vnd.ms-powerpoint',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  };
  return mimeMap[ext.toLowerCase()] || 'application/octet-stream';
}

// ── Singleton Export ──────────────────────────────────────────────────

export const fileTransferEngine = new FileTransferEngine();
