/**
 * Sprint 7: IPC handlers for the complete voice pipeline.
 *
 * Exposes WhisperProvider, AudioCapture, TranscriptionPipeline, TTSEngine,
 * VoiceProfileManager, and SpeechSynthesisManager to the renderer via
 * eve.voice namespace.
 *
 * Audio data (Float32Array) is serialized as regular arrays over IPC —
 * the renderer must convert back if needed.
 */

import { ipcMain, type BrowserWindow } from 'electron';
import { WhisperProvider } from '../voice/whisper-provider';
import { AudioCapture } from '../voice/audio-capture';
import { TranscriptionPipeline } from '../voice/transcription-pipeline';
import { TTSEngine } from '../voice/tts-engine';
import { VoiceProfileManager } from '../voice/voice-profile-manager';
import { SpeechSynthesisManager } from '../voice/speech-synthesis';
import { assertString, assertObject, assertNumber } from './validate';
import type { WhisperModelSize } from '../voice/whisper-provider';
import type { TTSBackend } from '../voice/tts-engine';

export interface VoicePipelineHandlerDeps {
  getMainWindow: () => BrowserWindow | null;
}

export function registerVoicePipelineHandlers(deps: VoicePipelineHandlerDeps): void {
  const whisper = WhisperProvider.getInstance();
  const capture = AudioCapture.getInstance();
  const pipeline = TranscriptionPipeline.getInstance();
  const tts = TTSEngine.getInstance();
  const voiceProfiles = VoiceProfileManager.getInstance();
  const synthesis = SpeechSynthesisManager.getInstance();

  // ── Whisper Provider (STT) ────────────────────────────────────────

  ipcMain.handle('voice:whisper:load-model', async (_event, size?: unknown) => {
    if (size !== undefined && size !== null) {
      assertString(size as unknown, 'voice:whisper:load-model size', 50);
    }
    return whisper.loadModel(size as WhisperModelSize | undefined);
  });

  ipcMain.handle('voice:whisper:unload-model', () => {
    whisper.unloadModel();
  });

  ipcMain.handle('voice:whisper:is-ready', () => {
    return whisper.isReady();
  });

  ipcMain.handle('voice:whisper:transcribe', async (_event, audioArray: unknown) => {
    if (!Array.isArray(audioArray) && !(audioArray instanceof Float32Array)) {
      throw new Error('voice:whisper:transcribe audio must be an array or Float32Array');
    }
    const audio = audioArray instanceof Float32Array
      ? audioArray
      : new Float32Array(audioArray as number[]);
    return whisper.transcribe(audio);
  });

  ipcMain.handle('voice:whisper:get-available-models', async () => {
    return whisper.getAvailableModels();
  });

  ipcMain.handle('voice:whisper:is-model-downloaded', async (_event, size?: unknown) => {
    if (size !== undefined && size !== null) {
      assertString(size as unknown, 'voice:whisper:is-model-downloaded size', 50);
    }
    return whisper.isModelDownloaded((size as any) || 'tiny');
  });

  ipcMain.handle('voice:whisper:download-model', async (event, size?: unknown) => {
    if (size !== undefined && size !== null) {
      assertString(size as unknown, 'voice:whisper:download-model size', 50);
    }
    const win = deps?.getMainWindow?.();
    return whisper.downloadModel(
      (size as any) || 'tiny',
      (downloaded: number, total: number) => {
        // Forward progress to renderer
        if (win && !win.isDestroyed()) {
          win.webContents.send('voice:whisper:download-progress', { downloaded, total });
        }
      },
    );
  });

  // ── Audio Capture (microphone) ────────────────────────────────────

  ipcMain.handle('voice:capture:start', async () => {
    return capture.startCapture();
  });

  ipcMain.handle('voice:capture:stop', () => {
    capture.stopCapture();
  });

  ipcMain.handle('voice:capture:is-capturing', () => {
    return capture.isCapturing();
  });

  ipcMain.handle('voice:capture:get-audio-level', () => {
    return capture.getAudioLevel();
  });

  // ── Transcription Pipeline (STT orchestration) ────────────────────

  ipcMain.handle('voice:pipeline:start', async () => {
    return pipeline.start();
  });

  ipcMain.handle('voice:pipeline:stop', () => {
    pipeline.stop();
  });

  ipcMain.handle('voice:pipeline:is-listening', () => {
    return pipeline.isListening();
  });

  ipcMain.handle('voice:pipeline:get-stats', () => {
    return pipeline.getStats();
  });

  // ── TTS Engine ────────────────────────────────────────────────────

  ipcMain.handle('voice:tts:load-engine', async (_event, backend?: unknown) => {
    if (backend !== undefined && backend !== null) {
      assertString(backend as unknown, 'voice:tts:load-engine backend', 50);
    }
    return tts.loadEngine(backend as TTSBackend | undefined);
  });

  ipcMain.handle('voice:tts:unload-engine', () => {
    tts.unloadEngine();
  });

  ipcMain.handle('voice:tts:is-ready', () => {
    return tts.isReady();
  });

  ipcMain.handle('voice:tts:synthesize', async (_event, text: unknown, opts?: unknown) => {
    assertString(text, 'voice:tts:synthesize text', 10_000);
    if (opts !== undefined && opts !== null) {
      assertObject(opts as unknown, 'voice:tts:synthesize opts');
    }
    const audio = await tts.synthesize(text as string, opts as any);
    // Convert Float32Array to regular array for IPC serialization
    return Array.from(audio);
  });

  ipcMain.handle('voice:tts:get-available-voices', () => {
    return tts.getAvailableVoices();
  });

  ipcMain.handle('voice:tts:get-info', () => {
    return tts.getInfo();
  });

  // ── Voice Profile Manager ─────────────────────────────────────────

  ipcMain.handle('voice:profiles:get-active', () => {
    return voiceProfiles.getActiveProfile();
  });

  ipcMain.handle('voice:profiles:set-active', (_event, id: unknown) => {
    assertString(id, 'voice:profiles:set-active id', 100);
    voiceProfiles.setActiveProfile(id as string);
  });

  ipcMain.handle('voice:profiles:list', () => {
    return voiceProfiles.listProfiles();
  });

  ipcMain.handle('voice:profiles:create', (_event, opts: unknown) => {
    assertObject(opts, 'voice:profiles:create opts');
    const o = opts as Record<string, unknown>;
    assertString(o.name, 'voice:profiles:create opts.name', 200);
    assertString(o.voiceId, 'voice:profiles:create opts.voiceId', 200);
    return voiceProfiles.createProfile(opts as any);
  });

  ipcMain.handle('voice:profiles:delete', (_event, id: unknown) => {
    assertString(id, 'voice:profiles:delete id', 100);
    return voiceProfiles.deleteProfile(id as string);
  });

  ipcMain.handle('voice:profiles:preview', async (_event, profileId: unknown) => {
    assertString(profileId, 'voice:profiles:preview profileId', 100);
    const audio = await voiceProfiles.previewVoice(profileId as string);
    return Array.from(audio);
  });

  // ── Speech Synthesis Manager (playback orchestration) ─────────────

  ipcMain.handle('voice:speech:speak', async (_event, text: unknown, opts?: unknown) => {
    assertString(text, 'voice:speech:speak text', 10_000);
    if (opts !== undefined && opts !== null) {
      assertObject(opts as unknown, 'voice:speech:speak opts');
    }
    return synthesis.speak(text as string, opts as any);
  });

  ipcMain.handle('voice:speech:speak-immediate', async (_event, text: unknown) => {
    assertString(text, 'voice:speech:speak-immediate text', 10_000);
    return synthesis.speakImmediate(text as string);
  });

  ipcMain.handle('voice:speech:stop', () => {
    synthesis.stop();
  });

  ipcMain.handle('voice:speech:pause', () => {
    synthesis.pause();
  });

  ipcMain.handle('voice:speech:resume', () => {
    synthesis.resume();
  });

  ipcMain.handle('voice:speech:is-speaking', () => {
    return synthesis.isSpeaking();
  });

  ipcMain.handle('voice:speech:get-queue-length', () => {
    return synthesis.getQueueLength();
  });

  // ── Binary Management ─────────────────────────────────────────────

  ipcMain.handle('voice:ensure-whisper-binary', async () => {
    try {
      const { ensureWhisperBinary } = await import('../voice/binary-downloader');
      return ensureWhisperBinary((downloaded, total) => {
        deps.getMainWindow()?.webContents.send('voice:binary-download-progress', {
          binary: 'whisper',
          downloaded,
          total,
        });
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      throw new Error(`Whisper binary setup failed: ${msg}`);
    }
  });

  ipcMain.handle('voice:ensure-tts-binary', async () => {
    try {
      const { ensureTTSBinary } = await import('../voice/binary-downloader');
      return ensureTTSBinary((downloaded, total) => {
        deps.getMainWindow()?.webContents.send('voice:binary-download-progress', {
          binary: 'tts',
          downloaded,
          total,
        });
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      throw new Error(`TTS binary setup failed: ${msg}`);
    }
  });

  ipcMain.handle('voice:ensure-tts-model', async () => {
    try {
      const { ensureTTSModel } = await import('../voice/binary-downloader');
      return ensureTTSModel((downloaded, total) => {
        deps.getMainWindow()?.webContents.send('voice:binary-download-progress', {
          binary: 'tts-model',
          downloaded,
          total,
        });
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      throw new Error(`TTS model setup failed: ${msg}`);
    }
  });

  // ── Chatterbox Turbo TTS (managed Python sidecar) ─────────────────

  ipcMain.handle('voice:chatterbox:is-setup-complete', async () => {
    try {
      const { isSetupComplete } = await import('../voice/chatterbox-server');
      return isSetupComplete();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      throw new Error(`Chatterbox status check failed: ${msg}`);
    }
  });

  ipcMain.handle('voice:chatterbox:is-python-available', async () => {
    try {
      const { isPythonAvailable } = await import('../voice/chatterbox-server');
      return isPythonAvailable();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      throw new Error(`Python availability check failed: ${msg}`);
    }
  });

  ipcMain.handle('voice:chatterbox:has-cuda-gpu', async () => {
    try {
      const { hasCudaGpu } = await import('../voice/chatterbox-server');
      return hasCudaGpu();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      throw new Error(`CUDA GPU check failed: ${msg}`);
    }
  });

  ipcMain.handle('voice:chatterbox:setup', async () => {
    try {
      const { setup } = await import('../voice/chatterbox-server');
      return await setup((progress) => {
        deps.getMainWindow()?.webContents.send('voice:chatterbox:setup-progress', progress);
      });
    } catch (err) {
      // Re-throw with the actual message so the renderer sees it
      // (Electron IPC strips error details by default)
      const msg = err instanceof Error ? err.message : String(err);
      throw new Error(msg);
    }
  });

  ipcMain.handle('voice:chatterbox:is-running', async () => {
    try {
      const { isRunning } = await import('../voice/chatterbox-server');
      return isRunning();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      throw new Error(`Chatterbox running check failed: ${msg}`);
    }
  });

  // ── Event forwarding to renderer ──────────────────────────────────
  // Clean up any previously registered forwarding listeners to prevent
  // listener accumulation on dev hot-reload or window recreate.
  // Each .on() returns a cleanup function.

  for (const cleanup of voiceEventCleanups) cleanup();
  voiceEventCleanups.length = 0;

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const fwd = (emitter: any, eventName: string, channel: string) => {
    voiceEventCleanups.push(
      emitter.on(eventName, (...args: unknown[]) => {
        deps.getMainWindow()?.webContents.send(channel, ...args);
      })
    );
  };

  // Audio capture events
  fwd(capture, 'voice-start', 'voice:event:voice-start');
  fwd(capture, 'voice-end', 'voice:event:voice-end');
  fwd(capture, 'audio-chunk', 'voice:event:audio-chunk');
  fwd(capture, 'error', 'voice:event:capture-error');

  // Transcription pipeline events
  fwd(pipeline, 'transcript', 'voice:event:transcript');
  fwd(pipeline, 'partial', 'voice:event:partial');
  fwd(pipeline, 'error', 'voice:event:pipeline-error');

  // Speech synthesis events
  fwd(synthesis, 'utterance-start', 'voice:event:utterance-start');
  fwd(synthesis, 'utterance-end', 'voice:event:utterance-end');
  fwd(synthesis, 'queue-empty', 'voice:event:queue-empty');
  fwd(synthesis, 'interrupted', 'voice:event:interrupted');
}

/** Stored cleanup functions from .on() — emptied and re-populated on each registration. */
const voiceEventCleanups: Array<() => void> = [];
