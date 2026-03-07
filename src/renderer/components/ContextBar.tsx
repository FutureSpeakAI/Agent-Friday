/**
 * Sprint 2, Phase E.1: Shared context display bar for apps.
 *
 * Shows the active work stream name and briefing summary (if any).
 * Renders nothing when context is empty — graceful degradation.
 */

import React from 'react';
import { useAppContext } from '../hooks/useAppContext';

interface ContextBarProps {
  appId: string;
}

export default function ContextBar({ appId }: ContextBarProps) {
  const { context, briefing } = useAppContext(appId);
  const streamName = context.activeStream?.name ?? null;

  if (!streamName && !briefing) return null;

  return (
    <div style={styles.bar} data-testid="context-bar">
      {streamName && (
        <span style={styles.stream}>
          <span style={styles.dot} />
          {streamName}
        </span>
      )}
      {briefing && (
        <span style={styles.briefing}>{briefing}</span>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  bar: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    padding: '6px 12px',
    background: 'rgba(0,240,255,0.04)',
    borderBottom: '1px solid rgba(0,240,255,0.1)',
    fontSize: 11,
    color: '#8888a0',
    minHeight: 28,
  },
  stream: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    color: '#00f0ff',
    fontWeight: 600,
  },
  dot: {
    width: 6,
    height: 6,
    borderRadius: '50%',
    background: '#00f0ff',
    flexShrink: 0,
  },
  briefing: {
    color: '#a0a0b8',
    fontStyle: 'italic',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
};
