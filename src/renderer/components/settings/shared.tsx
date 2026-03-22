import React from 'react';
import { styles } from './styles';

export function SectionHeader({ children }: { children: React.ReactNode }) {
  return <h3 style={styles.sectionTitle}>{children}</h3>;
}

export function Toggle({
  value,
  label,
  hint,
  onToggle,
}: {
  value: boolean;
  label: string;
  hint?: string;
  onToggle: () => void;
}) {
  return (
    <>
      <div style={styles.toggleRow} onClick={onToggle}>
        <div
          style={{
            ...styles.toggle,
            background: value ? 'rgba(0, 240, 255, 0.2)' : 'rgba(255,255,255,0.06)',
            borderColor: value ? 'rgba(0, 240, 255, 0.4)' : 'rgba(255,255,255,0.1)',
          }}
        >
          <div
            style={{
              ...styles.toggleDot,
              transform: value ? 'translateX(16px)' : 'translateX(0)',
              background: value ? '#00f0ff' : '#555568',
            }}
          />
        </div>
        <span style={styles.toggleLabel}>{label}</span>
      </div>
      {hint && <div style={styles.toggleHint}>{hint}</div>}
    </>
  );
}

export function ApiKeyField({
  label,
  hasKey,
  hint,
  value,
  onChange,
  onSave,
  description,
  validating,
}: {
  label: string;
  hasKey: boolean;
  hint: string;
  value: string;
  onChange: (v: string) => void;
  onSave: () => void;
  description?: string;
  validating?: boolean;
}) {
  return (
    <div style={styles.fieldGroup}>
      <label style={styles.label}>
        {label}
        {hasKey && <span style={styles.keyHint}>{hint}</span>}
        {hasKey && <span style={styles.connectedDot} />}
      </label>
      <div style={styles.keyRow}>
        <input
          type="password"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={hasKey ? 'Enter new key to replace' : `Paste your ${label}`}
          style={styles.keyInput}
          disabled={validating}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && value.trim() && !validating) onSave();
          }}
        />
        <button onClick={onSave} style={styles.saveBtn} disabled={!value.trim() || validating}>
          {validating ? 'Validating...' : 'Save'}
        </button>
      </div>
      {description && <div style={styles.toggleHint}>{description}</div>}
    </div>
  );
}

export function Divider() {
  return <div style={styles.divider} />;
}
