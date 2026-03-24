/**
 * GeminiWsProxy — WebSocket-compatible wrapper that routes through
 * the main process IPC proxy. The API key never reaches the renderer.
 *
 * Implements the subset of the WebSocket interface used by useGeminiLive
 * and its extracted modules (onopen, onmessage, onclose, onerror, send,
 * close, readyState), so it's a drop-in replacement for `new WebSocket(url)`.
 *
 * Security finding: C2 — Proxy Gemini API key through main process.
 */

type MessageHandler = ((this: WebSocket, ev: MessageEvent) => unknown) | null;
type CloseHandler = ((this: WebSocket, ev: CloseEvent) => unknown) | null;
type ErrorHandler = ((this: WebSocket, ev: Event) => unknown) | null;
type OpenHandler = ((this: WebSocket, ev: Event) => unknown) | null;

export class GeminiWsProxy {
  readyState: number = WebSocket.CONNECTING;

  onopen: OpenHandler = null;
  onmessage: MessageHandler = null;
  onclose: CloseHandler = null;
  onerror: ErrorHandler = null;

  private cleanups: Array<() => void> = [];

  constructor() {
    // Subscribe to IPC events BEFORE calling connect()
    this.cleanups.push(
      window.eve.geminiLive.onOpen(() => {
        this.readyState = WebSocket.OPEN;
        if (this.onopen) {
          this.onopen.call(this as unknown as WebSocket, new Event('open'));
        }
      }),
      window.eve.geminiLive.onMessage((rawData: string) => {
        if (this.onmessage) {
          this.onmessage.call(
            this as unknown as WebSocket,
            new MessageEvent('message', { data: rawData })
          );
        }
      }),
      window.eve.geminiLive.onClose((code: number, reason: string) => {
        this.readyState = WebSocket.CLOSED;
        if (this.onclose) {
          this.onclose.call(
            this as unknown as WebSocket,
            new CloseEvent('close', { code, reason, wasClean: code === 1000 })
          );
        }
        // Auto-cleanup IPC listeners once closed
        this._cleanup();
      }),
      window.eve.geminiLive.onError((message: string) => {
        if (this.onerror) {
          this.onerror.call(this as unknown as WebSocket, new Event('error'));
        }
      })
    );

    // Initiate connection via main process
    window.eve.geminiLive.connect()
      .then(() => {
        // Connection successful — onOpen event will fire via IPC
      })
      .catch((err: Error) => {
        this.readyState = WebSocket.CLOSED;
        if (this.onerror) {
          this.onerror.call(this as unknown as WebSocket, new Event('error'));
        }
        // Also fire close for code that waits on it
        if (this.onclose) {
          this.onclose.call(
            this as unknown as WebSocket,
            new CloseEvent('close', {
              code: 1006,
              reason: err.message || 'Connection failed',
              wasClean: false,
            })
          );
        }
        this._cleanup();
      });
  }

  send(data: string | ArrayBuffer | Blob): void {
    if (this.readyState !== WebSocket.OPEN) return;
    // The Gemini protocol only uses JSON strings; binary data should not occur.
    // If it does, silently drop — the main process proxy handles string relay.
    if (typeof data === 'string') {
      window.eve.geminiLive.send(data);
    }
  }

  close(code?: number, reason?: string): void {
    if (this.readyState === WebSocket.CLOSED || this.readyState === WebSocket.CLOSING) return;
    this.readyState = WebSocket.CLOSING;
    window.eve.geminiLive.disconnect(code, reason);
  }

  /** Cleanup IPC listeners when proxy is discarded */
  destroy(): void {
    this._cleanup();
  }

  private _cleanup(): void {
    this.cleanups.forEach((fn) => fn());
    this.cleanups = [];
  }
}
