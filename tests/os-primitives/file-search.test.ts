/**
 * FileSearchEngine — Unit tests for query sanitization and result parsing.
 *
 * Tests the pure logic of the file search engine by mocking child_process.execFile.
 * Validates query sanitization, result parsing, and search strategy selection.
 *
 * Phase B.2: "Sensory Tests" — OS Primitives
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';

// ── Mock child_process ─────────────────────────────────────────────

const mocks = vi.hoisted(() => ({
  execFile: vi.fn(),
}));

vi.mock('child_process', () => ({
  execFile: mocks.execFile,
}));

vi.mock('../../src/main/settings', () => ({
  getSanitizedEnv: () => ({ PATH: '/usr/bin' }),
}));

import { fileSearch, type FileSearchQuery } from '../../src/main/file-search';

// ── Helpers ─────────────────────────────────────────────────────────

/** Simulate PowerShell returning JSON results */
function mockPowerShellSuccess(jsonOutput: string): void {
  mocks.execFile.mockImplementation(
    (_cmd: string, _args: string[], _opts: unknown, cb: Function) => {
      cb(null, jsonOutput, '');
    },
  );
}

function mockPowerShellError(message: string): void {
  mocks.execFile.mockImplementation(
    (_cmd: string, _args: string[], _opts: unknown, cb: Function) => {
      cb(new Error(message), '', message);
    },
  );
}

const SAMPLE_RESULTS = JSON.stringify([
  {
    FullName: 'C:\\Users\\test\\Documents\\report.pdf',
    Name: 'report.pdf',
    Extension: '.pdf',
    Length: 1024,
    LastWriteTime: '2024-01-15T10:30:00.0000000+00:00',
    CreationTime: '2024-01-10T08:00:00.0000000+00:00',
    DirectoryName: 'C:\\Users\\test\\Documents',
  },
  {
    FullName: 'C:\\Users\\test\\Desktop\\notes.txt',
    Name: 'notes.txt',
    Extension: '.txt',
    Length: 256,
    LastWriteTime: '2024-02-01T14:00:00.0000000+00:00',
    CreationTime: '2024-01-20T09:00:00.0000000+00:00',
    DirectoryName: 'C:\\Users\\test\\Desktop',
  },
]);

const SINGLE_RESULT = JSON.stringify({
  FullName: 'C:\\Users\\test\\file.md',
  Name: 'file.md',
  Extension: '.md',
  Length: 512,
  LastWriteTime: '2024-03-01T12:00:00.0000000+00:00',
  CreationTime: '2024-02-28T10:00:00.0000000+00:00',
  DirectoryName: 'C:\\Users\\test',
});

// ── Tests ───────────────────────────────────────────────────────────

describe('fileSearch.search — query sanitization', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('returns empty results for empty query', async () => {
    const result = await fileSearch.search({ query: '' });
    expect(result.results).toEqual([]);
    expect(result.totalFound).toBe(0);
    expect(mocks.execFile).not.toHaveBeenCalled();
  });

  it('strips dangerous characters from query', async () => {
    // First call (Windows Search) fails, second call (PowerShell scan) succeeds
    let callCount = 0;
    mocks.execFile.mockImplementation(
      (_cmd: string, _args: string[], _opts: unknown, cb: Function) => {
        callCount++;
        if (callCount === 1) {
          // Windows Search fails
          cb(new Error('WDS unavailable'), '', 'error');
        } else {
          // PowerShell scan succeeds with empty results
          cb(null, '[]', '');
        }
      },
    );

    await fileSearch.search({ query: 'test`$"\\;|&<>{}()file' });

    // Should have been called (query sanitized to 'testfile')
    expect(mocks.execFile).toHaveBeenCalled();
    // The sanitized query 'testfile' should appear in the script, not the original
    const lastCall = mocks.execFile.mock.calls[mocks.execFile.mock.calls.length - 1];
    const scriptArg = lastCall[1].join(' ');
    expect(scriptArg).toContain('testfile');
    // Original dangerous chars should not appear as part of the query
    expect(scriptArg).not.toContain('test`');
  });

  it('returns empty for query that becomes empty after sanitization', async () => {
    const result = await fileSearch.search({ query: '`$"\\;|&' });
    expect(result.results).toEqual([]);
    expect(mocks.execFile).not.toHaveBeenCalled();
  });
});

describe('fileSearch.search — result parsing', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('parses array of results from PowerShell JSON', async () => {
    // Make Windows Search fail so it falls back to PowerShell scan
    let callCount = 0;
    mocks.execFile.mockImplementation(
      (_cmd: string, _args: string[], _opts: unknown, cb: Function) => {
        callCount++;
        if (callCount === 1) cb(new Error('fail'), '', 'fail');
        else cb(null, SAMPLE_RESULTS, '');
      },
    );

    const result = await fileSearch.search({ query: 'report' });
    expect(result.results).toHaveLength(2);
    expect(result.results[0].filePath).toBe('C:\\Users\\test\\Documents\\report.pdf');
    expect(result.results[0].name).toBe('report.pdf');
    expect(result.results[0].extension).toBe('pdf');
    expect(result.results[0].size).toBe(1024);
    expect(result.results[0].directory).toBe('C:\\Users\\test\\Documents');
  });

  it('parses a single result (non-array JSON)', async () => {
    let callCount = 0;
    mocks.execFile.mockImplementation(
      (_cmd: string, _args: string[], _opts: unknown, cb: Function) => {
        callCount++;
        if (callCount === 1) cb(new Error('fail'), '', 'fail');
        else cb(null, SINGLE_RESULT, '');
      },
    );

    const result = await fileSearch.search({ query: 'file' });
    expect(result.results).toHaveLength(1);
    expect(result.results[0].name).toBe('file.md');
    expect(result.results[0].extension).toBe('md');
  });

  it('returns empty array for null/empty PowerShell output', async () => {
    let callCount = 0;
    mocks.execFile.mockImplementation(
      (_cmd: string, _args: string[], _opts: unknown, cb: Function) => {
        callCount++;
        if (callCount === 1) cb(new Error('fail'), '', 'fail');
        else cb(null, '', '');
      },
    );

    const result = await fileSearch.search({ query: 'nonexistent' });
    expect(result.results).toEqual([]);
  });

  it('strips leading dot from extension', async () => {
    let callCount = 0;
    mocks.execFile.mockImplementation(
      (_cmd: string, _args: string[], _opts: unknown, cb: Function) => {
        callCount++;
        if (callCount === 1) cb(new Error('fail'), '', 'fail');
        else cb(null, SINGLE_RESULT, '');
      },
    );

    const result = await fileSearch.search({ query: 'file' });
    expect(result.results[0].extension).toBe('md'); // not '.md'
  });

  it('reports searchMethod correctly', async () => {
    // Windows Search succeeds
    mockPowerShellSuccess(SAMPLE_RESULTS);
    const wdsResult = await fileSearch.search({ query: 'report' });
    expect(wdsResult.searchMethod).toBe('windows-search');

    // Windows Search fails, falls through to PowerShell
    vi.clearAllMocks();
    let callCount = 0;
    mocks.execFile.mockImplementation(
      (_cmd: string, _args: string[], _opts: unknown, cb: Function) => {
        callCount++;
        if (callCount === 1) cb(new Error('fail'), '', 'fail');
        else cb(null, SAMPLE_RESULTS, '');
      },
    );
    const psResult = await fileSearch.search({ query: 'report' });
    expect(psResult.searchMethod).toBe('powershell-scan');
  });

  it('includes durationMs in response', async () => {
    mockPowerShellSuccess(SAMPLE_RESULTS);
    const result = await fileSearch.search({ query: 'test' });
    expect(result.durationMs).toBeGreaterThanOrEqual(0);
  });
});

describe('fileSearch.search — search strategy', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('falls back to PowerShell scan when Windows Search returns empty', async () => {
    let callCount = 0;
    mocks.execFile.mockImplementation(
      (_cmd: string, _args: string[], _opts: unknown, cb: Function) => {
        callCount++;
        if (callCount === 1) cb(null, '[]', ''); // WDS returns empty
        else cb(null, SAMPLE_RESULTS, '');        // PS scan returns results
      },
    );

    const result = await fileSearch.search({ query: 'report' });
    expect(callCount).toBe(2); // Both strategies tried
    expect(result.results).toHaveLength(2);
  });

  it('uses Windows Search results when available', async () => {
    mockPowerShellSuccess(SAMPLE_RESULTS);
    const result = await fileSearch.search({ query: 'report' });
    expect(result.searchMethod).toBe('windows-search');
    expect(mocks.execFile).toHaveBeenCalledTimes(1); // Only WDS, no fallback
  });
});

describe('fileSearch.findDuplicates', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('parses duplicate groups from PowerShell output', async () => {
    const dupOutput = JSON.stringify([
      { Key: 'report.pdf', Files: ['C:\\a\\report.pdf', 'C:\\b\\report.pdf'] },
      { Key: 'notes.txt', Files: ['C:\\x\\notes.txt', 'C:\\y\\notes.txt'] },
    ]);
    mockPowerShellSuccess(dupOutput);

    const dups = await fileSearch.findDuplicates('C:\\Users\\test');
    expect(dups).toHaveLength(2);
    expect(dups[0].key).toBe('report.pdf');
    expect(dups[0].files).toHaveLength(2);
  });

  it('rejects when PowerShell fails', async () => {
    mockPowerShellError('Access denied');
    await expect(fileSearch.findDuplicates('C:\\noaccess')).rejects.toThrow('Access denied');
  });

  it('handles single duplicate group (non-array)', async () => {
    const singleDup = JSON.stringify({
      Key: 'readme.md',
      Files: ['C:\\a\\readme.md', 'C:\\b\\readme.md'],
    });
    mockPowerShellSuccess(singleDup);

    const dups = await fileSearch.findDuplicates('C:\\test');
    expect(dups).toHaveLength(1);
    expect(dups[0].key).toBe('readme.md');
  });
});
