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
import { assertString, assertOptionalString, assertNumber, assertObject, assertStringArray } from './validate';

export function registerOnboardingHandlers(): void {
  // ── Onboarding ──────────────────────────────────────────────────────
  ipcMain.handle('onboarding:is-first-run', () => isFirstRun());
  ipcMain.handle('onboarding:is-complete', () => settingsManager.getAgentConfig().onboardingComplete);
  ipcMain.handle('onboarding:get-config', () => settingsManager.getAgentConfig());
  ipcMain.handle('onboarding:get-tool-declarations', () => buildAllOnboardingToolDeclarations());
  ipcMain.handle('onboarding:get-first-greeting', () => buildFirstGreetingPrompt());

  // Crypto Sprint 20: Validate IPC inputs.
  ipcMain.handle(
    'onboarding:finalize-agent',
    async (
      _event,
      config: unknown,
    ) => {
      assertObject(config, 'onboarding:finalize-agent config');
      const c = config as Record<string, unknown>;
      assertString(c.agentName, 'config.agentName', 200);
      assertString(c.agentVoice, 'config.agentVoice', 200);
      assertString(c.agentGender, 'config.agentGender', 50);
      assertString(c.agentAccent, 'config.agentAccent', 200);
      assertString(c.agentBackstory, 'config.agentBackstory', 5_000);
      assertStringArray(c.agentTraits, 'config.agentTraits', 50, 200);
      assertString(c.agentIdentityLine, 'config.agentIdentityLine', 1_000);
      assertString(c.userName, 'config.userName', 200);
      await settingsManager.saveAgentConfig(config as any);
      const featureState = initializeFeatureSetup();
      await settingsManager.setSetting('featureSetupState', featureState);
      // Mark feature setup as complete immediately — no sequential walkthrough phase.
      // Features are configured opportunistically during normal conversation.
      await settingsManager.setSetting('featureSetupComplete', true);
      console.log(
        `[Onboarding] Feature setup initialized (${featureState.steps.length} features, configured opportunistically)`,
      );
      ensureProfileOnDisk().catch((err) => {
        // Crypto Sprint 17: Sanitize error output.
        console.warn('[Onboarding] Profile rewrite failed:', err instanceof Error ? err.message : 'Unknown error');
      });
      console.log(`[Onboarding] Agent finalized: ${config.agentName} for ${config.userName}`);
      return { success: true };
    },
  );

  // ── Psychological profile ───────────────────────────────────────────
  ipcMain.handle('psych:save-intake', async (_event, responses: unknown) => {
    assertObject(responses, 'psych:save-intake responses');
    await settingsManager.setSetting('intakeResponses', responses as unknown as IntakeResponses);
    console.log('[PsychProfile] Intake responses saved');
    return { success: true };
  });

  ipcMain.handle('psych:get-intake', () => settingsManager.get().intakeResponses);

  ipcMain.handle('psych:generate', async (_event, responses: unknown) => {
    assertObject(responses, 'psych:generate responses');
    const profile = await generatePsychologicalProfile(responses as unknown as IntakeResponses);
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

  ipcMain.handle('feature-setup:get-prompt', (_event, step: unknown) => {
    assertString(step, 'feature-setup:get-prompt step', 200);
    return buildFeatureSetupPrompt(step as FeatureSetupStep);
  });

  ipcMain.handle(
    'feature-setup:advance',
    async (_event, step: unknown, action: unknown) => {
      assertString(step, 'feature-setup:advance step', 200);
      assertString(action, 'feature-setup:advance action', 20);
      return advanceFeatureStep(step as FeatureSetupStep, action as 'complete' | 'skip');
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
  ipcMain.handle('desktop-evolution:set-index', (_event: any, index: unknown) => {
    assertNumber(index, 'desktop-evolution:set-index index', 0, 100);
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
  ipcMain.handle('voice-audition:generate-sample', async (_event, voiceName: unknown, customPhrase?: unknown) => {
    assertString(voiceName, 'voice-audition:generate-sample voiceName', 200);
    if (customPhrase !== undefined && customPhrase !== null) {
      assertOptionalString(customPhrase as unknown, 'voice-audition:generate-sample customPhrase', 1_000);
    }
    return generateVoiceSample(voiceName as GeminiVoiceName, customPhrase as string | undefined);
  });

  ipcMain.handle('voice-audition:get-recommendations', (_event, genderPref: unknown) => {
    assertString(genderPref, 'voice-audition:get-recommendations genderPref', 50);
    return getVoiceRecommendations(genderPref as string);
  });

  ipcMain.handle('voice-audition:get-catalog', () => {
    return VOICE_CATALOG;
  });
}
