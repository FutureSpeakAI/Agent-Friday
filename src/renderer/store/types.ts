// ── Store types — extracted from App.tsx ──────────────────────────────────────

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  model?: string;
  timestamp: number;
}

export interface ConfirmationRequest {
  id: string;
  toolName: string;
  description: string;
}

export interface CodeProposal {
  id: string;
  filePath: string;
  description: string;
  diff: string;
}

export type ConnectionStatus = 'connected' | 'connecting' | 'offline' | 'no-key';

export interface ApiStatus {
  gemini: 'connected' | 'connecting' | 'offline' | 'no-key';
  claude: 'ready' | 'no-key';
  elevenlabs: 'ready' | 'no-key';
  openrouter: 'ready' | 'no-key';
  browser: 'ready' | 'unavailable';
}

export type AppPhase = 'checking' | 'passphrase-gate' | 'onboarding' | 'creating' | 'normal';

export interface EvolutionState {
  sessionCount: number;
  primaryHue: number;
  secondaryHue: number;
  particleSpeed: number;
  cubeFragmentation: number;
  coreScale: number;
  dustDensity: number;
  glowIntensity: number;
}
