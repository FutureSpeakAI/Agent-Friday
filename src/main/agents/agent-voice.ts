/**
 * agent-voice.ts — ElevenLabs TTS service for multi-agent voice synthesis.
 *
 * Provides text-to-speech synthesis using ElevenLabs API.
 * Each sub-agent persona gets a unique voice, making them
 * distinguishable from Friday (who uses Gemini Live native audio).
 */

import { settingsManager } from '../settings';

export interface VoiceSynthResult {
  audioBuffer: Buffer;
  contentType: string;
  durationEstimate: number; // rough estimate in seconds
}

class AgentVoiceService {
  /**
   * Synthesize text to speech using ElevenLabs API.
   * Returns an MP3 audio buffer ready for playback.
   */
  async speak(text: string, voiceId: string): Promise<VoiceSynthResult> {
    const apiKey = settingsManager.getElevenLabsApiKey();
    if (!apiKey) {
      throw new Error('ElevenLabs API key not configured — add it in Settings');
    }

    if (!text || text.trim().length === 0) {
      throw new Error('No text provided for speech synthesis');
    }

    // Truncate very long texts to avoid API limits / excessive cost
    const maxChars = 5000;
    const truncatedText = text.length > maxChars
      ? text.slice(0, maxChars) + '... (truncated for voice delivery)'
      : text;

    const response = await fetch(
      `https://api.elevenlabs.io/v1/text-to-speech/${voiceId}`,
      {
        method: 'POST',
        headers: {
          'xi-api-key': apiKey,
          'Content-Type': 'application/json',
          Accept: 'audio/mpeg',
        },
        body: JSON.stringify({
          text: truncatedText,
          model_id: 'eleven_turbo_v2_5',
          voice_settings: {
            stability: 0.5,
            similarity_boost: 0.75,
            style: 0.0,
            use_speaker_boost: true,
          },
        }),
      }
    );

    if (!response.ok) {
      const errorBody = await response.text().catch(() => 'Unknown error');
      throw new Error(
        `ElevenLabs TTS failed (${response.status}): ${errorBody}`
      );
    }

    const arrayBuffer = await response.arrayBuffer();
    const audioBuffer = Buffer.from(arrayBuffer);

    // Rough duration estimate: MP3 at ~128kbps → bytes / (128000/8) = seconds
    const durationEstimate = audioBuffer.length / 16000;

    return {
      audioBuffer,
      contentType: 'audio/mpeg',
      durationEstimate,
    };
  }

  /**
   * Check if ElevenLabs is configured and ready.
   */
  isAvailable(): boolean {
    return !!settingsManager.getElevenLabsApiKey();
  }
}

export const agentVoice = new AgentVoiceService();
