/**
 * FridayContacts.tsx — Agent Friday Contacts & Trust Graph
 *
 * Displays contacts with trust scores, linked commitments,
 * and the ability to add notes / update trust levels.
 *
 * IPC: window.eve.trustGraph.*, window.eve.commitments.*
 */

import React, { useState, useEffect, useCallback } from 'react';
import AppShell from '../AppShell';
import ContextBar from '../ContextBar';

/** Raw PersonNode from trust graph backend */
interface PersonNode {
  id: string;
  primaryName: string;
  aliases: { value: string; type: string; confidence: number }[];
  trust: { overall: number; reliability: number; expertise: { domain: string; score: number }[] };
  notes: string;
  domains: string[];
  lastSeen: number;
  firstSeen: number;
  interactionCount: number;
}

/** Flattened view-model for the contact list */
interface Contact {
  id: string;
  name: string;
  trustScore: number;
  lastInteraction?: string;
  notes?: string;
  tags?: string[];
  email?: string;
  phone?: string;
}

interface Commitment {
  id: string;
  personName: string;
  description: string;
  deadline: number | null;
  status?: string;
}

interface Props {
  visible: boolean;
  onClose: () => void;
}

export default function FridayContacts({ visible, onClose }: Props) {
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [commitments, setCommitments] = useState<Commitment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<Contact | null>(null);
  const [noteInput, setNoteInput] = useState('');
  const [trustInput, setTrustInput] = useState('');
  const [saving, setSaving] = useState(false);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [personsRes, commitmentsRes] = await Promise.all([
        (window as any).eve.trustGraph.getAll(),
        (window as any).eve.commitments.getAll(),
      ]);
      // Transform PersonNode[] → Contact[]
      const persons: PersonNode[] = Array.isArray(personsRes) ? personsRes : [];
      setContacts(
        persons.map((p) => ({
          id: p.id,
          name: p.primaryName,
          trustScore: Math.round(p.trust?.overall ?? 50),
          lastInteraction: p.lastSeen
            ? new Date(p.lastSeen).toLocaleDateString([], { month: 'short', day: 'numeric' })
            : undefined,
          notes: p.notes || undefined,
          tags: p.domains?.length ? p.domains : undefined,
          email: p.aliases?.find((a) => a.type === 'email')?.value,
          phone: p.aliases?.find((a) => a.type === 'phone')?.value,
        })),
      );
      setCommitments(Array.isArray(commitmentsRes) ? commitmentsRes : []);
    } catch (err: any) {
      setError(err?.message || 'Failed to load contacts');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (visible) loadData();
  }, [visible, loadData]);

  const filtered = contacts.filter((c) =>
    c.name.toLowerCase().includes(search.toLowerCase())
  );

  const personCommitments = selected
    ? commitments.filter(
        (cm) => cm.personName?.toLowerCase() === selected.name.toLowerCase()
      )
    : [];

  const handleSelectContact = async (contact: Contact) => {
    try {
      const result = await (window as any).eve.trustGraph.lookup(contact.name);
      const person: PersonNode | null = result?.person ?? null;
      if (person) {
        const refreshed: Contact = {
          id: person.id,
          name: person.primaryName,
          trustScore: Math.round(person.trust?.overall ?? 50),
          lastInteraction: person.lastSeen
            ? new Date(person.lastSeen).toLocaleDateString([], { month: 'short', day: 'numeric' })
            : undefined,
          notes: person.notes || undefined,
          tags: person.domains?.length ? person.domains : undefined,
          email: person.aliases?.find((a) => a.type === 'email')?.value,
          phone: person.aliases?.find((a) => a.type === 'phone')?.value,
        };
        setSelected(refreshed);
        setNoteInput(refreshed.notes || '');
        setTrustInput(String(refreshed.trustScore));
      } else {
        setSelected(contact);
        setNoteInput(contact.notes || '');
        setTrustInput(String(contact.trustScore ?? 50));
      }
    } catch {
      setSelected(contact);
      setNoteInput(contact.notes || '');
      setTrustInput(String(contact.trustScore ?? 50));
    }
  };

  const handleSaveNote = async () => {
    if (!selected) return;
    setSaving(true);
    try {
      // Save notes via trust graph
      if (noteInput !== (selected.notes || '')) {
        await (window as any).eve.trustGraph.updateNotes(selected.id, noteInput);
      }
      // If trust score changed, record as manual evidence
      const newScore = Number(trustInput) || selected.trustScore;
      if (newScore !== selected.trustScore) {
        await (window as any).eve.trustGraph.updateEvidence(selected.name, {
          type: 'user_stated',
          description: `Trust score manually adjusted to ${newScore}%`,
          impact: (newScore - selected.trustScore) / 100,
          domain: 'general',
        });
      }
      await loadData();
    } catch (err: any) {
      setError(err?.message || 'Failed to save');
    } finally {
      setSaving(false);
    }
  };

  const getTrustColor = (score: number): string => {
    if (score >= 80) return '#22c55e';
    if (score >= 50) return '#f97316';
    return '#ef4444';
  };

  const getTrustLabel = (score: number): string => {
    if (score >= 80) return 'High';
    if (score >= 50) return 'Medium';
    return 'Low';
  };

  return (
    <AppShell visible={visible} onClose={onClose} title="Contacts" icon="👤" width={960}>
      <ContextBar appId="friday-contacts" />
      {loading ? (
        <div style={s.center}>
          <span style={s.spinner}>⟳</span>
          <span style={s.secondaryText}>Loading contacts...</span>
        </div>
      ) : error ? (
        <div style={s.center}>
          <span style={{ color: '#ef4444', fontSize: 14 }}>{error}</span>
          <button style={s.retryBtn} onClick={loadData}>Retry</button>
        </div>
      ) : (
        <div style={s.layout}>
          {/* ── Left: Contact List ── */}
          <div style={s.sidebar}>
            <input
              type="text"
              placeholder="Search contacts..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              style={s.searchInput}
            />
            <div style={s.listScroll}>
              {filtered.length === 0 ? (
                <div style={s.emptyState}>No contacts found</div>
              ) : (
                filtered.map((c) => (
                  <div
                    key={c.name}
                    style={{
                      ...s.contactItem,
                      ...(selected?.name === c.name ? s.contactItemActive : {}),
                    }}
                    onClick={() => handleSelectContact(c)}
                  >
                    <div style={s.contactAvatar}>
                      {c.name.charAt(0).toUpperCase()}
                    </div>
                    <div style={s.contactInfo}>
                      <div style={s.contactName}>{c.name}</div>
                      <div style={s.contactMeta}>
                        Trust:{' '}
                        <span style={{ color: getTrustColor(c.trustScore) }}>
                          {c.trustScore}%
                        </span>
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
            <div style={s.contactCount}>
              {contacts.length} contact{contacts.length !== 1 ? 's' : ''}
            </div>
          </div>

          {/* ── Right: Detail Panel ── */}
          <div style={s.detail}>
            {!selected ? (
              <div style={s.center}>
                <span style={s.mutedText}>Select a contact to view details</span>
              </div>
            ) : (
              <>
                {/* Header Card */}
                <div style={s.detailCard}>
                  <div style={s.detailHeader}>
                    <div style={s.detailAvatarLg}>
                      {selected.name.charAt(0).toUpperCase()}
                    </div>
                    <div>
                      <div style={s.detailName}>{selected.name}</div>
                      {selected.email && (
                        <div style={s.secondaryText}>{selected.email}</div>
                      )}
                      {selected.lastInteraction && (
                        <div style={s.mutedText}>
                          Last seen: {selected.lastInteraction}
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Trust Score */}
                  <div style={s.trustSection}>
                    <div style={s.trustHeader}>
                      <span style={s.label}>Trust Score</span>
                      <span
                        style={{
                          ...s.trustBadge,
                          background: `${getTrustColor(selected.trustScore)}22`,
                          color: getTrustColor(selected.trustScore),
                        }}
                      >
                        {getTrustLabel(selected.trustScore)} ({selected.trustScore}%)
                      </span>
                    </div>
                    <div style={s.trustBar}>
                      <div
                        style={{
                          ...s.trustFill,
                          width: `${Math.min(100, selected.trustScore)}%`,
                          background: getTrustColor(selected.trustScore),
                        }}
                      />
                    </div>
                  </div>

                  {/* Tags */}
                  {selected.tags && selected.tags.length > 0 && (
                    <div style={s.tagsRow}>
                      {selected.tags.map((t) => (
                        <span key={t} style={s.tag}>{t}</span>
                      ))}
                    </div>
                  )}
                </div>

                {/* Commitments */}
                <div style={s.sectionCard}>
                  <div style={s.sectionTitle}>
                    Commitments ({personCommitments.length})
                  </div>
                  {personCommitments.length === 0 ? (
                    <div style={s.mutedText}>No commitments with this person</div>
                  ) : (
                    personCommitments.map((cm) => (
                      <div key={cm.id} style={s.commitmentRow}>
                        <div style={s.commitmentDot(cm.status)} />
                        <div style={{ flex: 1 }}>
                          <div style={s.primaryText}>{cm.description}</div>
                          {cm.deadline && (
                            <div style={s.mutedText}>
                              Due: {new Date(cm.deadline).toLocaleDateString([], { month: 'short', day: 'numeric' })}
                            </div>
                          )}
                        </div>
                        <span
                          style={{
                            ...s.statusBadge,
                            color:
                              cm.status === 'overdue'
                                ? '#ef4444'
                                : cm.status === 'completed'
                                ? '#22c55e'
                                : '#f97316',
                          }}
                        >
                          {cm.status || 'pending'}
                        </span>
                      </div>
                    ))
                  )}
                </div>

                {/* Notes / Update */}
                <div style={s.sectionCard}>
                  <div style={s.sectionTitle}>Notes & Update</div>
                  <textarea
                    value={noteInput}
                    onChange={(e) => setNoteInput(e.target.value)}
                    placeholder="Add notes about this contact..."
                    style={s.textarea}
                    rows={3}
                  />
                  <div style={s.updateRow}>
                    <label style={s.label}>Trust Score:</label>
                    <input
                      type="number"
                      min={0}
                      max={100}
                      value={trustInput}
                      onChange={(e) => setTrustInput(e.target.value)}
                      style={s.numberInput}
                    />
                    <button
                      style={s.saveBtn}
                      onClick={handleSaveNote}
                      disabled={saving}
                    >
                      {saving ? 'Saving...' : 'Save'}
                    </button>
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </AppShell>
  );
}

/* ── Styles ─────────────────────────────────────────────────────────────── */
const s: Record<string, any> = {
  layout: {
    display: 'flex',
    gap: 16,
    minHeight: 420,
    flex: 1,
  } as React.CSSProperties,
  sidebar: {
    width: 280,
    flexShrink: 0,
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
    borderRight: '1px solid rgba(255,255,255,0.07)',
    paddingRight: 16,
  } as React.CSSProperties,
  searchInput: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 8,
    padding: '8px 12px',
    color: '#F8FAFC',
    fontSize: 13,
    fontFamily: "'Inter', system-ui, sans-serif",
    outline: 'none',
  } as React.CSSProperties,
  listScroll: {
    flex: 1,
    overflowY: 'auto',
    display: 'flex',
    flexDirection: 'column',
    gap: 2,
  } as React.CSSProperties,
  contactItem: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    padding: '8px 10px',
    borderRadius: 8,
    cursor: 'pointer',
    transition: 'background 0.15s',
    background: 'transparent',
  } as React.CSSProperties,
  contactItemActive: {
    background: 'rgba(0,240,255,0.08)',
    border: '1px solid rgba(0,240,255,0.3)',
  } as React.CSSProperties,
  contactAvatar: {
    width: 32,
    height: 32,
    borderRadius: '50%',
    background: 'rgba(0,240,255,0.12)',
    color: '#00f0ff',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: 14,
    fontWeight: 700,
    fontFamily: "'Inter', system-ui, sans-serif",
    flexShrink: 0,
  } as React.CSSProperties,
  contactInfo: { display: 'flex', flexDirection: 'column', gap: 2 } as React.CSSProperties,
  contactName: {
    color: '#F8FAFC',
    fontSize: 13,
    fontWeight: 600,
    fontFamily: "'Inter', system-ui, sans-serif",
  } as React.CSSProperties,
  contactMeta: {
    fontSize: 11,
    color: '#8888a0',
    fontFamily: "'Inter', system-ui, sans-serif",
  } as React.CSSProperties,
  contactCount: {
    fontSize: 11,
    color: '#4a4a62',
    textAlign: 'center',
    padding: '4px 0',
    fontFamily: "'Inter', system-ui, sans-serif",
  } as React.CSSProperties,
  detail: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    gap: 12,
    overflowY: 'auto',
  } as React.CSSProperties,
  detailCard: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 12,
    padding: 16,
    display: 'flex',
    flexDirection: 'column',
    gap: 14,
  } as React.CSSProperties,
  detailHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 14,
  } as React.CSSProperties,
  detailAvatarLg: {
    width: 48,
    height: 48,
    borderRadius: '50%',
    background: 'rgba(0,240,255,0.15)',
    color: '#00f0ff',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: 20,
    fontWeight: 700,
    fontFamily: "'Inter', system-ui, sans-serif",
  } as React.CSSProperties,
  detailName: {
    color: '#F8FAFC',
    fontSize: 16,
    fontWeight: 700,
    fontFamily: "'Inter', system-ui, sans-serif",
  } as React.CSSProperties,
  trustSection: {
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
  } as React.CSSProperties,
  trustHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
  } as React.CSSProperties,
  trustBadge: {
    fontSize: 11,
    fontWeight: 600,
    padding: '2px 8px',
    borderRadius: 6,
    fontFamily: "'Inter', system-ui, sans-serif",
  } as React.CSSProperties,
  trustBar: {
    height: 6,
    borderRadius: 3,
    background: 'rgba(255,255,255,0.06)',
    overflow: 'hidden',
  } as React.CSSProperties,
  trustFill: {
    height: '100%',
    borderRadius: 3,
    transition: 'width 0.3s ease',
  } as React.CSSProperties,
  tagsRow: {
    display: 'flex',
    gap: 6,
    flexWrap: 'wrap',
  } as React.CSSProperties,
  tag: {
    fontSize: 11,
    color: '#8A2BE2',
    background: 'rgba(138,43,226,0.12)',
    padding: '2px 8px',
    borderRadius: 4,
    fontFamily: "'Inter', system-ui, sans-serif",
  } as React.CSSProperties,
  sectionCard: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 12,
    padding: 14,
    display: 'flex',
    flexDirection: 'column',
    gap: 10,
  } as React.CSSProperties,
  sectionTitle: {
    color: '#00f0ff',
    fontSize: 12,
    fontWeight: 700,
    letterSpacing: '0.04em',
    textTransform: 'uppercase' as const,
    fontFamily: "'Inter', system-ui, sans-serif",
  } as React.CSSProperties,
  commitmentRow: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 10,
    padding: '6px 0',
    borderBottom: '1px solid rgba(255,255,255,0.04)',
  } as React.CSSProperties,
  commitmentDot: (status?: string): React.CSSProperties => ({
    width: 8,
    height: 8,
    borderRadius: '50%',
    marginTop: 5,
    flexShrink: 0,
    background:
      status === 'overdue' ? '#ef4444' : status === 'completed' ? '#22c55e' : '#f97316',
  }),
  statusBadge: {
    fontSize: 11,
    fontWeight: 600,
    textTransform: 'capitalize' as const,
    fontFamily: "'Inter', system-ui, sans-serif",
  } as React.CSSProperties,
  textarea: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 8,
    padding: '8px 12px',
    color: '#F8FAFC',
    fontSize: 13,
    fontFamily: "'Inter', system-ui, sans-serif",
    resize: 'vertical' as const,
    outline: 'none',
    minHeight: 60,
  } as React.CSSProperties,
  updateRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
  } as React.CSSProperties,
  numberInput: {
    width: 60,
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 6,
    padding: '6px 8px',
    color: '#F8FAFC',
    fontSize: 13,
    fontFamily: "'JetBrains Mono', monospace",
    outline: 'none',
    textAlign: 'center' as const,
  } as React.CSSProperties,
  saveBtn: {
    marginLeft: 'auto',
    background: 'rgba(0,240,255,0.12)',
    border: '1px solid rgba(0,240,255,0.3)',
    borderRadius: 8,
    padding: '6px 18px',
    color: '#00f0ff',
    fontSize: 13,
    fontWeight: 600,
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
  } as React.CSSProperties,
  retryBtn: {
    marginTop: 8,
    background: 'rgba(255,255,255,0.06)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 8,
    padding: '6px 16px',
    color: '#F8FAFC',
    fontSize: 13,
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
  } as React.CSSProperties,
  center: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 10,
    padding: 40,
    flex: 1,
  } as React.CSSProperties,
  spinner: {
    fontSize: 28,
    color: '#00f0ff',
    animation: 'spin 1s linear infinite',
  } as React.CSSProperties,
  label: {
    fontSize: 12,
    color: '#8888a0',
    fontFamily: "'Inter', system-ui, sans-serif",
  } as React.CSSProperties,
  primaryText: {
    color: '#F8FAFC',
    fontSize: 13,
    fontFamily: "'Inter', system-ui, sans-serif",
  } as React.CSSProperties,
  secondaryText: {
    color: '#8888a0',
    fontSize: 12,
    fontFamily: "'Inter', system-ui, sans-serif",
  } as React.CSSProperties,
  mutedText: {
    color: '#4a4a62',
    fontSize: 11,
    fontFamily: "'Inter', system-ui, sans-serif",
  } as React.CSSProperties,
  emptyState: {
    color: '#4a4a62',
    fontSize: 13,
    textAlign: 'center',
    padding: 24,
    fontFamily: "'Inter', system-ui, sans-serif",
  } as React.CSSProperties,
};
