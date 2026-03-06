/**
 * weather.ts — Weather data provider for Agent Friday.
 *
 * Uses Open-Meteo (free, no API key required) for weather data.
 * Location is auto-detected via IP geolocation on first use.
 * Supports manual lat/lon override via settings.
 *
 * Contract consumed by FridayWeather.tsx via eve.weather namespace.
 */

import { app } from 'electron';
import fs from 'fs/promises';
import path from 'path';

/* ── Types ────────────────────────────────────────────────────────────── */

export interface CurrentWeather {
  temp: number;
  condition: string;
  humidity: number;
  wind: number;
  location: string;
}

export interface ForecastDay {
  day: string;
  high: number;
  low: number;
  condition: string;
}

interface GeoLocation {
  latitude: number;
  longitude: number;
  city: string;
  region: string;
}

interface WeatherCache {
  current: CurrentWeather | null;
  forecast: ForecastDay[] | null;
  fetchedAt: number;
  location: GeoLocation | null;
}

/* ── Constants ────────────────────────────────────────────────────────── */

const CACHE_TTL_MS = 30 * 60 * 1000; // 30 minutes
const DAY_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

// WMO weather code → human-readable condition
const WMO_CONDITIONS: Record<number, string> = {
  0: 'Clear', 1: 'Clear', 2: 'Partly Cloudy', 3: 'Cloudy',
  45: 'Fog', 48: 'Fog',
  51: 'Drizzle', 53: 'Drizzle', 55: 'Drizzle',
  61: 'Rain', 63: 'Rain', 65: 'Rain',
  71: 'Snow', 73: 'Snow', 75: 'Snow',
  80: 'Rain', 81: 'Rain', 82: 'Rain',
  95: 'Thunderstorm', 96: 'Thunderstorm', 99: 'Thunderstorm',
};

/* ── State ────────────────────────────────────────────────────────────── */

let cache: WeatherCache = {
  current: null,
  forecast: null,
  fetchedAt: 0,
  location: null,
};

const SETTINGS_FILE = () =>
  path.join(app.getPath('userData'), 'weather-settings.json');

/* ── Helpers ──────────────────────────────────────────────────────────── */

async function loadLocation(): Promise<GeoLocation> {
  // Try stored settings first
  try {
    const raw = await fs.readFile(SETTINGS_FILE(), 'utf-8');
    const settings = JSON.parse(raw);
    if (settings.latitude && settings.longitude) {
      return settings as GeoLocation;
    }
  } catch { /* no settings file — auto-detect */ }

  // Auto-detect via IP geolocation (no API key needed)
  try {
    const resp = await fetch('https://ipapi.co/json/', {
      signal: AbortSignal.timeout(5000),
    });
    if (resp.ok) {
      const data = await resp.json();
      const loc: GeoLocation = {
        latitude: data.latitude,
        longitude: data.longitude,
        city: data.city || 'Unknown',
        region: data.region || '',
      };
      cache.location = loc;
      // Save for next time
      await fs.writeFile(SETTINGS_FILE(), JSON.stringify(loc, null, 2), 'utf-8').catch(() => {});
      return loc;
    }
  } catch { /* geolocation failed */ }

  // Default fallback: New York
  return { latitude: 40.7128, longitude: -74.006, city: 'New York', region: 'NY' };
}

function wmoToCondition(code: number): string {
  return WMO_CONDITIONS[code] ?? 'Clear';
}

function isCacheValid(): boolean {
  return Date.now() - cache.fetchedAt < CACHE_TTL_MS;
}

async function fetchFromApi(loc: GeoLocation): Promise<void> {
  const url =
    `https://api.open-meteo.com/v1/forecast` +
    `?latitude=${loc.latitude}&longitude=${loc.longitude}` +
    `&current=temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code` +
    `&daily=weather_code,temperature_2m_max,temperature_2m_min` +
    `&temperature_unit=fahrenheit&wind_speed_unit=mph` +
    `&timezone=auto&forecast_days=5`;

  const resp = await fetch(url, { signal: AbortSignal.timeout(10000) });
  if (!resp.ok) throw new Error(`Weather API returned ${resp.status}`);

  const data = await resp.json();

  // Parse current weather
  const cur = data.current;
  cache.current = {
    temp: Math.round(cur.temperature_2m),
    condition: wmoToCondition(cur.weather_code),
    humidity: Math.round(cur.relative_humidity_2m),
    wind: Math.round(cur.wind_speed_10m),
    location: loc.region ? `${loc.city}, ${loc.region}` : loc.city,
  };

  // Parse 5-day forecast
  const daily = data.daily;
  cache.forecast = [];
  for (let i = 0; i < daily.time.length; i++) {
    const date = new Date(daily.time[i] + 'T12:00:00');
    cache.forecast.push({
      day: DAY_NAMES[date.getDay()],
      high: Math.round(daily.temperature_2m_max[i]),
      low: Math.round(daily.temperature_2m_min[i]),
      condition: wmoToCondition(daily.weather_code[i]),
    });
  }

  cache.fetchedAt = Date.now();
}

/* ── Public API ───────────────────────────────────────────────────────── */

export const weather = {
  /** Get current weather conditions. Auto-fetches if cache is stale. */
  async getCurrent(): Promise<CurrentWeather> {
    if (!isCacheValid()) {
      const loc = cache.location ?? await loadLocation();
      await fetchFromApi(loc);
    }
    return cache.current!;
  },

  /** Get 5-day forecast. Auto-fetches if cache is stale. */
  async getForecast(): Promise<ForecastDay[]> {
    if (!isCacheValid()) {
      const loc = cache.location ?? await loadLocation();
      await fetchFromApi(loc);
    }
    return cache.forecast!;
  },

  /** Manually set location. */
  async setLocation(lat: number, lon: number, city: string, region?: string): Promise<void> {
    const loc: GeoLocation = { latitude: lat, longitude: lon, city, region: region ?? '' };
    cache.location = loc;
    cache.fetchedAt = 0; // Invalidate cache
    await fs.writeFile(SETTINGS_FILE(), JSON.stringify(loc, null, 2), 'utf-8');
  },
};
