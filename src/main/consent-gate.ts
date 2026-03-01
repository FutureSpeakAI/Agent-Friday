/**
 * consent-gate.ts — Centralized consent gate for all side-effect actions.
 *
 * cLaw Architecture Recommendation: All side-effect code paths funnel through
 * this single module for consistent timeout, logging, and safe-mode awareness.
 *
 * Created to address: CRITICAL-002, CRITICAL-005, CRITICAL-006, CRITICAL-007, MEDIUM-002
 *
 * Usage:
 *   import { requireConsent } from './consent-gate';
 *   const approved = await requireConsent('send_email', { to: 'steve@co.com', subject: '...' });
 *   if (!approved) return { error: 'User denied action' };
 */

import { BrowserWindow } from 'electron';
import { integrityManager } from './integrity';

/** Reference to the main Electron window — set on app init */
let mainWindowRef: BrowserWindow | null = null;

export function setConsentWindow(win: BrowserWindow): void {
  mainWindowRef = win;
}

/** Pending consent requests, keyed by request ID */
const pendingConsents = new Map<string, {
  resolve: (approved: boolean) => void;
  timer: ReturnType<typeof setTimeout>;
}>();

let consentId = 0;

/**
 * Require explicit user consent before executing a side-effect action.
 *
 * - Auto-deny in safe mode (integrity compromised)
 * - Auto-deny on 30s timeout
 * - Auto-deny if no renderer window
 *
 * @param action — Human-readable action name (e.g. 'send_email', 'create_calendar_event')
 * @param details — Key/value details shown to the user
 * @param options — Optional overrides
 * @returns true if user approved, false otherwise
 */
export async function requireConsent(
  action: string,
  details: Record<string, unknown>,
  options?: { timeout?: number }
): Promise<boolean> {
  // cLaw: auto-deny ALL side effects when integrity is compromised
  if (integrityManager.isInSafeMode()) {
    console.warn(`[ConsentGate/cLaw] DENIED "${action}" — system is in safe mode`);
    return false;
  }

  // No window → no consent mechanism → deny
  if (!mainWindowRef || mainWindowRef.isDestroyed()) {
    console.warn(`[ConsentGate] DENIED "${action}" — no renderer window available`);
    return false;
  }

  const id = String(++consentId);
  const timeout = options?.timeout ?? 30_000;
  const description = formatConsentDescription(action, details);

  return new Promise<boolean>((resolve) => {
    const timer = setTimeout(() => {
      pendingConsents.delete(id);
      console.warn(`[ConsentGate] TIMEOUT "${action}" — auto-denied after ${timeout / 1000}s`);
      resolve(false); // auto-deny on timeout
    }, timeout);

    pendingConsents.set(id, { resolve, timer });

    // Use the same IPC channel as desktop-tools for renderer consistency
    mainWindowRef!.webContents.send('desktop:confirm-request', {
      id,
      toolName: action,
      description,
    });
  });
}

/** Called from IPC when user responds to a consent request */
export function handleConsentResponse(id: string, approved: boolean): void {
  const pending = pendingConsents.get(id);
  if (pending) {
    clearTimeout(pending.timer);
    pendingConsents.delete(id);
    pending.resolve(approved);
  }
}

/** Format a human-readable description of what the user is approving */
function formatConsentDescription(action: string, details: Record<string, unknown>): string {
  switch (action) {
    case 'gateway_send_message':
      return `Send ${details.channel} message to ${details.recipient_id}: "${String(details.text || '').slice(0, 100)}"`;
    case 'create_calendar_event':
      return `Create calendar event: "${details.summary}" (${details.startTime}${details.attendees ? `, attendees: ${details.attendees}` : ''})`;
    case 'slack_send_webhook':
      return `Send Slack webhook message to ${details.webhook_url ? 'configured webhook' : 'unknown'}`;
    case 'discord_send_webhook':
      return `Send Discord webhook message`;
    case 'teams_send_webhook':
      return `Send Teams webhook message`;
    case 'smtp_send_email':
      return `Send email to ${details.to}: "${String(details.subject || '').slice(0, 80)}"`;
    case 'http_request':
      return `HTTP ${details.method || 'GET'} request to ${String(details.url || '').slice(0, 120)}`;
    case 'webhook_send':
      return `POST to webhook: ${String(details.url || '').slice(0, 120)}`;
    case 'operate_computer':
      return `Autonomous screen control: "${String(details.objective || '').slice(0, 120)}"`;
    case 'browser_task':
      return `Autonomous browser task: "${String(details.task || '').slice(0, 120)}"`;
    case 'soc_click':
      return `Click screen at (${details.x}, ${details.y})`;
    case 'soc_type':
      return `Type text: "${String(details.text || '').slice(0, 80)}"`;
    case 'soc_press_keys':
      return `Press keys: ${details.keys}`;
    case 'container_execute':
      return `Run ${details.language || 'code'} in Docker container (trigger: ${details.trigger || 'unknown'})${details.codePreview ? `: "${String(details.codePreview).slice(0, 100)}"` : ''}${details.packages ? ` [packages: ${details.packages}]` : ''}${details.network && details.network !== 'none' ? ` [network: ${details.network}]` : ''}`;
    default:
      return `${action}: ${JSON.stringify(details).slice(0, 150)}`;
  }
}
