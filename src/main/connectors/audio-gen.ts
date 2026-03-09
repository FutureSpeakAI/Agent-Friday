/**
 * audio-gen.ts — "The Composer" connector for Agent Friday.
 *
 * Sprint 6 Track D: Exposes AI-powered audio/music creation and
 * composition tools to the Gemini function-calling layer.
 *
 * Two-tier architecture:
 *   Cloud  → Gemini 2.0 Flash (music, SFX, voice synthesis, podcasts)
 *            ElevenLabs (premium voice — optional, uses existing key)
 *   Local  → FFmpeg (mixing, effects, normalization, analysis)
 *
 * Delegates heavy lifting to the existing MultimediaEngine where possible,
 * adding new mixing/effects/SFX capabilities on top.
 *
 * Exports:
 *   TOOLS   — tool declarations array
 *   execute — async tool dispatcher
 *   detect  — returns true if Gemini key is configured OR FFmpeg available
 */

import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as https from 'node:https';
import { app } from 'electron';
import { settingsManager } from '../settings';

const execFileAsync = promisify(execFile);

// ── Types ────────────────────────────────────────────────────────────

interface ToolDeclaration {
  name: string;
  description: string;
  parameters: {
    type: string;
    properties: Record<string, unknown>;
    required?: string[];
  };
}

interface ToolResult {
  result?: string;
  error?: string;
}

// ── Constants ────────────────────────────────────────────────────────

const GEMINI_API_HOST = 'generativelanguage.googleapis.com';
const GEMINI_AUDIO_MODEL = 'gemini-2.0-flash';
const FFMPEG_TIMEOUT_MS = 120_000;

/** 30 prebuilt Gemini voices for speech synthesis */
const GEMINI_VOICES = [
  'Kore', 'Puck', 'Charon', 'Fenrir', 'Leda', 'Orus', 'Zephyr',
  'Aoede', 'Sulafat', 'Achird', 'Gacrux', 'Achernar', 'Sadachbia',
  'Vindemiatrix', 'Sadaltager', 'Schedar', 'Alnilam', 'Algieba',
  'Despina', 'Erinome', 'Algenib', 'Rasalgethi', 'Laomedeia',
  'Pulcherrima', 'Zubenelgenubi', 'Umbriel', 'Callirrhoe',
  'Autonoe', 'Enceladus', 'Iapetus',
] as const;

const MUSIC_TYPES = ['ambient', 'jingle', 'notification-tone', 'soundtrack', 'loop', 'intro'] as const;
const MOODS = [
  'calm', 'energetic', 'dark', 'uplifting', 'mysterious', 'epic',
  'playful', 'melancholic', 'triumphant', 'suspenseful', 'romantic',
  'futuristic', 'cinematic', 'relaxing', 'intense',
] as const;

const AUDIO_EFFECTS = [
  'normalize', 'fade_in', 'fade_out', 'speed', 'pitch',
  'reverb', 'bass_boost', 'treble_boost', 'compress', 'silence_trim',
] as const;

// ── WAV Header Utility ───────────────────────────────────────────────

function createWavHeader(dataLength: number, sampleRate = 24000, channels = 1, bits = 16): Buffer {
  const header = Buffer.alloc(44);
  const byteRate = sampleRate * channels * (bits / 8);
  const blockAlign = channels * (bits / 8);

  header.write('RIFF', 0);
  header.writeUInt32LE(36 + dataLength, 4);
  header.write('WAVE', 8);
  header.write('fmt ', 12);
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20);        // PCM
  header.writeUInt16LE(channels, 22);
  header.writeUInt32LE(sampleRate, 24);
  header.writeUInt32LE(byteRate, 28);
  header.writeUInt16LE(blockAlign, 32);
  header.writeUInt16LE(bits, 34);
  header.write('data', 36);
  header.writeUInt32LE(dataLength, 40);
  return header;
}

// ── FFmpeg Discovery ─────────────────────────────────────────────────

let _ffmpegPath: string | null = null;
let _ffprobePath: string | null = null;

async function findBinary(name: string): Promise<string | null> {
  // 1. Check PATH
  const cmd = process.platform === 'win32' ? 'where' : 'which';
  try {
    const { stdout } = await execFileAsync(cmd, [name], { timeout: 5_000 });
    const first = stdout.trim().split(/\r?\n/)[0];
    if (first && fs.existsSync(first)) return first;
  } catch { /* not on PATH */ }

  // 2. Common Windows install locations
  if (process.platform === 'win32') {
    const ext = '.exe';
    const candidates = [
      `C:\\ffmpeg\\bin\\${name}${ext}`,
      `C:\\Program Files\\ffmpeg\\bin\\${name}${ext}`,
      path.join(process.env.LOCALAPPDATA || '', 'ffmpeg', 'bin', `${name}${ext}`),
    ];
    for (const p of candidates) {
      if (p && fs.existsSync(p)) return p;
    }
  }

  return null;
}

async function findFFmpeg(): Promise<string | null> {
  if (_ffmpegPath !== null) return _ffmpegPath;
  _ffmpegPath = await findBinary('ffmpeg');
  return _ffmpegPath;
}

async function findFFprobe(): Promise<string | null> {
  if (_ffprobePath !== null) return _ffprobePath;
  _ffprobePath = await findBinary('ffprobe');
  return _ffprobePath;
}

// ── Gemini Audio API ─────────────────────────────────────────────────

/**
 * Send a Gemini generateContent request with response_modalities: ['AUDIO'].
 * Returns the raw base64 PCM audio from the response.
 */
async function geminiAudioRequest(
  systemPrompt: string,
  userPrompt: string,
  voice: string = 'Enceladus',
): Promise<{ audioBase64: string; mimeType: string }> {
  const apiKey = settingsManager.getGeminiApiKey();
  if (!apiKey) throw new Error('Gemini API key not configured — set it in Settings → API Keys');

  const body = JSON.stringify({
    system_instruction: {
      parts: [{ text: systemPrompt }],
    },
    contents: [{ parts: [{ text: userPrompt }] }],
    generation_config: {
      response_modalities: ['AUDIO'],
      speech_config: {
        voice_config: {
          prebuilt_voice_config: { voice_name: voice },
        },
      },
    },
  });

  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('Gemini audio request timed out (60s)')), 60_000);

    const req = https.request(
      {
        hostname: GEMINI_API_HOST,
        path: `/v1beta/models/${GEMINI_AUDIO_MODEL}:generateContent`,
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-goog-api-key': apiKey,
          'Content-Length': Buffer.byteLength(body),
        },
      },
      (res) => {
        let data = '';
        res.on('data', (chunk) => { data += chunk; });
        res.on('end', () => {
          clearTimeout(timeout);
          try {
            if (res.statusCode !== 200) {
              reject(new Error(`Gemini API error ${res.statusCode}: ${data.slice(0, 300)}`));
              return;
            }
            const json = JSON.parse(data);
            const parts = json.candidates?.[0]?.content?.parts || [];
            const audioPart = parts.find((p: any) => p.inlineData?.mimeType?.startsWith('audio/'));
            if (!audioPart) {
              reject(new Error('No audio content in Gemini response'));
              return;
            }
            resolve({
              audioBase64: audioPart.inlineData.data,
              mimeType: audioPart.inlineData.mimeType,
            });
          } catch (err) {
            reject(new Error(`Failed to parse Gemini response: ${err instanceof Error ? err.message : String(err)}`));
          }
        });
      },
    );

    req.on('error', (err) => { clearTimeout(timeout); reject(err); });
    req.write(body);
    req.end();
  });
}

/**
 * Save base64 PCM audio from Gemini as a WAV file.
 */
function saveAudioAsWav(audioBase64: string, outputPath: string): number {
  const pcm = Buffer.from(audioBase64, 'base64');
  const header = createWavHeader(pcm.length);
  const wav = Buffer.concat([header, pcm]);
  fs.writeFileSync(outputPath, wav);
  // Return duration in seconds (16-bit mono 24kHz)
  return pcm.length / (24000 * 2);
}

/**
 * Get the output directory for a given media type.
 */
function getOutputDir(subdir: string): string {
  const base = path.join(app.getPath('userData'), 'multimedia', subdir);
  fs.mkdirSync(base, { recursive: true });
  return base;
}

/**
 * Generate a timestamped filename.
 */
function makeFilename(prefix: string, ext = 'wav'): string {
  const ts = Date.now();
  const rand = Math.random().toString(36).slice(2, 8);
  return `${prefix}-${ts}-${rand}.${ext}`;
}

// ── ElevenLabs TTS (optional premium voice) ──────────────────────────

async function elevenLabsTTS(
  text: string,
  voiceId: string,
  stability = 0.5,
  similarityBoost = 0.75,
): Promise<Buffer> {
  const apiKey = settingsManager.getElevenLabsApiKey();
  if (!apiKey) throw new Error('ElevenLabs API key not configured');

  const body = JSON.stringify({
    text,
    model_id: 'eleven_turbo_v2_5',
    voice_settings: { stability, similarity_boost: similarityBoost },
  });

  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('ElevenLabs TTS timed out (30s)')), 30_000);

    const req = https.request(
      {
        hostname: 'api.elevenlabs.io',
        path: `/v1/text-to-speech/${voiceId}`,
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'xi-api-key': apiKey,
          'Accept': 'audio/mpeg',
          'Content-Length': Buffer.byteLength(body),
        },
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on('data', (chunk) => chunks.push(chunk));
        res.on('end', () => {
          clearTimeout(timeout);
          if (res.statusCode !== 200) {
            reject(new Error(`ElevenLabs API error ${res.statusCode}: ${Buffer.concat(chunks).toString().slice(0, 300)}`));
            return;
          }
          resolve(Buffer.concat(chunks));
        });
      },
    );

    req.on('error', (err) => { clearTimeout(timeout); reject(err); });
    req.write(body);
    req.end();
  });
}

// ── Tool Implementations ─────────────────────────────────────────────

async function handleGenerateMusic(args: Record<string, unknown>): Promise<ToolResult> {
  const mood = String(args.mood || 'calm');
  const style = args.style ? String(args.style) : undefined;
  const durationSec = Math.max(3, Math.min(30, Number(args.duration) || 10));
  const type = String(args.type || 'ambient');

  const styleNote = style ? ` Style: ${style}.` : '';
  const systemPrompt = `You are a music composer. Create a ${type} audio piece. Mood: ${mood}.${styleNote} Duration: approximately ${durationSec} seconds. Create ONLY musical audio — no speech, no explanations.`;
  const userPrompt = `Create a ${mood} ${type} piece, approximately ${durationSec} seconds long.`;

  try {
    const { audioBase64 } = await geminiAudioRequest(systemPrompt, userPrompt, 'Enceladus');
    const outputDir = getOutputDir('music');
    const filename = makeFilename(`music-${mood}`);
    const outputPath = path.join(outputDir, filename);
    const duration = saveAudioAsWav(audioBase64, outputPath);

    return {
      result: JSON.stringify({
        success: true,
        path: outputPath,
        duration: Math.round(duration * 10) / 10,
        mood,
        type,
        style: style || 'default',
        format: 'wav',
        sampleRate: 24000,
      }),
    };
  } catch (err) {
    return { error: `Music generation failed: ${err instanceof Error ? err.message : String(err)}` };
  }
}

async function handleGenerateSFX(args: Record<string, unknown>): Promise<ToolResult> {
  const description = String(args.description || '');
  if (!description) return { error: 'description is required — describe the sound effect you want' };

  const durationSec = Math.max(1, Math.min(15, Number(args.duration) || 3));

  const systemPrompt = `You are a sound designer. Create a sound effect that matches the description. Duration: approximately ${durationSec} seconds. Produce ONLY the sound — no speech, no music, no explanation. Just the raw sound effect.`;
  const userPrompt = `Sound effect: ${description}. Duration: ~${durationSec} seconds.`;

  try {
    const { audioBase64 } = await geminiAudioRequest(systemPrompt, userPrompt, 'Fenrir');
    const outputDir = getOutputDir('audio');
    const filename = makeFilename('sfx');
    const outputPath = path.join(outputDir, filename);
    const duration = saveAudioAsWav(audioBase64, outputPath);

    return {
      result: JSON.stringify({
        success: true,
        path: outputPath,
        duration: Math.round(duration * 10) / 10,
        description,
        format: 'wav',
      }),
    };
  } catch (err) {
    return { error: `SFX generation failed: ${err instanceof Error ? err.message : String(err)}` };
  }
}

async function handleSynthesizeSpeech(args: Record<string, unknown>): Promise<ToolResult> {
  const text = String(args.text || '');
  if (!text) return { error: 'text is required — provide the text to speak' };

  const voice = String(args.voice || 'Kore');
  const emotion = args.emotion ? String(args.emotion) : undefined;
  const provider = String(args.provider || 'gemini');
  const elevenlabsVoiceId = args.elevenlabs_voice_id ? String(args.elevenlabs_voice_id) : undefined;

  // Validate Gemini voice name
  if (provider === 'gemini' && !GEMINI_VOICES.includes(voice as any)) {
    return {
      error: `Unknown Gemini voice "${voice}". Available: ${GEMINI_VOICES.join(', ')}`,
    };
  }

  try {
    if (provider === 'elevenlabs' && elevenlabsVoiceId) {
      // ElevenLabs premium TTS
      const audioBuffer = await elevenLabsTTS(text, elevenlabsVoiceId);
      const outputDir = getOutputDir('audio');
      const filename = makeFilename('speech', 'mp3');
      const outputPath = path.join(outputDir, filename);
      fs.writeFileSync(outputPath, audioBuffer);

      return {
        result: JSON.stringify({
          success: true,
          path: outputPath,
          provider: 'elevenlabs',
          format: 'mp3',
          textLength: text.length,
        }),
      };
    }

    // Default: Gemini 2.0 Flash TTS
    const emotionDir = emotion ? `Speak with ${emotion} emotion.` : '';
    const systemPrompt = `You are a voice actor performing a reading. ${emotionDir} Speak naturally, with genuine feeling. Do not add or change any words — just speak exactly what is provided.`;

    const { audioBase64 } = await geminiAudioRequest(systemPrompt, text, voice);
    const outputDir = getOutputDir('audio');
    const filename = makeFilename('speech');
    const outputPath = path.join(outputDir, filename);
    const duration = saveAudioAsWav(audioBase64, outputPath);

    return {
      result: JSON.stringify({
        success: true,
        path: outputPath,
        duration: Math.round(duration * 10) / 10,
        voice,
        emotion: emotion || 'neutral',
        provider: 'gemini',
        format: 'wav',
        sampleRate: 24000,
      }),
    };
  } catch (err) {
    return { error: `Speech synthesis failed: ${err instanceof Error ? err.message : String(err)}` };
  }
}

async function handleCreatePodcast(args: Record<string, unknown>): Promise<ToolResult> {
  const topic = String(args.topic || '');
  if (!topic) return { error: 'topic is required — describe what the podcast should cover' };

  const format = String(args.format || 'deep-dive') as any;
  const duration = String(args.duration || 'medium') as any;
  const tone = String(args.tone || 'professional') as any;
  const audience = String(args.audience || 'general audience');

  try {
    // Use MultimediaEngine's podcast pipeline
    const { multimediaEngine } = await import('../multimedia-engine');

    const result = await multimediaEngine.generatePodcast({
      sources: [{ type: 'text', content: topic }],
      format,
      speakers: [], // Use presets
      duration,
      tone,
      audience,
    });

    return {
      result: JSON.stringify({
        success: true,
        path: result.audioPath,
        duration: Math.round(result.duration),
        title: result.title,
        segments: result.scriptSegments.length,
        format,
        tone,
      }),
    };
  } catch (err) {
    return { error: `Podcast creation failed: ${err instanceof Error ? err.message : String(err)}` };
  }
}

async function handleMixTracks(args: Record<string, unknown>): Promise<ToolResult> {
  const ffmpeg = await findFFmpeg();
  if (!ffmpeg) return { error: 'FFmpeg not found — install FFmpeg to mix audio tracks' };

  const tracks = args.tracks as Array<{ path: string; volume?: number; delay_ms?: number }> | undefined;
  if (!tracks || !Array.isArray(tracks) || tracks.length < 2) {
    return { error: 'tracks is required — provide an array of at least 2 track objects with { path, volume?, delay_ms? }' };
  }

  // Validate all track files exist
  for (const t of tracks) {
    if (!t.path || typeof t.path !== 'string') {
      return { error: 'Each track must have a "path" string' };
    }
    if (!fs.existsSync(t.path)) {
      return { error: `Track file not found: ${t.path}` };
    }
  }

  const outputPath = args.output_path
    ? String(args.output_path)
    : path.join(getOutputDir('audio'), makeFilename('mix'));

  try {
    // Build FFmpeg command for mixing
    // -i input1 -i input2 ... -filter_complex "[0]adelay=D1|D1,volume=V1[a0];[1]adelay=D2|D2,volume=V2[a1];...;[a0][a1]..amix=inputs=N"
    const inputArgs: string[] = [];
    const filterParts: string[] = [];
    const mixInputs: string[] = [];

    for (let i = 0; i < tracks.length; i++) {
      const t = tracks[i];
      inputArgs.push('-i', t.path);

      const vol = Math.max(0, Math.min(2, Number(t.volume) || 1));
      const delay = Math.max(0, Math.min(300_000, Number(t.delay_ms) || 0));

      const label = `a${i}`;
      if (delay > 0) {
        filterParts.push(`[${i}]adelay=${delay}|${delay},volume=${vol}[${label}]`);
      } else {
        filterParts.push(`[${i}]volume=${vol}[${label}]`);
      }
      mixInputs.push(`[${label}]`);
    }

    const filterComplex = `${filterParts.join(';')};${mixInputs.join('')}amix=inputs=${tracks.length}:duration=longest:dropout_transition=2`;

    const ffmpegArgs = [
      ...inputArgs,
      '-filter_complex', filterComplex,
      '-ac', '1',
      '-ar', '24000',
      '-y',
      outputPath,
    ];

    await execFileAsync(ffmpeg, ffmpegArgs, { timeout: FFMPEG_TIMEOUT_MS });

    return {
      result: JSON.stringify({
        success: true,
        path: outputPath,
        trackCount: tracks.length,
        format: path.extname(outputPath).slice(1) || 'wav',
      }),
    };
  } catch (err) {
    return { error: `Audio mixing failed: ${err instanceof Error ? err.message : String(err)}` };
  }
}

async function handleApplyEffects(args: Record<string, unknown>): Promise<ToolResult> {
  const ffmpeg = await findFFmpeg();
  if (!ffmpeg) return { error: 'FFmpeg not found — install FFmpeg to apply audio effects' };

  const inputPath = String(args.input_path || '');
  if (!inputPath || !fs.existsSync(inputPath)) {
    return { error: `Input file not found: ${inputPath || '(none)'}` };
  }

  const effects = args.effects as Record<string, unknown> | undefined;
  if (!effects || typeof effects !== 'object') {
    return { error: 'effects is required — object with effect names as keys. Available: normalize, fade_in, fade_out, speed, pitch, reverb, bass_boost, treble_boost, compress, silence_trim' };
  }

  const outputPath = args.output_path
    ? String(args.output_path)
    : path.join(getOutputDir('audio'), makeFilename('fx'));

  try {
    const filters: string[] = [];

    // Normalize loudness
    if (effects.normalize) {
      filters.push('loudnorm=I=-16:TP=-1.5:LRA=11');
    }

    // Fade in (value = duration in seconds)
    if (effects.fade_in) {
      const dur = Math.max(0.1, Math.min(10, Number(effects.fade_in) || 1));
      filters.push(`afade=t=in:st=0:d=${dur}`);
    }

    // Fade out (value = duration in seconds)
    if (effects.fade_out) {
      const dur = Math.max(0.1, Math.min(10, Number(effects.fade_out) || 1));
      // We need to know file duration for fade out — use a reverse approach
      filters.push(`areverse,afade=t=in:st=0:d=${dur},areverse`);
    }

    // Speed change (value = multiplier, e.g. 1.5 = 50% faster)
    if (effects.speed) {
      const speed = Math.max(0.5, Math.min(3, Number(effects.speed) || 1));
      filters.push(`atempo=${speed}`);
    }

    // Pitch shift (value = semitones, positive = higher)
    if (effects.pitch) {
      const semitones = Math.max(-12, Math.min(12, Number(effects.pitch) || 0));
      if (semitones !== 0) {
        const rate = Math.pow(2, semitones / 12);
        filters.push(`asetrate=24000*${rate.toFixed(6)},aresample=24000`);
      }
    }

    // Reverb simulation (simple echo-based)
    if (effects.reverb) {
      const intensity = Math.max(0.1, Math.min(1, Number(effects.reverb) || 0.3));
      filters.push(`aecho=0.8:${intensity}:60:0.4`);
    }

    // Bass boost
    if (effects.bass_boost) {
      const gain = Math.max(1, Math.min(20, Number(effects.bass_boost) || 5));
      filters.push(`bass=g=${gain}`);
    }

    // Treble boost
    if (effects.treble_boost) {
      const gain = Math.max(1, Math.min(20, Number(effects.treble_boost) || 5));
      filters.push(`treble=g=${gain}`);
    }

    // Dynamic compression
    if (effects.compress) {
      filters.push('acompressor=threshold=-20dB:ratio=4:attack=5:release=50');
    }

    // Trim silence from start and end
    if (effects.silence_trim) {
      filters.push('silenceremove=start_periods=1:start_silence=0.1:start_threshold=-50dB,areverse,silenceremove=start_periods=1:start_silence=0.1:start_threshold=-50dB,areverse');
    }

    if (filters.length === 0) {
      return { error: 'No recognized effects specified. Available: normalize, fade_in, fade_out, speed, pitch, reverb, bass_boost, treble_boost, compress, silence_trim' };
    }

    const ffmpegArgs = [
      '-i', inputPath,
      '-af', filters.join(','),
      '-y',
      outputPath,
    ];

    await execFileAsync(ffmpeg, ffmpegArgs, { timeout: FFMPEG_TIMEOUT_MS });

    return {
      result: JSON.stringify({
        success: true,
        path: outputPath,
        input: inputPath,
        appliedEffects: Object.keys(effects),
        format: path.extname(outputPath).slice(1) || 'wav',
      }),
    };
  } catch (err) {
    return { error: `Audio effects failed: ${err instanceof Error ? err.message : String(err)}` };
  }
}

async function handleListVoices(_args: Record<string, unknown>): Promise<ToolResult> {
  const voices: Array<{ name: string; provider: string; id: string }> = [];

  // Gemini voices
  for (const v of GEMINI_VOICES) {
    voices.push({ name: v, provider: 'gemini', id: v });
  }

  // Check if ElevenLabs key is available
  const hasElevenLabs = !!settingsManager.getElevenLabsApiKey();

  return {
    result: JSON.stringify({
      geminiVoices: GEMINI_VOICES.length,
      elevenLabsAvailable: hasElevenLabs,
      voices,
      hint: hasElevenLabs
        ? 'ElevenLabs voices available — use provider="elevenlabs" with elevenlabs_voice_id in composer_synthesize_speech'
        : 'Set an ElevenLabs API key in Settings to unlock premium voices',
    }),
  };
}

async function handleAnalyzeAudio(args: Record<string, unknown>): Promise<ToolResult> {
  const ffprobe = await findFFprobe();
  if (!ffprobe) return { error: 'FFprobe not found — install FFmpeg to analyze audio files' };

  const inputPath = String(args.input_path || '');
  if (!inputPath || !fs.existsSync(inputPath)) {
    return { error: `Input file not found: ${inputPath || '(none)'}` };
  }

  try {
    // Get detailed audio metadata
    const { stdout } = await execFileAsync(ffprobe, [
      '-v', 'quiet',
      '-print_format', 'json',
      '-show_format',
      '-show_streams',
      inputPath,
    ], { timeout: 15_000 });

    const info = JSON.parse(stdout);
    const audioStream = info.streams?.find((s: any) => s.codec_type === 'audio');
    const format = info.format || {};

    // Get loudness statistics
    let loudness: Record<string, unknown> | null = null;
    const ffmpeg = await findFFmpeg();
    if (ffmpeg) {
      try {
        const { stderr } = await execFileAsync(ffmpeg, [
          '-i', inputPath,
          '-af', 'loudnorm=print_format=json',
          '-f', 'null',
          '-',
        ], { timeout: 30_000 });

        // loudnorm prints JSON in stderr
        const jsonMatch = stderr.match(/\{[\s\S]*"input_i"[\s\S]*\}/);
        if (jsonMatch) {
          loudness = JSON.parse(jsonMatch[0]);
        }
      } catch { /* loudness analysis optional */ }
    }

    return {
      result: JSON.stringify({
        file: inputPath,
        duration: parseFloat(format.duration) || 0,
        size: parseInt(format.size) || 0,
        bitrate: parseInt(format.bit_rate) || 0,
        formatName: format.format_name,
        codec: audioStream?.codec_name,
        sampleRate: parseInt(audioStream?.sample_rate) || 0,
        channels: audioStream?.channels || 0,
        channelLayout: audioStream?.channel_layout,
        bitsPerSample: audioStream?.bits_per_raw_sample || audioStream?.bits_per_sample,
        loudness: loudness || 'unavailable',
      }),
    };
  } catch (err) {
    return { error: `Audio analysis failed: ${err instanceof Error ? err.message : String(err)}` };
  }
}

// ── Tool Declarations ────────────────────────────────────────────────

export const TOOLS: ToolDeclaration[] = [
  {
    name: 'composer_generate_music',
    description: 'Generate AI music via Gemini 2.0 Flash — ambient, jingles, soundtracks, loops. Returns a WAV file path. Supports mood, style, duration, and type parameters.',
    parameters: {
      type: 'object',
      properties: {
        mood: {
          type: 'string',
          description: `Music mood/feeling. Suggestions: ${MOODS.join(', ')}`,
        },
        style: {
          type: 'string',
          description: 'Optional style descriptor (e.g. "lo-fi hip hop", "orchestral", "electronic", "acoustic guitar")',
        },
        duration: {
          type: 'number',
          description: 'Duration in seconds (3-30, default 10)',
        },
        type: {
          type: 'string',
          description: `Music type: ${MUSIC_TYPES.join(', ')}`,
        },
      },
      required: ['mood'],
    },
  },
  {
    name: 'composer_generate_sfx',
    description: 'Generate AI sound effects via Gemini 2.0 Flash — whooshes, clicks, alerts, nature, mechanical, explosions, etc. Returns a WAV file path.',
    parameters: {
      type: 'object',
      properties: {
        description: {
          type: 'string',
          description: 'Detailed description of the sound effect (e.g. "futuristic door opening with a hydraulic hiss")',
        },
        duration: {
          type: 'number',
          description: 'Duration in seconds (1-15, default 3)',
        },
      },
      required: ['description'],
    },
  },
  {
    name: 'composer_synthesize_speech',
    description: 'High-quality text-to-speech synthesis via Gemini (30 voices) or ElevenLabs (premium). Returns audio file path. Supports emotion control and voice selection.',
    parameters: {
      type: 'object',
      properties: {
        text: {
          type: 'string',
          description: 'The text to speak aloud',
        },
        voice: {
          type: 'string',
          description: `Gemini voice name. Available: ${GEMINI_VOICES.slice(0, 10).join(', ')}... (use composer_list_voices for full list)`,
        },
        emotion: {
          type: 'string',
          description: 'Optional emotion/direction (e.g. "enthusiastic", "calm", "dramatic", "whispering")',
        },
        provider: {
          type: 'string',
          description: 'TTS provider: "gemini" (default, free) or "elevenlabs" (premium, requires API key)',
        },
        elevenlabs_voice_id: {
          type: 'string',
          description: 'ElevenLabs voice ID — required when provider is "elevenlabs"',
        },
      },
      required: ['text'],
    },
  },
  {
    name: 'composer_create_podcast',
    description: 'Create a multi-speaker AI podcast from any topic or content. Uses Claude for script generation and Gemini for voice synthesis. Returns a WAV file path with full podcast audio.',
    parameters: {
      type: 'object',
      properties: {
        topic: {
          type: 'string',
          description: 'The topic, content, or source material for the podcast',
        },
        format: {
          type: 'string',
          description: 'Podcast format: deep-dive, summary, debate, interview, explainer, storytelling',
        },
        duration: {
          type: 'string',
          description: 'Duration target: short (~3 min), medium (~8 min), long (~15 min)',
        },
        tone: {
          type: 'string',
          description: 'Conversational tone: professional, casual, educational, playful',
        },
        audience: {
          type: 'string',
          description: 'Target audience description (e.g. "software engineers", "general public")',
        },
      },
      required: ['topic'],
    },
  },
  {
    name: 'composer_mix_tracks',
    description: 'Mix multiple audio tracks together using FFmpeg — layer music, voice, and SFX with per-track volume and delay control. Requires FFmpeg installed locally.',
    parameters: {
      type: 'object',
      properties: {
        tracks: {
          type: 'array',
          description: 'Array of track objects: [{ path: string, volume?: number (0-2, default 1), delay_ms?: number }]',
        },
        output_path: {
          type: 'string',
          description: 'Optional output file path. Auto-generated if not provided.',
        },
      },
      required: ['tracks'],
    },
  },
  {
    name: 'composer_apply_effects',
    description: 'Apply audio effects to a file using FFmpeg — normalize loudness, add reverb, fade in/out, change speed/pitch, compress dynamics, boost bass/treble, trim silence. Requires FFmpeg.',
    parameters: {
      type: 'object',
      properties: {
        input_path: {
          type: 'string',
          description: 'Path to the input audio file',
        },
        effects: {
          type: 'object',
          description: 'Effects to apply. Keys: normalize (bool), fade_in (seconds), fade_out (seconds), speed (multiplier 0.5-3), pitch (semitones -12 to 12), reverb (intensity 0-1), bass_boost (dB 1-20), treble_boost (dB 1-20), compress (bool), silence_trim (bool)',
        },
        output_path: {
          type: 'string',
          description: 'Optional output path. Auto-generated if not provided.',
        },
      },
      required: ['input_path', 'effects'],
    },
  },
  {
    name: 'composer_list_voices',
    description: 'List all available voices for speech synthesis — 30 Gemini voices plus ElevenLabs availability status.',
    parameters: {
      type: 'object',
      properties: {},
    },
  },
  {
    name: 'composer_analyze_audio',
    description: 'Analyze an audio file — get duration, codec, sample rate, channels, bitrate, loudness statistics, and format details. Requires FFprobe.',
    parameters: {
      type: 'object',
      properties: {
        input_path: {
          type: 'string',
          description: 'Path to the audio file to analyze',
        },
      },
      required: ['input_path'],
    },
  },
];

// ── Execute Dispatcher ───────────────────────────────────────────────

export async function execute(
  toolName: string,
  args: Record<string, unknown>,
): Promise<ToolResult> {
  try {
    switch (toolName) {
      case 'composer_generate_music':
        return await handleGenerateMusic(args);
      case 'composer_generate_sfx':
        return await handleGenerateSFX(args);
      case 'composer_synthesize_speech':
        return await handleSynthesizeSpeech(args);
      case 'composer_create_podcast':
        return await handleCreatePodcast(args);
      case 'composer_mix_tracks':
        return await handleMixTracks(args);
      case 'composer_apply_effects':
        return await handleApplyEffects(args);
      case 'composer_list_voices':
        return await handleListVoices(args);
      case 'composer_analyze_audio':
        return await handleAnalyzeAudio(args);
      default:
        return { error: `Unknown composer tool: ${toolName}` };
    }
  } catch (err) {
    return { error: `Composer error: ${err instanceof Error ? err.message : String(err)}` };
  }
}

// ── Detect ────────────────────────────────────────────────────────────

export async function detect(): Promise<boolean> {
  // Available if Gemini key is configured (for AI audio generation)
  // OR FFmpeg is installed (for mixing/effects/analysis)
  const hasGemini = !!settingsManager.getGeminiApiKey();
  const hasFFmpeg = !!(await findFFmpeg());
  return hasGemini || hasFFmpeg;
}
