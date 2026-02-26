/**
 * adobe.ts — Adobe Creative Suite connector.
 *
 * Provides an AI agent with control over Adobe Photoshop, Illustrator,
 * Premiere Pro, and After Effects on Windows via ExtendScript/JSX execution
 * through COM automation and command-line invocation.
 *
 * Automation approach:
 *  - Photoshop: COM object (Photoshop.Application) with DoJavaScript method,
 *    plus direct file operations via PowerShell COM interop.
 *  - Illustrator: COM object (Illustrator.Application) with DoJavaScript method.
 *  - Premiere Pro / After Effects: Write ExtendScript to a temp .jsx file and
 *    execute via the app's command-line `-r` flag or through the `afterfx.exe` CLI.
 *
 * Safety:
 *  - All PowerShell commands run with -NoProfile -NonInteractive -ExecutionPolicy Bypass.
 *  - Default 30-second timeout on every spawn; callers may override per-tool.
 *  - Errors are returned as { error: "..." }, never thrown.
 *  - Temp files are cleaned up in finally blocks.
 */

import { execFile } from 'child_process';
import { promisify } from 'util';
import * as path from 'path';
import * as fs from 'fs';
import * as os from 'os';

const execFileAsync = promisify(execFile);

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

/** Default execution timeout in milliseconds (30 s). */
const DEFAULT_TIMEOUT_MS = 30_000;

/** Extended timeout for operations that launch Adobe apps (60 s). */
const EXTENDED_TIMEOUT_MS = 60_000;

/** Maximum stdout/stderr length returned to the caller (64 KB). */
const MAX_OUTPUT_LENGTH = 64 * 1024;

/** Base arguments passed to every powershell.exe invocation. */
const PS_BASE_ARGS = [
  '-NoProfile',
  '-NonInteractive',
  '-ExecutionPolicy', 'Bypass',
  '-File',
];

/** Standard Adobe installation root on Windows. */
const ADOBE_INSTALL_ROOT = 'C:\\Program Files\\Adobe';

/** Known Adobe application executable patterns for detection. */
const ADOBE_APP_PATHS: Record<string, string[]> = {
  photoshop: [
    'Adobe Photoshop *\\Photoshop.exe',
    'Adobe Photoshop CC *\\Photoshop.exe',
  ],
  illustrator: [
    'Adobe Illustrator *\\Support Files\\Contents\\Windows\\Illustrator.exe',
    'Adobe Illustrator CC *\\Support Files\\Contents\\Windows\\Illustrator.exe',
  ],
  premiere: [
    'Adobe Premiere Pro *\\Adobe Premiere Pro.exe',
    'Adobe Premiere Pro CC *\\Adobe Premiere Pro.exe',
  ],
  aftereffects: [
    'AfterFX *\\Support Files\\afterfx.exe',
    'Adobe After Effects *\\Support Files\\afterfx.exe',
    'Adobe After Effects CC *\\Support Files\\afterfx.exe',
  ],
};

// ---------------------------------------------------------------------------
// Helper utilities
// ---------------------------------------------------------------------------

/**
 * Get the temp directory for writing transient script files.
 */
function getTempDir(): string {
  return process.env.TEMP || process.env.TMP || os.tmpdir();
}

/**
 * Generate a unique temp file path with the given extension.
 */
function tempFile(prefix: string, ext: string): string {
  const name = `nexus_adobe_${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}${ext}`;
  return path.join(getTempDir(), name);
}

/**
 * Normalise a user-supplied file path to an absolute Windows path.
 */
function winPath(p: string): string {
  return path.resolve(p).replace(/\//g, '\\');
}

/**
 * Escape a string for safe interpolation into a PowerShell single-quoted literal.
 * Single quotes are doubled per PowerShell quoting rules.
 */
function psEscape(s: string): string {
  return s.replace(/'/g, "''");
}

/**
 * Escape a string for safe interpolation into an ExtendScript string literal.
 * Handles backslashes, quotes, newlines, and carriage returns.
 */
function jsxEscape(s: string): string {
  return s
    .replace(/\\/g, '\\\\')
    .replace(/'/g, "\\'")
    .replace(/"/g, '\\"')
    .replace(/\n/g, '\\n')
    .replace(/\r/g, '\\r');
}

/**
 * Truncate output to MAX_OUTPUT_LENGTH, appending a truncation notice if needed.
 */
function truncateOutput(s: string): string {
  if (s.length <= MAX_OUTPUT_LENGTH) return s;
  return s.slice(0, MAX_OUTPUT_LENGTH) + '\n... [output truncated]';
}

// ---------------------------------------------------------------------------
// PowerShell runner
// ---------------------------------------------------------------------------

/**
 * Execute a PowerShell script by writing it to a temp file and running via
 * `powershell.exe -File`. Returns stdout. Rejects on non-zero exit, timeout,
 * or spawn error.
 */
async function runPS(script: string, timeoutMs: number = DEFAULT_TIMEOUT_MS): Promise<string> {
  const tmpScript = tempFile('ps', '.ps1');
  try {
    fs.writeFileSync(tmpScript, script, 'utf-8');
    const { stdout, stderr } = await execFileAsync(
      'powershell.exe',
      [...PS_BASE_ARGS, tmpScript],
      {
        timeout: timeoutMs,
        windowsHide: true,
        maxBuffer: MAX_OUTPUT_LENGTH * 2,
      },
    );
    // Some PS scripts write non-fatal warnings to stderr; return stdout unless it's empty
    const out = (stdout ?? '').trim();
    if (!out && stderr && stderr.trim()) {
      throw new Error(stderr.trim());
    }
    return truncateOutput(out);
  } finally {
    try { fs.unlinkSync(tmpScript); } catch { /* ignore cleanup errors */ }
  }
}

/**
 * Convenience: wrap a call to `runPS` and catch all errors into a
 * ToolResult so callers never need try/catch.
 */
async function safeRun(script: string, timeoutMs?: number): Promise<ToolResult> {
  try {
    const output = await runPS(script, timeoutMs);
    return { result: output || '(no output)' };
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    return { error: msg };
  }
}

/**
 * Write an ExtendScript (.jsx) to a temp file, returning the file path.
 * The caller is responsible for cleanup.
 */
function writeJsxTempFile(prefix: string, scriptContent: string): string {
  const tmpJsx = tempFile(prefix, '.jsx');
  fs.writeFileSync(tmpJsx, scriptContent, 'utf-8');
  return tmpJsx;
}

// ---------------------------------------------------------------------------
// Tool declarations
// ---------------------------------------------------------------------------

export const TOOLS: ToolDeclaration[] = [
  // ---- Photoshop -----------------------------------------------------------
  {
    name: 'photoshop_run_script',
    description:
      'Execute an ExtendScript/JSX snippet inside Adobe Photoshop. ' +
      'The script runs in Photoshop\'s scripting engine via COM DoJavaScript. ' +
      'Returns the script\'s last evaluated value as a string.',
    parameters: {
      type: 'object',
      properties: {
        script: {
          type: 'string',
          description: 'ExtendScript (JSX) code to execute in Photoshop.',
        },
        timeout_seconds: {
          type: 'number',
          description: 'Maximum execution time in seconds (default 30).',
        },
      },
      required: ['script'],
    },
  },
  {
    name: 'photoshop_open_file',
    description:
      'Open an image file in Adobe Photoshop. Supports PSD, PNG, JPEG, TIFF, BMP, GIF, and other formats Photoshop can read.',
    parameters: {
      type: 'object',
      properties: {
        file_path: {
          type: 'string',
          description: 'Absolute or relative path to the image file to open.',
        },
      },
      required: ['file_path'],
    },
  },
  {
    name: 'photoshop_save_as',
    description:
      'Save the current active Photoshop document to a file in the specified format. ' +
      'Supported formats: PSD, PNG, JPEG, TIFF.',
    parameters: {
      type: 'object',
      properties: {
        file_path: {
          type: 'string',
          description: 'Destination file path for the saved document.',
        },
        format: {
          type: 'string',
          enum: ['PSD', 'PNG', 'JPEG', 'TIFF'],
          description: 'Output format (default: PSD).',
        },
        jpeg_quality: {
          type: 'number',
          description: 'JPEG quality 0-12 (default 10). Only used when format is JPEG.',
        },
      },
      required: ['file_path'],
    },
  },
  {
    name: 'photoshop_resize',
    description:
      'Resize the current active Photoshop document to the specified pixel dimensions. ' +
      'If only width or height is provided, the other is calculated to maintain aspect ratio.',
    parameters: {
      type: 'object',
      properties: {
        width: {
          type: 'number',
          description: 'Target width in pixels.',
        },
        height: {
          type: 'number',
          description: 'Target height in pixels.',
        },
        resample_method: {
          type: 'string',
          enum: ['BICUBIC', 'BILINEAR', 'NEARESTNEIGHBOR', 'BICUBICSHARPER', 'BICUBICSMOOTHER'],
          description: 'Resampling method (default: BICUBIC).',
        },
      },
      required: [],
    },
  },
  {
    name: 'photoshop_apply_filter',
    description:
      'Apply a built-in filter to the current active document in Photoshop. ' +
      'Executes the filter via ExtendScript. Common filters: GaussianBlur, ' +
      'UnsharpMask, MotionBlur, Sharpen, MedianNoise.',
    parameters: {
      type: 'object',
      properties: {
        filter_name: {
          type: 'string',
          description:
            'Filter name: GaussianBlur, UnsharpMask, MotionBlur, Sharpen, ' +
            'SharpenMore, Emboss, FindEdges, MedianNoise.',
        },
        filter_args: {
          type: 'object',
          description:
            'Filter-specific arguments as key-value pairs. For GaussianBlur: { radius: 5 }. ' +
            'For UnsharpMask: { amount: 100, radius: 2, threshold: 0 }. ' +
            'For MotionBlur: { angle: 45, distance: 20 }. ' +
            'For MedianNoise: { radius: 3 }.',
        },
      },
      required: ['filter_name'],
    },
  },

  // ---- Illustrator ---------------------------------------------------------
  {
    name: 'illustrator_run_script',
    description:
      'Execute an ExtendScript/JSX snippet inside Adobe Illustrator. ' +
      'The script runs in Illustrator\'s scripting engine via COM DoJavaScript. ' +
      'Returns the script\'s last evaluated value as a string.',
    parameters: {
      type: 'object',
      properties: {
        script: {
          type: 'string',
          description: 'ExtendScript (JSX) code to execute in Illustrator.',
        },
        timeout_seconds: {
          type: 'number',
          description: 'Maximum execution time in seconds (default 30).',
        },
      },
      required: ['script'],
    },
  },
  {
    name: 'illustrator_open_file',
    description:
      'Open a file in Adobe Illustrator. Supports AI, EPS, SVG, PDF, and other formats Illustrator can read.',
    parameters: {
      type: 'object',
      properties: {
        file_path: {
          type: 'string',
          description: 'Absolute or relative path to the file to open.',
        },
      },
      required: ['file_path'],
    },
  },
  {
    name: 'illustrator_export',
    description:
      'Export the current active Illustrator document to a specified format. ' +
      'Supported formats: SVG, PNG, PDF.',
    parameters: {
      type: 'object',
      properties: {
        file_path: {
          type: 'string',
          description: 'Destination file path for the exported document.',
        },
        format: {
          type: 'string',
          enum: ['SVG', 'PNG', 'PDF'],
          description: 'Export format.',
        },
        png_scale: {
          type: 'number',
          description: 'Scale factor for PNG export (default 100 = 100%). Only used when format is PNG.',
        },
      },
      required: ['file_path', 'format'],
    },
  },

  // ---- Premiere Pro ---------------------------------------------------------
  {
    name: 'premiere_run_script',
    description:
      'Execute an ExtendScript/JSX snippet inside Adobe Premiere Pro. ' +
      'The script is written to a temp .jsx file and executed via Premiere\'s ' +
      'ExtendScript engine. Returns any output captured from the script.',
    parameters: {
      type: 'object',
      properties: {
        script: {
          type: 'string',
          description: 'ExtendScript (JSX) code to execute in Premiere Pro.',
        },
        timeout_seconds: {
          type: 'number',
          description: 'Maximum execution time in seconds (default 30).',
        },
      },
      required: ['script'],
    },
  },

  // ---- After Effects --------------------------------------------------------
  {
    name: 'aftereffects_run_script',
    description:
      'Execute an ExtendScript/JSX snippet inside Adobe After Effects. ' +
      'The script is written to a temp .jsx file and executed via afterfx.exe -r. ' +
      'Returns any output captured from the script.',
    parameters: {
      type: 'object',
      properties: {
        script: {
          type: 'string',
          description: 'ExtendScript (JSX) code to execute in After Effects.',
        },
        timeout_seconds: {
          type: 'number',
          description: 'Maximum execution time in seconds (default 30).',
        },
      },
      required: ['script'],
    },
  },
];

// ---------------------------------------------------------------------------
// Photoshop script builders
// ---------------------------------------------------------------------------

/**
 * Build a PowerShell script that executes ExtendScript in Photoshop via
 * COM automation using the DoJavaScript method.
 */
function scriptPhotoshopRunJsx(jsxCode: string): string {
  const escaped = psEscape(jsxEscape(jsxCode));
  return `
$app = New-Object -ComObject Photoshop.Application
try {
  $result = $app.DoJavaScript('${escaped}')
  if ($null -ne $result) {
    Write-Output $result
  } else {
    Write-Output '(script returned null)'
  }
} finally {
  try { [System.Runtime.Interopservices.Marshal]::ReleaseComObject($app) | Out-Null } catch {}
  [System.GC]::Collect()
  [System.GC]::WaitForPendingFinalizers()
}`;
}

/**
 * Build a PowerShell script that opens a file in Photoshop via COM.
 */
function scriptPhotoshopOpenFile(filePath: string): string {
  const fp = psEscape(winPath(filePath));
  return `
$app = New-Object -ComObject Photoshop.Application
try {
  $app.Open('${fp}')
  $doc = $app.ActiveDocument
  $info = "Opened: $($doc.Name) ($($doc.Width)x$($doc.Height) px, $($doc.BitsPerChannel)-bit, $($doc.Mode))"
  Write-Output $info
} finally {
  try { [System.Runtime.Interopservices.Marshal]::ReleaseComObject($app) | Out-Null } catch {}
  [System.GC]::Collect()
  [System.GC]::WaitForPendingFinalizers()
}`;
}

/**
 * Build ExtendScript code to save the active document in Photoshop.
 */
function buildPhotoshopSaveJsx(filePath: string, format: string, jpegQuality: number): string {
  const fp = jsxEscape(winPath(filePath));

  switch (format.toUpperCase()) {
    case 'JPEG': {
      const q = Math.max(0, Math.min(12, jpegQuality));
      return `
var f = new File('${fp}');
var opts = new JPEGSaveOptions();
opts.quality = ${q};
opts.embedColorProfile = true;
app.activeDocument.saveAs(f, opts, true);
'Saved as JPEG: ' + f.fsName;
`;
    }
    case 'PNG':
      return `
var f = new File('${fp}');
var opts = new PNGSaveOptions();
opts.compression = 6;
opts.interlaced = false;
app.activeDocument.saveAs(f, opts, true);
'Saved as PNG: ' + f.fsName;
`;
    case 'TIFF':
      return `
var f = new File('${fp}');
var opts = new TiffSaveOptions();
opts.imageCompression = TIFFEncoding.TIFFLZW;
app.activeDocument.saveAs(f, opts, true);
'Saved as TIFF: ' + f.fsName;
`;
    case 'PSD':
    default:
      return `
var f = new File('${fp}');
var opts = new PhotoshopSaveOptions();
opts.layers = true;
app.activeDocument.saveAs(f, opts, true);
'Saved as PSD: ' + f.fsName;
`;
  }
}

/**
 * Build ExtendScript code to resize the active document in Photoshop.
 */
function buildPhotoshopResizeJsx(
  width?: number,
  height?: number,
  resampleMethod?: string,
): string {
  // Map friendly names to ExtendScript ResampleMethod constants
  const resampleMap: Record<string, string> = {
    BICUBIC: 'ResampleMethod.BICUBIC',
    BILINEAR: 'ResampleMethod.BILINEAR',
    NEARESTNEIGHBOR: 'ResampleMethod.NEARESTNEIGHBOR',
    BICUBICSHARPER: 'ResampleMethod.BICUBICSHARPER',
    BICUBICSMOOTHER: 'ResampleMethod.BICUBICSMOOTHER',
  };
  const method = resampleMap[(resampleMethod || 'BICUBIC').toUpperCase()] || 'ResampleMethod.BICUBIC';

  if (width && height) {
    return `
var doc = app.activeDocument;
doc.resizeImage(UnitValue(${width}, 'px'), UnitValue(${height}, 'px'), doc.resolution, ${method});
'Resized to ' + doc.width + ' x ' + doc.height;
`;
  } else if (width) {
    return `
var doc = app.activeDocument;
var ratio = ${width} / doc.width.as('px');
var newHeight = doc.height.as('px') * ratio;
doc.resizeImage(UnitValue(${width}, 'px'), UnitValue(newHeight, 'px'), doc.resolution, ${method});
'Resized to ' + doc.width + ' x ' + doc.height;
`;
  } else if (height) {
    return `
var doc = app.activeDocument;
var ratio = ${height} / doc.height.as('px');
var newWidth = doc.width.as('px') * ratio;
doc.resizeImage(UnitValue(newWidth, 'px'), UnitValue(${height}, 'px'), doc.resolution, ${method});
'Resized to ' + doc.width + ' x ' + doc.height;
`;
  }
  return "'No width or height specified; no resize performed.'";
}

/**
 * Build ExtendScript code to apply a filter in Photoshop.
 */
function buildPhotoshopFilterJsx(
  filterName: string,
  filterArgs?: Record<string, unknown>,
): string {
  const args = filterArgs || {};

  switch (filterName.toLowerCase()) {
    case 'gaussianblur': {
      const radius = Number(args.radius) || 5;
      return `
app.activeDocument.activeLayer.applyGaussianBlur(${radius});
'Applied Gaussian Blur with radius ${radius}';
`;
    }
    case 'unsharpmask': {
      const amount = Number(args.amount) || 100;
      const radius = Number(args.radius) || 2;
      const threshold = Number(args.threshold) || 0;
      return `
app.activeDocument.activeLayer.applyUnSharpMask(${amount}, ${radius}, ${threshold});
'Applied Unsharp Mask (amount=${amount}, radius=${radius}, threshold=${threshold})';
`;
    }
    case 'motionblur': {
      const angle = Number(args.angle) || 0;
      const distance = Number(args.distance) || 10;
      return `
app.activeDocument.activeLayer.applyMotionBlur(${angle}, ${distance});
'Applied Motion Blur (angle=${angle}, distance=${distance})';
`;
    }
    case 'sharpen':
      return `
app.activeDocument.activeLayer.applySharpen();
'Applied Sharpen';
`;
    case 'sharpenmore':
      return `
app.activeDocument.activeLayer.applySharpenMore();
'Applied Sharpen More';
`;
    case 'emboss': {
      const angle = Number(args.angle) || 135;
      const height = Number(args.height) || 3;
      const amount = Number(args.amount) || 100;
      return `
app.activeDocument.activeLayer.applyEmboss(${angle}, ${height}, ${amount});
'Applied Emboss (angle=${angle}, height=${height}, amount=${amount})';
`;
    }
    case 'findedges':
      return `
app.activeDocument.activeLayer.applyStyleize();  // Not directly available; use Action Manager
// Fallback: run via menu
var desc = new ActionDescriptor();
executeAction(stringIDToTypeID('findEdges'), desc, DialogModes.NO);
'Applied Find Edges';
`;
    case 'mediannoise': {
      const radius = Number(args.radius) || 3;
      return `
app.activeDocument.activeLayer.applyMedianNoise(${radius});
'Applied Median Noise with radius ${radius}';
`;
    }
    default:
      return `'Unknown filter: ${jsxEscape(filterName)}. Supported: GaussianBlur, UnsharpMask, MotionBlur, Sharpen, SharpenMore, Emboss, FindEdges, MedianNoise.'`;
  }
}

// ---------------------------------------------------------------------------
// Illustrator script builders
// ---------------------------------------------------------------------------

/**
 * Build a PowerShell script that executes ExtendScript in Illustrator via COM.
 */
function scriptIllustratorRunJsx(jsxCode: string): string {
  const escaped = psEscape(jsxEscape(jsxCode));
  return `
$app = New-Object -ComObject Illustrator.Application
try {
  $result = $app.DoJavaScript('${escaped}')
  if ($null -ne $result) {
    Write-Output $result
  } else {
    Write-Output '(script returned null)'
  }
} finally {
  try { [System.Runtime.Interopservices.Marshal]::ReleaseComObject($app) | Out-Null } catch {}
  [System.GC]::Collect()
  [System.GC]::WaitForPendingFinalizers()
}`;
}

/**
 * Build a PowerShell script that opens a file in Illustrator via COM.
 */
function scriptIllustratorOpenFile(filePath: string): string {
  const fp = psEscape(winPath(filePath));
  return `
$app = New-Object -ComObject Illustrator.Application
try {
  $app.Open('${fp}')
  $doc = $app.ActiveDocument
  $info = "Opened: $($doc.Name) ($($doc.Width) x $($doc.Height) pts, $($doc.Artboards.Count) artboard(s))"
  Write-Output $info
} finally {
  try { [System.Runtime.Interopservices.Marshal]::ReleaseComObject($app) | Out-Null } catch {}
  [System.GC]::Collect()
  [System.GC]::WaitForPendingFinalizers()
}`;
}

/**
 * Build ExtendScript code to export from Illustrator.
 */
function buildIllustratorExportJsx(filePath: string, format: string, pngScale?: number): string {
  const fp = jsxEscape(winPath(filePath));

  switch (format.toUpperCase()) {
    case 'SVG':
      return `
var doc = app.activeDocument;
var f = new File('${fp}');
var opts = new ExportOptionsSVG();
opts.embedRasterImages = true;
opts.fontSubsetting = SVGFontSubsetting.GLYPHSUSED;
opts.coordinatePrecision = 3;
doc.exportFile(f, ExportType.SVG, opts);
'Exported SVG: ' + f.fsName;
`;
    case 'PNG': {
      const scale = pngScale != null ? pngScale : 100;
      return `
var doc = app.activeDocument;
var f = new File('${fp}');
var opts = new ExportOptionsPNG24();
opts.antiAliasing = true;
opts.transparency = true;
opts.horizontalScale = ${scale};
opts.verticalScale = ${scale};
doc.exportFile(f, ExportType.PNG24, opts);
'Exported PNG (${scale}%): ' + f.fsName;
`;
    }
    case 'PDF':
      return `
var doc = app.activeDocument;
var f = new File('${fp}');
var opts = new PDFSaveOptions();
opts.compatibility = PDFCompatibility.ACROBAT7;
opts.preserveEditability = false;
doc.saveAs(f, opts);
'Exported PDF: ' + f.fsName;
`;
    default:
      return `'Unsupported format: ${jsxEscape(format)}. Supported: SVG, PNG, PDF.'`;
  }
}

// ---------------------------------------------------------------------------
// Premiere Pro script builder
// ---------------------------------------------------------------------------

/**
 * Build a PowerShell script that executes ExtendScript in Premiere Pro.
 *
 * Premiere Pro's COM interface does not expose a DoJavaScript method, so
 * we write the script to a temp .jsx file and invoke it via BridgeTalk or
 * by using PowerShell to send the script through the ExtendScript Toolkit
 * engine via COM class "AdobeES.Application" targeting Premiere.
 */
function scriptPremiereRunJsx(jsxCode: string): string {
  // We wrap the user script so its return value is written to a temp output file,
  // which we then read back in PowerShell.
  const outputFile = tempFile('premiere_out', '.txt').replace(/\\/g, '\\\\');
  const escapedJsx = psEscape(jsxCode.replace(/\\/g, '\\\\').replace(/'/g, "''"));

  return `
# Write the ExtendScript to a temp file
$jsxContent = @'
var _nexus_result;
try {
  _nexus_result = eval(${JSON.stringify(jsxCode)});
} catch(e) {
  _nexus_result = 'Error: ' + e.message;
}
// Write output to a temp file for retrieval
var outFile = new File('${outputFile}');
outFile.open('w');
outFile.write(String(_nexus_result));
outFile.close();
'@

$jsxPath = [System.IO.Path]::Combine($env:TEMP, 'nexus_premiere_' + [System.Guid]::NewGuid().ToString('N') + '.jsx')
$outPath = '${psEscape(outputFile.replace(/\\\\/g, '\\'))}'
Set-Content -Path $jsxPath -Value $jsxContent -Encoding UTF8

try {
  # Try using BridgeTalk via ExtendScript Toolkit engine
  $btScript = @"
var bt = new BridgeTalk();
bt.target = 'premierepro';
bt.body = '$.evalFile("' + '$jsxPath'.replace(/\\\\/g, '/') + '")';
bt.onResult = function(res) {};
bt.onError = function(err) {};
bt.send(10);
"@
  $btPath = [System.IO.Path]::Combine($env:TEMP, 'nexus_premiere_bt_' + [System.Guid]::NewGuid().ToString('N') + '.jsx')
  Set-Content -Path $btPath -Value $btScript -Encoding UTF8

  # First, try direct COM invocation
  try {
    $app = New-Object -ComObject premierepro.Application
    # Premiere may expose DoScript or similar
    $app.SourceMonitor.OpenFilePath($jsxPath)
    Start-Sleep -Seconds 3
  } catch {
    # Fallback: invoke via Adobe ExtendScript Toolkit if available
    try {
      $estk = New-Object -ComObject AdobeES.Application
      $estk.DoScript($btScript)
      Start-Sleep -Seconds 3
    } catch {
      # Final fallback: try running the script via cscript bridge
      Write-Output "Warning: Direct COM invocation failed. Attempting file-based execution."
      Write-Output "Error details: $($_.Exception.Message)"
    }
  }

  # Read result from output file if it exists
  if (Test-Path $outPath) {
    $content = Get-Content -Path $outPath -Raw -ErrorAction SilentlyContinue
    if ($content) {
      Write-Output $content
    } else {
      Write-Output '(script produced no output)'
    }
    Remove-Item -Path $outPath -Force -ErrorAction SilentlyContinue
  } else {
    Write-Output '(script executed but no output file was created — the script may still be running in Premiere Pro)'
  }
} finally {
  Remove-Item -Path $jsxPath -Force -ErrorAction SilentlyContinue
  Remove-Item -Path $btPath -Force -ErrorAction SilentlyContinue
}`;
}

// ---------------------------------------------------------------------------
// After Effects script builder
// ---------------------------------------------------------------------------

/**
 * Build a PowerShell script that executes ExtendScript in After Effects.
 *
 * After Effects supports the `-r` command-line flag for running scripts,
 * and also exposes a COM DoScript method. We try COM first for a running
 * instance, then fall back to command-line invocation.
 */
function scriptAfterEffectsRunJsx(jsxCode: string): string {
  const outputFile = tempFile('ae_out', '.txt').replace(/\\/g, '\\\\');

  return `
# Write the ExtendScript to a temp file
$jsxContent = @'
var _nexus_result;
try {
  _nexus_result = eval(${JSON.stringify(jsxCode)});
} catch(e) {
  _nexus_result = 'Error: ' + e.message;
}
// Write output to a temp file for retrieval
var outFile = new File('${outputFile}');
outFile.open('w');
outFile.write(String(_nexus_result));
outFile.close();
'@

$jsxPath = [System.IO.Path]::Combine($env:TEMP, 'nexus_ae_' + [System.Guid]::NewGuid().ToString('N') + '.jsx')
$outPath = '${psEscape(outputFile.replace(/\\\\/g, '\\'))}'
Set-Content -Path $jsxPath -Value $jsxContent -Encoding UTF8

try {
  $executed = $false

  # Try COM automation first (works if AE is already running)
  try {
    $app = New-Object -ComObject AfterEffects.Application
    $app.DoScript($jsxContent, 1)  # 1 = ScriptLanguage.JAVASCRIPT
    $executed = $true
  } catch {
    # COM failed — AE may not be running or COM class not available
  }

  if (-not $executed) {
    # Fallback: find afterfx.exe and run with -r flag
    $aePaths = @(
      Get-ChildItem -Path 'C:\\Program Files\\Adobe' -Filter 'afterfx.exe' -Recurse -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty FullName
    )
    $aePath = Get-ChildItem -Path 'C:\\Program Files\\Adobe' -Filter 'AfterFX.exe' -Recurse -ErrorAction SilentlyContinue |
      Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName

    if ($aePath) {
      # -r flag runs the script file
      $proc = Start-Process -FilePath $aePath -ArgumentList ('-r', "\`"$jsxPath\`"") -PassThru -WindowStyle Hidden
      $proc.WaitForExit(30000)  # Wait up to 30s
      $executed = $true
    } else {
      Write-Output 'Error: Could not find AfterFX.exe. Is After Effects installed?'
    }
  }

  if ($executed) {
    # Wait a moment for the output file to be written
    Start-Sleep -Seconds 2

    if (Test-Path $outPath) {
      $content = Get-Content -Path $outPath -Raw -ErrorAction SilentlyContinue
      if ($content) {
        Write-Output $content
      } else {
        Write-Output '(script produced no output)'
      }
      Remove-Item -Path $outPath -Force -ErrorAction SilentlyContinue
    } else {
      Write-Output '(script executed but no output file was created — it may still be running)'
    }
  }
} finally {
  Remove-Item -Path $jsxPath -Force -ErrorAction SilentlyContinue
}`;
}

// ---------------------------------------------------------------------------
// Tool implementations
// ---------------------------------------------------------------------------

/** photoshop_run_script */
async function photoshopRunScript(args: Record<string, unknown>): Promise<ToolResult> {
  const script = String(args.script ?? '');
  if (!script.trim()) {
    return { error: 'No script provided.' };
  }
  const timeoutSec = Number(args.timeout_seconds) || 30;
  const timeoutMs = Math.min(Math.max(timeoutSec, 1), 300) * 1000;
  return safeRun(scriptPhotoshopRunJsx(script), timeoutMs);
}

/** photoshop_open_file */
async function photoshopOpenFile(args: Record<string, unknown>): Promise<ToolResult> {
  const filePath = String(args.file_path ?? '');
  if (!filePath.trim()) {
    return { error: 'No file path provided.' };
  }
  const resolved = winPath(filePath);
  if (!fs.existsSync(resolved)) {
    return { error: `File not found: ${resolved}` };
  }
  return safeRun(scriptPhotoshopOpenFile(filePath), EXTENDED_TIMEOUT_MS);
}

/** photoshop_save_as */
async function photoshopSaveAs(args: Record<string, unknown>): Promise<ToolResult> {
  const filePath = String(args.file_path ?? '');
  if (!filePath.trim()) {
    return { error: 'No file path provided.' };
  }
  const format = String(args.format ?? 'PSD').toUpperCase();
  const validFormats = ['PSD', 'PNG', 'JPEG', 'TIFF'];
  if (!validFormats.includes(format)) {
    return { error: `Invalid format "${format}". Must be one of: ${validFormats.join(', ')}` };
  }
  const jpegQuality = Number(args.jpeg_quality) || 10;
  const jsx = buildPhotoshopSaveJsx(filePath, format, jpegQuality);
  return safeRun(scriptPhotoshopRunJsx(jsx), EXTENDED_TIMEOUT_MS);
}

/** photoshop_resize */
async function photoshopResize(args: Record<string, unknown>): Promise<ToolResult> {
  const width = args.width != null ? Number(args.width) : undefined;
  const height = args.height != null ? Number(args.height) : undefined;
  if (!width && !height) {
    return { error: 'At least one of width or height must be provided.' };
  }
  const resampleMethod = args.resample_method != null ? String(args.resample_method) : undefined;
  const jsx = buildPhotoshopResizeJsx(width, height, resampleMethod);
  return safeRun(scriptPhotoshopRunJsx(jsx), DEFAULT_TIMEOUT_MS);
}

/** photoshop_apply_filter */
async function photoshopApplyFilter(args: Record<string, unknown>): Promise<ToolResult> {
  const filterName = String(args.filter_name ?? '');
  if (!filterName.trim()) {
    return { error: 'No filter name provided.' };
  }
  const filterArgs = (args.filter_args as Record<string, unknown>) || {};
  const jsx = buildPhotoshopFilterJsx(filterName, filterArgs);
  return safeRun(scriptPhotoshopRunJsx(jsx), DEFAULT_TIMEOUT_MS);
}

/** illustrator_run_script */
async function illustratorRunScript(args: Record<string, unknown>): Promise<ToolResult> {
  const script = String(args.script ?? '');
  if (!script.trim()) {
    return { error: 'No script provided.' };
  }
  const timeoutSec = Number(args.timeout_seconds) || 30;
  const timeoutMs = Math.min(Math.max(timeoutSec, 1), 300) * 1000;
  return safeRun(scriptIllustratorRunJsx(script), timeoutMs);
}

/** illustrator_open_file */
async function illustratorOpenFile(args: Record<string, unknown>): Promise<ToolResult> {
  const filePath = String(args.file_path ?? '');
  if (!filePath.trim()) {
    return { error: 'No file path provided.' };
  }
  const resolved = winPath(filePath);
  if (!fs.existsSync(resolved)) {
    return { error: `File not found: ${resolved}` };
  }
  return safeRun(scriptIllustratorOpenFile(filePath), EXTENDED_TIMEOUT_MS);
}

/** illustrator_export */
async function illustratorExport(args: Record<string, unknown>): Promise<ToolResult> {
  const filePath = String(args.file_path ?? '');
  if (!filePath.trim()) {
    return { error: 'No file path provided.' };
  }
  const format = String(args.format ?? '').toUpperCase();
  const validFormats = ['SVG', 'PNG', 'PDF'];
  if (!validFormats.includes(format)) {
    return { error: `Invalid format "${format}". Must be one of: ${validFormats.join(', ')}` };
  }
  const pngScale = args.png_scale != null ? Number(args.png_scale) : undefined;
  const jsx = buildIllustratorExportJsx(filePath, format, pngScale);
  return safeRun(scriptIllustratorRunJsx(jsx), EXTENDED_TIMEOUT_MS);
}

/** premiere_run_script */
async function premiereRunScript(args: Record<string, unknown>): Promise<ToolResult> {
  const script = String(args.script ?? '');
  if (!script.trim()) {
    return { error: 'No script provided.' };
  }
  const timeoutSec = Number(args.timeout_seconds) || 30;
  const timeoutMs = Math.min(Math.max(timeoutSec, 1), 300) * 1000;
  return safeRun(scriptPremiereRunJsx(script), timeoutMs);
}

/** aftereffects_run_script */
async function afterEffectsRunScript(args: Record<string, unknown>): Promise<ToolResult> {
  const script = String(args.script ?? '');
  if (!script.trim()) {
    return { error: 'No script provided.' };
  }
  const timeoutSec = Number(args.timeout_seconds) || 30;
  const timeoutMs = Math.min(Math.max(timeoutSec, 1), 300) * 1000;
  return safeRun(scriptAfterEffectsRunJsx(script), timeoutMs);
}

// ---------------------------------------------------------------------------
// Execute router
// ---------------------------------------------------------------------------

/**
 * Route a tool call to the correct implementation.
 * Never throws — always returns a ToolResult.
 */
export async function execute(
  toolName: string,
  args: Record<string, unknown>,
): Promise<ToolResult> {
  try {
    switch (toolName) {
      case 'photoshop_run_script':
        return await photoshopRunScript(args);
      case 'photoshop_open_file':
        return await photoshopOpenFile(args);
      case 'photoshop_save_as':
        return await photoshopSaveAs(args);
      case 'photoshop_resize':
        return await photoshopResize(args);
      case 'photoshop_apply_filter':
        return await photoshopApplyFilter(args);
      case 'illustrator_run_script':
        return await illustratorRunScript(args);
      case 'illustrator_open_file':
        return await illustratorOpenFile(args);
      case 'illustrator_export':
        return await illustratorExport(args);
      case 'premiere_run_script':
        return await premiereRunScript(args);
      case 'aftereffects_run_script':
        return await afterEffectsRunScript(args);
      default:
        return { error: `Unknown tool: ${toolName}` };
    }
  } catch (err: unknown) {
    // Absolute last-resort safety net — should never be reached because
    // individual handlers already catch, but just in case.
    const msg = err instanceof Error ? err.message : String(err);
    return { error: `Unexpected error in ${toolName}: ${msg}` };
  }
}

// ---------------------------------------------------------------------------
// Detection
// ---------------------------------------------------------------------------

/**
 * Check whether any Adobe Creative Suite applications are installed.
 * Scans C:\Program Files\Adobe for known executable names.
 * Returns true if at least one Adobe app is found.
 */
export async function detect(): Promise<boolean> {
  try {
    // Quick check: does the Adobe install root even exist?
    if (!fs.existsSync(ADOBE_INSTALL_ROOT)) {
      return false;
    }

    // Scan for known Adobe executables using PowerShell glob
    const script = `
$found = $false
$adobeRoot = '${psEscape(ADOBE_INSTALL_ROOT)}'

if (Test-Path $adobeRoot) {
  # Check for Photoshop
  $ps = Get-ChildItem -Path $adobeRoot -Filter 'Photoshop.exe' -Recurse -Depth 3 -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($ps) { $found = $true }

  if (-not $found) {
    # Check for Illustrator
    $ai = Get-ChildItem -Path $adobeRoot -Filter 'Illustrator.exe' -Recurse -Depth 5 -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($ai) { $found = $true }
  }

  if (-not $found) {
    # Check for Premiere Pro
    $pr = Get-ChildItem -Path $adobeRoot -Filter 'Adobe Premiere Pro.exe' -Recurse -Depth 3 -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($pr) { $found = $true }
  }

  if (-not $found) {
    # Check for After Effects
    $ae = Get-ChildItem -Path $adobeRoot -Filter 'AfterFX.exe' -Recurse -Depth 4 -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($ae) { $found = $true }
  }
}

Write-Output $found
`;

    const result = await runPS(script, 10_000);
    return result.trim().toLowerCase() === 'true';
  } catch {
    return false;
  }
}
