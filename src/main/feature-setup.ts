/**
 * feature-setup.ts — Guided post-onboarding feature walkthrough.
 *
 * After the agent is created and speaks for the first time, it walks
 * the user through setting up each feature of the system. Steps can
 * only be skipped if the user explicitly says "skip."
 *
 * The agent explains each feature conversationally, helps configure it,
 * and only moves on when the user completes or explicitly skips.
 */

import type { FeatureSetupStep, FeatureSetupState } from './settings';
import { settingsManager } from './settings';

/** All feature setup steps in order */
const ALL_STEPS: FeatureSetupStep[] = [
  'obsidian',
  'browser',
  'calendar',
  'email',
  'screen-capture',
  'world-monitor',
  'intelligence',
  'research',
  'ai-services',
  'voice',
  'gateway',
  'scheduler',
];

/**
 * Initializes the feature setup state (called when entering feature-setup phase).
 */
export function initializeFeatureSetup(): FeatureSetupState {
  const state: FeatureSetupState = {
    currentStep: 0,
    steps: ALL_STEPS.map((id) => ({ id, status: 'pending' })),
  };
  return state;
}

/**
 * Advances to the next step after completing or skipping the current one.
 */
export async function advanceFeatureStep(
  stepId: FeatureSetupStep,
  action: 'complete' | 'skip'
): Promise<FeatureSetupState | null> {
  const settings = settingsManager.get();
  const state = settings.featureSetupState;

  if (!state) {
    console.warn('[FeatureSetup] No active setup state');
    return null;
  }

  const stepIndex = state.steps.findIndex((s) => s.id === stepId);
  if (stepIndex === -1) {
    console.warn(`[FeatureSetup] Unknown step: ${stepId}`);
    return state;
  }

  state.steps[stepIndex].status = action === 'complete' ? 'completed' : 'skipped';
  state.currentStep = stepIndex + 1;

  console.log(
    `[FeatureSetup] Step "${stepId}" ${action}ed. ` +
    `Progress: ${state.currentStep}/${state.steps.length}`
  );

  // Check if all steps are done
  if (state.currentStep >= state.steps.length) {
    await settingsManager.setSetting('featureSetupComplete', true);
    console.log('[FeatureSetup] All steps complete!');
  }

  await settingsManager.setSetting('featureSetupState', state);
  return state;
}

/**
 * Returns true when all feature setup steps are completed or skipped.
 */
export function isFeatureSetupComplete(): boolean {
  return settingsManager.get().featureSetupComplete;
}

/**
 * Gets the current feature setup state.
 */
export function getFeatureSetupState(): FeatureSetupState | null {
  return settingsManager.get().featureSetupState;
}

/**
 * Gets the current step that needs attention.
 */
export function getCurrentStep(): FeatureSetupStep | null {
  const state = settingsManager.get().featureSetupState;
  if (!state || state.currentStep >= state.steps.length) return null;
  return state.steps[state.currentStep].id;
}

/**
 * Builds the Gemini tool declaration for mark_feature_setup_step.
 */
export function buildFeatureSetupToolDeclaration(): {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
} {
  return {
    name: 'mark_feature_setup_step',
    description:
      'Call this when the user has completed setting up a feature OR explicitly asked to skip it. ' +
      'Only mark as "skip" if the user explicitly says they want to skip — do not skip on their behalf.',
    parameters: {
      type: 'object',
      properties: {
        step: {
          type: 'string',
          enum: ALL_STEPS,
          description: 'The feature setup step being completed or skipped',
        },
        action: {
          type: 'string',
          enum: ['complete', 'skip'],
          description: '"complete" if they set it up, "skip" ONLY if they explicitly asked to skip',
        },
      },
      required: ['step', 'action'],
    },
  };
}

/**
 * Builds a prompt for the agent to guide the user through a specific feature.
 */
export function buildFeatureSetupPrompt(step: FeatureSetupStep): string {
  const config = settingsManager.getAgentConfig();
  const userName = config.userName || 'there';

  const prompts: Record<FeatureSetupStep, string> = {
    obsidian: `[FEATURE SETUP — Obsidian Knowledge Base]
Explain to ${userName} that you can connect to their Obsidian vault to read and search their notes, giving you deep context about their knowledge and projects. Ask if they use Obsidian and if they'd like to connect their vault. If yes, guide them to provide the vault path. If they don't use Obsidian, explain that's fine and ask if they'd like to skip this step. Only call mark_feature_setup_step with action "skip" if they explicitly say to skip.`,

    browser: `[FEATURE SETUP — Browser Integration]
Explain to ${userName} that you can browse the web, search for information, read articles, and interact with web pages on their behalf using the browser extension. This is already available — just let them know the capability exists and ask if they have any questions. Then mark this step as complete.`,

    calendar: `[FEATURE SETUP — Google Calendar]
Explain to ${userName} that you can connect to their Google Calendar to check their schedule, create events, and give them proactive reminders about upcoming meetings. Ask if they'd like to connect their Google Calendar. If yes, guide them through the OAuth flow. If no, only skip if they explicitly say so.`,

    email: `[FEATURE SETUP — Gmail Integration]
Explain to ${userName} that you can help manage their email — reading, drafting, and organizing messages. This requires connecting their Gmail account. Ask if they'd like to set this up. If yes, guide them through it. If no, only skip if they explicitly say so.`,

    'screen-capture': `[FEATURE SETUP — Screen Awareness]
Explain to ${userName} that you can periodically capture what's on their screen to stay aware of what they're working on. This helps you provide contextual assistance without them having to explain everything. It's private — the captures stay local. Ask if they'd like to enable this. It's currently ${settingsManager.get().autoScreenCapture ? 'enabled' : 'disabled'}.`,

    'world-monitor': `[FEATURE SETUP — World Monitor]
Explain to ${userName} that you have a world monitor that tracks news, weather, and information relevant to them. Ask if they'd like to configure what topics and locations matter to them. Guide them through setting preferences.`,

    intelligence: `[FEATURE SETUP — Background Intelligence]
Explain to ${userName} that you can run background research on topics they care about — industry news, project-relevant updates, personal interests. Ask what topics they'd like you to keep an eye on. Save their preferences.`,

    research: `[FEATURE SETUP — Web Research & Search (Perplexity + Firecrawl)]
Explain to ${userName} that you have powerful web intelligence capabilities. You can search the internet in real-time, read and extract content from any webpage, and conduct deep multi-step research investigations — all with full source citations.

You have two research engines:
- **Perplexity AI** — AI-powered search that understands questions and synthesizes answers from across the web, with four tiers from quick search to deep investigation
- **Firecrawl** — Web scraping and crawling for reading full articles, documentation sites, and extracting structured data

Ask if they have a Perplexity API key. If they do, help them enter it. If they have a Firecrawl key, help with that too. ${settingsManager.get().perplexityApiKey ? 'Perplexity is already configured.' : 'Perplexity is not yet configured.'} ${settingsManager.get().firecrawlApiKey ? 'Firecrawl is already configured.' : 'Firecrawl is not yet configured.'} If both are already set up, let them know and mark as complete. Even without these keys, you still have web capabilities through other means — but these make you significantly more powerful.`,

    'ai-services': `[FEATURE SETUP — AI Services (OpenAI)]
Explain to ${userName} that you can generate images, perform deep mathematical and logical reasoning, and transcribe audio files using OpenAI's specialist models:

- **DALL-E 3** — Create images from descriptions (artwork, diagrams, mockups, illustrations)
- **o3 Reasoning** — Deep multi-step reasoning for complex analytical problems
- **Whisper** — Transcribe audio files (meetings, voice notes, podcasts) to text
- **Embeddings** — Semantic understanding for smarter memory search

Ask if they have an OpenAI API key. If yes, help them enter it. ${settingsManager.get().openaiApiKey ? 'OpenAI is already configured.' : 'OpenAI is not yet configured.'} These are optional but unlock powerful creative and analytical capabilities.`,

    voice: `[FEATURE SETUP — Voice (ElevenLabs)]
Explain to ${userName} that for the highest quality voice experience, you can use ElevenLabs for text-to-speech. This gives you a more natural, expressive voice compared to the built-in speech synthesis. Ask if they have an ElevenLabs API key. If yes, help them enter it. ${settingsManager.get().elevenLabsApiKey ? 'ElevenLabs is already configured — voice is active.' : 'ElevenLabs is not yet configured. The built-in Gemini voice works fine, but ElevenLabs sounds more natural.'}`,

    gateway: `[FEATURE SETUP — Messaging Gateway]
Explain to ${userName} that you can be reached through messaging apps like Telegram, so they can talk to you even when they're away from their computer. Ask if they'd like to set up Telegram or other messaging channels. If yes, guide them through providing their bot token.`,

    scheduler: `[FEATURE SETUP — Scheduled Tasks]
Explain to ${userName} that you can run scheduled tasks — morning briefings, periodic research, reminders, or any recurring task they need. Ask if they'd like to set up any recurring tasks right now, or if they'd prefer to do this later. This is the last setup step — once done, you're fully configured and ready to go.`,
  };

  return prompts[step] || `Guide ${userName} through setting up: ${step}`;
}
