import { desktopCapturer, BrowserWindow, screen } from 'electron';

class ScreenCaptureManager {
  private intervalId: NodeJS.Timeout | null = null;
  private latestFrame: string = '';
  private captureIntervalMs = 2000;
  private targetWindow: BrowserWindow | null = null;

  start(mainWindow: BrowserWindow): void {
    if (this.intervalId) return;
    this.targetWindow = mainWindow;

    this.intervalId = setInterval(async () => {
      try {
        const sources = await desktopCapturer.getSources({
          types: ['screen'],
          thumbnailSize: { width: 1280, height: 720 },
        });

        if (sources.length > 0) {
          // On multi-monitor, pick the display containing the main window
          let source = sources[0];
          if (sources.length > 1 && this.targetWindow && !this.targetWindow.isDestroyed()) {
            const winBounds = this.targetWindow.getBounds();
            const display = screen.getDisplayNearestPoint({
              x: winBounds.x + winBounds.width / 2,
              y: winBounds.y + winBounds.height / 2,
            });
            // Match by display ID in source name (Electron names them "Screen 1", "Screen 2", etc.)
            const match = sources.find((s) => s.display_id === String(display.id));
            if (match) source = match;
          }
          const frame = source.thumbnail.toJPEG(60);
          this.latestFrame = frame.toString('base64');

          if (this.targetWindow && !this.targetWindow.isDestroyed()) {
            this.targetWindow.webContents.send('screen-capture:frame', this.latestFrame);
          }
        }
      } catch (err) {
        console.warn('[ScreenCapture] Capture error:', err);
      }
    }, this.captureIntervalMs);

    console.log('[ScreenCapture] Started capturing');
  }

  stop(): void {
    if (this.intervalId) {
      clearInterval(this.intervalId);
      this.intervalId = null;
    }
    this.latestFrame = '';
    this.targetWindow = null;
    console.log('[ScreenCapture] Stopped capturing');
  }

  getLatestFrame(): string {
    return this.latestFrame;
  }

  isCapturing(): boolean {
    return this.intervalId !== null;
  }
}

export const screenCapture = new ScreenCaptureManager();
