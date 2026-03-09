/**
 * Auto-Updater — checks GitHub Releases for new versions of Agent Friday.
 *
 * Uses electron-updater with conservative defaults:
 *   - autoDownload = false (prompt user before downloading)
 *   - autoInstallOnAppQuit = true (install silently on next quit)
 *   - Checks on launch + every 4 hours
 *   - All errors are logged but never crash the app (best-effort)
 *
 * Only active when app.isPackaged === true (skipped in dev).
 */
import { dialog, app } from 'electron';
import { autoUpdater } from 'electron-updater';
import { logger } from './utils/logger';

/** How often to re-check for updates (ms). 4 hours. */
const CHECK_INTERVAL_MS = 4 * 60 * 60 * 1000;

let checkTimer: ReturnType<typeof setInterval> | null = null;

function configureUpdater(): void {
  // Don't auto-download — let the user decide
  autoUpdater.autoDownload = false;

  // Install silently when the user eventually quits
  autoUpdater.autoInstallOnAppQuit = true;

  // ── Event: update available ───────────────────────────────────────
  autoUpdater.on('update-available', (info) => {
    logger.info(`[Updater] Update available: v${info.version}`);

    dialog
      .showMessageBox({
        type: 'info',
        title: 'Update Available',
        message: `A new version of Agent Friday (v${info.version}) is available.`,
        detail: 'Would you like to download it now? The update will be installed when you next quit the app.',
        buttons: ['Download', 'Later'],
        defaultId: 0,
        cancelId: 1,
      })
      .then((result) => {
        if (result.response === 0) {
          logger.info('[Updater] User accepted download');
          autoUpdater.downloadUpdate().catch((err) => {
            logger.error('[Updater] Download failed:', err instanceof Error ? err.message : String(err));
          });
        } else {
          logger.info('[Updater] User deferred update');
        }
      })
      .catch((err) => {
        logger.error('[Updater] Dialog error:', err instanceof Error ? err.message : String(err));
      });
  });

  // ── Event: no update available ────────────────────────────────────
  autoUpdater.on('update-not-available', () => {
    logger.info('[Updater] No update available — current version is latest');
  });

  // ── Event: download complete ──────────────────────────────────────
  autoUpdater.on('update-downloaded', (info) => {
    logger.info(`[Updater] Update downloaded: v${info.version}`);

    dialog
      .showMessageBox({
        type: 'info',
        title: 'Update Ready',
        message: `Agent Friday v${info.version} has been downloaded.`,
        detail: 'Would you like to restart now to apply the update?',
        buttons: ['Restart Now', 'Later'],
        defaultId: 0,
        cancelId: 1,
      })
      .then((result) => {
        if (result.response === 0) {
          logger.info('[Updater] User accepted restart — quitting and installing');
          autoUpdater.quitAndInstall();
        } else {
          logger.info('[Updater] User deferred restart — will install on next quit');
        }
      })
      .catch((err) => {
        logger.error('[Updater] Dialog error:', err instanceof Error ? err.message : String(err));
      });
  });

  // ── Event: error (best-effort — never crash) ─────────────────────
  autoUpdater.on('error', (err) => {
    logger.warn('[Updater] Error checking for updates:', err instanceof Error ? err.message : String(err));
  });
}

function checkForUpdates(): void {
  autoUpdater.checkForUpdates().catch((err) => {
    logger.warn('[Updater] Check failed:', err instanceof Error ? err.message : String(err));
  });
}

/**
 * Initialize the auto-updater. Call once after the main window is created.
 * No-op when running in dev mode (app.isPackaged === false).
 */
export function initAutoUpdater(): void {
  if (!app.isPackaged) {
    logger.debug('[Updater] Skipping — running in dev mode');
    return;
  }

  logger.info('[Updater] Initializing auto-updater');

  configureUpdater();

  // Initial check shortly after launch (2-second delay to let the UI settle)
  setTimeout(() => checkForUpdates(), 2_000);

  // Periodic re-check every 4 hours
  checkTimer = setInterval(() => checkForUpdates(), CHECK_INTERVAL_MS);

  // Clean up timer on app exit so it doesn't hold the process open
  app.once('before-quit', () => {
    if (checkTimer) {
      clearInterval(checkTimer);
      checkTimer = null;
    }
  });
}
