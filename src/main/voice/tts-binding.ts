/**
 * tts-binding.ts -- Abstraction layer for TTS native bindings (Kokoro/Piper).
 *
 * This module provides the interface between TTSEngine and the
 * actual TTS binary/addon. In tests, this entire module is
 * mocked via vi.mock(). In production, it will load a native
 * Node addon for Kokoro or Piper TTS.
 *
 * Sprint 4 K.1: "The Mouth" -- TTSEngine
 */

// -- Types --------------------------------------------------------------------

export interface TTSBinding {
  loadModel(path: string, config?: Record<string, unknown>): Promise<void>;
  synthesize(
    text: string,
    options?: { speed?: number; pitch?: number; voiceId?: string },
  ): Promise<Float32Array>;
  synthesizeStream(
    text: string,
    options?: { speed?: number; pitch?: number; voiceId?: string },
  ): AsyncGenerator<Float32Array>;
  freeModel(): void;
  getVersion(): string;
}

// -- Placeholder Implementation -----------------------------------------------
// This will be replaced with actual Kokoro/Piper integration in a future sprint.

export const ttsBinding: TTSBinding = {
  async loadModel(_path: string, _config?: Record<string, unknown>): Promise<void> {
    throw new Error("TTS native binding not installed");
  },

  async synthesize(
    _text: string,
    _options?: { speed?: number; pitch?: number; voiceId?: string },
  ): Promise<Float32Array> {
    throw new Error("TTS native binding not installed");
  },

  async *synthesizeStream(
    _text: string,
    _options?: { speed?: number; pitch?: number; voiceId?: string },
  ): AsyncGenerator<Float32Array> {
    throw new Error("TTS native binding not installed");
  },

  freeModel(): void {
    // no-op until native binding is installed
  },

  getVersion(): string {
    return "0.0.0-placeholder";
  },
};

export default ttsBinding;
