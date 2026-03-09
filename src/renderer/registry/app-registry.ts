/**
 * app-registry.ts — Central App Registry for Agent Friday OS
 *
 * Maps all 25 apps to their metadata, icons, categories, shortcuts,
 * and lazy-loaded React components. Phase 0 infrastructure.
 */

import React from 'react';

// ── Shared app component props ─────────────────────────────────────────────
export interface AppProps {
  visible: boolean;
  onClose: () => void;
}

// ── App definition ─────────────────────────────────────────────────────────
export interface AppDefinition {
  id: string;
  displayName: string;
  icon: string;
  category: 'productivity' | 'communication' | 'media' | 'tools' | 'system';
  shortcut?: string;
  component: React.LazyExoticComponent<React.ComponentType<AppProps>>;
}

// ── Category metadata ──────────────────────────────────────────────────────
export const APP_CATEGORIES: Record<string, { label: string; order: number }> = {
  productivity: { label: 'PRODUCTIVITY', order: 0 },
  communication: { label: 'COMMS', order: 1 },
  media: { label: 'MEDIA', order: 2 },
  tools: { label: 'TOOLS', order: 3 },
  system: { label: 'SYSTEM', order: 4 },
};

// ── The Registry ───────────────────────────────────────────────────────────
// Each component is lazy-loaded so the main bundle stays lean.

export const APP_REGISTRY: AppDefinition[] = [
  // ─── Productivity ──────────────────────────────────────────────────────
  {
    id: 'dashboard',
    displayName: 'Command',
    icon: '◈',
    category: 'productivity',
    shortcut: 'Ctrl+Shift+D',
    component: React.lazy(() => import('../components/Dashboard')),
  },
  {
    id: 'calendar',
    displayName: 'Calendar',
    icon: '📅',
    category: 'productivity',
    shortcut: 'Ctrl+Shift+C',
    component: React.lazy(() => import('../components/apps/FridayCalendar')),
  },
  {
    id: 'tasks',
    displayName: 'Tasks',
    icon: '✅',
    category: 'productivity',
    component: React.lazy(() => import('../components/apps/FridayTasks')),
  },
  {
    id: 'notes',
    displayName: 'Notes',
    icon: '📝',
    category: 'productivity',
    component: React.lazy(() => import('../components/apps/FridayNotes')),
  },
  {
    id: 'docs',
    displayName: 'Docs',
    icon: '📄',
    category: 'productivity',
    component: React.lazy(() => import('../components/apps/FridayDocs')),
  },
  {
    id: 'contacts',
    displayName: 'Contacts',
    icon: '👤',
    category: 'productivity',
    component: React.lazy(() => import('../components/apps/FridayContacts')),
  },

  // ─── Communication ─────────────────────────────────────────────────────
  {
    id: 'comms',
    displayName: 'Mail',
    icon: '✉️',
    category: 'communication',
    component: React.lazy(() => import('../components/apps/FridayComms')),
  },
  {
    id: 'gateway',
    displayName: 'Gateway',
    icon: '🔗',
    category: 'communication',
    component: React.lazy(() => import('../components/apps/FridayGateway')),
  },
  {
    id: 'news',
    displayName: 'News',
    icon: '📰',
    category: 'communication',
    component: React.lazy(() => import('../components/apps/FridayNews')),
  },

  // ─── Media ─────────────────────────────────────────────────────────────
  {
    id: 'media',
    displayName: 'Media',
    icon: '🎬',
    category: 'media',
    component: React.lazy(() => import('../components/apps/FridayMedia')),
  },
  {
    id: 'gallery',
    displayName: 'Gallery',
    icon: '🖼️',
    category: 'media',
    component: React.lazy(() => import('../components/apps/FridayGallery')),
  },
  {
    id: 'camera',
    displayName: 'Camera',
    icon: '📷',
    category: 'media',
    component: React.lazy(() => import('../components/apps/FridayCamera')),
  },
  {
    id: 'recorder',
    displayName: 'Recorder',
    icon: '🎙️',
    category: 'media',
    component: React.lazy(() => import('../components/apps/FridayRecorder')),
  },
  {
    id: 'canvas',
    displayName: 'Canvas',
    icon: '🎨',
    category: 'media',
    component: React.lazy(() => import('../components/apps/FridayCanvas')),
  },
  {
    id: 'stage',
    displayName: 'Stage',
    icon: '🎭',
    category: 'media',
    shortcut: 'Ctrl+Shift+G',
    component: React.lazy(() => import('../components/apps/FridayStage')),
  },

  // ─── Tools ─────────────────────────────────────────────────────────────
  {
    id: 'browser',
    displayName: 'Browser',
    icon: '🌐',
    category: 'tools',
    component: React.lazy(() => import('../components/apps/FridayBrowser')),
  },
  {
    id: 'terminal',
    displayName: 'Terminal',
    icon: '⌨️',
    category: 'tools',
    component: React.lazy(() => import('../components/apps/FridayTerminal')),
  },
  {
    id: 'files',
    displayName: 'Files',
    icon: '📁',
    category: 'tools',
    component: React.lazy(() => import('../components/apps/FridayFiles')),
  },
  {
    id: 'calc',
    displayName: 'Calculator',
    icon: '🔢',
    category: 'tools',
    component: React.lazy(() => import('../components/apps/FridayCalc')),
  },
  {
    id: 'code',
    displayName: 'Code',
    icon: '💻',
    category: 'tools',
    component: React.lazy(() => import('../components/apps/FridayCode')),
  },
  {
    id: 'maps',
    displayName: 'Maps',
    icon: '🗺️',
    category: 'tools',
    component: React.lazy(() => import('../components/apps/FridayMaps')),
  },

  // ─── System ────────────────────────────────────────────────────────────
  {
    id: 'settings',
    displayName: 'Settings',
    icon: '⚙️',
    category: 'system',
    component: React.lazy(() => import('../components/Settings')),
  },
  {
    id: 'superpowers',
    displayName: 'Powers',
    icon: '🔮',
    category: 'system',
    shortcut: 'Ctrl+Shift+P',
    component: React.lazy(() => import('../components/SuperpowersPanel')),
  },
  {
    id: 'agents',
    displayName: 'Agents',
    icon: '⚡',
    category: 'system',
    shortcut: 'Ctrl+Shift+A',
    component: React.lazy(() => import('../components/AgentDashboard')),
  },
  {
    id: 'memory',
    displayName: 'Memory',
    icon: '🧠',
    category: 'system',
    shortcut: 'Ctrl+Shift+M',
    component: React.lazy(() => import('../components/MemoryExplorer')),
  },
  {
    id: 'monitor',
    displayName: 'Monitor',
    icon: '📊',
    category: 'system',
    component: React.lazy(() => import('../components/apps/FridayMonitor')),
  },
  {
    id: 'forge',
    displayName: 'Forge',
    icon: '🛠️',
    category: 'system',
    component: React.lazy(() => import('../components/apps/FridayForge')),
  },
  {
    id: 'weather',
    displayName: 'Weather',
    icon: '🌤️',
    category: 'system',
    component: React.lazy(() => import('../components/apps/FridayWeather')),
  },
];

// ── Lookup helpers ─────────────────────────────────────────────────────────
export function getAppById(id: string): AppDefinition | undefined {
  return APP_REGISTRY.find((a) => a.id === id);
}

export function getAppsByCategory(category: string): AppDefinition[] {
  return APP_REGISTRY.filter((a) => a.category === category);
}
