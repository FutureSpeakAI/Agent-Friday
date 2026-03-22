/**
 * FridayRecorder.tsx — Audio / Screen recorder for Agent Friday
 *
 * Uses MediaRecorder API for audio and getDisplayMedia for screen capture.
 * Includes a canvas-based audio level visualizer and recording timer.
 */

import React, { useState, useEffect, useRef, useCallback } from 'react';
import AppShell from '../AppShell';

interface Props {
  visible: boolean;
  onClose: () => void;
}

type RecordingMode = 'audio' | 'screen';
type RecordingState = 'idle' | 'requesting' | 'recording' | 'paused' | 'error';

interface RecordedFile {
  id: string;
  name: string;
  url: string;
  blob: Blob;
  duration: number;
  mode: RecordingMode;
  timestamp: number;
}

export default function FridayRecorder({ visible, onClose }: Props) {
  const [mode, setMode] = useState<RecordingMode>('audio');
  const [recordState, setRecordState] = useState<RecordingState>('idle');
  const [errorMsg, setErrorMsg] = useState('');
  const [elapsed, setElapsed] = useState(0);
  const [recordings, setRecordings] = useState<RecordedFile[]>([]);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<number | null>(null);
  const startTimeRef = useRef(0);
  const pausedElapsedRef = useRef(0);

  // Audio visualizer refs
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const animFrameRef = useRef<number>(0);

  // Format elapsed time
  const formatTime = (seconds: number): string => {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const sec = seconds % 60;
    const pad = (n: number) => n.toString().padStart(2, '0');
    return h > 0 ? `${pad(h)}:${pad(m)}:${pad(sec)}` : `${pad(m)}:${pad(sec)}`;
  };

  // Start the elapsed timer
  const startTimer = useCallback(() => {
    startTimeRef.current = Date.now();
    timerRef.current = window.setInterval(() => {
      const now = Date.now();
      setElapsed(pausedElapsedRef.current + Math.floor((now - startTimeRef.current) / 1000));
    }, 250);
  }, []);

  // Stop the timer
  const stopTimer = useCallback(() => {
    if (timerRef.current !== null) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  // Draw audio waveform visualizer
  const drawVisualizer = useCallback(() => {
    const canvas = canvasRef.current;
    const analyser = analyserRef.current;
    if (!canvas || !analyser) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const bufferLength = analyser.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLength);

    const draw = () => {
      animFrameRef.current = requestAnimationFrame(draw);
      analyser.getByteFrequencyData(dataArray);

      const w = canvas.width;
      const h = canvas.height;
      ctx.clearRect(0, 0, w, h);

      // Background
      ctx.fillStyle = 'rgba(10, 10, 20, 0.3)';
      ctx.fillRect(0, 0, w, h);

      const barCount = 64;
      const barWidth = (w / barCount) * 0.7;
      const gap = (w / barCount) * 0.3;
      const step = Math.floor(bufferLength / barCount);

      for (let i = 0; i < barCount; i++) {
        const value = dataArray[i * step] / 255;
        const barHeight = value * h * 0.85;

        // Gradient color from cyan to purple based on intensity
        const r = Math.floor(138 * value);
        const g = Math.floor(43 + 197 * (1 - value));
        const b = Math.floor(226 + 29 * value);

        ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${0.6 + value * 0.4})`;
        const x = i * (barWidth + gap);
        const y = h - barHeight;

        ctx.beginPath();
        ctx.roundRect(x, y, barWidth, barHeight, 2);
        ctx.fill();

        // Mirror reflection
        ctx.fillStyle = `rgba(${r}, ${g}, ${b}, 0.15)`;
        ctx.beginPath();
        ctx.roundRect(x, h, barWidth, barHeight * 0.3, 2);
        ctx.fill();
      }
    };

    draw();
  }, []);

  // Setup audio analyser
  const setupAnalyser = useCallback((stream: MediaStream) => {
    try {
      const audioCtx = new AudioContext();
      const source = audioCtx.createMediaStreamSource(stream);
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 256;
      source.connect(analyser);

      audioCtxRef.current = audioCtx;
      analyserRef.current = analyser;
      drawVisualizer();
    } catch {
      // Analyser is optional
    }
  }, [drawVisualizer]);

  // Cleanup audio analyser
  const cleanupAnalyser = useCallback(() => {
    if (animFrameRef.current) {
      cancelAnimationFrame(animFrameRef.current);
      animFrameRef.current = 0;
    }
    if (audioCtxRef.current) {
      audioCtxRef.current.close().catch(() => {});
      audioCtxRef.current = null;
    }
    analyserRef.current = null;
  }, []);

  // Start recording
  const startRecording = useCallback(async () => {
    setRecordState('requesting');
    setErrorMsg('');
    chunksRef.current = [];
    setElapsed(0);
    pausedElapsedRef.current = 0;

    try {
      let stream: MediaStream;

      if (mode === 'audio') {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } else {
        stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true });
      }

      streamRef.current = stream;

      // Listen for user stopping screen share via browser UI
      const onTrackEnded = () => {
        if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
          mediaRecorderRef.current.stop();
        }
      };
      stream.getVideoTracks().forEach((track) => {
        track.addEventListener('ended', onTrackEnded);
      });

      const mimeType = mode === 'audio'
        ? (MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : 'audio/webm')
        : (MediaRecorder.isTypeSupported('video/webm;codecs=vp9,opus') ? 'video/webm;codecs=vp9,opus' : 'video/webm');

      const recorder = new MediaRecorder(stream, { mimeType });
      mediaRecorderRef.current = recorder;

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) {
          chunksRef.current.push(e.data);
        }
      };

      recorder.onstop = () => {
        stopTimer();
        const blob = new Blob(chunksRef.current, { type: mimeType });
        const url = URL.createObjectURL(blob);
        const file: RecordedFile = {
          id: crypto.randomUUID?.() || `${Date.now()}`,
          name: `${mode}-${new Date().toLocaleTimeString().replace(/:/g, '-')}`,
          url,
          blob,
          duration: elapsed,
          mode,
          timestamp: Date.now(),
        };
        setRecordings((prev) => [file, ...prev]);
        setRecordState('idle');
        cleanupAnalyser();

        // Stop all tracks and remove event listeners
        if (streamRef.current) {
          streamRef.current.getVideoTracks().forEach((t) => t.removeEventListener('ended', onTrackEnded));
          streamRef.current.getTracks().forEach((t) => t.stop());
          streamRef.current = null;
        }
      };

      recorder.onerror = () => {
        setRecordState('error');
        setErrorMsg('Recording failed unexpectedly.');
        stopTimer();
        cleanupAnalyser();
      };

      recorder.start(100); // collect data every 100ms
      setRecordState('recording');
      startTimer();

      // Setup audio visualizer
      if (mode === 'audio') {
        setupAnalyser(stream);
      }
    } catch (err: any) {
      if (err.name === 'NotAllowedError') {
        setRecordState('error');
        setErrorMsg(mode === 'audio'
          ? 'Microphone permission denied.'
          : 'Screen sharing permission denied or cancelled.');
      } else {
        setRecordState('error');
        setErrorMsg(`Error: ${err.message || 'Unknown error'}`);
      }
    }
  }, [mode, elapsed, startTimer, stopTimer, setupAnalyser, cleanupAnalyser]);

  // Stop recording
  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop();
    }
  }, []);

  // Pause / Resume
  const togglePause = useCallback(() => {
    const recorder = mediaRecorderRef.current;
    if (!recorder) return;

    if (recorder.state === 'recording') {
      recorder.pause();
      pausedElapsedRef.current = elapsed;
      stopTimer();
      setRecordState('paused');
    } else if (recorder.state === 'paused') {
      recorder.resume();
      startTimer();
      setRecordState('recording');
    }
  }, [elapsed, startTimer, stopTimer]);

  // Download a recorded file
  const downloadRecording = useCallback((file: RecordedFile) => {
    const ext = file.mode === 'audio' ? 'webm' : 'webm';
    const link = document.createElement('a');
    link.href = file.url;
    link.download = `${file.name}.${ext}`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }, []);

  // Delete a recording
  const deleteRecording = useCallback((id: string) => {
    setRecordings((prev) => {
      const file = prev.find((f) => f.id === id);
      if (file) URL.revokeObjectURL(file.url);
      return prev.filter((f) => f.id !== id);
    });
  }, []);

  // Cleanup on unmount/close
  useEffect(() => {
    if (!visible) {
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
        mediaRecorderRef.current.stop();
      }
      stopTimer();
      cleanupAnalyser();
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((t) => t.stop());
        streamRef.current = null;
      }
      setRecordState('idle');
      setElapsed(0);
    }
    return () => {
      stopTimer();
      cleanupAnalyser();
    };
  }, [visible, stopTimer, cleanupAnalyser]);

  const isRecordingOrPaused = recordState === 'recording' || recordState === 'paused';

  return (
    <AppShell visible={visible} onClose={onClose} title="Recorder" icon="🎙️" width={700}>
      {/* Mode tabs */}
      <div style={s.tabBar}>
        {(['audio', 'screen'] as RecordingMode[]).map((m) => (
          <button
            key={m}
            style={{
              ...s.tab,
              color: mode === m ? '#00f0ff' : '#8888a0',
              borderBottomColor: mode === m ? '#00f0ff' : 'transparent',
              background: mode === m ? 'rgba(0,240,255,0.05)' : 'transparent',
            }}
            onClick={() => { if (!isRecordingOrPaused) setMode(m); }}
            disabled={isRecordingOrPaused}
          >
            {m === 'audio' ? '🎤 Audio' : '🖥️ Screen'}
          </button>
        ))}
      </div>

      {/* Visualizer / Status */}
      <div style={s.visualizerWrap}>
        {mode === 'audio' && (recordState === 'recording' || recordState === 'paused') ? (
          <canvas
            ref={canvasRef}
            width={620}
            height={120}
            style={s.visualizerCanvas}
          />
        ) : (
          <div style={s.visualizerPlaceholder}>
            <span style={{ fontSize: 40 }}>{mode === 'audio' ? '🎤' : '🖥️'}</span>
            <span style={s.placeholderText}>
              {recordState === 'idle' && `Ready to record ${mode}`}
              {recordState === 'requesting' && 'Requesting permission...'}
              {recordState === 'error' && errorMsg}
            </span>
          </div>
        )}
      </div>

      {/* Timer */}
      <div style={s.timerWrap}>
        <span style={{
          ...s.timer,
          color: recordState === 'recording' ? '#ef4444' : recordState === 'paused' ? '#f97316' : '#8888a0',
        }}>
          {recordState === 'recording' && (
            <span style={s.recordDot} />
          )}
          {recordState === 'paused' && '⏸ '}
          {formatTime(elapsed)}
        </span>
      </div>

      {/* Controls */}
      <div style={s.controlsRow}>
        {!isRecordingOrPaused ? (
          <button
            style={s.recordBtn}
            onClick={startRecording}
            disabled={recordState === 'requesting'}
          >
            <span style={s.recordBtnDot} />
            Record
          </button>
        ) : (
          <>
            <button style={s.ctrlBtn} onClick={togglePause}>
              {recordState === 'paused' ? '▶ Resume' : '⏸ Pause'}
            </button>
            <button style={{ ...s.ctrlBtn, borderColor: 'rgba(239,68,68,0.4)', color: '#ef4444' }} onClick={stopRecording}>
              ⏹ Stop
            </button>
          </>
        )}
      </div>

      {/* Recordings list */}
      {recordings.length > 0 && (
        <div style={s.recordingsSection}>
          <span style={s.sectionTitle}>Recordings ({recordings.length})</span>
          <div style={s.recordingsList}>
            {recordings.map((file) => (
              <div key={file.id} style={s.recordingItem}>
                <div style={s.recordingInfo}>
                  <span style={s.recordingName}>
                    {file.mode === 'audio' ? '🎤' : '🖥️'} {file.name}
                  </span>
                  <span style={s.recordingMeta}>
                    {formatTime(file.duration)} &middot; {(file.blob.size / 1024).toFixed(0)} KB
                  </span>
                </div>
                <div style={s.recordingActions}>
                  {file.mode === 'audio' ? (
                    <audio src={file.url} controls style={s.audioPlayer} />
                  ) : (
                    <video src={file.url} controls style={s.videoPlayer} />
                  )}
                  <button style={s.smallBtn} onClick={() => downloadRecording(file)} title="Download">
                    💾
                  </button>
                  <button style={{ ...s.smallBtn, color: '#ef4444' }} onClick={() => deleteRecording(file.id)} title="Delete">
                    🗑️
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </AppShell>
  );
}

// ── Styles ──────────────────────────────────────────────────────────────────
const s: Record<string, React.CSSProperties> = {
  tabBar: {
    display: 'flex',
    gap: 0,
    borderBottom: '1px solid rgba(255,255,255,0.06)',
    marginBottom: 16,
  },
  tab: {
    flex: 1,
    background: 'none',
    border: 'none',
    borderBottom: '2px solid transparent',
    padding: '10px 16px',
    fontSize: 13,
    fontWeight: 600,
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
    transition: 'color 0.15s, background 0.15s',
  },
  visualizerWrap: {
    width: '100%',
    height: 140,
    background: 'rgba(255,255,255,0.03)',
    borderRadius: 12,
    border: '1px solid rgba(255,255,255,0.07)',
    overflow: 'hidden',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
  },
  visualizerCanvas: {
    width: '100%',
    height: '100%',
    display: 'block',
  },
  visualizerPlaceholder: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 10,
  },
  placeholderText: {
    fontSize: 13,
    color: '#8888a0',
    fontFamily: "'Inter', system-ui, sans-serif",
    textAlign: 'center' as const,
    maxWidth: 360,
  },
  timerWrap: {
    display: 'flex',
    justifyContent: 'center',
    padding: '8px 0',
  },
  timer: {
    fontSize: 28,
    fontWeight: 700,
    fontFamily: "'JetBrains Mono', monospace",
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  recordDot: {
    display: 'inline-block',
    width: 10,
    height: 10,
    borderRadius: '50%',
    background: '#ef4444',
    animation: 'pulse 1s ease-in-out infinite',
  },
  controlsRow: {
    display: 'flex',
    justifyContent: 'center',
    gap: 12,
    padding: '4px 0 12px',
  },
  recordBtn: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    background: 'rgba(239,68,68,0.12)',
    border: '1px solid rgba(239,68,68,0.4)',
    borderRadius: 24,
    color: '#ef4444',
    fontSize: 14,
    fontWeight: 600,
    padding: '10px 28px',
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
    transition: 'background 0.15s',
  },
  recordBtnDot: {
    display: 'inline-block',
    width: 10,
    height: 10,
    borderRadius: '50%',
    background: '#ef4444',
  },
  ctrlBtn: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.1)',
    borderRadius: 10,
    color: '#F8FAFC',
    fontSize: 13,
    fontWeight: 500,
    padding: '9px 22px',
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
    transition: 'background 0.15s',
  },
  recordingsSection: {
    borderTop: '1px solid rgba(255,255,255,0.06)',
    paddingTop: 16,
  },
  sectionTitle: {
    fontSize: 13,
    fontWeight: 600,
    color: '#F8FAFC',
    fontFamily: "'Inter', system-ui, sans-serif",
    display: 'block',
    marginBottom: 10,
  },
  recordingsList: {
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  recordingItem: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 10,
    padding: '10px 14px',
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  recordingInfo: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  recordingName: {
    fontSize: 13,
    color: '#F8FAFC',
    fontWeight: 500,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  recordingMeta: {
    fontSize: 11,
    color: '#8888a0',
    fontFamily: "'JetBrains Mono', monospace",
  },
  recordingActions: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  audioPlayer: {
    flex: 1,
    height: 32,
    borderRadius: 6,
  },
  videoPlayer: {
    flex: 1,
    maxHeight: 120,
    borderRadius: 6,
    background: '#000',
  },
  smallBtn: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 6,
    color: '#8888a0',
    fontSize: 14,
    padding: '4px 8px',
    cursor: 'pointer',
    transition: 'background 0.15s',
  },
};
