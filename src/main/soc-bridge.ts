/**
 * Self-Operating Computer + Browser-Use Bridge
 *
 * Spawns a Python child process that communicates via JSONL (one JSON per line).
 * Provides TypeScript API for Agent Friday to invoke screen control and browser automation.
 */

import { spawn, ChildProcess } from 'child_process';
import { app } from 'electron';
import * as path from 'path';
import * as readline from 'readline';
import { randomUUID } from 'crypto';
import { getSanitizedEnv } from './settings';

/* ── Types ─────────────────────────────────────────────────────────────── */

interface BridgeMessage {
  id: string;
  tool: 'soc' | 'browser' | 'ping' | 'exit';
  action: string;
  params: Record<string, unknown>;
}

interface BridgeResponse {
  id: string;
  status: 'ok' | 'error';
  result?: unknown;
  error?: string;
  event?: string;
  data?: unknown;
}

type PendingCallback = {
  resolve: (value: BridgeResponse) => void;
  reject: (reason: Error) => void;
  events: BridgeResponse[];
};

/* ── Bridge Class ──────────────────────────────────────────────────────── */

class PythonToolBridge {
  private proc: ChildProcess | null = null;
  private pending = new Map<string, PendingCallback>();
  private ready = false;
  private readyPromise: Promise<void> | null = null;
  private bridgePath: string;

  constructor() {
    // In dev, tools/ is at project root; in production, it's at resources/tools/
    const isDev = !app.isPackaged;
    this.bridgePath = isDev
      ? path.join(__dirname, '..', '..', 'tools', 'agent-bridge.py')
      : path.join(process.resourcesPath, 'tools', 'agent-bridge.py');
  }

  /** Start the Python bridge process. */
  async start(): Promise<void> {
    if (this.proc) return;

    return new Promise((resolve, reject) => {
      const pythonCmd = process.platform === 'win32' ? 'python' : 'python3';

      this.proc = spawn(pythonCmd, [this.bridgePath], {
        stdio: ['pipe', 'pipe', 'pipe'],
        env: getSanitizedEnv() as NodeJS.ProcessEnv,
      });

      // Read stdout line-by-line for JSONL responses
      const rl = readline.createInterface({
        input: this.proc.stdout!,
        crlfDelay: Infinity,
      });

      rl.on('line', (line) => {
        try {
          const msg: BridgeResponse = JSON.parse(line);
          this.handleResponse(msg);
        } catch (e) {
          console.warn('[SOC Bridge] Non-JSON stdout:', line);
        }
      });

      // Log stderr
      this.proc.stderr?.on('data', (data) => {
        console.warn('[SOC Bridge] stderr:', data.toString().trim());
      });

      this.proc.on('error', (err) => {
        console.error('[SOC Bridge] Process error:', err);
        this.ready = false;
        reject(err);
      });

      this.proc.on('exit', (code) => {
        console.log('[SOC Bridge] Process exited with code:', code);
        this.ready = false;
        this.proc = null;
        // Reject all pending
        for (const [, cb] of this.pending) {
          cb.reject(new Error(`Bridge process exited with code ${code}`));
        }
        this.pending.clear();
      });

      // Wait for the _init ready message
      const timeout = setTimeout(() => {
        reject(new Error('Bridge startup timeout'));
      }, 15000);

      const initCheck = (msg: BridgeResponse) => {
        if (msg.id === '_init' && msg.status === 'ok') {
          clearTimeout(timeout);
          this.ready = true;
          resolve();
        }
      };

      // Temporarily listen for init
      this.pending.set('_init', {
        resolve: () => {
          clearTimeout(timeout);
          this.ready = true;
          resolve();
        },
        reject: (err) => {
          clearTimeout(timeout);
          reject(err);
        },
        events: [],
      });
    });
  }

  /** Stop the Python bridge process. */
  async stop(): Promise<void> {
    if (!this.proc) return;

    try {
      await this.send('exit', 'exit', '', {});
    } catch {
      // Process may already be dead
    }

    this.proc?.kill();
    this.proc = null;
    this.ready = false;
  }

  /** Send a command to the bridge and wait for a response. */
  async send(
    tool: BridgeMessage['tool'],
    action: string,
    id?: string,
    params?: Record<string, unknown>,
    timeoutMs = 120000,
  ): Promise<BridgeResponse> {
    if (!this.proc || !this.ready) {
      await this.start();
    }

    const msgId = id || randomUUID();
    const msg: BridgeMessage = { id: msgId, tool, action, params: params || {} };

    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.pending.delete(msgId);
        reject(new Error(`Bridge timeout after ${timeoutMs}ms for ${tool}:${action}`));
      }, timeoutMs);

      this.pending.set(msgId, {
        resolve: (resp) => {
          clearTimeout(timeout);
          resolve(resp);
        },
        reject: (err) => {
          clearTimeout(timeout);
          reject(err);
        },
        events: [],
      });

      const line = JSON.stringify(msg) + '\n';
      this.proc!.stdin!.write(line);
    });
  }

  /** Handle an incoming response from the bridge. */
  private handleResponse(msg: BridgeResponse): void {
    const pending = this.pending.get(msg.id);
    if (!pending) return;

    if (msg.event) {
      // Streaming event — accumulate
      pending.events.push(msg);
      return;
    }

    // Final response
    this.pending.delete(msg.id);
    pending.resolve(msg);
  }

  /** Check if bridge is running. */
  isRunning(): boolean {
    return this.ready && this.proc !== null;
  }
}

/* ── Singleton ─────────────────────────────────────────────────────────── */

export const pythonBridge = new PythonToolBridge();

// cLaw Security Fix (CRITICAL-007): Import centralized consent gate
import { requireConsent } from './consent-gate';

/* ── High-level API ────────────────────────────────────────────────────── */

/**
 * Run the self-operating computer to achieve an objective.
 * The SOC will take screenshots, analyze them with a vision model,
 * and execute mouse/keyboard actions autonomously.
 */
export async function operateComputer(objective: string, model = 'gpt-4-with-ocr', maxSteps = 10): Promise<{
  completed: boolean;
  summary: string;
}> {
  // cLaw Security Fix (CRITICAL-007): Autonomous screen control requires explicit user approval
  const approved = await requireConsent('operate_computer', { objective, model, maxSteps });
  if (!approved) throw new Error('User denied autonomous computer operation');

  const resp = await pythonBridge.send('soc', 'operate', undefined, { objective, model, max_steps: maxSteps }, 300000);
  if (resp.status === 'error') throw new Error(resp.error || 'SOC operation failed');
  return resp.result as { completed: boolean; summary: string };
}

/**
 * Take a screenshot and return as base64 PNG.
 */
export async function takeScreenshot(): Promise<{ image: string; width: number; height: number }> {
  const resp = await pythonBridge.send('soc', 'screenshot');
  if (resp.status === 'error') throw new Error(resp.error || 'Screenshot failed');
  return resp.result as { image: string; width: number; height: number };
}

/**
 * Click at screen coordinates.
 */
export async function clickScreen(x: number, y: number): Promise<void> {
  // cLaw Security Fix (CRITICAL-007): Screen clicks can perform destructive UI actions
  const approved = await requireConsent('soc_click', { x, y });
  if (!approved) throw new Error('User denied screen click');

  const resp = await pythonBridge.send('soc', 'click', undefined, { x, y });
  if (resp.status === 'error') throw new Error(resp.error || 'Click failed');
}

/**
 * Type text.
 */
export async function typeText(text: string): Promise<void> {
  // cLaw Security Fix (CRITICAL-007): Typing can enter passwords, submit forms, execute shortcuts
  const approved = await requireConsent('soc_type', { text });
  if (!approved) throw new Error('User denied text input');

  const resp = await pythonBridge.send('soc', 'type', undefined, { text });
  if (resp.status === 'error') throw new Error(resp.error || 'Type failed');
}

/**
 * Press keyboard keys.
 */
export async function pressKeys(keys: string[]): Promise<void> {
  // cLaw Security Fix (CRITICAL-007): Key presses can execute shortcuts (Alt+F4, Ctrl+A+Del, etc.)
  const approved = await requireConsent('soc_press_keys', { keys });
  if (!approved) throw new Error('User denied key press');

  const resp = await pythonBridge.send('soc', 'press', undefined, { keys });
  if (resp.status === 'error') throw new Error(resp.error || 'Press failed');
}

/**
 * Run a browser automation task using browser-use.
 */
export async function browserTask(task: string, model = 'gpt-4o', maxSteps = 20, headless = false): Promise<{
  completed: boolean;
  final_result: string;
  steps: number;
}> {
  // cLaw Security Fix (CRITICAL-007): Autonomous browser automation requires explicit approval
  const approved = await requireConsent('browser_task', { task, model, maxSteps, headless });
  if (!approved) throw new Error('User denied browser automation task');

  const resp = await pythonBridge.send('browser', 'run', undefined, { task, model, max_steps: maxSteps, headless }, 300000);
  if (resp.status === 'error') throw new Error(resp.error || 'Browser task failed');
  return resp.result as { completed: boolean; final_result: string; steps: number };
}

/**
 * Check which Python dependencies are installed for each tool.
 */
export async function checkDependencies(): Promise<{
  soc: Record<string, boolean>;
  browser: Record<string, boolean>;
}> {
  const [socResp, browserResp] = await Promise.all([
    pythonBridge.send('soc', 'check'),
    pythonBridge.send('browser', 'check'),
  ]);

  return {
    soc: (socResp.result as { dependencies: Record<string, boolean> })?.dependencies || {},
    browser: (browserResp.result as { dependencies: Record<string, boolean> })?.dependencies || {},
  };
}

/**
 * Build tool declarations for the agent to use SOC and browser-use.
 */
export function buildSocToolDeclarations(): Array<{
  name: string;
  description: string;
  parameters: Record<string, unknown>;
}> {
  return [
    {
      name: 'operate_computer',
      description: 'Autonomously operate the computer to achieve an objective. Takes screenshots, analyzes them with a vision AI model, and executes mouse/keyboard actions. Use for complex GUI tasks like installing software, configuring system settings, or any task that requires interacting with desktop applications. The AI will see the screen and figure out what to click/type.',
      parameters: {
        type: 'object',
        properties: {
          objective: {
            type: 'string',
            description: 'The task to accomplish, described in natural language. E.g., "Open Chrome and navigate to gmail.com" or "Find and open the Calculator app"',
          },
          model: {
            type: 'string',
            description: 'Vision model to use. Options: gpt-4-with-ocr (default), gpt-4-with-som, gemini-pro-vision, claude-3',
            enum: ['gpt-4-with-ocr', 'gpt-4-with-som', 'gpt-4.1-with-ocr', 'o1-with-ocr', 'gemini-pro-vision', 'claude-3'],
          },
          max_steps: {
            type: 'number',
            description: 'Maximum number of action steps (default: 10)',
          },
        },
        required: ['objective'],
      },
    },
    {
      name: 'browser_task',
      description: 'Run an autonomous browser agent to complete a web-based task. The agent will open a browser, navigate websites, fill forms, click buttons, and extract information. Use for web research, form filling, online purchases, data extraction, etc.',
      parameters: {
        type: 'object',
        properties: {
          task: {
            type: 'string',
            description: 'The web task to accomplish in natural language. E.g., "Go to amazon.com and find the best-rated wireless keyboard under $50" or "Search Google for the latest news about AI regulation"',
          },
          model: {
            type: 'string',
            description: 'LLM to drive the browser agent. Default: gpt-4o',
          },
          max_steps: {
            type: 'number',
            description: 'Maximum browser actions (default: 20)',
          },
          headless: {
            type: 'boolean',
            description: 'Run browser in headless mode (no visible window). Default: false',
          },
        },
        required: ['task'],
      },
    },
    {
      name: 'take_screenshot',
      description: 'Take a screenshot of the current screen. Returns a base64-encoded PNG image. Use to see what is currently displayed on the user\'s screen.',
      parameters: {
        type: 'object',
        properties: {},
        required: [],
      },
    },
    {
      name: 'click_screen',
      description: 'Click at specific pixel coordinates on the screen. Use after taking a screenshot to interact with visible UI elements.',
      parameters: {
        type: 'object',
        properties: {
          x: { type: 'number', description: 'X coordinate in pixels from left edge' },
          y: { type: 'number', description: 'Y coordinate in pixels from top edge' },
        },
        required: ['x', 'y'],
      },
    },
    {
      name: 'type_text',
      description: 'Type text at the current cursor position. Simulates keyboard input.',
      parameters: {
        type: 'object',
        properties: {
          text: { type: 'string', description: 'Text to type' },
        },
        required: ['text'],
      },
    },
    {
      name: 'press_keys',
      description: 'Press keyboard keys or key combinations. E.g., ["ctrl", "c"] for copy, ["enter"] for enter.',
      parameters: {
        type: 'object',
        properties: {
          keys: {
            type: 'array',
            items: { type: 'string' },
            description: 'Array of key names to press simultaneously. E.g., ["ctrl", "s"] for save, ["alt", "tab"] for window switch',
          },
        },
        required: ['keys'],
      },
    },
  ];
}
