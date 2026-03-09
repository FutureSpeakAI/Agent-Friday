/**
 * OnboardingWizard.tsx — Cinematic first-run wizard.
 *
 * Replaces WelcomeGate + voice-only onboarding with a structured 7-step
 * wizard: Awakening → Directives → Engines → Sovereignty → Identity →
 * Interview → Reveal.
 *
 * Each step is a separate component in ./onboarding/. The wizard manages
 * step transitions, progress display, and shared state.
 */

import React, { useState, useCallback, useRef } from 'react';
import AwakeningStep from './onboarding/AwakeningStep';
import DirectivesStep from './onboarding/DirectivesStep';
import EnginesStep from './onboarding/EnginesStep';
import SovereigntyStep from './onboarding/SovereigntyStep';
import IdentityStep from './onboarding/IdentityStep';
import InterviewStep from './onboarding/InterviewStep';
import RevealStep from './onboarding/RevealStep';

const STEPS = [
  { key: 'awakening', label: 'AWAKENING' },
  { key: 'directives', label: 'DIRECTIVES' },
  { key: 'engines', label: 'ENGINES' },
  { key: 'sovereignty', label: 'SOVEREIGNTY' },
  { key: 'identity', label: 'IDENTITY' },
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
}

const OnboardingWizard: React.FC<OnboardingWizardProps> = ({ onComplete, connectToGemini }) => {
  const [currentStep, setCurrentStep] = useState<StepKey>('awakening');
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

  return (
    <div style={styles.overlay} role="dialog" aria-label="Onboarding wizard" aria-modal="true">
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
        {currentStep === 'directives' && <DirectivesStep onComplete={next} onBack={prev} />}
        {currentStep === 'engines' && (
          <EnginesStep onComplete={() => next()} onBack={prev} />
        )}
        {currentStep === 'sovereignty' && <SovereigntyStep onComplete={next} onBack={prev} />}
        {currentStep === 'identity' && (
          <IdentityStep
            choices={identityChoices}
            onChange={setIdentityChoices}
            onComplete={next}
            onBack={prev}
          />
        )}
        {currentStep === 'interview' && (
          <InterviewStep
            identityChoices={identityChoices}
            connectToGemini={connectToGemini}
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
