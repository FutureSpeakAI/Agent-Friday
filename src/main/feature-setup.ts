/**
 * feature-setup.ts — Opportunity-based capability discovery.
 *
 * REDESIGNED: No more 12-step sequential checklist. Instead:
 * - The agent knows what's configured and what isn't (via self-knowledge)
 * - Setup tools remain available at all times (not just during a "phase")
 * - The agent discovers configuration opportunities through natural conversation
 * - When the user mentions calendars → agent offers to connect Google Calendar
 * - When the user asks about research → agent notices if Firecrawl/Perplexity aren't set up
 * - Setup happens organically, not as a forced walkthrough
 *
 * The old state tracking is kept for backward compatibility but simplified.
 * featureSetupComplete is set to true immediately after onboarding —
 * there's no "feature-setup phase" anymore.
 */

import type { FeatureSetupStep, FeatureSetupState } from './settings';
import { settingsManager } from './settings';

/** All feature setup steps — kept for type compatibility */
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
 * Initializes the feature setup state.
 * In the new model, everything starts as "pending" but the agent doesn't walk
 * through them sequentially. They're configured opportunistically.
 */
export function initializeFeatureSetup(): FeatureSetupState {
  const state: FeatureSetupState = {
    currentStep: 0,
    steps: ALL_STEPS.map((id) => ({ id, status: 'pending' })),
  };
  return state;
}

/**
 * Marks a feature step as completed or skipped.
 * In the new model, this is called whenever the agent helps configure something,
 * not as part of a sequential walkthrough.
 */
export async function advanceFeatureStep(
  stepId: FeatureSetupStep,
  action: 'complete' | 'skip'
): Promise<FeatureSetupState | null> {
  const settings = settingsManager.get();
  let state = settings.featureSetupState;

  if (!state) {
    // Auto-initialize if missing
    state = initializeFeatureSetup();
  }

  const stepIndex = state.steps.findIndex((s) => s.id === stepId);
  if (stepIndex === -1) {
    console.warn(`[FeatureSetup] Unknown step: ${stepId}`);
    return state;
  }

  state.steps[stepIndex].status = action === 'complete' ? 'completed' : 'skipped';

  const completedCount = state.steps.filter(
    (s) => s.status === 'completed' || s.status === 'skipped'
  ).length;
  state.currentStep = completedCount;

  console.log(
    `[FeatureSetup] "${stepId}" ${action}ed. ` +
    `Configured: ${completedCount}/${state.steps.length}`
  );

  await settingsManager.setSetting('featureSetupState', state);

  // Mark complete if all steps are done (backward compatibility)
  if (completedCount >= state.steps.length) {
    await settingsManager.setSetting('featureSetupComplete', true);
    console.log('[FeatureSetup] All features configured!');
  }

  return state;
}

/**
 * Returns true when all feature setup steps are completed or skipped.
 * In the new model, this is always true after onboarding — the "phase" is eliminated.
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
 * Gets the next unconfigured step (if any).
 * In the new model, this is informational — the agent doesn't force the user through it.
 */
export function getCurrentStep(): FeatureSetupStep | null {
  const state = settingsManager.get().featureSetupState;
  if (!state) return null;
  const pending = state.steps.find((s) => s.status === 'pending');
  return pending ? pending.id : null;
}

/**
 * Gets a list of features that haven't been configured yet.
 * Useful for the agent to know what it could offer to set up.
 */
export function getUnconfiguredFeatures(): FeatureSetupStep[] {
  const state = settingsManager.get().featureSetupState;
  if (!state) return ALL_STEPS;
  return state.steps
    .filter((s) => s.status === 'pending')
    .map((s) => s.id);
}

/**
 * Builds the Gemini tool declaration for mark_feature_setup_step.
 * Still available — the agent calls this when it helps configure something.
 */
export function buildFeatureSetupToolDeclaration(): {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
} {
  return {
    name: 'mark_feature_setup_step',
    description:
      'Record that a feature has been configured. Call this after successfully setting up ' +
      'a capability (calendar connected, API key saved, etc.) or when the user declines to configure something.',
    parameters: {
      type: 'object',
      properties: {
        step: {
          type: 'string',
          enum: ALL_STEPS,
          description: 'The feature that was configured or declined',
        },
        action: {
          type: 'string',
          enum: ['complete', 'skip'],
          description: '"complete" if configured successfully, "skip" if user declined',
        },
      },
      required: ['step', 'action'],
    },
  };
}

/**
 * Builds ALL feature setup tool declarations including helper tools.
 * These are available at ALL times (not just during a setup phase),
 * so the agent can configure things opportunistically during conversation.
 */
export function buildAllFeatureSetupToolDeclarations(): Array<{
  name: string;
  description: string;
  parameters: Record<string, unknown>;
}> {
  return [
    buildFeatureSetupToolDeclaration(),
    {
      name: 'start_calendar_auth',
      description:
        'Start the Google Calendar OAuth authentication flow. ' +
        'Opens a browser window for the user to sign into their Google account and grant calendar access.',
      parameters: {
        type: 'object',
        properties: {},
        required: [],
      },
    },
    {
      name: 'save_api_key',
      description:
        'Save an API key for one of the supported services. ' +
        'Call this when the user provides an API key, during any conversation.',
      parameters: {
        type: 'object',
        properties: {
          service: {
            type: 'string',
            enum: ['perplexity', 'firecrawl', 'openai', 'elevenlabs'],
            description: 'Which service the API key belongs to',
          },
          api_key: {
            type: 'string',
            description: 'The API key string provided by the user',
          },
        },
        required: ['service', 'api_key'],
      },
    },
    {
      name: 'set_obsidian_vault_path',
      description:
        'Set the path to the user\'s Obsidian vault directory.',
      parameters: {
        type: 'object',
        properties: {
          vault_path: {
            type: 'string',
            description: 'Absolute path to the Obsidian vault directory',
          },
        },
        required: ['vault_path'],
      },
    },
    {
      name: 'toggle_screen_capture',
      description:
        'Enable or disable automatic screen capture for screen awareness.',
      parameters: {
        type: 'object',
        properties: {
          enabled: {
            type: 'boolean',
            description: 'true to enable screen capture, false to disable',
          },
        },
        required: ['enabled'],
      },
    },
  ];
}

/**
 * Builds a context-aware setup prompt for a specific feature.
 * Used when the agent proactively offers to configure something.
 */
export function buildFeatureSetupPrompt(step: FeatureSetupStep): string {
  const config = settingsManager.getAgentConfig();
  const userName = config.userName || 'there';
  const settings = settingsManager.get();

  const prompts: Record<FeatureSetupStep, string> = {
    obsidian: `I can connect to your Obsidian vault to read and search your notes. If you use Obsidian, just tell me the path to your vault folder and I'll set it up.`,

    browser: `Browser integration is already active — I can browse the web, read pages, and search for information whenever you need.`,

    calendar: `I can connect to your Google Calendar to check your schedule, create events, and prep you for meetings. Want me to start the connection? I'll open a browser window for you to sign in.`,

    email: `I can draft emails and messages in your voice. When I draft something, it goes straight to your clipboard. No account connection needed — just ask me to "draft an email to..." anytime.`,

    'screen-capture': `Screen awareness lets me see what you're working on so I can offer contextual help. It's currently ${settings.autoScreenCapture ? 'enabled' : 'disabled'}. Everything stays local on your machine. Want me to ${settings.autoScreenCapture ? 'keep it on' : 'turn it on'}?`,

    'world-monitor': `I have a World Monitor that tracks global intelligence — conflicts, markets, cyber threats, natural disasters, and more across 17 domains. It needs a separate installation. Want me to help you set it up?`,

    intelligence: `I can run background research on topics you care about — industry news, project-relevant updates, personal interests. Want me to set up some research tasks based on what I know about you?`,

    research: `I have two research engines: Perplexity AI for intelligent web search and Firecrawl for reading full articles and documentation. ${settings.perplexityApiKey ? 'Perplexity is active.' : 'Perplexity needs an API key.'} ${settings.firecrawlApiKey ? 'Firecrawl is active.' : 'Firecrawl needs an API key.'} Want to configure either?`,

    'ai-services': `I can generate images with Gemini's image model (already active!). With an OpenAI key, I also get deep reasoning, audio transcription, and semantic search. ${settings.openaiApiKey ? 'OpenAI is configured.' : 'OpenAI needs a key for these extras.'}`,

    voice: `ElevenLabs gives me a more natural, expressive voice. ${settings.elevenLabsApiKey ? 'Already configured and active.' : 'Needs an API key — the built-in voice works fine without it.'}`,

    gateway: `I can be reached through Telegram or Discord when you're away from your computer. Want to set up a messaging channel?`,

    scheduler: `I can run scheduled tasks — morning briefings, periodic research, reminders. Want to set up any recurring tasks?`,
  };

  return prompts[step] || `Want me to help configure: ${step}?`;
}
