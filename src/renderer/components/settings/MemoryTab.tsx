import React from 'react';
import { styles } from './styles';
import { Divider } from './shared';
import type { LongTermEntry, MediumTermEntry } from './types';

interface MemoryTabProps {
  longTerm: LongTermEntry[];
  mediumTerm: MediumTermEntry[];
  onDeleteLongTerm: (id: string) => void;
  onDeleteMediumTerm: (id: string) => void;
}

export default function MemoryTab({
  longTerm,
  mediumTerm,
  onDeleteLongTerm,
  onDeleteMediumTerm,
}: MemoryTabProps) {
  return (
    <div style={styles.section}>
      <h3 style={styles.sectionTitle}>
        Long-term Memory
        <span style={styles.badge}>{longTerm.length}</span>
      </h3>
      {longTerm.length === 0 ? (
        <div style={styles.emptyState}>No memories yet — talk to your agent to build your profile</div>
      ) : (
        <div style={styles.entryList}>
          {longTerm.map((entry) => (
            <div key={entry.id} style={styles.entry}>
              <div style={styles.entryContent}>
                <span style={styles.entryCategory}>{entry.category}</span>
                <span style={styles.entryText}>{entry.fact}</span>
              </div>
              <button onClick={() => onDeleteLongTerm(entry.id)} style={styles.deleteBtn} title="Delete memory">
                ✕
              </button>
            </div>
          ))}
        </div>
      )}

      <Divider />

      <h3 style={styles.sectionTitle}>
        Medium-term Observations
        <span style={styles.badge}>{mediumTerm.length}</span>
      </h3>
      {mediumTerm.length === 0 ? (
        <div style={styles.emptyState}>No observations yet</div>
      ) : (
        <div style={styles.entryList}>
          {mediumTerm.map((entry) => (
            <div key={entry.id} style={styles.entry}>
              <div style={styles.entryContent}>
                <span style={styles.entryCategory}>{entry.category}</span>
                <span style={styles.entryText}>{entry.observation}</span>
                <span style={styles.entryMeta}>
                  {Math.round(entry.confidence * 100)}% confidence · {entry.occurrences} occurrences
                </span>
              </div>
              <button
                onClick={() => onDeleteMediumTerm(entry.id)}
                style={styles.deleteBtn}
                title="Delete observation"
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
