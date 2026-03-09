/**
 * agent-voice.ts — Multi-backend TTS service for sub-agent voice synthesis.
 *
 * Provides text-to-speech synthesis using a fallback chain:
 *   1. Gemini TTS (cloud) — uses the same Gemini API key as the main LLM
 *   2. Local TTS (Kokoro/Piper) — fully offline, no API key needed
 *   3. null — graceful degradation to text-only delivery
 *
 * Provider order is determined by the user's preferred provider setting:
 *   - 'local' / 'ollama' → try local first, then gemini
 *   - anything else → try gemini first, then local
 *
 * Each sub-agent persona specifies voice IDs per provider via VoiceMapping.
 * Friday (the conductor) uses Gemini Live native audio — this service is
 * only for sub-agent personas (Atlas, Nova, Cipher).
 */

import { settingsManager } from '../settings';
import { ttsEngine } from '../voice/tts-engine';
import { buildWavBuffer } from '../voice/tts-binding';

// ── Types ────────────────────────────────────────────────────────────────────

export interface VoiceSynthResult {
  audioBuffer: Buffer;
  contentType: string;
  durationEstimate: number; // rough estimate in seconds
}

/** Per-provider voice IDs for a single persona. */
export interface VoiceMapping {
  geminiVoice?: string;   // Gemini prebuilt voice name (e.g. 'Iapetus')
  localVoiceId?: string;  // Kokoro/Piper model voice ID
}

type TTSProvider = 'gemini' | 'local';

// ── Constants ────────────────────────────────────────────────────────────────

const GEMINI_TTS_URL =
  'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent';

const MAX_TEXT_CHARS = 5000;

const SAMPLE_RATE = 24_000;

// ── Service ──────────────────────────────────────────────────────────────────

class AgentVoiceService {
  private localEngineLoaded = false;
  private localEngineAttempted = false;

  /**
   * Synthesize text to speech using the best available provider.
   * Returns a WAV audio buffer ready for playback, or null if no
   * TTS provider is available (graceful degradation to text-only).
   */
  async speak(text: string, voices: VoiceMapping): Promise<VoiceSynthResult | null> {
    if (!text || text.trim().length === 0) {
      throw new Error('No text provided for speech synthesis');
    }

    const truncated = text.length > MAX_TEXT_CHARS
      ? text.slice(0, MAX_TEXT_CHARS) + '... (truncated for voice delivery)'
      : text;

    const chain = this.getProviderChain();

    for (const provider of chain) {
      try {
        const result = await this.tryProvider(provider, truncated, voices);
        if (result) return result;
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        console.warn(`[AgentVoice] ${provider} provider failed: ${msg}`);
        // Fall through to next provider
      }
    }

    console.log('[AgentVoice] No TTS provider available — degrading to text-only');
    return null;
  }

  /**
   * Check if any TTS provider is potentially available.
   */
  isAvailable(): boolean {
    return this.isGeminiAvailable() || this.isLocalAvailable();
  }

  // ── Provider chain ────────────────────────────────────────────────────────

  private getProviderChain(): TTSProvider[] {
    const pref = settingsManager.getPreferredProvider();
    if (pref === 'local' || pref === 'ollama') {
      return ['local', 'gemini'];
    }
    return ['gemini', 'local'];
  }

  private async tryProvider(
    provider: TTSProvider,
    text: string,
    voices: VoiceMapping,
  ): Promise<VoiceSynthResult | null> {
    switch (provider) {
      case 'gemini':
        return this.speakGemini(text, voices.geminiVoice);
      case 'local':
        return this.speakLocal(text, voices.localVoiceId);
      default:
        return null;
    }
  }

  // ── Gemini TTS ────────────────────────────────────────────────────────────

  private isGeminiAvailable(): boolean {
    return !!settingsManager.getGeminiApiKey();
  }

  private async speakGemini(
    text: string,
    voiceName?: string,
  ): Promise<VoiceSynthResult | null> {
    const apiKey = settingsManager.getGeminiApiKey();
    if (!apiKey) return null;

    const voice = voiceName || 'Puck';

    const response = await fetch(`${GEMINI_TTS_URL}?key=${apiKey}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        contents: [{ parts: [{ text: `Say the following aloud:\n\n${text}` }] }],
        generation_config: {
          response_modalities: ['AUDIO'],
          speech_config: {
            voice_config: {
              prebuilt_voice_config: { voice_name: voice },
            },
          },
        },
      }),
    });

    if (!response.ok) {
      const errBody = await response.text().catch(() => 'Unknown error');
      throw new Error(`Gemini TTS failed (${response.status}): ${errBody}`);
    }

    const json = await response.json();
    const candidates = json.candidates;
    if (!candidates?.length) {
      throw new Error('Gemini TTS returned no candidates');
    }

    const parts = candidates[0].content?.parts;
    if (!parts?.length) {
      throw new Error('Gemini TTS returned no audio parts');
    }

    const audioPart = parts.find(
      (p: any) => p.inlineData?.mimeType?.startsWith('audio/'),
    );
    if (!audioPart) {
      throw new Error('Gemini TTS returned no audio data');
    }

    // Gemini returns raw PCM: 16-bit signed LE, 24kHz mono
    const pcmBase64: string = audioPart.inlineData.data;
    const pcmBuffer = Buffer.from(pcmBase64, 'base64');

    // Wrap in WAV header for Web Audio decodeAudioData()
    const wavBuffer = this.wrapPcmInWav(pcmBuffer, SAMPLE_RATE);

    // Duration: pcmBytes / (sampleRate * bytesPerSample)
    const durationEstimate = pcmBuffer.length / (SAMPLE_RATE * 2);

    return {
      audioBuffer: wavBuffer,
      contentType: 'audio/wav',
      durationEstimate,
    };
  }

  // ── Local TTS (Kokoro / Piper) ────────────────────────────────────────────

  private isLocalAvailable(): boolean {
    return ttsEngine.isReady() || !this.localEngineAttempted;
  }

  private async speakLocal(
    text: string,
    voiceId?: string,
  ): Promise<VoiceSynthResult | null> {
    // Attempt to load the engine once if not yet tried
    if (!ttsEngine.isReady() && !this.localEngineAttempted) {
      this.localEngineAttempted = true;
      try {
        await ttsEngine.loadEngine();
        this.localEngineLoaded = true;
      } catch (err) {
        console.log('[AgentVoice] Local TTS engine not available:', err);
        return null;
      }
    }

    if (!ttsEngine.isReady()) return null;

    const pcmFloat32 = await ttsEngine.synthesize(text, {
      voiceId: voiceId || undefined,
    });

    if (pcmFloat32.length === 0) return null;

    // Convert Float32Array PCM to 16-bit WAV buffer
    const wavBuffer = buildWavBuffer(pcmFloat32, SAMPLE_RATE);

    const durationEstimate = pcmFloat32.length / SAMPLE_RATE;

    return {
      audioBuffer: wavBuffer,
      contentType: 'audio/wav',
      durationEstimate,
    };
  }

  // ── WAV Utilities ─────────────────────────────────────────────────────────

  /**
   * Wrap raw 16-bit signed LE PCM in a WAV header.
   * Used for Gemini output which arrives as raw PCM bytes.
   */
  private wrapPcmInWav(pcmBuffer: Buffer, sampleRate: number): Buffer {
    const numChannels = 1;
    const bitsPerSample = 16;
    const byteRate = sampleRate * numChannels * (bitsPerSample / 8);
    const blockAlign = numChannels * (bitsPerSample / 8);
    const dataSize = pcmBuffer.length;

    const header = Buffer.alloc(44);
    // RIFF header
    header.write('RIFF', 0);
    header.writeUInt32LE(36 + dataSize, 4);
    header.write('WAVE', 8);
    // fmt subchunk
    header.write('fmt ', 12);
    header.writeUInt32LE(16, 16);       // subchunk1 size
    header.writeUInt16LE(1, 20);        // PCM format
    header.writeUInt16LE(numChannels, 22);
    header.writeUInt32LE(sampleRate, 24);
    header.writeUInt32LE(byteRate, 28);
    header.writeUInt16LE(blockAlign, 30);
    header.writeUInt16LE(bitsPerSample, 32);
    // data subchunk
    header.write('data', 36);
    header.writeUInt32LE(dataSize, 40);

    return Buffer.concat([header, pcmBuffer]);
  }
}

export const agentVoice = new AgentVoiceService();
