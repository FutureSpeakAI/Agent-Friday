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
export { registerContextPushHandlers } from './context-push-handlers';
export type { ContextPushCleanup } from './context-push-handlers';
export { registerContextToolRouterHandlers } from './context-tool-router-handlers';
export { registerCommitmentTrackerHandlers } from './commitment-tracker-handlers';
export { registerDailyBriefingHandlers } from './daily-briefing-handlers';
export { registerBriefingDeliveryHandlers } from './briefing-delivery-handlers';
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
export { registerCodeExecutionHandlers } from './code-execution-handlers';
export { registerExecutionDelegateHandlers } from './execution-delegate-handlers';
export { registerDelegationEngineHandlers } from './delegation-engine-handlers';
export { registerOsPrimitivesHandlers } from './os-primitives-handlers';
export { registerAppContextHandlers } from './app-context-handlers';
export { registerNotesHandlers } from './notes-handlers';
export { registerFilesHandlers } from './files-handlers';
export { registerWeatherHandlers } from './weather-handlers';
export { registerSystemMonitorHandlers } from './system-monitor-handlers';

// ── Sprint 7: Integration Wiring — Sprint 3-6 module handlers ─────
export { registerHardwareHandlers } from './hardware-handlers';
export type { HardwareHandlerDeps } from './hardware-handlers';
export { registerSetupHandlers } from './setup-handlers';
export type { SetupHandlerDeps } from './setup-handlers';
export { registerOllamaHandlers } from './ollama-handlers';
export type { OllamaHandlerDeps } from './ollama-handlers';
export { registerVoicePipelineHandlers } from './voice-pipeline-handlers';
export type { VoicePipelineHandlerDeps } from './voice-pipeline-handlers';
export { registerVisionPipelineHandlers } from './vision-pipeline-handlers';
export type { VisionPipelineHandlerDeps } from './vision-pipeline-handlers';
export { registerChatHistoryHandlers } from './chat-history-handlers';
export { registerLocalConversationHandlers } from './local-conversation-handlers';
export type { LocalConversationHandlerDeps } from './local-conversation-handlers';
export { registerVoiceStateHandlers } from './voice-state-handlers';
export type { VoiceStateHandlerDeps } from './voice-state-handlers';
export { registerVoiceFallbackHandlers } from './voice-fallback-handlers';
export type { VoiceFallbackHandlerDeps } from './voice-fallback-handlers';
export { registerConnectionStageHandlers } from './connection-stage-handlers';
export type { ConnectionStageHandlerDeps } from './connection-stage-handlers';
export { registerTelemetryHandlers } from './telemetry-handlers';
