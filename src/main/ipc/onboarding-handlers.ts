/**
 * Onboarding IPC handlers — first-run flow, psych profile, feature setup, personality evolution.
 */
import { ipcMain } from 'electron';
import { isFirstRun, buildAllOnboardingToolDeclarations, buildFirstGreetingPrompt } from '../onboarding';
import { generatePsychologicalProfile } from '../psychological-profile';
import {
  initializeFeatureSetup,
  advanceFeatureStep,
  isFeatureSetupComplete,
  getFeatureSetupState,
  getCurrentStep,
  buildFeatureSetupToolDeclaration,
  buildAllFeatureSetupToolDeclarations,
  buildFeatureSetupPrompt,
} from '../feature-setup';
import { getEvolutionState, incrementSession } from '../personality-evolution';
import { generateVoiceSample, getVoiceRecommendations, VOICE_CATALOG, type GeminiVoiceName } from '../voice-audition';
import { settingsManager } from '../settings';
import { ensureProfileOnDisk } from '../friday-profile';
import type { IntakeResponses, FeatureSetupStep } from '../settings';

export function registerOnboardingHandlers(): void {
  // ── Onboarding ──────────────────────────────────────────────────────
  ipcMain.handle('onboarding:is-first-run', () => isFirstRun());
  ipcMain.handle('onboarding:is-complete', () => settingsManager.getAgentConfig().onboardingComplete);
  ipcMain.handle('onboarding:get-config', () => settingsManager.getAgentConfig());
  ipcMain.handle('onboarding:get-tool-declarations', () => buildAllOnboardingToolDeclarations());
  ipcMain.handle('onboarding:get-first-greeting', () => buildFirstGreetingPrompt());

  ipcMain.handle(
    'onboarding:finalize-agent',
    async (
      _event,
      config: {
        agentName: string;
        agentVoice: string;
        agentGender: string;
        agentAccent: string;
        agentBackstory: string;
        agentTraits: string[];
        agentIdentityLine: string;
        userName: string;
        onboardingComplete: boolean;
      },
    ) => {
      await settingsManager.saveAgentConfig(config);
      const featureState = initializeFeatureSetup();
      await settingsManager.setSetting('featureSetupState', featureState);
      console.log(
        `[Onboarding] Feature setup initialized with ${featureState.steps.length} steps`,
      );
      ensureProfileOnDisk().catch((err) => {
        console.warn('[Onboarding] Profile rewrite failed:', err);
      });
      console.log(`[Onboarding] Agent finalized: ${config.agentName} for ${config.userName}`);
      return { success: true };
    },
  );

  // ── Psychological profile ───────────────────────────────────────────
  ipcMain.handle('psych:save-intake', async (_event, responses: IntakeResponses) => {
    await settingsManager.setSetting('intakeResponses', responses);
    console.log('[PsychProfile] Intake responses saved');
    return { success: true };
  });

  ipcMain.handle('psych:get-intake', () => settingsManager.get().intakeResponses);

  ipcMain.handle('psych:generate', async (_event, responses: IntakeResponses) => {
    const profile = await generatePsychologicalProfile(responses);
    await settingsManager.setSetting('psychologicalProfile', profile);
    return profile;
  });

  ipcMain.handle('psych:get', () => settingsManager.get().psychologicalProfile);

  // ── Feature setup ───────────────────────────────────────────────────
  ipcMain.handle('feature-setup:initialize', async () => {
    const state = initializeFeatureSetup();
    await settingsManager.setSetting('featureSetupState', state);
    return state;
  });

  ipcMain.handle('feature-setup:get-state', () => getFeatureSetupState());

  ipcMain.handle('feature-setup:get-prompt', (_event, step: string) =>
    buildFeatureSetupPrompt(step as FeatureSetupStep),
  );

  ipcMain.handle(
    'feature-setup:advance',
    async (_event, step: string, action: 'complete' | 'skip') => {
      return advanceFeatureStep(step as FeatureSetupStep, action);
    },
  );

  ipcMain.handle('feature-setup:is-complete', () => isFeatureSetupComplete());
  ipcMain.handle('feature-setup:get-current-step', () => getCurrentStep());
  ipcMain.handle('feature-setup:get-tool-declaration', () => buildFeatureSetupToolDeclaration());
  ipcMain.handle('feature-setup:get-tool-declarations', () => buildAllFeatureSetupToolDeclarations());

  // ── Personality evolution ───────────────────────────────────────────
  ipcMain.handle('evolution:get-state', () => getEvolutionState());
  ipcMain.handle('evolution:increment-session', async () => incrementSession());

  // ── Desktop evolution (3D visualization structure index) ──────────
  ipcMain.handle('desktop-evolution:get-index', () => {
    const { settingsManager } = require('../settings');
    return settingsManager.get().desktopEvolutionIndex ?? 0;
  });
  ipcMain.handle('desktop-evolution:set-index', (_event: any, index: number) => {
    const { settingsManager } = require('../settings');
    settingsManager.set('desktopEvolutionIndex', index);
    settingsManager.set('desktopEvolutionLastChange', Date.now());
  });
  ipcMain.handle('desktop-evolution:get-transition', () => {
    const { settingsManager } = require('../settings');
    const settings = settingsManager.get();
    return {
      currentIndex: settings.desktopEvolutionIndex ?? 0,
      targetIndex: settings.desktopEvolutionIndex ?? 0,
      blend: 1.0,
      lastChange: settings.desktopEvolutionLastChange ?? 0,
    };
  });

  // ── Art evolution (weekly Gemini-powered visual evolution) ────────
  ipcMain.handle('art-evolution:get-state', () => {
    const { getArtEvolutionState } = require('../art-evolution');
    return getArtEvolutionState();
  });
  ipcMain.handle('art-evolution:get-latest', () => {
    const { getLatestEvolution } = require('../art-evolution');
    return getLatestEvolution();
  });
  ipcMain.handle('art-evolution:check', async () => {
    const { checkAndEvolve } = require('../art-evolution');
    return checkAndEvolve();
  });
  ipcMain.handle('art-evolution:force', async () => {
    const { forceEvolve } = require('../art-evolution');
    return forceEvolve();
  });

  // ── Voice audition ────────────────────────────────────────────────
  ipcMain.handle('voice-audition:generate-sample', async (_event, voiceName: string, customPhrase?: string) => {
    return generateVoiceSample(voiceName as GeminiVoiceName, customPhrase);
  });

  ipcMain.handle('voice-audition:get-recommendations', (_event, genderPref: string) => {
    return getVoiceRecommendations(genderPref);
  });

  ipcMain.handle('voice-audition:get-catalog', () => {
    return VOICE_CATALOG;
  });
}
