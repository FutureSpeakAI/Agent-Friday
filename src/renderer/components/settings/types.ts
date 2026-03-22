export type Tab = 'general' | 'memory' | 'tasks' | 'localai';

export interface SettingsProps {
  visible: boolean;
  onClose: () => void;
}

export interface MaskedSettings {
  autoLaunch: boolean;
  autoScreenCapture: boolean;
  obsidianVaultPath: string;
  hasGeminiKey: boolean;
  hasAnthropicKey: boolean;
  hasElevenLabsKey: boolean;
  hasOpenaiKey: boolean;
  hasPerplexityKey: boolean;
  hasFirecrawlKey: boolean;
  hasOpenrouterKey: boolean;
  geminiKeyHint: string;
  anthropicKeyHint: string;
  elevenLabsKeyHint: string;
  openaiKeyHint: string;
  perplexityKeyHint: string;
  firecrawlKeyHint: string;
  openrouterKeyHint: string;
  preferredProvider: 'anthropic' | 'openrouter' | 'local';
  openrouterModel: string;
  localModelEnabled: boolean;
  localInferenceEndpoint: string;
  localModelId: string;
  hasHuggingfaceKey: boolean;
  huggingfaceKeyHint: string;
  agentVoicesEnabled: boolean;
  wakeWordEnabled: boolean;
  notificationWhisperEnabled: boolean;
  notificationAllowedApps: string[];
  clipboardIntelligenceEnabled: boolean;
  googleCalendarEnabled: boolean;
  gatewayEnabled: boolean;
  hasTelegramToken: boolean;
  telegramOwnerId: string;
  hasDiscordToken: boolean;
  discordOwnerId: string;
  worldMonitorPath: string;
}

export interface LongTermEntry {
  id: string;
  fact: string;
  category: string;
  confirmed: boolean;
  source: string;
}

export interface MediumTermEntry {
  id: string;
  observation: string;
  category: string;
  confidence: number;
  occurrences: number;
}

export interface TaskEntry {
  id: string;
  description: string;
  type: string;
  action: string;
  payload: string;
  enabled: boolean;
  triggerTime?: number;
  cronPattern?: string;
}
