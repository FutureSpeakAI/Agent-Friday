/**
 * creative-3d.ts — 3D & VFX application connector for NEXUS OS.
 *
 * Provides AI agent tooling for controlling Blender, Unity, and Unreal Engine
 * on Windows through their respective CLI interfaces. Blender tools use
 * `--background --python` for headless Python scripting; Unity uses
 * `-batchmode -executeMethod`; Unreal uses UnrealEditor-Cmd.
 *
 * Exports:
 *   TOOLS    — Array of tool declarations for the agent tool registry
 *   execute  — Async handler that dispatches tool calls by name
 *   detect   — Async check for whether any supported 3D app is installed
 */

import { execFile, execFileSync, spawn } from 'child_process';
import { promisify } from 'util';
import * as path from 'path';
import * as fs from 'fs';
import * as os from 'os';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ToolDeclaration {
  name: string;
  description: string;
  parameters: {
    type: 'object';
    properties: Record<string, any>;
    required: string[];
  };
}

interface ToolResult {
  result?: string;
  error?: string;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Maximum characters returned in any single tool result. */
const MAX_OUTPUT_CHARS = 8_000;

/** Default timeout for quick CLI operations (30 s). */
const DEFAULT_TIMEOUT_MS = 30_000;

/** Extended timeout for renders and builds (300 s / 5 min). */
const RENDER_TIMEOUT_MS = 300_000;

/** Directory for temporary Blender Python scripts. */
const TEMP_DIR = path.join(os.tmpdir(), 'friday-creative-3d');

/** Dangerous Python patterns that should never run in Blender. */
const DANGEROUS_PYTHON_PATTERNS: RegExp[] = [
  /\bos\.system\b/,
  /\bsubprocess\b/,
  /\bshutil\.rmtree\b/,
  /\b__import__\b/,
  /\beval\b\s*\(/,
  /\bexec\b\s*\(/,
  /\bopen\b\s*\(.*['"][wa]['"]\s*\)/i,
  /\bkeylog/i,
  /\breverse.?shell/i,
];

// ---------------------------------------------------------------------------
// Binary resolution — Blender
// ---------------------------------------------------------------------------

/** Well-known Blender install directories on Windows. */
const BLENDER_SEARCH_PATHS: string[] = [
  // Scoop / Chocolatey / winget commonly place it here:
  path.join(process.env['ProgramFiles'] || 'C:\\Program Files', 'Blender Foundation'),
  // Steam:
  path.join(process.env['ProgramFiles(x86)'] || 'C:\\Program Files (x86)', 'Steam', 'steamapps', 'common', 'Blender'),
  // User-level install:
  path.join(os.homedir(), 'AppData', 'Local', 'Programs', 'Blender Foundation'),
];

let cachedBlenderPath: string | null = null;

/**
 * Locate the blender.exe binary.
 * 1. Check PATH via `blender --version`.
 * 2. Scan well-known install directories for versioned sub-folders.
 */
function resolveBlenderBin(): string | null {
  if (cachedBlenderPath) return cachedBlenderPath;

  // 1. Try PATH
  try {
    // Crypto Sprint 12: Use execFileSync — no shell needed for version detection.
    execFileSync('blender', ['--version'], { timeout: 5_000, stdio: 'pipe', windowsHide: true });
    cachedBlenderPath = 'blender';
    return cachedBlenderPath;
  } catch { /* not in PATH */ }

  // 2. Scan known directories
  for (const base of BLENDER_SEARCH_PATHS) {
    if (!fs.existsSync(base)) continue;
    try {
      // Blender Foundation dirs contain versioned folders like "Blender 4.1"
      const entries = fs.readdirSync(base).sort().reverse(); // newest first
      for (const entry of entries) {
        const candidate = path.join(base, entry, 'blender.exe');
        if (fs.existsSync(candidate)) {
          cachedBlenderPath = candidate;
          return cachedBlenderPath;
        }
      }
    } catch { /* permission denied or similar — skip */ }
  }

  return null;
}

// ---------------------------------------------------------------------------
// Binary resolution — Unity
// ---------------------------------------------------------------------------

const UNITY_HUB_PATHS: string[] = [
  path.join(process.env['ProgramFiles'] || 'C:\\Program Files', 'Unity Hub', 'Unity Hub.exe'),
  path.join(process.env['ProgramFiles(x86)'] || 'C:\\Program Files (x86)', 'Unity Hub', 'Unity Hub.exe'),
  path.join(process.env.LOCALAPPDATA || '', 'Programs', 'Unity Hub', 'Unity Hub.exe'),
];

const UNITY_EDITOR_ROOTS: string[] = [
  path.join(process.env['ProgramFiles'] || 'C:\\Program Files', 'Unity', 'Hub', 'Editor'),
  path.join(process.env['ProgramFiles(x86)'] || 'C:\\Program Files (x86)', 'Unity', 'Hub', 'Editor'),
];

let cachedUnityEditorPath: string | null = null;

/**
 * Find the newest Unity Editor binary.
 * Scans `<Unity Hub>/Editor/<version>/Editor/Unity.exe`.
 */
function resolveUnityEditor(): string | null {
  if (cachedUnityEditorPath) return cachedUnityEditorPath;

  for (const root of UNITY_EDITOR_ROOTS) {
    if (!fs.existsSync(root)) continue;
    try {
      const versions = fs.readdirSync(root).sort().reverse();
      for (const ver of versions) {
        const candidate = path.join(root, ver, 'Editor', 'Unity.exe');
        if (fs.existsSync(candidate)) {
          cachedUnityEditorPath = candidate;
          return cachedUnityEditorPath;
        }
      }
    } catch { /* skip */ }
  }

  return null;
}

function isUnityHubInstalled(): boolean {
  for (const p of UNITY_HUB_PATHS) {
    if (fs.existsSync(p)) return true;
  }
  return false;
}

// ---------------------------------------------------------------------------
// Binary resolution — Unreal Engine
// ---------------------------------------------------------------------------

const UNREAL_ROOTS: string[] = [
  path.join(process.env['ProgramFiles'] || 'C:\\Program Files', 'Epic Games'),
  path.join(process.env['ProgramFiles(x86)'] || 'C:\\Program Files (x86)', 'Epic Games'),
  'D:\\Epic Games',
];

let cachedUnrealEditorPath: string | null = null;

/**
 * Find the newest UnrealEditor-Cmd.exe for headless command execution.
 */
function resolveUnrealEditor(): string | null {
  if (cachedUnrealEditorPath) return cachedUnrealEditorPath;

  for (const root of UNREAL_ROOTS) {
    if (!fs.existsSync(root)) continue;
    try {
      const entries = fs.readdirSync(root).filter(e => e.startsWith('UE_')).sort().reverse();
      for (const entry of entries) {
        const candidate = path.join(root, entry, 'Engine', 'Binaries', 'Win64', 'UnrealEditor-Cmd.exe');
        if (fs.existsSync(candidate)) {
          cachedUnrealEditorPath = candidate;
          return cachedUnrealEditorPath;
        }
      }
    } catch { /* skip */ }
  }

  return null;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function truncate(text: string, limit: number = MAX_OUTPUT_CHARS): string {
  if (text.length <= limit) return text;
  return text.slice(0, limit) + `\n\n--- Output truncated (${text.length} chars total, showing first ${limit}) ---`;
}

function ok(text: string): ToolResult {
  return { result: truncate(text.trim()) };
}

function fail(msg: string): ToolResult {
  return { error: msg };
}

/** Ensure the temp directory exists for writing Blender scripts. */
function ensureTempDir(): void {
  if (!fs.existsSync(TEMP_DIR)) {
    fs.mkdirSync(TEMP_DIR, { recursive: true });
  }
}

/** Write a Python script to a temp file and return its path. */
function writeTempScript(script: string, name?: string): string {
  ensureTempDir();
  const filename = name || `bpy_${Date.now()}_${Math.random().toString(36).slice(2, 8)}.py`;
  const scriptPath = path.join(TEMP_DIR, filename);
  fs.writeFileSync(scriptPath, script, 'utf-8');
  return scriptPath;
}

/** Clean up a temp script after execution. */
function cleanupTempScript(scriptPath: string): void {
  try {
    if (fs.existsSync(scriptPath)) fs.unlinkSync(scriptPath);
  } catch { /* best-effort */ }
}

/**
 * Validate a file path exists and has the expected extension.
 */
function validateFilePath(filePath: string, extensions?: string[]): void {
  if (!filePath || typeof filePath !== 'string') {
    throw new Error('File path is required.');
  }
  if (!path.isAbsolute(filePath)) {
    throw new Error(`Path must be absolute: ${filePath}`);
  }
  if (extensions && extensions.length > 0) {
    const ext = path.extname(filePath).toLowerCase();
    if (!extensions.includes(ext)) {
      throw new Error(`Invalid file extension "${ext}". Expected one of: ${extensions.join(', ')}`);
    }
  }
}

/**
 * Run a child process with a promise-based timeout.
 * Collects both stdout and stderr. Returns combined output on success.
 * Throws on non-zero exit or timeout.
 */
function runProcess(
  command: string,
  args: string[],
  opts: { timeout?: number; cwd?: string } = {}
): Promise<string> {
  const { timeout = DEFAULT_TIMEOUT_MS, cwd } = opts;

  return new Promise<string>((resolve, reject) => {
    const proc = spawn(command, args, {
      cwd,
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe'],
    });

    let stdout = '';
    let stderr = '';
    let killed = false;

    const timer = setTimeout(() => {
      killed = true;
      proc.kill('SIGKILL');
      reject(new Error(`Process timed out after ${timeout / 1000}s`));
    }, timeout);

    proc.stdout?.on('data', (chunk: Buffer) => { stdout += chunk.toString(); });
    proc.stderr?.on('data', (chunk: Buffer) => { stderr += chunk.toString(); });

    proc.on('error', (err) => {
      clearTimeout(timer);
      reject(err);
    });

    proc.on('close', (code) => {
      clearTimeout(timer);
      if (killed) return; // already rejected by timeout
      if (code !== 0) {
        reject(new Error(stderr.trim() || stdout.trim() || `Process exited with code ${code}`));
      } else {
        resolve(stdout + (stderr ? `\n[stderr]: ${stderr}` : ''));
      }
    });
  });
}

/**
 * Check whether a Python script contains dangerous patterns.
 */
function validatePythonScript(script: string): void {
  for (const pattern of DANGEROUS_PYTHON_PATTERNS) {
    if (pattern.test(script)) {
      throw new Error(
        `Script contains a blocked pattern (${pattern.source}). ` +
        'For safety, os.system(), subprocess, eval(), exec(), and similar calls are not permitted.'
      );
    }
  }
}

// ---------------------------------------------------------------------------
// Tool Declarations
// ---------------------------------------------------------------------------

export const TOOLS: ToolDeclaration[] = [
  // ── Blender ─────────────────────────────────────────────────────────────
  {
    name: 'blender_run_script',
    description:
      'Execute a Blender Python (bpy) script in headless/background mode. ' +
      'The script has full access to the Blender Python API. ' +
      'Use print() for output — stdout is captured and returned. ' +
      'Optionally open a .blend file before running the script.',
    parameters: {
      type: 'object',
      properties: {
        script: {
          type: 'string',
          description: 'Python script source code to execute inside Blender.',
        },
        blend_file: {
          type: 'string',
          description: 'Optional absolute path to a .blend file to open before running the script.',
        },
        timeout_seconds: {
          type: 'number',
          description: 'Execution timeout in seconds (default: 60, max: 300).',
        },
      },
      required: ['script'],
    },
  },
  {
    name: 'blender_render',
    description:
      'Render the current Blender scene to an image or animation. ' +
      'Runs in background mode. Returns the output file path on success.',
    parameters: {
      type: 'object',
      properties: {
        blend_file: {
          type: 'string',
          description: 'Absolute path to the .blend file to render.',
        },
        output_path: {
          type: 'string',
          description: 'Absolute path for the rendered output (e.g. C:/renders/frame_####.png). ' +
            'Use #### for frame number padding in animations.',
        },
        format: {
          type: 'string',
          description: 'Output format: PNG, JPEG, BMP, TIFF, OPEN_EXR, AVI_JPEG, AVI_RAW, FFMPEG (default: PNG).',
        },
        frame_start: {
          type: 'number',
          description: 'First frame to render (default: scene start frame).',
        },
        frame_end: {
          type: 'number',
          description: 'Last frame to render (default: scene end frame). Set equal to frame_start for a single frame.',
        },
        engine: {
          type: 'string',
          description: 'Render engine: CYCLES, BLENDER_EEVEE, BLENDER_EEVEE_NEXT, BLENDER_WORKBENCH (default: scene setting).',
        },
        timeout_seconds: {
          type: 'number',
          description: 'Render timeout in seconds (default: 300).',
        },
      },
      required: ['blend_file', 'output_path'],
    },
  },
  {
    name: 'blender_open_file',
    description:
      'Open a .blend file in Blender (GUI mode). Launches Blender with the specified file.',
    parameters: {
      type: 'object',
      properties: {
        blend_file: {
          type: 'string',
          description: 'Absolute path to the .blend file to open.',
        },
      },
      required: ['blend_file'],
    },
  },
  {
    name: 'blender_export',
    description:
      'Export the current Blender scene to a 3D file format. ' +
      'Supported formats: FBX, OBJ, GLTF (glTF 2.0), STL. ' +
      'Runs headless via a Python export script.',
    parameters: {
      type: 'object',
      properties: {
        blend_file: {
          type: 'string',
          description: 'Absolute path to the .blend file containing the scene to export.',
        },
        output_path: {
          type: 'string',
          description: 'Absolute path for the exported file (extension determines format: .fbx, .obj, .glb/.gltf, .stl).',
        },
        format: {
          type: 'string',
          description: 'Export format override: FBX, OBJ, GLTF, GLB, STL. If omitted, inferred from output_path extension.',
        },
        selected_only: {
          type: 'boolean',
          description: 'Export only selected objects (default: false — exports entire scene).',
        },
      },
      required: ['blend_file', 'output_path'],
    },
  },
  {
    name: 'blender_import',
    description:
      'Import a 3D file into a Blender scene. ' +
      'Supported formats: FBX, OBJ, glTF/GLB, STL. ' +
      'Runs headless — the result is saved back to the .blend file.',
    parameters: {
      type: 'object',
      properties: {
        blend_file: {
          type: 'string',
          description: 'Absolute path to the .blend file to import into. Created if it does not exist.',
        },
        import_path: {
          type: 'string',
          description: 'Absolute path to the 3D file to import (.fbx, .obj, .glb, .gltf, .stl).',
        },
      },
      required: ['blend_file', 'import_path'],
    },
  },

  // ── Unity ───────────────────────────────────────────────────────────────
  {
    name: 'unity_run_method',
    description:
      'Execute a static C# method in the Unity Editor via the -executeMethod CLI flag. ' +
      'The method must be public, static, and parameterless (use EditorPrefs for input). ' +
      'Unity runs in batch mode (headless) and logs output to stdout.',
    parameters: {
      type: 'object',
      properties: {
        project_path: {
          type: 'string',
          description: 'Absolute path to the Unity project root folder (contains Assets/).',
        },
        method: {
          type: 'string',
          description: 'Fully qualified static method name, e.g. "MyNamespace.BuildScript.PerformBuild".',
        },
        extra_args: {
          type: 'string',
          description: 'Additional CLI arguments to pass to Unity (space-separated).',
        },
        timeout_seconds: {
          type: 'number',
          description: 'Execution timeout in seconds (default: 120, max: 300).',
        },
      },
      required: ['project_path', 'method'],
    },
  },
  {
    name: 'unity_open_project',
    description:
      'Open a Unity project in the Unity Editor (GUI mode). ' +
      'Uses the Unity Hub CLI or launches the Editor directly.',
    parameters: {
      type: 'object',
      properties: {
        project_path: {
          type: 'string',
          description: 'Absolute path to the Unity project root folder.',
        },
      },
      required: ['project_path'],
    },
  },
  {
    name: 'unity_build',
    description:
      'Build a Unity project for a target platform. Runs in batch mode. ' +
      'Requires a static BuildScript method in the project (or uses the default build pipeline).',
    parameters: {
      type: 'object',
      properties: {
        project_path: {
          type: 'string',
          description: 'Absolute path to the Unity project root folder.',
        },
        build_target: {
          type: 'string',
          description: 'Target platform: Win64, Android, iOS, WebGL, Linux64, OSXUniversal (default: Win64).',
        },
        output_path: {
          type: 'string',
          description: 'Absolute path for the build output (directory or executable).',
        },
        method: {
          type: 'string',
          description: 'Custom build method to call (fully qualified static method). ' +
            'If omitted, a default build script is generated.',
        },
        timeout_seconds: {
          type: 'number',
          description: 'Build timeout in seconds (default: 300).',
        },
      },
      required: ['project_path', 'output_path'],
    },
  },

  // ── Unreal Engine ──────────────────────────────────────────────────────
  {
    name: 'unreal_run_command',
    description:
      'Execute an Unreal Editor command via the UnrealEditor-Cmd CLI. ' +
      'This runs the editor in commandlet mode for automation tasks like ' +
      'cooking content, running tests, or custom commandlets.',
    parameters: {
      type: 'object',
      properties: {
        project_path: {
          type: 'string',
          description: 'Absolute path to the .uproject file.',
        },
        command: {
          type: 'string',
          description: 'Unreal command/commandlet to execute, e.g. "-run=cook -targetplatform=Windows" ' +
            'or "-run=automation RunTests MyTest".',
        },
        timeout_seconds: {
          type: 'number',
          description: 'Execution timeout in seconds (default: 120, max: 300).',
        },
      },
      required: ['project_path', 'command'],
    },
  },
  {
    name: 'unreal_build',
    description:
      'Build an Unreal Engine project using UnrealBuildTool. ' +
      'Compiles the project for a specified configuration and platform.',
    parameters: {
      type: 'object',
      properties: {
        project_path: {
          type: 'string',
          description: 'Absolute path to the .uproject file.',
        },
        config: {
          type: 'string',
          description: 'Build configuration: Development, DebugGame, Shipping, Test (default: Development).',
        },
        platform: {
          type: 'string',
          description: 'Target platform: Win64, Linux, Mac, Android, iOS (default: Win64).',
        },
        timeout_seconds: {
          type: 'number',
          description: 'Build timeout in seconds (default: 300).',
        },
      },
      required: ['project_path'],
    },
  },
];

// ---------------------------------------------------------------------------
// Execute — Blender tools
// ---------------------------------------------------------------------------

async function handleBlenderRunScript(args: Record<string, any>): Promise<ToolResult> {
  const blender = resolveBlenderBin();
  if (!blender) return fail('Blender not found. Install Blender and ensure blender.exe is in PATH or a standard location.');

  const script = String(args.script);
  const blendFile = args.blend_file ? String(args.blend_file) : undefined;
  const timeoutSec = Math.min(Number(args.timeout_seconds) || 60, 300);

  validatePythonScript(script);

  if (blendFile) {
    validateFilePath(blendFile, ['.blend']);
    if (!fs.existsSync(blendFile)) return fail(`Blend file not found: ${blendFile}`);
  }

  const scriptPath = writeTempScript(script);
  try {
    const cliArgs: string[] = [];
    if (blendFile) {
      cliArgs.push(blendFile);
    }
    cliArgs.push('--background', '--python', scriptPath);

    const output = await runProcess(blender, cliArgs, { timeout: timeoutSec * 1000 });
    // Filter out Blender startup noise — only return lines after "Blender quit"
    // or the entire output if that marker isn't found.
    const lines = output.split('\n');
    const quitIdx = lines.findIndex(l => l.includes('Blender quit'));
    const usefulLines = quitIdx >= 0
      ? lines.slice(0, quitIdx).filter(l => !l.startsWith('Blender ') && !l.startsWith('Read prefs:'))
      : lines;
    return ok(usefulLines.join('\n') || 'Script executed successfully (no output).');
  } finally {
    cleanupTempScript(scriptPath);
  }
}

async function handleBlenderRender(args: Record<string, any>): Promise<ToolResult> {
  const blender = resolveBlenderBin();
  if (!blender) return fail('Blender not found.');

  const blendFile = String(args.blend_file);
  const outputPath = String(args.output_path);
  const format = args.format ? String(args.format).toUpperCase() : undefined;
  const frameStart = args.frame_start != null ? Number(args.frame_start) : undefined;
  const frameEnd = args.frame_end != null ? Number(args.frame_end) : undefined;
  const engine = args.engine ? String(args.engine).toUpperCase() : undefined;
  const timeoutSec = Math.min(Number(args.timeout_seconds) || 300, 300);

  validateFilePath(blendFile, ['.blend']);
  if (!fs.existsSync(blendFile)) return fail(`Blend file not found: ${blendFile}`);

  // Ensure the output directory exists
  const outDir = path.dirname(outputPath);
  if (!fs.existsSync(outDir)) {
    fs.mkdirSync(outDir, { recursive: true });
  }

  const cliArgs: string[] = [blendFile, '--background'];

  // Build a setup script for engine/format overrides
  const setupLines: string[] = ['import bpy'];
  if (engine) {
    setupLines.push(`bpy.context.scene.render.engine = '${engine}'`);
  }
  if (format) {
    setupLines.push(`bpy.context.scene.render.image_settings.file_format = '${format}'`);
  }

  if (setupLines.length > 1) {
    const setupScript = writeTempScript(setupLines.join('\n'), `render_setup_${Date.now()}.py`);
    cliArgs.push('--python', setupScript);
    // Cleanup handled after render in finally block is not feasible with spawn,
    // so we schedule cleanup after a generous delay.
    setTimeout(() => cleanupTempScript(setupScript), timeoutSec * 1000 + 5000);
  }

  cliArgs.push('--render-output', outputPath);

  if (frameStart != null && frameEnd != null && frameStart === frameEnd) {
    // Single frame
    cliArgs.push('--render-frame', String(frameStart));
  } else {
    if (frameStart != null) cliArgs.push('--frame-start', String(frameStart));
    if (frameEnd != null) cliArgs.push('--frame-end', String(frameEnd));
    cliArgs.push('--render-anim');
  }

  const output = await runProcess(blender, cliArgs, { timeout: timeoutSec * 1000 });

  // Extract useful render stats from output
  const savedLines = output.split('\n').filter(l => l.includes('Saved:') || l.includes('Time:'));
  const summary = savedLines.length > 0
    ? savedLines.join('\n')
    : `Render complete. Output: ${outputPath}`;
  return ok(summary);
}

async function handleBlenderOpenFile(args: Record<string, any>): Promise<ToolResult> {
  const blender = resolveBlenderBin();
  if (!blender) return fail('Blender not found.');

  const blendFile = String(args.blend_file);
  validateFilePath(blendFile, ['.blend']);
  if (!fs.existsSync(blendFile)) return fail(`Blend file not found: ${blendFile}`);

  // Fire-and-forget: launch Blender GUI with the file
  const child = spawn(blender, [blendFile], {
    detached: true,
    stdio: 'ignore',
    windowsHide: false,
  });
  child.unref();

  return ok(`Blender launched with ${blendFile}`);
}

async function handleBlenderExport(args: Record<string, any>): Promise<ToolResult> {
  const blender = resolveBlenderBin();
  if (!blender) return fail('Blender not found.');

  const blendFile = String(args.blend_file);
  const outputPath = String(args.output_path);
  const selectedOnly = args.selected_only === true;

  validateFilePath(blendFile, ['.blend']);
  if (!fs.existsSync(blendFile)) return fail(`Blend file not found: ${blendFile}`);

  // Determine format from explicit arg or output extension
  const ext = path.extname(outputPath).toLowerCase();
  let format = args.format ? String(args.format).toUpperCase() : null;
  if (!format) {
    const extMap: Record<string, string> = {
      '.fbx': 'FBX', '.obj': 'OBJ', '.gltf': 'GLTF', '.glb': 'GLB', '.stl': 'STL',
    };
    format = extMap[ext] || null;
  }
  if (!format) return fail(`Cannot determine export format from extension "${ext}". Specify the format parameter.`);

  // Ensure output directory exists
  const outDir = path.dirname(outputPath);
  if (!fs.existsSync(outDir)) {
    fs.mkdirSync(outDir, { recursive: true });
  }

  // Build the export Python script
  const selKwarg = selectedOnly ? ', use_selection=True' : '';
  let exportCall: string;
  switch (format) {
    case 'FBX':
      exportCall = `bpy.ops.export_scene.fbx(filepath=r'${outputPath}'${selKwarg})`;
      break;
    case 'OBJ':
      exportCall = `bpy.ops.wm.obj_export(filepath=r'${outputPath}'${selectedOnly ? ', export_selected_objects=True' : ''})`;
      break;
    case 'GLTF':
    case 'GLB':
      exportCall = `bpy.ops.export_scene.gltf(filepath=r'${outputPath}', export_format='${format === 'GLB' ? 'GLB' : 'GLTF_SEPARATE'}'${selKwarg.replace('use_selection', 'use_selection')})`;
      break;
    case 'STL':
      exportCall = `bpy.ops.export_mesh.stl(filepath=r'${outputPath}'${selKwarg.replace('use_selection', 'use_selection')})`;
      break;
    default:
      return fail(`Unsupported export format: ${format}`);
  }

  const script = [
    'import bpy',
    exportCall,
    `print(f"Exported to: ${outputPath}")`,
  ].join('\n');

  const scriptPath = writeTempScript(script);
  try {
    await runProcess(blender, [blendFile, '--background', '--python', scriptPath], {
      timeout: DEFAULT_TIMEOUT_MS,
    });
    return ok(`Exported ${format} to ${outputPath}`);
  } finally {
    cleanupTempScript(scriptPath);
  }
}

async function handleBlenderImport(args: Record<string, any>): Promise<ToolResult> {
  const blender = resolveBlenderBin();
  if (!blender) return fail('Blender not found.');

  const blendFile = String(args.blend_file);
  const importPath = String(args.import_path);

  if (!fs.existsSync(importPath)) return fail(`Import file not found: ${importPath}`);

  const ext = path.extname(importPath).toLowerCase();
  const importMap: Record<string, string> = {
    '.fbx': `bpy.ops.import_scene.fbx(filepath=r'${importPath}')`,
    '.obj': `bpy.ops.wm.obj_import(filepath=r'${importPath}')`,
    '.gltf': `bpy.ops.import_scene.gltf(filepath=r'${importPath}')`,
    '.glb': `bpy.ops.import_scene.gltf(filepath=r'${importPath}')`,
    '.stl': `bpy.ops.import_mesh.stl(filepath=r'${importPath}')`,
  };

  const importCall = importMap[ext];
  if (!importCall) return fail(`Unsupported import format: ${ext}. Supported: .fbx, .obj, .gltf, .glb, .stl`);

  const script = [
    'import bpy',
    importCall,
    `bpy.ops.wm.save_as_mainfile(filepath=r'${blendFile}')`,
    `print(f"Imported ${path.basename(importPath)} and saved to ${blendFile}")`,
  ].join('\n');

  const cliArgs: string[] = [];
  if (fs.existsSync(blendFile)) {
    cliArgs.push(blendFile);
  }
  cliArgs.push('--background', '--python');

  const scriptPath = writeTempScript(script);
  try {
    cliArgs.push(scriptPath);
    await runProcess(blender, cliArgs, { timeout: DEFAULT_TIMEOUT_MS });
    return ok(`Imported ${path.basename(importPath)} into ${blendFile}`);
  } finally {
    cleanupTempScript(scriptPath);
  }
}

// ---------------------------------------------------------------------------
// Execute — Unity tools
// ---------------------------------------------------------------------------

async function handleUnityRunMethod(args: Record<string, any>): Promise<ToolResult> {
  const unity = resolveUnityEditor();
  if (!unity) return fail('Unity Editor not found. Install Unity Hub and at least one Unity Editor version.');

  const projectPath = String(args.project_path);
  const method = String(args.method);
  const extraArgs = args.extra_args ? String(args.extra_args) : '';
  const timeoutSec = Math.min(Number(args.timeout_seconds) || 120, 300);

  if (!fs.existsSync(projectPath)) return fail(`Unity project not found: ${projectPath}`);
  if (!fs.existsSync(path.join(projectPath, 'Assets'))) {
    return fail(`Not a valid Unity project (no Assets/ folder): ${projectPath}`);
  }

  // Validate method name format (Namespace.Class.Method)
  if (!/^[\w.]+$/.test(method)) {
    return fail(`Invalid method name format: "${method}". Expected: Namespace.Class.Method`);
  }

  const cliArgs = [
    '-batchmode',
    '-nographics',
    '-projectPath', projectPath,
    '-executeMethod', method,
    '-logFile', '-', // write log to stdout
    '-quit',
  ];

  if (extraArgs) {
    cliArgs.push(...extraArgs.split(' ').filter(Boolean));
  }

  const output = await runProcess(unity, cliArgs, { timeout: timeoutSec * 1000 });
  return ok(output || 'Method executed successfully (no output).');
}

async function handleUnityOpenProject(args: Record<string, any>): Promise<ToolResult> {
  const projectPath = String(args.project_path);
  if (!fs.existsSync(projectPath)) return fail(`Unity project not found: ${projectPath}`);

  // Prefer Unity Hub for opening projects (handles version selection)
  let launched = false;
  for (const hubPath of UNITY_HUB_PATHS) {
    if (fs.existsSync(hubPath)) {
      const child = spawn(hubPath, ['--', '--projectPath', projectPath], {
        detached: true,
        stdio: 'ignore',
        windowsHide: false,
      });
      child.unref();
      launched = true;
      break;
    }
  }

  // Fallback to direct Editor launch
  if (!launched) {
    const unity = resolveUnityEditor();
    if (!unity) return fail('Neither Unity Hub nor Unity Editor found.');

    const child = spawn(unity, ['-projectPath', projectPath], {
      detached: true,
      stdio: 'ignore',
      windowsHide: false,
    });
    child.unref();
  }

  return ok(`Unity project opened: ${projectPath}`);
}

async function handleUnityBuild(args: Record<string, any>): Promise<ToolResult> {
  const unity = resolveUnityEditor();
  if (!unity) return fail('Unity Editor not found.');

  const projectPath = String(args.project_path);
  const buildTarget = String(args.build_target || 'Win64');
  const outputPath = String(args.output_path);
  const customMethod = args.method ? String(args.method) : null;
  const timeoutSec = Math.min(Number(args.timeout_seconds) || 300, 300);

  if (!fs.existsSync(projectPath)) return fail(`Unity project not found: ${projectPath}`);
  if (!fs.existsSync(path.join(projectPath, 'Assets'))) {
    return fail(`Not a valid Unity project (no Assets/ folder): ${projectPath}`);
  }

  // Ensure output directory exists
  const outDir = path.dirname(outputPath);
  if (!fs.existsSync(outDir)) {
    fs.mkdirSync(outDir, { recursive: true });
  }

  const targetMap: Record<string, string> = {
    'Win64': 'Win64',
    'Win': 'Win64',
    'Android': 'Android',
    'iOS': 'iOS',
    'WebGL': 'WebGL',
    'Linux64': 'Linux64',
    'Linux': 'Linux64',
    'OSXUniversal': 'OSXUniversal',
    'Mac': 'OSXUniversal',
  };
  const resolvedTarget = targetMap[buildTarget] || buildTarget;

  let methodToCall = customMethod;

  // If no custom method provided, generate a temporary build script
  if (!methodToCall) {
    methodToCall = 'NexusBuildHelper.PerformBuild';
    const editorScriptDir = path.join(projectPath, 'Assets', 'Editor');
    if (!fs.existsSync(editorScriptDir)) {
      fs.mkdirSync(editorScriptDir, { recursive: true });
    }

    const buildTargetEnum = resolvedTarget === 'Win64' ? 'StandaloneWindows64'
      : resolvedTarget === 'Android' ? 'Android'
      : resolvedTarget === 'iOS' ? 'iOS'
      : resolvedTarget === 'WebGL' ? 'WebGL'
      : resolvedTarget === 'Linux64' ? 'StandaloneLinux64'
      : resolvedTarget === 'OSXUniversal' ? 'StandaloneOSX'
      : 'StandaloneWindows64';

    const csScript = `
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;

public class NexusBuildHelper
{
    public static void PerformBuild()
    {
        string[] scenes = new string[0];
        // Find all enabled scenes in build settings
        var editorScenes = EditorBuildSettings.scenes;
        var sceneList = new System.Collections.Generic.List<string>();
        foreach (var s in editorScenes)
        {
            if (s.enabled) sceneList.Add(s.path);
        }
        scenes = sceneList.ToArray();

        BuildPlayerOptions opts = new BuildPlayerOptions();
        opts.scenes = scenes;
        opts.locationPathName = @"${outputPath.replace(/\\/g, '\\\\')}";
        opts.target = BuildTarget.${buildTargetEnum};
        opts.options = BuildOptions.None;

        BuildReport report = BuildPipeline.BuildPlayer(opts);
        if (report.summary.result == BuildResult.Succeeded)
        {
            Debug.Log("BUILD SUCCEEDED: " + report.summary.totalSize + " bytes");
        }
        else
        {
            Debug.LogError("BUILD FAILED: " + report.summary.result);
            EditorApplication.Exit(1);
        }
    }
}
`.trim();

    const csPath = path.join(editorScriptDir, 'NexusBuildHelper.cs');
    fs.writeFileSync(csPath, csScript, 'utf-8');
  }

  const cliArgs = [
    '-batchmode',
    '-nographics',
    '-projectPath', projectPath,
    '-buildTarget', resolvedTarget,
    '-executeMethod', methodToCall,
    '-logFile', '-',
    '-quit',
  ];

  const output = await runProcess(unity, cliArgs, { timeout: timeoutSec * 1000 });

  // Check for success markers
  if (output.includes('BUILD SUCCEEDED') || output.includes('Build completed')) {
    return ok(`Unity build succeeded for ${resolvedTarget}.\nOutput: ${outputPath}\n${truncate(output, 2000)}`);
  }
  if (output.includes('BUILD FAILED') || output.includes('Error')) {
    return fail(`Unity build failed:\n${truncate(output, 4000)}`);
  }
  return ok(`Unity build process completed.\nOutput: ${outputPath}\n${truncate(output, 2000)}`);
}

// ---------------------------------------------------------------------------
// Execute — Unreal Engine tools
// ---------------------------------------------------------------------------

async function handleUnrealRunCommand(args: Record<string, any>): Promise<ToolResult> {
  const unreal = resolveUnrealEditor();
  if (!unreal) return fail('Unreal Engine (UnrealEditor-Cmd.exe) not found. Install via the Epic Games Launcher.');

  const projectPath = String(args.project_path);
  const command = String(args.command);
  const timeoutSec = Math.min(Number(args.timeout_seconds) || 120, 300);

  validateFilePath(projectPath, ['.uproject']);
  if (!fs.existsSync(projectPath)) return fail(`Unreal project not found: ${projectPath}`);

  // Safety: block extremely dangerous commandlets
  const dangerousPatterns = [/\bdelete\b/i, /\bformat\b/i, /\brm\s+-rf/i];
  for (const pat of dangerousPatterns) {
    if (pat.test(command)) {
      return fail(`Blocked: command contains dangerous pattern (${pat.source}).`);
    }
  }

  const cliArgs = [projectPath, ...command.split(' ').filter(Boolean), '-unattended', '-nopause'];
  const output = await runProcess(unreal, cliArgs, { timeout: timeoutSec * 1000 });
  return ok(output || 'Command executed successfully.');
}

async function handleUnrealBuild(args: Record<string, any>): Promise<ToolResult> {
  const projectPath = String(args.project_path);
  const config = String(args.config || 'Development');
  const platform = String(args.platform || 'Win64');
  const timeoutSec = Math.min(Number(args.timeout_seconds) || 300, 300);

  validateFilePath(projectPath, ['.uproject']);
  if (!fs.existsSync(projectPath)) return fail(`Unreal project not found: ${projectPath}`);

  // Validate config
  const validConfigs = ['Development', 'DebugGame', 'Shipping', 'Test'];
  if (!validConfigs.includes(config)) {
    return fail(`Invalid build config "${config}". Valid: ${validConfigs.join(', ')}`);
  }

  // Find UnrealBuildTool — it lives alongside the editor
  const unreal = resolveUnrealEditor();
  if (!unreal) return fail('Unreal Engine not found.');

  // Derive the engine root from the editor path
  // UnrealEditor-Cmd.exe is at <Engine>/Binaries/Win64/UnrealEditor-Cmd.exe
  const engineBinDir = path.dirname(unreal);
  const ubtPath = path.join(engineBinDir, 'UnrealBuildTool.exe');
  const uatPath = path.join(engineBinDir, '..', '..', 'Build', 'BatchFiles', 'RunUAT.bat');

  // Prefer UAT (Unreal Automation Tool) for project builds as it handles full pipeline
  let buildCmd: string;
  let buildArgs: string[];

  if (fs.existsSync(uatPath)) {
    buildCmd = uatPath;
    buildArgs = [
      'BuildCookRun',
      `-project=${projectPath}`,
      `-targetplatform=${platform}`,
      `-clientconfig=${config}`,
      '-build',
      '-cook',
      '-stage',
      '-pak',
      '-unattended',
      '-nopause',
    ];
  } else if (fs.existsSync(ubtPath)) {
    // Fallback to direct UBT invocation
    const projectName = path.basename(projectPath, '.uproject');
    buildCmd = ubtPath;
    buildArgs = [
      projectName,
      platform,
      config,
      `-project=${projectPath}`,
    ];
  } else {
    // Last resort: use UnrealEditor-Cmd with -run=cook
    buildCmd = unreal;
    buildArgs = [
      projectPath,
      `-run=cook`,
      `-targetplatform=${platform}`,
      '-unattended',
      '-nopause',
    ];
  }

  const output = await runProcess(buildCmd, buildArgs, { timeout: timeoutSec * 1000 });

  if (output.toLowerCase().includes('error') && output.toLowerCase().includes('fail')) {
    return fail(`Unreal build encountered errors:\n${truncate(output, 4000)}`);
  }
  return ok(`Unreal build completed (${config} / ${platform}).\n${truncate(output, 3000)}`);
}

// ---------------------------------------------------------------------------
// Execute — Main dispatcher
// ---------------------------------------------------------------------------

export async function execute(
  toolName: string,
  args: Record<string, unknown>
): Promise<ToolResult> {
  try {
    switch (toolName) {
      // Blender
      case 'blender_run_script':  return await handleBlenderRunScript(args);
      case 'blender_render':      return await handleBlenderRender(args);
      case 'blender_open_file':   return await handleBlenderOpenFile(args);
      case 'blender_export':      return await handleBlenderExport(args);
      case 'blender_import':      return await handleBlenderImport(args);

      // Unity
      case 'unity_run_method':    return await handleUnityRunMethod(args);
      case 'unity_open_project':  return await handleUnityOpenProject(args);
      case 'unity_build':         return await handleUnityBuild(args);

      // Unreal
      case 'unreal_run_command':  return await handleUnrealRunCommand(args);
      case 'unreal_build':        return await handleUnrealBuild(args);

      default:
        return fail(`Unknown creative-3d tool: ${toolName}`);
    }
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    return { error: `Tool "${toolName}" failed: ${message}` };
  }
}

// ---------------------------------------------------------------------------
// Detect — Check if any 3D/VFX application is installed
// ---------------------------------------------------------------------------

export async function detect(): Promise<boolean> {
  // Return true if any of Blender, Unity, or Unreal Engine is found.
  if (resolveBlenderBin()) return true;
  if (isUnityHubInstalled() || resolveUnityEditor()) return true;
  if (resolveUnrealEditor()) return true;
  return false;
}
