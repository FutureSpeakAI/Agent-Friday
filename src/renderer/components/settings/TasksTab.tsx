import React from 'react';
import { styles } from './styles';
import type { TaskEntry } from './types';

interface TasksTabProps {
  tasks: TaskEntry[];
  onDelete: (id: string) => void;
}

export default function TasksTab({ tasks, onDelete }: TasksTabProps) {
  return (
    <div style={styles.section}>
      <h3 style={styles.sectionTitle}>
        Scheduled Tasks
        <span style={styles.badge}>{tasks.length}</span>
      </h3>
      {tasks.length === 0 ? (
        <div style={styles.emptyState}>No scheduled tasks — ask your agent to set a reminder</div>
      ) : (
        <div style={styles.entryList}>
          {tasks.map((task) => (
            <div key={task.id} style={styles.entry}>
              <div style={styles.entryContent}>
                <span style={styles.entryText}>{task.description}</span>
                <span style={styles.entryMeta}>
                  {task.type === 'recurring' ? `Recurring: ${task.cronPattern}` : ''}
                  {task.type === 'once' && task.triggerTime
                    ? `Once: ${new Date(task.triggerTime).toLocaleString()}`
                    : ''}
                  {' · '}
                  {task.action}: {task.payload}
                </span>
              </div>
              <button onClick={() => onDelete(task.id)} style={styles.deleteBtn} title="Delete task">
                ✕
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
