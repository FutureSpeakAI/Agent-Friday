/**
 * psychological-profile.ts — Generates a structured psychological profile
 * from the user's onboarding intake responses using Claude Sonnet.
 *
 * The "Her"-inspired intake asks three questions:
 *   1. Voice gender preference
 *   2. How they describe themselves in social situations
 *   3. "Tell me about your relationship with your mother"
 *
 * The mother question is the key: HOW they answer (or deflect) reveals
 * attachment style, emotional availability, trust patterns, and what
 * they need from an AI companion.
 */

import { llmClient } from './llm-client';
import type { PsychologicalProfile, IntakeResponses } from './settings';
import { settingsManager } from './settings';

const ANALYSIS_PROMPT = `You are a psychological profiling system. You are analyzing responses from a brief intake interview for an AI companion app. Your job is to understand WHO this person is — not just what they said, but what they revealed about themselves through how they said it.

You will receive three responses:
1. Their voice gender preference for their AI agent
2. How they describe themselves in social situations
3. Their description of their relationship with their mother

The third question is the most important. It's designed to reveal attachment style, emotional openness, trust patterns, and defense mechanisms. Pay attention to:
- Did they answer directly or deflect?
- Did they use humor to avoid depth?
- How much emotional detail did they offer?
- What did they NOT say?
- If they refused or deflected, that itself is profoundly informative

Cross-reference all three answers for consistency. Someone who claims to be social but deflects the mother question may be performatively open but actually guarded.

Return a JSON object with these exact fields:
{
  "openness": <0-1 float — how freely they share personal information>,
  "trustReadiness": <0-1 float — how quickly they'll trust an AI companion>,
  "emotionalDepth": <0-1 float — depth of emotional engagement they showed>,
  "humorAsArmor": <boolean — do they use humor to deflect from vulnerability?>,
  "guardedness": <0-1 float — how guarded or defensive they are>,
  "connectionStyle": <"warm" | "intellectual" | "playful" | "reserved">,
  "needsFromAI": <string — what they likely need from an AI companion, 1-2 sentences>,
  "approachStrategy": <string — how the agent should approach this person to build trust, 2-3 sentences>,
  "motherRelationshipInsight": <string — the key psychological insight from the mother question, 1-2 sentences>,
  "rawAnalysis": <string — your full analysis, 3-5 sentences covering all three responses and what they reveal together>
}

Return ONLY the JSON object. No markdown fencing, no explanation.`;

/**
 * Generates a psychological profile from the user's intake responses.
 * Uses Claude Sonnet for nuanced psychological analysis.
 */
export async function generatePsychologicalProfile(
  responses: IntakeResponses
): Promise<PsychologicalProfile> {
  const userMessage = `Here are the intake responses to analyze:

1. Voice preference: "${responses.voicePreference}"

2. Social self-description: "${responses.socialDescription}"

3. Relationship with mother: "${responses.motherRelationship || '[User deflected or refused to answer]'}"`;

  console.log('[PsychProfile] Generating psychological profile from intake responses...');

  const text = await llmClient.text(userMessage, { systemPrompt: ANALYSIS_PROMPT, maxTokens: 1024 });

  try {
    const profile: PsychologicalProfile = JSON.parse(text);

    // Validate required fields exist
    const required = [
      'openness', 'trustReadiness', 'emotionalDepth', 'humorAsArmor',
      'guardedness', 'connectionStyle', 'needsFromAI', 'approachStrategy',
      'motherRelationshipInsight', 'rawAnalysis',
    ];
    for (const field of required) {
      if (!(field in profile)) {
        throw new Error(`Missing field: ${field}`);
      }
    }

    // Clamp numeric values to 0-1
    profile.openness = Math.max(0, Math.min(1, profile.openness));
    profile.trustReadiness = Math.max(0, Math.min(1, profile.trustReadiness));
    profile.emotionalDepth = Math.max(0, Math.min(1, profile.emotionalDepth));
    profile.guardedness = Math.max(0, Math.min(1, profile.guardedness));

    console.log(
      `[PsychProfile] Profile generated: connectionStyle=${profile.connectionStyle}, ` +
      `openness=${profile.openness.toFixed(2)}, guardedness=${profile.guardedness.toFixed(2)}`
    );

    return profile;
  } catch (parseErr) {
    console.error('[PsychProfile] Failed to parse Claude response:', text);
    throw new Error(`Psychological profile parse error: ${parseErr}`);
  }
}
