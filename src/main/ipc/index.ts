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
export { registerCapabilityGapHandlers } from './capability-gap-handlers';
export { registerContextStreamHandlers } from './context-stream-handlers';
export { registerContextGraphHandlers } from './context-graph-handlers';
export { registerContextToolRouterHandlers } from './context-tool-router-handlers';
export { registerCommitmentTrackerHandlers } from './commitment-tracker-handlers';
export { registerDailyBriefingHandlers } from './daily-briefing-handlers';
export { registerWorkflowRecorderHandlers } from './workflow-recorder-handlers';
export { registerWorkflowExecutorHandlers } from './workflow-executor-handlers';
export { registerUnifiedInboxHandlers } from './unified-inbox-handlers';
export { registerOutboundIntelligenceHandlers } from './outbound-intelligence-handlers';
export { registerIntelligenceRouterHandlers } from './intelligence-router-handlers';
export { registerAgentNetworkHandlers } from './agent-network-handlers';
export { registerSuperpowerEcosystemHandlers } from './superpower-ecosystem-handlers';
export { registerStateExportHandlers } from './state-export-handlers';
export { registerMemoryQualityHandlers } from './memory-quality-handlers';
export { registerPersonalityCalibrationHandlers } from './personality-calibration-handlers';
export { registerMemoryPersonalityBridgeHandlers } from './memory-personality-bridge-handlers';
export { registerAgentTrustHandlers } from './agent-trust-handlers';
export { registerMultimediaHandlers } from './multimedia-handlers';
export { registerContainerEngineHandlers } from './container-engine-handlers';
export { registerDelegationEngineHandlers } from './delegation-engine-handlers';
export { registerOsPrimitivesHandlers } from './os-primitives-handlers';
