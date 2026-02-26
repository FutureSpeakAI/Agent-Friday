import { contextBridge, ipcRenderer } from 'electron';

contextBridge.exposeInMainWorld('eve', {
  getApiPort: () => ipcRenderer.invoke('get-api-port'),
  getGeminiApiKey: () => ipcRenderer.invoke('get-gemini-api-key'),
  getLiveSystemInstruction: () => ipcRenderer.invoke('get-live-system-instruction'),

  mcp: {
    listTools: () => ipcRenderer.invoke('mcp:list-tools'),
    callTool: (name: string, args: Record<string, unknown>) =>
      ipcRenderer.invoke('mcp:call-tool', name, args),
    getStatus: () => ipcRenderer.invoke('mcp:get-status'),
    addServer: (config: any) => ipcRenderer.invoke('mcp:add-server', config),
  },

  memory: {
    getShortTerm: () => ipcRenderer.invoke('memory:get-short-term'),
    getMediumTerm: () => ipcRenderer.invoke('memory:get-medium-term'),
    getLongTerm: () => ipcRenderer.invoke('memory:get-long-term'),
    updateShortTerm: (messages: Array<{ role: string; content: string }>) =>
      ipcRenderer.invoke('memory:update-short-term', messages),
    extract: (history: Array<{ role: string; content: string }>) =>
      ipcRenderer.invoke('memory:extract', history),
    updateLongTerm: (id: string, updates: Record<string, unknown>) =>
      ipcRenderer.invoke('memory:update-long-term', id, updates),
    deleteLongTerm: (id: string) => ipcRenderer.invoke('memory:delete-long-term', id),
    deleteMediumTerm: (id: string) => ipcRenderer.invoke('memory:delete-medium-term', id),
    addImmediate: (fact: string, category: string) =>
      ipcRenderer.invoke('memory:add-immediate', fact, category),
  },

  desktop: {
    listTools: () => ipcRenderer.invoke('desktop:list-tools'),
    callTool: (name: string, args: Record<string, unknown>) =>
      ipcRenderer.invoke('desktop:call-tool', name, args),
    focusWindow: (target: string) =>
      ipcRenderer.invoke('desktop:focus-window', target),
  },

  browser: {
    listTools: () => ipcRenderer.invoke('browser:list-tools'),
    callTool: (name: string, args: Record<string, unknown>) =>
      ipcRenderer.invoke('browser:call-tool', name, args),
  },

  sessionHealth: {
    get: () => ipcRenderer.invoke('session-health:get'),
    reset: () => ipcRenderer.invoke('session-health:reset'),
    sessionStarted: () => ipcRenderer.invoke('session-health:session-started'),
    recordToolCall: (name: string, success: boolean, durationMs: number) =>
      ipcRenderer.invoke('session-health:record-tool-call', name, success, durationMs),
    recordError: (source: string, message: string) =>
      ipcRenderer.invoke('session-health:record-error', source, message),
    recordWsClose: (code: number, reason: string) =>
      ipcRenderer.invoke('session-health:record-ws-close', code, reason),
    recordReconnect: (type: 'preemptive' | 'auto-retry', success: boolean) =>
      ipcRenderer.invoke('session-health:record-reconnect', type, success),
    recordVoiceAnchor: () => ipcRenderer.invoke('session-health:record-voice-anchor'),
    recordPromptSize: (chars: number) =>
      ipcRenderer.invoke('session-health:record-prompt-size', chars),
    onUpdate: (callback: (summary: Record<string, unknown>) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, summary: Record<string, unknown>) => callback(summary);
      ipcRenderer.on('session-health:update', handler);
      return () => {
        ipcRenderer.removeListener('session-health:update', handler);
      };
    },
  },

  scheduler: {
    listTools: () => ipcRenderer.invoke('scheduler:list-tools'),
    createTask: (params: Record<string, unknown>) =>
      ipcRenderer.invoke('scheduler:create-task', params),
    listTasks: () => ipcRenderer.invoke('scheduler:list-tasks'),
    deleteTask: (id: string) => ipcRenderer.invoke('scheduler:delete-task', id),
    onTaskFired: (callback: (task: { id: string; description: string; action: string; payload: string }) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, task: { id: string; description: string; action: string; payload: string }) => callback(task);
      ipcRenderer.on('scheduler:task-fired', handler);
      return () => {
        ipcRenderer.removeListener('scheduler:task-fired', handler);
      };
    },
  },

  predictor: {
    recordInteraction: () => ipcRenderer.invoke('predictor:record-interaction'),
    onSuggestion: (callback: (suggestion: { type: string; message: string; confidence: number }) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, suggestion: { type: string; message: string; confidence: number }) => callback(suggestion);
      ipcRenderer.on('predictor:suggestion', handler);
      return () => {
        ipcRenderer.removeListener('predictor:suggestion', handler);
      };
    },
  },

  onboarding: {
    isFirstRun: () => ipcRenderer.invoke('onboarding:is-first-run') as Promise<boolean>,
    isComplete: () => ipcRenderer.invoke('onboarding:is-complete') as Promise<boolean>,
    getAgentConfig: () => ipcRenderer.invoke('onboarding:get-config') as Promise<{
      agentName: string;
      agentVoice: string;
      agentGender: string;
      agentAccent: string;
      agentBackstory: string;
      agentTraits: string[];
      agentIdentityLine: string;
      userName: string;
      onboardingComplete: boolean;
    }>,
    getToolDeclarations: () => ipcRenderer.invoke('onboarding:get-tool-declarations') as Promise<Array<{
      name: string;
      description: string;
      parameters: Record<string, unknown>;
    }>>,
    getFirstGreeting: () => ipcRenderer.invoke('onboarding:get-first-greeting') as Promise<string>,
    finalizeAgent: (config: Record<string, unknown>) =>
      ipcRenderer.invoke('onboarding:finalize-agent', config) as Promise<{ success: boolean }>,
  },

  intelligence: {
    getBriefing: () => ipcRenderer.invoke('intelligence:get-briefing') as Promise<string>,
    listAll: () => ipcRenderer.invoke('intelligence:list-all') as Promise<Array<{
      id: string; topic: string; content: string; createdAt: number;
      delivered: boolean; priority: 'high' | 'medium' | 'low';
    }>>,
    setup: (topics: Array<{ topic: string; schedule: string; priority: string }>) =>
      ipcRenderer.invoke('intelligence:setup', topics) as Promise<string>,
  },

  screenCapture: {
    start: () => ipcRenderer.invoke('screen-capture:start'),
    stop: () => ipcRenderer.invoke('screen-capture:stop'),
    onFrame: (callback: (frame: string) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, frame: string) => callback(frame);
      ipcRenderer.on('screen-capture:frame', handler);
      return () => {
        ipcRenderer.removeListener('screen-capture:frame', handler);
      };
    },
  },

  settings: {
    get: () => ipcRenderer.invoke('settings:get'),
    setAutoLaunch: (enabled: boolean) => ipcRenderer.invoke('settings:set-auto-launch', enabled),
    setAutoScreenCapture: (enabled: boolean) => ipcRenderer.invoke('settings:set-auto-screen-capture', enabled),
    setApiKey: (key: 'gemini' | 'anthropic' | 'elevenlabs' | 'firecrawl' | 'perplexity' | 'openai' | 'openrouter', value: string) =>
      ipcRenderer.invoke('settings:set-api-key', key, value),
    setObsidianVaultPath: (vaultPath: string) =>
      ipcRenderer.invoke('settings:set-obsidian-vault-path', vaultPath),
    set: (key: string, value: unknown) => ipcRenderer.invoke('settings:set', key, value),
  },

  ambient: {
    getState: () => ipcRenderer.invoke('ambient:get-state'),
    getContextString: () => ipcRenderer.invoke('ambient:get-context-string'),
  },

  episodic: {
    create: (
      transcript: Array<{ role: string; text: string }>,
      startTime: number,
      endTime: number
    ) => ipcRenderer.invoke('episodic:create', transcript, startTime, endTime),
    list: () => ipcRenderer.invoke('episodic:list'),
    search: (query: string) => ipcRenderer.invoke('episodic:search', query),
    get: (id: string) => ipcRenderer.invoke('episodic:get', id),
    delete: (id: string) => ipcRenderer.invoke('episodic:delete', id),
    recent: (count?: number) => ipcRenderer.invoke('episodic:recent', count || 5),
  },

  search: {
    query: (query: string, options?: Record<string, unknown>) =>
      ipcRenderer.invoke('search:query', query, options),
    stats: () => ipcRenderer.invoke('search:stats'),
  },

  notifications: {
    getRecent: () => ipcRenderer.invoke('notifications:get-recent'),
    onCaptured: (callback: (notif: { app: string; title: string; body: string; timestamp: number }) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, notif: { app: string; title: string; body: string; timestamp: number }) => callback(notif);
      ipcRenderer.on('notification:captured', handler);
      return () => {
        ipcRenderer.removeListener('notification:captured', handler);
      };
    },
  },

  agents: {
    spawn: (agentType: string, description: string, input: Record<string, unknown>) =>
      ipcRenderer.invoke('agents:spawn', agentType, description, input),
    list: (status?: string) => ipcRenderer.invoke('agents:list', status),
    get: (taskId: string) => ipcRenderer.invoke('agents:get', taskId),
    cancel: (taskId: string) => ipcRenderer.invoke('agents:cancel', taskId),
    getTypes: () => ipcRenderer.invoke('agents:types'),
    onUpdate: (callback: (task: Record<string, unknown>) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, task: Record<string, unknown>) => callback(task);
      ipcRenderer.on('agents:update', handler);
      return () => {
        ipcRenderer.removeListener('agents:update', handler);
      };
    },
    onSpeak: (callback: (data: {
      taskId: string;
      personaId: string;
      personaName: string;
      personaRole: string;
      audioBase64: string;
      contentType: string;
      durationEstimate: number;
      spokenText: string;
    }) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: any) => callback(data);
      ipcRenderer.on('agents:speak', handler);
      return () => {
        ipcRenderer.removeListener('agents:speak', handler);
      };
    },
  },

  sentiment: {
    analyse: (text: string) => ipcRenderer.invoke('sentiment:analyse', text),
    getState: () => ipcRenderer.invoke('sentiment:get-state'),
    getMoodLog: () => ipcRenderer.invoke('sentiment:get-mood-log'),
  },

  confirmation: {
    onRequest: (callback: (req: { id: string; toolName: string; description: string }) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, req: { id: string; toolName: string; description: string }) => callback(req);
      ipcRenderer.on('desktop:confirm-request', handler);
      return () => {
        ipcRenderer.removeListener('desktop:confirm-request', handler);
      };
    },
    respond: (id: string, approved: boolean) =>
      ipcRenderer.invoke('desktop:confirm-response', id, approved),
  },

  selfImprove: {
    readFile: (filePath: string) => ipcRenderer.invoke('self-improve:read-file', filePath),
    listFiles: (dirPath: string) => ipcRenderer.invoke('self-improve:list-files', dirPath),
    proposeChange: (filePath: string, newContent: string, description: string) =>
      ipcRenderer.invoke('self-improve:propose', filePath, newContent, description),
    onProposal: (callback: (proposal: { id: string; filePath: string; description: string; diff: string }) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, proposal: { id: string; filePath: string; description: string; diff: string }) => callback(proposal);
      ipcRenderer.on('self-improve:propose', handler);
      return () => {
        ipcRenderer.removeListener('self-improve:propose', handler);
      };
    },
    respondToProposal: (id: string, approved: boolean) =>
      ipcRenderer.invoke('self-improve:respond', id, approved),
  },

  clipboard: {
    getRecent: (count?: number) => ipcRenderer.invoke('clipboard:get-recent', count),
    getCurrent: () => ipcRenderer.invoke('clipboard:get-current'),
    onChanged: (callback: (entry: { type: string; preview: string; timestamp: number }) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, entry: { type: string; preview: string; timestamp: number }) => callback(entry);
      ipcRenderer.on('clipboard:changed', handler);
      return () => {
        ipcRenderer.removeListener('clipboard:changed', handler);
      };
    },
  },

  project: {
    watch: (rootPath: string) => ipcRenderer.invoke('project:watch', rootPath),
    list: () => ipcRenderer.invoke('project:list'),
    get: (rootPath: string) => ipcRenderer.invoke('project:get', rootPath),
    onUpdated: (callback: (profile: Record<string, unknown>) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, profile: Record<string, unknown>) => callback(profile);
      ipcRenderer.on('project:updated', handler);
      return () => {
        ipcRenderer.removeListener('project:updated', handler);
      };
    },
  },

  documents: {
    pickAndIngest: () => ipcRenderer.invoke('documents:pick-and-ingest'),
    ingestFile: (filePath: string) => ipcRenderer.invoke('documents:ingest-file', filePath),
    list: () => ipcRenderer.invoke('documents:list'),
    get: (id: string) => ipcRenderer.invoke('documents:get', id),
    search: (query: string) => ipcRenderer.invoke('documents:search', query),
  },

  calendar: {
    authenticate: () => ipcRenderer.invoke('calendar:authenticate'),
    isAuthenticated: () => ipcRenderer.invoke('calendar:is-authenticated'),
    getUpcoming: (count?: number) => ipcRenderer.invoke('calendar:get-upcoming', count),
    getToday: () => ipcRenderer.invoke('calendar:get-today'),
    createEvent: (opts: {
      summary: string;
      description?: string;
      startTime: string;
      endTime: string;
      attendees?: string[];
      location?: string;
    }) => ipcRenderer.invoke('calendar:create-event', opts),
  },

  meetingPrep: {
    onBriefing: (callback: (briefing: {
      eventId: string;
      eventTitle: string;
      startTime: string;
      minutesUntil: number;
      attendeeContext: Array<{ name: string; memories: string[]; recentTopics: string[] }>;
      relevantProjects: string[];
      suggestedTopics: string[];
      briefingText: string;
    }) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, briefing: any) => callback(briefing);
      ipcRenderer.on('meeting:briefing', handler);
      return () => {
        ipcRenderer.removeListener('meeting:briefing', handler);
      };
    },
  },

  communications: {
    draft: (request: {
      type: 'email' | 'message' | 'reply' | 'follow-up';
      to: string;
      subject?: string;
      context: string;
      tone?: 'formal' | 'casual' | 'friendly' | 'professional' | 'urgent';
      originalMessage?: string;
      maxLength?: 'short' | 'medium' | 'long';
    }) => ipcRenderer.invoke('communications:draft', request),
    refine: (draftId: string, instruction: string) =>
      ipcRenderer.invoke('communications:refine', draftId, instruction),
    copy: (draftId: string) => ipcRenderer.invoke('communications:copy', draftId),
    openEmail: (draftId: string) => ipcRenderer.invoke('communications:open-email', draftId),
    listDrafts: () => ipcRenderer.invoke('communications:list-drafts'),
  },

  psychProfile: {
    generate: (responses: { voicePreference: string; socialDescription: string; motherRelationship: string }) =>
      ipcRenderer.invoke('psych:generate', responses),
    get: () => ipcRenderer.invoke('psych:get'),
    saveIntakeResponses: (responses: { voicePreference: string; socialDescription: string; motherRelationship: string }) =>
      ipcRenderer.invoke('psych:save-intake', responses),
    getIntakeResponses: () => ipcRenderer.invoke('psych:get-intake'),
  },

  featureSetup: {
    initialize: () => ipcRenderer.invoke('feature-setup:initialize'),
    getState: () => ipcRenderer.invoke('feature-setup:get-state'),
    getPrompt: (step: string) => ipcRenderer.invoke('feature-setup:get-prompt', step),
    advance: (step: string, action: 'complete' | 'skip') =>
      ipcRenderer.invoke('feature-setup:advance', step, action),
    isComplete: () => ipcRenderer.invoke('feature-setup:is-complete'),
    getCurrentStep: () => ipcRenderer.invoke('feature-setup:get-current-step'),
    getToolDeclaration: () => ipcRenderer.invoke('feature-setup:get-tool-declaration'),
    getToolDeclarations: () => ipcRenderer.invoke('feature-setup:get-tool-declarations') as Promise<Array<{
      name: string;
      description: string;
      parameters: Record<string, unknown>;
    }>>,
  },

  evolution: {
    getState: () => ipcRenderer.invoke('evolution:get-state'),
    incrementSession: () => ipcRenderer.invoke('evolution:increment-session'),
  },

  voiceAudition: {
    generateSample: (voiceName: string, customPhrase?: string) =>
      ipcRenderer.invoke('voice-audition:generate-sample', voiceName, customPhrase) as Promise<{
        audio: string;
        mimeType: string;
      } | null>,
    getRecommendations: (genderPref: string) =>
      ipcRenderer.invoke('voice-audition:get-recommendations', genderPref) as Promise<Array<{
        name: string;
        gender: string;
        description: string;
      }>>,
    getCatalog: () =>
      ipcRenderer.invoke('voice-audition:get-catalog') as Promise<Array<{
        name: string;
        gender: string;
        description: string;
      }>>,
  },

  gateway: {
    getStatus: () => ipcRenderer.invoke('gateway:get-status'),
    setEnabled: (enabled: boolean) => ipcRenderer.invoke('gateway:set-enabled', enabled),
    getPendingPairings: () => ipcRenderer.invoke('gateway:get-pending-pairings'),
    getPairedIdentities: () => ipcRenderer.invoke('gateway:get-paired-identities'),
    approvePairing: (code: string, tier?: string) =>
      ipcRenderer.invoke('gateway:approve-pairing', code, tier),
    revokePairing: (identityId: string) =>
      ipcRenderer.invoke('gateway:revoke-pairing', identityId),
    getActiveSessions: () => ipcRenderer.invoke('gateway:get-active-sessions'),
  },

  gitLoader: {
    load: (repoUrl: string, options?: Record<string, unknown>) =>
      ipcRenderer.invoke('git:load', repoUrl, options),
    getTree: (repoId: string) => ipcRenderer.invoke('git:get-tree', repoId),
    getFile: (repoId: string, filePath: string) =>
      ipcRenderer.invoke('git:get-file', repoId, filePath),
    search: (repoId: string, query: string, options?: Record<string, unknown>) =>
      ipcRenderer.invoke('git:search', repoId, query, options),
    getReadme: (repoId: string) => ipcRenderer.invoke('git:get-readme', repoId),
    getSummary: (repoId: string) => ipcRenderer.invoke('git:get-summary', repoId),
    listLoaded: () => ipcRenderer.invoke('git:list-loaded'),
    unload: (repoId: string) => ipcRenderer.invoke('git:unload', repoId),
    listTools: () => ipcRenderer.invoke('git:list-tools'),
    callTool: (name: string, args: Record<string, unknown>) =>
      ipcRenderer.invoke('git:call-tool', name, args),
  },

  soc: {
    listTools: () => ipcRenderer.invoke('soc:list-tools'),
    callTool: (name: string, args: Record<string, unknown>) =>
      ipcRenderer.invoke('soc:call-tool', name, args),
    checkDeps: () => ipcRenderer.invoke('soc:check-deps'),
    startBridge: () => ipcRenderer.invoke('soc:start-bridge'),
    stopBridge: () => ipcRenderer.invoke('soc:stop-bridge'),
    bridgeStatus: () => ipcRenderer.invoke('soc:bridge-status'),
  },

  connectors: {
    listTools: () => ipcRenderer.invoke('connectors:list-tools'),
    callTool: (name: string, args: Record<string, unknown>) =>
      ipcRenderer.invoke('connectors:call-tool', name, args),
    isConnectorTool: (name: string) => ipcRenderer.invoke('connectors:is-connector-tool', name),
    status: () => ipcRenderer.invoke('connectors:status'),
    getToolRouting: () => ipcRenderer.invoke('connectors:get-tool-routing'),
  },

  callIntegration: {
    isVirtualAudioAvailable: () => ipcRenderer.invoke('call:is-virtual-audio-available'),
    enterCallMode: (meetingUrl?: string) => ipcRenderer.invoke('call:enter-call-mode', meetingUrl),
    exitCallMode: () => ipcRenderer.invoke('call:exit-call-mode'),
    isInCallMode: () => ipcRenderer.invoke('call:is-in-call-mode'),
    openMeetingUrl: (url: string) => ipcRenderer.invoke('call:open-meeting-url', url),
    getContextString: () => ipcRenderer.invoke('call:get-context-string'),
  },

  integrity: {
    getState: () => ipcRenderer.invoke('integrity:get-state') as Promise<{
      initialized: boolean;
      lawsIntact: boolean;
      identityIntact: boolean;
      memoriesIntact: boolean;
      safeMode: boolean;
      safeModeReason: string | null;
      lastVerified: number;
      memoryChanges: {
        longTermAdded: string[];
        longTermRemoved: string[];
        longTermModified: string[];
        mediumTermAdded: string[];
        mediumTermRemoved: string[];
        mediumTermModified: string[];
        detectedAt: number;
        acknowledged: boolean;
      } | null;
    }>,
    isInSafeMode: () => ipcRenderer.invoke('integrity:is-safe-mode') as Promise<boolean>,
    acknowledgeMemoryChanges: () => ipcRenderer.invoke('integrity:acknowledge-memory-changes'),
    verify: () => ipcRenderer.invoke('integrity:verify') as Promise<{
      lawsIntact: boolean;
      identityIntact: boolean;
      memoriesIntact: boolean;
      safeMode: boolean;
    }>,
  },

  shell: {
    showInFolder: (filePath: string) => ipcRenderer.invoke('shell:show-in-folder', filePath),
    openPath: (filePath: string) => ipcRenderer.invoke('shell:open-path', filePath),
  },

  office: {
    getState: () => ipcRenderer.invoke('office:get-state'),
    isOpen: () => ipcRenderer.invoke('office:is-open'),
    requestOpen: () => ipcRenderer.send('office:request-open'),
    requestClose: () => ipcRenderer.send('office:request-close'),
    onFullState: (callback: (state: any) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, state: any) => callback(state);
      ipcRenderer.on('office:full-state', handler);
      return () => { ipcRenderer.removeListener('office:full-state', handler); };
    },
    onAgentSpawned: (callback: (character: any) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, character: any) => callback(character);
      ipcRenderer.on('office:agent-spawned', handler);
      return () => { ipcRenderer.removeListener('office:agent-spawned', handler); };
    },
    onAgentThought: (callback: (data: any) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: any) => callback(data);
      ipcRenderer.on('office:agent-thought', handler);
      return () => { ipcRenderer.removeListener('office:agent-thought', handler); };
    },
    onAgentPhase: (callback: (data: any) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: any) => callback(data);
      ipcRenderer.on('office:agent-phase', handler);
      return () => { ipcRenderer.removeListener('office:agent-phase', handler); };
    },
    onAgentCompleted: (callback: (data: any) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: any) => callback(data);
      ipcRenderer.on('office:agent-completed', handler);
      return () => { ipcRenderer.removeListener('office:agent-completed', handler); };
    },
    onAgentStopped: (callback: (data: any) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: any) => callback(data);
      ipcRenderer.on('office:agent-stopped', handler);
      return () => { ipcRenderer.removeListener('office:agent-stopped', handler); };
    },
    onAgentRemoved: (callback: (data: any) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: any) => callback(data);
      ipcRenderer.on('office:agent-removed', handler);
      return () => { ipcRenderer.removeListener('office:agent-removed', handler); };
    },
  },

  onFileModified: (callback: (data: { path: string; action: string; size: number; timestamp: number }) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, data: any) => callback(data);
    ipcRenderer.on('file:modified', handler);
    return () => { ipcRenderer.removeListener('file:modified', handler); };
  },

  window: {
    minimize: () => ipcRenderer.invoke('window:minimize'),
    maximize: () => ipcRenderer.invoke('window:maximize'),
    close: () => ipcRenderer.invoke('window:close'),
  },
});
