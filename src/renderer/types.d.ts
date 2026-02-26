export {};

declare global {
  interface Window {
    eve: {
      getApiPort: () => Promise<number>;
      getGeminiApiKey: () => Promise<string>;
      getLiveSystemInstruction: () => Promise<string>;

      mcp: {
        listTools: () => Promise<Array<{ name: string; description?: string; inputSchema?: unknown }>>;
        callTool: (name: string, args: Record<string, unknown>) => Promise<unknown>;
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
        getToolDeclaration: () => Promise<{
          name: string;
          description: string;
          parameters: Record<string, unknown>;
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
          geminiKeyHint: string;
          anthropicKeyHint: string;
          elevenLabsKeyHint: string;
          hasOpenaiKey: boolean;
          openaiKeyHint: string;
          hasPerplexityKey: boolean;
          perplexityKeyHint: string;
          hasFirecrawlKey: boolean;
          firecrawlKeyHint: string;
          agentVoicesEnabled: boolean;
          wakeWordEnabled: boolean;
          notificationWhisperEnabled: boolean;
          notificationAllowedApps: string[];
          clipboardIntelligenceEnabled: boolean;
          googleCalendarEnabled: boolean;
        }>;
        setAutoLaunch: (enabled: boolean) => Promise<void>;
        setAutoScreenCapture: (enabled: boolean) => Promise<void>;
        setApiKey: (key: 'gemini' | 'anthropic' | 'elevenlabs' | 'openai' | 'perplexity' | 'firecrawl', value: string) => Promise<void>;
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
      };

      featureSetup: {
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

      shell: {
        showInFolder: (filePath: string) => Promise<void>;
        openPath: (filePath: string) => Promise<string>;
      };

      onFileModified: (callback: (data: {
        path: string;
        action: string;
        size: number;
        timestamp: number;
      }) => void) => () => void;

      window: {
        minimize: () => void;
        maximize: () => void;
        close: () => void;
      };
    };
  }
}
