/**
 * file-search.ts — Native File Search Engine.
 * Provides fast file search across the OS using Windows Search (WDS) via PowerShell,
 * with fallback to Get-ChildItem recursive scan.
 * Supports search by name, extension, content, date range, and size filters.
 */

import { execFile } from 'child_process';
import * as path from 'path';
import { getSanitizedEnv } from './settings';

// ── Types ───────────────────────────────────────────────────────────────────

export interface FileSearchQuery {
  /** Search term — matches file name, path, or content depending on mode */
  query: string;
  /** Where to search (defaults to user profile) */
  searchPaths?: string[];
  /** Filter by extensions (e.g. ['pdf', 'docx']) */
  extensions?: string[];
  /** Minimum file size in bytes */
  minSize?: number;
  /** Maximum file size in bytes */
  maxSize?: number;
  /** Only files modified after this date (ISO string or epoch ms) */
  modifiedAfter?: string | number;
  /** Only files modified before this date */
  modifiedBefore?: string | number;
  /** Max results to return */
  limit?: number;
  /** Search mode */
  mode?: 'filename' | 'content' | 'everything';
}

export interface FileSearchResult {
  /** Absolute path */
  filePath: string;
  /** File name with extension */
  name: string;
  /** Extension (lowercase, no dot) */
  extension: string;
  /** Size in bytes */
  size: number;
  /** Last modified (ISO string) */
  modifiedAt: string;
  /** Created (ISO string) */
  createdAt: string;
  /** Containing directory */
  directory: string;
  /** Match relevance hint */
  matchType: 'name' | 'path' | 'content' | 'index';
}

export interface FileSearchResponse {
  results: FileSearchResult[];
  totalFound: number;
  searchMethod: 'windows-search' | 'powershell-scan';
  durationMs: number;
}

// ── Constants ───────────────────────────────────────────────────────────────

const SEARCH_TIMEOUT_MS = 30_000;
const DEFAULT_LIMIT = 50;

// ── Implementation ──────────────────────────────────────────────────────────

class FileSearchEngine {
  private everythingAvailable: boolean | null = null;

  /**
   * Search for files across the OS.
   * Strategy: Try Windows Search (indexed, fast) first, fall back to PowerShell scan.
   */
  async search(query: FileSearchQuery): Promise<FileSearchResponse> {
    const start = Date.now();
    const limit = query.limit ?? DEFAULT_LIMIT;

    // Sanitize search query — prevent PowerShell injection
    const safeQuery = query.query.replace(/[`$'"\\;|&<>{}()]/g, '');
    if (!safeQuery.trim()) {
      return { results: [], totalFound: 0, searchMethod: 'windows-search', durationMs: 0 };
    }

    // Try Windows Search (WDS) first — uses pre-built index, near-instant
    try {
      const results = await this.searchWindowsIndex(safeQuery, query, limit);
      if (results.length > 0) {
        return {
          results,
          totalFound: results.length,
          searchMethod: 'windows-search',
          durationMs: Date.now() - start,
        };
      }
    } catch (err) {
      console.warn('[FileSearch] Windows Search failed, falling back to scan:', err instanceof Error ? err.message : err);
    }

    // Fallback: PowerShell recursive scan
    const results = await this.searchPowerShell(safeQuery, query, limit);
    return {
      results,
      totalFound: results.length,
      searchMethod: 'powershell-scan',
      durationMs: Date.now() - start,
    };
  }

  /**
   * Get recently modified files across common user directories.
   */
  async getRecentFiles(limit = 20, extensions?: string[]): Promise<FileSearchResult[]> {
    const home = process.env.USERPROFILE || process.env.HOME || '';
    if (!home) return [];

    const dirs = ['Desktop', 'Documents', 'Downloads'].map(d => path.join(home, d));
    const extFilter = extensions?.length
      ? extensions.map(e => `-Include "*.${e}"`).join(' ')
      : '-Include "*"';

    const script = `
      $dirs = @(${dirs.map(d => `"${d.replace(/\\/g, '\\\\')}"`).join(',')})
      $results = @()
      foreach ($dir in $dirs) {
        if (Test-Path $dir) {
          $results += Get-ChildItem -Path $dir -File -Recurse ${extFilter} -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First ${limit * 2}
        }
      }
      $results | Sort-Object LastWriteTime -Descending |
        Select-Object -First ${limit} |
        ForEach-Object {
          [PSCustomObject]@{
            FullName = $_.FullName
            Name = $_.Name
            Extension = $_.Extension
            Length = $_.Length
            LastWriteTime = $_.LastWriteTime.ToString("o")
            CreationTime = $_.CreationTime.ToString("o")
            DirectoryName = $_.DirectoryName
          }
        } | ConvertTo-Json -Compress
    `;

    const raw = await this.runPowerShell(script);
    return this.parseResults(raw, 'name');
  }

  /**
   * Find duplicate files by name or size in a directory.
   */
  async findDuplicates(dirPath: string, mode: 'name' | 'size' = 'name'): Promise<Array<{ key: string; files: string[] }>> {
    const safePath = dirPath.replace(/[`$'"]/g, '');
    const groupBy = mode === 'name' ? 'Name' : 'Length';

    const script = `
      Get-ChildItem -Path "${safePath}" -File -Recurse -ErrorAction SilentlyContinue |
        Group-Object ${groupBy} |
        Where-Object { $_.Count -gt 1 } |
        Select-Object -First 20 |
        ForEach-Object {
          [PSCustomObject]@{
            Key = $_.Name
            Files = ($_.Group | ForEach-Object { $_.FullName })
          }
        } | ConvertTo-Json -Compress
    `;

    const raw = await this.runPowerShell(script);
    try {
      const parsed = JSON.parse(raw || '[]');
      const arr = Array.isArray(parsed) ? parsed : [parsed];
      return arr.filter(Boolean).map((item: { Key: string; Files: string[] }) => ({
        key: String(item.Key),
        files: Array.isArray(item.Files) ? item.Files.map(String) : [],
      }));
    } catch {
      return [];
    }
  }

  // ── Windows Search (WDS) via OLE DB ─────────────────────────────────

  private async searchWindowsIndex(
    query: string,
    opts: FileSearchQuery,
    limit: number,
  ): Promise<FileSearchResult[]> {
    // Build SQL for Windows Search via OLEDB provider
    const conditions: string[] = [];

    // Name/content matching
    if (opts.mode === 'content') {
      conditions.push(`CONTAINS(*, '"${query}"')`);
    } else if (opts.mode === 'filename') {
      conditions.push(`System.FileName LIKE '%${query}%'`);
    } else {
      // "everything" mode — match name or content
      conditions.push(`(System.FileName LIKE '%${query}%' OR CONTAINS(*, '"${query}"'))`);
    }

    // Extension filter
    if (opts.extensions?.length) {
      const extList = opts.extensions.map(e => `'.${e}'`).join(',');
      conditions.push(`System.FileExtension IN (${extList})`);
    }

    // Size filters
    if (opts.minSize) conditions.push(`System.Size >= ${opts.minSize}`);
    if (opts.maxSize) conditions.push(`System.Size <= ${opts.maxSize}`);

    // Date filters
    if (opts.modifiedAfter) {
      const d = typeof opts.modifiedAfter === 'number' ? new Date(opts.modifiedAfter).toISOString() : opts.modifiedAfter;
      conditions.push(`System.DateModified >= '${d}'`);
    }
    if (opts.modifiedBefore) {
      const d = typeof opts.modifiedBefore === 'number' ? new Date(opts.modifiedBefore).toISOString() : opts.modifiedBefore;
      conditions.push(`System.DateModified <= '${d}'`);
    }

    // Scope to specific paths
    let scopeClause = '';
    if (opts.searchPaths?.length) {
      const scopes = opts.searchPaths.map(p => `'${p.replace(/'/g, "''")}'`).join(',');
      scopeClause = `AND SCOPE = 'file:'`;  // WDS defaults to indexed locations
    }

    const sql = `SELECT TOP ${limit} System.ItemPathDisplay, System.FileName, System.FileExtension, System.Size, System.DateModified, System.DateCreated, System.ItemFolderPathDisplay FROM SystemIndex WHERE ${conditions.join(' AND ')} ${scopeClause} ORDER BY System.DateModified DESC`;

    const script = `
      try {
        $conn = New-Object -ComObject ADODB.Connection
        $conn.Open("Provider=Search.CollatorDSO;Extended Properties='Application=Windows';")
        $rs = $conn.Execute("${sql.replace(/"/g, '`"')}")
        $results = @()
        while (-not $rs.EOF) {
          $results += [PSCustomObject]@{
            FullName = $rs.Fields.Item("System.ItemPathDisplay").Value
            Name = $rs.Fields.Item("System.FileName").Value
            Extension = $rs.Fields.Item("System.FileExtension").Value
            Length = $rs.Fields.Item("System.Size").Value
            LastWriteTime = if ($rs.Fields.Item("System.DateModified").Value) { ([datetime]$rs.Fields.Item("System.DateModified").Value).ToString("o") } else { "" }
            CreationTime = if ($rs.Fields.Item("System.DateCreated").Value) { ([datetime]$rs.Fields.Item("System.DateCreated").Value).ToString("o") } else { "" }
            DirectoryName = $rs.Fields.Item("System.ItemFolderPathDisplay").Value
          }
          $rs.MoveNext()
        }
        $rs.Close()
        $conn.Close()
        $results | ConvertTo-Json -Compress
      } catch {
        Write-Error $_.Exception.Message
        exit 1
      }
    `;

    const raw = await this.runPowerShell(script);
    return this.parseResults(raw, 'index');
  }

  // ── PowerShell fallback scan ────────────────────────────────────────

  private async searchPowerShell(
    query: string,
    opts: FileSearchQuery,
    limit: number,
  ): Promise<FileSearchResult[]> {
    const home = process.env.USERPROFILE || process.env.HOME || '';
    const searchPaths = opts.searchPaths?.length
      ? opts.searchPaths
      : [path.join(home, 'Desktop'), path.join(home, 'Documents'), path.join(home, 'Downloads')];

    const dirs = searchPaths.map(d => `"${d.replace(/\\/g, '\\\\')}"`).join(',');
    const extFilter = opts.extensions?.length
      ? opts.extensions.map(e => `"*.${e}"`).join(',')
      : `"*${query}*"`;

    // Build filter pipeline
    const filters: string[] = [];
    if (!opts.extensions?.length) {
      filters.push(`Where-Object { $_.Name -like '*${query}*' -or $_.DirectoryName -like '*${query}*' }`);
    }
    if (opts.minSize) filters.push(`Where-Object { $_.Length -ge ${opts.minSize} }`);
    if (opts.maxSize) filters.push(`Where-Object { $_.Length -le ${opts.maxSize} }`);
    if (opts.modifiedAfter) {
      const d = typeof opts.modifiedAfter === 'number' ? new Date(opts.modifiedAfter).toISOString() : opts.modifiedAfter;
      filters.push(`Where-Object { $_.LastWriteTime -ge [datetime]'${d}' }`);
    }

    const filterPipeline = filters.length > 0 ? '| ' + filters.join(' | ') : '';

    const script = `
      $dirs = @(${dirs})
      $results = @()
      foreach ($dir in $dirs) {
        if (Test-Path $dir) {
          $results += Get-ChildItem -Path $dir -File -Recurse -Include ${extFilter} -ErrorAction SilentlyContinue -Depth 10 ${filterPipeline}
        }
      }
      $results | Sort-Object LastWriteTime -Descending |
        Select-Object -First ${limit} |
        ForEach-Object {
          [PSCustomObject]@{
            FullName = $_.FullName
            Name = $_.Name
            Extension = $_.Extension
            Length = $_.Length
            LastWriteTime = $_.LastWriteTime.ToString("o")
            CreationTime = $_.CreationTime.ToString("o")
            DirectoryName = $_.DirectoryName
          }
        } | ConvertTo-Json -Compress
    `;

    const raw = await this.runPowerShell(script);
    return this.parseResults(raw, 'name');
  }

  // ── Helpers ─────────────────────────────────────────────────────────

  private parseResults(raw: string, matchType: FileSearchResult['matchType']): FileSearchResult[] {
    if (!raw || raw.trim() === '' || raw.trim() === 'null') return [];
    try {
      const parsed = JSON.parse(raw);
      const arr: unknown[] = Array.isArray(parsed) ? parsed : [parsed];
      return arr.filter(Boolean).map((item: any) => ({
        filePath: String(item.FullName || ''),
        name: String(item.Name || ''),
        extension: String(item.Extension || '').replace(/^\./, '').toLowerCase(),
        size: Number(item.Length) || 0,
        modifiedAt: String(item.LastWriteTime || ''),
        createdAt: String(item.CreationTime || ''),
        directory: String(item.DirectoryName || ''),
        matchType,
      }));
    } catch {
      return [];
    }
  }

  private runPowerShell(script: string): Promise<string> {
    return new Promise((resolve, reject) => {
      const child = execFile(
        'powershell.exe',
        ['-NoProfile', '-NonInteractive', '-Command', script],
        {
          timeout: SEARCH_TIMEOUT_MS,
          maxBuffer: 10 * 1024 * 1024, // 10MB — search can return a lot
          env: getSanitizedEnv(),
          windowsHide: true,
        },
        (err, stdout, stderr) => {
          if (err) {
            reject(new Error(stderr || err.message));
          } else {
            resolve(stdout.trim());
          }
        },
      );
    });
  }
}

export const fileSearch = new FileSearchEngine();
