/**
 * CyberGrid.tsx — Subtle SVG grid background for onboarding.
 *
 * Renders a low-opacity grid pattern that slowly drifts vertically
 * using the onb-grid-drift keyframe.
 */

import React from 'react';

const CyberGrid: React.FC = () => {
  return (
    <div style={styles.container} aria-hidden="true">
      <svg width="100%" height="100%" style={styles.svg}>
        <defs>
          <pattern id="onb-grid" width="40" height="40" patternUnits="userSpaceOnUse">
            <path
              d="M 40 0 L 0 0 0 40"
              fill="none"
              stroke="rgba(0, 240, 255, 0.04)"
              strokeWidth="0.5"
            />
          </pattern>
        </defs>
        <rect width="100%" height="200%" fill="url(#onb-grid)" />
      </svg>
    </div>
  );
};

const styles: Record<string, React.CSSProperties> = {
  container: {
    position: 'absolute',
    inset: 0,
    overflow: 'hidden',
    pointerEvents: 'none',
    zIndex: 0,
    animation: 'onb-grid-drift 20s linear infinite',
  },
  svg: {
    position: 'absolute',
    top: '-40px',
    left: 0,
    width: '100%',
    height: 'calc(100% + 80px)',
  },
};

export default CyberGrid;
