/**
 * HolographicDiamond.tsx — Persistent holographic diamond entity.
 *
 * A rotating 45-degree square with pulsing border, positioned in the
 * top-right corner. Persists across all onboarding steps.
 */

import React from 'react';

interface HolographicDiamondProps {
  intense?: boolean;
}

const HolographicDiamond: React.FC<HolographicDiamondProps> = ({ intense = false }) => {
  return (
    <div
      aria-hidden="true"
      style={{
        ...styles.container,
        animation: intense
          ? 'onb-diamond-pulse-intense 3s ease-in-out infinite'
          : 'onb-diamond-pulse 4s ease-in-out infinite',
      }}
    >
      <div style={styles.inner} />
    </div>
  );
};

const styles: Record<string, React.CSSProperties> = {
  container: {
    position: 'absolute',
    top: 36,
    right: 36,
    width: 48,
    height: 48,
    transform: 'rotate(45deg)',
    border: '1px solid var(--diamond-border)',
    zIndex: 5,
    pointerEvents: 'none',
  },
  inner: {
    position: 'absolute',
    inset: 6,
    border: '1px solid rgba(0, 240, 255, 0.1)',
  },
};

export default HolographicDiamond;
