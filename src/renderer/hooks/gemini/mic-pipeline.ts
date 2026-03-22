/**
 * Mic capture pipeline — startListening() and stopListening() logic.
 *
 * Handles microphone acquisition, AudioWorklet/ScriptProcessor setup,
 * noise gate filtering, screen capture forwarding, and webcam/call cleanup.
 */

import type { GeminiRefs, GeminiLiveState } from './types';
import { float32ToInt16, arrayBufferToBase64 } from './audio-helpers';

/**
 * Start mic capture + screen sharing.
 * Opens getUserMedia, creates AudioContext + worklet/processor, and
 * forwards PCM frames to the Gemini WebSocket.
 */
export async function startMicPipeline(
  refs: GeminiRefs,
  setState: React.Dispatch<React.SetStateAction<GeminiLiveState>>
): Promise<void> {
  if (!refs.wsRef.current || refs.wsRef.current.readyState !== WebSocket.OPEN) {
    console.warn('[GeminiLive] Cannot start listening — not connected');
    return;
  }

  // Ensure any previous mic pipeline is fully torn down before recreating
  // (critical for reconnect scenarios — prevents orphaned AudioContexts)
  if (refs.audioContextRef.current) {
    console.log('[GeminiLive] Tearing down stale audio context before restart');
    try { refs.workletNodeRef.current?.disconnect(); } catch { /* teardown */ }
    try { refs.processorRef.current?.disconnect(); } catch { /* teardown */ }
    try { refs.audioContextRef.current.close(); } catch { /* teardown */ }
    refs.audioContextRef.current = null;
    refs.workletNodeRef.current = null;
    refs.processorRef.current = null;
  }
  if (refs.streamRef.current) {
    refs.streamRef.current.getTracks().forEach((t) => t.stop());
    refs.streamRef.current = null;
  }

  try {
    // Flush any in-progress audio playback so Friday stops talking immediately
    refs.playbackEngineRef.current?.flush();

    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        sampleRate: 16000,
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,       // Stabilizes mic levels over long sessions
      },
    });
    refs.streamRef.current = stream;

    const audioContext = new AudioContext({ sampleRate: 16000 });
    refs.audioContextRef.current = audioContext;
    const source = audioContext.createMediaStreamSource(stream);

    // Create mic analyser for audio reactivity
    const micAnalyser = audioContext.createAnalyser();
    micAnalyser.fftSize = 256;
    micAnalyser.smoothingTimeConstant = 0.8;
    source.connect(micAnalyser);
    refs.micAnalyserRef.current = micAnalyser;
    refs.micAnalyserDataRef.current = new Uint8Array(micAnalyser.frequencyBinCount);

    // Try AudioWorklet first, fall back to ScriptProcessorNode
    let workletLoaded = false;
    try {
      await audioContext.audioWorklet.addModule('./pcm-capture-processor.js');
      const workletNode = new AudioWorkletNode(audioContext, 'pcm-capture-processor');
      refs.workletNodeRef.current = workletNode;

      workletNode.port.onmessage = (e: MessageEvent<ArrayBuffer>) => {
        // Guard: don't send audio until Gemini has confirmed setup (prevents pre-setup contamination on reconnect)
        if (!refs.wsRef.current || refs.wsRef.current.readyState !== WebSocket.OPEN || !refs.setupCompleteRef.current) return;

        // Client-side noise gate: compute RMS energy and skip near-silent frames
        // This prevents ambient noise, keyboard clicks, and fan noise from triggering Gemini's VAD
        const samples = new Int16Array(e.data);
        let sumSq = 0;
        for (let i = 0; i < samples.length; i++) {
          const normalized = samples[i] / 32768;
          sumSq += normalized * normalized;
        }
        const rms = Math.sqrt(sumSq / samples.length);

        // Threshold: 0.015 filters ambient noise while passing normal speech (typically RMS > 0.05)
        if (rms < 0.015) return;

        const b64 = arrayBufferToBase64(e.data);
        refs.wsRef.current.send(
          JSON.stringify({
            realtime_input: {
              media_chunks: [{ data: b64, mime_type: 'audio/pcm;rate=16000' }],
            },
          })
        );
      };

      source.connect(workletNode);
      workletNode.connect(audioContext.destination);
      workletLoaded = true;
      console.log('[GeminiLive] Using AudioWorklet for mic capture');
    } catch (workletErr) {
      console.warn('[GeminiLive] AudioWorklet unavailable, falling back to ScriptProcessor:', workletErr);
    }

    if (!workletLoaded) {
      const processor = audioContext.createScriptProcessor(4096, 1, 1);
      refs.processorRef.current = processor;

      processor.onaudioprocess = (event) => {
        // Guard: don't send audio until Gemini has confirmed setup (prevents pre-setup contamination on reconnect)
        if (!refs.wsRef.current || refs.wsRef.current.readyState !== WebSocket.OPEN || !refs.setupCompleteRef.current) return;

        const input = event.inputBuffer.getChannelData(0);

        // Client-side noise gate: compute RMS energy and skip near-silent frames
        let sumSq = 0;
        for (let i = 0; i < input.length; i++) {
          sumSq += input[i] * input[i];
        }
        const rms = Math.sqrt(sumSq / input.length);
        if (rms < 0.015) return; // Filter ambient noise

        const pcm16 = float32ToInt16(input);
        const b64 = arrayBufferToBase64(pcm16.buffer as ArrayBuffer);

        refs.wsRef.current.send(
          JSON.stringify({
            realtime_input: {
              media_chunks: [{ data: b64, mime_type: 'audio/pcm;rate=16000' }],
            },
          })
        );
      };

      source.connect(processor);
      processor.connect(audioContext.destination);
      console.log('[GeminiLive] Using ScriptProcessorNode for mic capture (fallback)');
    }

    // Start screen capture and forward frames to Gemini (skip if already running from auto-start)
    if (window.eve.screenCapture && !refs.screenFrameCleanupRef.current) {
      await window.eve.screenCapture.start();
      const cleanup = window.eve.screenCapture.onFrame((frame: string) => {
        // Guard: don't send frames until Gemini has confirmed setup
        if (refs.wsRef.current?.readyState === WebSocket.OPEN && refs.setupCompleteRef.current) {
          refs.wsRef.current.send(
            JSON.stringify({
              realtime_input: {
                media_chunks: [{ data: frame, mime_type: 'image/jpeg' }],
              },
            })
          );
        }
      });
      refs.screenFrameCleanupRef.current = cleanup;
    }

    setState((s) => ({ ...s, isListening: true }));
    const wsState = refs.wsRef.current?.readyState === WebSocket.OPEN ? 'OPEN' : 'CLOSED';
    const acState = refs.audioContextRef.current?.state || 'none';
    const micTracks = refs.streamRef.current?.getAudioTracks().length || 0;
    console.log(`[GeminiLive] Listening started — ws:${wsState} audioCtx:${acState} micTracks:${micTracks}`);
  } catch (err) {
    console.error('[GeminiLive] Mic access error:', err);
    refs.optionsRef.current.onError?.('Microphone access denied');
  }
}

/**
 * Stop mic capture + screen sharing + webcam + call mode cleanup.
 */
export function stopMicPipeline(
  refs: GeminiRefs,
  setState: React.Dispatch<React.SetStateAction<GeminiLiveState>>
): void {
  refs.workletNodeRef.current?.disconnect();
  refs.workletNodeRef.current = null;

  refs.processorRef.current?.disconnect();
  refs.processorRef.current = null;

  // Close mic AudioContext — wait for it to fully close before allowing restart
  try { refs.audioContextRef.current?.close(); } catch { /* teardown */ }
  refs.audioContextRef.current = null;

  // Fully stop all mic media tracks so the browser releases the device
  refs.streamRef.current?.getTracks().forEach((t) => t.stop());
  refs.streamRef.current = null;

  // Clear mic analyser so startListening creates fresh ones
  refs.micAnalyserRef.current = null;
  refs.micAnalyserDataRef.current = null;

  refs.screenFrameCleanupRef.current?.();
  refs.screenFrameCleanupRef.current = null;

  window.eve.screenCapture?.stop();

  // Auto-cleanup webcam if active
  if (refs.webcamIntervalRef.current) {
    clearInterval(refs.webcamIntervalRef.current);
    refs.webcamIntervalRef.current = null;
  }
  refs.webcamStreamRef.current?.getTracks().forEach((t) => t.stop());
  refs.webcamStreamRef.current = null;
  if (refs.webcamVideoRef.current) {
    refs.webcamVideoRef.current.remove();
    refs.webcamVideoRef.current = null;
  }
  refs.webcamCanvasRef.current = null;

  // Auto-cleanup call mode if active
  if (refs.playbackEngineRef.current?.getCurrentSinkId()) {
    refs.playbackEngineRef.current.resetOutputDevice().catch(() => {});
    window.eve.callIntegration.exitCallMode().catch(() => {});
  }

  setState((s) => ({ ...s, isListening: false, isWebcamActive: false, isInCall: false }));
}
