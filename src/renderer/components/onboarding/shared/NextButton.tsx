/**
 * NextButton.tsx — Reusable onboarding button with laser-line hover effect.
 *
 * Variants: primary (cyan), secondary (ghost), skip (muted).
 * The laser-line `::after` animation is defined in global.css via `.onb-next-btn`.
 */

import React from 'react';

interface NextButtonProps {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  variant?: 'primary' | 'secondary' | 'skip';
  icon?: React.ReactNode;
  loading?: boolean;
}

const NextButton: React.FC<NextButtonProps> = ({
  label,
  onClick,
  disabled = false,
  variant = 'primary',
  icon,
  loading = false,
}) => {
  const isDisabled = disabled || loading;
  const variantStyles = VARIANT_STYLES[variant];

  return (
    <button
      className="onb-next-btn"
      onClick={onClick}
      disabled={isDisabled}
      style={{
        ...styles.base,
        ...variantStyles,
        opacity: isDisabled ? 0.35 : 1,
        pointerEvents: isDisabled ? 'none' : 'auto',
      }}
    >
      {loading ? (
        <span style={styles.loadingText}>...</span>
      ) : (
        <>
          <span>{label}</span>
          {icon && <span style={styles.icon}>{icon}</span>}
        </>
      )}
    </button>
  );
};

const styles: Record<string, React.CSSProperties> = {
  base: {
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 10,
    padding: '14px 48px',
    borderRadius: 8,
    fontSize: 14,
    fontWeight: 500,
    letterSpacing: '0.05em',
    fontFamily: "'Space Grotesk', sans-serif",
    cursor: 'pointer',
    transition: 'all 0.2s ease',
  },
  icon: {
    display: 'flex',
    alignItems: 'center',
  },
  loadingText: {
    letterSpacing: '0.3em',
    fontSize: 16,
  },
};

const VARIANT_STYLES: Record<string, React.CSSProperties> = {
  primary: {
    background: 'var(--accent-cyan-10)',
    border: '1px solid var(--accent-cyan-20)',
    color: 'var(--accent-cyan-90)',
  },
  secondary: {
    background: 'transparent',
    border: '1px solid var(--onboarding-border)',
    color: 'var(--text-60)',
  },
  skip: {
    background: 'var(--onboarding-card)',
    border: '1px solid var(--onboarding-border)',
    color: 'var(--text-50)',
    padding: '10px 28px',
    fontSize: 13,
  },
};

export default NextButton;
