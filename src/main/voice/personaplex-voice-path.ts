/**
 * personaplex-voice-path.ts — Main-process WebSocket manager for PersonaPlex.
 *
 * Manages the full-duplex WebSocket connection to the local PersonaPlex server.
 * Audio flows:
 *   Renderer → IPC (PCM Float32) → this module → WSS (binary PCM) → PersonaPlex
 *   PersonaPlex → WSS (Ogg Opus pages) → this module → decode → IPC (PCM Float32) → Renderer
 *
 * This keeps all binary protocol complexity in the main process.
 * The renderer only handles mic capture and audio playback.
 */

import { ipcMain, BrowserWindow } from 'electron';
import { EventEmitter } from 'node:events';
import WebSocket from 'ws';

// -- Types --------------------------------------------------------------------

export interface PersonaPlexSessionConfig {
  /** WSS URL of the local PersonaPlex server */
  wssUrl: string;
  /** Voice preset ID (e.g., 'NATF2') */
  voiceId?: string;
  /** Text prompt defining persona/role */
  textPrompt?: string;
}

interface OggPageHeader {
  capturePattern: string;
  version: number;
  headerType: number;
  granulePosition: bigint;
  serialNumber: number;
  pageSequenceNumber: number;
  segmentCount: number;
  segmentTable: number[];
  dataOffset: number;
  dataLength: number;
}

// -- Module state -------------------------------------------------------------

let activeWs: WebSocket | null = null;
let isConnected = false;

export const personaplexPathEvents = new EventEmitter();
personaplexPathEvents.on('error', (err) => {
  console.error('[PersonaPlexPath] Unhandled event error:', err);
});

// -- Helpers ------------------------------------------------------------------

function getMainWindow(): BrowserWindow | null {
  const wins = BrowserWindow.getAllWindows();
  return wins[0] ?? null;
}

function sendToRenderer(channel: string, ...args: unknown[]): void {
  const win = getMainWindow();
  if (win && !win.isDestroyed()) {
    win.webContents.send(channel, ...args);
  }
}

/**
 * Parse Ogg page headers to extract raw Opus packets.
 * Ogg format: https://xiph.org/ogg/doc/framing.html
 *
 * Each page has:
 *   - 4 bytes: "OggS" capture pattern
 *   - 1 byte: version (0)
 *   - 1 byte: header type flags
 *   - 8 bytes: granule position
 *   - 4 bytes: serial number
 *   - 4 bytes: page sequence number
 *   - 4 bytes: CRC checksum
 *   - 1 byte: number of segments
 *   - N bytes: segment table (each byte = segment length)
 *   - Data: concatenated segments
 */
function parseOggPages(buffer: Buffer): Buffer[] {
  const packets: Buffer[] = [];
  let offset = 0;

  while (offset + 27 <= buffer.length) {
    // Check for OggS capture pattern
    if (buffer.toString('ascii', offset, offset + 4) !== 'OggS') {
      // Try to find next OggS
      const nextOgg = buffer.indexOf('OggS', offset + 1);
      if (nextOgg === -1) break;
      offset = nextOgg;
      continue;
    }

    const segmentCount = buffer[offset + 26];
    if (offset + 27 + segmentCount > buffer.length) break;

    const segmentTable = [];
    let dataLength = 0;
    for (let i = 0; i < segmentCount; i++) {
      const segLen = buffer[offset + 27 + i];
      segmentTable.push(segLen);
      dataLength += segLen;
    }

    const dataStart = offset + 27 + segmentCount;
    if (dataStart + dataLength > buffer.length) break;

    // Extract packet data (skip Ogg header pages — page sequence 0 and 1)
    const pageSequence = buffer.readUInt32LE(offset + 18);
    if (pageSequence >= 2) {
      // This is audio data, not header
      packets.push(buffer.subarray(dataStart, dataStart + dataLength));
    }

    offset = dataStart + dataLength;
  }

  return packets;
}

/**
 * Convert PCM Float32Array to Int16 binary buffer for transmission.
 * PersonaPlex expects PCM audio frames.
 */
function float32ToInt16Buffer(float32: Float32Array): Buffer {
  const int16 = new Int16Array(float32.length);
  for (let i = 0; i < float32.length; i++) {
    const s = Math.max(-1, Math.min(1, float32[i]));
    int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
  }
  return Buffer.from(int16.buffer);
}

// -- Connection Management ----------------------------------------------------

/**
 * Connect to the local PersonaPlex server via WebSocket.
 */
async function connectToServer(config: PersonaPlexSessionConfig): Promise<void> {
  if (activeWs) {
    activeWs.close();
    activeWs = null;
    isConnected = false;
  }

  console.log(`[PersonaPlexPath] Connecting to ${config.wssUrl}...`);

  return new Promise<void>((resolve, reject) => {
    const ws = new WebSocket(config.wssUrl, {
      rejectUnauthorized: false, // Self-signed cert for local server
    });

    ws.binaryType = 'nodebuffer';
    activeWs = ws;

    const timeout = setTimeout(() => {
      ws.close();
      reject(new Error('PersonaPlex WebSocket connection timed out'));
    }, 15_000);

    ws.on('open', () => {
      clearTimeout(timeout);
      isConnected = true;
      console.log('[PersonaPlexPath] WebSocket connected');
      sendToRenderer('personaplex:connected');
      personaplexPathEvents.emit('connected');
      resolve();
    });

    ws.on('message', (data: Buffer) => {
      if (!isConnected) return;

      // PersonaPlex sends Ogg Opus pages as binary frames
      if (Buffer.isBuffer(data)) {
        // Forward Ogg Opus data as base64 for the renderer to decode
        // via Web Audio's decodeAudioData() — avoids slow Array.from() on large buffers
        sendToRenderer('personaplex:audio-data', data.toString('base64'));

        // Also try to extract any text content if the server sends text messages
      } else if (typeof data === 'string') {
        try {
          const msg = JSON.parse(data);
          if (msg.text) {
            sendToRenderer('personaplex:transcript', msg.text);
            personaplexPathEvents.emit('transcript', msg.text);
          }
        } catch {
          // Not JSON — might be text transcript
          sendToRenderer('personaplex:transcript', data);
        }
      }
    });

    ws.on('close', (code, reason) => {
      clearTimeout(timeout);
      isConnected = false;
      activeWs = null;
      const reasonStr = reason?.toString() || '';
      console.log(`[PersonaPlexPath] WebSocket closed: ${code} ${reasonStr}`);
      sendToRenderer('personaplex:disconnected', code, reasonStr);
      personaplexPathEvents.emit('disconnected', code, reasonStr);
    });

    ws.on('error', (err) => {
      console.error('[PersonaPlexPath] WebSocket error:', err.message);
      sendToRenderer('personaplex:error', err.message);
      personaplexPathEvents.emit('error', err);

      if (!isConnected) {
        clearTimeout(timeout);
        reject(err);
      }
    });
  });
}

/**
 * Send PCM audio data to PersonaPlex server.
 * Accepts Float32Array (from renderer mic capture) and converts to binary PCM.
 */
function sendAudio(audioData: number[]): void {
  if (!activeWs || activeWs.readyState !== WebSocket.OPEN) return;

  const float32 = new Float32Array(audioData);
  const pcmBuffer = float32ToInt16Buffer(float32);
  activeWs.send(pcmBuffer);
}

/**
 * Disconnect from PersonaPlex server.
 */
function disconnect(): void {
  if (activeWs) {
    isConnected = false;
    activeWs.close(1000, 'Client disconnect');
    activeWs = null;
  }
}

// -- IPC Registration ---------------------------------------------------------

/**
 * Register all PersonaPlex voice path IPC handlers.
 */
export function registerPersonaPlexVoicePathHandlers(): void {
  ipcMain.handle('personaplex:connect', async (_event, config: unknown) => {
    if (!config || typeof config !== 'object') {
      throw new Error('personaplex:connect requires a config object');
    }
    const c = config as PersonaPlexSessionConfig;
    if (!c.wssUrl || typeof c.wssUrl !== 'string') {
      throw new Error('personaplex:connect config must include wssUrl');
    }
    await connectToServer(c);
  });

  ipcMain.on('personaplex:send-audio', (_event, audioData: number[]) => {
    sendAudio(audioData);
  });

  ipcMain.handle('personaplex:disconnect', () => {
    disconnect();
  });

  ipcMain.handle('personaplex:is-connected', () => {
    return isConnected;
  });

  // PersonaPlex server lifecycle handlers
  ipcMain.handle('personaplex:setup', async (_event, config?: unknown) => {
    const { setup } = await import('./personaplex-server');
    const c = (config || {}) as import('./personaplex-server').PersonaPlexConfig;
    return setup((progress) => {
      const win = getMainWindow();
      if (win && !win.isDestroyed()) {
        win.webContents.send('personaplex:setup-progress', progress);
      }
    }, c);
  });

  ipcMain.handle('personaplex:start-server', async (_event, config?: unknown) => {
    const { start } = await import('./personaplex-server');
    const c = (config || {}) as import('./personaplex-server').PersonaPlexConfig;
    return start(c);
  });

  ipcMain.handle('personaplex:stop-server', async () => {
    const { stop } = await import('./personaplex-server');
    stop();
  });

  ipcMain.handle('personaplex:is-setup-complete', async () => {
    const { isSetupComplete } = await import('./personaplex-server');
    return isSetupComplete();
  });

  ipcMain.handle('personaplex:is-server-running', async () => {
    const { isRunning } = await import('./personaplex-server');
    return isRunning();
  });

  ipcMain.handle('personaplex:has-cuda-gpu', async () => {
    const { hasCudaGpu } = await import('./personaplex-server');
    return hasCudaGpu();
  });

  ipcMain.handle('personaplex:list-voices', async () => {
    const { listVoices } = await import('./personaplex-server');
    return listVoices();
  });

  ipcMain.handle('personaplex:get-wss-url', async () => {
    const { getWssUrl } = await import('./personaplex-server');
    return getWssUrl();
  });
}

/**
 * Clean up on app shutdown.
 */
export function cleanupPersonaPlex(): void {
  disconnect();
}
