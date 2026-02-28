/**
 * media-streaming.ts  --  Media & Streaming connector for Agent Friday
 *
 * Provides tool declarations and an executor for controlling OBS Studio,
 * FFmpeg media processing, and audio device enumeration on Windows.
 *
 * OBS integration uses the obs-websocket v5 protocol via PowerShell's
 * System.Net.WebSockets.ClientWebSocket (no third-party dependencies).
 *
 * FFmpeg/FFprobe operations use child_process.execFile.
 * Audio device enumeration uses WMI queries via PowerShell.
 *
 * Exports:
 *   TOOLS    -- tool declarations array
 *   execute  -- async tool dispatcher
 *   detect   -- capability check (OBS installed OR FFmpeg in PATH)
 */

import { execFile, execSync } from 'child_process';
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
    required?: string[];
  };
}

interface ToolResult {
  result?: string;
  error?: string;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PS_TIMEOUT = 30_000;     // 30 s for PowerShell / OBS WebSocket calls
const FFMPEG_TIMEOUT = 120_000; // 2 min for media conversions
const MAX_OUTPUT = 64 * 1024;   // 64 KB cap on stdout

// ---------------------------------------------------------------------------
// Module-level state  (OBS WebSocket connection details)
// ---------------------------------------------------------------------------

let obsHost = '127.0.0.1';
let obsPort = 4455;
let obsPassword = '';
let obsConnected = false;

// ---------------------------------------------------------------------------
// PowerShell helper
// ---------------------------------------------------------------------------

function runPS(script: string, timeout = PS_TIMEOUT): string {
  const tmp = path.join(
    process.env.TEMP || process.env.TMP || 'C:\\Windows\\Temp',
    `friday_media_${Date.now()}_${Math.random().toString(36).slice(2, 8)}.ps1`,
  );
  try {
    fs.writeFileSync(tmp, script, 'utf-8');
    const out = execSync(
      `powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "${tmp}"`,
      {
        timeout,
        encoding: 'utf-8',
        stdio: ['pipe', 'pipe', 'pipe'],
        windowsHide: true,
        maxBuffer: MAX_OUTPUT,
      },
    );
    return (out ?? '').trim();
  } finally {
    try { fs.unlinkSync(tmp); } catch { /* ignore */ }
  }
}

/**
 * Escape a string for safe interpolation into a PowerShell single-quoted literal.
 */
function psEscape(s: string): string {
  return s.replace(/'/g, "''");
}

/**
 * Resolve and normalise a file path to an absolute Windows path.
 */
function winPath(p: string): string {
  return path.resolve(p).replace(/\//g, '\\');
}

// ---------------------------------------------------------------------------
// OBS WebSocket helper  (uses PowerShell + .NET ClientWebSocket)
//
// obs-websocket v5 protocol: JSON messages over WebSocket.
// We send a request and read one response. Authentication uses the
// obs-websocket challenge/response flow (SHA256 + Base64).
// ---------------------------------------------------------------------------

/**
 * Build a PowerShell script that opens a WebSocket to OBS, optionally
 * authenticates, sends a JSON request, and returns the JSON response.
 *
 * @param requestType  obs-websocket request type (e.g. "SetCurrentProgramScene")
 * @param requestData  optional data payload for the request
 */
function buildOBSScript(requestType: string, requestData?: Record<string, unknown>): string {
  const dataJson = requestData ? JSON.stringify(requestData) : '{}';
  const reqId = `nexus-${Date.now()}`;

  // The script:
  //  1. Connects to the OBS WebSocket
  //  2. Reads the Hello message (opcode 0)
  //  3. If authentication is required, computes the auth string
  //  4. Sends Identify (opcode 1)
  //  5. Reads Identified (opcode 2)
  //  6. Sends the actual request (opcode 6)
  //  7. Reads the response (opcode 7)
  //  8. Outputs the result JSON
  return `
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12

$uri = [Uri]"ws://${psEscape(obsHost)}:${obsPort}"
$ws  = [System.Net.WebSockets.ClientWebSocket]::new()
$cts = [System.Threading.CancellationTokenSource]::new(20000)

try {
  # Connect
  $ws.ConnectAsync($uri, $cts.Token).GetAwaiter().GetResult()

  # Helper: receive one full message
  function Receive-Message {
    $buf  = [byte[]]::new(65536)
    $seg  = [ArraySegment[byte]]::new($buf)
    $full = [System.Text.StringBuilder]::new()
    do {
      $r = $ws.ReceiveAsync($seg, $cts.Token).GetAwaiter().GetResult()
      $full.Append([System.Text.Encoding]::UTF8.GetString($buf, 0, $r.Count)) | Out-Null
    } while (-not $r.EndOfMessage)
    return $full.ToString()
  }

  # Helper: send a message
  function Send-Message($text) {
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($text)
    $seg   = [ArraySegment[byte]]::new($bytes)
    $ws.SendAsync($seg, [System.Net.WebSockets.WebSocketMessageType]::Text, $true, $cts.Token).GetAwaiter().GetResult()
  }

  # 1) Read Hello (opcode 0)
  $helloRaw = Receive-Message
  $hello    = $helloRaw | ConvertFrom-Json

  # 2) Build Identify message (opcode 1)
  $identify = @{ op = 1; d = @{ rpcVersion = 1 } }

  $auth = $hello.d.authentication
  if ($auth -and '${psEscape(obsPassword)}' -ne '') {
    # Compute auth string: Base64(SHA256(password + salt)) then Base64(SHA256(base64secret + challenge))
    $sha = [System.Security.Cryptography.SHA256]::Create()

    $passBytes   = [System.Text.Encoding]::UTF8.GetBytes('${psEscape(obsPassword)}' + $auth.salt)
    $base64Secret = [Convert]::ToBase64String($sha.ComputeHash($passBytes))

    $chalBytes   = [System.Text.Encoding]::UTF8.GetBytes($base64Secret + $auth.challenge)
    $authString  = [Convert]::ToBase64String($sha.ComputeHash($chalBytes))

    $identify.d.authentication = $authString
  }

  Send-Message ($identify | ConvertTo-Json -Depth 10 -Compress)

  # 3) Read Identified (opcode 2)
  $identifiedRaw = Receive-Message
  $identified    = $identifiedRaw | ConvertFrom-Json
  if ($identified.op -ne 2) {
    throw "OBS authentication failed. Response: $identifiedRaw"
  }

  # 4) Send the actual request (opcode 6)
  $request = @{
    op = 6
    d  = @{
      requestType = '${psEscape(requestType)}'
      requestId   = '${psEscape(reqId)}'
      requestData = ${dataJson} | ConvertFrom-Json
    }
  }
  # Re-serialize to handle the nested ConvertFrom-Json
  $reqObj = @{
    op = 6
    d  = @{
      requestType = '${psEscape(requestType)}'
      requestId   = '${psEscape(reqId)}'
    }
  }
  $reqDataParsed = '${psEscape(dataJson)}' | ConvertFrom-Json
  if (($reqDataParsed | Get-Member -MemberType NoteProperty).Count -gt 0) {
    $reqObj.d.requestData = $reqDataParsed
  }
  Send-Message ($reqObj | ConvertTo-Json -Depth 10 -Compress)

  # 5) Read the response (opcode 7)
  $respRaw = Receive-Message
  Write-Output $respRaw

} catch {
  Write-Error $_.Exception.Message
  exit 1
} finally {
  if ($ws.State -eq 'Open') {
    try {
      $ws.CloseAsync(
        [System.Net.WebSockets.WebSocketCloseStatus]::NormalClosure,
        'done',
        [System.Threading.CancellationToken]::None
      ).GetAwaiter().GetResult()
    } catch {}
  }
  $ws.Dispose()
  $cts.Dispose()
}
`;
}

/**
 * Send a request to OBS via WebSocket and return the parsed response data.
 */
function obsRequest(requestType: string, requestData?: Record<string, unknown>): Record<string, any> {
  const script = buildOBSScript(requestType, requestData);
  const raw = runPS(script);
  try {
    const parsed = JSON.parse(raw);
    // obs-websocket wraps responses in { op: 7, d: { requestType, requestId, requestStatus, responseData } }
    if (parsed.op === 7 && parsed.d) {
      const status = parsed.d.requestStatus;
      if (status && status.result === false) {
        throw new Error(`OBS error (code ${status.code}): ${status.comment || 'Unknown error'}`);
      }
      return parsed.d.responseData || {};
    }
    return parsed;
  } catch (err) {
    if (err instanceof SyntaxError) {
      throw new Error(`OBS returned non-JSON response: ${raw.slice(0, 500)}`);
    }
    throw err;
  }
}

// ---------------------------------------------------------------------------
// FFmpeg / FFprobe helpers
// ---------------------------------------------------------------------------

/**
 * Locate ffmpeg.exe in PATH or common install locations.
 * Returns the full path, or null if not found.
 */
function findFFmpeg(): string | null {
  // Check PATH first
  try {
    const out = execSync('where ffmpeg.exe', {
      encoding: 'utf-8',
      timeout: 5_000,
      windowsHide: true,
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    const first = out.trim().split(/\r?\n/)[0];
    if (first && fs.existsSync(first)) return first;
  } catch { /* not in PATH */ }

  // Check common install locations
  const candidates = [
    'C:\\ffmpeg\\bin\\ffmpeg.exe',
    'C:\\Program Files\\ffmpeg\\bin\\ffmpeg.exe',
    'C:\\Program Files (x86)\\ffmpeg\\bin\\ffmpeg.exe',
    path.join(os.homedir(), 'ffmpeg', 'bin', 'ffmpeg.exe'),
    path.join(os.homedir(), 'scoop', 'shims', 'ffmpeg.exe'),
  ];
  for (const c of candidates) {
    if (fs.existsSync(c)) return c;
  }
  return null;
}

/**
 * Locate ffprobe.exe — typically alongside ffmpeg.
 */
function findFFprobe(): string | null {
  try {
    const out = execSync('where ffprobe.exe', {
      encoding: 'utf-8',
      timeout: 5_000,
      windowsHide: true,
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    const first = out.trim().split(/\r?\n/)[0];
    if (first && fs.existsSync(first)) return first;
  } catch { /* not in PATH */ }

  const ffmpegPath = findFFmpeg();
  if (ffmpegPath) {
    const probePath = path.join(path.dirname(ffmpegPath), 'ffprobe.exe');
    if (fs.existsSync(probePath)) return probePath;
  }
  return null;
}

/**
 * Run an FFmpeg command with arguments and return stdout + stderr.
 */
async function runFFmpeg(args: string[], timeout = FFMPEG_TIMEOUT): Promise<string> {
  const ffmpegPath = findFFmpeg();
  if (!ffmpegPath) throw new Error('FFmpeg not found. Install FFmpeg and ensure it is in PATH.');

  const { stdout, stderr } = await execFileAsync(ffmpegPath, args, {
    timeout,
    maxBuffer: MAX_OUTPUT,
    windowsHide: true,
  });
  // FFmpeg writes most info to stderr
  return ((stdout || '') + '\n' + (stderr || '')).trim();
}

/**
 * Run an FFprobe command and return stdout.
 */
async function runFFprobe(args: string[], timeout = 30_000): Promise<string> {
  const ffprobePath = findFFprobe();
  if (!ffprobePath) throw new Error('FFprobe not found. Install FFmpeg (includes FFprobe) and ensure it is in PATH.');

  const { stdout } = await execFileAsync(ffprobePath, args, {
    timeout,
    maxBuffer: MAX_OUTPUT,
    windowsHide: true,
  });
  return (stdout || '').trim();
}

// ---------------------------------------------------------------------------
// Tool declarations
// ---------------------------------------------------------------------------

export const TOOLS: ToolDeclaration[] = [
  // ---- OBS Studio --------------------------------------------------------
  {
    name: 'obs_connect',
    description:
      'Connect to OBS Studio via obs-websocket v5. Stores connection parameters for subsequent OBS commands. Default host is 127.0.0.1, default port is 4455.',
    parameters: {
      type: 'object',
      properties: {
        host: { type: 'string', description: 'OBS WebSocket host (default: 127.0.0.1).' },
        port: { type: 'number', description: 'OBS WebSocket port (default: 4455).' },
        password: { type: 'string', description: 'OBS WebSocket password. Leave empty if authentication is disabled.' },
      },
      required: [],
    },
  },
  {
    name: 'obs_set_scene',
    description: 'Switch OBS to a different scene by name.',
    parameters: {
      type: 'object',
      properties: {
        scene_name: { type: 'string', description: 'Name of the scene to switch to.' },
      },
      required: ['scene_name'],
    },
  },
  {
    name: 'obs_start_recording',
    description: 'Start recording in OBS Studio.',
    parameters: {
      type: 'object',
      properties: {},
      required: [],
    },
  },
  {
    name: 'obs_stop_recording',
    description: 'Stop the current recording in OBS Studio.',
    parameters: {
      type: 'object',
      properties: {},
      required: [],
    },
  },
  {
    name: 'obs_start_streaming',
    description: 'Start streaming in OBS Studio using the configured stream settings.',
    parameters: {
      type: 'object',
      properties: {},
      required: [],
    },
  },
  {
    name: 'obs_stop_streaming',
    description: 'Stop the current stream in OBS Studio.',
    parameters: {
      type: 'object',
      properties: {},
      required: [],
    },
  },
  {
    name: 'obs_toggle_source',
    description: 'Show or hide a source within a scene in OBS Studio.',
    parameters: {
      type: 'object',
      properties: {
        scene_name: { type: 'string', description: 'Name of the scene containing the source.' },
        source_name: { type: 'string', description: 'Name of the source to toggle.' },
        visible: { type: 'boolean', description: 'true to show, false to hide the source.' },
      },
      required: ['scene_name', 'source_name', 'visible'],
    },
  },
  {
    name: 'obs_get_scenes',
    description: 'List all available scenes in OBS Studio, including the currently active scene.',
    parameters: {
      type: 'object',
      properties: {},
      required: [],
    },
  },
  {
    name: 'obs_screenshot',
    description:
      'Capture a screenshot of the current OBS output and save it to a file. Returns the file path of the saved screenshot.',
    parameters: {
      type: 'object',
      properties: {
        output_path: {
          type: 'string',
          description: 'File path to save the screenshot (PNG). Defaults to a temp file.',
        },
        width: { type: 'number', description: 'Optional output width in pixels.' },
        height: { type: 'number', description: 'Optional output height in pixels.' },
      },
      required: [],
    },
  },

  // ---- FFmpeg / FFprobe --------------------------------------------------
  {
    name: 'ffmpeg_convert',
    description:
      'Convert a media file from one format to another using FFmpeg. Supports video/audio format conversion, codec changes, and quality settings.',
    parameters: {
      type: 'object',
      properties: {
        input: { type: 'string', description: 'Path to the input media file.' },
        output: { type: 'string', description: 'Path for the output file (extension determines format).' },
        video_codec: { type: 'string', description: 'Video codec (e.g. libx264, libx265, copy). Omit for default.' },
        audio_codec: { type: 'string', description: 'Audio codec (e.g. aac, libmp3lame, copy). Omit for default.' },
        extra_args: {
          type: 'array',
          items: { type: 'string' },
          description: 'Additional FFmpeg arguments (e.g. ["-crf", "23", "-preset", "fast"]).',
        },
      },
      required: ['input', 'output'],
    },
  },
  {
    name: 'ffmpeg_extract_audio',
    description:
      'Extract the audio track from a video file using FFmpeg.',
    parameters: {
      type: 'object',
      properties: {
        input: { type: 'string', description: 'Path to the input video file.' },
        output: { type: 'string', description: 'Path for the output audio file (e.g. .mp3, .wav, .aac).' },
        audio_codec: { type: 'string', description: 'Audio codec (e.g. libmp3lame, pcm_s16le, copy). Defaults to codec appropriate for output extension.' },
      },
      required: ['input', 'output'],
    },
  },
  {
    name: 'ffmpeg_trim',
    description:
      'Trim (cut) a media file to a specific time range using FFmpeg.',
    parameters: {
      type: 'object',
      properties: {
        input: { type: 'string', description: 'Path to the input media file.' },
        output: { type: 'string', description: 'Path for the trimmed output file.' },
        start: { type: 'string', description: 'Start time (e.g. "00:01:30" or "90" for 90 seconds).' },
        duration: { type: 'string', description: 'Duration from start (e.g. "00:00:30" or "30"). If omitted, trims to end of file.' },
        end: { type: 'string', description: 'End time (alternative to duration). e.g. "00:02:00".' },
      },
      required: ['input', 'output', 'start'],
    },
  },
  {
    name: 'ffmpeg_info',
    description:
      'Get detailed information about a media file using FFprobe: duration, codecs, resolution, bitrate, frame rate, etc.',
    parameters: {
      type: 'object',
      properties: {
        input: { type: 'string', description: 'Path to the media file to inspect.' },
      },
      required: ['input'],
    },
  },

  // ---- Audio devices -----------------------------------------------------
  {
    name: 'audio_list_devices',
    description:
      'List all audio input (microphone) and output (speaker/headphone) devices on this Windows system.',
    parameters: {
      type: 'object',
      properties: {},
      required: [],
    },
  },
];

// ---------------------------------------------------------------------------
// Tool implementations
// ---------------------------------------------------------------------------

// ---- OBS tools -----------------------------------------------------------

function handleObsConnect(args: Record<string, unknown>): ToolResult {
  obsHost = args.host ? String(args.host) : '127.0.0.1';
  obsPort = args.port ? Number(args.port) : 4455;
  obsPassword = args.password ? String(args.password) : '';

  // Verify connectivity by requesting the OBS version
  try {
    const data = obsRequest('GetVersion');
    obsConnected = true;
    const version = data.obsVersion || 'unknown';
    const wsVersion = data.obsWebSocketVersion || 'unknown';
    return {
      result: `Connected to OBS Studio v${version} (obs-websocket v${wsVersion}) at ${obsHost}:${obsPort}.`,
    };
  } catch (err) {
    obsConnected = false;
    const message = err instanceof Error ? err.message : String(err);
    return { error: `Failed to connect to OBS at ${obsHost}:${obsPort}: ${message}` };
  }
}

function handleObsSetScene(args: Record<string, unknown>): ToolResult {
  const sceneName = String(args.scene_name);
  obsRequest('SetCurrentProgramScene', { sceneName });
  return { result: `Switched to scene: ${sceneName}` };
}

function handleObsStartRecording(): ToolResult {
  obsRequest('StartRecord');
  return { result: 'Recording started.' };
}

function handleObsStopRecording(): ToolResult {
  const data = obsRequest('StopRecord');
  const outputPath = data.outputPath || 'unknown';
  return { result: `Recording stopped. File saved: ${outputPath}` };
}

function handleObsStartStreaming(): ToolResult {
  obsRequest('StartStream');
  return { result: 'Streaming started.' };
}

function handleObsStopStreaming(): ToolResult {
  obsRequest('StopStream');
  return { result: 'Streaming stopped.' };
}

function handleObsToggleSource(args: Record<string, unknown>): ToolResult {
  const sceneName = String(args.scene_name);
  const sourceName = String(args.source_name);
  const visible = Boolean(args.visible);

  // First get the scene item ID for this source
  const itemData = obsRequest('GetSceneItemId', {
    sceneName,
    sourceName,
  });
  const sceneItemId = itemData.sceneItemId;
  if (sceneItemId == null) {
    return { error: `Source "${sourceName}" not found in scene "${sceneName}".` };
  }

  obsRequest('SetSceneItemEnabled', {
    sceneName,
    sceneItemId,
    sceneItemEnabled: visible,
  });
  return { result: `Source "${sourceName}" is now ${visible ? 'visible' : 'hidden'} in scene "${sceneName}".` };
}

function handleObsGetScenes(): ToolResult {
  const data = obsRequest('GetSceneList');
  const current = data.currentProgramSceneName || 'unknown';
  const scenes: Array<{ sceneName: string; sceneIndex: number }> = data.scenes || [];
  const sceneNames = scenes.map((s) => s.sceneName);
  return {
    result: [
      `Current scene: ${current}`,
      `All scenes (${sceneNames.length}):`,
      ...sceneNames.map((name, i) => `  ${i + 1}. ${name}${name === current ? ' (active)' : ''}`),
    ].join('\n'),
  };
}

function handleObsScreenshot(args: Record<string, unknown>): ToolResult {
  const outputPath = args.output_path
    ? winPath(String(args.output_path))
    : path.join(
        os.tmpdir(),
        `obs_screenshot_${Date.now()}.png`,
      );

  const requestData: Record<string, unknown> = {
    imageFormat: 'png',
    imageFilePath: outputPath,
  };
  if (args.width) requestData.imageWidth = Number(args.width);
  if (args.height) requestData.imageHeight = Number(args.height);

  // Use SaveSourceScreenshot on the current scene
  // First get the current scene name
  const sceneData = obsRequest('GetCurrentProgramScene');
  const currentScene = sceneData.currentProgramSceneName || sceneData.sceneName;

  obsRequest('SaveSourceScreenshot', {
    sourceName: currentScene,
    ...requestData,
  });

  return { result: `Screenshot saved: ${outputPath}` };
}

// ---- FFmpeg tools --------------------------------------------------------

async function handleFFmpegConvert(args: Record<string, unknown>): Promise<ToolResult> {
  const input = winPath(String(args.input));
  const output = winPath(String(args.output));

  if (!fs.existsSync(input)) {
    return { error: `Input file not found: ${input}` };
  }

  const ffArgs: string[] = ['-i', input, '-y']; // -y to overwrite without asking

  if (args.video_codec) {
    ffArgs.push('-c:v', String(args.video_codec));
  }
  if (args.audio_codec) {
    ffArgs.push('-c:a', String(args.audio_codec));
  }
  if (Array.isArray(args.extra_args)) {
    for (const a of args.extra_args) {
      ffArgs.push(String(a));
    }
  }

  ffArgs.push(output);

  await runFFmpeg(ffArgs);
  return { result: `Conversion complete: ${output}` };
}

async function handleFFmpegExtractAudio(args: Record<string, unknown>): Promise<ToolResult> {
  const input = winPath(String(args.input));
  const output = winPath(String(args.output));

  if (!fs.existsSync(input)) {
    return { error: `Input file not found: ${input}` };
  }

  const ffArgs: string[] = ['-i', input, '-vn', '-y']; // -vn = no video

  if (args.audio_codec) {
    ffArgs.push('-c:a', String(args.audio_codec));
  }

  ffArgs.push(output);

  await runFFmpeg(ffArgs);
  return { result: `Audio extracted: ${output}` };
}

async function handleFFmpegTrim(args: Record<string, unknown>): Promise<ToolResult> {
  const input = winPath(String(args.input));
  const output = winPath(String(args.output));
  const start = String(args.start);

  if (!fs.existsSync(input)) {
    return { error: `Input file not found: ${input}` };
  }

  const ffArgs: string[] = ['-ss', start, '-i', input, '-y'];

  if (args.duration) {
    ffArgs.push('-t', String(args.duration));
  } else if (args.end) {
    ffArgs.push('-to', String(args.end));
  }

  // Use stream copy for speed when no re-encoding is needed
  ffArgs.push('-c', 'copy');
  ffArgs.push(output);

  await runFFmpeg(ffArgs);
  return { result: `Trimmed file saved: ${output}` };
}

async function handleFFmpegInfo(args: Record<string, unknown>): Promise<ToolResult> {
  const input = winPath(String(args.input));

  if (!fs.existsSync(input)) {
    return { error: `Input file not found: ${input}` };
  }

  // Use ffprobe to get JSON info
  const jsonOutput = await runFFprobe([
    '-v', 'quiet',
    '-print_format', 'json',
    '-show_format',
    '-show_streams',
    input,
  ]);

  try {
    const info = JSON.parse(jsonOutput);
    const format = info.format || {};
    const streams: any[] = info.streams || [];

    const lines: string[] = [
      `File: ${path.basename(input)}`,
      `Format: ${format.format_long_name || format.format_name || 'unknown'}`,
      `Duration: ${format.duration ? Number(format.duration).toFixed(2) + ' seconds' : 'unknown'}`,
      `Size: ${format.size ? (Number(format.size) / (1024 * 1024)).toFixed(2) + ' MB' : 'unknown'}`,
      `Bitrate: ${format.bit_rate ? (Number(format.bit_rate) / 1000).toFixed(0) + ' kbps' : 'unknown'}`,
      '',
      `Streams (${streams.length}):`,
    ];

    for (const stream of streams) {
      if (stream.codec_type === 'video') {
        lines.push(`  Video: ${stream.codec_name || 'unknown'} ${stream.width}x${stream.height}`
          + ` @ ${stream.r_frame_rate || 'unknown'} fps`
          + (stream.bit_rate ? `, ${(Number(stream.bit_rate) / 1000).toFixed(0)} kbps` : '')
          + (stream.pix_fmt ? `, ${stream.pix_fmt}` : ''));
      } else if (stream.codec_type === 'audio') {
        lines.push(`  Audio: ${stream.codec_name || 'unknown'}`
          + ` ${stream.sample_rate || '?'} Hz`
          + `, ${stream.channels || '?'} ch`
          + (stream.bit_rate ? `, ${(Number(stream.bit_rate) / 1000).toFixed(0)} kbps` : '')
          + (stream.channel_layout ? ` (${stream.channel_layout})` : ''));
      } else if (stream.codec_type === 'subtitle') {
        lines.push(`  Subtitle: ${stream.codec_name || 'unknown'}`
          + (stream.tags?.language ? ` [${stream.tags.language}]` : ''));
      } else {
        lines.push(`  ${stream.codec_type}: ${stream.codec_name || 'unknown'}`);
      }
    }

    return { result: lines.join('\n') };
  } catch {
    // Fall back to raw output if JSON parsing fails
    return { result: jsonOutput };
  }
}

// ---- Audio device listing ------------------------------------------------

function handleAudioListDevices(): ToolResult {
  const script = `
$ErrorActionPreference = 'SilentlyContinue'
$sb = [System.Text.StringBuilder]::new()

# Method 1: Use WMI Win32_SoundDevice for basic info
$devices = Get-CimInstance -ClassName Win32_SoundDevice 2>$null
if ($devices) {
  [void]$sb.AppendLine("=== Sound Devices (WMI) ===")
  foreach ($d in $devices) {
    [void]$sb.AppendLine("  Name: $($d.Name)")
    [void]$sb.AppendLine("  Manufacturer: $($d.Manufacturer)")
    [void]$sb.AppendLine("  Status: $($d.Status)")
    [void]$sb.AppendLine("")
  }
}

# Method 2: Use PowerShell audio endpoint enumeration via COM
try {
  Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

[Guid("D666063F-1587-4E43-81F1-B948E807363F")]
[InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice {
    int Activate(ref Guid iid, int dwClsCtx, IntPtr pActivationParams, [MarshalAs(UnmanagedType.IUnknown)] out object ppInterface);
    int OpenPropertyStore(int stgmAccess, [MarshalAs(UnmanagedType.IUnknown)] out object ppProperties);
    int GetId([MarshalAs(UnmanagedType.LPWStr)] out string ppstrId);
    int GetState(out int pdwState);
}

[Guid("0BD7A1BE-7A1A-44DB-8397-CC5392387B5E")]
[InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceCollection {
    int GetCount(out int pcDevices);
    int Item(int nDevice, out IMMDevice ppDevice);
}

[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6")]
[InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator {
    int EnumAudioEndpoints(int dataFlow, int dwStateMask, out IMMDeviceCollection ppDevices);
    int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice ppEndpoint);
}

[ComImport]
[Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
public class MMDeviceEnumerator {}

public class AudioHelper {
    public static string ListEndpoints() {
        var sb = new System.Text.StringBuilder();
        try {
            var enumerator = (IMMDeviceEnumerator)new MMDeviceEnumerator();

            // 0 = eRender (output), 1 = eCapture (input), 1 = DEVICE_STATE_ACTIVE
            string[] flowNames = { "Output", "Input" };
            for (int flow = 0; flow <= 1; flow++) {
                IMMDeviceCollection collection;
                enumerator.EnumAudioEndpoints(flow, 1, out collection);
                int count;
                collection.GetCount(out count);
                sb.AppendLine("=== Audio " + flowNames[flow] + " Devices (" + count + ") ===");
                for (int i = 0; i < count; i++) {
                    IMMDevice device;
                    collection.Item(i, out device);
                    string id;
                    device.GetId(out id);

                    // Get friendly name via property store
                    object propStoreObj;
                    device.OpenPropertyStore(0, out propStoreObj);
                    sb.AppendLine("  Device " + (i+1) + ": " + id);
                }
                sb.AppendLine("");
            }
        } catch (Exception ex) {
            sb.AppendLine("COM enumeration error: " + ex.Message);
        }
        return sb.ToString();
    }
}
'@ -ErrorAction SilentlyContinue
  $comResult = [AudioHelper]::ListEndpoints()
  [void]$sb.Append($comResult)
} catch {}

# Method 3: Also list via pnputil / registry as fallback for friendly names
try {
  [void]$sb.AppendLine("=== Audio Endpoints (Registry) ===")
  $regPath = 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\MMDevices\\Audio'
  foreach ($flow in @('Render','Capture')) {
    $flowPath = Join-Path $regPath $flow
    if (Test-Path $flowPath) {
      [void]$sb.AppendLine("  --- $flow ---")
      $endpoints = Get-ChildItem $flowPath -ErrorAction SilentlyContinue
      foreach ($ep in $endpoints) {
        $props = Join-Path $ep.PSPath 'Properties'
        if (Test-Path $props) {
          # Property {a45c254e-df1c-4efd-8020-67d146a850e0},2 = friendly name
          $friendlyNameKey = '{a45c254e-df1c-4efd-8020-67d146a850e0},2'
          $val = (Get-ItemProperty -Path $props -Name $friendlyNameKey -ErrorAction SilentlyContinue).$friendlyNameKey
          if ($val) {
            # Check if device is active (DeviceState property {a45c254e-df1c-4efd-8020-67d146a850e0},1)
            $stateKey = 'DeviceState'
            $state = (Get-ItemProperty -Path $ep.PSPath -Name $stateKey -ErrorAction SilentlyContinue).$stateKey
            $stateStr = switch ($state) { 1 { 'Active' } 2 { 'Disabled' } 4 { 'NotPresent' } 8 { 'Unplugged' } default { "State=$state" } }
            [void]$sb.AppendLine("    $val [$stateStr]")
          }
        }
      }
    }
  }
} catch {}

Write-Output $sb.ToString()
`;
  const output = runPS(script, 15_000);
  return { result: output || 'No audio devices found.' };
}

// ---------------------------------------------------------------------------
// detect()
// ---------------------------------------------------------------------------

/**
 * Check whether OBS Studio or FFmpeg is available on this system.
 * Returns true if either is found.
 */
export async function detect(): Promise<boolean> {
  // Check for FFmpeg in PATH
  const ffmpegFound = findFFmpeg() !== null;
  if (ffmpegFound) return true;

  // Check for OBS Studio in standard install locations
  const obsLocations = [
    'C:\\Program Files\\obs-studio\\bin\\64bit\\obs64.exe',
    'C:\\Program Files (x86)\\obs-studio\\bin\\64bit\\obs64.exe',
    'C:\\Program Files\\obs-studio\\bin\\32bit\\obs32.exe',
  ];
  for (const loc of obsLocations) {
    if (fs.existsSync(loc)) return true;
  }

  // Check via registry as fallback
  try {
    const out = runPS(`
$found = $false
try {
  $obsKey = Get-ItemProperty 'HKLM:\\SOFTWARE\\OBS Studio' -ErrorAction SilentlyContinue
  if ($obsKey) { $found = $true }
} catch {}
if (-not $found) {
  try {
    $uninstall = Get-ChildItem 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall' -ErrorAction SilentlyContinue |
      Where-Object { (Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue).DisplayName -like '*OBS Studio*' }
    if ($uninstall) { $found = $true }
  } catch {}
}
Write-Output $found
`, 10_000);
    if (out.trim().toLowerCase() === 'true') return true;
  } catch { /* ignore */ }

  return false;
}

// ---------------------------------------------------------------------------
// execute()
// ---------------------------------------------------------------------------

export async function execute(
  toolName: string,
  args: Record<string, unknown>,
): Promise<ToolResult> {
  try {
    switch (toolName) {
      // -- OBS Studio -----------------------------------------------------
      case 'obs_connect':
        return handleObsConnect(args);

      case 'obs_set_scene':
        return handleObsSetScene(args);

      case 'obs_start_recording':
        return handleObsStartRecording();

      case 'obs_stop_recording':
        return handleObsStopRecording();

      case 'obs_start_streaming':
        return handleObsStartStreaming();

      case 'obs_stop_streaming':
        return handleObsStopStreaming();

      case 'obs_toggle_source':
        return handleObsToggleSource(args);

      case 'obs_get_scenes':
        return handleObsGetScenes();

      case 'obs_screenshot':
        return handleObsScreenshot(args);

      // -- FFmpeg / FFprobe -----------------------------------------------
      case 'ffmpeg_convert':
        return await handleFFmpegConvert(args);

      case 'ffmpeg_extract_audio':
        return await handleFFmpegExtractAudio(args);

      case 'ffmpeg_trim':
        return await handleFFmpegTrim(args);

      case 'ffmpeg_info':
        return await handleFFmpegInfo(args);

      // -- Audio devices --------------------------------------------------
      case 'audio_list_devices':
        return handleAudioListDevices();

      default:
        return { error: `Unknown tool: ${toolName}` };
    }
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    return { error: `Media-streaming tool "${toolName}" failed: ${message}` };
  }
}
