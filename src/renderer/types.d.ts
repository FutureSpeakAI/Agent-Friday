export {};

// ─── CSS WebkitAppRegion augmentation (for Electron drag regions) ──────────
declare module 'react' {
  interface CSSProperties {
    WebkitAppRegion?: 'drag' | 'no-drag' | 'none';
  }
}

declare global {
  // ─── Web Speech API ambient types (for useWakeWord.ts) ───────────────────
  interface SpeechRecognition extends EventTarget {
    continuous: boolean;
    grammars: any;
    interimResults: boolean;
    lang: string;
    maxAlternatives: number;
    onaudioend: ((this: SpeechRecognition, ev: Event) => any) | null;
    onaudiostart: ((this: SpeechRecognition, ev: Event) => any) | null;
    onend: ((this: SpeechRecognition, ev: Event) => any) | null;
    onerror: ((this: SpeechRecognition, ev: SpeechRecognitionErrorEvent) => any) | null;
    onnomatch: ((this: SpeechRecognition, ev: SpeechRecognitionEvent) => any) | null;
    onresult: ((this: SpeechRecognition, ev: SpeechRecognitionEvent) => any) | null;
    onsoundend: ((this: SpeechRecognition, ev: Event) => any) | null;
    onsoundstart: ((this: SpeechRecognition, ev: Event) => any) | null;
    onspeechend: ((this: SpeechRecognition, ev: Event) => any) | null;
    onspeechstart: ((this: SpeechRecognition, ev: Event) => any) | null;
    onstart: ((this: SpeechRecognition, ev: Event) => any) | null;
    abort(): void;
    start(): void;
    stop(): void;
  }

  var SpeechRecognition: {
    prototype: SpeechRecognition;
    new(): SpeechRecognition;
  };

  interface SpeechRecognitionEvent extends Event {
    readonly resultIndex: number;
    readonly results: SpeechRecognitionResultList;
  }

  interface SpeechRecognitionResultList {
    readonly length: number;
    item(index: number): SpeechRecognitionResult;
    [index: number]: SpeechRecognitionResult;
  }

  interface SpeechRecognitionResult {
    readonly isFinal: boolean;
    readonly length: number;
    item(index: number): SpeechRecognitionAlternative;
    [index: number]: SpeechRecognitionAlternative;
  }

  interface SpeechRecognitionAlternative {
    readonly confidence: number;
    readonly transcript: string;
  }

  interface SpeechRecognitionErrorEvent extends Event {
    readonly error: string;
    readonly message: string;
  }

  interface Window {
    eve: {
      getApiPort: () => Promise<number>;
      getGeminiApiKey: () => Promise<string>;
      getLiveSystemInstruction: () => Promise<string>;

      mcp: {
        listTools: () => Promise<Array<{ name: string; description?: string; inputSchema?: unknown }>>;
        callTool: (name: string, args: Record<string, unknown>) => Promise<unknown>;
        getStatus: () => Promise<any>;
        addServer: (config: any) => Promise<any>;
      };

      memory: {
        getShortTerm: () => Promise<Array<{ role: string; content: string; timestamp: number }>>;
        getMediumTerm: () => Promise<
          Array<{
            id: string;
            observation: string;
            category: string;
            confidence: number;
            occurrences: number;
          }>
        >;
        getLongTerm: () => Promise<
          Array<{
            id: string;
            fact: string;
            category: string;
            confirmed: boolean;
            source: string;
          }>
        >;
        updateShortTerm: (messages: Array<{ role: string; content: string }>) => Promise<void>;
        extract: (history: Array<{ role: string; content: string }>) => Promise<void>;
        updateLongTerm: (id: string, updates: Record<string, unknown>) => Promise<void>;
        deleteLongTerm: (id: string) => Promise<void>;
        deleteMediumTerm: (id: string) => Promise<void>;
        addImmediate: (fact: string, category: string) => Promise<void>;
      };

      desktop: {
        listTools: () => Promise<
          Array<{ name: string; description: string; parameters: Record<string, unknown> }>
        >;
        callTool: (
          name: string,
          args: Record<string, unknown>
        ) => Promise<{ result?: string; error?: string }>;
        focusWindow: (target: string) => Promise<{ result?: string; error?: string }>;
      };

      browser: {
        listTools: () => Promise<
          Array<{ name: string; description: string; parameters: Record<string, unknown> }>
        >;
        callTool: (
          name: string,
          args: Record<string, unknown>
        ) => Promise<string>;
      };

      soc: {
        listTools: () => Promise<
          Array<{ name: string; description: string; parameters: Record<string, unknown> }>
        >;
        callTool: (
          name: string,
          args: Record<string, unknown>
        ) => Promise<unknown>;
        checkDeps: () => Promise<{
          soc: Record<string, boolean>;
          browser: Record<string, boolean>;
        } | { error: string }>;
        startBridge: () => Promise<{ status: string } | { error: string }>;
        stopBridge: () => Promise<{ status: string } | { error: string }>;
        bridgeStatus: () => Promise<{ running: boolean }>;
      };

      gitLoader: {
        load: (repoUrl: string, options?: {
          branch?: string;
          sparse?: string[];
          maxFileSize?: number;
          includePatterns?: string[];
          excludePatterns?: string[];
        }) => Promise<{
          id: string;
          name: string;
          owner: string;
          branch: string;
          description: string;
          files: number;
          totalSize: number;
          loadedAt: number;
        }>;
        getTree: (repoId: string) => Promise<Array<{
          path: string;
          type: 'file' | 'directory';
          size?: number;
          language?: string;
        }>>;
        getFile: (repoId: string, filePath: string) => Promise<{
          path: string;
          content: string;
          language: string;
          size: number;
        }>;
        search: (repoId: string, query: string, options?: {
          filePattern?: string;
          maxResults?: number;
          contextLines?: number;
        }) => Promise<Array<{
          file: string;
          line: number;
          content: string;
          context: string[];
        }>>;
        getReadme: (repoId: string) => Promise<string | null>;
        getSummary: (repoId: string) => Promise<{
          name: string;
          description: string;
          language: string;
          topics: string[];
          structure: string;
          keyFiles: string[];
          dependencies: Record<string, string>;
        }>;
        listLoaded: () => Promise<Array<{
          id: string;
          name: string;
          owner: string;
          branch: string;
          files: number;
          loadedAt: number;
        }>>;
        unload: (repoId: string) => Promise<boolean>;
        listTools: () => Promise<any>;
        callTool: (name: string, args: Record<string, unknown>) => Promise<any>;
      };

      sessionHealth: {
        get: () => Promise<Record<string, unknown>>;
        reset: () => Promise<void>;
        sessionStarted: () => Promise<void>;
        recordToolCall: (name: string, success: boolean, durationMs: number) => Promise<void>;
        recordError: (source: string, message: string) => Promise<void>;
        recordWsClose: (code: number, reason: string) => Promise<void>;
        recordReconnect: (type: 'preemptive' | 'auto-retry', success: boolean) => Promise<void>;
        recordVoiceAnchor: () => Promise<void>;
        recordPromptSize: (chars: number) => Promise<void>;
        onUpdate: (callback: (summary: Record<string, unknown>) => void) => () => void;
      };

      scheduler: {
        listTools: () => Promise<
          Array<{ name: string; description: string; parameters: Record<string, unknown> }>
        >;
        createTask: (params: Record<string, unknown>) => Promise<{
          id: string;
          description: string;
          type: string;
          action: string;
          payload: string;
          enabled: boolean;
        }>;
        listTasks: () => Promise<
          Array<{
            id: string;
            description: string;
            type: string;
            action: string;
            payload: string;
            enabled: boolean;
            triggerTime?: number;
            cronPattern?: string;
          }>
        >;
        deleteTask: (id: string) => Promise<boolean>;
        onTaskFired: (
          callback: (task: { id: string; description: string; action: string; payload: string }) => void
        ) => () => void;
      };

      predictor: {
        recordInteraction: () => Promise<void>;
        onSuggestion: (
          callback: (suggestion: { type: string; message: string; confidence: number }) => void
        ) => () => void;
      };

      onboarding: {
        isFirstRun: () => Promise<boolean>;
        isComplete: () => Promise<boolean>;
        getAgentConfig: () => Promise<{
          agentName: string;
          agentVoice: string;
          agentGender: string;
          agentAccent: string;
          agentBackstory: string;
          agentTraits: string[];
          agentIdentityLine: string;
          userName: string;
          onboardingComplete: boolean;
        }>;
        getToolDeclarations: () => Promise<Array<{
          name: string;
          description: string;
          parameters: Record<string, unknown>;
        }>>;
        getFirstGreeting: () => Promise<string>;
        finalizeAgent: (config: Record<string, unknown>) => Promise<{ success: boolean }>;
      };

      intelligence: {
        getBriefing: () => Promise<string>;
        listAll: () => Promise<Array<{
          id: string;
          topic: string;
          content: string;
          createdAt: number;
          delivered: boolean;
          priority: 'high' | 'medium' | 'low';
        }>>;
        setup: (
          topics: Array<{ topic: string; schedule: string; priority: string }>
        ) => Promise<string>;
      };

      screenCapture: {
        start: () => Promise<void>;
        stop: () => Promise<void>;
        onFrame: (callback: (frame: string) => void) => () => void;
      };

      ambient: {
        getState: () => Promise<{
          activeApp: string;
          windowTitle: string;
          appDurations: Record<string, number>;
          focusStreak: number;
          inferredTask: string;
          lastUpdated: number;
        }>;
        getContextString: () => Promise<string>;
      };

      episodic: {
        create: (
          transcript: Array<{ role: string; text: string }>,
          startTime: number,
          endTime: number
        ) => Promise<{
          id: string;
          startTime: number;
          endTime: number;
          durationSeconds: number;
          summary: string;
          topics: string[];
          emotionalTone: string;
          keyDecisions: string[];
          turnCount: number;
        } | null>;
        list: () => Promise<
          Array<{
            id: string;
            startTime: number;
            endTime: number;
            durationSeconds: number;
            summary: string;
            topics: string[];
            emotionalTone: string;
            keyDecisions: string[];
            turnCount: number;
          }>
        >;
        search: (query: string) => Promise<
          Array<{
            id: string;
            startTime: number;
            endTime: number;
            durationSeconds: number;
            summary: string;
            topics: string[];
            emotionalTone: string;
            keyDecisions: string[];
            turnCount: number;
          }>
        >;
        get: (id: string) => Promise<{
          id: string;
          startTime: number;
          endTime: number;
          durationSeconds: number;
          summary: string;
          topics: string[];
          emotionalTone: string;
          keyDecisions: string[];
          turnCount: number;
        } | undefined>;
        delete: (id: string) => Promise<boolean>;
        recent: (count?: number) => Promise<
          Array<{
            id: string;
            startTime: number;
            endTime: number;
            durationSeconds: number;
            summary: string;
            topics: string[];
            emotionalTone: string;
            keyDecisions: string[];
            turnCount: number;
          }>
        >;
      };

      search: {
        query: (
          query: string,
          options?: {
            maxResults?: number;
            minScore?: number;
            types?: Array<'long-term' | 'medium-term' | 'episode' | 'document'>;
          }
        ) => Promise<
          Array<{
            id: string;
            text: string;
            type: 'long-term' | 'medium-term' | 'episode' | 'document';
            meta: Record<string, unknown>;
            score: number;
          }>
        >;
        stats: () => Promise<Record<string, number>>;
      };

      agents: {
        spawn: (
          agentType: string,
          description: string,
          input: Record<string, unknown>
        ) => Promise<{
          id: string;
          agentType: string;
          description: string;
          status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
          progress: number;
          createdAt: number;
        }>;
        list: (status?: string) => Promise<
          Array<{
            id: string;
            agentType: string;
            description: string;
            status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
            progress: number;
            result?: string;
            error?: string;
            logs: string[];
            createdAt: number;
            startedAt?: number;
            completedAt?: number;
            parentId?: string;
          }>
        >;
        get: (taskId: string) => Promise<{
          id: string;
          agentType: string;
          description: string;
          status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
          progress: number;
          result?: string;
          error?: string;
          logs: string[];
          createdAt: number;
          startedAt?: number;
          completedAt?: number;
          parentId?: string;
        } | undefined>;
        cancel: (taskId: string) => Promise<boolean>;
        getTypes: () => Promise<Array<{ name: string; description: string }>>;
        onUpdate: (
          callback: (task: {
            id: string;
            agentType: string;
            description: string;
            status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
            progress: number;
            result?: string;
            error?: string;
            logs: string[];
            createdAt: number;
            startedAt?: number;
            completedAt?: number;
            parentId?: string;
            windowTitle?: string;
          }) => void
        ) => () => void;
        onSpeak: (callback: (data: {
          taskId: string;
          personaId: string;
          personaName: string;
          personaRole: string;
          audioBase64: string;
          contentType: string;
          durationEstimate: number;
          spokenText: string;
        }) => void) => () => void;
      };

      sentiment: {
        analyse: (text: string) => Promise<string>;
        getState: () => Promise<{
          currentMood: string;
          confidence: number;
          energyLevel: number;
          moodStreak: number;
          lastAnalysed: number;
        }>;
        getMoodLog: () => Promise<
          Array<{
            mood: string;
            confidence: number;
            energy: number;
            timestamp: number;
            trigger?: string;
          }>
        >;
      };

      confirmation: {
        onRequest: (
          callback: (req: { id: string; toolName: string; description: string }) => void
        ) => () => void;
        respond: (id: string, approved: boolean) => Promise<void>;
      };

      selfImprove: {
        readFile: (filePath: string) => Promise<string>;
        listFiles: (dirPath: string) => Promise<string[]>;
        proposeChange: (
          filePath: string,
          newContent: string,
          description: string
        ) => Promise<{ approved: boolean; message: string }>;
        onProposal: (
          callback: (proposal: { id: string; filePath: string; description: string; diff: string }) => void
        ) => () => void;
        respondToProposal: (id: string, approved: boolean) => Promise<void>;
      };

      notifications: {
        getRecent: () => Promise<
          Array<{
            app: string;
            title: string;
            body: string;
            timestamp: number;
          }>
        >;
        onCaptured: (
          callback: (notif: { app: string; title: string; body: string; timestamp: number }) => void
        ) => () => void;
      };

      settings: {
        get: () => Promise<{
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
          preferredProvider: 'anthropic' | 'openrouter';
          openrouterModel: string;
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
        }>;
        setAutoLaunch: (enabled: boolean) => Promise<void>;
        setAutoScreenCapture: (enabled: boolean) => Promise<void>;
        setApiKey: (key: 'gemini' | 'anthropic' | 'elevenlabs' | 'firecrawl' | 'perplexity' | 'openai' | 'openrouter', value: string) => Promise<void>;
        setObsidianVaultPath: (vaultPath: string) => Promise<void>;
        set: (key: string, value: unknown) => Promise<void>;
      };

      clipboard: {
        getRecent: (count?: number) => Promise<
          Array<{
            text: string;
            type: 'url' | 'code' | 'email' | 'json' | 'path' | 'text' | 'empty';
            timestamp: number;
            preview: string;
          }>
        >;
        getCurrent: () => Promise<{
          text: string;
          type: 'url' | 'code' | 'email' | 'json' | 'path' | 'text' | 'empty';
          timestamp: number;
          preview: string;
        } | null>;
        onChanged: (
          callback: (entry: { type: string; preview: string; timestamp: number }) => void
        ) => () => void;
      };

      project: {
        watch: (rootPath: string) => Promise<{
          id: string;
          name: string;
          rootPath: string;
          type: string;
          framework?: string;
          description?: string;
          gitBranch?: string;
          gitStatus?: string;
          recentChanges: string[];
          keyFiles: string[];
          structure: string[];
          lastScanned: number;
        }>;
        list: () => Promise<
          Array<{
            id: string;
            name: string;
            rootPath: string;
            type: string;
            framework?: string;
            description?: string;
            gitBranch?: string;
            gitStatus?: string;
            recentChanges: string[];
            keyFiles: string[];
            structure: string[];
            lastScanned: number;
          }>
        >;
        get: (rootPath: string) => Promise<{
          id: string;
          name: string;
          rootPath: string;
          type: string;
          framework?: string;
          description?: string;
          gitBranch?: string;
          gitStatus?: string;
          recentChanges: string[];
          keyFiles: string[];
          structure: string[];
          lastScanned: number;
        } | undefined>;
        onUpdated: (
          callback: (profile: Record<string, unknown>) => void
        ) => () => void;
      };

      documents: {
        pickAndIngest: () => Promise<
          Array<{
            id: string;
            filename: string;
            filePath: string;
            mimeType: string;
            size: number;
            summary: string;
            content: string;
            ingestedAt: number;
          }>
        >;
        ingestFile: (filePath: string) => Promise<{
          id: string;
          filename: string;
          filePath: string;
          mimeType: string;
          size: number;
          summary: string;
          content: string;
          ingestedAt: number;
        } | null>;
        list: () => Promise<
          Array<{
            id: string;
            filename: string;
            filePath: string;
            mimeType: string;
            size: number;
            summary: string;
            content: string;
            ingestedAt: number;
          }>
        >;
        get: (id: string) => Promise<{
          id: string;
          filename: string;
          filePath: string;
          mimeType: string;
          size: number;
          summary: string;
          content: string;
          ingestedAt: number;
        } | undefined>;
        search: (query: string) => Promise<
          Array<{
            id: string;
            filename: string;
            filePath: string;
            mimeType: string;
            size: number;
            summary: string;
            content: string;
            ingestedAt: number;
          }>
        >;
      };

      calendar: {
        authenticate: () => Promise<boolean>;
        isAuthenticated: () => Promise<boolean>;
        getUpcoming: (count?: number) => Promise<
          Array<{
            id: string;
            summary: string;
            description: string;
            location: string;
            start: string;
            end: string;
            attendees: string[];
            organizer: string;
            hangoutLink: string;
            status: string;
            isAllDay: boolean;
          }>
        >;
        getToday: () => Promise<
          Array<{
            id: string;
            summary: string;
            description: string;
            location: string;
            start: string;
            end: string;
            attendees: string[];
            organizer: string;
            hangoutLink: string;
            status: string;
            isAllDay: boolean;
          }>
        >;
        createEvent: (opts: {
          summary: string;
          description?: string;
          startTime: string;
          endTime: string;
          attendees?: string[];
          location?: string;
        }) => Promise<{
          id: string;
          summary: string;
          description: string;
          location: string;
          start: string;
          end: string;
          attendees: string[];
          organizer: string;
          hangoutLink: string;
          status: string;
          isAllDay: boolean;
        } | null>;
      };

      gateway: {
        getStatus: () => Promise<{ enabled: boolean; adapters: string[]; activeSessions: number }>;
        setEnabled: (enabled: boolean) => Promise<{ enabled: boolean; adapters: string[]; activeSessions: number }>;
        getPendingPairings: () => Promise<Array<{ code: string; platform: string; timestamp: number }>>;
        getPairedIdentities: () => Promise<Array<{ id: string; name: string; platform: string; tier: string }>>;
        approvePairing: (code: string, tier?: string) => Promise<void>;
        revokePairing: (identityId: string) => Promise<void>;
        getActiveSessions: () => Promise<Array<{ id: string; platform: string; identity: string; startTime: number }>>;
      };

      meetingPrep: {
        onBriefing: (
          callback: (briefing: {
            eventId: string;
            eventTitle: string;
            startTime: string;
            minutesUntil: number;
            attendeeContext: Array<{
              name: string;
              memories: string[];
              recentTopics: string[];
            }>;
            relevantProjects: string[];
            suggestedTopics: string[];
            briefingText: string;
          }) => void
        ) => () => void;
      };

      communications: {
        draft: (request: {
          type: 'email' | 'message' | 'reply' | 'follow-up';
          to: string;
          subject?: string;
          context: string;
          tone?: 'formal' | 'casual' | 'friendly' | 'professional' | 'urgent';
          originalMessage?: string;
          maxLength?: 'short' | 'medium' | 'long';
        }) => Promise<{
          id: string;
          type: string;
          subject: string;
          body: string;
          to: string;
          tone: string;
          createdAt: number;
        }>;
        refine: (draftId: string, instruction: string) => Promise<{
          id: string;
          type: string;
          subject: string;
          body: string;
          to: string;
          tone: string;
          createdAt: number;
        } | null>;
        copy: (draftId: string) => Promise<boolean>;
        openEmail: (draftId: string) => Promise<boolean>;
        listDrafts: () => Promise<
          Array<{
            id: string;
            type: string;
            subject: string;
            body: string;
            to: string;
            tone: string;
            createdAt: number;
          }>
        >;
      };

      connectors: {
        listTools: () => Promise<Array<{
          name: string;
          description: string;
          parameters: { type: string; properties: Record<string, unknown>; required?: string[] };
        }>>;
        callTool: (name: string, args: Record<string, unknown>) => Promise<{ result?: string; error?: string }>;
        isConnectorTool: (name: string) => Promise<boolean>;
        status: () => Promise<{
          initialized: boolean;
          totalConnectors: number;
          availableConnectors: number;
          totalTools: number;
          connectors: Array<{
            id: string;
            label: string;
            category: string;
            available: boolean;
            toolCount: number;
          }>;
        }>;
        getToolRouting: () => Promise<string>;
      };

      callIntegration: {
        isVirtualAudioAvailable: () => Promise<boolean>;
        enterCallMode: (meetingUrl?: string) => Promise<void>;
        exitCallMode: () => Promise<void>;
        isInCallMode: () => Promise<boolean>;
        openMeetingUrl: (url: string) => Promise<void>;
        getContextString: () => Promise<string>;
      };

      psychProfile: {
        generate: (responses: {
          voicePreference: string;
          socialDescription: string;
          motherRelationship: string;
        }) => Promise<{
          openness: number;
          trustReadiness: number;
          emotionalDepth: number;
          humorAsArmor: boolean;
          guardedness: number;
          connectionStyle: 'warm' | 'intellectual' | 'playful' | 'reserved';
          needsFrom: string;
          approachStrategy: string;
          motherRelationshipInsight: string;
          rawAnalysis: string;
        }>;
        get: () => Promise<{
          openness: number;
          trustReadiness: number;
          emotionalDepth: number;
          humorAsArmor: boolean;
          guardedness: number;
          connectionStyle: string;
          needsFrom: string;
          approachStrategy: string;
          motherRelationshipInsight: string;
          rawAnalysis: string;
        } | null>;
        saveIntakeResponses: (responses: {
          voicePreference: string;
          socialDescription: string;
          motherRelationship: string;
        }) => Promise<any>;
        getIntakeResponses: () => Promise<any>;
      };

      agentTrust: {
        getState: () => Promise<{
          score: number;
          frustrationSignals: number;
          corrections: number;
          successStreak: number;
          lastFrustration: number;
          recoveryMode: boolean;
        }>;
        processMessage: (message: string) => Promise<any>;
        resetSession: () => Promise<any>;
        getPromptBlock: () => Promise<string>;
        getLabel: () => Promise<string>;
        boost: (amount: number) => Promise<any>;
      };

      featureSetup: {
        initialize: () => Promise<any>;
        getState: () => Promise<{
          currentStep: number;
          steps: Array<{ id: string; status: 'pending' | 'completed' | 'skipped' }>;
        } | null>;
        getPrompt: (step: string) => Promise<string>;
        advance: (step: string, action: string) => Promise<{
          currentStep: number;
          steps: Array<{ id: string; status: 'pending' | 'completed' | 'skipped' }>;
        }>;
        isComplete: () => Promise<boolean>;
        getCurrentStep: () => Promise<string | null>;
        getToolDeclaration: () => Promise<any>;
        getToolDeclarations: () => Promise<Array<{
          name: string;
          description: string;
          parameters: Record<string, unknown>;
        }>>;
      };

      evolution: {
        getState: () => Promise<{
          sessionCount: number;
          primaryHue: number;
          secondaryHue: number;
          particleSpeed: number;
          cubeFragmentation: number;
          coreScale: number;
          dustDensity: number;
          glowIntensity: number;
        } | null>;
        incrementSession: () => Promise<{
          sessionCount: number;
          primaryHue: number;
          secondaryHue: number;
          particleSpeed: number;
          cubeFragmentation: number;
          coreScale: number;
          dustDensity: number;
          glowIntensity: number;
        }>;
      };

      desktopEvolution: {
        getIndex: () => Promise<number>;
        setIndex: (index: number) => Promise<void>;
        getTransitionState: () => Promise<{
          currentIndex: number;
          targetIndex: number;
          blend: number;
          lastChange: number;
        }>;
      };

      artEvolution: {
        getState: () => Promise<any>;
        getLatest: () => Promise<any>;
        check: () => Promise<any>;
        force: () => Promise<any>;
      };

      trustGraph: {
        lookup: (name: string) => Promise<any>;
        updateEvidence: (personName: string, evidence: Record<string, unknown>) => Promise<any>;
        logComm: (personName: string, event: Record<string, unknown>) => Promise<any>;
        addAlias: (personId: string, alias: string, type: string) => Promise<any>;
        getAll: () => Promise<any>;
        getContext: (personId: string) => Promise<any>;
        getPromptContext: () => Promise<any>;
        findByDomain: (domain: string) => Promise<any>;
        getMostTrusted: (limit?: number) => Promise<any>;
        getRecent: (limit?: number) => Promise<any>;
        updateNotes: (personId: string, notes: string) => Promise<any>;
        linkPersons: (idA: string, idB: string, label: string) => Promise<any>;
      };

      voiceAudition: {
        generateSample: (voiceName: string, customPhrase?: string) => Promise<{
          audio: string;
          mimeType: string;
        } | null>;
        getRecommendations: (genderPref: string) => Promise<Array<{
          name: string;
          gender: string;
          description: string;
        }>>;
        getCatalog: () => Promise<Array<{
          name: string;
          gender: string;
          description: string;
        }>>;
      };

      meetingIntel: {
        create: (opts: Record<string, unknown>) => Promise<any>;
        get: (id: string) => Promise<any>;
        list: (opts?: Record<string, unknown>) => Promise<any>;
        getActive: () => Promise<any>;
        update: (meetingId: string, updates: Record<string, unknown>) => Promise<any>;
        start: (meetingId: string) => Promise<any>;
        end: (meetingId: string, opts?: Record<string, unknown>) => Promise<any>;
        cancel: (meetingId: string) => Promise<any>;
        endActive: (transcript?: string) => Promise<any>;
        addNote: (meetingId: string, note: Record<string, unknown>) => Promise<any>;
        addNoteActive: (content: string, type?: string) => Promise<any>;
        setTranscript: (meetingId: string, transcript: string) => Promise<any>;
        setSummary: (meetingId: string, summary: string) => Promise<any>;
        search: (query: string, limit?: number) => Promise<any>;
        stats: () => Promise<any>;
        recentSummaries: (count?: number) => Promise<any>;
        fromCalendar: (event: Record<string, unknown>) => Promise<any>;
        quickStart: (meetingUrl: string, name?: string) => Promise<any>;
        refreshIntel: (meetingId: string) => Promise<any>;
        getContext: () => Promise<any>;
      };

      integrity: {
        getState: () => Promise<{
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
        }>;
        isInSafeMode: () => Promise<boolean>;
        acknowledgeMemoryChanges: () => Promise<any>;
        verify: () => Promise<{
          lawsIntact: boolean;
          identityIntact: boolean;
          memoriesIntact: boolean;
          safeMode: boolean;
        }>;
        reset: () => Promise<{
          success: boolean;
          message: string;
        }>;
      };

      superpowers: {
        list: () => Promise<any>;
        get: (id: string) => Promise<any>;
        toggle: (id: string, enabled: boolean) => Promise<any>;
        toggleTool: (superpowerId: string, toolName: string, enabled: boolean) => Promise<any>;
        updatePermissions: (id: string, perms: Record<string, unknown>) => Promise<any>;
        install: (repoUrl: string) => Promise<any>;
        uninstall: (id: string) => Promise<any>;
        uninstallPreview: (id: string) => Promise<any>;
        usageStats: (id: string) => Promise<any>;
        enabledTools: () => Promise<any>;
        flush: () => Promise<any>;
        storeList: () => Promise<any>;
        storeGet: (id: string) => Promise<any>;
        storeConfirm: (id: string, consentToken: string) => Promise<any>;
        storeEnabledTools: () => Promise<any>;
        storeStatus: () => Promise<any>;
        storePromptContext: () => Promise<any>;
        storeNeedsAttention: () => Promise<any>;
      };

      capabilityGaps: {
        record: (taskDescription: string) => Promise<any>;
        top: (limit?: number) => Promise<any>;
        get: (gapId: string) => Promise<any>;
        generateProposals: () => Promise<any>;
        pendingProposals: () => Promise<any>;
        acceptedProposals: () => Promise<any>;
        getProposal: (proposalId: string) => Promise<any>;
        present: (proposalId: string) => Promise<any>;
        accept: (proposalId: string) => Promise<any>;
        decline: (proposalId: string) => Promise<any>;
        markInstalled: (proposalId: string) => Promise<any>;
        promptContext: () => Promise<any>;
        status: () => Promise<any>;
        prune: () => Promise<any>;
      };

      contextStream: {
        push: (event: {
          type: string;
          source: string;
          summary: string;
          data?: Record<string, unknown>;
          dedupeKey?: string;
          ttlMs?: number;
        }) => Promise<any>;
        snapshot: () => Promise<any>;
        recent: (opts?: { limit?: number; types?: string[]; sinceMs?: number }) => Promise<any>;
        byType: (type: string, limit?: number) => Promise<any>;
        latestByType: () => Promise<any>;
        contextString: () => Promise<any>;
        promptContext: () => Promise<any>;
        status: () => Promise<any>;
        prune: () => Promise<any>;
        setEnabled: (enabled: boolean) => Promise<any>;
        clear: () => Promise<any>;
      };

      contextGraph: {
        snapshot: () => Promise<any>;
        activeStream: () => Promise<any>;
        recentStreams: (limit?: number) => Promise<any>;
        streamsByTask: (task: string) => Promise<any>;
        entitiesByType: (type: string, limit?: number) => Promise<any>;
        topEntities: (limit?: number) => Promise<any>;
        activeEntities: (windowMs?: number) => Promise<any>;
        relatedEntities: (type: string, value: string, limit?: number) => Promise<any>;
        contextString: () => Promise<any>;
        promptContext: () => Promise<any>;
        status: () => Promise<any>;
      };

      toolRouter: {
        suggestions: () => Promise<any>;
        activeCategory: () => Promise<any>;
        categoryScores: () => Promise<any>;
        snapshot: () => Promise<any>;
        contextString: () => Promise<any>;
        promptContext: () => Promise<any>;
        status: () => Promise<any>;
        registerTools: (tools: Array<{ name: string; description?: string }>) => Promise<any>;
        unregisterTool: (name: string) => Promise<any>;
        config: () => Promise<any>;
      };

      commitments: {
        getActive: () => Promise<any>;
        getOverdue: () => Promise<any>;
        getByPerson: (personName: string) => Promise<any>;
        getUpcoming: (withinHours?: number) => Promise<any>;
        getById: (id: string) => Promise<any>;
        getAll: () => Promise<any>;
        add: (mention: Record<string, unknown>) => Promise<any>;
        complete: (id: string, notes?: string) => Promise<any>;
        cancel: (id: string, reason?: string) => Promise<any>;
        snooze: (id: string, untilMs: number) => Promise<any>;
        trackOutbound: (msg: { recipient: string; channel: string; summary: string }) => Promise<any>;
        recordReply: (recipient: string, channel: string) => Promise<any>;
        getUnreplied: () => Promise<any>;
        generateSuggestions: () => Promise<any>;
        getPendingSuggestions: () => Promise<any>;
        markSuggestionDelivered: (id: string) => Promise<any>;
        markSuggestionActedOn: (id: string) => Promise<any>;
        contextString: () => Promise<any>;
        promptContext: () => Promise<any>;
        status: () => Promise<any>;
        config: () => Promise<any>;
      };

      dailyBriefing: {
        generate: (type: string, sourceData: Record<string, unknown>) => Promise<any>;
        shouldGenerate: () => Promise<any>;
        adaptiveLength: (sourceData: Record<string, unknown>) => Promise<any>;
        getLatest: (type?: string) => Promise<any>;
        getLatestToday: (type: string) => Promise<any>;
        getById: (id: string) => Promise<any>;
        getHistory: (limit?: number) => Promise<any>;
        getAll: () => Promise<any>;
        markDelivered: (id: string, channel: string) => Promise<any>;
        markDeliveryFailed: (id: string, channel: string, reason: string) => Promise<any>;
        isStale: (type: string) => Promise<any>;
        scheduledTimeToday: (timeStr: string) => Promise<any>;
        formatText: (id: string) => Promise<any>;
        formatMarkdown: (id: string) => Promise<any>;
        contextString: () => Promise<any>;
        promptContext: () => Promise<any>;
        status: () => Promise<any>;
        config: () => Promise<any>;
      };

      workflowRecorder: {
        startRecording: (name: string) => Promise<any>;
        stopRecording: () => Promise<any>;
        cancelRecording: () => Promise<any>;
        recordEvent: (type: string, description: string, payload?: Record<string, unknown>) => Promise<any>;
        addAnnotation: (text: string) => Promise<any>;
        addKeyFrame: (filePath: string, activeApp: string) => Promise<any>;
        createTemplate: (recordingId: string, overrides?: Record<string, unknown>) => Promise<any>;
        deleteTemplate: (id: string) => Promise<any>;
        status: () => Promise<any>;
        getRecording: (id: string) => Promise<any>;
        getAllRecordings: () => Promise<any>;
        getRecentRecordings: (limit?: number) => Promise<any>;
        getTemplate: (id: string) => Promise<any>;
        getAllTemplates: () => Promise<any>;
        getTemplatesByTag: (tag: string) => Promise<any>;
        deleteRecording: (id: string) => Promise<any>;
        config: () => Promise<any>;
      };

      workflowExecutor: {
        execute: (templateId: string, params?: Record<string, string>, triggeredBy?: string) => Promise<any>;
        pause: () => Promise<any>;
        resume: () => Promise<any>;
        cancel: () => Promise<any>;
        provideUserResponse: (response: string) => Promise<any>;
        grantPermission: (templateId: string, opts?: Record<string, unknown>) => Promise<any>;
        revokePermission: (templateId: string) => Promise<any>;
        getPermissions: () => Promise<any>;
        activeRun: () => Promise<any>;
        isRunning: () => Promise<any>;
        runHistory: (limit?: number) => Promise<any>;
        getRun: (runId: string) => Promise<any>;
        getConfig: () => Promise<any>;
        updateConfig: (updates: Record<string, unknown>) => Promise<any>;
      };

      inbox: {
        getMessages: (opts?: Record<string, unknown>) => Promise<any>;
        getMessage: (id: string) => Promise<any>;
        getStats: () => Promise<any>;
        markRead: (ids: string | string[]) => Promise<any>;
        markUnread: (ids: string | string[]) => Promise<any>;
        archive: (ids: string | string[]) => Promise<any>;
        unarchive: (ids: string | string[]) => Promise<any>;
        delete: (ids: string | string[]) => Promise<any>;
        markAllRead: () => Promise<any>;
        getConfig: () => Promise<any>;
        updateConfig: (partial: Record<string, unknown>) => Promise<any>;
      };

      outbound: {
        createDraft: (params: Record<string, unknown>) => Promise<any>;
        getDraft: (id: string) => Promise<any>;
        editDraft: (id: string, updates: Record<string, unknown>) => Promise<any>;
        deleteDraft: (id: string) => Promise<any>;
        getDrafts: (opts?: Record<string, unknown>) => Promise<any>;
        getPending: () => Promise<any>;
        approve: (id: string) => Promise<any>;
        reject: (id: string) => Promise<any>;
        approveAll: () => Promise<any>;
        tryAutoApprove: (id: string) => Promise<any>;
        send: (id: string) => Promise<any>;
        approveAndSend: (id: string) => Promise<any>;
        sendAllApproved: () => Promise<any>;
        batchReview: () => Promise<any>;
        getStyleProfile: (personId: string) => Promise<any>;
        updateStyleProfile: (personId: string, name: string, obs: Record<string, unknown>) => Promise<any>;
        getAllStyleProfiles: () => Promise<any>;
        addStandingPermission: (params: Record<string, unknown>) => Promise<any>;
        revokeStandingPermission: (id: string) => Promise<any>;
        deleteStandingPermission: (id: string) => Promise<any>;
        getStandingPermissions: () => Promise<any>;
        getAllStandingPermissions: () => Promise<any>;
        getStats: () => Promise<any>;
        getConfig: () => Promise<any>;
        updateConfig: (partial: Record<string, unknown>) => Promise<any>;
        getPromptContext: () => Promise<any>;
      };

      intelligenceRouter: {
        classifyTask: (params: {
          messageContent: string;
          toolCount: number;
          hasImages: boolean;
          hasAudio: boolean;
          systemPromptLength: number;
          conversationLength: number;
        }) => Promise<any>;
        selectModel: (task: Record<string, unknown>) => Promise<any>;
        classifyAndRoute: (params: {
          messageContent: string;
          toolCount: number;
          hasImages: boolean;
          hasAudio: boolean;
          systemPromptLength: number;
          conversationLength: number;
        }) => Promise<any>;
        recordOutcome: (decisionId: string, outcome: {
          success: boolean;
          durationMs: number;
          inputTokens?: number;
          outputTokens?: number;
        }) => Promise<any>;
        getModel: (modelId: string) => Promise<any>;
        getAllModels: () => Promise<any>;
        getAvailableModels: () => Promise<any>;
        registerModel: (model: Record<string, unknown>) => Promise<any>;
        setModelAvailability: (modelId: string, available: boolean) => Promise<any>;
        resetModelFailures: (modelId: string) => Promise<any>;
        getDecision: (id: string) => Promise<any>;
        getRecentDecisions: (limit?: number) => Promise<any>;
        getDecisionsForModel: (modelId: string, limit?: number) => Promise<any>;
        getStats: () => Promise<any>;
        getConfig: () => Promise<any>;
        updateConfig: (partial: Record<string, unknown>) => Promise<any>;
        getPromptContext: () => Promise<any>;
      };

      agentNetwork: {
        getIdentity: () => Promise<any>;
        getAgentId: () => Promise<any>;
        generatePairingOffer: () => Promise<any>;
        getActivePairingCode: () => Promise<any>;
        acceptPairing: (remoteIdentity: Record<string, unknown>, ownerPersonId: string | null, ownerTrust: { overall: number } | null) => Promise<any>;
        recordInboundPairing: (remoteIdentity: Record<string, unknown>) => Promise<any>;
        blockAgent: (agentId: string) => Promise<any>;
        unpairAgent: (agentId: string) => Promise<any>;
        getPeer: (agentId: string) => Promise<any>;
        getAllPeers: () => Promise<any>;
        getPairedPeers: () => Promise<any>;
        getPendingPairingRequests: () => Promise<any>;
        updatePeerTrust: (agentId: string, ownerTrust: { overall: number } | null, ownerPersonId?: string) => Promise<any>;
        setAutoApproveTaskTypes: (agentId: string, taskTypes: string[]) => Promise<any>;
        updatePeerCapabilities: (agentId: string, capabilities: string[]) => Promise<any>;
        findPeersWithCapability: (capability: string) => Promise<any>;
        createMessage: (toAgentId: string, type: string, payload: Record<string, unknown>) => Promise<any>;
        processInboundMessage: (message: Record<string, unknown>) => Promise<any>;
        getMessageLog: (limit?: number) => Promise<any>;
        createDelegation: (targetAgentId: string, description: string, requiredCapabilities?: string[], deadline?: number) => Promise<any>;
        handleInboundDelegation: (requestingAgentId: string, delegationId: string, description: string, requiredCapabilities: string[], deadline: number) => Promise<any>;
        approveDelegation: (delegationId: string) => Promise<any>;
        rejectDelegation: (delegationId: string) => Promise<any>;
        startDelegation: (delegationId: string) => Promise<any>;
        completeDelegation: (delegationId: string, result: unknown) => Promise<any>;
        failDelegation: (delegationId: string, error: string) => Promise<any>;
        cancelDelegation: (delegationId: string) => Promise<any>;
        getDelegation: (delegationId: string) => Promise<any>;
        getAllDelegations: () => Promise<any>;
        getDelegationsForAgent: (agentId: string) => Promise<any>;
        getPendingInboundDelegations: () => Promise<any>;
        getStats: () => Promise<any>;
        getConfig: () => Promise<any>;
        updateConfig: (partial: Record<string, unknown>) => Promise<any>;
        getPromptContext: () => Promise<any>;
      };

      ecosystem: {
        createManifest: (opts: Record<string, unknown>) => Promise<any>;
        validateManifest: (manifest: Record<string, unknown>) => Promise<any>;
        getDeveloperKeys: () => Promise<any>;
        hasDeveloperKeys: () => Promise<any>;
        signPackage: (manifest: Record<string, unknown>) => Promise<any>;
        publishPackage: (pkg: Record<string, unknown>) => Promise<any>;
        getPublishedPackages: () => Promise<any>;
        getPublishedPackage: (packageId: string) => Promise<any>;
        unpublishPackage: (packageId: string) => Promise<any>;
        searchRegistry: (query: Record<string, unknown>) => Promise<any>;
        getRegistryListing: (packageId: string) => Promise<any>;
        searchForCapability: (description: string, keywords: string[]) => Promise<any>;
        initiatePurchase: (packageId: string, amountUsdCents: number, type?: string) => Promise<any>;
        approvePurchase: (transactionId: string, consentToken: string) => Promise<any>;
        cancelPurchase: (transactionId: string) => Promise<any>;
        executePurchase: (transactionId: string) => Promise<any>;
        getTransactions: () => Promise<any>;
        getTransactionsForPackage: (packageId: string) => Promise<any>;
        getTransaction: (transactionId: string) => Promise<any>;
        isPurchased: (packageId: string) => Promise<any>;
        getStats: () => Promise<any>;
        getConfig: () => Promise<any>;
        updateConfig: (partial: Record<string, unknown>) => Promise<any>;
        getPromptContext: () => Promise<any>;
      };

      persistence: {
        exportState: (passphrase: string, outputPath?: string) => Promise<any>;
        exportIncremental: (passphrase: string, outputPath?: string) => Promise<any>;
        importState: (archivePath: string, passphrase: string) => Promise<any>;
        validateArchive: (archivePath: string, passphrase: string) => Promise<any>;
        setAutoPassphrase: (passphrase: string) => Promise<any>;
        clearAutoPassphrase: () => Promise<any>;
        runScheduledBackup: () => Promise<any>;
        getStateFiles: () => Promise<any>;
        enumerateState: () => Promise<any>;
        getBackupHistory: () => Promise<any>;
        getLastBackup: () => Promise<any>;
        getConfig: () => Promise<any>;
        updateConfig: (partial: Record<string, unknown>) => Promise<any>;
        getPromptContext: () => Promise<any>;
        checkContinuity: () => Promise<any>;
      };

      memoryQuality: {
        assessExtraction: (results: Array<Record<string, unknown>>) => Promise<any>;
        assessRetrieval: (results: Array<Record<string, unknown>>) => Promise<any>;
        assessConsolidation: (results: Array<Record<string, unknown>>) => Promise<any>;
        assessPersonMentions: (results: Array<Record<string, unknown>>) => Promise<any>;
        buildReport: (
          extractionResults: Array<Record<string, unknown>>,
          retrievalResults: Array<Record<string, unknown>>,
          consolidationResults: Array<Record<string, unknown>>,
        ) => Promise<any>;
        getExtractionBenchmarks: () => Promise<any>;
        getRetrievalBenchmarks: () => Promise<any>;
        getConsolidationBenchmarks: () => Promise<any>;
        getLatestReport: () => Promise<any>;
        getQualityHistory: () => Promise<any>;
        getQualityTrend: (count?: number) => Promise<any>;
        getConfig: () => Promise<any>;
        updateConfig: (partial: Record<string, unknown>) => Promise<any>;
        getPromptContext: () => Promise<any>;
      };

      personalityCalibration: {
        processMessage: (text: string, responseTimeMs?: number) => Promise<any>;
        recordDismissal: () => Promise<any>;
        recordEngagement: () => Promise<any>;
        incrementSession: () => Promise<any>;
        getDimensions: () => Promise<any>;
        getState: () => Promise<any>;
        getDismissalRate: () => Promise<any>;
        getEffectiveProactivity: (isCritical: boolean) => Promise<any>;
        getHistory: () => Promise<any>;
        getExplanation: () => Promise<any>;
        getPromptContext: () => Promise<any>;
        getVisualWarmthModifier: () => Promise<any>;
        getVisualEnergyModifier: () => Promise<any>;
        getConfig: () => Promise<any>;
        updateConfig: (partial: Record<string, unknown>) => Promise<any>;
        resetDimension: (dimension: string) => Promise<any>;
        resetAll: () => Promise<any>;
      };

      memoryPersonalityBridge: {
        recordEngagement: (memoryId: string, type: string, context: string) => Promise<any>;
        getEngagements: () => Promise<any>;
        getPriorityAdjustments: () => Promise<any>;
        getExtractionGuidance: () => Promise<any>;
        getExtractionHints: () => Promise<any>;
        recomputeExtractionHints: () => Promise<any>;
        proposeProactivity: (proposal: any) => Promise<any>;
        arbitrateProactivity: () => Promise<any>;
        getProactivityCooldown: () => Promise<any>;
        getPendingProposals: () => Promise<any>;
        recordExchange: (flattery: boolean, urgency: boolean, options: number) => Promise<any>;
        getManipulationMetrics: () => Promise<any>;
        getPromptContext: () => Promise<any>;
        getState: () => Promise<any>;
        getConfig: () => Promise<any>;
        getRelevanceWeights: () => Promise<any>;
        syncMemoryToPersonality: () => Promise<any>;
        updateConfig: (updates: Record<string, unknown>) => Promise<any>;
        reset: () => Promise<any>;
      };

      shell: {
        showInFolder: (filePath: string) => Promise<void>;
        openPath: (filePath: string) => Promise<string>;
      };

      office: {
        getState: () => Promise<any>;
        isOpen: () => Promise<any>;
        requestOpen: () => void;
        requestClose: () => void;
        onFullState: (callback: (state: any) => void) => () => void;
        onAgentSpawned: (callback: (character: any) => void) => () => void;
        onAgentThought: (callback: (data: any) => void) => () => void;
        onAgentPhase: (callback: (data: any) => void) => () => void;
        onAgentCompleted: (callback: (data: any) => void) => () => void;
        onAgentStopped: (callback: (data: any) => void) => () => void;
        onAgentRemoved: (callback: (data: any) => void) => () => void;
      };

      vault: {
        isUnlocked: () => Promise<boolean>;
        isInitialized: () => Promise<boolean>;
        isRecoveryPhraseShown: () => Promise<boolean>;
        getRecoveryPhrase: () => Promise<string | null>;
        clearRecoveryPhrase: () => Promise<boolean>;
        markRecoveryPhraseShown: () => Promise<void>;
        recover: (phrase: string) => Promise<{ ok: boolean; error?: string }>;
        onRecoveryPhrase: (callback: (phrase: string) => void) => () => void;
      };

      multimedia: {
        createPodcast: (request: any) => Promise<any>;
        createVisual: (request: any) => Promise<any>;
        createAudioMessage: (request: any) => Promise<any>;
        createMusic: (request: any) => Promise<any>;
        getPermissions: () => Promise<any>;
        updatePermissions: (permissions: any) => Promise<any>;
        canCreate: (level: string) => Promise<boolean>;
        listMedia: (type?: string) => Promise<any>;
        getSpeakerPresets: () => Promise<any>;
        getMediaDir: () => Promise<string>;
      };

      onFileModified: (callback: (data: {
        path: string;
        action: string;
        size: number;
        timestamp: number;
      }) => void) => () => void;

      window: {
        minimize: () => Promise<void>;
        maximize: () => Promise<void>;
        close: () => Promise<void>;
      };
    };
    SpeechRecognition: typeof SpeechRecognition;
    webkitSpeechRecognition: typeof SpeechRecognition;
  }
}
