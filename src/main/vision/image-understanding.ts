/**
 * image-understanding.ts -- User image input processing for Agent Friday.
 *
 * The Focus. Enables users to provide images via clipboard paste, drag-drop,
 * or native file picker. Validates image format (PNG, JPEG, WebP, GIF) and
 * size (max 10MB), then delegates to VisionProvider for natural language
 * description or visual question answering.
 *
 * Caches the last result and emits 'image-result' events for downstream
 * consumers (chat UI, context engine, etc.).
 *
 * IPC Channels: vision:process-clipboard, vision:file-dropped, vision:select-file
 *
 * Sprint 5 M.3: "The Focus" -- ImageUnderstanding
 */

import { clipboard, dialog } from 'electron';
import { readFile, stat } from 'node:fs/promises';
import { VisionProvider } from './vision-provider';

// -- Constants ----------------------------------------------------------------

/** Maximum image size in bytes (10MB) */
const MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024;

/** Supported image file extensions */
const SUPPORTED_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'webp', 'gif']);

/** File picker filter for images */
const IMAGE_FILE_FILTERS = [
  { name: 'Images', extensions: ['png', 'jpg', 'jpeg', 'webp', 'gif'] },
];

// -- Types --------------------------------------------------------------------

export interface ImageResult {
  description: string;
  source: 'clipboard' | 'file' | 'drop' | 'screen' | 'buffer';
  timestamp: number;
  imageSizeBytes: number;
}

type ImageEvent = 'image-result';
type EventCallback = (...args: unknown[]) => void;

// -- ImageUnderstanding -------------------------------------------------------

export class ImageUnderstanding {
  private static instance: ImageUnderstanding | null = null;

  private lastResult: ImageResult | null = null;
  private listeners = new Map<ImageEvent, Set<EventCallback>>();

  private constructor() {}

  static getInstance(): ImageUnderstanding {
    if (!ImageUnderstanding.instance) {
      ImageUnderstanding.instance = new ImageUnderstanding();
    }
    return ImageUnderstanding.instance;
  }

  static resetInstance(): void {
    if (ImageUnderstanding.instance) {
      ImageUnderstanding.instance.listeners.clear();
      ImageUnderstanding.instance.lastResult = null;
    }
    ImageUnderstanding.instance = null;
  }

  // -- Public API -------------------------------------------------------------

  /**
   * Analyze an image via VisionProvider.
   * Accepts a Buffer (raw image bytes) or a file path string.
   * If question is provided, uses visual QA; otherwise generates a description.
   */
  async processImage(source: Buffer | string, question?: string): Promise<ImageResult> {
    const provider = VisionProvider.getInstance();
    let imageBuffer: Buffer;
    let sourceType: ImageResult['source'];

    if (Buffer.isBuffer(source)) {
      imageBuffer = source;
      sourceType = 'buffer';
    } else {
      // String input: treat as file path
      this.validateExtension(source);
      const fileStats = await stat(source);
      if (fileStats.size > MAX_IMAGE_SIZE_BYTES) {
        throw new Error(
          'Image too large: ' + fileStats.size + ' bytes exceeds 10 MB limit.',
        );
      }
      imageBuffer = await readFile(source);
      sourceType = 'file';
    }

    // Validate size for Buffer input
    if (imageBuffer.byteLength > MAX_IMAGE_SIZE_BYTES) {
      throw new Error(
        'Image too large: ' + imageBuffer.byteLength + ' bytes exceeds 10 MB limit.',
      );
    }

    const description = question
      ? await provider.answer(imageBuffer, question)
      : await provider.describe(imageBuffer);

    const result: ImageResult = {
      description,
      source: sourceType,
      timestamp: Date.now(),
      imageSizeBytes: imageBuffer.byteLength,
    };

    this.lastResult = result;
    this.emit('image-result', result);
    return result;
  }

  /**
   * Read PNG/JPEG from the system clipboard and process it.
   * Returns null if clipboard contains no image.
   */
  async processClipboardImage(): Promise<ImageResult | null> {
    const nativeImage = clipboard.readImage();
    if (!nativeImage || nativeImage.isEmpty()) return null;

    const buffer = nativeImage.toPNG();
    if (!buffer || buffer.byteLength === 0) return null;

    const provider = VisionProvider.getInstance();
    const description = await provider.describe(buffer);

    const result: ImageResult = {
      description,
      source: 'clipboard',
      timestamp: Date.now(),
      imageSizeBytes: buffer.byteLength,
    };

    this.lastResult = result;
    this.emit('image-result', result);
    return result;
  }

  /**
   * Filter file paths for image files and process the first valid one.
   * Returns null if no valid image files found.
   */
  async handleDrop(files: string[]): Promise<ImageResult | null> {
    const imageFile = files.find((f) => this.isImageFile(f));
    if (!imageFile) return null;

    const fileStats = await stat(imageFile);
    if (fileStats.size > MAX_IMAGE_SIZE_BYTES) {
      throw new Error(
        'Image too large: ' + fileStats.size + ' bytes exceeds 10 MB limit.',
      );
    }

    const imageBuffer = await readFile(imageFile);
    const provider = VisionProvider.getInstance();
    const description = await provider.describe(imageBuffer);

    const result: ImageResult = {
      description,
      source: 'drop',
      timestamp: Date.now(),
      imageSizeBytes: imageBuffer.byteLength,
    };

    this.lastResult = result;
    this.emit('image-result', result);
    return result;
  }

  /**
   * Open native file picker filtered to image files.
   * Returns null if the user cancels the dialog.
   */
  async handleFileSelect(): Promise<ImageResult | null> {
    const dialogResult = await dialog.showOpenDialog({
      properties: ['openFile'],
      filters: IMAGE_FILE_FILTERS,
    });

    if (dialogResult.canceled || dialogResult.filePaths.length === 0) {
      return null;
    }

    return this.processImage(dialogResult.filePaths[0]);
  }

  /**
   * Return the cached last ImageResult, or null if none.
   */
  getLastResult(): ImageResult | null {
    return this.lastResult;
  }

  /**
   * Subscribe to events. Returns an unsubscribe function.
   */
  on(event: string, cb: (...args: unknown[]) => void): () => void {
    const key = event as ImageEvent;
    if (!this.listeners.has(key)) {
      this.listeners.set(key, new Set());
    }
    this.listeners.get(key)!.add(cb);

    return () => {
      this.listeners.get(key)?.delete(cb);
    };
  }

  // -- Private ----------------------------------------------------------------

  /**
   * Check if a file path has a supported image extension.
   */
  private isImageFile(filePath: string): boolean {
    const ext = filePath.split('.').pop()?.toLowerCase() ?? '';
    return SUPPORTED_EXTENSIONS.has(ext);
  }

  /**
   * Validate that a file path has a supported image extension.
   * Throws if the extension is not supported.
   */
  private validateExtension(filePath: string): void {
    if (!this.isImageFile(filePath)) {
      const ext = filePath.split('.').pop()?.toLowerCase() ?? '(none)';
      throw new Error(
        'Unsupported image format: .' + ext + '. Supported: PNG, JPEG, WebP, GIF.',
      );
    }
  }

  /**
   * Emit an event to all subscribed listeners.
   */
  private emit(event: ImageEvent, ...args: unknown[]): void {
    const callbacks = this.listeners.get(event);
    if (callbacks) {
      for (const cb of callbacks) {
        cb(...args);
      }
    }
  }
}

export const imageUnderstanding = ImageUnderstanding.getInstance();
