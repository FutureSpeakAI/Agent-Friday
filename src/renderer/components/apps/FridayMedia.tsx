/**
 * FridayMedia.tsx — Media creation studio for Agent Friday
 *
 * IPC: window.eve.multimedia.*
 * Features: Podcast, visual, audio, music creation + media library + playback/preview
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import AppShell from '../AppShell';

type MediaTab = 'podcast' | 'visual' | 'audio' | 'library';

interface MediaItem {
  id: string;
  name: string;
  type: string;
  path?: string;
  url?: string;
  thumbnail?: string;
  createdAt?: string;
  duration?: number;
}

interface PodcastForm {
  topic: string;
  speakers: string;
  duration: string;
}

interface VisualForm {
  prompt: string;
  type: string;
}

interface AudioForm {
  text: string;
  voice: string;
}

interface FridayMediaProps {
  visible: boolean;
  onClose: () => void;
}

const VISUAL_TYPES = ['image', 'thumbnail', 'banner', 'avatar', 'diagram'];
const VOICE_OPTIONS = ['alloy', 'echo', 'fable', 'onyx', 'nova', 'shimmer'];

function formatDuration(seconds?: number): string {
  if (!seconds) return '';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

export default function FridayMedia({ visible, onClose }: FridayMediaProps) {
  const [tab, setTab] = useState<MediaTab>('podcast');
  const [library, setLibrary] = useState<MediaItem[]>([]);
  const [mediaDir, setMediaDir] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [createResult, setCreateResult] = useState<string | null>(null);
  const [preview, setPreview] = useState<MediaItem | null>(null);

  const [podcastForm, setPodcastForm] = useState<PodcastForm>({
    topic: '',
    speakers: '2',
    duration: '5',
  });
  const [visualForm, setVisualForm] = useState<VisualForm>({
    prompt: '',
    type: 'image',
  });
  const [audioForm, setAudioForm] = useState<AudioForm>({
    text: '',
    voice: 'alloy',
  });

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState(false);

  const loadLibrary = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const [items, dir] = await Promise.all([
        window.eve.multimedia.listMedia(),
        window.eve.multimedia.getMediaDir(),
      ]);
      setLibrary(Array.isArray(items) ? items : []);
      setMediaDir(typeof dir === 'string' ? dir : '');
    } catch (err: any) {
      setError(err?.message || 'Failed to load media library');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!visible) {
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current = null;
      }
      setPlaying(false);
      return;
    }
    loadLibrary();
  }, [visible, loadLibrary]);

  const handleCreatePodcast = async () => {
    if (!podcastForm.topic) return;
    try {
      setCreating(true);
      setError(null);
      setCreateResult(null);
      const result = await window.eve.multimedia.createPodcast({
        topic: podcastForm.topic,
        speakers: parseInt(podcastForm.speakers) || 2,
        duration: parseInt(podcastForm.duration) || 5,
      });
      setCreateResult(typeof result === 'string' ? result : JSON.stringify(result));
      setPodcastForm({ topic: '', speakers: '2', duration: '5' });
      await loadLibrary();
    } catch (err: any) {
      setError(err?.message || 'Failed to create podcast');
    } finally {
      setCreating(false);
    }
  };

  const handleCreateVisual = async () => {
    if (!visualForm.prompt) return;
    try {
      setCreating(true);
      setError(null);
      setCreateResult(null);
      const result = await window.eve.multimedia.createVisual({
        prompt: visualForm.prompt,
        type: visualForm.type,
      });
      setCreateResult(typeof result === 'string' ? result : JSON.stringify(result));
      setVisualForm({ prompt: '', type: 'image' });
      await loadLibrary();
    } catch (err: any) {
      setError(err?.message || 'Failed to create visual');
    } finally {
      setCreating(false);
    }
  };

  const handleGenerateAudio = async () => {
    if (!audioForm.text) return;
    try {
      setCreating(true);
      setError(null);
      setCreateResult(null);
      const result = await window.eve.multimedia.createAudioMessage({
        text: audioForm.text,
        voice: audioForm.voice,
      });
      setCreateResult(typeof result === 'string' ? result : JSON.stringify(result));
      setAudioForm({ text: '', voice: 'alloy' });
      await loadLibrary();
    } catch (err: any) {
      setError(err?.message || 'Failed to generate audio');
    } finally {
      setCreating(false);
    }
  };

  const handlePlay = (item: MediaItem) => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
    setPreview(item);
    const src = item.url || item.path;
    if (src && (item.type === 'audio' || item.type === 'podcast' || item.type === 'music')) {
      const audio = new Audio(src);
      audio.onended = () => setPlaying(false);
      audio.play().catch(() => {});
      audioRef.current = audio;
      setPlaying(true);
    }
  };

  const handleStop = () => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
    setPlaying(false);
  };

  const renderTabBar = () => (
    <div style={s.tabBar}>
      {([
        { key: 'podcast', label: '🎙 Podcast' },
        { key: 'visual', label: '🖼 Visual' },
        { key: 'audio', label: '🔊 Audio' },
        { key: 'library', label: '📚 Library' },
      ] as const).map(({ key, label }) => (
        <button
          key={key}
          style={tab === key ? { ...s.tab, ...s.tabActive } : s.tab}
          onClick={() => { setTab(key); setCreateResult(null); }}
        >
          {label}
        </button>
      ))}
    </div>
  );

  const renderPodcast = () => (
    <div style={s.formCard}>
      <div style={s.formTitle}>Create Podcast</div>
      <div style={s.formHint}>Generate an AI podcast discussion on any topic</div>
      <input
        style={s.input}
        placeholder="Topic (e.g., The future of AI agents)"
        value={podcastForm.topic}
        onChange={(e) => setPodcastForm({ ...podcastForm, topic: e.target.value })}
      />
      <div style={s.row}>
        <div style={{ flex: 1 }}>
          <label style={s.label}>Speakers</label>
          <select
            style={s.select}
            value={podcastForm.speakers}
            onChange={(e) => setPodcastForm({ ...podcastForm, speakers: e.target.value })}
          >
            <option value="1">1 Speaker</option>
            <option value="2">2 Speakers</option>
            <option value="3">3 Speakers</option>
          </select>
        </div>
        <div style={{ flex: 1 }}>
          <label style={s.label}>Duration (min)</label>
          <select
            style={s.select}
            value={podcastForm.duration}
            onChange={(e) => setPodcastForm({ ...podcastForm, duration: e.target.value })}
          >
            <option value="3">3 minutes</option>
            <option value="5">5 minutes</option>
            <option value="10">10 minutes</option>
            <option value="15">15 minutes</option>
          </select>
        </div>
      </div>
      <button
        style={s.createBtn}
        onClick={handleCreatePodcast}
        disabled={creating || !podcastForm.topic}
      >
        {creating ? 'Generating Podcast...' : 'Generate Podcast'}
      </button>
    </div>
  );

  const renderVisual = () => (
    <div style={s.formCard}>
      <div style={s.formTitle}>Create Visual</div>
      <div style={s.formHint}>Generate images, thumbnails, banners, and more</div>
      <textarea
        style={{ ...s.input, minHeight: 80, resize: 'vertical' }}
        placeholder="Describe the visual you want to create..."
        value={visualForm.prompt}
        onChange={(e) => setVisualForm({ ...visualForm, prompt: e.target.value })}
      />
      <label style={s.label}>Type</label>
      <div style={s.typeGrid}>
        {VISUAL_TYPES.map((t) => (
          <button
            key={t}
            style={visualForm.type === t ? { ...s.typeBtn, ...s.typeBtnActive } : s.typeBtn}
            onClick={() => setVisualForm({ ...visualForm, type: t })}
          >
            {t}
          </button>
        ))}
      </div>
      <button
        style={s.createBtn}
        onClick={handleCreateVisual}
        disabled={creating || !visualForm.prompt}
      >
        {creating ? 'Generating...' : 'Generate Visual'}
      </button>
    </div>
  );

  const renderAudio = () => (
    <div style={s.formCard}>
      <div style={s.formTitle}>Generate Audio</div>
      <div style={s.formHint}>Text-to-speech and audio generation</div>
      <textarea
        style={{ ...s.input, minHeight: 100, resize: 'vertical' }}
        placeholder="Enter text to convert to speech..."
        value={audioForm.text}
        onChange={(e) => setAudioForm({ ...audioForm, text: e.target.value })}
      />
      <label style={s.label}>Voice</label>
      <div style={s.typeGrid}>
        {VOICE_OPTIONS.map((v) => (
          <button
            key={v}
            style={audioForm.voice === v ? { ...s.typeBtn, ...s.typeBtnActive } : s.typeBtn}
            onClick={() => setAudioForm({ ...audioForm, voice: v })}
          >
            {v}
          </button>
        ))}
      </div>
      <button
        style={s.createBtn}
        onClick={handleGenerateAudio}
        disabled={creating || !audioForm.text}
      >
        {creating ? 'Generating...' : 'Generate Audio'}
      </button>
    </div>
  );

  const renderLibrary = () => (
    <div style={s.librarySection}>
      {mediaDir && <div style={s.mediaDirHint}>Media directory: {mediaDir}</div>}
      {library.length === 0 && !loading && <div style={s.empty}>No media files yet</div>}
      <div style={s.mediaGrid}>
        {library.map((item) => (
          <div
            key={item.id}
            style={preview?.id === item.id ? { ...s.mediaCard, ...s.mediaCardActive } : s.mediaCard}
            onClick={() => handlePlay(item)}
          >
            {item.thumbnail ? (
              <div
                style={{
                  ...s.mediaThumbnail,
                  backgroundImage: `url(${item.thumbnail})`,
                }}
              />
            ) : (
              <div style={s.mediaPlaceholder}>
                {item.type === 'podcast' ? '🎙' : item.type === 'image' || item.type === 'visual' ? '🖼' : item.type === 'music' ? '🎵' : '🔊'}
              </div>
            )}
            <div style={s.mediaInfo}>
              <div style={s.mediaName}>{item.name}</div>
              <div style={s.mediaMeta}>
                <span style={s.mediaType}>{item.type}</span>
                {item.duration !== undefined && (
                  <span> &middot; {formatDuration(item.duration)}</span>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );

  return (
    <AppShell visible={visible} onClose={onClose} icon="🎬" title="Media Studio" width={860}>
      {error && <div style={s.errorBar}>{error}</div>}
      {renderTabBar()}

      {creating && (
        <div style={s.progressBar}>
          <div style={s.progressFill} />
          <span style={s.progressText}>Creating media... this may take a moment</span>
        </div>
      )}

      {createResult && (
        <div style={s.resultBar}>
          <span style={s.resultIcon}>✓</span>
          <span style={s.resultText}>{createResult}</span>
        </div>
      )}

      {tab === 'podcast' && renderPodcast()}
      {tab === 'visual' && renderVisual()}
      {tab === 'audio' && renderAudio()}
      {tab === 'library' && renderLibrary()}

      {/* Player Bar */}
      {preview && (
        <div style={s.playerBar}>
          <div style={s.playerInfo}>
            <span style={s.playerName}>{preview.name}</span>
            <span style={s.playerType}>{preview.type}</span>
          </div>
          <div style={s.playerControls}>
            {preview.type === 'image' || preview.type === 'visual' ? (
              <span style={s.previewHint}>Preview in file manager</span>
            ) : (
              <button style={s.playBtn} onClick={playing ? handleStop : () => handlePlay(preview)}>
                {playing ? '⏹ Stop' : '▶ Play'}
              </button>
            )}
          </div>
        </div>
      )}

      <button style={s.refreshBtn} onClick={loadLibrary} disabled={loading}>
        {loading ? 'Loading...' : '↻ Refresh Library'}
      </button>
    </AppShell>
  );
}

const s: Record<string, React.CSSProperties> = {
  errorBar: {
    background: 'rgba(239, 68, 68, 0.1)',
    border: '1px solid rgba(239, 68, 68, 0.3)',
    borderRadius: 8,
    padding: '10px 16px',
    color: '#ef4444',
    fontSize: 13,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  tabBar: {
    display: 'flex',
    gap: 2,
    background: 'rgba(255, 255, 255, 0.02)',
    borderRadius: 10,
    padding: 3,
  },
  tab: {
    flex: 1,
    background: 'none',
    border: 'none',
    color: '#8888a0',
    padding: '10px 0',
    fontSize: 13,
    fontWeight: 500,
    cursor: 'pointer',
    borderRadius: 8,
    fontFamily: "'Inter', system-ui, sans-serif",
    transition: 'all 0.15s',
  },
  tabActive: {
    background: 'rgba(0, 240, 255, 0.08)',
    color: '#00f0ff',
  },
  progressBar: {
    background: 'rgba(138, 43, 226, 0.08)',
    border: '1px solid rgba(138, 43, 226, 0.2)',
    borderRadius: 8,
    padding: '12px 16px',
    position: 'relative',
    overflow: 'hidden',
  },
  progressFill: {
    position: 'absolute',
    top: 0,
    left: 0,
    bottom: 0,
    width: '30%',
    background: 'linear-gradient(90deg, rgba(138, 43, 226, 0.15), rgba(0, 240, 255, 0.1))',
    animation: 'shimmer 2s ease infinite',
  },
  progressText: {
    color: '#8A2BE2',
    fontSize: 13,
    fontFamily: "'Inter', system-ui, sans-serif",
    position: 'relative',
    zIndex: 1,
  },
  resultBar: {
    background: 'rgba(34, 197, 94, 0.08)',
    border: '1px solid rgba(34, 197, 94, 0.2)',
    borderRadius: 8,
    padding: '10px 16px',
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  resultIcon: {
    color: '#22c55e',
    fontSize: 16,
    fontWeight: 700,
  },
  resultText: {
    color: '#22c55e',
    fontSize: 13,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  formCard: {
    background: 'rgba(255, 255, 255, 0.03)',
    border: '1px solid rgba(255, 255, 255, 0.07)',
    borderRadius: 12,
    padding: 20,
    display: 'flex',
    flexDirection: 'column',
    gap: 12,
  },
  formTitle: {
    color: '#F8FAFC',
    fontSize: 15,
    fontWeight: 600,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  formHint: {
    color: '#8888a0',
    fontSize: 12,
    fontFamily: "'Inter', system-ui, sans-serif",
    marginTop: -6,
  },
  input: {
    background: 'rgba(255, 255, 255, 0.04)',
    border: '1px solid rgba(255, 255, 255, 0.1)',
    borderRadius: 8,
    color: '#F8FAFC',
    padding: '10px 14px',
    fontSize: 13,
    fontFamily: "'Inter', system-ui, sans-serif",
    outline: 'none',
    width: '100%',
    boxSizing: 'border-box',
  },
  select: {
    background: 'rgba(255, 255, 255, 0.04)',
    border: '1px solid rgba(255, 255, 255, 0.1)',
    borderRadius: 8,
    color: '#F8FAFC',
    padding: '10px 14px',
    fontSize: 13,
    fontFamily: "'Inter', system-ui, sans-serif",
    outline: 'none',
    width: '100%',
    boxSizing: 'border-box',
    cursor: 'pointer',
  },
  label: {
    color: '#8888a0',
    fontSize: 11,
    fontWeight: 600,
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  row: {
    display: 'flex',
    gap: 12,
  },
  typeGrid: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: 6,
  },
  typeBtn: {
    background: 'rgba(255, 255, 255, 0.04)',
    border: '1px solid rgba(255, 255, 255, 0.08)',
    borderRadius: 8,
    color: '#8888a0',
    padding: '6px 16px',
    fontSize: 12,
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
    textTransform: 'capitalize',
    transition: 'all 0.15s',
  },
  typeBtnActive: {
    borderColor: 'rgba(0, 240, 255, 0.3)',
    color: '#00f0ff',
    background: 'rgba(0, 240, 255, 0.06)',
  },
  createBtn: {
    background: 'linear-gradient(135deg, #8A2BE2, #6a1fb0)',
    border: 'none',
    borderRadius: 8,
    color: '#fff',
    padding: '12px 0',
    fontSize: 14,
    fontWeight: 600,
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
    width: '100%',
    marginTop: 4,
  },
  librarySection: {
    display: 'flex',
    flexDirection: 'column',
    gap: 10,
  },
  mediaDirHint: {
    color: '#4a4a62',
    fontSize: 11,
    fontFamily: "'JetBrains Mono', monospace",
  },
  mediaGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
    gap: 10,
  },
  mediaCard: {
    background: 'rgba(255, 255, 255, 0.03)',
    border: '1px solid rgba(255, 255, 255, 0.07)',
    borderRadius: 10,
    overflow: 'hidden',
    cursor: 'pointer',
    transition: 'border-color 0.15s',
  },
  mediaCardActive: {
    borderColor: 'rgba(0, 240, 255, 0.3)',
  },
  mediaThumbnail: {
    width: '100%',
    height: 100,
    backgroundSize: 'cover',
    backgroundPosition: 'center',
    backgroundColor: 'rgba(255, 255, 255, 0.02)',
  },
  mediaPlaceholder: {
    width: '100%',
    height: 100,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: 28,
    background: 'rgba(255, 255, 255, 0.02)',
  },
  mediaInfo: {
    padding: '10px 12px',
  },
  mediaName: {
    color: '#F8FAFC',
    fontSize: 12,
    fontWeight: 500,
    fontFamily: "'Inter', system-ui, sans-serif",
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
    marginBottom: 2,
  },
  mediaMeta: {
    color: '#8888a0',
    fontSize: 11,
    fontFamily: "'JetBrains Mono', monospace",
  },
  mediaType: {
    textTransform: 'capitalize',
  },
  empty: {
    color: '#4a4a62',
    fontSize: 13,
    textAlign: 'center',
    padding: '40px 0',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  playerBar: {
    background: 'rgba(0, 240, 255, 0.04)',
    border: '1px solid rgba(0, 240, 255, 0.15)',
    borderRadius: 10,
    padding: '12px 16px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 12,
  },
  playerInfo: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    minWidth: 0,
    flex: 1,
  },
  playerName: {
    color: '#F8FAFC',
    fontSize: 13,
    fontWeight: 500,
    fontFamily: "'Inter', system-ui, sans-serif",
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  playerType: {
    color: '#8888a0',
    fontSize: 11,
    fontFamily: "'JetBrains Mono', monospace",
    textTransform: 'capitalize',
    flexShrink: 0,
  },
  playerControls: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    flexShrink: 0,
  },
  playBtn: {
    background: 'rgba(0, 240, 255, 0.12)',
    border: '1px solid rgba(0, 240, 255, 0.25)',
    borderRadius: 8,
    color: '#00f0ff',
    padding: '6px 18px',
    fontSize: 13,
    fontWeight: 500,
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  previewHint: {
    color: '#8888a0',
    fontSize: 12,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  refreshBtn: {
    background: 'none',
    border: '1px solid rgba(255, 255, 255, 0.07)',
    borderRadius: 8,
    color: '#8888a0',
    padding: '8px 16px',
    fontSize: 12,
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
    alignSelf: 'center',
  },
};
