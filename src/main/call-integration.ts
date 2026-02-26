/**
 * call-integration.ts — Live Call Participation for Agent Friday.
 *
 * Enables the agent to join video/audio calls (Google Meet, Zoom, Teams)
 * by routing audio through a virtual audio device (VB-Cable).
 *
 * Architecture:
 *   Agent audio output → VB-Cable Input (virtual mic) → Meeting app
 *   Meeting audio → System speakers/headphones → Agent mic input (echo-cancelled)
 *
 * Requires VB-Cable (free) installed on Windows:
 *   https://vb-audio.com/Cable/
 */

import { exec } from 'child_process';
import { promisify } from 'util';

const execAsync = promisify(exec);

export interface VirtualAudioStatus {
  available: boolean;
  devices: string[];
  installUrl: string;
}

class CallIntegration {
  private inCallMode = false;
  private activeMeetingUrl: string | null = null;

  /**
   * Check if VB-Cable virtual audio driver is installed.
   * Looks for "CABLE" in audio device names via PowerShell.
   */
  async isVirtualAudioAvailable(): Promise<VirtualAudioStatus> {
    const installUrl = 'https://vb-audio.com/Cable/';
    try {
      const { stdout } = await execAsync(
        'powershell -Command "Get-CimInstance Win32_SoundDevice | Where-Object { $_.Name -like \'*CABLE*\' -or $_.Name -like \'*VB-Audio*\' -or $_.Name -like \'*Virtual*Cable*\' } | Select-Object -ExpandProperty Name"',
        { timeout: 5000 }
      );
      const devices = stdout.trim().split('\n').map((d) => d.trim()).filter(Boolean);
      return { available: devices.length > 0, devices, installUrl };
    } catch {
      return { available: false, devices: [], installUrl };
    }
  }

  /**
   * Enter call mode — tells the renderer to switch audio output to VB-Cable.
   */
  enterCallMode(meetingUrl?: string): { success: boolean; message: string } {
    if (this.inCallMode) {
      return { success: true, message: 'Already in call mode.' };
    }
    this.inCallMode = true;
    this.activeMeetingUrl = meetingUrl || null;
    console.log('[CallIntegration] Entered call mode' + (meetingUrl ? `: ${meetingUrl}` : ''));
    return { success: true, message: 'Call mode activated. Audio is now routed to the virtual microphone.' };
  }

  /**
   * Exit call mode — tells the renderer to switch audio output back to default.
   */
  exitCallMode(): { success: boolean; message: string } {
    if (!this.inCallMode) {
      return { success: true, message: 'Not in call mode.' };
    }
    this.inCallMode = false;
    this.activeMeetingUrl = null;
    console.log('[CallIntegration] Exited call mode');
    return { success: true, message: 'Call mode deactivated. Audio routing restored to normal.' };
  }

  /**
   * Check if currently in call mode.
   */
  isInCallMode(): boolean {
    return this.inCallMode;
  }

  /**
   * Get the active meeting URL.
   */
  getActiveMeetingUrl(): string | null {
    return this.activeMeetingUrl;
  }

  /**
   * Open a meeting URL in the default browser.
   */
  async openMeetingUrl(url: string): Promise<void> {
    // Validate URL
    if (!url.startsWith('http://') && !url.startsWith('https://')) {
      throw new Error('Invalid meeting URL — must start with http:// or https://');
    }

    return new Promise((resolve, reject) => {
      // On Windows, use `start` to open URL in default browser
      exec(`start "" "${url}"`, (err) => {
        if (err) {
          console.warn('[CallIntegration] Failed to open meeting URL:', err);
          reject(err);
        } else {
          console.log('[CallIntegration] Opened meeting URL:', url);
          resolve();
        }
      });
    });
  }

  /**
   * Get call status for the system prompt / context.
   */
  getContextString(): string {
    if (!this.inCallMode) return '';
    const parts = ['[CALL MODE ACTIVE]'];
    parts.push('You are currently participating in a live call.');
    parts.push('Your voice is being routed to the meeting via virtual microphone.');
    parts.push('Meeting participants can hear you speak.');
    parts.push('Be conversational and natural — you\'re in a live meeting.');
    if (this.activeMeetingUrl) {
      parts.push(`Meeting: ${this.activeMeetingUrl}`);
    }
    return parts.join('\n');
  }
}

export const callIntegration = new CallIntegration();
