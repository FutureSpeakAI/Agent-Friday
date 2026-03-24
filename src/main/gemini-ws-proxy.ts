/**
 * Gemini Live WebSocket Proxy — keeps the API key in the main process.
 *
 * The renderer communicates via IPC; this module relays messages to
 * Google's Multimodal Live API WebSocket. The raw API key NEVER
 * reaches the renderer, eliminating exposure via XSS or renderer compromise.
 *
 * Security finding: C2 — Proxy Gemini API key through main process.
 */
import { ipcMain, BrowserWindow } from 'electron';
import { settingsManager } from './settings';

const GEMINI_WS_URL =
  'wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent';

let activeWs: WebSocket | null = null;
let targetWindow: BrowserWindow | null = null;

function getWindow(): BrowserWindow | null {
  if (targetWindow && !targetWindow.isDestroyed()) return targetWindow;
  return BrowserWindow.getAllWindows()[0] ?? null;
}

function sendToRenderer(channel: string, ...args: unknown[]): void {
  const win = getWindow();
  if (win && !win.isDestroyed()) {
    win.webContents.send(channel, ...args);
  }
}

export function registerGeminiLiveProxy(): void {
  ipcMain.handle('gemini-live:connect', async (event) => {
    // Resolve the originating window for renderer-bound messages
    const senderWindow = BrowserWindow.fromWebContents(event.sender);
    if (senderWindow) targetWindow = senderWindow;

    // Close existing connection if any
    if (activeWs) {
      try { activeWs.close(); } catch { /* ignore */ }
      activeWs = null;
    }

    const apiKey = settingsManager.getGeminiApiKey();
    if (!apiKey) {
      throw new Error('No Gemini API key configured');
    }

    return new Promise<void>((resolve, reject) => {
      const url = `${GEMINI_WS_URL}?key=${apiKey}`;
      // Use native Node.js WebSocket (available in Node 22+)
      const ws = new globalThis.WebSocket(url);
      activeWs = ws;

      const timeout = setTimeout(() => {
        ws.close();
        reject(new Error('Gemini WebSocket connection timed out'));
      }, 15000);

      ws.addEventListener('open', () => {
        clearTimeout(timeout);
        sendToRenderer('gemini-live:open');
        resolve();
      });

      ws.addEventListener('message', (event: MessageEvent) => {
        // Relay message data to renderer as a string
        const data = event.data;
        if (typeof data === 'string') {
          sendToRenderer('gemini-live:message', data);
        } else if (data instanceof Blob) {
          // Convert Blob to text before relaying
          data.text().then((text: string) => {
            sendToRenderer('gemini-live:message', text);
          }).catch(() => {
            // If blob conversion fails, skip this message
          });
        } else if (data instanceof ArrayBuffer) {
          const decoder = new TextDecoder();
          sendToRenderer('gemini-live:message', decoder.decode(data));
        } else {
          sendToRenderer('gemini-live:message', String(data));
        }
      });

      ws.addEventListener('close', (event: CloseEvent) => {
        clearTimeout(timeout);
        activeWs = null;
        sendToRenderer('gemini-live:close', event.code, event.reason || '');
      });

      ws.addEventListener('error', () => {
        sendToRenderer('gemini-live:error', 'WebSocket connection error');
        if (ws.readyState === WebSocket.CONNECTING) {
          clearTimeout(timeout);
          reject(new Error('Gemini WebSocket connection failed'));
        }
      });
    });
  });

  ipcMain.on('gemini-live:send', (_event, data: string) => {
    if (activeWs && activeWs.readyState === WebSocket.OPEN) {
      activeWs.send(data);
    }
  });

  ipcMain.handle('gemini-live:disconnect', async (_event, code?: number, reason?: string) => {
    if (activeWs) {
      activeWs.close(code ?? 1000, reason ?? '');
      activeWs = null;
    }
  });

  ipcMain.handle('gemini-live:is-connected', () => {
    return activeWs !== null && activeWs.readyState === WebSocket.OPEN;
  });
}
