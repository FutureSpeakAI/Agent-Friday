/**
 * IPC handlers for weather data — current conditions and forecast.
 *
 * Exposes weather data to the renderer process via eve.weather namespace.
 */

import { ipcMain } from 'electron';
import { weather } from '../weather';
import { assertNumber, assertString } from './validate';

export function registerWeatherHandlers(): void {
  ipcMain.handle('weather:current', async () => {
    return weather.getCurrent();
  });

  ipcMain.handle('weather:forecast', async () => {
    return weather.getForecast();
  });

  ipcMain.handle(
    'weather:set-location',
    async (_event, lat: unknown, lon: unknown, city: unknown, region?: unknown) => {
      assertNumber(lat, 'weather:set-location lat', -90, 90);
      assertNumber(lon, 'weather:set-location lon', -180, 180);
      assertString(city, 'weather:set-location city', 200);
      if (region !== undefined && region !== null) {
        assertString(region, 'weather:set-location region', 200);
      }
      return weather.setLocation(
        lat as number,
        lon as number,
        city as string,
        region as string | undefined,
      );
    },
  );
}
