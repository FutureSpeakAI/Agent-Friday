/**
 * screen-context.ts -- Screenshot capture and UI analysis for Agent Friday.
 *
 * The Glance. Captures screenshots of the display, specific windows, or
 * screen regions using Electron's desktopCapturer. Passes captured images
 * to VisionProvider for natural language descriptions. Caches the latest
 * description and emits events when the screen context changes.
 *
 * Supports auto-capture at configurable intervals for continuous
 * environmental awareness.
 *
 * Sprint 5 M.2: "The Glance" -- ScreenContext
 */

import { desktopCapturer, screen } from 'electron';
import { visionProvider } from './vision-provider';

// -- Types --------------------------------------------------------------------

export interface Rectangle {
  x: number;
  y: number;
  width: number;
  height: number;
}

type ScreenContextEvent = 'context-update';
type EventCallback = (payload?: unknown) => void;

// -- Constants ----------------------------------------------------------------

/** Default thumbnail size: cap longest edge at 768px for VLM efficiency */
const THUMBNAIL_SIZE = { width: 768, height: 768 };

/** Default auto-capture interval in ms */
const DEFAULT_AUTO_CAPTURE_MS = 30_000;

// -- ScreenContext ------------------------------------------------------------

export class ScreenContext {
  private static instance: ScreenContext | null = null;

  private lastContext: string | null = null;
  private autoTimer: ReturnType<typeof setInterval> | null = null;
  private listeners = new Map<ScreenContextEvent, Set<EventCallback>>();

  private constructor() {}

  static getInstance(): ScreenContext {
    if (!ScreenContext.instance) {
      ScreenContext.instance = new ScreenContext();
    }
    return ScreenContext.instance;
  }

  static resetInstance(): void {
    if (ScreenContext.instance) {
      ScreenContext.instance.stopAutoCapture();
      ScreenContext.instance.listeners.clear();
    }
    ScreenContext.instance = null;
  }

  // -- Public API -------------------------------------------------------------

  /**
   * Capture a full screenshot of the primary display.
   * Returns a PNG buffer, or null on failure.
   */
  async captureScreen(): Promise<Buffer | null> {
    try {
      const sources = await desktopCapturer.getSources({
        types: ['screen'],
        thumbnailSize: THUMBNAIL_SIZE,
      });

      if (!sources || sources.length === 0) return null;

      // Find primary display source
      const primaryDisplay = screen.getPrimaryDisplay();
      const primaryId = String(primaryDisplay.id);
      const source = sources.find((s) => s.display_id === primaryId) ?? sources[0];

      const image = source.thumbnail;
      if (!image || image.isEmpty()) return null;

      const buffer = image.toPNG();
      await this.describeAndCache(buffer);
      return buffer;
    } catch {
      return null;
    }
  }

  /**
   * Capture a specific window by window id.
   * If no windowId provided, returns the first available window.
   */
  async captureWindow(windowId?: number): Promise<Buffer | null> {
    try {
      const sources = await desktopCapturer.getSources({
        types: ['window'],
        thumbnailSize: THUMBNAIL_SIZE,
      });

      if (!sources || sources.length === 0) return null;

      let source = sources[0];
      if (windowId !== undefined) {
        const match = sources.find((s) => s.id.includes(':' + String(windowId) + ':'));
        if (match) source = match;
      }

      const image = source.thumbnail;
      if (!image || image.isEmpty()) return null;

      const buffer = image.toPNG();
      await this.describeAndCache(buffer);
      return buffer;
    } catch {
      return null;
    }
  }

  /**
   * Capture a rectangular region of the screen.
   * Captures full screen first, then crops to the given rectangle.
   */
  async captureRegion(rect: Rectangle): Promise<Buffer | null> {
    try {
      const sources = await desktopCapturer.getSources({
        types: ['screen'],
        thumbnailSize: THUMBNAIL_SIZE,
      });

      if (!sources || sources.length === 0) return null;

      const source = sources[0];
      const image = source.thumbnail;
      if (!image || image.isEmpty()) return null;

      const cropped = image.crop(rect);
      const buffer = cropped.toPNG();
      await this.describeAndCache(buffer);
      return buffer;
    } catch {
      return null;
    }
  }

  /**
   * Return the latest cached screen description, or null if none.
   * Does not trigger a new capture or vision call.
   */
  getContext(): string | null {
    return this.lastContext;
  }

  /**
   * Start periodic auto-capture at the given interval.
   * Each tick captures the screen and describes it.
   */
  startAutoCapture(ms: number = DEFAULT_AUTO_CAPTURE_MS): void {
    this.stopAutoCapture();
    this.autoTimer = setInterval(() => {
      void this.captureScreen();
    }, ms);
  }

  /**
   * Stop periodic auto-capture.
   */
  stopAutoCapture(): void {
    if (this.autoTimer !== null) {
      clearInterval(this.autoTimer);
      this.autoTimer = null;
    }
  }

  /**
   * Subscribe to events. Returns an unsubscribe function.
   */
  on(event: ScreenContextEvent, callback: EventCallback): () => void {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, new Set());
    }
    this.listeners.get(event)!.add(callback);

    return () => {
      this.listeners.get(event)?.delete(callback);
    };
  }

  // -- Private ----------------------------------------------------------------

  /**
   * Send captured buffer to VisionProvider for description.
   * Cache result and emit context-update if description changed.
   */
  private async describeAndCache(buffer: Buffer): Promise<void> {
    if (!visionProvider.isReady()) return;

    try {
      const description = await visionProvider.describe(buffer);
      if (description && description !== this.lastContext) {
        this.lastContext = description;
        this.emit('context-update', description);
      } else if (description) {
        this.lastContext = description;
      }
    } catch {
      // Vision describe failed silently; keep previous context
    }
  }

  /**
   * Emit an event to all subscribed listeners.
   */
  private emit(event: ScreenContextEvent, payload?: unknown): void {
    const callbacks = this.listeners.get(event);
    if (callbacks) {
      for (const cb of callbacks) {
        cb(payload);
      }
    }
  }
}

export const screenContext = ScreenContext.getInstance();
