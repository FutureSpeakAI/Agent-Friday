/**
 * OnboardingWizard.tsx — Cinematic first-run wizard.
 *
 * 7-step wizard: Awakening → Mission → Hardware → Privacy → ApiKeys →
 * Interview → Reveal. Agent personality (name, gender, voice) is
 * discovered through the voice interview rather than a form — creating
 * a genuine "Her"-style moment when the agent's voice appears for the
 * first time with the personality the user described in conversation.
 */

import React, { useState, useCallback } from 'react';
import AwakeningStep from './onboarding/AwakeningStep';
import MissionStep from './onboarding/MissionStep';
import HardwareStep from './onboarding/HardwareStep';
import PrivacyStep from './onboarding/PrivacyStep';
import ApiKeysStep from './onboarding/ApiKeysStep';
import InterviewStep from './onboarding/InterviewStep';
import RevealStep from './onboarding/RevealStep';
import CyberGrid from './onboarding/shared/CyberGrid';
import CursorGlow from './onboarding/shared/CursorGlow';
import HolographicDiamond from './onboarding/shared/HolographicDiamond';

type TierName = 'whisper' | 'light' | 'standard' | 'full' | 'sovereign';

const STEPS = [
  { key: 'awakening', label: 'AWAKENING' },
  { key: 'mission', label: 'MISSION' },
  { key: 'hardware', label: 'HARDWARE' },
  { key: 'privacy', label: 'PRIVACY' },
  { key: 'apikeys', label: 'API KEYS' },
  { key: 'interview', label: 'INTERVIEW' },
  { key: 'reveal', label: 'REVEAL' },
] as const;

type StepKey = (typeof STEPS)[number]['key'];

export interface IdentityChoices {
  agentName: string;
  gender: 'male' | 'female' | 'neutral';
  voiceFeel: 'warm' | 'sharp' | 'deep' | 'soft' | 'bright';
}

interface OnboardingWizardProps {
  onComplete: (agentName: string) => void;
  connectToGemini?: (identityContext?: string) => void;
  sendTextToGemini?: (text: string) => void;
}

const OnboardingWizard: React.FC<OnboardingWizardProps> = ({ onComplete, connectToGemini, sendTextToGemini }) => {
  const [currentStep, setCurrentStep] = useState<StepKey>('awakening');
  const [detectedTier, setDetectedTier] = useState<TierName | null>(null);
  const [identityChoices, setIdentityChoices] = useState<IdentityChoices>({
    agentName: 'Friday',
    gender: 'male',
    voiceFeel: 'warm',
  });
  /** Fade-out for step transitions */
  const [transitioning, setTransitioning] = useState(false);

  const currentIndex = STEPS.findIndex((s) => s.key === currentStep);

  const goTo = useCallback((step: StepKey) => {
    setTransitioning(true);
    setTimeout(() => {
      setCurrentStep(step);
      setTransitioning(false);
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

  // Don't show progress bar on awakening (splash) or reveal (boot sequence)
  const showProgress = currentStep !== 'awakening' && currentStep !== 'reveal';
  // Intense diamond glow on awakening + reveal
  const diamondIntense = currentStep === 'awakening' || currentStep === 'reveal';

  return (
    <div style={styles.overlay} role="dialog" aria-label="Onboarding wizard" aria-modal="true">
      {/* Global ambient elements */}
      <CyberGrid />
      <CursorGlow />
      <HolographicDiamond intense={diamondIntense} />

      {/* Progress indicator */}
      {showProgress && (
        <nav style={styles.progressBar} aria-label="Onboarding progress">
          {STEPS.slice(1, -1).map((step, i) => {
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

      {/* Step content — fades on transition */}
      <div
        aria-live="polite"
        style={{
          ...styles.stepContainer,
          opacity: transitioning ? 0 : 1,
          transition: 'opacity 0.4s ease-in-out',
        }}
      >
        {currentStep === 'awakening' && <AwakeningStep onComplete={next} />}
        {currentStep === 'mission' && <MissionStep onComplete={next} onBack={prev} />}
        {currentStep === 'hardware' && (
          <HardwareStep
            onComplete={(tier) => {
              setDetectedTier(tier);
              next();
            }}
            onBack={prev}
          />
        )}
        {currentStep === 'privacy' && <PrivacyStep onComplete={next} onBack={prev} />}
        {currentStep === 'apikeys' && (
          <ApiKeysStep detectedTier={detectedTier} onComplete={next} onBack={prev} />
        )}
        {currentStep === 'interview' && (
          <InterviewStep
            identityChoices={identityChoices}
            connectToGemini={connectToGemini}
            sendTextToGemini={sendTextToGemini}
            onComplete={(finalName) => {
              if (finalName) {
                setIdentityChoices((p) => ({ ...p, agentName: finalName }));
              }
              goTo('reveal');
            }}
            onBack={prev}
          />
        )}
        {currentStep === 'reveal' && (
          <RevealStep
            agentName={identityChoices.agentName}
            onComplete={() => onComplete(identityChoices.agentName)}
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
    gap: 32,
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
    fontSize: 9,
    fontWeight: 500,
    letterSpacing: '0.15em',
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
