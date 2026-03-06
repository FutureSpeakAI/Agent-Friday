/**
 * FridayMaps.tsx — Embedded map viewer for Agent Friday
 *
 * Uses OpenStreetMap iframe embed (no API key required).
 * Includes search bar, zoom controls, and open-in-browser link.
 */

import React, { useState, useCallback, useRef, useEffect } from 'react';
import AppShell from '../AppShell';

interface Props {
  visible: boolean;
  onClose: () => void;
}

interface MapLocation {
  name: string;
  lat: number;
  lon: number;
  zoom: number;
}

const DEFAULT_LOCATION: MapLocation = {
  name: 'World',
  lat: 20,
  lon: 0,
  zoom: 3,
};

const QUICK_LOCATIONS: MapLocation[] = [
  { name: 'New York', lat: 40.7128, lon: -74.006, zoom: 12 },
  { name: 'London', lat: 51.5074, lon: -0.1278, zoom: 12 },
  { name: 'Tokyo', lat: 35.6762, lon: 139.6503, zoom: 12 },
  { name: 'San Francisco', lat: 37.7749, lon: -122.4194, zoom: 13 },
  { name: 'Paris', lat: 48.8566, lon: 2.3522, zoom: 12 },
  { name: 'Sydney', lat: -33.8688, lon: 151.2093, zoom: 12 },
];

export default function FridayMaps({ visible, onClose }: Props) {
  const [location, setLocation] = useState<MapLocation>(DEFAULT_LOCATION);
  const [searchQuery, setSearchQuery] = useState('');
  const [isSearching, setIsSearching] = useState(false);
  const [searchError, setSearchError] = useState('');
  const [history, setHistory] = useState<MapLocation[]>([]);
  const iframeRef = useRef<HTMLIFrameElement>(null);

  // Build the OpenStreetMap embed URL
  const buildMapUrl = useCallback((loc: MapLocation): string => {
    const bbox = getBoundingBox(loc.lat, loc.lon, loc.zoom);
    return `https://www.openstreetmap.org/export/embed.html?bbox=${bbox}&layer=mapnik&marker=${loc.lat},${loc.lon}`;
  }, []);

  // Build full OpenStreetMap URL for "open in browser"
  const buildFullUrl = useCallback((loc: MapLocation): string => {
    return `https://www.openstreetmap.org/#map=${loc.zoom}/${loc.lat}/${loc.lon}`;
  }, []);

  // Calculate approximate bounding box from lat/lon/zoom
  function getBoundingBox(lat: number, lon: number, zoom: number): string {
    const scale = 360 / Math.pow(2, zoom);
    const lonMin = lon - scale / 2;
    const lonMax = lon + scale / 2;
    const latMin = lat - scale / 4;
    const latMax = lat + scale / 4;
    return `${lonMin.toFixed(4)},${latMin.toFixed(4)},${lonMax.toFixed(4)},${latMax.toFixed(4)}`;
  }

  // Search via Nominatim (free geocoding)
  const handleSearch = useCallback(async () => {
    const query = searchQuery.trim();
    if (!query) return;

    setIsSearching(true);
    setSearchError('');

    try {
      const response = await fetch(
        `https://nominatim.openstreetmap.org/search?format=json&q=${encodeURIComponent(query)}&limit=1`,
        {
          headers: {
            'Accept': 'application/json',
            'User-Agent': 'NexusOS-FridayMaps/1.0',
          },
        }
      );

      if (!response.ok) {
        throw new Error(`Search failed: ${response.status}`);
      }

      const results = await response.json();

      if (results.length === 0) {
        setSearchError(`No results found for "${query}"`);
        return;
      }

      const result = results[0];
      const newLoc: MapLocation = {
        name: result.display_name?.split(',')[0] || query,
        lat: parseFloat(result.lat),
        lon: parseFloat(result.lon),
        zoom: getZoomForType(result.type),
      };

      setHistory((prev) => [location, ...prev].slice(0, 10));
      setLocation(newLoc);
    } catch (err: any) {
      setSearchError(err.message || 'Search failed. Check your internet connection.');
    } finally {
      setIsSearching(false);
    }
  }, [searchQuery, location]);

  // Determine zoom level based on result type
  function getZoomForType(type: string): number {
    switch (type) {
      case 'country': return 5;
      case 'state':
      case 'region': return 7;
      case 'city':
      case 'town': return 12;
      case 'village':
      case 'suburb': return 14;
      case 'building':
      case 'house': return 17;
      default: return 13;
    }
  }

  // Zoom controls
  const zoomIn = useCallback(() => {
    setLocation((prev) => ({
      ...prev,
      zoom: Math.min(prev.zoom + 1, 18),
    }));
  }, []);

  const zoomOut = useCallback(() => {
    setLocation((prev) => ({
      ...prev,
      zoom: Math.max(prev.zoom - 1, 2),
    }));
  }, []);

  // Go to quick location
  const goToLocation = useCallback((loc: MapLocation) => {
    setHistory((prev) => [location, ...prev].slice(0, 10));
    setLocation(loc);
    setSearchQuery('');
    setSearchError('');
  }, [location]);

  // Go back in history
  const goBack = useCallback(() => {
    if (history.length === 0) return;
    const [prev, ...rest] = history;
    setLocation(prev);
    setHistory(rest);
  }, [history]);

  // Open in external browser
  const openInBrowser = useCallback(() => {
    const url = buildFullUrl(location);
    try {
      window.open(url, '_blank');
    } catch {
      // Fallback for Electron
      try {
        (window as any).eve?.shell?.openExternal?.(url);
      } catch {
        // silently fail
      }
    }
  }, [location, buildFullUrl]);

  // Search on Enter
  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleSearch();
    }
  }, [handleSearch]);

  // Reset on close
  useEffect(() => {
    if (!visible) {
      setSearchError('');
      setIsSearching(false);
    }
  }, [visible]);

  const mapUrl = buildMapUrl(location);

  return (
    <AppShell visible={visible} onClose={onClose} title="Maps" icon="🗺️" width={850} maxHeightVh={90}>
      {/* Search bar */}
      <div style={s.searchRow}>
        <div style={s.searchInputWrap}>
          <span style={s.searchIcon}>🔍</span>
          <input
            style={s.searchInput}
            value={searchQuery}
            onChange={(e) => { setSearchQuery(e.target.value); setSearchError(''); }}
            onKeyDown={handleKeyDown}
            placeholder="Search for a place..."
            disabled={isSearching}
          />
          {searchQuery && (
            <button
              style={s.clearBtn}
              onClick={() => { setSearchQuery(''); setSearchError(''); }}
            >
              ✕
            </button>
          )}
        </div>
        <button
          style={s.searchBtn}
          onClick={handleSearch}
          disabled={isSearching || !searchQuery.trim()}
        >
          {isSearching ? '...' : 'Search'}
        </button>
      </div>

      {searchError && (
        <div style={s.errorBanner}>
          <span style={{ color: '#f97316' }}>⚠</span> {searchError}
        </div>
      )}

      {/* Quick locations */}
      <div style={s.quickRow}>
        {QUICK_LOCATIONS.map((loc) => (
          <button
            key={loc.name}
            style={{
              ...s.quickBtn,
              color: location.name === loc.name ? '#00f0ff' : '#8888a0',
              borderColor: location.name === loc.name ? 'rgba(0,240,255,0.3)' : 'rgba(255,255,255,0.07)',
            }}
            onClick={() => goToLocation(loc)}
          >
            {loc.name}
          </button>
        ))}
      </div>

      {/* Map container */}
      <div style={s.mapContainer}>
        <iframe
          ref={iframeRef}
          src={mapUrl}
          style={s.mapIframe}
          title="OpenStreetMap"
          loading="lazy"
          referrerPolicy="no-referrer"
          sandbox="allow-scripts allow-same-origin"
        />

        {/* Zoom controls overlay */}
        <div style={s.zoomControls}>
          <button style={s.zoomBtn} onClick={zoomIn} title="Zoom in">
            +
          </button>
          <div style={s.zoomLevel}>{location.zoom}</div>
          <button style={s.zoomBtn} onClick={zoomOut} title="Zoom out">
            −
          </button>
        </div>
      </div>

      {/* Bottom bar */}
      <div style={s.bottomBar}>
        <div style={s.locationInfo}>
          <span style={s.locationName}>{location.name}</span>
          <span style={s.locationCoords}>
            {location.lat.toFixed(4)}, {location.lon.toFixed(4)}
          </span>
        </div>
        <div style={s.bottomActions}>
          {history.length > 0 && (
            <button style={s.actionBtn} onClick={goBack}>
              ← Back
            </button>
          )}
          <button style={s.actionBtn} onClick={() => goToLocation(DEFAULT_LOCATION)}>
            🌍 Reset
          </button>
          <button style={{ ...s.actionBtn, color: '#00f0ff', borderColor: 'rgba(0,240,255,0.3)' }} onClick={openInBrowser}>
            🔗 Open in Browser
          </button>
        </div>
      </div>
    </AppShell>
  );
}

// ── Styles ──────────────────────────────────────────────────────────────────
const s: Record<string, React.CSSProperties> = {
  searchRow: {
    display: 'flex',
    gap: 8,
    marginBottom: 8,
  },
  searchInputWrap: {
    flex: 1,
    position: 'relative',
    display: 'flex',
    alignItems: 'center',
  },
  searchIcon: {
    position: 'absolute',
    left: 12,
    fontSize: 14,
    pointerEvents: 'none',
    zIndex: 1,
  },
  searchInput: {
    width: '100%',
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 10,
    color: '#F8FAFC',
    fontSize: 13,
    padding: '10px 36px 10px 36px',
    fontFamily: "'Inter', system-ui, sans-serif",
    outline: 'none',
    transition: 'border-color 0.15s',
    boxSizing: 'border-box' as const,
  },
  clearBtn: {
    position: 'absolute',
    right: 8,
    background: 'none',
    border: 'none',
    color: '#8888a0',
    fontSize: 12,
    cursor: 'pointer',
    padding: '2px 6px',
  },
  searchBtn: {
    background: 'rgba(0,240,255,0.1)',
    border: '1px solid rgba(0,240,255,0.3)',
    borderRadius: 10,
    color: '#00f0ff',
    fontSize: 13,
    fontWeight: 600,
    padding: '0 20px',
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
    whiteSpace: 'nowrap',
    transition: 'background 0.15s',
  },
  errorBanner: {
    background: 'rgba(249,115,22,0.08)',
    border: '1px solid rgba(249,115,22,0.2)',
    borderRadius: 8,
    padding: '8px 12px',
    fontSize: 12,
    color: '#f97316',
    fontFamily: "'Inter', system-ui, sans-serif",
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    marginBottom: 8,
  },
  quickRow: {
    display: 'flex',
    gap: 6,
    flexWrap: 'wrap',
    marginBottom: 12,
  },
  quickBtn: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 16,
    color: '#8888a0',
    fontSize: 11,
    padding: '5px 12px',
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
    transition: 'background 0.12s, color 0.12s',
    whiteSpace: 'nowrap',
  },
  mapContainer: {
    position: 'relative',
    width: '100%',
    height: '55vh',
    minHeight: 300,
    borderRadius: 12,
    overflow: 'hidden',
    border: '1px solid rgba(255,255,255,0.07)',
    background: '#0a0a14',
  },
  mapIframe: {
    width: '100%',
    height: '100%',
    border: 'none',
    display: 'block',
  },
  zoomControls: {
    position: 'absolute',
    top: 12,
    right: 12,
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 2,
    zIndex: 5,
  },
  zoomBtn: {
    width: 36,
    height: 36,
    borderRadius: 8,
    background: 'rgba(12,12,20,0.9)',
    border: '1px solid rgba(255,255,255,0.12)',
    color: '#F8FAFC',
    fontSize: 20,
    fontWeight: 700,
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    backdropFilter: 'blur(8px)',
    transition: 'background 0.12s',
  },
  zoomLevel: {
    fontSize: 10,
    color: '#8888a0',
    fontFamily: "'JetBrains Mono', monospace",
    padding: '2px 0',
  },
  bottomBar: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingTop: 12,
    gap: 12,
    flexWrap: 'wrap',
  },
  locationInfo: {
    display: 'flex',
    flexDirection: 'column',
    gap: 2,
  },
  locationName: {
    fontSize: 14,
    fontWeight: 600,
    color: '#F8FAFC',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  locationCoords: {
    fontSize: 11,
    color: '#4a4a62',
    fontFamily: "'JetBrains Mono', monospace",
  },
  bottomActions: {
    display: 'flex',
    gap: 6,
  },
  actionBtn: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 8,
    color: '#8888a0',
    fontSize: 12,
    padding: '6px 14px',
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
    transition: 'background 0.12s',
    whiteSpace: 'nowrap',
  },
};
