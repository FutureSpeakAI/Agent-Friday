/**
 * Dev Environments Connector for Agent Friday
 *
 * Provides tools for managing Jupyter notebooks, Python virtual environments,
 * conda environments, Docker Compose stacks, and local database queries.
 */

import { execFile, spawn } from 'child_process';
import { promisify } from 'util';
import * as path from 'path';
import * as fs from 'fs';
import { getSanitizedEnv } from '../settings';

const execFileAsync = promisify(execFile);
const fsAccess = promisify(fs.access);
const fsReaddir = promisify(fs.readdir);

const MAX_OUTPUT_BYTES = 64 * 1024;
const DEFAULT_TIMEOUT_MS = 60_000;

const DANGEROUS_PIP_PATTERNS: RegExp[] = [
  /keylog/i, /keystroke/i, /screenlog/i, /rat[-_]?tool/i,
  /reverse[-_]?shell/i, /trojan/i, /exploit/i, /payload[-_]?gen/i,
];

const PRODUCTION_DB_PATTERNS: RegExp[] = [
  /prod/i, /production/i, /\.rds\.amazonaws\.com/i,
  /\.database\.azure\.com/i, /\.cloudsql\.google/i,
];

const DANGEROUS_SQL_PATTERNS: RegExp[] = [
  /\bDROP\b/i, /\bTRUNCATE\b/i, /\bDELETE\s+FROM\b/i,
  /\bALTER\s+TABLE\b.*\bDROP\b/i,
];

export const TOOLS = [
  { name: 'jupyter_start', description: 'Start a Jupyter notebook server.', parameters: { type: 'object' as const, properties: { directory: { type: 'string', description: 'Working directory for the Jupyter server.' }, port: { type: 'number', description: 'Port to listen on (default 8888).' } }, required: [] as string[] } },
  { name: 'jupyter_list', description: 'List running Jupyter notebook servers.', parameters: { type: 'object' as const, properties: {}, required: [] as string[] } },
  { name: 'jupyter_stop', description: 'Stop a running Jupyter server by port.', parameters: { type: 'object' as const, properties: { port: { type: 'number', description: 'Port of the Jupyter server to stop.' } }, required: ['port'] } },
  { name: 'python_venv_create', description: 'Create a Python virtual environment.', parameters: { type: 'object' as const, properties: { path: { type: 'string', description: 'Filesystem path for the new venv.' }, python_path: { type: 'string', description: 'Path to a specific Python interpreter.' } }, required: ['path'] } },
  { name: 'python_venv_list', description: 'List Python venvs under a directory.', parameters: { type: 'object' as const, properties: { search_path: { type: 'string', description: 'Root directory to search.' } }, required: ['search_path'] } },
  { name: 'python_pip_install', description: 'Install Python packages in a venv.', parameters: { type: 'object' as const, properties: { venv_path: { type: 'string', description: 'Path to the venv.' }, packages: { type: 'string', description: 'Space-separated package list.' } }, required: ['venv_path', 'packages'] } },
  { name: 'python_pip_list', description: 'List installed packages in a venv.', parameters: { type: 'object' as const, properties: { venv_path: { type: 'string', description: 'Path to the venv.' } }, required: ['venv_path'] } },
  { name: 'python_run', description: 'Run a Python script.', parameters: { type: 'object' as const, properties: { script_path: { type: 'string', description: 'Path to the script.' }, args: { type: 'string', description: 'Optional arguments.' }, venv_path: { type: 'string', description: 'Optional venv.' }, timeout_seconds: { type: 'number', description: 'Timeout (default 60).' } }, required: ['script_path'] } },
  { name: 'conda_env_list', description: 'List conda environments.', parameters: { type: 'object' as const, properties: {}, required: [] as string[] } },
  { name: 'conda_env_create', description: 'Create a new conda environment.', parameters: { type: 'object' as const, properties: { name: { type: 'string', description: 'Environment name.' }, python_version: { type: 'string', description: 'Python version (e.g. "3.11").' }, packages: { type: 'string', description: 'Space-separated packages.' } }, required: ['name'] } },
  { name: 'conda_install', description: 'Install packages in a conda env.', parameters: { type: 'object' as const, properties: { env_name: { type: 'string', description: 'Conda env name.' }, packages: { type: 'string', description: 'Space-separated packages.' } }, required: ['env_name', 'packages'] } },
  { name: 'docker_compose_up', description: 'Run docker-compose up.', parameters: { type: 'object' as const, properties: { compose_path: { type: 'string', description: 'Path to docker-compose.yml.' }, detach: { type: 'boolean', description: 'Detached mode (default true).' }, services: { type: 'string', description: 'Services to start (optional).' } }, required: ['compose_path'] } },
  { name: 'docker_compose_down', description: 'Run docker-compose down.', parameters: { type: 'object' as const, properties: { compose_path: { type: 'string', description: 'Path to docker-compose.yml.' }, volumes: { type: 'boolean', description: 'Remove volumes (default false).' } }, required: ['compose_path'] } },
  { name: 'docker_compose_logs', description: 'Get docker-compose logs.', parameters: { type: 'object' as const, properties: { compose_path: { type: 'string', description: 'Path to docker-compose.yml.' }, service: { type: 'string', description: 'Specific service (optional).' }, tail: { type: 'number', description: 'Lines to tail (default 100).' } }, required: ['compose_path'] } },
  { name: 'database_query', description: 'Run a SQL query against a local database.', parameters: { type: 'object' as const, properties: { engine: { type: 'string', enum: ['sqlite', 'postgres', 'mysql'], description: 'Database engine.' }, connection_string: { type: 'string', description: 'Connection string / path.' }, query: { type: 'string', description: 'SQL query.' }, timeout_seconds: { type: 'number', description: 'Timeout in seconds (default 30).' } }, required: ['engine', 'connection_string', 'query'] } },
];

function cap(output: string): string {
  if (Buffer.byteLength(output, 'utf-8') <= MAX_OUTPUT_BYTES) return output;
  const truncated = Buffer.from(output, 'utf-8').subarray(0, MAX_OUTPUT_BYTES).toString('utf-8');
  return truncated + '\n... [output truncated at 64 KB]';
}

function venvPython(venvPath: string): string {
  return process.platform === 'win32'
    ? path.join(venvPath, 'Scripts', 'python.exe')
    : path.join(venvPath, 'bin', 'python');
}

function venvPip(venvPath: string): string {
  return process.platform === 'win32'
    ? path.join(venvPath, 'Scripts', 'pip.exe')
    : path.join(venvPath, 'bin', 'pip');
}

// Crypto Sprint 3: shell: true is restricted to Windows only (needed for .cmd files
// like npm.cmd, pip.cmd, etc.). On Linux/macOS, shell is disabled to prevent
// shell metacharacter injection if any args are derived from untrusted input.
async function run(
  command: string, args: string[],
  options: { cwd?: string; timeoutMs?: number; stdin?: string; env?: NodeJS.ProcessEnv } = {},
): Promise<{ stdout: string; stderr: string }> {
  const timeout = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  return new Promise((resolve, reject) => {
    const proc = spawn(command, args, {
      // Crypto Sprint 15 (HIGH): Default to getSanitizedEnv() to prevent API key leakage
      // to child processes (jupyter, conda, pip, python, psql, mysql, npm, docker-compose).
      cwd: options.cwd, env: options.env ?? getSanitizedEnv() as NodeJS.ProcessEnv,
      shell: process.platform === 'win32', windowsHide: true,
    });
    let stdout = '', stderr = '', killed = false;
    const timer = setTimeout(() => {
      killed = true; proc.kill('SIGKILL');
      reject(new Error(`Command timed out after ${timeout / 1000}s`));
    }, timeout);
    proc.stdout?.on('data', (chunk: Buffer) => {
      stdout += chunk.toString();
      if (Buffer.byteLength(stdout, 'utf-8') > MAX_OUTPUT_BYTES * 2) stdout = cap(stdout);
    });
    proc.stderr?.on('data', (chunk: Buffer) => {
      stderr += chunk.toString();
      if (Buffer.byteLength(stderr, 'utf-8') > MAX_OUTPUT_BYTES * 2) stderr = cap(stderr);
    });
    if (options.stdin) { proc.stdin?.write(options.stdin); proc.stdin?.end(); }
    proc.on('close', (code) => {
      clearTimeout(timer); if (killed) return;
      if (code !== 0) reject(new Error(cap(stderr.trim() || `Process exited with code ${code}`)));
      else resolve({ stdout: cap(stdout), stderr: cap(stderr) });
    });
    proc.on('error', (err) => { clearTimeout(timer); if (!killed) reject(err); });
  });
}

async function isOnPath(bin: string): Promise<boolean> {
  try {
    await execFileAsync(process.platform === 'win32' ? 'where' : 'which', [bin], { timeout: 5000 });
    return true;
  } catch { return false; }
}

async function resolveDockerCompose(): Promise<string[]> {
  try { await execFileAsync('docker', ['compose', 'version'], { timeout: 5000 }); return ['docker', 'compose']; } catch { /* not found */ }
  try { await execFileAsync('docker-compose', ['version'], { timeout: 5000 }); return ['docker-compose']; } catch { /* not found */ }
  throw new Error('Neither "docker compose" nor "docker-compose" found on PATH.');
}

export async function detect(): Promise<boolean> {
  const [hasPython, hasPython3] = await Promise.all([isOnPath('python'), isOnPath('python3')]);
  return hasPython || hasPython3;
}

async function jupyterStart(args: Record<string, unknown>): Promise<string> {
  const port = (args.port as number) ?? 8888;
  const directory = (args.directory as string) ?? process.cwd();
  return new Promise((resolve, reject) => {
    // Crypto Sprint 3: shell only on Windows (needed for .cmd wrappers)
    const proc = spawn('jupyter', ['notebook', '--no-browser', `--port=${port}`, `--notebook-dir=${directory}`],
      { shell: process.platform === 'win32', windowsHide: true, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
    let output = '', resd = false;
    const timer = setTimeout(() => {
      if (!resd) { resd = true; proc.unref();
        resolve(output.trim() || `Jupyter server starting on port ${port}. Use "jupyter_list" to check status.`); }
    }, 15_000);
    const hd = (chunk: Buffer) => {
      output += chunk.toString();
      const m = output.match(/(https?:\/\/[^\s]+\?token=[^\s]+)/);
      if (m && !resd) { resd = true; clearTimeout(timer); proc.unref(); resolve(`Jupyter server started: ${m[1]}`); }
    };
    proc.stdout?.on('data', hd); proc.stderr?.on('data', hd);
    proc.on('error', (e) => { clearTimeout(timer); if (!resd) { resd = true; reject(e); } });
    proc.on('close', (code) => {
      clearTimeout(timer);
      if (!resd) { resd = true;
        if (code !== 0) reject(new Error(cap(output) || `Jupyter exited with code ${code}`));
        else resolve(output.trim() || 'Jupyter server started.'); }
    });
  });
}

async function jupyterList(): Promise<string> {
  const { stdout } = await run('jupyter', ['notebook', 'list'], { timeoutMs: 15_000 });
  return stdout.trim() || 'No running Jupyter servers found.';
}

async function jupyterStop(args: Record<string, unknown>): Promise<string> {
  const port = args.port as number;
  if (!port) throw new Error('port is required');
  const { stdout, stderr } = await run('jupyter', ['notebook', 'stop', String(port)], { timeoutMs: 15_000 });
  return (stdout + stderr).trim() || `Jupyter server on port ${port} stopped.`;
}

async function pythonVenvCreate(args: Record<string, unknown>): Promise<string> {
  const venvPath = args.path as string;
  if (!venvPath) throw new Error('path is required');
  const pythonBin = (args.python_path as string) ?? 'python';
  const { stdout, stderr } = await run(pythonBin, ['-m', 'venv', venvPath], { timeoutMs: 120_000 });
  return (stdout + stderr).trim() || `Virtual environment created at ${venvPath}`;
}

async function pythonVenvList(args: Record<string, unknown>): Promise<string> {
  const searchPath = args.search_path as string;
  if (!searchPath) throw new Error('search_path is required');
  const results: string[] = [];
  async function scan(dir: string, depth: number): Promise<void> {
    if (depth > 3) return;
    try {
      const entries = await fsReaddir(dir, { withFileTypes: true });
      for (const entry of entries) {
        if (!entry.isDirectory() || entry.name.startsWith('.') || entry.name === 'node_modules') continue;
        const full = path.join(dir, entry.name);
        try { await fsAccess(path.join(full, 'pyvenv.cfg')); results.push(full); }
        catch { await scan(full, depth + 1); }
      }
    } catch { /* permission denied */ }
  }
  await scan(searchPath, 0);
  return results.length > 0 ? `Found ${results.length} virtual environment(s):\n${results.join('\n')}` : 'No virtual environments found.';
}

async function pythonPipInstall(args: Record<string, unknown>): Promise<string> {
  const venvPath = args.venv_path as string;
  const packages = args.packages as string;
  if (!venvPath) throw new Error('venv_path is required');
  if (!packages) throw new Error('packages is required');
  const pkgList = packages.split(/\s+/).filter(Boolean);
  for (const pkg of pkgList) {
    for (const pattern of DANGEROUS_PIP_PATTERNS) {
      if (pattern.test(pkg)) throw new Error(`Blocked: package "${pkg}" matches dangerous pattern (${pattern}). Installation refused.`);
    }
  }
  const pip = venvPip(venvPath);
  const { stdout, stderr } = await run(pip, ['install', ...pkgList], { timeoutMs: 300_000 });
  return cap((stdout + '\n' + stderr).trim());
}

async function pythonPipList(args: Record<string, unknown>): Promise<string> {
  const venvPath = args.venv_path as string;
  if (!venvPath) throw new Error('venv_path is required');
  const { stdout } = await run(venvPip(venvPath), ['list', '--format=columns'], { timeoutMs: 30_000 });
  return stdout.trim() || 'No packages installed.';
}

async function pythonRun(args: Record<string, unknown>): Promise<string> {
  const scriptPath = args.script_path as string;
  if (!scriptPath) throw new Error('script_path is required');
  const scriptArgs = args.args ? (args.args as string).split(/\s+/) : [];
  const timeoutSec = (args.timeout_seconds as number) ?? 60;
  const pyBin = args.venv_path ? venvPython(args.venv_path as string) : 'python';
  const { stdout, stderr } = await run(pyBin, [scriptPath, ...scriptArgs], { timeoutMs: timeoutSec * 1000 });
  return (stdout + '\n' + stderr).trim() || 'Script completed with no output.';
}

async function condaEnvList(): Promise<string> {
  const { stdout } = await run('conda', ['env', 'list', '--json'], { timeoutMs: 30_000 });
  return stdout.trim();
}

async function condaEnvCreate(args: Record<string, unknown>): Promise<string> {
  const name = args.name as string;
  if (!name) throw new Error('name is required');
  const pyV = args.python_version as string | undefined;
  const pkgs = args.packages ? (args.packages as string).split(/\s+/) : [];
  const a = ['create', '-n', name, '-y'];
  if (pyV) a.push(`python=${pyV}`);
  a.push(...pkgs);
  const { stdout, stderr } = await run('conda', a, { timeoutMs: 600_000 });
  return cap((stdout + '\n' + stderr).trim());
}

async function condaInstall(args: Record<string, unknown>): Promise<string> {
  const envName = args.env_name as string;
  const packages = args.packages as string;
  if (!envName) throw new Error('env_name is required');
  if (!packages) throw new Error('packages is required');
  const pkgs = packages.split(/\s+/).filter(Boolean);
  const { stdout, stderr } = await run('conda', ['install', '-n', envName, '-y', ...pkgs], { timeoutMs: 600_000 });
  return cap((stdout + '\n' + stderr).trim());
}

async function dockerComposeUp(args: Record<string, unknown>): Promise<string> {
  const composePath = args.compose_path as string;
  if (!composePath) throw new Error('compose_path is required');
  const detach = (args.detach as boolean) ?? true;
  const services = args.services ? (args.services as string).split(/\s+/) : [];
  const [base, ...prefix] = await resolveDockerCompose();
  const dir = path.dirname(composePath), file = path.basename(composePath);
  const a = [...prefix, '-f', file, 'up'];
  if (detach) a.push('-d');
  a.push(...services);
  const { stdout, stderr } = await run(base, a, { cwd: dir, timeoutMs: 300_000 });
  return cap((stdout + '\n' + stderr).trim()) || 'docker-compose up completed.';
}

async function dockerComposeDown(args: Record<string, unknown>): Promise<string> {
  const composePath = args.compose_path as string;
  if (!composePath) throw new Error('compose_path is required');
  const volumes = (args.volumes as boolean) ?? false;
  const [base, ...prefix] = await resolveDockerCompose();
  const dir = path.dirname(composePath), file = path.basename(composePath);
  const a = [...prefix, '-f', file, 'down'];
  if (volumes) a.push('-v');
  const { stdout, stderr } = await run(base, a, { cwd: dir, timeoutMs: 120_000 });
  return cap((stdout + '\n' + stderr).trim()) || 'docker-compose down completed.';
}

async function dockerComposeLogs(args: Record<string, unknown>): Promise<string> {
  const composePath = args.compose_path as string;
  if (!composePath) throw new Error('compose_path is required');
  const service = args.service as string | undefined;
  const tail = (args.tail as number) ?? 100;
  const [base, ...prefix] = await resolveDockerCompose();
  const dir = path.dirname(composePath), file = path.basename(composePath);
  const a = [...prefix, '-f', file, 'logs', '--no-color', `--tail=${tail}`];
  if (service) a.push(service);
  const { stdout, stderr } = await run(base, a, { cwd: dir, timeoutMs: 30_000 });
  return cap((stdout + '\n' + stderr).trim()) || 'No logs available.';
}

async function databaseQuery(args: Record<string, unknown>): Promise<string> {
  const engine = args.engine as string;
  const connStr = args.connection_string as string;
  const query = args.query as string;
  const timeoutSec = (args.timeout_seconds as number) ?? 30;
  if (!engine) throw new Error('engine is required');
  if (!connStr) throw new Error('connection_string is required');
  if (!query) throw new Error('query is required');
  const looksProd = PRODUCTION_DB_PATTERNS.some((p) => p.test(connStr));
  if (looksProd) {
    for (const pattern of DANGEROUS_SQL_PATTERNS) {
      if (pattern.test(query)) throw new Error(`Blocked: destructive SQL (${pattern}) against production-looking database. Refusing.`);
    }
  }
  const tms = timeoutSec * 1000;
  switch (engine) {
    case 'sqlite': {
      const { stdout } = await run('sqlite3', ['-header', '-column', connStr, query], { timeoutMs: tms });
      return stdout.trim() || 'Query completed with no output.';
    }
    case 'postgres': {
      const { stdout } = await run('psql', [connStr, '-c', query], { timeoutMs: tms });
      return stdout.trim() || 'Query completed with no output.';
    }
    case 'mysql': {
      const mArgs: string[] = [];
      const parts = connStr.match(/(\w+)=([^\s]+)/g) || [];
      const cm: Record<string, string> = {};
      for (const p of parts) { const [k, ...r] = p.split('='); cm[k.toLowerCase()] = r.join('='); }
      if (cm.host) mArgs.push(`-h${cm.host}`);
      if (cm.port) mArgs.push(`-P${cm.port}`);
      if (cm.user) mArgs.push(`-u${cm.user}`);
      if (cm.password) mArgs.push(`-p${cm.password}`);
      if (cm.database || cm.db) mArgs.push(cm.database || cm.db);
      const { stdout } = await run('mysql', [...mArgs, '-e', query], { timeoutMs: tms });
      return stdout.trim() || 'Query completed with no output.';
    }
    default: throw new Error(`Unsupported database engine: ${engine}. Use sqlite, postgres, or mysql.`);
  }
}

export async function execute(
  toolName: string, args: Record<string, unknown>,
): Promise<{ result?: string; error?: string }> {
  try {
    let result: string;
    switch (toolName) {
      case 'jupyter_start': result = await jupyterStart(args); break;
      case 'jupyter_list': result = await jupyterList(); break;
      case 'jupyter_stop': result = await jupyterStop(args); break;
      case 'python_venv_create': result = await pythonVenvCreate(args); break;
      case 'python_venv_list': result = await pythonVenvList(args); break;
      case 'python_pip_install': result = await pythonPipInstall(args); break;
      case 'python_pip_list': result = await pythonPipList(args); break;
      case 'python_run': result = await pythonRun(args); break;
      case 'conda_env_list': result = await condaEnvList(); break;
      case 'conda_env_create': result = await condaEnvCreate(args); break;
      case 'conda_install': result = await condaInstall(args); break;
      case 'docker_compose_up': result = await dockerComposeUp(args); break;
      case 'docker_compose_down': result = await dockerComposeDown(args); break;
      case 'docker_compose_logs': result = await dockerComposeLogs(args); break;
      case 'database_query': result = await databaseQuery(args); break;
      default: return { error: `Unknown tool: ${toolName}` };
    }
    return { result };
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    return { error: cap(message) };
  }
}
