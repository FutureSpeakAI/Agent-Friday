/**
 * CyberInput.tsx — Text input with floating label animation.
 *
 * Label starts centered in the input and transitions to a small label above
 * when the input is focused or has a value. Uses direct DOM manipulation via
 * ref for the label position to avoid re-render storms.
 */

import React, { useState, useRef, useEffect } from 'react';

interface CyberInputProps {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: 'text' | 'password';
  error?: string;
  success?: boolean;
  monospace?: boolean;
  onKeyDown?: (e: React.KeyboardEvent) => void;
  autoFocus?: boolean;
  maxLength?: number;
  disabled?: boolean;
  'aria-describedby'?: string;
}

const CyberInput: React.FC<CyberInputProps> = ({
  id,
  label,
  value,
  onChange,
  type = 'text',
  error,
  success = false,
  monospace = false,
  onKeyDown,
  autoFocus = false,
  maxLength,
  disabled = false,
  ...ariaProps
}) => {
  const [isFocused, setIsFocused] = useState(false);
  const isFloating = isFocused || value.length > 0;

  const borderColor = error
    ? 'rgba(239, 68, 68, 0.4)'
    : success
      ? 'rgba(34, 197, 94, 0.4)'
      : isFocused
        ? 'var(--accent-cyan-30)'
        : 'var(--onboarding-border)';

  return (
    <div style={styles.wrapper}>
      <div style={{ ...styles.inputContainer, borderColor }}>
        <label
          htmlFor={id}
          style={{
            ...styles.label,
            ...(isFloating ? styles.labelFloating : styles.labelResting),
            color: error
              ? 'rgba(239, 68, 68, 0.7)'
              : isFloating
                ? 'var(--accent-cyan-70)'
                : 'var(--text-30)',
          }}
        >
          {label}
        </label>
        <input
          id={id}
          type={type}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onFocus={() => setIsFocused(true)}
          onBlur={() => setIsFocused(false)}
          onKeyDown={onKeyDown}
          autoFocus={autoFocus}
          maxLength={maxLength}
          disabled={disabled}
          aria-describedby={ariaProps['aria-describedby']}
          aria-invalid={!!error}
          style={{
            ...styles.input,
            fontFamily: monospace
              ? "'JetBrains Mono', monospace"
              : "'Space Grotesk', sans-serif",
          }}
        />
      </div>
      {error && (
        <span role="alert" style={styles.error}>{error}</span>
      )}
    </div>
  );
};

const styles: Record<string, React.CSSProperties> = {
  wrapper: {
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
    width: '100%',
  },
  inputContainer: {
    position: 'relative',
    background: 'var(--onboarding-card)',
    border: '1px solid var(--onboarding-border)',
    borderRadius: 8,
    transition: 'border-color 0.2s ease',
  },
  label: {
    position: 'absolute',
    left: 14,
    pointerEvents: 'none',
    transition: 'all 0.2s ease',
    fontFamily: "'Space Grotesk', sans-serif",
    fontWeight: 500,
    zIndex: 1,
  },
  labelResting: {
    top: '50%',
    transform: 'translateY(-50%)',
    fontSize: 13,
  },
  labelFloating: {
    top: 6,
    transform: 'translateY(0)',
    fontSize: 9,
    letterSpacing: '0.1em',
    fontWeight: 600,
  },
  input: {
    width: '100%',
    background: 'transparent',
    border: 'none',
    outline: 'none',
    padding: '22px 14px 8px',
    fontSize: 14,
    color: 'var(--text-primary)',
    letterSpacing: '0.03em',
  },
  error: {
    fontSize: 10,
    color: 'var(--accent-red)',
    fontFamily: "'Inter', sans-serif",
    paddingLeft: 2,
  },
};

export default CyberInput;
