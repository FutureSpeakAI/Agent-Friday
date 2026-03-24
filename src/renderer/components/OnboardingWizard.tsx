/**
 * OnboardingWizard.tsx — Cinematic first-run wizard (10-step flow).
 *
 * Steps:
 *   1. Awakening     — Splash / brand intro
 *   2. Mission       — What Agent Friday does
 *   3. Hardware      — GPU/VRAM detection, Ollama check, tier (with override)
 *   4. Providers     — All 8 API keys + routing preference
 *   5. Models        — Local model selection (chat, STT, TTS, embeddings)
 *   6. VoiceIdentity — Agent name, gender, voice feel, voice engine
 *   7. Privacy       — Vault passphrase, privacy toggles, memory depth
 *   8. Integrations  — Calendar, Obsidian, Gateway (optional)
 *   9. Personality   — Interview vs Manual sliders vs Skip → routes to Interview/FirstContact
 *  10. Reveal        — Terminal boot sequence
 *
 * The Personality step gates the cinematic moment — interview users get a
 * full voice calibration; manual users get a brief "first contact" intro;
 * skip users go straight to reveal with defaults.
 */

import React, { useState, useCallback, useEffect, useRef } from 'react';
import AwakeningStep from './onboarding/AwakeningStep';
import MissionStep from './onboarding/MissionStep';
import HardwareStep from './onboarding/HardwareStep';
import ProvidersStep from './onboarding/ProvidersStep';
import ModelsStep from './onboarding/ModelsStep';
import VoiceIdentityStep from './onboarding/VoiceIdentityStep';
import PrivacyPermissionsStep from './onboarding/PrivacyPermissionsStep';
import IntegrationsStep from './onboarding/IntegrationsStep';
import PersonalityStep from './onboarding/PersonalityStep';
import InterviewStep from './onboarding/InterviewStep';
import RevealStep from './onboarding/RevealStep';
import CyberGrid from './onboarding/shared/CyberGrid';
import CursorGlow from './onboarding/shared/CursorGlow';
import HolographicDiamond from './onboarding/shared/HolographicDiamond';

import type { PersonalityPath } from './onboarding/PersonalityStep';

type TierName = 'whisper' | 'light' | 'standard' | 'full' | 'sovereign';

const STEPS = [
  { key: 'awakening',     label: 'AWAKENING' },
  { key: 'mission',       label: 'MISSION' },
  { key: 'hardware',      label: 'HARDWARE' },
  { key: 'providers',     label: 'PROVIDERS' },
  { key: 'models',        label: 'MODELS' },
  { key: 'voiceidentity', label: 'IDENTITY' },
  { key: 'privacy',       label: 'PRIVACY' },
  { key: 'integrations',  label: 'INTEGRATIONS' },
  { key: 'personality',   label: 'PERSONALITY' },
  { key: 'interview',     label: 'INTERVIEW' },
  { key: 'reveal',        label: 'REVEAL' },
] as const;

type StepKey = (typeof STEPS)[number]['key'];

export interface IdentityChoices {
  agentName: string;
  gender: 'male' | 'female' | 'neutral';
  voiceFeel: 'warm' | 'sharp' | 'deep' | 'soft' | 'bright';
}

interface OnboardingWizardProps {
  onComplete: (agentName: string) => void;
  connectVoice?: (identityContext?: string) => Promise<void>;
  sendText?: (text: string) => void;
}

const OnboardingWizard: React.FC<OnboardingWizardProps> = ({ onComplete, connectVoice, sendText }) => {
  const [currentStep, setCurrentStep] = useState<StepKey>('awakening');
  const [detectedTier, setDetectedTier] = useState<TierName | null>(null);
  const [personalityPath, setPersonalityPath] = useState<PersonalityPath | null>(null);
  const [identityChoices, setIdentityChoices] = useState<IdentityChoices>({
    agentName: 'Friday',
    gender: 'male',
    voiceFeel: 'warm',
  });
  const [transitioning, setTransitioning] = useState(false);
  const identityChoicesRef = useRef(identityChoices);
  identityChoicesRef.current = identityChoices;

  const currentIndex = STEPS.findIndex((s) => s.key === currentStep);

  // Restore checkpoint on mount (crash recovery)
  useEffect(() => {
    window.eve.onboarding.getCheckpoint().then((checkpoint) => {
      if (checkpoint && checkpoint.step) {
        const validStep = STEPS.find((s) => s.key === checkpoint.step);
        if (validStep) {
          setCurrentStep(validStep.key);
          if (checkpoint.identityChoices) {
            setIdentityChoices(checkpoint.identityChoices as IdentityChoices);
          }
          console.log(`[OnboardingWizard] Restored checkpoint at step: ${checkpoint.step}`);
        }
      }
    }).catch(() => {
      // No checkpoint or IPC unavailable — start from scratch
    });
  }, []);

  const goTo = useCallback((step: StepKey) => {
    setTransitioning(true);
    setTimeout(() => {
      setCurrentStep(step);
      setTransitioning(false);
      window.eve.onboarding.saveCheckpoint({ step, identityChoices: identityChoicesRef.current }).catch(() => {});
    }, 400);
  }, []);

  const next = useCallback(() => {
    const idx = STEPS.findIndex((s) => s.key === currentStep);
    if (idx < STEPS.length - 1) {
      goTo(STEPS[idx + 1].key);
    }
  }, [currentStep, goTo]);

  const prev = useCallback(() => {
    const idx = STEPS.findIndex((s) => s.key === currentStep);
    if (idx > 0) {
      goTo(STEPS[idx - 1].key);
    }
  }, [currentStep, goTo]);

  // Hide progress on splash (awakening) and finale (reveal)
  const showProgress = currentStep !== 'awakening' && currentStep !== 'reveal';
  // Also hide on interview — it has its own UI state
  const showProgressBar = showProgress && currentStep !== 'interview';
  const diamondIntense = currentStep === 'awakening' || currentStep === 'reveal';

  // Progress dots: show all steps between awakening and reveal (exclusive)
  const progressSteps = STEPS.slice(1, -1); // skip awakening + reveal

  return (
    <div style={styles.overlay} role="dialog" aria-label="Onboarding wizard" aria-modal="true">
      <CyberGrid />
      <CursorGlow />
      <HolographicDiamond intense={diamondIntense} />

      {showProgressBar && (
        <nav style={styles.progressBar} aria-label="Onboarding progress">
          {progressSteps.map((step, i) => {
            const stepIdx = i + 1; // offset by 1 since we skip awakening
            const isActive = currentIndex === stepIdx;
            const isComplete = currentIndex > stepIdx;
            return (
              <div key={step.key} style={styles.progressStep} aria-current={isActive ? 'step' : undefined}>
                <div
                  aria-hidden="true"
                  style={{
                    ...styles.progressDot,
                    background: isComplete
                      ? 'var(--accent-cyan)'
                      : isActive
                        ? 'var(--accent-cyan-50)'
                        : 'rgba(255, 255, 255, 0.1)',
                    boxShadow: isActive ? '0 0 8px var(--accent-cyan-30)' : 'none',
                  }}
                />
                <span
                  style={{
                    ...styles.progressLabel,
                    color: isComplete
                      ? 'var(--accent-cyan-70)'
                      : isActive
                        ? 'var(--accent-cyan-90)'
                        : 'var(--text-20)',
                  }}
                >
                  {step.label}
                </span>
              </div>
            );
          })}
        </nav>
      )}

      <div
        aria-live="polite"
        style={{
          ...styles.stepContainer,
          opacity: transitioning ? 0 : 1,
          transition: 'opacity 0.4s ease-in-out',
        }}
      >
        {/* Step 1: Awakening */}
        {currentStep === 'awakening' && <AwakeningStep onComplete={next} />}

        {/* Step 2: Mission */}
        {currentStep === 'mission' && <MissionStep onComplete={next} onBack={prev} />}

        {/* Step 3: Hardware (with tier override) */}
        {currentStep === 'hardware' && (
          <HardwareStep
            onComplete={(tier) => {
              setDetectedTier(tier);
              next();
            }}
            onBack={prev}
          />
        )}

        {/* Step 4: Providers (all 8 API keys + routing) */}
        {currentStep === 'providers' && (
          <ProvidersStep
            detectedTier={detectedTier}
            onComplete={next}
            onBack={prev}
          />
        )}

        {/* Step 5: Models (local model selection) */}
        {currentStep === 'models' && (
          <ModelsStep
            detectedTier={detectedTier}
            onComplete={() => next()}
            onBack={prev}
          />
        )}

        {/* Step 6: Voice Identity (name, gender, voice feel, engine) */}
        {currentStep === 'voiceidentity' && (
          <VoiceIdentityStep
            choices={identityChoices}
            onChange={setIdentityChoices}
            onComplete={next}
            onBack={prev}
          />
        )}

        {/* Step 7: Privacy & Permissions (vault, toggles, memory) */}
        {currentStep === 'privacy' && (
          <PrivacyPermissionsStep
            onComplete={next}
            onBack={prev}
          />
        )}

        {/* Step 8: Integrations (calendar, obsidian, gateway) */}
        {currentStep === 'integrations' && (
          <IntegrationsStep
            onComplete={next}
            onBack={prev}
          />
        )}

        {/* Step 9: Personality (path selector) */}
        {currentStep === 'personality' && (
          <PersonalityStep
            onComplete={(path, _sliders) => {
              setPersonalityPath(path);
              if (path === 'interview' || path === 'firstContact') {
                // Route to interview step (full interview or first contact)
                goTo('interview');
              } else {
                // Skip — go straight to reveal with defaults
                goTo('reveal');
              }
            }}
            onBack={prev}
          />
        )}

        {/* Step 9b: Interview / First Contact */}
        {currentStep === 'interview' && (
          <InterviewStep
            identityChoices={identityChoices}
            connectVoice={connectVoice}
            sendText={sendText}
            firstContact={personalityPath === 'firstContact'}
            onComplete={(finalName) => {
              if (finalName) {
                setIdentityChoices((p) => ({ ...p, agentName: finalName }));
              }
              goTo('reveal');
            }}
            onBack={() => goTo('personality')}
          />
        )}

        {/* Step 10: Reveal */}
        {currentStep === 'reveal' && (
          <RevealStep
            agentName={identityChoices.agentName}
            onComplete={() => {
              window.eve.onboarding.clearCheckpoint().catch(() => {});
              onComplete(identityChoices.agentName);
            }}
          />
        )}
      </div>
    </div>
  );
};

/* ── Styles ─────────────────────────────────────────────────── */

const styles: Record<string, React.CSSProperties> = {
  overlay: {
    position: 'fixed',
    inset: 0,
    zIndex: 200,
    background: 'var(--onboarding-bg)',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    fontFamily: "'Space Grotesk', 'Inter', system-ui, sans-serif",
    overflow: 'hidden',
    WebkitAppRegion: 'no-drag',
  } as React.CSSProperties,
  progressBar: {
    position: 'absolute',
    top: 32,
    left: '50%',
    transform: 'translateX(-50%)',
    display: 'flex',
    gap: 20,
    zIndex: 10,
  },
  progressStep: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 6,
  },
  progressDot: {
    width: 8,
    height: 8,
    borderRadius: '50%',
    transition: 'all 0.4s ease',
  },
  progressLabel: {
    fontSize: 8,
    fontWeight: 500,
    letterSpacing: '0.12em',
    fontFamily: "'Space Grotesk', sans-serif",
    transition: 'color 0.4s ease',
  },
  stepContainer: {
    width: '100%',
    height: '100%',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
  },
};

export default OnboardingWizard;
