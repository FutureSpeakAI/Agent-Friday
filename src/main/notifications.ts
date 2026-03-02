/**
 * notifications.ts — Notification Whisper Engine.
 * Captures Windows notifications via PowerShell (Action Center polling)
 * and forwards relevant ones to the renderer for natural voice delivery.
 */

import { BrowserWindow } from 'electron';
import { execFile } from 'child_process';
import { settingsManager, getSanitizedEnv } from './settings';

export interface CapturedNotification {
  app: string;
  title: string;
  body: string;
  timestamp: number;
}

const POLL_INTERVAL = 15_000; // 15s
const MAX_RECENT = 20;

// Default allowlist — user configurable via settings
const DEFAULT_ALLOWED_APPS = ['Slack', 'Teams', 'Outlook', 'Calendar', 'Discord', 'WhatsApp', 'Telegram'];

class NotificationEngine {
  private mainWindow: BrowserWindow | null = null;
  private interval: ReturnType<typeof setInterval> | null = null;
  private recentNotifications: CapturedNotification[] = [];
  private seenIds = new Set<string>();

  initialize(win: BrowserWindow): void {
    this.mainWindow = win;
    this.interval = setInterval(() => this.poll(), POLL_INTERVAL);
    console.log('[Notifications] Initialized — polling every 15s');
  }

  stop(): void {
    if (this.interval) {
      clearInterval(this.interval);
      this.interval = null;
    }
  }

  getRecent(): CapturedNotification[] {
    return [...this.recentNotifications];
  }

  private async poll(): Promise<void> {
    if (!this.mainWindow || this.mainWindow.isDestroyed()) return;

    const settings = settingsManager.get() as unknown as Record<string, unknown>;
    const enabled = settings.notificationWhisperEnabled !== false;
    if (!enabled) return;

    const allowedApps = (settings.notificationAllowedApps as string[] | undefined) || DEFAULT_ALLOWED_APPS;

    try {
      const notifications = await this.fetchNotifications();

      for (const notif of notifications) {
        // Generate a dedup key
        const key = `${notif.app}:${notif.title}:${notif.body}`.slice(0, 200);
        if (this.seenIds.has(key)) continue;
        this.seenIds.add(key);

        // Check allowlist (case-insensitive partial match)
        const isAllowed = allowedApps.some((app) =>
          notif.app.toLowerCase().includes(app.toLowerCase())
        );

        if (!isAllowed) continue;

        const captured: CapturedNotification = {
          app: notif.app,
          title: notif.title,
          body: notif.body,
          timestamp: Date.now(),
        };

        this.recentNotifications.push(captured);
        if (this.recentNotifications.length > MAX_RECENT) {
          this.recentNotifications.shift();
        }

        // Send to renderer for injection into Gemini
        this.mainWindow.webContents.send('notification:captured', captured);
        console.log(`[Notifications] Captured: ${notif.app} — ${notif.title}`);
      }

      // Prune old seen IDs to prevent unbounded growth
      if (this.seenIds.size > 500) {
        const entries = [...this.seenIds];
        this.seenIds = new Set(entries.slice(-200));
      }
    } catch {
      // Non-critical — notification polling failure shouldn't crash the app
    }
  }

  private fetchNotifications(): Promise<Array<{ app: string; title: string; body: string }>> {
    return new Promise((resolve) => {
      // PowerShell script to query Windows Toast notifications from Action Center
      // Uses the Windows.UI.Notifications API via .NET interop
      const script = `
try {
  [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
  [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime] | Out-Null

  $listener = [Windows.UI.Notifications.Management.UserNotificationListener]::Current
  $notifications = $listener.GetNotificationsAsync([Windows.UI.Notifications.NotificationKinds]::Toast).GetAwaiter().GetResult()

  $results = @()
  foreach ($n in $notifications) {
    try {
      $binding = $n.Notification.Visual.GetBinding([Windows.UI.Notifications.KnownNotificationBindings]::ToastGeneric)
      $texts = $binding.GetTextElements()
      $title = ''
      $body = ''
      $i = 0
      foreach ($t in $texts) {
        if ($i -eq 0) { $title = $t.Text }
        elseif ($i -eq 1) { $body = $t.Text }
        $i++
      }
      $results += @{ App = $n.AppInfo.DisplayInfo.DisplayName; Title = $title; Body = $body }
    } catch { }
  }
  $results | ConvertTo-Json -Compress
} catch {
  Write-Output '[]'
}
`.trim();

      // Crypto Sprint 3: exec() → execFile() to avoid shell metacharacter injection.
      // execFile() bypasses cmd.exe — the script is passed as a direct argument
      // to powershell.exe, not interpreted by an intermediate shell.
      execFile(
        'powershell.exe',
        ['-NoProfile', '-NonInteractive', '-Command', script],
        { timeout: 10_000, maxBuffer: 512 * 1024, env: getSanitizedEnv() as NodeJS.ProcessEnv },
        (err, stdout) => {
          if (err) {
            resolve([]);
            return;
          }

          try {
            const parsed = JSON.parse(stdout.trim() || '[]');
            // Ensure it's an array
            const arr = Array.isArray(parsed) ? parsed : [parsed];
            resolve(
              arr
                .filter((n: Record<string, unknown>) => n && n.App && (n.Title || n.Body))
                .map((n: Record<string, unknown>) => ({
                  app: String(n.App || ''),
                  title: String(n.Title || ''),
                  body: String(n.Body || ''),
                }))
            );
          } catch {
            resolve([]);
          }
        }
      );
    });
  }
}

export const notificationEngine = new NotificationEngine();
