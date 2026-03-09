/**
 * voice-audition.ts — Generate short voice samples using Gemini REST API.
 *
 * During onboarding customization, the setup voice (Charon) stays connected
 * via the Live WebSocket. Voice samples are generated through separate REST
 * API calls so we never have to disconnect/reconnect the live session.
 *
 * Uses Gemini 2.0 Flash with response_modalities: ["AUDIO"] and
 * speechConfig.voiceConfig.prebuiltVoiceConfig to select specific voices.
 */

import { settingsManager } from './settings';
import { privacyShield } from './privacy-shield';

/** All available Gemini TTS voices */
export const GEMINI_VOICES = [
  'Kore', 'Puck', 'Charon', 'Fenrir', 'Leda', 'Orus', 'Zephyr', 'Aoede',
  'Sulafat', 'Achird', 'Gacrux', 'Achernar', 'Sadachbia', 'Vindemiatrix',
  'Sadaltager', 'Schedar', 'Alnilam', 'Algieba', 'Despina', 'Erinome',
  'Algenib', 'Rasalgethi', 'Laomedeia', 'Pulcherrima', 'Zubenelgenubi',
  'Umbriel', 'Callirrhoe', 'Autonoe', 'Enceladus', 'Iapetus',
] as const;

export type GeminiVoiceName = (typeof GEMINI_VOICES)[number];

/** Voice metadata for the onboarding flow */
export interface VoiceInfo {
  name: GeminiVoiceName;
  gender: 'female' | 'male' | 'neutral';
  description: string;
}

/** Curated voice list shown during onboarding (subset with descriptions) */
export const VOICE_CATALOG: VoiceInfo[] = [
  // Female voices
  { name: 'Kore', gender: 'female', description: 'Confident and sharp — a clear, authoritative presence' },
  { name: 'Leda', gender: 'female', description: 'Bright and warm — enthusiastic with natural energy' },
  { name: 'Aoede', gender: 'female', description: 'Relaxed and cool — calm with a smooth cadence' },
  { name: 'Achernar', gender: 'female', description: 'Gentle and calm — soft-spoken and thoughtful' },
  { name: 'Despina', gender: 'female', description: 'Clear and expressive — animated and engaging' },
  { name: 'Erinome', gender: 'female', description: 'Soft and thoughtful — introspective and measured' },
  // Male voices
  { name: 'Puck', gender: 'male', description: 'Energetic and charismatic — quick-witted and lively' },
  { name: 'Charon', gender: 'male', description: 'Steady and composed — measured and authoritative' },
  { name: 'Orus', gender: 'male', description: 'Grounded and authoritative — deep and resonant' },
  { name: 'Fenrir', gender: 'male', description: 'Dynamic and expressive — versatile and engaging' },
  { name: 'Enceladus', gender: 'male', description: 'Warm and measured — calm with quiet confidence' },
  { name: 'Iapetus', gender: 'male', description: 'Deep and resonant — commanding and rich-toned' },
  // Neutral voices
  { name: 'Zephyr', gender: 'neutral', description: 'Clear and bright — balanced and approachable' },
  { name: 'Achird', gender: 'neutral', description: 'Warm and approachable — friendly and easy-going' },
  { name: 'Sulafat', gender: 'neutral', description: 'Mellow and gentle — soothing and unhurried' },
];

/** The sample phrase each voice speaks during audition */
const AUDITION_PHRASE =
  "Hello — I'm here whenever you need me. Just say the word and we'll figure it out together.";

/**
 * Generate a voice sample using the Gemini REST API.
 * Returns base64-encoded audio data and its MIME type.
 */
export async function generateVoiceSample(
  voiceName: GeminiVoiceName,
  customPhrase?: string,
): Promise<{ audio: string; mimeType: string } | null> {
  const apiKey = settingsManager.getGeminiApiKey();
  if (!apiKey) {
    console.warn('[VoiceAudition] No Gemini API key — cannot generate sample');
    return null;
  }

  const phrase = customPhrase || AUDITION_PHRASE;

  // Privacy Shield: scrub custom phrases before sending to Google cloud TTS.
  const ttsPhrase = privacyShield.isEnabled()
    ? privacyShield.scrub(phrase).text
    : phrase;

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
              text: 'You are a voice assistant. Speak the user\'s text aloud naturally and expressively. Do not add, remove, or change any words. Just speak exactly what is provided.',
            }],
          },
          contents: [{ parts: [{ text: ttsPhrase }] }],
          generation_config: {
            response_modalities: ['AUDIO'],
            speech_config: {
              voice_config: {
                prebuilt_voice_config: {
                  voice_name: voiceName,
                },
              },
            },
          },
        }),
      },
    );

    if (!response.ok) {
      const errorText = await response.text();
      console.error(`[VoiceAudition] API error (${response.status}):`, errorText.slice(0, 300));
      return null;
    }

    const data = await response.json();
    const parts = data.candidates?.[0]?.content?.parts || [];
    const audioPart = parts.find((p: any) => p.inlineData?.mimeType?.startsWith('audio/'));

    if (audioPart) {
      console.log(`[VoiceAudition] Generated sample for ${voiceName} (${audioPart.inlineData.mimeType})`);
      return {
        audio: audioPart.inlineData.data,
        mimeType: audioPart.inlineData.mimeType,
      };
    }

    console.warn('[VoiceAudition] No audio in response for', voiceName);
    return null;
  } catch (err) {
    // Crypto Sprint 13: Sanitize — API key is in scope; raw err could leak it.
    console.error('[VoiceAudition] Failed to generate sample:', err instanceof Error ? err.message : 'Unknown error');
    return null;
  }
}

/**
 * Get voice recommendations based on gender preference.
 * Returns a curated list filtered by gender.
 */
export function getVoiceRecommendations(genderPref: string): VoiceInfo[] {
  const lower = genderPref.toLowerCase();

  if (lower.includes('female') || lower.includes('woman') || lower.includes('her') || lower.includes('she')) {
    return VOICE_CATALOG.filter((v) => v.gender === 'female');
  }
  if (lower.includes('male') || lower.includes('man') || lower.includes('him') || lower.includes('his') || lower.includes('he')) {
    return VOICE_CATALOG.filter((v) => v.gender === 'male');
  }
  // No strong preference — show a mix
  return VOICE_CATALOG.filter((v) =>
    ['Zephyr', 'Achird', 'Sulafat', 'Puck', 'Kore', 'Aoede'].includes(v.name),
  );
}
