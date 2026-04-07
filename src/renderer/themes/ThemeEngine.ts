/**
 * ThemeEngine.ts — JSON token-based theme system for Agent Friday.
 *
 * Inspired by Pi coding agent's 51-token theme system, extended with
 * mood-reactive modifiers that integrate with FridayCore's personality system.
 *
 * Tokens are injected as CSS custom properties on :root, enabling
 * zero-JS theme switching for all CSS-based components.
 */

// Theme file structure
export interface ThemeFile {
  name: string;
  description?: string;
  extends: string | null;
  tokens: Record<string, string>;
  mood_modifiers?: Record<string, Record<string, string>>;
}

// Resolved theme (after inheritance)
export interface ResolvedTheme {
  name: string;
  tokens: Record<string, string>;
  moodModifiers: Record<string, Record<string, string>>;
}

// Import theme files
import nexusDark from './nexus-dark.json';
import nexusLight from './nexus-light.json';
import nexusMidnight from './nexus-midnight.json';

type ThemeChangeListener = (theme: ResolvedTheme) => void;

class ThemeEngine {
  private themes = new Map<string, ThemeFile>();
  private activeTheme: ResolvedTheme | null = null;
  private activeMood: string | null = null;
  private listeners: ThemeChangeListener[] = [];
  private styleElement: HTMLStyleElement | null = null;

  constructor() {
    // Register built-in themes
    this.register(nexusDark as ThemeFile);
    this.register(nexusLight as ThemeFile);
    this.register(nexusMidnight as ThemeFile);
  }

  /** Register a theme definition */
  register(theme: ThemeFile): void {
    this.themes.set(theme.name, theme);
  }

  /** List available theme names */
  list(): string[] {
    return Array.from(this.themes.keys());
  }

  /** Get the current active theme */
  getActive(): ResolvedTheme | null {
    return this.activeTheme;
  }

  /** Get current theme name */
  getActiveName(): string {
    return this.activeTheme?.name ?? 'nexus-dark';
  }

  /** Resolve a theme (apply inheritance chain) */
  resolve(themeName: string): ResolvedTheme {
    const theme = this.themes.get(themeName);
    if (!theme) {
      throw new Error(`[ThemeEngine] Unknown theme: '${themeName}'`);
    }

    // Build inheritance chain
    let tokens: Record<string, string> = {};
    const chain: ThemeFile[] = [];
    let current: ThemeFile | undefined = theme;

    while (current) {
      chain.unshift(current); // prepend so base is first
      current = current.extends ? this.themes.get(current.extends) : undefined;
    }

    // Apply tokens in order (base -> derived)
    for (const t of chain) {
      tokens = { ...tokens, ...t.tokens };
    }

    // Collect mood modifiers (only from the target theme, not inherited)
    const moodModifiers = theme.mood_modifiers ?? {};

    return { name: theme.name, tokens, moodModifiers };
  }

  /** Activate a theme by name, injecting CSS variables */
  activate(themeName: string): void {
    const resolved = this.resolve(themeName);
    this.activeTheme = resolved;

    // Apply mood overlay if one is active
    let finalTokens = { ...resolved.tokens };
    if (this.activeMood && resolved.moodModifiers[this.activeMood]) {
      finalTokens = { ...finalTokens, ...resolved.moodModifiers[this.activeMood] };
    }

    this.injectCSS(finalTokens);
    this.notify(resolved);
  }

  /** Set the active mood, applying mood modifiers on top of current theme */
  setMood(mood: string | null): void {
    this.activeMood = mood;
    if (this.activeTheme) {
      let finalTokens = { ...this.activeTheme.tokens };
      if (mood && this.activeTheme.moodModifiers[mood]) {
        finalTokens = { ...finalTokens, ...this.activeTheme.moodModifiers[mood] };
      }
      this.injectCSS(finalTokens);
    }
  }

  /** Get the current mood */
  getMood(): string | null {
    return this.activeMood;
  }

  /** Get a specific token value (with mood overlay applied) */
  getToken(key: string): string | undefined {
    if (!this.activeTheme) return undefined;
    // Check mood overlay first
    if (this.activeMood && this.activeTheme.moodModifiers[this.activeMood]?.[key]) {
      return this.activeTheme.moodModifiers[this.activeMood][key];
    }
    return this.activeTheme.tokens[key];
  }

  /** Subscribe to theme changes */
  onChange(listener: ThemeChangeListener): () => void {
    this.listeners.push(listener);
    return () => {
      this.listeners = this.listeners.filter(l => l !== listener);
    };
  }

  /** Inject CSS custom properties into :root */
  private injectCSS(tokens: Record<string, string>): void {
    if (!this.styleElement) {
      this.styleElement = document.createElement('style');
      this.styleElement.id = 'nexus-theme-tokens';
      document.head.appendChild(this.styleElement);
    }

    const vars = Object.entries(tokens)
      .map(([key, value]) => `  --theme-${key}: ${value};`)
      .join('\n');

    this.styleElement.textContent = `:root {\n${vars}\n}`;
  }

  private notify(theme: ResolvedTheme): void {
    for (const listener of this.listeners) {
      try {
        listener(theme);
      } catch (err) {
        console.warn('[ThemeEngine] Listener error:', err);
      }
    }
  }
}

/** Singleton theme engine instance */
export const themeEngine = new ThemeEngine();
