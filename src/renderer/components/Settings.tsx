import React, { useState, useEffect, useCallback } from 'react';
import { styles } from './settings/styles';
import GeneralTab from './settings/GeneralTab';
import LocalAITab from './settings/LocalAITab';
import MemoryTab from './settings/MemoryTab';
import TasksTab from './settings/TasksTab';
import type { Tab, SettingsProps, MaskedSettings, LongTermEntry, MediumTermEntry, TaskEntry } from './settings/types';

export default function Settings({ visible, onClose }: SettingsProps) {
  const [tab, setTab] = useState<Tab>('general');
  const [settings, setSettings] = useState<MaskedSettings | null>(null);
  const [longTerm, setLongTerm] = useState<LongTermEntry[]>([]);
  const [mediumTerm, setMediumTerm] = useState<MediumTermEntry[]>([]);
  const [tasks, setTasks] = useState<TaskEntry[]>([]);
  const [saveMsg, setSaveMsg] = useState('');

  // Confirmation dialog state
  const [confirmAction, setConfirmAction] = useState<{
    message: string;
    onConfirm: () => void;
  } | null>(null);

  const loadSettings = useCallback(async () => {
    try {
      const s = await window.eve.settings.get();
      setSettings(s as unknown as MaskedSettings);
    } catch {
      // ignore
    }
  }, []);

  const loadMemory = useCallback(async () => {
    try {
      const [lt, mt] = await Promise.all([
        window.eve.memory.getLongTerm(),
        window.eve.memory.getMediumTerm(),
      ]);
      setLongTerm(lt);
      setMediumTerm(mt);
    } catch {
      // ignore
    }
  }, []);

  const loadTasks = useCallback(async () => {
    try {
      const t = await window.eve.scheduler.listTasks();
      setTasks(t);
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    if (!visible) return;
    loadSettings();
    loadMemory();
    loadTasks();
  }, [visible, loadSettings, loadMemory, loadTasks]);

  const overlayRef = React.useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (visible) {
      setTimeout(() => overlayRef.current?.focus(), 50);
    }
  }, [visible]);

  if (!visible) return null;

  const flash = (msg: string) => {
    setSaveMsg(msg);
    setTimeout(() => setSaveMsg(''), 3000);
  };

  const handleDeleteLongTerm = (id: string) => {
    setConfirmAction({
      message: 'Delete this memory? This cannot be undone.',
      onConfirm: async () => {
        try {
          await window.eve.memory.deleteLongTerm(id);
          await loadMemory();
        } catch (err) {
          flash(`Failed to delete memory: ${err instanceof Error ? err.message : 'unknown error'}`);
        }
        setConfirmAction(null);
      },
    });
  };

  const handleDeleteMediumTerm = (id: string) => {
    setConfirmAction({
      message: 'Delete this observation? This cannot be undone.',
      onConfirm: async () => {
        try {
          await window.eve.memory.deleteMediumTerm(id);
          await loadMemory();
        } catch (err) {
          flash(`Failed to delete observation: ${err instanceof Error ? err.message : 'unknown error'}`);
        }
        setConfirmAction(null);
      },
    });
  };

  const handleDeleteTask = (id: string) => {
    setConfirmAction({
      message: 'Delete this scheduled task? This cannot be undone.',
      onConfirm: async () => {
        try {
          await window.eve.scheduler.deleteTask(id);
          await loadTasks();
        } catch (err) {
          flash(`Failed to delete task: ${err instanceof Error ? err.message : 'unknown error'}`);
        }
        setConfirmAction(null);
      },
    });
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') onClose();
  };

  const tabs: { key: Tab; label: string }[] = [
    { key: 'general', label: 'General' },
    { key: 'localai', label: 'Local AI' },
    { key: 'memory', label: 'Memory' },
    { key: 'tasks', label: 'Tasks' },
  ];

  return (
    <div ref={overlayRef} style={styles.overlay} onKeyDown={handleKeyDown} tabIndex={-1}>
      <div style={styles.panel}>
        {/* Confirmation dialog */}
        {confirmAction && (
          <div style={styles.confirmOverlay}>
            <div style={styles.confirmBox}>
              <div style={styles.confirmMsg}>{confirmAction.message}</div>
              <div style={styles.confirmBtns}>
                <button onClick={() => setConfirmAction(null)} style={styles.confirmCancel}>
                  Cancel
                </button>
                <button onClick={confirmAction.onConfirm} style={styles.confirmDelete}>
                  Delete
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Header */}
        <div style={styles.header}>
          <span style={styles.headerIcon}>⚙</span>
          <span style={styles.headerTitle}>Settings</span>
          <button onClick={onClose} style={styles.closeBtn}>✕</button>
        </div>

        {/* Tabs */}
        <div style={styles.tabs}>
          {tabs.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              style={{
                ...styles.tab,
                ...(tab === t.key ? styles.tabActive : {}),
              }}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* Save feedback */}
        {saveMsg && <div style={styles.saveMsg}>{saveMsg}</div>}

        {/* Content — scrollable with custom scrollbar */}
        <div className="settings-scroll" style={styles.content}>
          {tab === 'general' && settings && (
            <GeneralTab settings={settings} loadSettings={loadSettings} flash={flash} />
          )}

          {tab === 'general' && !settings && (
            <div style={styles.loading}>Loading settings...</div>
          )}

          {tab === 'memory' && (
            <MemoryTab
              longTerm={longTerm}
              mediumTerm={mediumTerm}
              onDeleteLongTerm={handleDeleteLongTerm}
              onDeleteMediumTerm={handleDeleteMediumTerm}
            />
          )}

          {tab === 'localai' && settings && (
            <LocalAITab settings={settings} loadSettings={loadSettings} flash={flash} />
          )}

          {tab === 'tasks' && <TasksTab tasks={tasks} onDelete={handleDeleteTask} />}
        </div>
      </div>

      {/* Injected scrollbar styles */}
      <style>{`
        .settings-scroll::-webkit-scrollbar {
          width: 6px;
        }
        .settings-scroll::-webkit-scrollbar-track {
          background: transparent;
        }
        .settings-scroll::-webkit-scrollbar-thumb {
          background: rgba(0, 240, 255, 0.15);
          border-radius: 3px;
        }
        .settings-scroll::-webkit-scrollbar-thumb:hover {
          background: rgba(0, 240, 255, 0.3);
        }
      `}</style>
    </div>
  );
}
