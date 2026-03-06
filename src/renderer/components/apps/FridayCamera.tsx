/**
 * FridayCamera.tsx — Camera capture app for Agent Friday
 *
 * Uses navigator.mediaDevices.getUserMedia for live camera preview,
 * canvas snapshot for photo capture, and device enumeration for camera selection.
 */

import React, { useState, useEffect, useRef, useCallback } from 'react';
import AppShell from '../AppShell';

interface Props {
  visible: boolean;
  onClose: () => void;
}

type CameraState = 'idle' | 'requesting' | 'active' | 'denied' | 'error';

export default function FridayCamera({ visible, onClose }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);

  const [cameraState, setCameraState] = useState<CameraState>('idle');
  const [errorMsg, setErrorMsg] = useState('');
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [selectedDeviceId, setSelectedDeviceId] = useState<string>('');
  const [capturedPhoto, setCapturedPhoto] = useState<string | null>(null);
  const [isMirrored, setIsMirrored] = useState(true);
  const [flashActive, setFlashActive] = useState(false);

  // Enumerate video devices
  const enumerateDevices = useCallback(async () => {
    try {
      const allDevices = await navigator.mediaDevices.enumerateDevices();
      const videoDevices = allDevices.filter((d) => d.kind === 'videoinput');
      setDevices(videoDevices);
      if (videoDevices.length > 0 && !selectedDeviceId) {
        setSelectedDeviceId(videoDevices[0].deviceId);
      }
    } catch {
      // Devices may not be enumerable before permission grant
    }
  }, [selectedDeviceId]);

  // Start camera stream
  const startCamera = useCallback(async (deviceId?: string) => {
    // Stop any existing stream first
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }

    setCameraState('requesting');
    setErrorMsg('');

    try {
      const constraints: MediaStreamConstraints = {
        video: deviceId
          ? { deviceId: { exact: deviceId }, width: { ideal: 1280 }, height: { ideal: 720 } }
          : { width: { ideal: 1280 }, height: { ideal: 720 } },
      };

      const stream = await navigator.mediaDevices.getUserMedia(constraints);
      streamRef.current = stream;

      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }

      setCameraState('active');
      // Re-enumerate devices after permission is granted
      await enumerateDevices();
    } catch (err: any) {
      if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
        setCameraState('denied');
        setErrorMsg('Camera permission was denied. Please allow camera access in your browser/system settings.');
      } else if (err.name === 'NotFoundError') {
        setCameraState('error');
        setErrorMsg('No camera device found. Please connect a camera and try again.');
      } else if (err.name === 'NotReadableError') {
        setCameraState('error');
        setErrorMsg('Camera is in use by another application.');
      } else {
        setCameraState('error');
        setErrorMsg(`Camera error: ${err.message || 'Unknown error'}`);
      }
    }
  }, [enumerateDevices]);

  // Stop camera stream
  const stopCamera = useCallback(() => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    if (videoRef.current) {
      videoRef.current.srcObject = null;
    }
    setCameraState('idle');
  }, []);

  // Auto-start when visible, cleanup on close
  useEffect(() => {
    if (visible) {
      startCamera(selectedDeviceId || undefined);
    } else {
      stopCamera();
      setCapturedPhoto(null);
    }
    return () => {
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((t) => t.stop());
        streamRef.current = null;
      }
    };
  }, [visible]); // eslint-disable-line react-hooks/exhaustive-deps

  // Switch camera device
  const handleDeviceChange = useCallback((e: React.ChangeEvent<HTMLSelectElement>) => {
    const id = e.target.value;
    setSelectedDeviceId(id);
    if (cameraState === 'active') {
      startCamera(id);
    }
  }, [cameraState, startCamera]);

  // Capture photo from video
  const capturePhoto = useCallback(() => {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas) return;

    canvas.width = video.videoWidth || 1280;
    canvas.height = video.videoHeight || 720;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    if (isMirrored) {
      ctx.translate(canvas.width, 0);
      ctx.scale(-1, 1);
    }
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    if (isMirrored) {
      ctx.setTransform(1, 0, 0, 1, 0, 0);
    }

    const dataUrl = canvas.toDataURL('image/png');
    setCapturedPhoto(dataUrl);

    // Flash effect
    setFlashActive(true);
    setTimeout(() => setFlashActive(false), 150);
  }, [isMirrored]);

  // Save captured photo
  const savePhoto = useCallback(() => {
    if (!capturedPhoto) return;

    // Try IPC save first, fallback to download
    try {
      if ((window as any).eve?.multimedia?.saveCapture) {
        (window as any).eve.multimedia.saveCapture(capturedPhoto);
        return;
      }
    } catch {
      // fallback below
    }

    const link = document.createElement('a');
    link.href = capturedPhoto;
    link.download = `capture-${Date.now()}.png`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }, [capturedPhoto]);

  return (
    <AppShell visible={visible} onClose={onClose} title="Camera" icon="📷" width={780}>
      {/* Hidden canvas for capture */}
      <canvas ref={canvasRef} style={{ display: 'none' }} />

      {/* Camera feed / states */}
      <div style={s.viewfinder}>
        {cameraState === 'active' && (
          <>
            <video
              ref={videoRef}
              style={{
                ...s.video,
                transform: isMirrored ? 'scaleX(-1)' : 'none',
              }}
              playsInline
              muted
              autoPlay
            />
            {flashActive && <div style={s.flash} />}
          </>
        )}

        {cameraState === 'idle' && (
          <div style={s.placeholder}>
            <span style={{ fontSize: 48 }}>📷</span>
            <span style={s.placeholderText}>Camera is off</span>
            <button style={s.primaryBtn} onClick={() => startCamera(selectedDeviceId || undefined)}>
              Start Camera
            </button>
          </div>
        )}

        {cameraState === 'requesting' && (
          <div style={s.placeholder}>
            <div style={s.spinner} />
            <span style={s.placeholderText}>Requesting camera access...</span>
          </div>
        )}

        {(cameraState === 'denied' || cameraState === 'error') && (
          <div style={s.placeholder}>
            <span style={{ fontSize: 40 }}>⚠️</span>
            <span style={{ ...s.placeholderText, color: '#ef4444', maxWidth: 400, textAlign: 'center' as const }}>
              {errorMsg}
            </span>
            <button style={s.primaryBtn} onClick={() => startCamera(selectedDeviceId || undefined)}>
              Retry
            </button>
          </div>
        )}
      </div>

      {/* Controls bar */}
      <div style={s.controlsBar}>
        <div style={s.controlsLeft}>
          {/* Device selector */}
          {devices.length > 1 && (
            <select
              style={s.select}
              value={selectedDeviceId}
              onChange={handleDeviceChange}
            >
              {devices.map((d, i) => (
                <option key={d.deviceId} value={d.deviceId}>
                  {d.label || `Camera ${i + 1}`}
                </option>
              ))}
            </select>
          )}

          {/* Mirror toggle */}
          <button
            style={{
              ...s.toolBtn,
              color: isMirrored ? '#00f0ff' : '#8888a0',
              borderColor: isMirrored ? 'rgba(0,240,255,0.3)' : 'rgba(255,255,255,0.07)',
            }}
            onClick={() => setIsMirrored(!isMirrored)}
            title="Mirror"
          >
            🔄 Mirror
          </button>
        </div>

        <div style={s.controlsCenter}>
          {/* Capture button */}
          <button
            style={s.captureBtn}
            onClick={capturePhoto}
            disabled={cameraState !== 'active'}
            title="Capture Photo"
          >
            <div style={s.captureBtnInner} />
          </button>
        </div>

        <div style={s.controlsRight}>
          {cameraState === 'active' ? (
            <button style={s.toolBtn} onClick={stopCamera}>
              ⏹ Stop
            </button>
          ) : (
            <button
              style={s.toolBtn}
              onClick={() => startCamera(selectedDeviceId || undefined)}
              disabled={cameraState === 'requesting'}
            >
              ▶ Start
            </button>
          )}
        </div>
      </div>

      {/* Captured photo preview */}
      {capturedPhoto && (
        <div style={s.previewSection}>
          <div style={s.previewHeader}>
            <span style={s.sectionTitle}>Captured Photo</span>
            <div style={{ display: 'flex', gap: 8 }}>
              <button style={s.primaryBtn} onClick={savePhoto}>
                💾 Save
              </button>
              <button style={s.toolBtn} onClick={() => setCapturedPhoto(null)}>
                ✕ Discard
              </button>
            </div>
          </div>
          <div style={s.previewImageWrap}>
            <img src={capturedPhoto} alt="Captured" style={s.previewImage} />
          </div>
        </div>
      )}
    </AppShell>
  );
}

// ── Styles ──────────────────────────────────────────────────────────────────
const s: Record<string, React.CSSProperties> = {
  viewfinder: {
    position: 'relative',
    width: '100%',
    aspectRatio: '16/9',
    background: '#0a0a14',
    borderRadius: 12,
    overflow: 'hidden',
    border: '1px solid rgba(255,255,255,0.07)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
  },
  video: {
    width: '100%',
    height: '100%',
    objectFit: 'cover',
    display: 'block',
  },
  flash: {
    position: 'absolute',
    inset: 0,
    background: 'rgba(255,255,255,0.8)',
    pointerEvents: 'none',
    zIndex: 10,
  },
  placeholder: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 16,
    padding: 40,
  },
  placeholderText: {
    fontSize: 14,
    color: '#8888a0',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  spinner: {
    width: 32,
    height: 32,
    border: '3px solid rgba(255,255,255,0.1)',
    borderTopColor: '#00f0ff',
    borderRadius: '50%',
    animation: 'spin 1s linear infinite',
  },
  controlsBar: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '12px 0',
    gap: 12,
  },
  controlsLeft: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    flex: 1,
  },
  controlsCenter: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
  },
  controlsRight: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'flex-end',
    gap: 8,
    flex: 1,
  },
  captureBtn: {
    width: 64,
    height: 64,
    borderRadius: '50%',
    background: 'none',
    border: '3px solid #F8FAFC',
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 4,
    transition: 'transform 0.1s, border-color 0.15s',
  },
  captureBtnInner: {
    width: '100%',
    height: '100%',
    borderRadius: '50%',
    background: '#F8FAFC',
    transition: 'background 0.15s',
  },
  select: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 8,
    color: '#F8FAFC',
    fontSize: 12,
    padding: '6px 10px',
    fontFamily: "'Inter', system-ui, sans-serif",
    outline: 'none',
    maxWidth: 180,
    cursor: 'pointer',
  },
  toolBtn: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 8,
    color: '#8888a0',
    fontSize: 12,
    padding: '7px 14px',
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
    transition: 'background 0.15s, color 0.15s',
    whiteSpace: 'nowrap',
  },
  primaryBtn: {
    background: 'rgba(0,240,255,0.1)',
    border: '1px solid rgba(0,240,255,0.3)',
    borderRadius: 8,
    color: '#00f0ff',
    fontSize: 12,
    fontWeight: 600,
    padding: '7px 16px',
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
    transition: 'background 0.15s',
    whiteSpace: 'nowrap',
  },
  previewSection: {
    borderTop: '1px solid rgba(255,255,255,0.06)',
    paddingTop: 16,
  },
  previewHeader: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 12,
  },
  sectionTitle: {
    fontSize: 13,
    fontWeight: 600,
    color: '#F8FAFC',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  previewImageWrap: {
    borderRadius: 10,
    overflow: 'hidden',
    border: '1px solid rgba(255,255,255,0.07)',
    background: '#0a0a14',
  },
  previewImage: {
    width: '100%',
    display: 'block',
    maxHeight: 300,
    objectFit: 'contain',
  },
};
