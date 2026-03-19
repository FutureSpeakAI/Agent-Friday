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

  chatHistory: {
    load: () => ipcRenderer.invoke('chat-history:load'),
    save: (messages: Array<{ id: string; role: string; content: string; model?: string; timestamp: number }>) =>
      ipcRenderer.invoke('chat-history:save', messages),
    clear: () => ipcRenderer.invoke('chat-history:clear'),
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

  toolExecution: {
    execute: (toolCall: { id: string; type: string; name: string; input: unknown }) =>
      ipcRenderer.invoke('tool:execute', toolCall),
    confirmResponse: (decisionId: string, approved: boolean) =>
      ipcRenderer.invoke('tool:confirm-response', { decisionId, approved }),
    listTools: () => ipcRenderer.invoke('tool:list-tools'),
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
    setApiKey: (key: 'gemini' | 'anthropic' | 'elevenlabs' | 'firecrawl' | 'perplexity' | 'openai' | 'openrouter' | 'huggingface', value: string) =>
      ipcRenderer.invoke('settings:set-api-key', key, value),
    validateApiKey: (keyType: string, value: string) =>
      ipcRenderer.invoke('settings:validate-api-key', keyType, value),
    checkApiHealth: () => ipcRenderer.invoke('settings:check-api-health'),
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

  trustGraph: {
    lookup: (name: string) => ipcRenderer.invoke('trust:lookup', name),
    updateEvidence: (personName: string, evidence: Record<string, unknown>) =>
      ipcRenderer.invoke('trust:update-evidence', personName, evidence),
    logComm: (personName: string, event: Record<string, unknown>) =>
      ipcRenderer.invoke('trust:log-comm', personName, event),
    addAlias: (personId: string, alias: string, type: string) =>
      ipcRenderer.invoke('trust:add-alias', personId, alias, type),
    getAll: () => ipcRenderer.invoke('trust:get-all'),
    getContext: (personId: string) => ipcRenderer.invoke('trust:get-context', personId),
    getPromptContext: () => ipcRenderer.invoke('trust:get-prompt-context'),
    findByDomain: (domain: string) => ipcRenderer.invoke('trust:find-by-domain', domain),
    getMostTrusted: (limit?: number) => ipcRenderer.invoke('trust:most-trusted', limit),
    getRecent: (limit?: number) => ipcRenderer.invoke('trust:recent', limit),
    updateNotes: (personId: string, notes: string) =>
      ipcRenderer.invoke('trust:update-notes', personId, notes),
    linkPersons: (idA: string, idB: string, label: string) =>
      ipcRenderer.invoke('trust:link-persons', idA, idB, label),
  },

  agentTrust: {
    getState: () => ipcRenderer.invoke('agent-trust:get-state'),
    processMessage: (message: string) => ipcRenderer.invoke('agent-trust:process-message', message),
    resetSession: () => ipcRenderer.invoke('agent-trust:reset-session'),
    getPromptBlock: () => ipcRenderer.invoke('agent-trust:get-prompt-block'),
    getLabel: () => ipcRenderer.invoke('agent-trust:get-label'),
    boost: (amount: number) => ipcRenderer.invoke('agent-trust:boost', amount),
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

  desktopEvolution: {
    getIndex: () => ipcRenderer.invoke('desktop-evolution:get-index') as Promise<number>,
    setIndex: (index: number) => ipcRenderer.invoke('desktop-evolution:set-index', index) as Promise<void>,
    getTransitionState: () => ipcRenderer.invoke('desktop-evolution:get-transition') as Promise<{ currentIndex: number; targetIndex: number; blend: number; lastChange: number }>,
  },

  artEvolution: {
    getState: () => ipcRenderer.invoke('art-evolution:get-state'),
    getLatest: () => ipcRenderer.invoke('art-evolution:get-latest'),
    check: () => ipcRenderer.invoke('art-evolution:check'),
    force: () => ipcRenderer.invoke('art-evolution:force'),
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

  meetingIntel: {
    create: (opts: Record<string, unknown>) =>
      ipcRenderer.invoke('meeting-intel:create', opts),
    get: (id: string) => ipcRenderer.invoke('meeting-intel:get', id),
    list: (opts?: Record<string, unknown>) =>
      ipcRenderer.invoke('meeting-intel:list', opts),
    getActive: () => ipcRenderer.invoke('meeting-intel:get-active'),
    update: (meetingId: string, updates: Record<string, unknown>) =>
      ipcRenderer.invoke('meeting-intel:update', meetingId, updates),
    start: (meetingId: string) => ipcRenderer.invoke('meeting-intel:start', meetingId),
    end: (meetingId: string, opts?: Record<string, unknown>) =>
      ipcRenderer.invoke('meeting-intel:end', meetingId, opts),
    cancel: (meetingId: string) => ipcRenderer.invoke('meeting-intel:cancel', meetingId),
    endActive: (transcript?: string) =>
      ipcRenderer.invoke('meeting-intel:end-active', transcript),
    addNote: (meetingId: string, note: Record<string, unknown>) =>
      ipcRenderer.invoke('meeting-intel:add-note', meetingId, note),
    addNoteActive: (content: string, type?: string) =>
      ipcRenderer.invoke('meeting-intel:add-note-active', content, type),
    setTranscript: (meetingId: string, transcript: string) =>
      ipcRenderer.invoke('meeting-intel:set-transcript', meetingId, transcript),
    setSummary: (meetingId: string, summary: string) =>
      ipcRenderer.invoke('meeting-intel:set-summary', meetingId, summary),
    search: (query: string, limit?: number) =>
      ipcRenderer.invoke('meeting-intel:search', query, limit),
    stats: () => ipcRenderer.invoke('meeting-intel:stats'),
    recentSummaries: (count?: number) =>
      ipcRenderer.invoke('meeting-intel:recent-summaries', count),
    fromCalendar: (event: Record<string, unknown>) =>
      ipcRenderer.invoke('meeting-intel:from-calendar', event),
    quickStart: (meetingUrl: string, name?: string) =>
      ipcRenderer.invoke('meeting-intel:quick-start', meetingUrl, name),
    refreshIntel: (meetingId: string) =>
      ipcRenderer.invoke('meeting-intel:refresh-intel', meetingId),
    getContext: () => ipcRenderer.invoke('meeting-intel:get-context'),
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
    reset: () => ipcRenderer.invoke('integrity:reset') as Promise<{
      success: boolean;
      message: string;
    }>,
  },

  superpowers: {
    list: () => ipcRenderer.invoke('superpowers:list'),
    get: (id: string) => ipcRenderer.invoke('superpowers:get', id),
    toggle: (id: string, enabled: boolean) => ipcRenderer.invoke('superpowers:toggle', id, enabled),
    toggleTool: (superpowerId: string, toolName: string, enabled: boolean) =>
      ipcRenderer.invoke('superpowers:toggle-tool', superpowerId, toolName, enabled),
    updatePermissions: (id: string, perms: Record<string, unknown>) =>
      ipcRenderer.invoke('superpowers:update-permissions', id, perms),
    install: (repoUrl: string) => ipcRenderer.invoke('superpowers:install', repoUrl),
    uninstall: (id: string) => ipcRenderer.invoke('superpowers:uninstall', id),
    uninstallPreview: (id: string) => ipcRenderer.invoke('superpowers:uninstall-preview', id),
    usageStats: (id: string) => ipcRenderer.invoke('superpowers:usage-stats', id),
    enabledTools: () => ipcRenderer.invoke('superpowers:enabled-tools'),
    flush: () => ipcRenderer.invoke('superpowers:flush'),
    // v2 Adapted Superpower Store
    storeList: () => ipcRenderer.invoke('superpowers:store-list'),
    storeGet: (id: string) => ipcRenderer.invoke('superpowers:store-get', id),
    storeConfirm: (id: string, consentToken: string) =>
      ipcRenderer.invoke('superpowers:store-confirm', id, consentToken),
    storeEnabledTools: () => ipcRenderer.invoke('superpowers:store-enabled-tools'),
    storeStatus: () => ipcRenderer.invoke('superpowers:store-status'),
    storePromptContext: () => ipcRenderer.invoke('superpowers:store-prompt-context'),
    storeNeedsAttention: () => ipcRenderer.invoke('superpowers:store-needs-attention'),
  },

  capabilityGaps: {
    record: (taskDescription: string) =>
      ipcRenderer.invoke('capability-gaps:record', taskDescription),
    top: (limit?: number) => ipcRenderer.invoke('capability-gaps:top', limit),
    get: (gapId: string) => ipcRenderer.invoke('capability-gaps:get', gapId),
    generateProposals: () => ipcRenderer.invoke('capability-gaps:generate-proposals'),
    pendingProposals: () => ipcRenderer.invoke('capability-gaps:pending-proposals'),
    acceptedProposals: () => ipcRenderer.invoke('capability-gaps:accepted-proposals'),
    getProposal: (proposalId: string) => ipcRenderer.invoke('capability-gaps:get-proposal', proposalId),
    present: (proposalId: string) => ipcRenderer.invoke('capability-gaps:present', proposalId),
    accept: (proposalId: string) => ipcRenderer.invoke('capability-gaps:accept', proposalId),
    decline: (proposalId: string) => ipcRenderer.invoke('capability-gaps:decline', proposalId),
    markInstalled: (proposalId: string) => ipcRenderer.invoke('capability-gaps:mark-installed', proposalId),
    promptContext: () => ipcRenderer.invoke('capability-gaps:prompt-context'),
    status: () => ipcRenderer.invoke('capability-gaps:status'),
    prune: () => ipcRenderer.invoke('capability-gaps:prune'),
  },

  contextStream: {
    push: (event: {
      type: string;
      source: string;
      summary: string;
      data?: Record<string, unknown>;
      dedupeKey?: string;
      ttlMs?: number;
    }) => ipcRenderer.invoke('context-stream:push', event),
    snapshot: () => ipcRenderer.invoke('context-stream:snapshot'),
    recent: (opts?: { limit?: number; types?: string[]; sinceMs?: number }) =>
      ipcRenderer.invoke('context-stream:recent', opts),
    byType: (type: string, limit?: number) =>
      ipcRenderer.invoke('context-stream:by-type', type, limit),
    latestByType: () => ipcRenderer.invoke('context-stream:latest-by-type'),
    contextString: () => ipcRenderer.invoke('context-stream:context-string'),
    promptContext: () => ipcRenderer.invoke('context-stream:prompt-context'),
    status: () => ipcRenderer.invoke('context-stream:status'),
    prune: () => ipcRenderer.invoke('context-stream:prune'),
    setEnabled: (enabled: boolean) =>
      ipcRenderer.invoke('context-stream:set-enabled', enabled),
    clear: () => ipcRenderer.invoke('context-stream:clear'),
  },

  contextGraph: {
    snapshot: () => ipcRenderer.invoke('context-graph:snapshot'),
    activeStream: () => ipcRenderer.invoke('context-graph:active-stream'),
    recentStreams: (limit?: number) =>
      ipcRenderer.invoke('context-graph:recent-streams', limit),
    streamsByTask: (task: string) =>
      ipcRenderer.invoke('context-graph:streams-by-task', task),
    entitiesByType: (type: string, limit?: number) =>
      ipcRenderer.invoke('context-graph:entities-by-type', type, limit),
    topEntities: (limit?: number) =>
      ipcRenderer.invoke('context-graph:top-entities', limit),
    activeEntities: (windowMs?: number) =>
      ipcRenderer.invoke('context-graph:active-entities', windowMs),
    relatedEntities: (type: string, value: string, limit?: number) =>
      ipcRenderer.invoke('context-graph:related-entities', type, value, limit),
    contextString: () => ipcRenderer.invoke('context-graph:context-string'),
    promptContext: () => ipcRenderer.invoke('context-graph:prompt-context'),
    status: () => ipcRenderer.invoke('context-graph:status'),
    onStreamUpdate: (callback: (payload: { activeStream: any; recentEntities: any[]; streamHistory: any[] }) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, payload: { activeStream: any; recentEntities: any[]; streamHistory: any[] }) => callback(payload);
      ipcRenderer.on('context:stream-update', handler);
      return () => {
        ipcRenderer.removeListener('context:stream-update', handler);
      };
    },
  },

  toolRouter: {
    suggestions: () => ipcRenderer.invoke('tool-router:suggestions'),
    activeCategory: () => ipcRenderer.invoke('tool-router:active-category'),
    categoryScores: () => ipcRenderer.invoke('tool-router:category-scores'),
    snapshot: () => ipcRenderer.invoke('tool-router:snapshot'),
    contextString: () => ipcRenderer.invoke('tool-router:context-string'),
    promptContext: () => ipcRenderer.invoke('tool-router:prompt-context'),
    status: () => ipcRenderer.invoke('tool-router:status'),
    registerTools: (tools: Array<{ name: string; description?: string }>) =>
      ipcRenderer.invoke('tool-router:register-tools', tools),
    unregisterTool: (name: string) =>
      ipcRenderer.invoke('tool-router:unregister-tool', name),
    config: () => ipcRenderer.invoke('tool-router:config'),
  },

  commitments: {
    getActive: () => ipcRenderer.invoke('commitment:get-active'),
    getOverdue: () => ipcRenderer.invoke('commitment:get-overdue'),
    getByPerson: (personName: string) =>
      ipcRenderer.invoke('commitment:get-by-person', personName),
    getUpcoming: (withinHours?: number) =>
      ipcRenderer.invoke('commitment:get-upcoming', withinHours),
    getById: (id: string) => ipcRenderer.invoke('commitment:get-by-id', id),
    getAll: () => ipcRenderer.invoke('commitment:get-all'),
    add: (mention: Record<string, unknown>) =>
      ipcRenderer.invoke('commitment:add', mention),
    complete: (id: string, notes?: string) =>
      ipcRenderer.invoke('commitment:complete', id, notes),
    cancel: (id: string, reason?: string) =>
      ipcRenderer.invoke('commitment:cancel', id, reason),
    snooze: (id: string, untilMs: number) =>
      ipcRenderer.invoke('commitment:snooze', id, untilMs),
    trackOutbound: (msg: { recipient: string; channel: string; summary: string }) =>
      ipcRenderer.invoke('commitment:track-outbound', msg),
    recordReply: (recipient: string, channel: string) =>
      ipcRenderer.invoke('commitment:record-reply', recipient, channel),
    getUnreplied: () => ipcRenderer.invoke('commitment:get-unreplied'),
    generateSuggestions: () => ipcRenderer.invoke('commitment:generate-suggestions'),
    getPendingSuggestions: () => ipcRenderer.invoke('commitment:get-pending-suggestions'),
    markSuggestionDelivered: (id: string) =>
      ipcRenderer.invoke('commitment:mark-suggestion-delivered', id),
    markSuggestionActedOn: (id: string) =>
      ipcRenderer.invoke('commitment:mark-suggestion-acted-on', id),
    contextString: () => ipcRenderer.invoke('commitment:context-string'),
    promptContext: () => ipcRenderer.invoke('commitment:prompt-context'),
    status: () => ipcRenderer.invoke('commitment:status'),
    config: () => ipcRenderer.invoke('commitment:config'),
  },

  dailyBriefing: {
    generate: (type: string, sourceData: Record<string, unknown>) =>
      ipcRenderer.invoke('briefing:generate', type, sourceData),
    shouldGenerate: () => ipcRenderer.invoke('briefing:should-generate'),
    adaptiveLength: (sourceData: Record<string, unknown>) =>
      ipcRenderer.invoke('briefing:adaptive-length', sourceData),
    getLatest: (type?: string) => ipcRenderer.invoke('briefing:get-latest', type),
    getLatestToday: (type: string) => ipcRenderer.invoke('briefing:get-latest-today', type),
    getById: (id: string) => ipcRenderer.invoke('briefing:get-by-id', id),
    getHistory: (limit?: number) => ipcRenderer.invoke('briefing:get-history', limit),
    getAll: () => ipcRenderer.invoke('briefing:get-all'),
    markDelivered: (id: string, channel: string) =>
      ipcRenderer.invoke('briefing:mark-delivered', id, channel),
    markDeliveryFailed: (id: string, channel: string, reason: string) =>
      ipcRenderer.invoke('briefing:mark-delivery-failed', id, channel, reason),
    isStale: (type: string) => ipcRenderer.invoke('briefing:is-stale', type),
    scheduledTimeToday: (timeStr: string) =>
      ipcRenderer.invoke('briefing:scheduled-time-today', timeStr),
    formatText: (id: string) => ipcRenderer.invoke('briefing:format-text', id),
    formatMarkdown: (id: string) => ipcRenderer.invoke('briefing:format-markdown', id),
    contextString: () => ipcRenderer.invoke('briefing:context-string'),
    promptContext: () => ipcRenderer.invoke('briefing:prompt-context'),
    status: () => ipcRenderer.invoke('briefing:status'),
    config: () => ipcRenderer.invoke('briefing:config'),
  },

  briefingDelivery: {
    list: () => ipcRenderer.invoke('briefing:list'),
    dismiss: (id: string) => ipcRenderer.invoke('briefing:dismiss', id),
    onNew: (callback: (briefing: { id: string; topic: string; content: string; priority: string; timestamp: number }) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, briefing: { id: string; topic: string; content: string; priority: string; timestamp: number }) => callback(briefing);
      ipcRenderer.on('briefing:new', handler);
      return () => {
        ipcRenderer.removeListener('briefing:new', handler);
      };
    },
  },

  workflowRecorder: {
    startRecording: (name: string) =>
      ipcRenderer.invoke('workflow:start-recording', name),
    stopRecording: () => ipcRenderer.invoke('workflow:stop-recording'),
    cancelRecording: () => ipcRenderer.invoke('workflow:cancel-recording'),
    recordEvent: (type: string, description: string, payload?: Record<string, unknown>) =>
      ipcRenderer.invoke('workflow:record-event', type, description, payload),
    addAnnotation: (text: string) =>
      ipcRenderer.invoke('workflow:add-annotation', text),
    addKeyFrame: (filePath: string, activeApp: string) =>
      ipcRenderer.invoke('workflow:add-keyframe', filePath, activeApp),
    createTemplate: (recordingId: string, overrides?: Record<string, unknown>) =>
      ipcRenderer.invoke('workflow:create-template', recordingId, overrides),
    deleteTemplate: (id: string) =>
      ipcRenderer.invoke('workflow:delete-template', id),
    status: () => ipcRenderer.invoke('workflow:status'),
    getRecording: (id: string) =>
      ipcRenderer.invoke('workflow:get-recording', id),
    getAllRecordings: () => ipcRenderer.invoke('workflow:get-all-recordings'),
    getRecentRecordings: (limit?: number) =>
      ipcRenderer.invoke('workflow:get-recent-recordings', limit),
    getTemplate: (id: string) =>
      ipcRenderer.invoke('workflow:get-template', id),
    getAllTemplates: () => ipcRenderer.invoke('workflow:get-all-templates'),
    getTemplatesByTag: (tag: string) =>
      ipcRenderer.invoke('workflow:get-templates-by-tag', tag),
    deleteRecording: (id: string) =>
      ipcRenderer.invoke('workflow:delete-recording', id),
    config: () => ipcRenderer.invoke('workflow:config'),
  },

  workflowExecutor: {
    execute: (templateId: string, params?: Record<string, string>, triggeredBy?: string) =>
      ipcRenderer.invoke('wf-exec:execute', templateId, params, triggeredBy),
    pause: () => ipcRenderer.invoke('wf-exec:pause'),
    resume: () => ipcRenderer.invoke('wf-exec:resume'),
    cancel: () => ipcRenderer.invoke('wf-exec:cancel'),
    provideUserResponse: (response: string) =>
      ipcRenderer.invoke('wf-exec:provide-user-response', response),
    grantPermission: (templateId: string, opts?: Record<string, unknown>) =>
      ipcRenderer.invoke('wf-exec:grant-permission', templateId, opts),
    revokePermission: (templateId: string) =>
      ipcRenderer.invoke('wf-exec:revoke-permission', templateId),
    getPermissions: () => ipcRenderer.invoke('wf-exec:get-permissions'),
    activeRun: () => ipcRenderer.invoke('wf-exec:active-run'),
    isRunning: () => ipcRenderer.invoke('wf-exec:is-running'),
    runHistory: (limit?: number) =>
      ipcRenderer.invoke('wf-exec:run-history', limit),
    getRun: (runId: string) =>
      ipcRenderer.invoke('wf-exec:get-run', runId),
    getConfig: () => ipcRenderer.invoke('wf-exec:get-config'),
    updateConfig: (updates: Record<string, unknown>) =>
      ipcRenderer.invoke('wf-exec:update-config', updates),
  },

  inbox: {
    getMessages: (opts?: Record<string, unknown>) =>
      ipcRenderer.invoke('inbox:get-messages', opts),
    getMessage: (id: string) =>
      ipcRenderer.invoke('inbox:get-message', id),
    getStats: () => ipcRenderer.invoke('inbox:get-stats'),
    markRead: (ids: string | string[]) =>
      ipcRenderer.invoke('inbox:mark-read', ids),
    markUnread: (ids: string | string[]) =>
      ipcRenderer.invoke('inbox:mark-unread', ids),
    archive: (ids: string | string[]) =>
      ipcRenderer.invoke('inbox:archive', ids),
    unarchive: (ids: string | string[]) =>
      ipcRenderer.invoke('inbox:unarchive', ids),
    delete: (ids: string | string[]) =>
      ipcRenderer.invoke('inbox:delete', ids),
    markAllRead: () => ipcRenderer.invoke('inbox:mark-all-read'),
    getConfig: () => ipcRenderer.invoke('inbox:get-config'),
    updateConfig: (partial: Record<string, unknown>) =>
      ipcRenderer.invoke('inbox:update-config', partial),
  },

  outbound: {
    createDraft: (params: Record<string, unknown>) =>
      ipcRenderer.invoke('outbound:create-draft', params),
    getDraft: (id: string) => ipcRenderer.invoke('outbound:get-draft', id),
    editDraft: (id: string, updates: Record<string, unknown>) =>
      ipcRenderer.invoke('outbound:edit-draft', id, updates),
    deleteDraft: (id: string) => ipcRenderer.invoke('outbound:delete-draft', id),
    getDrafts: (opts?: Record<string, unknown>) =>
      ipcRenderer.invoke('outbound:get-drafts', opts),
    getPending: () => ipcRenderer.invoke('outbound:get-pending'),
    approve: (id: string) => ipcRenderer.invoke('outbound:approve', id),
    reject: (id: string) => ipcRenderer.invoke('outbound:reject', id),
    approveAll: () => ipcRenderer.invoke('outbound:approve-all'),
    tryAutoApprove: (id: string) =>
      ipcRenderer.invoke('outbound:try-auto-approve', id),
    send: (id: string) => ipcRenderer.invoke('outbound:send', id),
    approveAndSend: (id: string) =>
      ipcRenderer.invoke('outbound:approve-and-send', id),
    sendAllApproved: () => ipcRenderer.invoke('outbound:send-all-approved'),
    batchReview: () => ipcRenderer.invoke('outbound:batch-review'),
    getStyleProfile: (personId: string) =>
      ipcRenderer.invoke('outbound:get-style-profile', personId),
    updateStyleProfile: (personId: string, name: string, obs: Record<string, unknown>) =>
      ipcRenderer.invoke('outbound:update-style-profile', personId, name, obs),
    getAllStyleProfiles: () =>
      ipcRenderer.invoke('outbound:get-all-style-profiles'),
    addStandingPermission: (params: Record<string, unknown>) =>
      ipcRenderer.invoke('outbound:add-standing-permission', params),
    revokeStandingPermission: (id: string) =>
      ipcRenderer.invoke('outbound:revoke-standing-permission', id),
    deleteStandingPermission: (id: string) =>
      ipcRenderer.invoke('outbound:delete-standing-permission', id),
    getStandingPermissions: () =>
      ipcRenderer.invoke('outbound:get-standing-permissions'),
    getAllStandingPermissions: () =>
      ipcRenderer.invoke('outbound:get-all-standing-permissions'),
    getStats: () => ipcRenderer.invoke('outbound:get-stats'),
    getConfig: () => ipcRenderer.invoke('outbound:get-config'),
    updateConfig: (partial: Record<string, unknown>) =>
      ipcRenderer.invoke('outbound:update-config', partial),
    getPromptContext: () =>
      ipcRenderer.invoke('outbound:get-prompt-context'),
  },

  intelligenceRouter: {
    classifyTask: (params: {
      messageContent: string;
      toolCount: number;
      hasImages: boolean;
      hasAudio: boolean;
      systemPromptLength: number;
      conversationLength: number;
    }) => ipcRenderer.invoke('router:classify-task', params),
    selectModel: (task: Record<string, unknown>) =>
      ipcRenderer.invoke('router:select-model', task),
    classifyAndRoute: (params: {
      messageContent: string;
      toolCount: number;
      hasImages: boolean;
      hasAudio: boolean;
      systemPromptLength: number;
      conversationLength: number;
    }) => ipcRenderer.invoke('router:classify-and-route', params),
    recordOutcome: (decisionId: string, outcome: {
      success: boolean;
      durationMs: number;
      inputTokens?: number;
      outputTokens?: number;
    }) => ipcRenderer.invoke('router:record-outcome', decisionId, outcome),
    getModel: (modelId: string) =>
      ipcRenderer.invoke('router:get-model', modelId),
    getAllModels: () => ipcRenderer.invoke('router:get-all-models'),
    getAvailableModels: () => ipcRenderer.invoke('router:get-available-models'),
    registerModel: (model: Record<string, unknown>) =>
      ipcRenderer.invoke('router:register-model', model),
    setModelAvailability: (modelId: string, available: boolean) =>
      ipcRenderer.invoke('router:set-model-availability', modelId, available),
    resetModelFailures: (modelId: string) =>
      ipcRenderer.invoke('router:reset-model-failures', modelId),
    getDecision: (id: string) =>
      ipcRenderer.invoke('router:get-decision', id),
    getRecentDecisions: (limit?: number) =>
      ipcRenderer.invoke('router:get-recent-decisions', limit),
    getDecisionsForModel: (modelId: string, limit?: number) =>
      ipcRenderer.invoke('router:get-decisions-for-model', modelId, limit),
    getStats: () => ipcRenderer.invoke('router:get-stats'),
    getConfig: () => ipcRenderer.invoke('router:get-config'),
    updateConfig: (partial: Record<string, unknown>) =>
      ipcRenderer.invoke('router:update-config', partial),
    getPromptContext: () =>
      ipcRenderer.invoke('router:get-prompt-context'),
    discoverLocalModels: () =>
      ipcRenderer.invoke('router:discover-local-models') as Promise<{ found: number; models: string[] }>,
  },

  agentNetwork: {
    getIdentity: () => ipcRenderer.invoke('agent-net:get-identity'),
    getAgentId: () => ipcRenderer.invoke('agent-net:get-agent-id'),
    generatePairingOffer: () => ipcRenderer.invoke('agent-net:generate-pairing-offer'),
    getActivePairingCode: () => ipcRenderer.invoke('agent-net:get-active-pairing-code'),
    acceptPairing: (remoteIdentity: Record<string, unknown>, ownerPersonId: string | null, ownerTrust: { overall: number } | null) =>
      ipcRenderer.invoke('agent-net:accept-pairing', remoteIdentity, ownerPersonId, ownerTrust),
    recordInboundPairing: (remoteIdentity: Record<string, unknown>) =>
      ipcRenderer.invoke('agent-net:record-inbound-pairing', remoteIdentity),
    blockAgent: (agentId: string) => ipcRenderer.invoke('agent-net:block-agent', agentId),
    unpairAgent: (agentId: string) => ipcRenderer.invoke('agent-net:unpair-agent', agentId),
    getPeer: (agentId: string) => ipcRenderer.invoke('agent-net:get-peer', agentId),
    getAllPeers: () => ipcRenderer.invoke('agent-net:get-all-peers'),
    getPairedPeers: () => ipcRenderer.invoke('agent-net:get-paired-peers'),
    getPendingPairingRequests: () => ipcRenderer.invoke('agent-net:get-pending-pairing-requests'),
    updatePeerTrust: (agentId: string, ownerTrust: { overall: number } | null, ownerPersonId?: string) =>
      ipcRenderer.invoke('agent-net:update-peer-trust', agentId, ownerTrust, ownerPersonId),
    setAutoApproveTaskTypes: (agentId: string, taskTypes: string[]) =>
      ipcRenderer.invoke('agent-net:set-auto-approve-task-types', agentId, taskTypes),
    updatePeerCapabilities: (agentId: string, capabilities: string[]) =>
      ipcRenderer.invoke('agent-net:update-peer-capabilities', agentId, capabilities),
    findPeersWithCapability: (capability: string) =>
      ipcRenderer.invoke('agent-net:find-peers-with-capability', capability),
    createMessage: (toAgentId: string, type: string, payload: Record<string, unknown>) =>
      ipcRenderer.invoke('agent-net:create-message', toAgentId, type, payload),
    processInboundMessage: (message: Record<string, unknown>) =>
      ipcRenderer.invoke('agent-net:process-inbound-message', message),
    getMessageLog: (limit?: number) => ipcRenderer.invoke('agent-net:get-message-log', limit),
    createDelegation: (targetAgentId: string, description: string, requiredCapabilities?: string[], deadline?: number) =>
      ipcRenderer.invoke('agent-net:create-delegation', targetAgentId, description, requiredCapabilities, deadline),
    handleInboundDelegation: (requestingAgentId: string, delegationId: string, description: string, requiredCapabilities: string[], deadline: number) =>
      ipcRenderer.invoke('agent-net:handle-inbound-delegation', requestingAgentId, delegationId, description, requiredCapabilities, deadline),
    approveDelegation: (delegationId: string) => ipcRenderer.invoke('agent-net:approve-delegation', delegationId),
    rejectDelegation: (delegationId: string) => ipcRenderer.invoke('agent-net:reject-delegation', delegationId),
    startDelegation: (delegationId: string) => ipcRenderer.invoke('agent-net:start-delegation', delegationId),
    completeDelegation: (delegationId: string, result: unknown) =>
      ipcRenderer.invoke('agent-net:complete-delegation', delegationId, result),
    failDelegation: (delegationId: string, error: string) =>
      ipcRenderer.invoke('agent-net:fail-delegation', delegationId, error),
    cancelDelegation: (delegationId: string) => ipcRenderer.invoke('agent-net:cancel-delegation', delegationId),
    getDelegation: (delegationId: string) => ipcRenderer.invoke('agent-net:get-delegation', delegationId),
    getAllDelegations: () => ipcRenderer.invoke('agent-net:get-all-delegations'),
    getDelegationsForAgent: (agentId: string) =>
      ipcRenderer.invoke('agent-net:get-delegations-for-agent', agentId),
    getPendingInboundDelegations: () => ipcRenderer.invoke('agent-net:get-pending-inbound-delegations'),
    // SAS Verification (Crypto Sprint 3 — HIGH-004)
    getSafetyNumber: (agentId: string) => ipcRenderer.invoke('agent-net:get-safety-number', agentId),
    verifySAS: (agentId: string) => ipcRenderer.invoke('agent-net:verify-sas', agentId),
    isSASVerified: (agentId: string) => ipcRenderer.invoke('agent-net:is-sas-verified', agentId),
    getStats: () => ipcRenderer.invoke('agent-net:get-stats'),
    getConfig: () => ipcRenderer.invoke('agent-net:get-config'),
    updateConfig: (partial: Record<string, unknown>) =>
      ipcRenderer.invoke('agent-net:update-config', partial),
    getPromptContext: () => ipcRenderer.invoke('agent-net:get-prompt-context'),
  },

  ecosystem: {
    createManifest: (opts: Record<string, unknown>) =>
      ipcRenderer.invoke('ecosystem:create-manifest', opts),
    validateManifest: (manifest: Record<string, unknown>) =>
      ipcRenderer.invoke('ecosystem:validate-manifest', manifest),
    getDeveloperKeys: () => ipcRenderer.invoke('ecosystem:get-developer-keys'),
    hasDeveloperKeys: () => ipcRenderer.invoke('ecosystem:has-developer-keys'),
    signPackage: (manifest: Record<string, unknown>) =>
      ipcRenderer.invoke('ecosystem:sign-package', manifest),
    publishPackage: (pkg: Record<string, unknown>) =>
      ipcRenderer.invoke('ecosystem:publish-package', pkg),
    getPublishedPackages: () => ipcRenderer.invoke('ecosystem:get-published-packages'),
    getPublishedPackage: (packageId: string) =>
      ipcRenderer.invoke('ecosystem:get-published-package', packageId),
    unpublishPackage: (packageId: string) =>
      ipcRenderer.invoke('ecosystem:unpublish-package', packageId),
    searchRegistry: (query: Record<string, unknown>) =>
      ipcRenderer.invoke('ecosystem:search-registry', query),
    getRegistryListing: (packageId: string) =>
      ipcRenderer.invoke('ecosystem:get-registry-listing', packageId),
    searchForCapability: (description: string, keywords: string[]) =>
      ipcRenderer.invoke('ecosystem:search-for-capability', description, keywords),
    initiatePurchase: (packageId: string, amountUsdCents: number, type?: string) =>
      ipcRenderer.invoke('ecosystem:initiate-purchase', packageId, amountUsdCents, type),
    approvePurchase: (transactionId: string, consentToken: string) =>
      ipcRenderer.invoke('ecosystem:approve-purchase', transactionId, consentToken),
    cancelPurchase: (transactionId: string) =>
      ipcRenderer.invoke('ecosystem:cancel-purchase', transactionId),
    executePurchase: (transactionId: string) =>
      ipcRenderer.invoke('ecosystem:execute-purchase', transactionId),
    getTransactions: () => ipcRenderer.invoke('ecosystem:get-transactions'),
    getTransactionsForPackage: (packageId: string) =>
      ipcRenderer.invoke('ecosystem:get-transactions-for-package', packageId),
    getTransaction: (transactionId: string) =>
      ipcRenderer.invoke('ecosystem:get-transaction', transactionId),
    isPurchased: (packageId: string) =>
      ipcRenderer.invoke('ecosystem:is-purchased', packageId),
    getStats: () => ipcRenderer.invoke('ecosystem:get-stats'),
    getConfig: () => ipcRenderer.invoke('ecosystem:get-config'),
    updateConfig: (partial: Record<string, unknown>) =>
      ipcRenderer.invoke('ecosystem:update-config', partial),
    getPromptContext: () => ipcRenderer.invoke('ecosystem:get-prompt-context'),
  },

  persistence: {
    exportState: (passphrase: string, outputPath?: string) =>
      ipcRenderer.invoke('persistence:export-state', passphrase, outputPath),
    exportIncremental: (passphrase: string, outputPath?: string) =>
      ipcRenderer.invoke('persistence:export-incremental', passphrase, outputPath),
    importState: (archivePath: string, passphrase: string) =>
      ipcRenderer.invoke('persistence:import-state', archivePath, passphrase),
    validateArchive: (archivePath: string, passphrase: string) =>
      ipcRenderer.invoke('persistence:validate-archive', archivePath, passphrase),
    setAutoPassphrase: (passphrase: string) =>
      ipcRenderer.invoke('persistence:set-auto-passphrase', passphrase),
    clearAutoPassphrase: () =>
      ipcRenderer.invoke('persistence:clear-auto-passphrase'),
    runScheduledBackup: () =>
      ipcRenderer.invoke('persistence:run-scheduled-backup'),
    getStateFiles: () => ipcRenderer.invoke('persistence:get-state-files'),
    enumerateState: () => ipcRenderer.invoke('persistence:enumerate-state'),
    getBackupHistory: () => ipcRenderer.invoke('persistence:get-backup-history'),
    getLastBackup: () => ipcRenderer.invoke('persistence:get-last-backup'),
    getConfig: () => ipcRenderer.invoke('persistence:get-config'),
    updateConfig: (partial: Record<string, unknown>) =>
      ipcRenderer.invoke('persistence:update-config', partial),
    getPromptContext: () => ipcRenderer.invoke('persistence:get-prompt-context'),
    checkContinuity: () => ipcRenderer.invoke('persistence:check-continuity'),
  },

  memoryQuality: {
    assessExtraction: (results: Array<Record<string, unknown>>) =>
      ipcRenderer.invoke('memquality:assess-extraction', results),
    assessRetrieval: (results: Array<Record<string, unknown>>) =>
      ipcRenderer.invoke('memquality:assess-retrieval', results),
    assessConsolidation: (results: Array<Record<string, unknown>>) =>
      ipcRenderer.invoke('memquality:assess-consolidation', results),
    assessPersonMentions: (results: Array<Record<string, unknown>>) =>
      ipcRenderer.invoke('memquality:assess-person-mentions', results),
    buildReport: (
      extractionResults: Array<Record<string, unknown>>,
      retrievalResults: Array<Record<string, unknown>>,
      consolidationResults: Array<Record<string, unknown>>,
    ) => ipcRenderer.invoke('memquality:build-report', extractionResults, retrievalResults, consolidationResults),
    getExtractionBenchmarks: () => ipcRenderer.invoke('memquality:get-extraction-benchmarks'),
    getRetrievalBenchmarks: () => ipcRenderer.invoke('memquality:get-retrieval-benchmarks'),
    getConsolidationBenchmarks: () => ipcRenderer.invoke('memquality:get-consolidation-benchmarks'),
    getLatestReport: () => ipcRenderer.invoke('memquality:get-latest-report'),
    getQualityHistory: () => ipcRenderer.invoke('memquality:get-quality-history'),
    getQualityTrend: (count?: number) => ipcRenderer.invoke('memquality:get-quality-trend', count),
    getConfig: () => ipcRenderer.invoke('memquality:get-config'),
    updateConfig: (partial: Record<string, unknown>) =>
      ipcRenderer.invoke('memquality:update-config', partial),
    getPromptContext: () => ipcRenderer.invoke('memquality:get-prompt-context'),
  },

  personalityCalibration: {
    processMessage: (text: string, responseTimeMs?: number) =>
      ipcRenderer.invoke('calibration:process-message', text, responseTimeMs),
    recordDismissal: () => ipcRenderer.invoke('calibration:record-dismissal'),
    recordEngagement: () => ipcRenderer.invoke('calibration:record-engagement'),
    incrementSession: () => ipcRenderer.invoke('calibration:increment-session'),
    getDimensions: () => ipcRenderer.invoke('calibration:get-dimensions'),
    getState: () => ipcRenderer.invoke('calibration:get-state'),
    getDismissalRate: () => ipcRenderer.invoke('calibration:get-dismissal-rate'),
    getEffectiveProactivity: (isCritical: boolean) =>
      ipcRenderer.invoke('calibration:get-effective-proactivity', isCritical),
    getHistory: () => ipcRenderer.invoke('calibration:get-history'),
    getExplanation: () => ipcRenderer.invoke('calibration:get-explanation'),
    getPromptContext: () => ipcRenderer.invoke('calibration:get-prompt-context'),
    getVisualWarmthModifier: () => ipcRenderer.invoke('calibration:get-visual-warmth-modifier'),
    getVisualEnergyModifier: () => ipcRenderer.invoke('calibration:get-visual-energy-modifier'),
    getConfig: () => ipcRenderer.invoke('calibration:get-config'),
    updateConfig: (partial: Record<string, unknown>) =>
      ipcRenderer.invoke('calibration:update-config', partial),
    resetDimension: (dimension: string) =>
      ipcRenderer.invoke('calibration:reset-dimension', dimension),
    resetAll: () => ipcRenderer.invoke('calibration:reset-all'),
  },

  memoryPersonalityBridge: {
    recordEngagement: (memoryId: string, type: string, context: string) =>
      ipcRenderer.invoke('bridge:record-engagement', memoryId, type, context),
    getEngagements: () => ipcRenderer.invoke('bridge:get-engagements'),
    getPriorityAdjustments: () => ipcRenderer.invoke('bridge:get-priority-adjustments'),
    getExtractionGuidance: () => ipcRenderer.invoke('bridge:get-extraction-guidance'),
    getExtractionHints: () => ipcRenderer.invoke('bridge:get-extraction-hints'),
    recomputeExtractionHints: () => ipcRenderer.invoke('bridge:recompute-extraction-hints'),
    proposeProactivity: (proposal: any) => ipcRenderer.invoke('bridge:propose-proactivity', proposal),
    arbitrateProactivity: () => ipcRenderer.invoke('bridge:arbitrate-proactivity'),
    getProactivityCooldown: () => ipcRenderer.invoke('bridge:get-proactivity-cooldown'),
    getPendingProposals: () => ipcRenderer.invoke('bridge:get-pending-proposals'),
    recordExchange: (flattery: boolean, urgency: boolean, options: number) =>
      ipcRenderer.invoke('bridge:record-exchange', flattery, urgency, options),
    getManipulationMetrics: () => ipcRenderer.invoke('bridge:get-manipulation-metrics'),
    getPromptContext: () => ipcRenderer.invoke('bridge:get-prompt-context'),
    getState: () => ipcRenderer.invoke('bridge:get-state'),
    getConfig: () => ipcRenderer.invoke('bridge:get-config'),
    getRelevanceWeights: () => ipcRenderer.invoke('bridge:get-relevance-weights'),
    syncMemoryToPersonality: () => ipcRenderer.invoke('bridge:sync-memory-to-personality'),
    updateConfig: (updates: Record<string, unknown>) =>
      ipcRenderer.invoke('bridge:update-config', updates),
    reset: () => ipcRenderer.invoke('bridge:reset'),
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

  vault: {
    isInitialized: () => ipcRenderer.invoke('vault:is-initialized'),
    isUnlocked: () => ipcRenderer.invoke('vault:is-unlocked'),
    initializeNew: (passphrase: string) => ipcRenderer.invoke('vault:initialize-new', passphrase),
    unlock: (passphrase: string) => ipcRenderer.invoke('vault:unlock', passphrase),
    resetAll: () => ipcRenderer.invoke('vault:reset-all'),
    onBootComplete: (callback: () => void) => {
      const handler = () => callback();
      ipcRenderer.on('vault:boot-complete', handler);
      return () => { ipcRenderer.removeListener('vault:boot-complete', handler); };
    },
  },

  multimedia: {
    createPodcast: (request: any) => ipcRenderer.invoke('multimedia:create-podcast', request),
    createVisual: (request: any) => ipcRenderer.invoke('multimedia:create-visual', request),
    createAudioMessage: (request: any) => ipcRenderer.invoke('multimedia:create-audio-message', request),
    createMusic: (request: any) => ipcRenderer.invoke('multimedia:create-music', request),
    getPermissions: () => ipcRenderer.invoke('multimedia:get-permissions'),
    updatePermissions: (permissions: any) => ipcRenderer.invoke('multimedia:update-permissions', permissions),
    canCreate: (level: string) => ipcRenderer.invoke('multimedia:can-create', level),
    listMedia: (type?: string) => ipcRenderer.invoke('multimedia:list-media', type),
    getSpeakerPresets: () => ipcRenderer.invoke('multimedia:get-speaker-presets'),
    getMediaDir: () => ipcRenderer.invoke('multimedia:get-media-dir'),
  },

  container: {
    execute: (payload: {
      code: string;
      language: string;
      trigger?: string;
      description?: string;
      packages?: string[];
      sourcePath?: string;
      limits?: Record<string, number>;
      network?: string;
      env?: Record<string, string>;
    }) => ipcRenderer.invoke('container:execute', payload),
    cancel: (taskId: string) => ipcRenderer.invoke('container:cancel', taskId),
    status: () => ipcRenderer.invoke('container:status'),
    list: () => ipcRenderer.invoke('container:list'),
    get: (taskId: string) => ipcRenderer.invoke('container:get', taskId),
    available: () => ipcRenderer.invoke('container:available'),
    activeCount: () => ipcRenderer.invoke('container:active-count'),
  },

  delegation: {
    registerRoot: (taskId: string, agentType: string, description: string, trustTier?: string) =>
      ipcRenderer.invoke('delegation:register-root', taskId, agentType, description, trustTier),
    spawnSubAgent: (payload: Record<string, unknown>) =>
      ipcRenderer.invoke('delegation:spawn-sub-agent', payload),
    reportCompletion: (taskId: string, result: string | null, error: string | null) =>
      ipcRenderer.invoke('delegation:report-completion', taskId, result, error),
    collectResults: (parentTaskId: string) =>
      ipcRenderer.invoke('delegation:collect-results', parentTaskId),
    haltTree: (taskId: string) =>
      ipcRenderer.invoke('delegation:halt-tree', taskId),
    haltAll: () =>
      ipcRenderer.invoke('delegation:halt-all'),
    getTree: (rootId: string) =>
      ipcRenderer.invoke('delegation:get-tree', rootId),
    getNode: (taskId: string) =>
      ipcRenderer.invoke('delegation:get-node', taskId),
    getActiveTrees: () =>
      ipcRenderer.invoke('delegation:get-active-trees'),
    getAllTrees: () =>
      ipcRenderer.invoke('delegation:get-all-trees'),
    getAncestry: (taskId: string) =>
      ipcRenderer.invoke('delegation:get-ancestry', taskId),
    getStats: () =>
      ipcRenderer.invoke('delegation:get-stats'),
    getConfig: () =>
      ipcRenderer.invoke('delegation:get-config'),
    updateConfig: (updates: Record<string, unknown>) =>
      ipcRenderer.invoke('delegation:update-config', updates),
    cleanup: (maxAgeMs?: number) =>
      ipcRenderer.invoke('delegation:cleanup', maxAgeMs),
    onUpdate: (callback: (update: Record<string, unknown>) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, update: Record<string, unknown>) => callback(update);
      ipcRenderer.on('delegation:update', handler);
      return () => { ipcRenderer.removeListener('delegation:update', handler); };
    },
  },

  // ── Notes ──────────────────────────────────────────────────────────

  notes: {
    list: () =>
      ipcRenderer.invoke('notes:list'),
    get: (id: string) =>
      ipcRenderer.invoke('notes:get', id),
    create: (input: { title: string; content: string }) =>
      ipcRenderer.invoke('notes:create', input),
    update: (id: string, patch: { title?: string; content?: string }) =>
      ipcRenderer.invoke('notes:update', id, patch),
    delete: (id: string) =>
      ipcRenderer.invoke('notes:delete', id),
    search: (query: string) =>
      ipcRenderer.invoke('notes:search', query),
  },

  // ── Files ─────────────────────────────────────────────────────────

  files: {
    listDirectory: (dirPath: string, showHidden?: boolean) =>
      ipcRenderer.invoke('files:list-directory', dirPath, showHidden),
    open: (filePath: string) =>
      ipcRenderer.invoke('files:open', filePath),
    showInFolder: (filePath: string) =>
      ipcRenderer.invoke('files:show-in-folder', filePath),
    getStats: (filePath: string) =>
      ipcRenderer.invoke('files:get-stats', filePath),
    exists: (filePath: string) =>
      ipcRenderer.invoke('files:exists', filePath),
    readText: (filePath: string) =>
      ipcRenderer.invoke('files:read-text', filePath),
    rename: (filePath: string, newName: string) =>
      ipcRenderer.invoke('files:rename', filePath, newName),
    delete: (filePath: string, useTrash?: boolean) =>
      ipcRenderer.invoke('files:delete', filePath, useTrash),
    copy: (srcPath: string, destDir: string) =>
      ipcRenderer.invoke('files:copy', srcPath, destDir),
    move: (srcPath: string, destDir: string) =>
      ipcRenderer.invoke('files:move', srcPath, destDir),
    createFolder: (parentDir: string, folderName: string) =>
      ipcRenderer.invoke('files:create-folder', parentDir, folderName),
    createFile: (parentDir: string, fileName: string) =>
      ipcRenderer.invoke('files:create-file', parentDir, fileName),
    copyPath: (filePath: string) =>
      ipcRenderer.invoke('files:copy-path', filePath),
    homeDir: () =>
      ipcRenderer.invoke('files:home-dir'),
  },

  // ── Weather ───────────────────────────────────────────────────────

  weather: {
    getCurrent: () =>
      ipcRenderer.invoke('weather:current'),
    getForecast: () =>
      ipcRenderer.invoke('weather:forecast'),
    setLocation: (lat: number, lon: number, city: string, region?: string) =>
      ipcRenderer.invoke('weather:set-location', lat, lon, city, region),
  },

  // ── System Monitor ────────────────────────────────────────────────

  system: {
    getStats: () =>
      ipcRenderer.invoke('system:stats'),
    getProcesses: (limit?: number) =>
      ipcRenderer.invoke('system:processes', limit),
  },

  // ── OS Primitives ──────────────────────────────────────────────────

  fileSearch: {
    search: (query: Record<string, unknown>) =>
      ipcRenderer.invoke('file-search:search', query),
    recentFiles: (limit?: number, extensions?: string[]) =>
      ipcRenderer.invoke('file-search:recent', limit, extensions),
    findDuplicates: (dirPath: string, mode?: 'name' | 'size') =>
      ipcRenderer.invoke('file-search:duplicates', dirPath, mode),
  },

  fileWatcher: {
    addWatch: (dirPath: string) =>
      ipcRenderer.invoke('file-watcher:add-watch', dirPath),
    removeWatch: (dirPath: string) =>
      ipcRenderer.invoke('file-watcher:remove-watch', dirPath),
    getWatched: () =>
      ipcRenderer.invoke('file-watcher:get-watched'),
    getEvents: (limit?: number) =>
      ipcRenderer.invoke('file-watcher:get-events', limit),
    getContext: () =>
      ipcRenderer.invoke('file-watcher:context'),
    onFileModified: (callback: (data: { path: string; action: string; size: number; timestamp: number }) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: any) => callback(data);
      ipcRenderer.on('file:modified', handler);
      return () => { ipcRenderer.removeListener('file:modified', handler); };
    },
  },

  osEvents: {
    getPowerState: () =>
      ipcRenderer.invoke('os-events:power-state'),
    getRecentEvents: (limit?: number) =>
      ipcRenderer.invoke('os-events:recent', limit),
    getDisplays: () =>
      ipcRenderer.invoke('os-events:displays'),
    getFileAssociation: (ext: string) =>
      ipcRenderer.invoke('os-events:file-association', ext),
    getFileAssociations: (extensions: string[]) =>
      ipcRenderer.invoke('os-events:file-associations', extensions),
    openWithDefault: (filePath: string) =>
      ipcRenderer.invoke('os-events:open-with-default', filePath),
    getStartupPrograms: () =>
      ipcRenderer.invoke('os-events:startup-programs'),
    getContext: () =>
      ipcRenderer.invoke('os-events:context'),
    onOsEvent: (callback: (event: Record<string, unknown>) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: Record<string, unknown>) => callback(data);
      ipcRenderer.on('os:event', handler);
      return () => { ipcRenderer.removeListener('os:event', handler); };
    },
  },

  appContext: {
    get: (appId: string) =>
      ipcRenderer.invoke('app-context:get', appId),
    onUpdate: (callback: (ctx: { activeStream: any; entities: any[]; briefingSummary: string | null }) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, ctx: any) => callback(ctx);
      ipcRenderer.on('app-context:update', handler);
      return () => {
        ipcRenderer.removeListener('app-context:update', handler);
      };
    },
  },

  // Legacy alias — kept for backward compatibility with existing code
  onFileModified: (callback: (data: { path: string; action: string; size: number; timestamp: number }) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, data: any) => callback(data);
    ipcRenderer.on('file:modified', handler);
    return () => { ipcRenderer.removeListener('file:modified', handler); };
  },

  // ── Sprint 7: Hardware Detection & Tier Recommendation ──────────

  hardware: {
    detect: () => ipcRenderer.invoke('hardware:detect'),
    getProfile: () => ipcRenderer.invoke('hardware:get-profile'),
    refresh: () => ipcRenderer.invoke('hardware:refresh'),
    getEffectiveVRAM: () => ipcRenderer.invoke('hardware:get-effective-vram'),
    getTier: (profile: Record<string, unknown>) => ipcRenderer.invoke('hardware:get-tier', profile),
    getModelList: (tier: string) => ipcRenderer.invoke('hardware:get-model-list', tier),
    estimateVRAM: (models: string[]) => ipcRenderer.invoke('hardware:estimate-vram', models),
    recommend: (profile: Record<string, unknown>) => ipcRenderer.invoke('hardware:recommend', profile),
    loadTierModels: (tier: string) => ipcRenderer.invoke('hardware:load-tier-models', tier),
    getLoadedModels: () => ipcRenderer.invoke('hardware:get-loaded-models'),
    getVRAMUsage: () => ipcRenderer.invoke('hardware:get-vram-usage'),
    loadModel: (name: string) => ipcRenderer.invoke('hardware:load-model', name),
    unloadModel: (name: string) => ipcRenderer.invoke('hardware:unload-model', name),
    evictLeastRecent: () => ipcRenderer.invoke('hardware:evict-least-recent'),
    getOrchestratorState: () => ipcRenderer.invoke('hardware:get-orchestrator-state'),
    markModelUsed: (name: string) => ipcRenderer.invoke('hardware:mark-model-used', name),
    onDetected: (callback: (profile: Record<string, unknown>) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: Record<string, unknown>) => callback(data);
      ipcRenderer.on('hardware:event:detected', handler);
      return () => { ipcRenderer.removeListener('hardware:event:detected', handler); };
    },
    onModelLoaded: (callback: (data: Record<string, unknown>) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: Record<string, unknown>) => callback(data);
      ipcRenderer.on('hardware:event:model-loaded', handler);
      return () => { ipcRenderer.removeListener('hardware:event:model-loaded', handler); };
    },
    onModelUnloaded: (callback: (data: Record<string, unknown>) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: Record<string, unknown>) => callback(data);
      ipcRenderer.on('hardware:event:model-unloaded', handler);
      return () => { ipcRenderer.removeListener('hardware:event:model-unloaded', handler); };
    },
    onVRAMWarning: (callback: (data: Record<string, unknown>) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: Record<string, unknown>) => callback(data);
      ipcRenderer.on('hardware:event:vram-warning', handler);
      return () => { ipcRenderer.removeListener('hardware:event:vram-warning', handler); };
    },
  },

  // ── Sprint 7: Setup Wizard ──────────────────────────────────────

  setup: {
    isFirstRun: () => ipcRenderer.invoke('setup:is-first-run'),
    getState: () => ipcRenderer.invoke('setup:get-state'),
    start: () => ipcRenderer.invoke('setup:start'),
    skip: () => ipcRenderer.invoke('setup:skip'),
    confirmTier: (tier: string) => ipcRenderer.invoke('setup:confirm-tier', tier),
    startDownload: () => ipcRenderer.invoke('setup:start-download'),
    getDownloadProgress: () => ipcRenderer.invoke('setup:get-download-progress'),
    complete: () => ipcRenderer.invoke('setup:complete'),
    reset: () => ipcRenderer.invoke('setup:reset'),
    onStateChanged: (callback: (data: Record<string, unknown>) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: Record<string, unknown>) => callback(data);
      ipcRenderer.on('setup:event:state-changed', handler);
      return () => { ipcRenderer.removeListener('setup:event:state-changed', handler); };
    },
    onDownloadProgress: (callback: (data: Record<string, unknown>) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: Record<string, unknown>) => callback(data);
      ipcRenderer.on('setup:event:download-progress', handler);
      return () => { ipcRenderer.removeListener('setup:event:download-progress', handler); };
    },
    onComplete: (callback: (data: Record<string, unknown>) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: Record<string, unknown>) => callback(data);
      ipcRenderer.on('setup:event:complete', handler);
      return () => { ipcRenderer.removeListener('setup:event:complete', handler); };
    },
    onError: (callback: (data: Record<string, unknown>) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: Record<string, unknown>) => callback(data);
      ipcRenderer.on('setup:event:error', handler);
      return () => { ipcRenderer.removeListener('setup:event:error', handler); };
    },
  },

  // ── Sprint 7: Profile Manager ───────────────────────────────────

  profile: {
    create: (opts: Record<string, unknown>) => ipcRenderer.invoke('profile:create', opts),
    get: (id: string) => ipcRenderer.invoke('profile:get', id),
    getActive: () => ipcRenderer.invoke('profile:get-active'),
    setActive: (id: string) => ipcRenderer.invoke('profile:set-active', id),
    update: (id: string, data: Record<string, unknown>) => ipcRenderer.invoke('profile:update', id, data),
    delete: (id: string) => ipcRenderer.invoke('profile:delete', id),
    export: (id: string) => ipcRenderer.invoke('profile:export', id),
    import: (json: string) => ipcRenderer.invoke('profile:import', json),
    list: () => ipcRenderer.invoke('profile:list'),
    onChanged: (callback: (data: Record<string, unknown>) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: Record<string, unknown>) => callback(data);
      ipcRenderer.on('profile:event:changed', handler);
      return () => { ipcRenderer.removeListener('profile:event:changed', handler); };
    },
    onCreated: (callback: (data: Record<string, unknown>) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: Record<string, unknown>) => callback(data);
      ipcRenderer.on('profile:event:created', handler);
      return () => { ipcRenderer.removeListener('profile:event:created', handler); };
    },
    onDeleted: (callback: (data: Record<string, unknown>) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: Record<string, unknown>) => callback(data);
      ipcRenderer.on('profile:event:deleted', handler);
      return () => { ipcRenderer.removeListener('profile:event:deleted', handler); };
    },
  },

  // ── Sprint 7: Ollama Lifecycle ──────────────────────────────────

  ollama: {
    start: () => ipcRenderer.invoke('ollama:start'),
    stop: () => ipcRenderer.invoke('ollama:stop'),
    getHealth: () => ipcRenderer.invoke('ollama:get-health'),
    getAvailableModels: () => ipcRenderer.invoke('ollama:get-available-models'),
    getLoadedModels: () => ipcRenderer.invoke('ollama:get-loaded-models'),
    isModelAvailable: (name: string) => ipcRenderer.invoke('ollama:is-model-available', name),
    pullModel: (name: string) => ipcRenderer.invoke('ollama:pull-model', name),
    onHealthy: (callback: () => void) => {
      const handler = () => callback();
      ipcRenderer.on('ollama:event:healthy', handler);
      return () => { ipcRenderer.removeListener('ollama:event:healthy', handler); };
    },
    onUnhealthy: (callback: () => void) => {
      const handler = () => callback();
      ipcRenderer.on('ollama:event:unhealthy', handler);
      return () => { ipcRenderer.removeListener('ollama:event:unhealthy', handler); };
    },
    onHealthChange: (callback: (data: Record<string, unknown>) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: Record<string, unknown>) => callback(data);
      ipcRenderer.on('ollama:event:health-change', handler);
      return () => { ipcRenderer.removeListener('ollama:event:health-change', handler); };
    },
    onModelLoaded: (callback: (data: Record<string, unknown>) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: Record<string, unknown>) => callback(data);
      ipcRenderer.on('ollama:event:model-loaded', handler);
      return () => { ipcRenderer.removeListener('ollama:event:model-loaded', handler); };
    },
    onModelUnloaded: (callback: (data: Record<string, unknown>) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: Record<string, unknown>) => callback(data);
      ipcRenderer.on('ollama:event:model-unloaded', handler);
      return () => { ipcRenderer.removeListener('ollama:event:model-unloaded', handler); };
    },
    onPullProgress: (callback: (data: Record<string, unknown>) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: Record<string, unknown>) => callback(data);
      ipcRenderer.on('ollama:event:pull-progress', handler);
      return () => { ipcRenderer.removeListener('ollama:event:pull-progress', handler); };
    },
  },

  // ── Sprint 7: Voice Pipeline ────────────────────────────────────

  voice: {
    whisper: {
      loadModel: (size?: string) => ipcRenderer.invoke('voice:whisper:load-model', size),
      unloadModel: () => ipcRenderer.invoke('voice:whisper:unload-model'),
      isReady: () => ipcRenderer.invoke('voice:whisper:is-ready'),
      transcribe: (audio: number[]) => ipcRenderer.invoke('voice:whisper:transcribe', audio),
      getAvailableModels: () => ipcRenderer.invoke('voice:whisper:get-available-models'),
      isModelDownloaded: (size?: string) => ipcRenderer.invoke('voice:whisper:is-model-downloaded', size),
      downloadModel: (size?: string) => ipcRenderer.invoke('voice:whisper:download-model', size),
      onDownloadProgress: (cb: (progress: { downloaded: number; total: number }) => void) => {
        const handler = (_event: any, progress: { downloaded: number; total: number }) => cb(progress);
        ipcRenderer.on('voice:whisper:download-progress', handler);
        return () => ipcRenderer.removeListener('voice:whisper:download-progress', handler);
      },
    },
    capture: {
      start: () => ipcRenderer.invoke('voice:capture:start'),
      stop: () => ipcRenderer.invoke('voice:capture:stop'),
      isCapturing: () => ipcRenderer.invoke('voice:capture:is-capturing'),
      getAudioLevel: () => ipcRenderer.invoke('voice:capture:get-audio-level'),
    },
    pipeline: {
      start: () => ipcRenderer.invoke('voice:pipeline:start'),
      stop: () => ipcRenderer.invoke('voice:pipeline:stop'),
      isListening: () => ipcRenderer.invoke('voice:pipeline:is-listening'),
      getStats: () => ipcRenderer.invoke('voice:pipeline:get-stats'),
    },
    tts: {
      loadEngine: (backend?: string) => ipcRenderer.invoke('voice:tts:load-engine', backend),
      unloadEngine: () => ipcRenderer.invoke('voice:tts:unload-engine'),
      isReady: () => ipcRenderer.invoke('voice:tts:is-ready'),
      synthesize: (text: string, opts?: Record<string, unknown>) => ipcRenderer.invoke('voice:tts:synthesize', text, opts),
      getAvailableVoices: () => ipcRenderer.invoke('voice:tts:get-available-voices'),
      getInfo: () => ipcRenderer.invoke('voice:tts:get-info'),
    },
    profiles: {
      getActive: () => ipcRenderer.invoke('voice:profiles:get-active'),
      setActive: (id: string) => ipcRenderer.invoke('voice:profiles:set-active', id),
      list: () => ipcRenderer.invoke('voice:profiles:list'),
      create: (opts: Record<string, unknown>) => ipcRenderer.invoke('voice:profiles:create', opts),
      delete: (id: string) => ipcRenderer.invoke('voice:profiles:delete', id),
      preview: (profileId: string) => ipcRenderer.invoke('voice:profiles:preview', profileId),
    },
    speech: {
      speak: (text: string, opts?: Record<string, unknown>) => ipcRenderer.invoke('voice:speech:speak', text, opts),
      speakImmediate: (text: string) => ipcRenderer.invoke('voice:speech:speak-immediate', text),
      stop: () => ipcRenderer.invoke('voice:speech:stop'),
      pause: () => ipcRenderer.invoke('voice:speech:pause'),
      resume: () => ipcRenderer.invoke('voice:speech:resume'),
      isSpeaking: () => ipcRenderer.invoke('voice:speech:is-speaking'),
      getQueueLength: () => ipcRenderer.invoke('voice:speech:get-queue-length'),
    },
    onVoiceStart: (callback: (data: Record<string, unknown>) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: Record<string, unknown>) => callback(data);
      ipcRenderer.on('voice:event:voice-start', handler);
      return () => { ipcRenderer.removeListener('voice:event:voice-start', handler); };
    },
    onVoiceEnd: (callback: (data: Record<string, unknown>) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: Record<string, unknown>) => callback(data);
      ipcRenderer.on('voice:event:voice-end', handler);
      return () => { ipcRenderer.removeListener('voice:event:voice-end', handler); };
    },
    onAudioChunk: (callback: (data: Record<string, unknown>) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: Record<string, unknown>) => callback(data);
      ipcRenderer.on('voice:event:audio-chunk', handler);
      return () => { ipcRenderer.removeListener('voice:event:audio-chunk', handler); };
    },
    onCaptureError: (callback: (data: Record<string, unknown>) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: Record<string, unknown>) => callback(data);
      ipcRenderer.on('voice:event:capture-error', handler);
      return () => { ipcRenderer.removeListener('voice:event:capture-error', handler); };
    },
    onTranscript: (callback: (data: Record<string, unknown>) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: Record<string, unknown>) => callback(data);
      ipcRenderer.on('voice:event:transcript', handler);
      return () => { ipcRenderer.removeListener('voice:event:transcript', handler); };
    },
    onPartial: (callback: (data: Record<string, unknown>) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: Record<string, unknown>) => callback(data);
      ipcRenderer.on('voice:event:partial', handler);
      return () => { ipcRenderer.removeListener('voice:event:partial', handler); };
    },
    onPipelineError: (callback: (data: Record<string, unknown>) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: Record<string, unknown>) => callback(data);
      ipcRenderer.on('voice:event:pipeline-error', handler);
      return () => { ipcRenderer.removeListener('voice:event:pipeline-error', handler); };
    },
    onUtteranceStart: (callback: (data: Record<string, unknown>) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: Record<string, unknown>) => callback(data);
      ipcRenderer.on('voice:event:utterance-start', handler);
      return () => { ipcRenderer.removeListener('voice:event:utterance-start', handler); };
    },
    onUtteranceEnd: (callback: (data: Record<string, unknown>) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: Record<string, unknown>) => callback(data);
      ipcRenderer.on('voice:event:utterance-end', handler);
      return () => { ipcRenderer.removeListener('voice:event:utterance-end', handler); };
    },
    onQueueEmpty: (callback: (data: Record<string, unknown>) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: Record<string, unknown>) => callback(data);
      ipcRenderer.on('voice:event:queue-empty', handler);
      return () => { ipcRenderer.removeListener('voice:event:queue-empty', handler); };
    },
    onInterrupted: (callback: (data: Record<string, unknown>) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: Record<string, unknown>) => callback(data);
      ipcRenderer.on('voice:event:interrupted', handler);
      return () => { ipcRenderer.removeListener('voice:event:interrupted', handler); };
    },
  },

  // ── Local Voice Conversation (zero-cloud interview fallback) ────

  localConversation: {
    start: (systemPrompt: string, tools: unknown[], initialPrompt?: string) =>
      ipcRenderer.invoke('local-conversation:start', systemPrompt, tools, initialPrompt),
    sendText: (text: string) =>
      ipcRenderer.invoke('local-conversation:send', text),
    stop: () => ipcRenderer.invoke('local-conversation:stop'),

    onStarted: (cb: () => void) => {
      const handler = () => cb();
      ipcRenderer.on('local-conversation:event:started', handler);
      return () => { ipcRenderer.removeListener('local-conversation:event:started', handler); };
    },
    onTranscript: (cb: (text: string) => void) => {
      const handler = (_e: Electron.IpcRendererEvent, text: string) => cb(text);
      ipcRenderer.on('local-conversation:event:transcript', handler);
      return () => { ipcRenderer.removeListener('local-conversation:event:transcript', handler); };
    },
    onResponse: (cb: (text: string) => void) => {
      const handler = (_e: Electron.IpcRendererEvent, text: string) => cb(text);
      ipcRenderer.on('local-conversation:event:response', handler);
      return () => { ipcRenderer.removeListener('local-conversation:event:response', handler); };
    },
    onAgentFinalized: (cb: (config: Record<string, unknown>) => void) => {
      const handler = (_e: Electron.IpcRendererEvent, config: Record<string, unknown>) => cb(config);
      ipcRenderer.on('local-conversation:event:agent-finalized', handler);
      return () => { ipcRenderer.removeListener('local-conversation:event:agent-finalized', handler); };
    },
    onError: (cb: (error: string) => void) => {
      const handler = (_e: Electron.IpcRendererEvent, error: string) => cb(error);
      ipcRenderer.on('local-conversation:event:error', handler);
      return () => { ipcRenderer.removeListener('local-conversation:event:error', handler); };
    },
  },

  // ── Sprint 7: Vision Pipeline ───────────────────────────────────

  vision: {
    loadModel: (opts?: Record<string, unknown>) => ipcRenderer.invoke('vision:load-model', opts),
    unloadModel: () => ipcRenderer.invoke('vision:unload-model'),
    describe: (imageBase64: string, opts?: Record<string, unknown>) => ipcRenderer.invoke('vision:describe', imageBase64, opts),
    answer: (imageBase64: string, question: string) => ipcRenderer.invoke('vision:answer', imageBase64, question),
    isReady: () => ipcRenderer.invoke('vision:is-ready'),
    getModelInfo: () => ipcRenderer.invoke('vision:get-model-info'),
    screen: {
      captureScreen: () => ipcRenderer.invoke('vision:screen:capture-screen'),
      captureWindow: (windowId: string) => ipcRenderer.invoke('vision:screen:capture-window', windowId),
      captureRegion: (region: Record<string, unknown>) => ipcRenderer.invoke('vision:screen:capture-region', region),
      getContext: () => ipcRenderer.invoke('vision:screen:get-context'),
      startAutoCapture: (intervalMs?: number) => ipcRenderer.invoke('vision:screen:start-auto-capture', intervalMs),
      stopAutoCapture: () => ipcRenderer.invoke('vision:screen:stop-auto-capture'),
    },
    understand: {
      processImage: (imageBase64: string, opts?: Record<string, unknown>) => ipcRenderer.invoke('vision:understand:process-image', imageBase64, opts),
      processClipboard: () => ipcRenderer.invoke('vision:understand:process-clipboard'),
      handleDrop: (imageBase64: string) => ipcRenderer.invoke('vision:understand:handle-drop', imageBase64),
      handleFileSelect: (filePath: string) => ipcRenderer.invoke('vision:understand:handle-file-select', filePath),
      getLastResult: () => ipcRenderer.invoke('vision:understand:get-last-result'),
    },
    onContextUpdate: (callback: (data: Record<string, unknown>) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: Record<string, unknown>) => callback(data);
      ipcRenderer.on('vision:event:context-update', handler);
      return () => { ipcRenderer.removeListener('vision:event:context-update', handler); };
    },
    onImageResult: (callback: (data: Record<string, unknown>) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: Record<string, unknown>) => callback(data);
      ipcRenderer.on('vision:event:image-result', handler);
      return () => { ipcRenderer.removeListener('vision:event:image-result', handler); };
    },
  },

  window: {
    minimize: () => ipcRenderer.invoke('window:minimize'),
    maximize: () => ipcRenderer.invoke('window:maximize'),
    close: () => ipcRenderer.invoke('window:close'),
  },
});
