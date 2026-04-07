/**
 * ThemeContext.tsx — React context provider for the theme engine.
 * Bridges ThemeEngine with React component tree.
 */

import React, { createContext, useContext, useEffect, useState, useCallback } from 'react';
import { themeEngine, type ResolvedTheme } from './ThemeEngine';

interface ThemeContextValue {
  /** Current theme name */
  themeName: string;
  /** Current resolved theme */
  theme: ResolvedTheme | null;
  /** Switch to a different theme */
  setTheme: (name: string) => void;
  /** Set the mood overlay */
  setMood: (mood: string | null) => void;
  /** Current mood */
  mood: string | null;
  /** Get a specific token value */
  token: (key: string) => string;
  /** List available themes */
  availableThemes: string[];
}

const ThemeContext = createContext<ThemeContextValue>({
  themeName: 'nexus-dark',
  theme: null,
  setTheme: () => {},
  setMood: () => {},
  mood: null,
  token: () => '',
  availableThemes: [],
});

export function ThemeProvider({ children, defaultTheme = 'nexus-dark' }: { children: React.ReactNode; defaultTheme?: string }) {
  const [theme, setThemeState] = useState<ResolvedTheme | null>(null);
  const [themeName, setThemeName] = useState(defaultTheme);
  const [mood, setMoodState] = useState<string | null>(null);

  // Initialize theme on mount
  useEffect(() => {
    themeEngine.activate(defaultTheme);
    setThemeState(themeEngine.getActive());
  }, [defaultTheme]);

  // Listen for theme changes
  useEffect(() => {
    return themeEngine.onChange((resolved) => {
      setThemeState(resolved);
      setThemeName(resolved.name);
    });
  }, []);

  const setTheme = useCallback((name: string) => {
    themeEngine.activate(name);
    setThemeName(name);
  }, []);

  const setMood = useCallback((m: string | null) => {
    themeEngine.setMood(m);
    setMoodState(m);
  }, []);

  const token = useCallback((key: string): string => {
    return themeEngine.getToken(key) ?? '';
  }, [theme, mood]); // Re-derive when theme or mood changes

  const value: ThemeContextValue = {
    themeName,
    theme,
    setTheme,
    setMood,
    mood,
    token,
    availableThemes: themeEngine.list(),
  };

  return (
    <ThemeContext.Provider value={value}>
      {children}
    </ThemeContext.Provider>
  );
}

/** Hook to access theme context */
export function useTheme(): ThemeContextValue {
  return useContext(ThemeContext);
}
