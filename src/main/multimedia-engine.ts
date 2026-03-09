/**
 * multimedia-engine.ts — Podcast, visual, audio, and media creation engine.
 *
 * Transforms any content into multi-speaker podcasts, infographics,
 * voice messages, music, and video using Gemini TTS + Claude Sonnet
 * for script generation + code-based visual rendering.
 *
 * Architecture:
 *   Script/content generation  → Claude Sonnet (deep reasoning)
 *   Voice synthesis per speaker → Gemini 2.0 Flash REST (native audio)
 *   Audio assembly              → Pure Node.js PCM/WAV concatenation
 *   Visual generation           → HTML/SVG → Puppeteer screenshot
 *   Permissions                 → Trust-gated creative autonomy
 */

import { llmClient } from './llm-client';
import { settingsManager } from './settings';
import { privacyShield } from './privacy-shield';
import * as fs from 'fs';
import * as fsAsync from 'fs/promises';
import * as path from 'path';
import * as crypto from 'crypto';
import { app } from 'electron';
import type { GeminiVoiceName } from './voice-audition';

// ── Types ──────────────────────────────────────────────────────────

export interface PodcastSource {
  type: 'file' | 'url' | 'text' | 'conversation' | 'memory';
  content: string; // path, URL, or raw text
}

export interface PodcastSpeaker {
  role: 'host' | 'expert' | 'skeptic' | 'learner' | 'narrator';
  voice: GeminiVoiceName;
  personality: string;
}

export interface PodcastRequest {
  sources: PodcastSource[];
  format: 'deep-dive' | 'summary' | 'debate' | 'interview' | 'explainer' | 'storytelling';
  speakers: PodcastSpeaker[];
  duration: 'short' | 'medium' | 'long'; // ~3min / ~8min / ~15min
  tone: 'professional' | 'casual' | 'educational' | 'playful';
  audience: string;
}

export interface ScriptSegment {
  speakerIndex: number;
  speakerRole: string;
  text: string;
  direction?: string; // vocal direction e.g. "speak with enthusiasm"
}

export interface PodcastResult {
  audioPath: string;
  scriptSegments: ScriptSegment[];
  duration: number; // estimated seconds
  title: string;
}

export interface VisualRequest {
  type: 'infographic' | 'chart' | 'diagram' | 'timeline' | 'mindmap' |
        'comparison' | 'dashboard' | 'storyboard';
  source: string; // raw text or data
  style: 'professional' | 'playful' | 'minimal' | 'rich';
  format: 'png' | 'svg' | 'html';
  title?: string;
}

export interface VisualResult {
  imagePath: string;
  htmlPath?: string;
  title: string;
}

export interface AudioMessageRequest {
  type: 'voice-message' | 'voice-letter' | 'audio-note';
  content: string;
  voice: GeminiVoiceName;
  emotion?: string;
  title?: string;
}

export interface AudioMessageResult {
  audioPath: string;
  duration: number;
  title: string;
}

export interface MusicRequest {
  type: 'ambient' | 'jingle' | 'notification-tone';
  mood: string;
  duration: number; // seconds
  style?: string;
}

export interface MusicResult {
  audioPath: string;
  duration: number;
  title: string;
}

export interface CreativePermissions {
  canCreateOnRequest: true;     // Always — user asks, agent creates
  canCreateDrafts: boolean;     // Agent creates media and presents for approval
  canCreateBriefings: boolean;  // Agent produces morning/weekly audio briefings
  canCreateUnprompted: boolean; // Agent creates media it thinks user would enjoy
  canCreateAutonomously: boolean; // Agent creates as personal expression
}

// ── Constants ──────────────────────────────────────────────────────

const MEDIA_DIR = 'multimedia';

const DURATION_TARGETS: Record<string, number> = {
  short: 180,    // ~3 min
  medium: 480,   // ~8 min
  long: 900,     // ~15 min
};

const SPEAKER_PRESETS: Record<string, PodcastSpeaker[]> = {
  'deep-dive': [
    { role: 'host', voice: 'Puck', personality: 'sharp, curious, guides the conversation' },
    { role: 'expert', voice: 'Kore', personality: 'authoritative but accessible, goes deep' },
  ],
  'debate': [
    { role: 'host', voice: 'Charon', personality: 'neutral moderator, asks hard questions' },
    { role: 'expert', voice: 'Aoede', personality: 'advocates for position A, passionate' },
    { role: 'skeptic', voice: 'Fenrir', personality: 'advocates for position B, incisive' },
  ],
  'summary': [
    { role: 'host', voice: 'Puck', personality: 'clear and efficient, highlights key points' },
    { role: 'expert', voice: 'Leda', personality: 'warm, adds context and color' },
  ],
  'interview': [
    { role: 'host', voice: 'Achird', personality: 'curious interviewer, asks follow-ups' },
    { role: 'expert', voice: 'Enceladus', personality: 'thoughtful subject, gives detailed answers' },
  ],
  'explainer': [
    { role: 'expert', voice: 'Leda', personality: 'warm teacher, breaks things down' },
    { role: 'learner', voice: 'Achird', personality: 'asks the questions the audience is thinking' },
  ],
  'storytelling': [
    { role: 'narrator', voice: 'Enceladus', personality: 'measured, evocative, lets moments breathe' },
  ],
};

// ── WAV Header Utility ─────────────────────────────────────────────

function createWavHeader(dataLength: number, sampleRate: number, channels: number, bitsPerSample: number): Buffer {
  const header = Buffer.alloc(44);
  const byteRate = sampleRate * channels * (bitsPerSample / 8);
  const blockAlign = channels * (bitsPerSample / 8);

  header.write('RIFF', 0);
  header.writeUInt32LE(36 + dataLength, 4);
  header.write('WAVE', 8);
  header.write('fmt ', 12);
  header.writeUInt32LE(16, 16);       // PCM chunk size
  header.writeUInt16LE(1, 20);        // PCM format
  header.writeUInt16LE(channels, 22);
  header.writeUInt32LE(sampleRate, 24);
  header.writeUInt32LE(byteRate, 28);
  header.writeUInt16LE(blockAlign, 32);
  header.writeUInt16LE(bitsPerSample, 34);
  header.write('data', 36);
  header.writeUInt32LE(dataLength, 40);
  return header;
}

function createSilenceBuffer(durationMs: number, sampleRate: number): Buffer {
  const numSamples = Math.floor((sampleRate * durationMs) / 1000);
  return Buffer.alloc(numSamples * 2); // 16-bit = 2 bytes per sample, silence = zeros
}

// ── Engine ─────────────────────────────────────────────────────────

class MultimediaEngine {
  private mediaDir = '';
  private permissions: CreativePermissions = {
    canCreateOnRequest: true,
    canCreateDrafts: false,
    canCreateBriefings: false,
    canCreateUnprompted: false,
    canCreateAutonomously: false,
  };

  async initialize(): Promise<void> {
    this.mediaDir = path.join(app.getPath('userData'), MEDIA_DIR);
    await fsAsync.mkdir(this.mediaDir, { recursive: true });
    await fsAsync.mkdir(path.join(this.mediaDir, 'podcasts'), { recursive: true });
    await fsAsync.mkdir(path.join(this.mediaDir, 'visuals'), { recursive: true });
    await fsAsync.mkdir(path.join(this.mediaDir, 'audio'), { recursive: true });
    await fsAsync.mkdir(path.join(this.mediaDir, 'music'), { recursive: true });

    // Load permissions from settings
    const saved = settingsManager.get().creativePermissions;
    if (saved) {
      this.permissions = { ...this.permissions, ...saved };
    }

    console.log('[Multimedia] Engine initialized');
  }

  // ── Podcast Generation ───────────────────────────────────────────

  async generatePodcast(request: PodcastRequest): Promise<PodcastResult> {
    const startTime = Date.now();
    console.log(`[Multimedia] Generating ${request.format} podcast (${request.duration})...`);

    // Step 1: Collect source content
    const sourceContent = await this.collectSources(request.sources);

    // Step 2: Use default speakers if none provided
    const speakers = request.speakers.length > 0
      ? request.speakers
      : SPEAKER_PRESETS[request.format] || SPEAKER_PRESETS['deep-dive'];

    // Step 3: Generate script via Claude Sonnet
    const script = await this.generateScript(sourceContent, speakers, request);

    // Step 4: Synthesize each segment via Gemini TTS
    const audioSegments = await this.synthesizeSegments(script, speakers);

    // Step 5: Assemble final audio
    const fileName = `podcast-${Date.now()}-${crypto.randomBytes(3).toString('hex')}.wav`;
    const audioPath = path.join(this.mediaDir, 'podcasts', fileName);
    const duration = await this.assembleAudio(audioSegments, audioPath);

    const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
    console.log(`[Multimedia] Podcast generated in ${elapsed}s — ${duration.toFixed(0)}s audio`);

    return {
      audioPath,
      scriptSegments: script,
      duration,
      title: `${request.format} podcast — ${new Date().toLocaleDateString()}`,
    };
  }

  private async collectSources(sources: PodcastSource[]): Promise<string> {
    const parts: string[] = [];

    for (const source of sources) {
      switch (source.type) {
        case 'text':
          parts.push(source.content);
          break;
        case 'file':
          try {
            const content = await fsAsync.readFile(source.content, 'utf-8');
            parts.push(content.slice(0, 50000)); // cap at 50k chars
          } catch {
            parts.push(`[Could not read file: ${source.content}]`);
          }
          break;
        case 'url':
          parts.push(`[Content from URL: ${source.content}]`);
          break;
        case 'conversation':
        case 'memory':
          parts.push(source.content);
          break;
      }
    }

    return parts.join('\n\n---\n\n');
  }

  private async generateScript(
    sourceContent: string,
    speakers: PodcastSpeaker[],
    request: PodcastRequest,
  ): Promise<ScriptSegment[]> {
    const targetDuration = DURATION_TARGETS[request.duration] || 480;
    // Rough estimate: 150 words/minute spoken → words needed
    const targetWords = Math.floor((targetDuration / 60) * 150);

    const speakerDescriptions = speakers.map((s, i) =>
      `Speaker ${i + 1} (${s.role}): ${s.personality}`
    ).join('\n');

    const response = await llmClient.complete({
      messages: [{
        role: 'user',
        content: `You are a podcast script writer. Generate a ${request.format} podcast script from the following source material.

TARGET: ~${targetWords} words total (${request.duration} duration: ~${Math.floor(targetDuration / 60)} minutes)
TONE: ${request.tone}
AUDIENCE: ${request.audience}
FORMAT: ${request.format}

SPEAKERS:
${speakerDescriptions}

RULES:
- Write natural, conversational dialogue — NOT a lecture
- Include natural elements: "oh that's interesting", building on points, brief interruptions
- Each speaker stays in character with their described personality
- Vary segment lengths: some short exchanges, some longer explanations
- Include transitions between topics
- Start with a brief intro, end with a wrap-up
- Do NOT include stage directions or meta-commentary — just the spoken words

OUTPUT FORMAT: Return a JSON array of segments, each with:
- speakerIndex: number (0-based, matching speaker order above)
- text: string (what they say — 1-4 sentences per segment)
- direction: string (optional vocal direction like "enthusiastic", "thoughtful", "questioning")

Example:
[
  {"speakerIndex": 0, "text": "Welcome everyone. Today we're diving into something fascinating.", "direction": "warm, welcoming"},
  {"speakerIndex": 1, "text": "Thanks for having me. This topic is one I've been thinking about a lot lately.", "direction": "genuine interest"}
]

SOURCE MATERIAL:
${sourceContent.slice(0, 30000)}`,
      }],
      maxTokens: 8192,
    });

    const rawText = response.content || '[]';

    // Extract JSON array from response (may be wrapped in markdown code block)
    const jsonMatch = rawText.match(/\[[\s\S]*\]/);
    if (!jsonMatch) {
      console.error('[Multimedia] Failed to parse script JSON');
      return [{
        speakerIndex: 0,
        speakerRole: speakers[0]?.role || 'host',
        text: 'I was unable to generate a full script for this content. Here is a brief summary instead.',
        direction: 'apologetic',
      }];
    }

    try {
      const parsed: Array<{ speakerIndex: number; text: string; direction?: string }> = JSON.parse(jsonMatch[0]);
      return parsed.map((seg) => ({
        speakerIndex: seg.speakerIndex,
        speakerRole: speakers[seg.speakerIndex]?.role || 'host',
        text: seg.text,
        direction: seg.direction,
      }));
    } catch {
      console.error('[Multimedia] JSON parse error in script');
      return [];
    }
  }

  private async synthesizeSegments(
    script: ScriptSegment[],
    speakers: PodcastSpeaker[],
  ): Promise<Array<{ audio: Buffer; mimeType: string }>> {
    const apiKey = settingsManager.getGeminiApiKey();
    if (!apiKey) throw new Error('Gemini API key not configured');

    const segments: Array<{ audio: Buffer; mimeType: string }> = [];

    for (let i = 0; i < script.length; i++) {
      const seg = script[i];
      const speaker = speakers[seg.speakerIndex] || speakers[0];
      const direction = seg.direction ? `Speak ${seg.direction}.` : '';

      console.log(`[Multimedia] Synthesizing segment ${i + 1}/${script.length} (${speaker.role})...`);

      // Privacy Shield: scrub podcast text before sending to Google cloud TTS.
      const shieldOn = privacyShield.isEnabled();
      const ttsText = shieldOn ? privacyShield.scrub(seg.text).text : seg.text;

      try {
        const response = await fetch(
          'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent',
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'x-goog-api-key': apiKey,
            },
            body: JSON.stringify({
              system_instruction: {
                parts: [{
                  text: `You are a podcast speaker. ${direction} Speak the user's text aloud naturally and expressively. Do not add, remove, or change any words. Just speak exactly what is provided with the appropriate tone and emotion.`,
                }],
              },
              contents: [{ parts: [{ text: ttsText }] }],
              generation_config: {
                response_modalities: ['AUDIO'],
                speech_config: {
                  voice_config: {
                    prebuilt_voice_config: {
                      voice_name: speaker.voice,
                    },
                  },
                },
              },
            }),
          },
        );

        if (!response.ok) {
          console.error(`[Multimedia] TTS failed for segment ${i + 1}: ${response.status}`);
          continue;
        }

        const data = await response.json();
        const parts = data.candidates?.[0]?.content?.parts || [];
        const audioPart = parts.find((p: any) => p.inlineData?.mimeType?.startsWith('audio/'));

        if (audioPart) {
          segments.push({
            audio: Buffer.from(audioPart.inlineData.data, 'base64'),
            mimeType: audioPart.inlineData.mimeType,
          });
        }
      } catch (err) {
        // Crypto Sprint 17: Sanitize error output.
        console.error(`[Multimedia] TTS error for segment ${i + 1}:`, err instanceof Error ? err.message : 'Unknown error');
      }

      // Small delay between API calls to avoid rate limits
      if (i < script.length - 1) {
        await new Promise((r) => setTimeout(r, 200));
      }
    }

    return segments;
  }

  private async assembleAudio(
    segments: Array<{ audio: Buffer; mimeType: string }>,
    outputPath: string,
  ): Promise<number> {
    if (segments.length === 0) {
      throw new Error('No audio segments to assemble');
    }

    // Gemini returns audio as PCM (L16) at 24kHz by default
    const sampleRate = 24000;
    const silenceBetweenSpeakers = createSilenceBuffer(300, sampleRate); // 300ms pause

    // Concatenate all PCM segments with inter-speaker pauses
    const pcmBuffers: Buffer[] = [];
    for (let i = 0; i < segments.length; i++) {
      pcmBuffers.push(segments[i].audio);
      if (i < segments.length - 1) {
        pcmBuffers.push(silenceBetweenSpeakers);
      }
    }

    const pcmData = Buffer.concat(pcmBuffers);
    const wavHeader = createWavHeader(pcmData.length, sampleRate, 1, 16);
    const wavBuffer = Buffer.concat([wavHeader, pcmData]);

    await fsAsync.writeFile(outputPath, wavBuffer);

    // Duration in seconds: total samples / sample rate
    const duration = pcmData.length / (sampleRate * 2); // 2 bytes per sample (16-bit)
    return duration;
  }

  // ── Visual Generation ────────────────────────────────────────────

  async generateVisual(request: VisualRequest): Promise<VisualResult> {
    console.log(`[Multimedia] Generating ${request.type} visual (${request.style})...`);

    // Step 1: Generate HTML/SVG via Claude Sonnet
    const htmlContent = await this.generateVisualCode(request);

    // Step 2: Save HTML
    const baseName = `visual-${Date.now()}-${crypto.randomBytes(3).toString('hex')}`;
    const htmlPath = path.join(this.mediaDir, 'visuals', `${baseName}.html`);
    await fsAsync.writeFile(htmlPath, htmlContent, 'utf-8');

    // Step 3: For PNG output, we'd need puppeteer; for HTML, we're done
    const imagePath = htmlPath; // Default to HTML; screenshot capture done via desktop tools

    console.log(`[Multimedia] Visual generated: ${htmlPath}`);

    return {
      imagePath,
      htmlPath,
      title: request.title || `${request.type} — ${new Date().toLocaleDateString()}`,
    };
  }

  private async generateVisualCode(request: VisualRequest): Promise<string> {
    const styleGuide: Record<string, string> = {
      professional: 'Clean corporate design. Navy, white, subtle grays. Helvetica/Inter fonts. Grid-based layout.',
      playful: 'Vibrant colors, rounded shapes, friendly fonts. Fun but readable.',
      minimal: 'Stark minimalism. Black, white, one accent color. Lots of whitespace. Mono fonts.',
      rich: 'Dense information design. Multiple visual elements, color coding, detailed annotations.',
    };

    const response = await llmClient.complete({
      messages: [{
        role: 'user',
        content: `Generate a complete, self-contained HTML file that creates a ${request.type}.

STYLE: ${styleGuide[request.style] || styleGuide.professional}
FORMAT: ${request.format}
${request.title ? `TITLE: ${request.title}` : ''}

The HTML must:
- Be completely self-contained (inline CSS, no external dependencies)
- Use modern CSS (flexbox, grid, gradients)
- Be sized for 1200x900px viewport
- Look polished and professional
- Use SVG for charts/diagrams if applicable
- Include proper data visualization

DATA/CONTENT:
${request.source.slice(0, 20000)}

Return ONLY the complete HTML file, no explanation.`,
      }],
      maxTokens: 8192,
    });

    let html = response.content;

    // Strip markdown code fence if present
    html = html.replace(/^```html?\s*\n?/i, '').replace(/\n?```\s*$/i, '');

    return html;
  }

  // ── Audio Messages ───────────────────────────────────────────────

  async createAudioMessage(request: AudioMessageRequest): Promise<AudioMessageResult> {
    console.log(`[Multimedia] Creating ${request.type} (${request.voice})...`);

    const apiKey = settingsManager.getGeminiApiKey();
    if (!apiKey) throw new Error('Gemini API key not configured');

    const emotionDirection = request.emotion ? `Speak with ${request.emotion} emotion.` : '';

    // Privacy Shield: scrub audio message content before sending to Google cloud TTS.
    const ttsContent = privacyShield.isEnabled()
      ? privacyShield.scrub(request.content).text
      : request.content;

    const response = await fetch(
      'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent',
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-goog-api-key': apiKey,
        },
        body: JSON.stringify({
          system_instruction: {
            parts: [{
              text: `You are composing a ${request.type}. ${emotionDirection} Speak naturally, with genuine feeling. Do not add words beyond what is provided — just speak the text with the right emotion and pacing.`,
            }],
          },
          contents: [{ parts: [{ text: ttsContent }] }],
          generation_config: {
            response_modalities: ['AUDIO'],
            speech_config: {
              voice_config: {
                prebuilt_voice_config: {
                  voice_name: request.voice,
                },
              },
            },
          },
        }),
      },
    );

    if (!response.ok) {
      throw new Error(`Audio message TTS failed: ${response.status}`);
    }

    const data = await response.json();
    const parts = data.candidates?.[0]?.content?.parts || [];
    const audioPart = parts.find((p: any) => p.inlineData?.mimeType?.startsWith('audio/'));

    if (!audioPart) {
      throw new Error('No audio in Gemini response');
    }

    const audioBuffer = Buffer.from(audioPart.inlineData.data, 'base64');
    const sampleRate = 24000;
    const wavHeader = createWavHeader(audioBuffer.length, sampleRate, 1, 16);
    const wavBuffer = Buffer.concat([wavHeader, audioBuffer]);

    const fileName = `${request.type}-${Date.now()}-${crypto.randomBytes(3).toString('hex')}.wav`;
    const audioPath = path.join(this.mediaDir, 'audio', fileName);
    await fsAsync.writeFile(audioPath, wavBuffer);

    const duration = audioBuffer.length / (sampleRate * 2);

    console.log(`[Multimedia] Audio message created: ${duration.toFixed(1)}s`);

    return {
      audioPath,
      duration,
      title: request.title || `${request.type} — ${new Date().toLocaleDateString()}`,
    };
  }

  // ── Music Generation ─────────────────────────────────────────────

  async generateMusic(request: MusicRequest): Promise<MusicResult> {
    console.log(`[Multimedia] Generating ${request.type} music (${request.mood}, ${request.duration}s)...`);

    const apiKey = settingsManager.getGeminiApiKey();
    if (!apiKey) throw new Error('Gemini API key not configured');

    const styleNote = request.style ? ` Style: ${request.style}.` : '';

    // Privacy Shield: scrub music generation prompt (may contain personal context).
    const shieldOn = privacyShield.isEnabled();
    const cleanMood = shieldOn ? privacyShield.scrub(request.mood).text : request.mood;
    const cleanStyle = shieldOn && request.style ? privacyShield.scrub(request.style).text : request.style;
    const cleanStyleNote = cleanStyle ? ` Style: ${cleanStyle}.` : '';

    const response = await fetch(
      'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent',
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-goog-api-key': apiKey,
        },
        body: JSON.stringify({
          system_instruction: {
            parts: [{
              text: `You are a music creator. Create a ${request.type} audio piece. Mood: ${cleanMood}.${cleanStyleNote} Duration: approximately ${request.duration} seconds. Create the audio directly — do not speak or explain, just produce the musical content.`,
            }],
          },
          contents: [{ parts: [{ text: `Create a ${cleanMood} ${request.type} piece, approximately ${request.duration} seconds long.` }] }],
          generation_config: {
            response_modalities: ['AUDIO'],
            speech_config: {
              voice_config: {
                prebuilt_voice_config: {
                  voice_name: 'Enceladus', // Deep, measured — best for musical content
                },
              },
            },
          },
        }),
      },
    );

    if (!response.ok) {
      throw new Error(`Music generation failed: ${response.status}`);
    }

    const data = await response.json();
    const parts = data.candidates?.[0]?.content?.parts || [];
    const audioPart = parts.find((p: any) => p.inlineData?.mimeType?.startsWith('audio/'));

    if (!audioPart) {
      throw new Error('No audio in Gemini music response');
    }

    const audioBuffer = Buffer.from(audioPart.inlineData.data, 'base64');
    const sampleRate = 24000;
    const wavHeader = createWavHeader(audioBuffer.length, sampleRate, 1, 16);
    const wavBuffer = Buffer.concat([wavHeader, audioBuffer]);

    const fileName = `music-${Date.now()}-${crypto.randomBytes(3).toString('hex')}.wav`;
    const audioPath = path.join(this.mediaDir, 'music', fileName);
    await fsAsync.writeFile(audioPath, wavBuffer);

    const duration = audioBuffer.length / (sampleRate * 2);

    console.log(`[Multimedia] Music generated: ${duration.toFixed(1)}s`);

    return {
      audioPath,
      duration,
      title: `${request.mood} ${request.type}`,
    };
  }

  // ── Creative Permissions ─────────────────────────────────────────

  getPermissions(): CreativePermissions {
    return { ...this.permissions };
  }

  async updatePermissions(update: Partial<CreativePermissions>): Promise<CreativePermissions> {
    this.permissions = { ...this.permissions, ...update };
    await settingsManager.setSetting('creativePermissions', this.permissions);
    console.log('[Multimedia] Permissions updated:', this.permissions);
    return this.permissions;
  }

  canCreate(level: 'request' | 'draft' | 'briefing' | 'unprompted' | 'autonomous'): boolean {
    switch (level) {
      case 'request': return true;
      case 'draft': return this.permissions.canCreateDrafts;
      case 'briefing': return this.permissions.canCreateBriefings;
      case 'unprompted': return this.permissions.canCreateUnprompted;
      case 'autonomous': return this.permissions.canCreateAutonomously;
      default: return false;
    }
  }

  // ── Utilities ────────────────────────────────────────────────────

  getMediaDir(): string {
    return this.mediaDir;
  }

  async listMedia(type: 'podcasts' | 'visuals' | 'audio' | 'music'): Promise<string[]> {
    const dir = path.join(this.mediaDir, type);
    try {
      const files = await fsAsync.readdir(dir);
      return files.map((f) => path.join(dir, f));
    } catch {
      return [];
    }
  }

  getSpeakerPresets(): Record<string, PodcastSpeaker[]> {
    return { ...SPEAKER_PRESETS };
  }
}

export const multimediaEngine = new MultimediaEngine();
