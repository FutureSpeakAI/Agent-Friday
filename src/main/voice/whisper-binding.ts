/**
 * whisper-binding.ts -- Abstraction layer for whisper.cpp native bindings.
 *
 * This module provides the interface between WhisperProvider and the
 * actual whisper.cpp binary/addon. In tests, this entire module is
 * mocked via vi.mock(). In production, it will spawn whisper-cpp as
 * a subprocess or load a native Node addon.
 *
 * Sprint 4 J.1: "The Ear" -- WhisperProvider
 */

// -- Types --------------------------------------------------------------------

export interface WhisperModelHandle {
  handle: string;
}

export interface WhisperTranscribeOptions {
  sampleRate: number;
  language?: string;
}

export interface WhisperRawResult {
  text: string;
  language: string;
  segments: Array<{
    text: string;
    start: number;
    end: number;
  }>;
}

// -- Binding Interface -------------------------------------------------------

export interface WhisperBinding {
  loadModel(path: string): Promise<WhisperModelHandle>;
  transcribe(
    audio: Float32Array,
    options: WhisperTranscribeOptions,
  ): Promise<WhisperRawResult>;
  freeModel(handle: WhisperModelHandle): void;
}

// -- Placeholder Implementation -----------------------------------------------
// This will be replaced with actual whisper.cpp integration in a future sprint.

export const whisperBinding: WhisperBinding = {
  async loadModel(_path: string): Promise<WhisperModelHandle> {
    throw new Error('whisper.cpp native binding not installed');
  },

  async transcribe(
    _audio: Float32Array,
    _options: WhisperTranscribeOptions,
  ): Promise<WhisperRawResult> {
    throw new Error('whisper.cpp native binding not installed');
  },

  freeModel(_handle: WhisperModelHandle): void {
    // no-op until native binding is installed
  },
};

export default whisperBinding;
