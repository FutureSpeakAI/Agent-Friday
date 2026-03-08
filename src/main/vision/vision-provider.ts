/**
 * vision-provider.ts -- Local vision-language model via Ollama.
 *
 * The Gaze. Takes image input (Buffer, file path, or base64 string) and
 * produces natural language descriptions or answers visual questions using
 * a vision-language model (default: moondream) running locally via Ollama.
 *
 * Manages model loading, readiness checks, VRAM tracking, and graceful
 * error handling for invalid images or missing models.
 *
 * Sprint 5 M.1: "The Gaze" -- VisionProvider
 */

import { readFile } from 'node:fs/promises';

// -- Constants ----------------------------------------------------------------

/** Default Ollama endpoint */
const DEFAULT_OLLAMA_ENDPOINT = 'http://localhost:11434';

/** Default vision model */
const DEFAULT_MODEL = 'moondream:latest';

/** Default VRAM estimate in MB for moondream Q4 */
const DEFAULT_VRAM_ESTIMATE_MB = 1200;

/** Default prompt for image description */
const DESCRIBE_PROMPT = 'Describe this image in detail.';

// -- Types --------------------------------------------------------------------

/** Image input: raw Buffer, file path string, or base64 string */
export type ImageInput = Buffer | string;

/** Model information including VRAM usage */
export interface VisionModelInfo {
  name: string;
  vramUsageMB: number;
  loaded: boolean;
}

/** Ollama /api/generate response */
interface OllamaGenerateResponse {
  model: string;
  response: string;
  done: boolean;
  error?: string;
}

/** Ollama /api/show response */
interface OllamaShowResponse {
  name: string;
  model_info: Record<string, unknown>;
  size: number;
  error?: string;
}

/** Ollama /api/ps response */
interface OllamaPsModel {
  name: string;
  size: number;
  size_vram: number;
}

interface OllamaPsResponse {
  models: OllamaPsModel[];
}

// -- VisionProvider -----------------------------------------------------------

export class VisionProvider {
  private static instance: VisionProvider | null = null;

  private endpoint: string;
  private modelName: string;
  private modelLoaded: boolean;
  private vramUsageMB: number;

  private constructor(endpoint?: string) {
    this.endpoint = endpoint ?? DEFAULT_OLLAMA_ENDPOINT;
    this.modelName = '';
    this.modelLoaded = false;
    this.vramUsageMB = 0;
  }

  static getInstance(endpoint?: string): VisionProvider {
    if (!VisionProvider.instance) {
      VisionProvider.instance = new VisionProvider(endpoint);
    }
    return VisionProvider.instance;
  }

  static resetInstance(): void {
    if (VisionProvider.instance) {
      VisionProvider.instance.unloadModel();
    }
    VisionProvider.instance = null;
  }

  // -- Public API -------------------------------------------------------------

  /**
   * Load a vision model via Ollama.
   * Checks /api/show to verify model exists, then queries /api/ps for VRAM info.
   */
  async loadModel(name?: string): Promise<void> {
    const modelName = name ?? DEFAULT_MODEL;

    // Check if model exists via /api/show
    const showUrl = this.endpoint + '/api/show';
    const showResponse = await fetch(showUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: modelName }),
    });

    if (!showResponse.ok) {
      const errorData = (await showResponse.json()) as { error?: string };
      throw new Error(
        'Vision model not found: ' + modelName + '. ' + (errorData.error ?? 'Model not available in Ollama'),
      );
    }

    this.modelName = modelName;
    this.modelLoaded = true;

    // Query VRAM usage from /api/ps
    await this.updateVramUsage();
  }

  /**
   * Unload the model, freeing VRAM. isReady() becomes false.
   */
  unloadModel(): void {
    this.modelLoaded = false;
    this.vramUsageMB = 0;
  }

  /**
   * Generate a natural language description of an image.
   */
  async describe(image: ImageInput): Promise<string> {
    return this.generate(image, DESCRIBE_PROMPT);
  }

  /**
   * Answer a visual question about an image.
   */
  async answer(image: ImageInput, question: string): Promise<string> {
    return this.generate(image, question);
  }

  /**
   * Whether a vision model is loaded and ready.
   */
  isReady(): boolean {
    return this.modelLoaded;
  }

  /**
   * Return model name, VRAM usage, and loaded state.
   */
  getModelInfo(): VisionModelInfo {
    return {
      name: this.modelName,
      vramUsageMB: this.vramUsageMB,
      loaded: this.modelLoaded,
    };
  }

  // -- Private ----------------------------------------------------------------

  /**
   * Core method: send image + prompt to Ollama /api/generate.
   */
  private async generate(image: ImageInput, prompt: string): Promise<string> {
    if (!this.modelLoaded) {
      throw new Error('Vision model not loaded. Call loadModel() first.');
    }

    const base64Image = await this.resolveImage(image);

    const generateUrl = this.endpoint + '/api/generate';
    const response = await fetch(generateUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: this.modelName,
        prompt,
        images: [base64Image],
        stream: false,
      }),
    });

    if (!response.ok) {
      const errorData = (await response.json()) as { error?: string };
      throw new Error(
        'Ollama vision error: ' + (errorData.error ?? 'Unknown error (HTTP ' + response.status + ')'),
      );
    }

    const data = (await response.json()) as OllamaGenerateResponse;
    return data.response;
  }

  /**
   * Convert ImageInput to base64 string for Ollama API.
   * - Buffer: convert to base64
   * - String starting with / or drive letter or containing \: treat as file path
   * - Other string: treat as base64
   */
  private async resolveImage(image: ImageInput): Promise<string> {
    if (Buffer.isBuffer(image)) {
      return image.toString('base64');
    }

    // String input: determine if file path or base64
    if (this.isFilePath(image)) {
      const fileBuffer = await readFile(image);
      return fileBuffer.toString('base64');
    }

    // Assume base64 string
    return image;
  }

  /**
   * Heuristic to determine if a string is a file path.
   * Paths start with /, or contain \, or start with a drive letter (e.g. C:).
   */
  private isFilePath(input: string): boolean {
    // Unix absolute path
    if (input.startsWith('/')) return true;
    // Windows path with backslash
    if (input.includes('\\')) return true;
    // Windows drive letter (e.g., C:)
    if (/^[A-Za-z]:/.test(input)) return true;
    return false;
  }

  /**
   * Query /api/ps to get VRAM usage for the loaded model.
   * Falls back to default estimate if model not found in running list.
   */
  private async updateVramUsage(): Promise<void> {
    try {
      const psUrl = this.endpoint + '/api/ps';
      const response = await fetch(psUrl);

      if (response.ok) {
        const data = (await response.json()) as OllamaPsResponse;
        const model = data.models?.find((m) => m.name === this.modelName);

        if (model && model.size_vram > 0) {
          this.vramUsageMB = Math.round(model.size_vram / (1024 * 1024));
          return;
        }
      }
    } catch {
      // Fall through to default estimate
    }

    // Default estimate for moondream Q4
    this.vramUsageMB = DEFAULT_VRAM_ESTIMATE_MB;
  }
}

export const visionProvider = VisionProvider.getInstance();
