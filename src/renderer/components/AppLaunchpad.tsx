/**
 * AppLaunchpad.tsx — Categorized app launcher for the HUD tray
 *
 * Replaces the hardcoded 6-icon grid in HudOverlay.tsx with a
 * scrollable, categorized grid of all registered apps.
 */

import { useMemo } from 'react';
import { APP_REGISTRY, APP_CATEGORIES } from '../registry/app-registry';
import type { AppDefinition } from '../registry/app-registry';
import '../styles/app-launchpad.css';

interface AppLaunchpadProps {
  onOpenApp: (id: string) => void;
}

export default function AppLaunchpad({ onOpenApp }: AppLaunchpadProps) {
  // Group apps by category, sorted by category order
  const grouped = useMemo(() => {
    const map = new Map<string, AppDefinition[]>();
    for (const app of APP_REGISTRY) {
      const list = map.get(app.category) || [];
      list.push(app);
      map.set(app.category, list);
    }

    return Array.from(map.entries())
      .sort(([a], [b]) => {
        const oa = APP_CATEGORIES[a]?.order ?? 99;
        const ob = APP_CATEGORIES[b]?.order ?? 99;
        return oa - ob;
      })
      .map(([category, apps]) => ({
        category,
        label: APP_CATEGORIES[category]?.label || category.toUpperCase(),
        apps,
      }));
  }, []);

  return (
    <div className="launchpad">
      {grouped.map((group) => (
        <div key={group.category} className="launchpad-group">
          <div className="launchpad-category">{group.label}</div>
          <div className="launchpad-grid">
            {group.apps.map((app) => (
              <button
                key={app.id}
                className="launchpad-icon"
                onClick={() => onOpenApp(app.id)}
                title={`${app.displayName}${app.shortcut ? ` (${app.shortcut})` : ''}`}
              >
                <span className="launchpad-emoji">{app.icon}</span>
                <span className="launchpad-label">{app.displayName}</span>
              </button>
            ))}
          </div>
        </div>
      ))}

      {/* Discord community link (not an app — persistent shortcut) */}
      <div className="launchpad-group">
        <div className="launchpad-category">COMMUNITY</div>
        <div className="launchpad-grid">
          <button
            className="launchpad-icon"
            onClick={() => window.eve?.shell?.openPath?.('https://discord.gg/8af2bFqn')}
            title="Discord Community"
          >
            <span className="launchpad-emoji">💬</span>
            <span className="launchpad-label">Discord</span>
          </button>
        </div>
      </div>
    </div>
  );
}
