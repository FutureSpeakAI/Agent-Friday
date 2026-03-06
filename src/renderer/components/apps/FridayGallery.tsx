/**
 * FridayGallery.tsx — Gallery app for Agent Friday
 *
 * IPC: window.eve.multimedia.listMedia(type?), window.eve.multimedia.getMediaDir()
 * Displays photo/video/audio grid with lightbox view.
 */

import React, { useState, useEffect, useCallback } from 'react';
import AppShell from '../AppShell';

interface GalleryProps {
  visible: boolean;
  onClose: () => void;
}

interface MediaItem {
  path: string;
  name: string;
  type: 'image' | 'video' | 'audio';
  createdAt: string;
}

type FilterTab = 'all' | 'image' | 'video' | 'audio';

const TABS: { key: FilterTab; label: string; icon: string }[] = [
  { key: 'all', label: 'All', icon: '📂' },
  { key: 'image', label: 'Images', icon: '🖼️' },
  { key: 'video', label: 'Video', icon: '🎬' },
  { key: 'audio', label: 'Audio', icon: '🎵' },
];

function getThumbIcon(type: string): string {
  if (type === 'image') return '🖼️';
  if (type === 'video') return '🎬';
  return '🎵';
}

function formatDate(d: string): string {
  try {
    return new Date(d).toLocaleDateString(undefined, {
      month: 'short', day: 'numeric', year: 'numeric',
    });
  } catch {
    return d;
  }
}

export default function FridayGallery({ visible, onClose }: GalleryProps) {
  const [items, setItems] = useState<MediaItem[]>([]);
  const [filter, setFilter] = useState<FilterTab>('all');
  const [mediaDir, setMediaDir] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lightbox, setLightbox] = useState<MediaItem | null>(null);

  const fetchMedia = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const dir = await (window as any).eve?.multimedia?.getMediaDir();
      if (dir) setMediaDir(dir);

      const result = await (window as any).eve?.multimedia?.listMedia(
        filter === 'all' ? undefined : filter
      );
      if (Array.isArray(result)) {
        setItems(result);
      } else {
        setError('Backend not available');
        setItems([]);
      }
    } catch {
      setError('Multimedia backend not available — media indexing requires the Electron backend');
      setItems([]);
    }
    setLoading(false);
  }, [filter]);

  useEffect(() => {
    if (visible) fetchMedia();
  }, [visible, fetchMedia]);

  const filtered = filter === 'all' ? items : items.filter((m) => m.type === filter);

  return (
    <AppShell visible={visible} onClose={onClose} title="Gallery" icon="🖼️" width={880}>
      {/* Filter Tabs */}
      <div style={s.tabRow}>
        {TABS.map((tab) => (
          <button
            key={tab.key}
            style={{
              ...s.tab,
              ...(filter === tab.key ? s.tabActive : {}),
            }}
            onClick={() => setFilter(tab.key)}
          >
            <span>{tab.icon}</span>
            <span>{tab.label}</span>
          </button>
        ))}
      </div>

      {/* Media Directory */}
      {mediaDir && (
        <div style={s.dirBar}>
          <span style={s.dirLabel}>Media directory:</span>
          <span style={s.dirPath}>{mediaDir}</span>
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div style={s.center}>
          <span style={s.loadingText}>Scanning media files...</span>
        </div>
      )}

      {/* Error */}
      {error && !loading && (
        <div style={s.errorBox}>
          <div style={s.errorIcon}>🖼️</div>
          <div style={s.errorTitle}>Gallery Unavailable</div>
          <div style={s.errorMsg}>{error}</div>
        </div>
      )}

      {/* Grid */}
      {!loading && !error && (
        <>
          {filtered.length === 0 ? (
            <div style={s.empty}>
              <div style={{ fontSize: 36 }}>📭</div>
              <div style={s.emptyText}>No media files found</div>
              <div style={s.emptyHint}>
                Add images, videos, or audio files to your media directory
              </div>
            </div>
          ) : (
            <div style={s.grid}>
              {filtered.map((item, i) => (
                <div
                  key={`${item.path}-${i}`}
                  style={s.card}
                  onClick={() => setLightbox(item)}
                  onMouseEnter={(e) => {
                    (e.currentTarget as HTMLElement).style.borderColor = 'rgba(0,240,255,0.3)';
                    (e.currentTarget as HTMLElement).style.transform = 'translateY(-2px)';
                  }}
                  onMouseLeave={(e) => {
                    (e.currentTarget as HTMLElement).style.borderColor = 'rgba(255,255,255,0.07)';
                    (e.currentTarget as HTMLElement).style.transform = 'none';
                  }}
                >
                  <div style={s.thumbArea}>
                    {item.type === 'image' ? (
                      <img
                        src={`file://${item.path}`}
                        alt={item.name}
                        style={s.thumbImg}
                        onError={(e) => {
                          (e.target as HTMLImageElement).style.display = 'none';
                          const parent = (e.target as HTMLElement).parentElement;
                          if (parent) {
                            const fallback = document.createElement('span');
                            fallback.textContent = '🖼️';
                            fallback.style.fontSize = '32px';
                            parent.appendChild(fallback);
                          }
                        }}
                      />
                    ) : (
                      <span style={s.thumbIcon}>{getThumbIcon(item.type)}</span>
                    )}
                    <span style={s.typeBadge}>{item.type.toUpperCase()}</span>
                  </div>
                  <div style={s.cardInfo}>
                    <div style={s.cardName} title={item.name}>{item.name}</div>
                    <div style={s.cardDate}>{formatDate(item.createdAt)}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
          <div style={s.countBar}>
            {filtered.length} item{filtered.length !== 1 ? 's' : ''}
          </div>
        </>
      )}

      {/* Lightbox */}
      {lightbox && (
        <div style={s.lightbox} onClick={() => setLightbox(null)}>
          <div style={s.lbContent} onClick={(e) => e.stopPropagation()}>
            <button style={s.lbClose} onClick={() => setLightbox(null)}>✕</button>
            <div style={s.lbPreview}>
              {lightbox.type === 'image' ? (
                <img
                  src={`file://${lightbox.path}`}
                  alt={lightbox.name}
                  style={s.lbImage}
                />
              ) : lightbox.type === 'video' ? (
                <video src={`file://${lightbox.path}`} controls style={s.lbVideo} />
              ) : (
                <div style={s.lbAudio}>
                  <span style={{ fontSize: 64 }}>🎵</span>
                  <audio src={`file://${lightbox.path}`} controls style={{ width: '100%' }} />
                </div>
              )}
            </div>
            <div style={s.lbMeta}>
              <div style={s.lbName}>{lightbox.name}</div>
              <div style={s.lbPath}>{lightbox.path}</div>
              <div style={s.lbDate}>{formatDate(lightbox.createdAt)}</div>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}

/* ── Styles ───────────────────────────────────────────────── */
const s: Record<string, React.CSSProperties> = {
  tabRow: { display: 'flex', gap: 6 },
  tab: {
    display: 'flex', alignItems: 'center', gap: 6,
    padding: '8px 16px',
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 8, color: '#8888a0', fontSize: 13,
    cursor: 'pointer', transition: 'all 0.15s',
  },
  tabActive: {
    background: 'rgba(0,240,255,0.08)',
    border: '1px solid rgba(0,240,255,0.3)',
    color: '#00f0ff',
  },
  dirBar: {
    display: 'flex', gap: 8, alignItems: 'center',
    padding: '6px 12px',
    background: 'rgba(0,0,0,0.3)', borderRadius: 6,
  },
  dirLabel: { color: '#8888a0', fontSize: 11 },
  dirPath: { color: '#4a4a62', fontSize: 11, fontFamily: "'JetBrains Mono', monospace" },
  center: { display: 'flex', justifyContent: 'center', padding: 40 },
  loadingText: { color: '#8888a0', fontSize: 13 },
  errorBox: {
    textAlign: 'center', padding: 32,
    background: 'rgba(239,68,68,0.06)',
    border: '1px solid rgba(239,68,68,0.2)',
    borderRadius: 12,
  },
  errorIcon: { fontSize: 36, marginBottom: 8 },
  errorTitle: { color: '#ef4444', fontSize: 16, fontWeight: 700, marginBottom: 6 },
  errorMsg: { color: '#8888a0', fontSize: 13, lineHeight: 1.5 },
  empty: {
    textAlign: 'center', padding: 40,
    display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8,
  },
  emptyText: { color: '#8888a0', fontSize: 14, fontWeight: 600 },
  emptyHint: { color: '#4a4a62', fontSize: 12 },
  grid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))',
    gap: 12,
  },
  card: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 10, overflow: 'hidden',
    cursor: 'pointer', transition: 'all 0.2s',
  },
  thumbArea: {
    height: 120, display: 'flex',
    alignItems: 'center', justifyContent: 'center',
    background: 'rgba(0,0,0,0.3)', position: 'relative',
    overflow: 'hidden',
  },
  thumbImg: {
    width: '100%', height: '100%',
    objectFit: 'cover',
  },
  thumbIcon: { fontSize: 36 },
  typeBadge: {
    position: 'absolute', top: 6, right: 6,
    padding: '2px 6px', borderRadius: 4,
    background: 'rgba(0,0,0,0.6)', color: '#00f0ff',
    fontSize: 9, fontWeight: 700, letterSpacing: '0.05em',
  },
  cardInfo: { padding: '8px 10px' },
  cardName: {
    color: '#F8FAFC', fontSize: 12, fontWeight: 500,
    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
  },
  cardDate: { color: '#4a4a62', fontSize: 10, marginTop: 2 },
  countBar: {
    color: '#4a4a62', fontSize: 12, textAlign: 'center', paddingTop: 4,
  },
  /* Lightbox */
  lightbox: {
    position: 'fixed', inset: 0, zIndex: 200,
    background: 'rgba(0,0,0,0.85)',
    backdropFilter: 'blur(16px)',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
  },
  lbContent: {
    maxWidth: '90vw', maxHeight: '90vh',
    display: 'flex', flexDirection: 'column',
    background: 'rgba(12,12,20,0.98)',
    border: '1px solid rgba(255,255,255,0.08)',
    borderRadius: 16, overflow: 'hidden',
    position: 'relative',
  },
  lbClose: {
    position: 'absolute', top: 10, right: 10, zIndex: 10,
    background: 'rgba(0,0,0,0.5)', border: 'none',
    color: '#F8FAFC', fontSize: 16, cursor: 'pointer',
    width: 32, height: 32, borderRadius: 8,
    display: 'flex', alignItems: 'center', justifyContent: 'center',
  },
  lbPreview: {
    maxHeight: '60vh', display: 'flex',
    alignItems: 'center', justifyContent: 'center',
    background: 'rgba(0,0,0,0.4)', minHeight: 200,
  },
  lbImage: { maxWidth: '100%', maxHeight: '60vh', objectFit: 'contain' },
  lbVideo: { maxWidth: '100%', maxHeight: '60vh' },
  lbAudio: {
    display: 'flex', flexDirection: 'column',
    alignItems: 'center', gap: 20, padding: 40,
  },
  lbMeta: { padding: 16 },
  lbName: { color: '#F8FAFC', fontSize: 15, fontWeight: 600 },
  lbPath: {
    color: '#4a4a62', fontSize: 11, marginTop: 4,
    fontFamily: "'JetBrains Mono', monospace",
    wordBreak: 'break-all',
  },
  lbDate: { color: '#8888a0', fontSize: 12, marginTop: 4 },
};
