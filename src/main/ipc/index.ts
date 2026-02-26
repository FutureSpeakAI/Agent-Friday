/**
 * IPC handler registry — barrel export for all domain handler modules.
 */
export { registerCoreHandlers } from './core-handlers';
export type { CoreHandlerDeps } from './core-handlers';

export { registerMemoryHandlers } from './memory-handlers';
export { registerToolHandlers } from './tool-handlers';
export type { ToolHandlerDeps } from './tool-handlers';

export { registerAgentHandlers } from './agent-handlers';
export { registerOnboardingHandlers } from './onboarding-handlers';
export { registerIntegrationHandlers } from './integration-handlers';
export { registerIntegrityHandlers } from './integrity-handlers';
export { registerSuperpowersHandlers } from './superpowers-handlers';
export { registerTrustGraphHandlers } from './trust-graph-handlers';
export { registerMeetingIntelligenceHandlers } from './meeting-intelligence-handlers';
