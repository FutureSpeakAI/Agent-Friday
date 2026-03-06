/**
 * FridayWeather.tsx — Weather app for Agent Friday
 *
 * IPC: window.eve.weather?.getCurrent(), window.eve.weather?.getForecast()
 * Falls back to hardcoded mock data if backend not available.
 */

import React, { useState, useEffect, useCallback } from 'react';
import AppShell from '../AppShell';

interface WeatherProps {
  visible: boolean;
  onClose: () => void;
}

interface CurrentWeather {
  temp: number;
  condition: string;
  humidity: number;
  wind: number;
  location: string;
}

interface ForecastDay {
  day: string;
  high: number;
  low: number;
  condition: string;
}

const CONDITION_ICONS: Record<string, string> = {
  sunny: '☀️', clear: '☀️',
  'partly cloudy': '⛅', cloudy: '☁️',
  rain: '🌧️', drizzle: '🌦️',
  thunderstorm: '⛈️', snow: '🌨️',
  fog: '🌫️', windy: '💨',
  default: '🌤️',
};

function getConditionIcon(condition: string): string {
  const key = condition.toLowerCase();
  return CONDITION_ICONS[key] || CONDITION_ICONS.default;
}

const MOCK_CURRENT: CurrentWeather = {
  temp: 72,
  condition: 'Partly Cloudy',
  humidity: 45,
  wind: 8,
  location: 'Not configured',
};

const MOCK_FORECAST: ForecastDay[] = [
  { day: 'Mon', high: 74, low: 58, condition: 'Sunny' },
  { day: 'Tue', high: 71, low: 55, condition: 'Partly Cloudy' },
  { day: 'Wed', high: 68, low: 52, condition: 'Rain' },
  { day: 'Thu', high: 65, low: 50, condition: 'Cloudy' },
  { day: 'Fri', high: 70, low: 54, condition: 'Sunny' },
];

export default function FridayWeather({ visible, onClose }: WeatherProps) {
  const [current, setCurrent] = useState<CurrentWeather | null>(null);
  const [forecast, setForecast] = useState<ForecastDay[]>([]);
  const [loading, setLoading] = useState(true);
  const [usingMock, setUsingMock] = useState(false);

  const fetchWeather = useCallback(async () => {
    setLoading(true);
    let usedMock = false;

    try {
      const cur = await (window as any).eve?.weather?.getCurrent();
      if (cur && cur.temp !== undefined) {
        setCurrent(cur);
      } else {
        setCurrent(MOCK_CURRENT);
        usedMock = true;
      }
    } catch {
      setCurrent(MOCK_CURRENT);
      usedMock = true;
    }

    try {
      const fc = await (window as any).eve?.weather?.getForecast();
      if (Array.isArray(fc) && fc.length > 0) {
        setForecast(fc);
      } else {
        setForecast(MOCK_FORECAST);
        usedMock = true;
      }
    } catch {
      setForecast(MOCK_FORECAST);
      usedMock = true;
    }

    setUsingMock(usedMock);
    setLoading(false);
  }, []);

  useEffect(() => {
    if (visible) fetchWeather();
  }, [visible, fetchWeather]);

  return (
    <AppShell visible={visible} onClose={onClose} title="Weather" icon="🌤️" width={560}>
      {loading ? (
        <div style={s.center}>
          <span style={s.loadingText}>Fetching weather...</span>
        </div>
      ) : (
        <>
          {/* Mock data notice */}
          {usingMock && (
            <div style={s.mockNotice}>
              <span>⚙️</span>
              <span>
                Showing sample data. Set your location in Settings to get live weather.
              </span>
            </div>
          )}

          {/* Current Conditions */}
          {current && (
            <div style={s.currentCard}>
              <div style={s.currentMain}>
                <span style={s.currentIcon}>{getConditionIcon(current.condition)}</span>
                <div style={s.currentTemp}>{current.temp}°</div>
              </div>
              <div style={s.currentDetails}>
                <div style={s.conditionText}>{current.condition}</div>
                <div style={s.locationText}>{current.location}</div>
                <div style={s.detailsRow}>
                  <div style={s.detailItem}>
                    <span style={s.detailIcon}>💧</span>
                    <div>
                      <div style={s.detailValue}>{current.humidity}%</div>
                      <div style={s.detailLabel}>Humidity</div>
                    </div>
                  </div>
                  <div style={s.detailItem}>
                    <span style={s.detailIcon}>💨</span>
                    <div>
                      <div style={s.detailValue}>{current.wind} mph</div>
                      <div style={s.detailLabel}>Wind</div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* 5-Day Forecast */}
          <div style={s.sectionLabel}>5-Day Forecast</div>
          <div style={s.forecastRow}>
            {forecast.map((day, i) => (
              <div
                key={`${day.day}-${i}`}
                style={s.forecastCard}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLElement).style.borderColor = 'rgba(0,240,255,0.3)';
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLElement).style.borderColor = 'rgba(255,255,255,0.07)';
                }}
              >
                <div style={s.fcDay}>{day.day}</div>
                <div style={s.fcIcon}>{getConditionIcon(day.condition)}</div>
                <div style={s.fcCondition}>{day.condition}</div>
                <div style={s.fcTemps}>
                  <span style={s.fcHigh}>{day.high}°</span>
                  <span style={s.fcLow}>{day.low}°</span>
                </div>
              </div>
            ))}
          </div>

          {/* Refresh */}
          <button
            style={s.refreshBtn}
            onClick={fetchWeather}
            onMouseEnter={(e) => {
              (e.target as HTMLElement).style.background = 'rgba(0,240,255,0.12)';
            }}
            onMouseLeave={(e) => {
              (e.target as HTMLElement).style.background = 'rgba(255,255,255,0.03)';
            }}
          >
            ↻ Refresh
          </button>
        </>
      )}
    </AppShell>
  );
}

/* ── Styles ───────────────────────────────────────────────── */
const s: Record<string, React.CSSProperties> = {
  center: {
    display: 'flex', justifyContent: 'center', padding: 40,
  },
  loadingText: { color: '#8888a0', fontSize: 13 },
  mockNotice: {
    display: 'flex', alignItems: 'center', gap: 8,
    padding: '10px 14px',
    background: 'rgba(249,115,22,0.08)',
    border: '1px solid rgba(249,115,22,0.2)',
    borderRadius: 10, color: '#f97316', fontSize: 12,
  },
  currentCard: {
    display: 'flex', gap: 24,
    padding: 24,
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 16,
    alignItems: 'center',
  },
  currentMain: {
    display: 'flex', flexDirection: 'column',
    alignItems: 'center', gap: 4, minWidth: 120,
  },
  currentIcon: { fontSize: 56 },
  currentTemp: {
    fontSize: 48, fontWeight: 700, color: '#F8FAFC',
    fontFamily: "'Inter', system-ui, sans-serif",
    lineHeight: 1,
  },
  currentDetails: {
    flex: 1, display: 'flex', flexDirection: 'column', gap: 8,
  },
  conditionText: {
    fontSize: 18, fontWeight: 600, color: '#F8FAFC',
  },
  locationText: {
    fontSize: 13, color: '#8888a0',
  },
  detailsRow: {
    display: 'flex', gap: 20, marginTop: 8,
  },
  detailItem: {
    display: 'flex', alignItems: 'center', gap: 8,
  },
  detailIcon: { fontSize: 20 },
  detailValue: {
    color: '#F8FAFC', fontSize: 14, fontWeight: 600,
    fontFamily: "'JetBrains Mono', monospace",
  },
  detailLabel: { color: '#4a4a62', fontSize: 11 },
  sectionLabel: {
    fontSize: 12, fontWeight: 600, color: '#8888a0',
    textTransform: 'uppercase' as const,
    letterSpacing: '0.05em',
  },
  forecastRow: {
    display: 'grid',
    gridTemplateColumns: 'repeat(5, 1fr)',
    gap: 10,
  },
  forecastCard: {
    display: 'flex', flexDirection: 'column',
    alignItems: 'center', gap: 6,
    padding: '14px 8px',
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 12,
    transition: 'border-color 0.2s',
  },
  fcDay: {
    color: '#8888a0', fontSize: 12, fontWeight: 600,
    textTransform: 'uppercase' as const,
  },
  fcIcon: { fontSize: 28 },
  fcCondition: { color: '#F8FAFC', fontSize: 11, textAlign: 'center' },
  fcTemps: {
    display: 'flex', gap: 8, marginTop: 2,
  },
  fcHigh: {
    color: '#F8FAFC', fontSize: 14, fontWeight: 600,
    fontFamily: "'JetBrains Mono', monospace",
  },
  fcLow: {
    color: '#4a4a62', fontSize: 14,
    fontFamily: "'JetBrains Mono', monospace",
  },
  refreshBtn: {
    alignSelf: 'center',
    padding: '8px 20px',
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 8, color: '#00f0ff',
    fontSize: 13, cursor: 'pointer',
    transition: 'background 0.15s',
  },
};
