/**
 * Production-aware logger for the main process.
 *
 * In development (app.isPackaged === false), all log levels are emitted.
 * In production (app.isPackaged === true), only warn and error are emitted.
 *
 * Usage:
 *   import { logger } from './utils/logger';
 *   logger.debug('Detailed info for devs only');
 *   logger.info('Noteworthy event');
 *   logger.warn('Something looks wrong');
 *   logger.error('Something broke', err);
 *
 * Migration guide: Replace `console.log(...)` with `logger.debug(...)` or
 * `logger.info(...)` incrementally. No need to change all 800+ call sites
 * at once — this utility is a foundation for gradual adoption.
 */
import { app } from 'electron';

type LogLevel = 'debug' | 'info' | 'warn' | 'error';

const LEVEL_PRIORITY: Record<LogLevel, number> = {
  debug: 0,
  info: 1,
  warn: 2,
  error: 3,
};

function getMinLevel(): LogLevel {
  return app.isPackaged ? 'warn' : 'debug';
}

function shouldLog(level: LogLevel): boolean {
  return LEVEL_PRIORITY[level] >= LEVEL_PRIORITY[getMinLevel()];
}

export const logger = {
  debug(...args: unknown[]): void {
    if (shouldLog('debug')) console.log('[DEBUG]', ...args);
  },
  info(...args: unknown[]): void {
    if (shouldLog('info')) console.log('[INFO]', ...args);
  },
  warn(...args: unknown[]): void {
    if (shouldLog('warn')) console.warn('[WARN]', ...args);
  },
  error(...args: unknown[]): void {
    if (shouldLog('error')) console.error('[ERROR]', ...args);
  },
};
