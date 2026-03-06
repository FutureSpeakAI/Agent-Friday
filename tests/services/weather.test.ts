/**
 * weather.ts — Unit tests for weather data provider.
 *
 * Tests API response parsing, caching, geolocation fallback, WMO code
 * mapping, and location override. Mocks all HTTP requests and file I/O.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';

// ── Mocks ──────────────────────────────────────────────────────────

const mocks = vi.hoisted(() => ({
  readFile: vi.fn(),
  writeFile: vi.fn(),
  fetch: vi.fn(),
}));

vi.mock('fs/promises', () => ({
  default: {
    readFile: mocks.readFile,
    writeFile: mocks.writeFile,
  },
  readFile: mocks.readFile,
  writeFile: mocks.writeFile,
}));

vi.mock('electron', () => ({
  app: { getPath: () => '/mock/userData' },
}));

// Mock global fetch
const originalFetch = globalThis.fetch;

// ── Helpers ────────────────────────────────────────────────────────

const MOCK_GEOLOCATION = {
  latitude: 34.0522,
  longitude: -118.2437,
  city: 'Los Angeles',
  region: 'California',
};

const MOCK_WEATHER_API = {
  current: {
    temperature_2m: 72.4,
    relative_humidity_2m: 45,
    wind_speed_10m: 8.3,
    weather_code: 2,
  },
  daily: {
    time: ['2024-06-01', '2024-06-02', '2024-06-03', '2024-06-04', '2024-06-05'],
    weather_code: [0, 2, 61, 3, 1],
    temperature_2m_max: [75, 78, 65, 70, 80],
    temperature_2m_min: [58, 60, 52, 55, 62],
  },
};

function mockFetchSuccess(geoData: unknown, weatherData: unknown): void {
  let callCount = 0;
  globalThis.fetch = vi.fn(async (url: string | URL | Request) => {
    const urlStr = typeof url === 'string' ? url : url.toString();
    if (urlStr.includes('ipapi.co')) {
      return { ok: true, json: async () => geoData } as Response;
    }
    if (urlStr.includes('open-meteo.com')) {
      return { ok: true, json: async () => weatherData } as Response;
    }
    return { ok: false } as Response;
  }) as any;
}

function mockFetchGeoFail(weatherData: unknown): void {
  globalThis.fetch = vi.fn(async (url: string | URL | Request) => {
    const urlStr = typeof url === 'string' ? url : url.toString();
    if (urlStr.includes('ipapi.co')) {
      throw new Error('Network error');
    }
    if (urlStr.includes('open-meteo.com')) {
      return { ok: true, json: async () => weatherData } as Response;
    }
    return { ok: false } as Response;
  }) as any;
}

// ── Tests ──────────────────────────────────────────────────────────

let weather: typeof import('../../src/main/weather').weather;

describe('weather.getCurrent', () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    vi.resetModules();
    globalThis.fetch = originalFetch;
    // No saved settings by default
    mocks.readFile.mockRejectedValue(new Error('ENOENT'));
    mocks.writeFile.mockResolvedValue(undefined);
    const mod = await import('../../src/main/weather');
    weather = mod.weather;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it('returns current weather with parsed temperature and condition', async () => {
    mockFetchSuccess(MOCK_GEOLOCATION, MOCK_WEATHER_API);

    const current = await weather.getCurrent();
    expect(current.temp).toBe(72);
    expect(current.condition).toBe('Partly Cloudy'); // WMO code 2
    expect(current.humidity).toBe(45);
    expect(current.wind).toBe(8);
    expect(current.location).toBe('Los Angeles, California');
  });

  it('falls back to NYC when geolocation fails and no settings', async () => {
    mockFetchGeoFail(MOCK_WEATHER_API);

    const current = await weather.getCurrent();
    // Should still succeed using fallback NYC coordinates
    expect(current).toBeDefined();
    expect(current.temp).toBe(72);
    expect(current.location).toBe('New York, NY');
  });

  it('uses saved location settings when available', async () => {
    mocks.readFile.mockResolvedValue(JSON.stringify({
      latitude: 51.5074,
      longitude: -0.1278,
      city: 'London',
      region: 'England',
    }));
    mockFetchSuccess(null, MOCK_WEATHER_API);

    const current = await weather.getCurrent();
    expect(current.location).toBe('London, England');
  });

  it('returns cached data on subsequent calls within TTL', async () => {
    mockFetchSuccess(MOCK_GEOLOCATION, MOCK_WEATHER_API);

    const first = await weather.getCurrent();
    const second = await weather.getCurrent();

    expect(first).toEqual(second);
    // fetch should only be called for the first request (geo + weather)
    expect(globalThis.fetch).toHaveBeenCalledTimes(2); // ipapi + open-meteo
  });
});

describe('weather.getForecast', () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    vi.resetModules();
    globalThis.fetch = originalFetch;
    mocks.readFile.mockRejectedValue(new Error('ENOENT'));
    mocks.writeFile.mockResolvedValue(undefined);
    const mod = await import('../../src/main/weather');
    weather = mod.weather;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it('returns 5-day forecast with parsed conditions', async () => {
    mockFetchSuccess(MOCK_GEOLOCATION, MOCK_WEATHER_API);

    const forecast = await weather.getForecast();
    expect(forecast).toHaveLength(5);

    expect(forecast[0].high).toBe(75);
    expect(forecast[0].low).toBe(58);
    expect(forecast[0].condition).toBe('Clear');       // WMO code 0

    expect(forecast[2].condition).toBe('Rain');          // WMO code 61
    expect(forecast[4].condition).toBe('Clear');         // WMO code 1
  });

  it('forecast days include day-of-week names', async () => {
    mockFetchSuccess(MOCK_GEOLOCATION, MOCK_WEATHER_API);

    const forecast = await weather.getForecast();
    // Each day should be a 3-letter day name
    for (const day of forecast) {
      expect(['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']).toContain(day.day);
    }
  });
});

describe('weather.setLocation', () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    vi.resetModules();
    globalThis.fetch = originalFetch;
    mocks.readFile.mockRejectedValue(new Error('ENOENT'));
    mocks.writeFile.mockResolvedValue(undefined);
    const mod = await import('../../src/main/weather');
    weather = mod.weather;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it('saves location to disk and invalidates cache', async () => {
    await weather.setLocation(48.8566, 2.3522, 'Paris', 'Ile-de-France');

    expect(mocks.writeFile).toHaveBeenCalled();
    const written = JSON.parse(mocks.writeFile.mock.calls[0][1]);
    expect(written.latitude).toBe(48.8566);
    expect(written.longitude).toBe(2.3522);
    expect(written.city).toBe('Paris');
    expect(written.region).toBe('Ile-de-France');
  });

  it('uses the new location for subsequent weather fetches', async () => {
    await weather.setLocation(48.8566, 2.3522, 'Paris', 'Ile-de-France');

    mockFetchSuccess(null, MOCK_WEATHER_API);

    const current = await weather.getCurrent();
    expect(current.location).toBe('Paris, Ile-de-France');

    // Verify the API was called with Paris coordinates
    const fetchCalls = (globalThis.fetch as any).mock.calls;
    const weatherCall = fetchCalls.find((c: any) =>
      c[0].includes('open-meteo.com'),
    );
    expect(weatherCall[0]).toContain('48.8566');
    expect(weatherCall[0]).toContain('2.3522');
  });

  it('sets empty region when not provided', async () => {
    await weather.setLocation(35.6762, 139.6503, 'Tokyo');

    const written = JSON.parse(mocks.writeFile.mock.calls[0][1]);
    expect(written.region).toBe('');
  });
});

describe('WMO condition mapping', () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    vi.resetModules();
    globalThis.fetch = originalFetch;
    mocks.readFile.mockRejectedValue(new Error('ENOENT'));
    mocks.writeFile.mockResolvedValue(undefined);
    const mod = await import('../../src/main/weather');
    weather = mod.weather;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it('maps fog codes correctly', async () => {
    const data = {
      ...MOCK_WEATHER_API,
      current: { ...MOCK_WEATHER_API.current, weather_code: 45 },
    };
    mockFetchSuccess(MOCK_GEOLOCATION, data);
    const current = await weather.getCurrent();
    expect(current.condition).toBe('Fog');
  });

  it('maps thunderstorm codes correctly', async () => {
    const data = {
      ...MOCK_WEATHER_API,
      current: { ...MOCK_WEATHER_API.current, weather_code: 95 },
    };
    mockFetchSuccess(MOCK_GEOLOCATION, data);
    const current = await weather.getCurrent();
    expect(current.condition).toBe('Thunderstorm');
  });

  it('maps snow codes correctly', async () => {
    const data = {
      ...MOCK_WEATHER_API,
      current: { ...MOCK_WEATHER_API.current, weather_code: 73 },
    };
    mockFetchSuccess(MOCK_GEOLOCATION, data);
    const current = await weather.getCurrent();
    expect(current.condition).toBe('Snow');
  });

  it('defaults unknown codes to Clear', async () => {
    const data = {
      ...MOCK_WEATHER_API,
      current: { ...MOCK_WEATHER_API.current, weather_code: 999 },
    };
    mockFetchSuccess(MOCK_GEOLOCATION, data);
    const current = await weather.getCurrent();
    expect(current.condition).toBe('Clear');
  });
});
