/**
 * useLocalMicCapture.ts — Renderer-side microphone capture for the local voice pipeline.
 *
 * Listens for voice:start-capture / voice:stop-capture from the main process
 * AudioCapture singleton. When capture starts, opens getUserMedia with 16kHz
 * mono PCM and streams Float32Array chunks back to the main process via IPC
 * for Whisper STT processing.
 *
 * This bridges the gap between:
 *   Main: AudioCapture.startCapture() → sends 'voice:start-capture' to renderer
 *   Renderer: (this hook) → getUserMedia → ScriptProcessor → sends 'voice:audio-chunk' to main
 *   Main: AudioCapture.handleAudioChunk() → VAD → TranscriptionPipeline → Whisper
 */

import { useEffect, useRef } from 'react';

/** Target sample rate for Whisper STT (must match AudioCapture config) */
const TARGET_SAMPLE_RATE = 16_000;

/** ScriptProcessor buffer size — 4096 samples at 16kHz = 256ms chunks */
const BUFFER_SIZE = 4096;

export function useLocalMicCapture(): void {
  const streamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);

  useEffect(() => {
    const startCapture = async () => {
      // Prevent duplicate captures
      if (streamRef.current) return;

      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            sampleRate: TARGET_SAMPLE_RATE,
            channelCount: 1,
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          },
        });

        streamRef.current = stream;

        // Create AudioContext at target sample rate (browser may resample)
        const ctx = new AudioContext({ sampleRate: TARGET_SAMPLE_RATE });
        audioCtxRef.current = ctx;

        const source = ctx.createMediaStreamSource(stream);
        sourceRef.current = source;

        // ScriptProcessorNode captures raw PCM samples.
        // (AudioWorklet is preferred but ScriptProcessor is simpler and
        // sufficient for 16kHz mono — the entire Whisper pipeline runs
        // in the main process anyway, so renderer CPU impact is minimal.)
        const processor = ctx.createScriptProcessor(BUFFER_SIZE, 1, 1);
        processorRef.current = processor;

        processor.onaudioprocess = (e: AudioProcessingEvent) => {
          const inputData = e.inputBuffer.getChannelData(0);
          // Copy the buffer (it's reused by the AudioContext)
          const chunk = new Float32Array(inputData.length);
          chunk.set(inputData);
          window.eve.voice.sendAudioChunk(chunk);
        };

        source.connect(processor);
        processor.connect(ctx.destination); // Required for onaudioprocess to fire

        console.log('[LocalMicCapture] Mic capture started (16kHz mono PCM)');
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        console.error('[LocalMicCapture] Failed to start mic capture:', msg);
        window.eve.voice.sendCaptureError(msg);
      }
    };

    const stopCapture = () => {
      if (processorRef.current) {
        processorRef.current.onaudioprocess = null;
        processorRef.current.disconnect();
        processorRef.current = null;
      }
      if (sourceRef.current) {
        sourceRef.current.disconnect();
        sourceRef.current = null;
      }
      if (audioCtxRef.current) {
        audioCtxRef.current.close().catch(() => {});
        audioCtxRef.current = null;
      }
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((t) => t.stop());
        streamRef.current = null;
      }
      console.log('[LocalMicCapture] Mic capture stopped');
    };

    // Subscribe to main process capture commands
    const cleanupStart = window.eve.voice.onStartCapture(() => {
      startCapture();
    });
    const cleanupStop = window.eve.voice.onStopCapture(() => {
      stopCapture();
    });

    return () => {
      cleanupStart();
      cleanupStop();
      stopCapture();
    };
  }, []);
}
