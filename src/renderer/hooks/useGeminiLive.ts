import { useState, useRef, useCallback, useEffect } from 'react';
import { AudioPlaybackEngine } from '../audio/AudioPlaybackEngine';
import { SessionManager } from '../session/SessionManager';
import { IdleBehavior, type IdleTier } from '../session/IdleBehavior';

interface UseGeminiLiveOptions {
  onTextResponse?: (text: string) => void;
  onClaudeUsed?: (question: string, answer: string) => void;
  onError?: (error: string) => void;
  onToolStart?: (id: string, name: string) => void;
  onToolEnd?: (id: string, name: string, success: boolean) => void;
  onAgentFinalized?: (config: Record<string, unknown>) => void;
  onPhaseChange?: (phase: 'onboarding' | 'customizing' | 'creating' | 'feature-setup' | 'normal') => void;
}

export interface GeminiLiveState {
  isConnected: boolean;
  isConnecting: boolean;
  isListening: boolean;
  isSpeaking: boolean;
  isWebcamActive: boolean;
  isInCall: boolean;
  transcript: string;
  error: string;
  idleTier: IdleTier;
}

const GEMINI_WS_URL =
  'wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent';

// Claude Opus tool declaration — Gemini can delegate complex multi-step tasks
const ASK_CLAUDE_TOOL = {
  name: 'ask_claude',
  description:
    'Delegate a complex, multi-step task to Claude Opus 4.6, a deeply intelligent AI with full autonomous tool access. Claude can use ALL tools — MCP servers (Desktop Commander for file management, code editing, terminal), browser automation, AND every software connector (PowerShell, VS Code, Git, Office, Adobe, Firecrawl web search, World Monitor intelligence, etc.). Claude will autonomously execute multi-step workflows, making sequential tool calls as needed. Use this for: code refactoring, research tasks that require web searching AND file writing, debugging workflows, file organization, multi-app automation, or any task requiring deep reasoning and multiple sequential steps. Be specific and detailed in your question — Claude works best with rich context.',
  parameters: {
    type: 'object',
    properties: {
      question: {
        type: 'string',
        description:
          'The full task description to delegate to Claude Opus. Be detailed — include context, desired outcome, and any relevant file paths or URLs. Claude will autonomously plan and execute multi-step workflows using all available tools.',
      },
    },
    required: ['question'],
  },
};

// Save memory tool — Gemini can proactively save facts about the user
const SAVE_MEMORY_TOOL = {
  name: 'save_memory',
  description:
    'Save an important fact or preference about the user to long-term memory. Use this when the user tells you something personal, expresses a preference, or shares information you should remember across sessions. Examples: their name, job, favourite tools, communication preferences.',
  parameters: {
    type: 'object',
    properties: {
      fact: {
        type: 'string',
        description: 'The fact to remember (e.g. "User prefers dark mode", "User\'s name is Alex").',
      },
      category: {
        type: 'string',
        description: 'Category: identity, preference, relationship, or professional.',
      },
    },
    required: ['fact', 'category'],
  },
};

// Setup intelligence tool — Gemini calls this after onboarding to create research tasks
const SETUP_INTELLIGENCE_TOOL = {
  name: 'setup_intelligence',
  description:
    'Set up background intelligence research tasks based on the user profile. Call this after getting to know the user during onboarding. Provide an array of research topics tailored to what you learned about them. Each topic has: topic (what to research), schedule (daily_morning, daily_evening, weekly_monday, weekly_friday, hourly, twice_daily), priority (high, medium, low).',
  parameters: {
    type: 'object',
    properties: {
      research_topics: {
        type: 'array',
        description: 'Array of research topic objects.',
        items: {
          type: 'object',
          properties: {
            topic: { type: 'string', description: 'What to research, specific to the user.' },
            schedule: { type: 'string', description: 'How often: daily_morning, daily_evening, weekly_monday, weekly_friday, hourly, twice_daily.' },
            priority: { type: 'string', description: 'high, medium, or low.' },
          },
          required: ['topic', 'schedule', 'priority'],
        },
      },
    },
    required: ['research_topics'],
  },
};

// Self-improvement tools — Gemini can read and propose changes to Friday's own source code
const SELF_IMPROVE_TOOLS = [
  {
    name: 'read_own_source',
    description:
      'Read one of your own source code files. Use this to understand your current implementation before proposing changes. Path is relative to the project root (e.g. "src/main/personality.ts").',
    parameters: {
      type: 'object',
      properties: {
        file_path: {
          type: 'string',
          description: 'Relative path to the file within the Agent Friday project.',
        },
      },
      required: ['file_path'],
    },
  },
  {
    name: 'list_own_files',
    description:
      'List files in a directory of your own source code. Path is relative to project root (e.g. "src/main" or "src/renderer/components").',
    parameters: {
      type: 'object',
      properties: {
        dir_path: {
          type: 'string',
          description: 'Relative directory path within the Agent Friday project.',
        },
      },
      required: ['dir_path'],
    },
  },
  {
    name: 'propose_code_change',
    description:
      'Propose a change to your own source code. The user will see a diff and must approve before the change is applied. Use this to fix bugs in yourself, add new capabilities, or improve your own code. IMPORTANT: Always read the file first with read_own_source, make targeted changes, and explain clearly what you are changing and why.',
    parameters: {
      type: 'object',
      properties: {
        file_path: {
          type: 'string',
          description: 'Relative path to the file to modify (e.g. "src/main/personality.ts").',
        },
        new_content: {
          type: 'string',
          description: 'The complete new content for the file.',
        },
        description: {
          type: 'string',
          description:
            'Clear description of what is being changed and why. This is shown to the user for approval.',
        },
      },
      required: ['file_path', 'new_content', 'description'],
    },
  },
];

// Webcam tools — Gemini can enable/disable the user's camera (tool-gated, permission-first)
const WEBCAM_TOOLS = [
  {
    name: 'enable_webcam',
    description:
      'Turn on the user\'s webcam so you can see what they\'re showing you. ALWAYS ask permission first ("Want me to take a look? I\'ll turn on the camera."). The webcam streams image snapshots at ~1fps so you can describe what you see.',
    parameters: { type: 'object', properties: {}, required: [] },
  },
  {
    name: 'disable_webcam',
    description:
      'Turn off the webcam. ALWAYS call this when done looking at something. Never leave the webcam running while doing other tasks.',
    parameters: { type: 'object', properties: {}, required: [] },
  },
];

// Household voice recognition tool — Gemini can register household members
const HOUSEHOLD_TOOLS = [
  {
    name: 'register_household_member',
    description:
      'Register a household member so you can recognize them by voice in future sessions. Call this when the user introduces someone new or when you detect a different voice and learn who it is. Their info will be stored in long-term memory.',
    parameters: {
      type: 'object',
      properties: {
        name: { type: 'string', description: 'Name of the household member.' },
        relationship: {
          type: 'string',
          description: 'Relationship to the user (wife, husband, partner, child, friend, roommate, etc.).',
        },
        voice_description: {
          type: 'string',
          description:
            'Your description of their voice characteristics for future recognition (pitch, accent, pace, timbre, distinctive features).',
        },
      },
      required: ['name', 'relationship'],
    },
  },
];

// Live call participation tools — Gemini can join/leave meetings via virtual audio
const CALL_TOOLS = [
  {
    name: 'join_meeting',
    description:
      'Join a video call (Google Meet, Zoom, Teams). Opens the meeting link and routes your voice through a virtual microphone so meeting participants can hear you. ALWAYS ask the user for permission first. Requires VB-Cable virtual audio driver installed.',
    parameters: {
      type: 'object',
      properties: {
        meeting_url: {
          type: 'string',
          description: 'The meeting URL or link (Google Meet, Zoom, Teams, etc.).',
        },
      },
      required: ['meeting_url'],
    },
  },
  {
    name: 'leave_meeting',
    description:
      'Leave the current meeting and restore normal audio routing. ALWAYS call this when the meeting ends or when the user asks you to leave.',
    parameters: { type: 'object', properties: {}, required: [] },
  },
];

// Meeting Intelligence tools — Gemini can manage meetings, take notes, and review history
const MEETING_INTEL_TOOLS = [
  {
    name: 'create_meeting',
    description:
      'Create a new meeting in the meeting intelligence system. Use when the user says they have an upcoming meeting, want to prepare for one, or when you detect a meeting from their calendar. This tracks the full meeting lifecycle with notes, attendee intelligence, and post-meeting summaries.',
    parameters: {
      type: 'object',
      properties: {
        name: { type: 'string', description: 'Meeting name or title.' },
        description: { type: 'string', description: 'Brief meeting description or agenda.' },
        attendees: {
          type: 'array',
          items: { type: 'string' },
          description: 'Names or emails of meeting attendees.',
        },
        meeting_url: { type: 'string', description: 'Video call URL if available.' },
        scheduled_start: { type: 'string', description: 'Scheduled start time in ISO format.' },
        scheduled_end: { type: 'string', description: 'Scheduled end time in ISO format.' },
        tags: { type: 'array', items: { type: 'string' }, description: 'Optional tags for categorization.' },
        project_name: { type: 'string', description: 'Related project name if applicable.' },
      },
      required: ['name'],
    },
  },
  {
    name: 'meeting_note',
    description:
      'Add a note, action item, decision, or question to the currently active meeting. Use this during meetings to capture key points, decisions made, action items assigned, or important questions raised. Notes are preserved in meeting history.',
    parameters: {
      type: 'object',
      properties: {
        content: { type: 'string', description: 'The note content.' },
        note_type: {
          type: 'string',
          enum: ['note', 'action-item', 'decision', 'question', 'insight'],
          description: 'Type of note: general note, action-item, decision, question, or insight.',
        },
      },
      required: ['content'],
    },
  },
  {
    name: 'end_current_meeting',
    description:
      'End the currently active meeting. Call this when the user says the meeting is over or when you detect the call has ended. This triggers post-meeting processing: summarization, action item extraction, and memory storage.',
    parameters: { type: 'object', properties: {}, required: [] },
  },
  {
    name: 'get_meeting_history',
    description:
      'Get recent meeting history with summaries and action items. Use when the user asks about past meetings, wants to review what was discussed, or needs to find action items from a previous meeting.',
    parameters: {
      type: 'object',
      properties: {
        search: { type: 'string', description: 'Optional search query to filter meetings.' },
        count: { type: 'number', description: 'Number of meetings to return (default 5).' },
      },
      required: [],
    },
  },
];

// Trust Graph tools — Gemini can update, query, and log interactions with people
const TRUST_GRAPH_TOOLS = [
  {
    name: 'update_trust',
    description:
      'Record a trust-relevant observation about a person in the user\'s world. Use this when the user mentions someone keeping a promise, giving good/bad advice, being reliable/unreliable, or any interaction that affects how much to trust that person\'s input. The trust graph tracks multi-dimensional credibility across domains.',
    parameters: {
      type: 'object',
      properties: {
        person_name: {
          type: 'string',
          description: 'Name of the person this observation is about.',
        },
        evidence_type: {
          type: 'string',
          enum: [
            'promise_kept', 'promise_broken', 'accurate_info', 'inaccurate_info',
            'helpful_action', 'unhelpful_action', 'emotional_support',
            'user_stated', 'observed',
          ],
          description: 'Type of trust evidence being recorded.',
        },
        description: {
          type: 'string',
          description: 'What happened — brief description of the observation.',
        },
        impact: {
          type: 'number',
          description: 'Impact from -1.0 (very negative) to +1.0 (very positive).',
        },
        domain: {
          type: 'string',
          description: 'Optional domain this applies to (e.g. "cooking", "finance", "typescript", "management").',
        },
      },
      required: ['person_name', 'evidence_type', 'description', 'impact'],
    },
  },
  {
    name: 'lookup_person',
    description:
      'Look up everything the agent knows about a person — their trust scores, expertise domains, recent interactions, communication history, and notes. Use this when the user asks about someone, before a meeting with someone, or when you need context about a person mentioned in conversation.',
    parameters: {
      type: 'object',
      properties: {
        person_name: {
          type: 'string',
          description: 'Name of the person to look up.',
        },
      },
      required: ['person_name'],
    },
  },
  {
    name: 'note_interaction',
    description:
      'Log a communication event with a person. Use this when the user mentions they spoke with, emailed, met with, or otherwise interacted with someone. Helps build a picture of communication patterns and relationship dynamics.',
    parameters: {
      type: 'object',
      properties: {
        person_name: {
          type: 'string',
          description: 'Name of the person interacted with.',
        },
        channel: {
          type: 'string',
          enum: ['email', 'slack', 'telegram', 'meeting', 'phone', 'text', 'conversation'],
          description: 'Communication channel used.',
        },
        direction: {
          type: 'string',
          enum: ['inbound', 'outbound', 'bidirectional'],
          description: 'Direction of communication.',
        },
        summary: {
          type: 'string',
          description: 'One-line summary of what was discussed or communicated.',
        },
        sentiment: {
          type: 'number',
          description: 'Sentiment from -1.0 (very negative) to +1.0 (very positive).',
        },
      },
      required: ['person_name', 'channel', 'direction', 'summary', 'sentiment'],
    },
  },
];

// Episodic memory tool — Gemini can search past conversations
const SEARCH_EPISODES_TOOL = {
  name: 'search_episodes',
  description:
    'Search past conversation episodes by keyword or topic. Use this when the user says "remember when we talked about...", references a previous session, or when you want to recall context from an earlier conversation. Returns summaries, topics, emotional tone, and key decisions from matching sessions.',
  parameters: {
    type: 'object',
    properties: {
      query: {
        type: 'string',
        description: 'What to search for in past conversations (topic, keyword, or phrase).',
      },
    },
    required: ['query'],
  },
};

// Background agent tools — Gemini can spawn and check background tasks
const AGENT_TOOLS = [
  {
    name: 'spawn_agent',
    description:
      'Launch a background agent to handle a time-consuming task while you continue chatting. Available agent types: "research" (deep web research on a topic), "summarize" (summarize long text), "code-review" (review code for bugs/security/performance), "draft-email" (draft a professional email). Returns a task ID to check later.',
    parameters: {
      type: 'object',
      properties: {
        agent_type: {
          type: 'string',
          description: 'The type of agent: research, summarize, code-review, or draft-email.',
        },
        description: {
          type: 'string',
          description: 'Brief description of what this task should accomplish.',
        },
        input: {
          type: 'object',
          description:
            'Input for the agent. Research: {topic: "..."}, Summarize: {text: "...", style?: "..."}, Code review: {code: "...", language?: "...", focus?: "..."}, Draft email: {to: "...", subject: "...", key_points: "...", tone?: "..."}.',
          properties: {},
        },
      },
      required: ['agent_type', 'description', 'input'],
    },
  },
  {
    name: 'check_agent',
    description:
      'Check the status and result of a background agent task. Returns status (queued/running/completed/failed/cancelled), progress percentage, logs, and the result if complete.',
    parameters: {
      type: 'object',
      properties: {
        task_id: {
          type: 'string',
          description: 'The task ID returned by spawn_agent.',
        },
      },
      required: ['task_id'],
    },
  },
];

// Document tools — Gemini can read and search ingested documents
const DOCUMENT_TOOLS = [
  {
    name: 'read_document',
    description:
      'Read a document that has been ingested into Friday\'s document library. Use this when the user asks about a specific document by name or ID. Returns the document content, summary, and metadata.',
    parameters: {
      type: 'object',
      properties: {
        query: {
          type: 'string',
          description: 'Document filename, ID, or search query to find the document.',
        },
      },
      required: ['query'],
    },
  },
  {
    name: 'search_documents',
    description:
      'Search through all ingested documents by keyword. Returns matching documents with summaries. Use when the user asks about content across documents or wants to find specific information in his document library.',
    parameters: {
      type: 'object',
      properties: {
        query: {
          type: 'string',
          description: 'What to search for across all ingested documents.',
        },
      },
      required: ['query'],
    },
  },
];

// Project tools — Gemini can watch and query project directories
const PROJECT_TOOLS = [
  {
    name: 'watch_project',
    description:
      'Start watching a project directory. Friday will detect the project type (Node.js, Python, Rust, Go, Java), framework, git status, and key files. Use when the user mentions a project path or asks you to look at a project.',
    parameters: {
      type: 'object',
      properties: {
        root_path: {
          type: 'string',
          description: 'Absolute path to the project root directory.',
        },
      },
      required: ['root_path'],
    },
  },
  {
    name: 'get_project_context',
    description:
      'Get the current context of all watched projects including type, framework, git branch, recent commits, and key files. Use when the user asks about his projects or when you need project context for a technical discussion.',
    parameters: {
      type: 'object',
      properties: {},
    },
  },
];

// Calendar tools — Gemini can read schedule and create events
const CALENDAR_TOOLS = [
  {
    name: 'get_calendar',
    description:
      'Get today\'s upcoming events from Google Calendar. Returns event titles, times, attendees, locations, and video links. Use when the user asks about his schedule, upcoming meetings, or what\'s next.',
    parameters: {
      type: 'object',
      properties: {
        count: {
          type: 'number',
          description: 'Number of upcoming events to return (default: 5).',
        },
      },
    },
  },
  {
    name: 'create_calendar_event',
    description:
      'Create a new Google Calendar event. Use when the user asks to schedule a meeting, block time, or add something to his calendar. Provide summary and times in ISO format.',
    parameters: {
      type: 'object',
      properties: {
        summary: { type: 'string', description: 'Event title/summary.' },
        start_time: { type: 'string', description: 'Start time in ISO format (e.g. "2025-01-15T14:00:00Z").' },
        end_time: { type: 'string', description: 'End time in ISO format.' },
        description: { type: 'string', description: 'Optional event description.' },
        attendees: {
          type: 'array',
          description: 'Optional array of attendee email addresses.',
          items: { type: 'string' },
        },
        location: { type: 'string', description: 'Optional location.' },
      },
      required: ['summary', 'start_time', 'end_time'],
    },
  },
];

// Communications tool — Gemini can draft emails and messages
const COMMUNICATIONS_TOOL = {
  name: 'draft_communication',
  description:
    'Draft an email, message, reply, or follow-up in the user\'s voice. The draft is automatically copied to clipboard. Use when the user asks you to write, compose, or draft any communication. After drafting, offer to refine or open in email client.',
  parameters: {
    type: 'object',
    properties: {
      type: {
        type: 'string',
        description: 'Communication type: "email", "message", "reply", or "follow-up".',
      },
      to: { type: 'string', description: 'Recipient name or email address.' },
      context: { type: 'string', description: 'What the user wants to communicate — the core intent and key points.' },
      subject: { type: 'string', description: 'Optional subject line (auto-generated if not provided).' },
      tone: {
        type: 'string',
        description: 'Tone: "formal", "casual", "friendly", "professional" (default), or "urgent".',
      },
      original_message: { type: 'string', description: 'For replies — the original message being replied to.' },
      max_length: {
        type: 'string',
        description: 'Length: "short" (2-4 sentences), "medium" (1-2 paragraphs, default), or "long" (3-4 paragraphs).',
      },
    },
    required: ['type', 'to', 'context'],
  },
};

// Scheduler tool declarations — Gemini can create/list/delete tasks
const SCHEDULER_TOOLS = [
  {
    name: 'create_task',
    description:
      'Create a scheduled task or reminder. For one-time tasks, set trigger_time as Unix timestamp in milliseconds (calculate from current time using Date.now()). For recurring tasks, set cron_pattern (minute hour dayOfMonth month dayOfWeek). Examples: "remind me in 30 minutes" → once with trigger_time = Date.now() + 30*60*1000, "every weekday at 9am" → recurring with cron "0 9 * * 1-5".',
    parameters: {
      type: 'object',
      properties: {
        description: { type: 'string', description: 'What to remind or do.' },
        type: { type: 'string', description: '"once" for one-time, "recurring" for repeating tasks.' },
        trigger_time: { type: 'number', description: 'Unix timestamp in ms for one-time tasks.' },
        cron_pattern: { type: 'string', description: 'Cron pattern for recurring: "min hour dom mon dow".' },
        action: { type: 'string', description: '"remind", "launch_app", or "run_command".' },
        payload: { type: 'string', description: 'Reminder text, app name, or command.' },
      },
      required: ['description', 'type', 'action', 'payload'],
    },
  },
  {
    name: 'list_tasks',
    description: 'List all scheduled tasks and reminders.',
    parameters: { type: 'object', properties: {} },
  },
  {
    name: 'delete_task',
    description: 'Delete a scheduled task by its ID.',
    parameters: {
      type: 'object',
      properties: {
        task_id: { type: 'string', description: 'The ID of the task to delete.' },
      },
      required: ['task_id'],
    },
  },
];

// Sanitise tool schemas for Gemini compatibility
function sanitizeSchema(schema: unknown): Record<string, unknown> {
  if (!schema || typeof schema !== 'object') {
    return { type: 'object', properties: {} };
  }

  const s = schema as Record<string, unknown>;
  const clean: Record<string, unknown> = {};

  if (s.type) clean.type = s.type;
  if (s.description) clean.description = s.description;
  if (Array.isArray(s.required)) clean.required = s.required;
  if (Array.isArray(s.enum)) clean.enum = s.enum;

  if (s.properties && typeof s.properties === 'object') {
    const props: Record<string, unknown> = {};
    for (const [key, val] of Object.entries(s.properties as Record<string, unknown>)) {
      props[key] = sanitizeSchema(val);
    }
    clean.properties = props;
  }

  if (s.items && typeof s.items === 'object') {
    clean.items = sanitizeSchema(s.items);
  }

  if (!clean.type) clean.type = 'object';
  // Gemini requires array types to have items with a type field
  if (clean.type === 'array' && !clean.items) {
    clean.items = { type: 'string' };
  }
  if (clean.type === 'object' && !clean.properties) clean.properties = {};

  return clean;
}

export function useGeminiLive(options: UseGeminiLiveOptions = {}) {
  const [state, setState] = useState<GeminiLiveState>({
    isConnected: false,
    isConnecting: false,
    isListening: false,
    isSpeaking: false,
    isWebcamActive: false,
    isInCall: false,
    transcript: '',
    error: '',
    idleTier: 0,
  });

  const wsRef = useRef<WebSocket | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const workletNodeRef = useRef<AudioWorkletNode | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const playbackEngineRef = useRef<AudioPlaybackEngine | null>(null);
  const sessionManagerRef = useRef<SessionManager | null>(null);
  const screenFrameCleanupRef = useRef<(() => void) | null>(null);
  const micAnalyserRef = useRef<AnalyserNode | null>(null);
  const micAnalyserDataRef = useRef<Uint8Array | null>(null);
  const intentionalDisconnectRef = useRef(false);
  const wsReconnectAttemptsRef = useRef(0);
  const startListeningRef = useRef<(() => Promise<void>) | null>(null);
  const idleBehaviorRef = useRef<IdleBehavior | null>(null);
  const keepaliveRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const lastServerMessageRef = useRef<number>(Date.now());
  const responseWatchdogRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const ambientContextCacheRef = useRef<string>('');
  const stateRef = useRef(state);
  stateRef.current = state;
  const optionsRef = useRef(options);
  const apiPortRef = useRef<number | null>(null);
  const toolsRef = useRef<Array<{ name: string; description?: string; parameters?: unknown; inputSchema?: unknown }>>([]);
  const voiceNameRef = useRef<string>('Kore');
  const agentAccentRef = useRef<string>('');
  const agentNameRef = useRef<string>('');
  const mcpToolNamesRef = useRef<Set<string>>(new Set());
  const smReconnectingRef = useRef(false);
  const isAutoReconnectingRef = useRef(false);
  const setupCompleteRef = useRef(false);
  const reconnectStabilizingRef = useRef(false);
  const webcamStreamRef = useRef<MediaStream | null>(null);
  const webcamIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const webcamVideoRef = useRef<HTMLVideoElement | null>(null);
  const webcamCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const sendTextRef = useRef<((text: string) => void) | null>(null);
  optionsRef.current = options;

  // Initialize playback engine once
  if (!playbackEngineRef.current) {
    playbackEngineRef.current = new AudioPlaybackEngine();
    playbackEngineRef.current.setSpeakingCallback((speaking) => {
      setState((s) => ({ ...s, isSpeaking: speaking }));
    });
  }

  // Initialize session manager once
  if (!sessionManagerRef.current) {
    sessionManagerRef.current = new SessionManager();
    // Set agent identity for dynamic accent in conversation summaries
    window.eve.onboarding.getAgentConfig().then((config: Record<string, unknown>) => {
      if (config?.agentName && sessionManagerRef.current) {
        sessionManagerRef.current.setAgentIdentity(
          config.agentName as string,
          (config.agentAccent as string) || ''
        );
      }
      // Cache accent/name locally so reconnect never needs async IPC
      agentAccentRef.current = (config?.agentAccent as string) || '';
      agentNameRef.current = (config?.agentName as string) || '';
    }).catch(() => {});
  }

  // Initialize idle behavior once
  if (!idleBehaviorRef.current) {
    idleBehaviorRef.current = new IdleBehavior();
  }

  // --- Get the API base URL for Claude routing ---
  const getApiBase = useCallback(async () => {
    if (!apiPortRef.current) {
      try {
        apiPortRef.current = await window.eve.getApiPort();
      } catch {
        apiPortRef.current = 3333;
      }
    }
    return `http://localhost:${apiPortRef.current}`;
  }, []);

  // --- Connect to Gemini Live WebSocket ---
  const connect = useCallback(
    async (
      systemInstruction: string,
      externalTools?: Array<{ name: string; description?: string; parameters?: unknown; inputSchema?: unknown }>,
      voiceName?: string
    ): Promise<void> => {
      // Store voice name for reconnects
      if (voiceName) voiceNameRef.current = voiceName;
      const apiKey = await window.eve.getGeminiApiKey();
      if (!apiKey) {
        const msg = 'No Gemini API key configured — add GEMINI_API_KEY to .env';
        setState((s) => ({ ...s, error: msg }));
        optionsRef.current.onError?.(msg);
        return;
      }

      // Close old socket with intentional flag to suppress stale onclose reconnect
      if (wsRef.current) {
        intentionalDisconnectRef.current = true;
        wsRef.current.close();
        wsRef.current = null;
      }

      // Store tools for reconnect
      if (externalTools) {
        toolsRef.current = externalTools;
      }

      intentionalDisconnectRef.current = false;
      setState((s) => ({ ...s, isConnecting: true, error: '' }));

      // Fetch browser tool declarations from main process (Gemini-compatible format)
      let browserToolDecls: Array<{ name: string; description: string; parameters: Record<string, unknown> }> = [];
      try {
        browserToolDecls = await window.eve.browser.listTools();
      } catch (err) {
        console.warn('[GeminiLive] Failed to load browser tools:', err);
      }

      // Fetch SOC (Self-Operating Computer) + Browser-Use tool declarations
      let socToolDecls: Array<{ name: string; description: string; parameters: Record<string, unknown> }> = [];
      try {
        socToolDecls = await window.eve.soc.listTools();
        if (socToolDecls.length > 0) {
          console.log(`[GeminiLive] Loaded ${socToolDecls.length} SOC/browser-use tools`);
        }
      } catch (err) {
        console.warn('[GeminiLive] SOC tools unavailable:', err);
      }

      // Fetch GitLoader tool declarations
      let gitToolDecls: Array<{ name: string; description: string; parameters: Record<string, unknown> }> = [];
      try {
        gitToolDecls = await window.eve.gitLoader.listTools();
        if (gitToolDecls.length > 0) {
          console.log(`[GeminiLive] Loaded ${gitToolDecls.length} GitLoader tools`);
        }
      } catch (err) {
        console.warn('[GeminiLive] GitLoader tools unavailable:', err);
      }

      // Build tool declarations: core + browser + connector + MCP tools
      // Load connector tools dynamically (only installed software)
      let connectorToolDecls: Array<{ name: string; description: string; parameters: unknown }> = [];
      try {
        const connectorTools = await window.eve.connectors.listTools();
        connectorToolDecls = connectorTools.map((t) => ({
          name: t.name,
          description: (t.description || '').slice(0, 512),
          parameters: sanitizeSchema(t.parameters || { type: 'object', properties: {} }),
        }));
        if (connectorToolDecls.length > 0) {
          console.log(`[GeminiLive] Loaded ${connectorToolDecls.length} connector tools`);
        }
      } catch (err) {
        console.warn('[GeminiLive] Connector tools unavailable:', err);
      }

      // Load MCP tools (Desktop Commander, user-added MCP servers, etc.)
      let mcpToolDecls: Array<{ name: string; description: string; parameters: unknown }> = [];
      const mcpToolNamesSet = new Set<string>();
      try {
        const mcpTools = await window.eve.mcp.listTools();
        mcpToolDecls = mcpTools
          .filter((t: any) => {
            // Skip MCP tools that conflict with connector tools (connectors take priority)
            const connectorNames = new Set(connectorToolDecls.map((c) => c.name));
            return !connectorNames.has(t.name);
          })
          .map((t: any) => {
            mcpToolNamesSet.add(t.name);
            return {
              name: t.name,
              description: (t.description || '').slice(0, 512),
              parameters: sanitizeSchema(t.inputSchema || t.parameters || { type: 'object', properties: {} }),
            };
          });
        if (mcpToolDecls.length > 0) {
          console.log(`[GeminiLive] Loaded ${mcpToolDecls.length} MCP tools`);
        }
      } catch (err) {
        console.warn('[GeminiLive] MCP tools unavailable:', err);
      }
      // Store MCP tool names in a ref so the execution handler can check them
      mcpToolNamesRef.current = mcpToolNamesSet;

      const functionDeclarations = [
        ASK_CLAUDE_TOOL,
        SAVE_MEMORY_TOOL,
        SETUP_INTELLIGENCE_TOOL,
        SEARCH_EPISODES_TOOL,
        ...AGENT_TOOLS,
        ...DOCUMENT_TOOLS,
        ...PROJECT_TOOLS,
        ...SELF_IMPROVE_TOOLS,
        ...WEBCAM_TOOLS,
        ...HOUSEHOLD_TOOLS,
        ...TRUST_GRAPH_TOOLS,
        ...CALL_TOOLS,
        ...MEETING_INTEL_TOOLS,
        ...SCHEDULER_TOOLS,
        ...CALENDAR_TOOLS,
        COMMUNICATIONS_TOOL,
        ...browserToolDecls,
        ...socToolDecls,
        ...gitToolDecls,
        ...connectorToolDecls,
        ...mcpToolDecls,
        ...(externalTools || toolsRef.current).map((t) => ({
          name: t.name,
          description: (t.description || '').slice(0, 512),
          parameters: sanitizeSchema(t.parameters || t.inputSchema),
        })),
      ];

      console.log(`[GeminiLive] Connecting with ${functionDeclarations.length} tools...`);

      return new Promise<void>((resolve, reject) => {
        // Guard: prevent mic from streaming to this WS until Gemini confirms setup
        setupCompleteRef.current = false;

        const ws = new WebSocket(`${GEMINI_WS_URL}?key=${apiKey}`);
        wsRef.current = ws;

        const timeout = setTimeout(() => {
          if (!ws || ws.readyState === WebSocket.CLOSED) return;
          const msg = 'Connection timed out — Gemini Live did not respond';
          console.error('[GeminiLive]', msg);
          setState((s) => ({ ...s, isConnecting: false, error: msg }));
          optionsRef.current.onError?.(msg);
          ws.close();
          reject(new Error(msg));
        }, 15000);

        ws.onopen = () => {
          console.log('[GeminiLive] WebSocket opened, sending setup...');

          const setup = {
            setup: {
              model: 'models/gemini-2.5-flash-native-audio-preview-12-2025',
              generation_config: {
                response_modalities: ['AUDIO'],
                speech_config: {
                  voice_config: {
                    prebuilt_voice_config: {
                      voice_name: voiceNameRef.current,
                    },
                  },
                },
              },
              // Tune VAD for responsive interruptions while avoiding false barge-in
              // HIGH start sensitivity = reliably detects when the user starts speaking (enables interruption)
              // LOW end sensitivity = waits longer before deciding user stopped (prevents choppy cut-off)
              // Echo cancellation on the mic (getUserMedia) handles self-interruption prevention
              realtime_input_config: {
                automatic_activity_detection: {
                  start_of_speech_sensitivity: 'START_SENSITIVITY_HIGH',
                  end_of_speech_sensitivity: 'END_SENSITIVITY_LOW',
                  prefix_padding_ms: 100,
                  silence_duration_ms: 300,
                },
              },
              system_instruction: {
                parts: [{ text: systemInstruction }],
              },
              tools: [{ function_declarations: functionDeclarations }],
            },
          };

          ws.send(JSON.stringify(setup));
        };

        ws.onmessage = async (event) => {
          try {
            const raw =
              typeof event.data === 'string' ? event.data : await (event.data as Blob).text();
            const data = JSON.parse(raw);

            // Catch Gemini error responses (invalid model, auth failure, etc.)
            if (data.error) {
              clearTimeout(timeout);
              const errMsg = data.error.message || data.error.status || JSON.stringify(data.error);
              console.error('[GeminiLive] Server error:', errMsg);
              setState((s) => ({ ...s, isConnecting: false, error: `Gemini error: ${errMsg}` }));
              optionsRef.current.onError?.(`Gemini error: ${errMsg}`);
              ws.close();
              reject(new Error(errMsg));
              return;
            }

            if (data.setupComplete) {
              clearTimeout(timeout);
              // CRITICAL: Allow mic/screen/webcam frames to flow to this WebSocket now
              setupCompleteRef.current = true;
              console.log('[GeminiLive] Setup complete — ready (mic gate opened)');
              setState((s) => ({ ...s, isConnected: true, isConnecting: false, error: '', idleTier: 0 }));

              // Notify session manager + session health
              // (skip sessionStarted if SM is managing this reconnect — it calls sessionStarted itself)
              if (!smReconnectingRef.current) {
                sessionManagerRef.current?.sessionStarted();
              }
              try { window.eve.sessionHealth.sessionStarted(); } catch { /* ignored */ }

              // Start WebSocket keepalive (every 8s) — send tiny silent PCM frame
              // to keep the Gemini session alive. Dead sockets caught via send() failure.
              if (keepaliveRef.current) clearInterval(keepaliveRef.current);
              lastServerMessageRef.current = Date.now();

              // Pre-encode 160 samples of silence (10ms at 16kHz) as base64
              const silentPcm = new ArrayBuffer(320); // 160 * 2 bytes (16-bit PCM)
              const silentB64 = btoa(String.fromCharCode(...new Uint8Array(silentPcm)));

              keepaliveRef.current = setInterval(() => {
                if (ws.readyState === WebSocket.OPEN) {
                  try {
                    // Send a tiny silent audio frame — Gemini counts this as real activity
                    ws.send(JSON.stringify({
                      realtime_input: {
                        media_chunks: [{ data: silentB64, mime_type: 'audio/pcm;rate=16000' }],
                      },
                    }));
                  } catch {
                    // Send failure = dead connection → trigger seamless reconnect
                    console.warn('[GeminiLive] Keepalive send failed — dead connection, triggering reconnect');
                    if (keepaliveRef.current) clearInterval(keepaliveRef.current);
                    keepaliveRef.current = null;
                    const sm = sessionManagerRef.current;
                    if (sm) {
                      intentionalDisconnectRef.current = true;
                      sm.requestReconnect();
                    }
                  }
                  // NO response watchdog — removed to prevent false-positive reconnect
                  // cascades during idle. SessionManager handles timed reconnects at ~5.5 min.
                  // Dead sockets are caught by the send() try/catch above.
                } else {
                  console.warn('[GeminiLive] Keepalive detected dead socket — readyState:', ws.readyState);
                  if (keepaliveRef.current) clearInterval(keepaliveRef.current);
                  keepaliveRef.current = null;
                }
              }, 8_000);

              // Auto-start screen capture if enabled in settings (skip if already running from previous session)
              try {
                const settings = await window.eve.settings.get();
                if (settings.autoScreenCapture && window.eve.screenCapture && !screenFrameCleanupRef.current) {
                  await window.eve.screenCapture.start();
                  const cleanup = window.eve.screenCapture.onFrame((frame: string) => {
                    if (wsRef.current?.readyState === WebSocket.OPEN) {
                      wsRef.current.send(
                        JSON.stringify({
                          realtime_input: {
                            media_chunks: [{ data: frame, mime_type: 'image/jpeg' }],
                          },
                        })
                      );
                    }
                  });
                  screenFrameCleanupRef.current = cleanup;
                  console.log('[GeminiLive] Auto-started screen capture');
                }
              } catch (err) {
                console.warn('[GeminiLive] Auto screen capture failed:', err);
              }

              resolve();
              return;
            }

            // Server content — audio and text responses
            if (data.serverContent) {
              lastServerMessageRef.current = Date.now();
              const parts = data.serverContent.modelTurn?.parts || [];
              for (const part of parts) {
                if (part.text) {
                  setState((s) => ({ ...s, transcript: s.transcript + part.text }));
                  optionsRef.current.onTextResponse?.(part.text);

                  // Track in session manager for context rollover
                  sessionManagerRef.current?.addEntry('assistant', part.text);
                }
                if (part.inlineData?.mimeType?.startsWith('audio/')) {
                  const pcm = base64ToFloat32(part.inlineData.data);
                  playbackEngineRef.current?.enqueue(pcm);
                }
              }

              if (data.serverContent.turnComplete) {
                setState((s) => ({ ...s, transcript: '' }));
              }

              // Handle interruption — user spoke while Friday was talking
              // The server stops generating, but we must flush the audio buffer
              // so Friday actually stops speaking immediately
              if (data.serverContent.interrupted) {
                console.log('[GeminiLive] Server signalled interruption — flushing audio buffer');
                playbackEngineRef.current?.flush();
              }
            }

            // Handle goAway — server is about to terminate the session
            // Trigger graceful reconnect with conversation context before disconnect
            if (data.goAway) {
              console.warn('[GeminiLive] Received goAway — server will disconnect soon, triggering graceful reconnect');
              // Mark intentional IMMEDIATELY so the onclose handler (which may fire before
              // our timeout) doesn't trigger a competing auto-reconnect
              intentionalDisconnectRef.current = true;
              // Use SessionManager's reconnect (includes conversation summary) instead of
              // waiting for the raw WebSocket close which loses context
              const sm = sessionManagerRef.current;
              if (sm) {
                // Small delay to let the server finish any in-flight messages
                setTimeout(() => sm.requestReconnect(), 500);
              }
            }

            // Tool calls — route to Claude, desktop, self-improve tools (parallel execution)
            if (data.toolCall) {
              lastServerMessageRef.current = Date.now();
              const calls = data.toolCall.functionCalls || [];

              // Execute all tool calls in parallel with timing and error recovery
              const responsePromises = calls.map(async (fc: { id: string; name: string; args?: Record<string, unknown> }) => {
                const actionId = `${fc.id}-${Date.now()}`;
                const toolStartTime = Date.now();
                optionsRef.current.onToolStart?.(actionId, fc.name);
                let success = true;

                try {
                  let resultText: string;

                  if (fc.name === 'ask_claude') {
                    const question = fc.args?.question || '';
                    console.log('[GeminiLive] Routing to Claude:', String(question).slice(0, 100));

                    const base = await getApiBase();
                    const res = await fetch(`${base}/api/chat`, {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({ message: question, history: [] }),
                    });

                    const chatData = await res.json();
                    resultText = chatData.response || 'No response from Claude.';
                    optionsRef.current.onClaudeUsed?.(String(question), resultText);
                  } else if (fc.name === 'save_memory') {
                    const fact = String(fc.args?.fact || '');
                    const category = String(fc.args?.category || 'identity');
                    await window.eve.memory.addImmediate(fact, category);
                    resultText = `Saved to memory: "${fact}"`;
                    console.log('[GeminiLive] Memory saved:', fact);
                  } else if (fc.name === 'setup_intelligence') {
                    const topics = (fc.args?.research_topics || []) as Array<{
                      topic: string;
                      schedule: string;
                      priority: string;
                    }>;
                    resultText = await window.eve.intelligence.setup(topics);
                    console.log('[GeminiLive] Intelligence setup:', resultText);
                  } else if (fc.name === 'create_task') {
                    const task = await window.eve.scheduler.createTask(fc.args || {});
                    resultText = `Task created: "${task.description}" (ID: ${(task as any).id}, type: ${(task as any).type})`;
                    console.log('[GeminiLive] Task created:', (task as any).id);
                  } else if (fc.name === 'list_tasks') {
                    const tasks = await window.eve.scheduler.listTasks();
                    if (tasks.length === 0) {
                      resultText = 'No scheduled tasks.';
                    } else {
                      resultText = tasks
                        .map(
                          (t: any) =>
                            `[${t.id}] ${t.description} (${t.type}, action: ${t.action}${t.cronPattern ? `, cron: ${t.cronPattern}` : ''}${t.triggerTime ? `, at: ${new Date(t.triggerTime).toLocaleString()}` : ''})`
                        )
                        .join('\n');
                    }
                  } else if (fc.name === 'delete_task') {
                    const deleted = await window.eve.scheduler.deleteTask(
                      String(fc.args?.task_id || '')
                    );
                    resultText = deleted ? 'Task deleted.' : 'Task not found.';
                  } else if (fc.name === 'read_own_source') {
                    const filePath = String(fc.args?.file_path || '');
                    console.log('[GeminiLive] Self-improve: reading', filePath);
                    resultText = await window.eve.selfImprove.readFile(filePath);
                  } else if (fc.name === 'list_own_files') {
                    const dirPath = String(fc.args?.dir_path || '.');
                    console.log('[GeminiLive] Self-improve: listing', dirPath);
                    const files = await window.eve.selfImprove.listFiles(dirPath);
                    resultText = (files as string[]).join('\n');
                  } else if (fc.name === 'propose_code_change') {
                    const filePath = String(fc.args?.file_path || '');
                    const newContent = String(fc.args?.new_content || '');
                    const description = String(fc.args?.description || '');
                    console.log('[GeminiLive] Self-improve: proposing change to', filePath);
                    const result = await window.eve.selfImprove.proposeChange(filePath, newContent, description);
                    resultText = result.message || (result.approved ? 'Change approved and applied.' : 'Change was denied by user.');
                  } else if (fc.name === 'spawn_agent') {
                    const agentType = String(fc.args?.agent_type || '');
                    const description = String(fc.args?.description || '');
                    const input = (fc.args?.input || {}) as Record<string, unknown>;
                    console.log('[GeminiLive] Spawning agent:', agentType, description);
                    const task = await window.eve.agents.spawn(agentType, description, input);
                    resultText = `Agent spawned: "${description}" (type: ${agentType}, ID: ${task.id.slice(0, 8)}, status: ${task.status}). I'll work on this in the background.`;
                  } else if (fc.name === 'check_agent') {
                    const taskId = String(fc.args?.task_id || '');
                    console.log('[GeminiLive] Checking agent:', taskId);
                    const task = await window.eve.agents.get(taskId);
                    if (!task) {
                      resultText = 'Task not found — it may have been cleaned up.';
                    } else {
                      const parts = [`Status: ${task.status}`, `Progress: ${task.progress}%`];
                      if (task.logs.length > 0) {
                        parts.push(`Latest log: ${task.logs[task.logs.length - 1]}`);
                      }
                      if (task.result) {
                        parts.push(`\nResult:\n${task.result}`);
                      }
                      if (task.error) {
                        parts.push(`Error: ${task.error}`);
                      }
                      if (task.completedAt && task.startedAt) {
                        const secs = Math.round((task.completedAt - task.startedAt) / 1000);
                        parts.push(`Duration: ${secs}s`);
                      }
                      resultText = parts.join('\n');
                    }
                  } else if (fc.name === 'read_document') {
                    const query = String(fc.args?.query || '');
                    console.log('[GeminiLive] Reading document:', query);

                    // Try to find by ID first, then search by name
                    let doc = await window.eve.documents.get(query);
                    if (!doc) {
                      const results = await window.eve.documents.search(query);
                      doc = results[0] || undefined;
                    }

                    if (!doc) {
                      resultText = `No document found matching "${query}". The user may need to ingest the document first (File > Ingest Document).`;
                    } else {
                      const preview = doc.content.length > 3000
                        ? doc.content.slice(0, 3000) + '\n\n[... content truncated — full document is ' + Math.round(doc.content.length / 1024) + 'KB]'
                        : doc.content;
                      resultText = `**${doc.filename}** (${doc.mimeType}, ${Math.round(doc.size / 1024)}KB)\n\nSummary: ${doc.summary}\n\nContent:\n${preview}`;
                    }
                  } else if (fc.name === 'search_documents') {
                    const query = String(fc.args?.query || '');
                    console.log('[GeminiLive] Searching documents:', query);

                    const docs = await window.eve.documents.search(query);
                    if (docs.length === 0) {
                      resultText = 'No matching documents found. The user may need to ingest documents first.';
                    } else {
                      resultText = docs
                        .map((d: any) =>
                          `- **${d.filename}** (${Math.round(d.size / 1024)}KB, ${d.mimeType}): ${d.summary}`
                        )
                        .join('\n');
                    }
                  } else if (fc.name === 'watch_project') {
                    const rootPath = String(fc.args?.root_path || '');
                    console.log('[GeminiLive] Watching project:', rootPath);

                    const profile = await window.eve.project.watch(rootPath);
                    const parts = [
                      `Project: ${profile.name} (${profile.type}${profile.framework ? '/' + profile.framework : ''})`,
                    ];
                    if (profile.description) parts.push(`Description: ${profile.description}`);
                    if (profile.gitBranch) parts.push(`Branch: ${profile.gitBranch} (${profile.gitStatus || 'unknown'})`);
                    if (profile.keyFiles.length > 0) parts.push(`Key files: ${profile.keyFiles.join(', ')}`);
                    if (profile.recentChanges.length > 0) parts.push(`Recent commits:\n${profile.recentChanges.map((c: string) => `  - ${c}`).join('\n')}`);
                    if (profile.structure.length > 0) parts.push(`Structure:\n${profile.structure.slice(0, 15).join('\n')}`);
                    resultText = parts.join('\n');
                  } else if (fc.name === 'get_project_context') {
                    console.log('[GeminiLive] Getting project context');

                    const projects = await window.eve.project.list();
                    if (projects.length === 0) {
                      resultText = 'No projects being watched. Ask the user for a project path to watch.';
                    } else {
                      resultText = projects
                        .map((p: any) => {
                          const parts = [`**${p.name}** (${p.type}${p.framework ? '/' + p.framework : ''})`];
                          if (p.gitBranch) parts.push(`Branch: ${p.gitBranch} (${p.gitStatus || 'unknown'})`);
                          if (p.keyFiles.length > 0) parts.push(`Key files: ${p.keyFiles.join(', ')}`);
                          if (p.recentChanges.length > 0) parts.push(`Recent: ${p.recentChanges[0]}`);
                          return parts.join(' | ');
                        })
                        .join('\n');
                    }
                  } else if (fc.name === 'get_calendar') {
                    const count = Number(fc.args?.count || 5);
                    console.log('[GeminiLive] Getting calendar events');

                    const isAuthed = await window.eve.calendar.isAuthenticated();
                    if (!isAuthed) {
                      resultText = 'Google Calendar is not connected. The user needs to authenticate in Settings first.';
                    } else {
                      const events = await window.eve.calendar.getUpcoming(count);
                      if (events.length === 0) {
                        resultText = 'No upcoming events today.';
                      } else {
                        resultText = events
                          .map((e: any) => {
                            const start = new Date(e.start);
                            const timeStr = start.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
                            const minsUntil = Math.round((start.getTime() - Date.now()) / 60000);
                            let line = `- ${timeStr} (in ${minsUntil}m): ${e.summary}`;
                            if (e.attendees.length > 0) line += ` [${e.attendees.length} attendees: ${e.attendees.slice(0, 3).join(', ')}${e.attendees.length > 3 ? '...' : ''}]`;
                            if (e.hangoutLink) line += ' [has video link]';
                            if (e.location) line += ` @ ${e.location}`;
                            return line;
                          })
                          .join('\n');
                      }
                    }
                  } else if (fc.name === 'create_calendar_event') {
                    const summary = String(fc.args?.summary || '');
                    const startTime = String(fc.args?.start_time || '');
                    const endTime = String(fc.args?.end_time || '');
                    const description = fc.args?.description ? String(fc.args.description) : undefined;
                    const attendees = Array.isArray(fc.args?.attendees) ? fc.args.attendees as string[] : undefined;
                    const location = fc.args?.location ? String(fc.args.location) : undefined;
                    console.log('[GeminiLive] Creating calendar event:', summary);

                    const isAuthed = await window.eve.calendar.isAuthenticated();
                    if (!isAuthed) {
                      resultText = 'Google Calendar is not connected. The user needs to authenticate in Settings first.';
                    } else {
                      const event = await window.eve.calendar.createEvent({
                        summary,
                        startTime,
                        endTime,
                        description,
                        attendees,
                        location,
                      });
                      if (event) {
                        const startStr = new Date(event.start).toLocaleString('en-GB', {
                          weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
                        });
                        resultText = `Event created: "${event.summary}" on ${startStr}${event.attendees.length > 0 ? ` with ${event.attendees.length} attendees` : ''}${event.location ? ` @ ${event.location}` : ''}`;
                      } else {
                        resultText = 'Failed to create calendar event. Check the Google Calendar connection.';
                      }
                    }
                  } else if (fc.name === 'draft_communication') {
                    const type = String(fc.args?.type || 'email') as 'email' | 'message' | 'reply' | 'follow-up';
                    const to = String(fc.args?.to || '');
                    const context = String(fc.args?.context || '');
                    const subject = fc.args?.subject ? String(fc.args.subject) : undefined;
                    const tone = (fc.args?.tone || 'professional') as 'formal' | 'casual' | 'friendly' | 'professional' | 'urgent';
                    const originalMessage = fc.args?.original_message ? String(fc.args.original_message) : undefined;
                    const maxLength = (fc.args?.max_length || 'medium') as 'short' | 'medium' | 'long';
                    console.log('[GeminiLive] Drafting communication:', type, 'to', to);

                    const draft = await window.eve.communications.draft({
                      type,
                      to,
                      context,
                      subject,
                      tone,
                      originalMessage,
                      maxLength,
                    });

                    // Auto-copy to clipboard
                    await window.eve.communications.copy(draft.id);

                    resultText = `Draft ${type} created and copied to clipboard.\n\n${draft.subject ? `Subject: ${draft.subject}\n\n` : ''}${draft.body}\n\n---\n(Draft ID: ${draft.id} — I can refine this or open it in your email client.)`;
                  } else if (fc.name === 'finalize_agent_identity') {
                    // Onboarding complete — save agent config and notify renderer
                    const agentConfig = {
                      agentName: String(fc.args?.agent_name || ''),
                      agentVoice: String(fc.args?.voice_name || 'Kore'),
                      agentGender: String(fc.args?.gender || 'female'),
                      agentAccent: String(fc.args?.accent || ''),
                      agentBackstory: String(fc.args?.backstory || ''),
                      agentTraits: Array.isArray(fc.args?.personality_traits) ? (fc.args!.personality_traits as string[]) : [],
                      agentIdentityLine: String(fc.args?.identity_line || ''),
                      userName: String(fc.args?.user_name || ''),
                      onboardingComplete: true,
                    };
                    console.log('[GeminiLive] Finalizing agent identity:', agentConfig.agentName);
                    // Save via IPC to main process
                    await window.eve.onboarding.finalizeAgent(agentConfig);
                    // Notify App.tsx to show creation animation + reconnect
                    optionsRef.current.onAgentFinalized?.(agentConfig);
                    resultText = `Agent identity saved. ${agentConfig.agentName} is being created now. Goodbye — and welcome, ${agentConfig.agentName}.`;
                  } else if (fc.name === 'play_voice_sample') {
                    // Voice audition — generate a voice sample via REST API and play it
                    const voiceName = String(fc.args?.voice_name || 'Kore');
                    console.log(`[GeminiLive] Playing voice sample: ${voiceName}`);
                    try {
                      const sample = await window.eve.voiceAudition.generateSample(voiceName);
                      if (sample && sample.audio) {
                        // Decode base64 audio and play through a temporary Audio element
                        // (not the main playback engine, since that expects raw PCM chunks)
                        const audioBytes = Uint8Array.from(atob(sample.audio), (c) => c.charCodeAt(0));
                        const blob = new Blob([audioBytes], { type: sample.mimeType });
                        const url = URL.createObjectURL(blob);
                        const audio = new Audio(url);
                        // Wait for playback to finish so Gemini can time its next message
                        await new Promise<void>((resolveAudio) => {
                          audio.onended = () => {
                            URL.revokeObjectURL(url);
                            resolveAudio();
                          };
                          audio.onerror = () => {
                            URL.revokeObjectURL(url);
                            resolveAudio();
                          };
                          audio.play().catch(() => resolveAudio());
                        });
                        resultText = `Voice sample for "${voiceName}" played successfully. Ask the user what they think.`;
                      } else {
                        resultText = `Could not generate a voice sample for "${voiceName}". Describe the voice instead and move on.`;
                      }
                    } catch (err) {
                      const msg = err instanceof Error ? err.message : String(err);
                      console.warn('[GeminiLive] Voice sample error:', msg);
                      resultText = `Voice sample generation failed: ${msg}. Describe the voice instead.`;
                    }
                  } else if (fc.name === 'acknowledge_introduction') {
                    // Trust introduction complete — user is ready to proceed to intake
                    const userResponse = String(fc.args?.user_response || '');
                    const questions = fc.args?.questions_asked as string[] || [];
                    console.log('[GeminiLive] Trust introduction acknowledged:', userResponse);
                    if (questions.length > 0) {
                      console.log('[GeminiLive] User asked questions:', questions.join(', '));
                    }
                    resultText = 'Trust introduction acknowledged. The user understands the system and is ready for setup. Now transition to the intake phase — ask the three "Her" questions one at a time.';
                  } else if (fc.name === 'save_intake_responses') {
                    // "Her" intake — save the three raw responses and generate psych profile
                    const responses = {
                      voicePreference: String(fc.args?.voice_preference || ''),
                      socialDescription: String(fc.args?.social_description || ''),
                      motherRelationship: String(fc.args?.mother_relationship || ''),
                    };
                    console.log('[GeminiLive] Saving intake responses');
                    await window.eve.psychProfile.generate(responses);
                    resultText = 'Intake responses saved and psychological profile generated. You may now transition to agent customization.';
                  } else if (fc.name === 'transition_to_customization') {
                    // Signal renderer to enter customization phase
                    console.log('[GeminiLive] Transitioning to customization phase');
                    optionsRef.current.onPhaseChange?.('customizing');
                    resultText = 'Transitioning to agent customization phase. Guide the user through choosing their agent\'s name, voice, personality, and backstory.';
                  } else if (fc.name === 'mark_feature_setup_step') {
                    // Feature setup walkthrough — advance to next step
                    const step = String(fc.args?.step || '');
                    const action = String(fc.args?.action || 'complete') as 'complete' | 'skip';
                    console.log(`[GeminiLive] Feature setup: ${step} → ${action}`);
                    try {
                      const state = await window.eve.featureSetup.advance(step, action);
                      if (state && state.currentStep >= state.steps.length) {
                        // All steps done — transition to normal
                        optionsRef.current.onPhaseChange?.('normal');
                        resultText = `Feature "${step}" ${action === 'complete' ? 'completed' : 'skipped'}. All features are now configured! You're fully set up.`;
                      } else {
                        // Get prompt for the next step
                        const nextStep = await window.eve.featureSetup.getCurrentStep();
                        if (nextStep) {
                          const nextPrompt = await window.eve.featureSetup.getPrompt(nextStep);
                          // Send next step prompt after a brief pause
                          setTimeout(() => {
                            sendTextRef.current?.(nextPrompt);
                          }, 2000);
                        }
                        resultText = `Feature "${step}" ${action === 'complete' ? 'completed' : 'skipped'}. Moving to the next feature.`;
                      }
                    } catch (fsErr) {
                      const fsMsg = fsErr instanceof Error ? fsErr.message : String(fsErr);
                      resultText = `Feature setup error: ${fsMsg}`;
                    }
                  } else if (fc.name === 'start_calendar_auth') {
                    // Feature setup — trigger Google Calendar OAuth flow
                    console.log('[GeminiLive] Starting Calendar OAuth');
                    try {
                      const result = await window.eve.calendar.authenticate();
                      if (result) {
                        resultText = 'Google Calendar connected successfully! The user can now ask about their schedule, and I can create events for them.';
                      } else {
                        resultText = 'Calendar authentication was cancelled or failed. The user can try again later in Settings.';
                      }
                    } catch (authErr) {
                      const authMsg = authErr instanceof Error ? authErr.message : String(authErr);
                      console.warn('[GeminiLive] Calendar auth error:', authMsg);
                      if (authMsg.includes('credentials') || authMsg.includes('ENOENT')) {
                        resultText = 'Calendar authentication failed — no Google credentials file found. The user needs to set up a Google Cloud project with Calendar API enabled and place the credentials.json file in the app data directory. This is a one-time setup. They can skip this for now and set it up later.';
                      } else {
                        resultText = `Calendar authentication failed: ${authMsg}. The user can try again later in Settings.`;
                      }
                    }
                  } else if (fc.name === 'save_api_key') {
                    // Feature setup — save an API key for a service
                    const service = String(fc.args?.service || '');
                    const apiKey = String(fc.args?.api_key || '');
                    console.log(`[GeminiLive] Saving API key for: ${service}`);
                    try {
                      const keyMap: Record<string, 'perplexity' | 'firecrawl' | 'openai' | 'elevenlabs'> = {
                        perplexity: 'perplexity',
                        firecrawl: 'firecrawl',
                        openai: 'openai',
                        elevenlabs: 'elevenlabs',
                      };
                      const settingsKey = keyMap[service];
                      if (!settingsKey) {
                        resultText = `Unknown service "${service}". Supported: perplexity, firecrawl, openai, elevenlabs.`;
                      } else if (!apiKey || apiKey.length < 8) {
                        resultText = `The API key seems too short or empty. Ask the user to double-check it.`;
                      } else {
                        await window.eve.settings.setApiKey(settingsKey, apiKey);
                        resultText = `${service.charAt(0).toUpperCase() + service.slice(1)} API key saved successfully! The service is now available.`;
                      }
                    } catch (keyErr) {
                      const keyMsg = keyErr instanceof Error ? keyErr.message : String(keyErr);
                      resultText = `Failed to save API key: ${keyMsg}`;
                    }
                  } else if (fc.name === 'set_obsidian_vault_path') {
                    // Feature setup — set Obsidian vault path
                    const vaultPath = String(fc.args?.vault_path || '');
                    console.log(`[GeminiLive] Setting Obsidian vault path: ${vaultPath}`);
                    try {
                      if (!vaultPath) {
                        resultText = 'No vault path provided. Ask the user for the full path to their Obsidian vault folder.';
                      } else {
                        await window.eve.settings.setObsidianVaultPath(vaultPath);
                        resultText = `Obsidian vault path set to "${vaultPath}". I can now read and search notes from this vault.`;
                      }
                    } catch (vaultErr) {
                      const vaultMsg = vaultErr instanceof Error ? vaultErr.message : String(vaultErr);
                      resultText = `Failed to set vault path: ${vaultMsg}`;
                    }
                  } else if (fc.name === 'toggle_screen_capture') {
                    // Feature setup — enable/disable screen capture
                    const enabled = Boolean(fc.args?.enabled);
                    console.log(`[GeminiLive] Screen capture: ${enabled ? 'enabling' : 'disabling'}`);
                    try {
                      await window.eve.settings.setAutoScreenCapture(enabled);
                      resultText = enabled
                        ? 'Screen capture enabled. I\'ll periodically capture what\'s on screen to stay contextually aware. All captures stay local and private.'
                        : 'Screen capture disabled. I won\'t capture screen content. The user can re-enable this anytime in Settings.';
                    } catch (scErr) {
                      const scMsg = scErr instanceof Error ? scErr.message : String(scErr);
                      resultText = `Failed to toggle screen capture: ${scMsg}`;
                    }
                  } else if (fc.name === 'search_episodes') {
                    const query = String(fc.args?.query || '');
                    console.log('[GeminiLive] Searching episodes:', query);

                    // Try semantic search first, fall back to text search
                    let episodes: any[] = [];
                    try {
                      const semanticResults = await window.eve.search.query(query, {
                        types: ['episode'],
                        maxResults: 8,
                      });
                      if (semanticResults.length > 0) {
                        // Fetch full episode details for semantic matches
                        const episodePromises = semanticResults.map((r: any) =>
                          window.eve.episodic.get(r.id)
                        );
                        const fetched = await Promise.all(episodePromises);
                        episodes = fetched.filter(Boolean);
                      }
                    } catch {
                      // Semantic search unavailable — fall through
                    }

                    // Fall back to text search if semantic returned nothing
                    if (episodes.length === 0) {
                      episodes = await window.eve.episodic.search(query);
                    }

                    if (episodes.length === 0) {
                      resultText = 'No matching past conversations found.';
                    } else {
                      resultText = episodes
                        .map((ep: any) => {
                          const date = new Date(ep.startTime).toLocaleDateString('en-GB', {
                            day: 'numeric',
                            month: 'short',
                            year: 'numeric',
                          });
                          const mins = Math.round(ep.durationSeconds / 60);
                          const topics = ep.topics.length > 0 ? ` [${ep.topics.join(', ')}]` : '';
                          const decisions =
                            ep.keyDecisions.length > 0
                              ? `\n  Decisions: ${ep.keyDecisions.join('; ')}`
                              : '';
                          return `- ${date} (${mins}min, ${ep.emotionalTone}): ${ep.summary}${topics}${decisions}`;
                        })
                        .join('\n');
                    }
                  } else if (fc.name === 'enable_webcam') {
                    // Webcam vision — start streaming camera frames to Gemini
                    console.log('[GeminiLive] Enabling webcam');
                    try {
                      // Clean up any existing webcam session first
                      if (webcamIntervalRef.current) clearInterval(webcamIntervalRef.current);
                      webcamStreamRef.current?.getTracks().forEach((t) => t.stop());

                      const camStream = await navigator.mediaDevices.getUserMedia({
                        video: { width: 640, height: 480, facingMode: 'user' },
                      });
                      webcamStreamRef.current = camStream;

                      // Create hidden video + canvas for frame capture
                      const video = document.createElement('video');
                      video.srcObject = camStream;
                      video.muted = true;
                      video.playsInline = true;
                      video.style.display = 'none';
                      document.body.appendChild(video);
                      await video.play();
                      webcamVideoRef.current = video;

                      const canvas = document.createElement('canvas');
                      canvas.width = 640;
                      canvas.height = 480;
                      webcamCanvasRef.current = canvas;
                      const ctx2d = canvas.getContext('2d')!;

                      // Stream ~1fps JPEG frames via realtime_input.media_chunks
                      webcamIntervalRef.current = setInterval(() => {
                        // Guard: don't send frames until Gemini has confirmed setup
                        if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN || !setupCompleteRef.current) return;
                        ctx2d.drawImage(video, 0, 0, 640, 480);
                        const dataUrl = canvas.toDataURL('image/jpeg', 0.7);
                        const b64 = dataUrl.split(',')[1]; // strip data:image/jpeg;base64, prefix
                        wsRef.current.send(
                          JSON.stringify({
                            realtime_input: {
                              media_chunks: [{ data: b64, mime_type: 'image/jpeg' }],
                            },
                          })
                        );
                      }, 1000);

                      setState((s) => ({ ...s, isWebcamActive: true }));
                      resultText = 'Webcam enabled — I can now see what your camera shows. I\'ll describe what I see. Remember to call disable_webcam when done.';
                    } catch (camErr) {
                      const camMsg = camErr instanceof Error ? camErr.message : String(camErr);
                      resultText = `Could not access webcam: ${camMsg}. The user may need to grant camera permission.`;
                    }
                  } else if (fc.name === 'disable_webcam') {
                    // Webcam vision — stop camera
                    console.log('[GeminiLive] Disabling webcam');
                    if (webcamIntervalRef.current) {
                      clearInterval(webcamIntervalRef.current);
                      webcamIntervalRef.current = null;
                    }
                    webcamStreamRef.current?.getTracks().forEach((t) => t.stop());
                    webcamStreamRef.current = null;
                    if (webcamVideoRef.current) {
                      webcamVideoRef.current.remove();
                      webcamVideoRef.current = null;
                    }
                    webcamCanvasRef.current = null;
                    setState((s) => ({ ...s, isWebcamActive: false }));
                    resultText = 'Webcam disabled.';
                  } else if (fc.name === 'join_meeting') {
                    // Live call participation — join a video call via virtual audio routing
                    const meetingUrl = String(fc.args?.meeting_url || '');
                    console.log('[GeminiLive] Joining meeting:', meetingUrl);

                    // Check if VB-Cable virtual audio is available
                    const vbAvailable = await window.eve.callIntegration.isVirtualAudioAvailable();
                    if (!vbAvailable) {
                      resultText = 'Cannot join the call — VB-Cable virtual audio driver is not installed. The user needs to install VB-Cable (free) from https://vb-audio.com/Cable/ so I can route my voice into the meeting. Ask them to install it and restart.';
                    } else {
                      try {
                        // Find the VB-Cable device ID for audio output routing
                        const devices = await navigator.mediaDevices.enumerateDevices();
                        const vbCableOutput = devices.find(
                          (d) => d.kind === 'audiooutput' && d.label.toLowerCase().includes('cable input')
                        );

                        if (vbCableOutput && playbackEngineRef.current) {
                          // Route agent's audio output to VB-Cable (appears as mic in meeting apps)
                          const routed = await playbackEngineRef.current.setOutputDevice(vbCableOutput.deviceId);
                          if (!routed) {
                            resultText = 'Found VB-Cable but failed to route audio output. The browser may not support audio device switching.';
                          } else {
                            // Enter call mode in main process (tracks state)
                            await window.eve.callIntegration.enterCallMode(meetingUrl);
                            // Open the meeting URL
                            if (meetingUrl) {
                              await window.eve.callIntegration.openMeetingUrl(meetingUrl);
                            }
                            setState((s) => ({ ...s, isInCall: true }));
                            // Auto-create meeting in Meeting Intelligence
                            try {
                              await window.eve.meetingIntel.quickStart(meetingUrl || '', `Call at ${new Date().toLocaleTimeString()}`);
                            } catch { /* non-critical */ }
                            resultText = `Joined call mode — my voice is now routed through VB-Cable virtual microphone. ${meetingUrl ? 'I\'ve opened the meeting link in the browser.' : ''} The user should select "CABLE Output (VB-Audio Virtual Cable)" as the microphone in their meeting app to hear me. I can hear them through the normal microphone. Meeting intelligence is tracking this call. Use meeting_note to capture key points. Call leave_meeting when done.`;
                          }
                        } else {
                          resultText = 'VB-Cable is installed but I couldn\'t find the "CABLE Input" output device. The user may need to restart their computer after installing VB-Cable.';
                        }
                      } catch (callErr) {
                        const callMsg = callErr instanceof Error ? callErr.message : String(callErr);
                        resultText = `Failed to join call: ${callMsg}`;
                      }
                    }
                  } else if (fc.name === 'leave_meeting') {
                    // Live call participation — leave meeting and restore normal audio
                    console.log('[GeminiLive] Leaving meeting');
                    try {
                      if (playbackEngineRef.current) {
                        await playbackEngineRef.current.resetOutputDevice();
                      }
                      await window.eve.callIntegration.exitCallMode();
                      setState((s) => ({ ...s, isInCall: false }));
                      // Auto-end meeting in Meeting Intelligence
                      try {
                        await window.eve.meetingIntel.endActive();
                      } catch { /* non-critical */ }
                      resultText = 'Left the meeting — audio routing restored to normal speakers. Meeting intelligence will generate a summary and extract action items. I\'m back to regular mode.';
                    } catch (leaveErr) {
                      const leaveMsg = leaveErr instanceof Error ? leaveErr.message : String(leaveErr);
                      resultText = `Error leaving meeting: ${leaveMsg}`;
                    }
                  } else if (fc.name === 'register_household_member') {
                    // Household voice recognition — store member info in long-term memory
                    const memberName = String(fc.args?.name || '');
                    const relationship = String(fc.args?.relationship || '');
                    const voiceDesc = String(fc.args?.voice_description || 'not yet characterized');
                    console.log('[GeminiLive] Registering household member:', memberName, relationship);

                    const memoryFact = `Household member: ${memberName} (${relationship}). Voice characteristics: ${voiceDesc}. Registered on ${new Date().toLocaleDateString()}.`;
                    await window.eve.memory.addImmediate(memoryFact, 'household');
                    resultText = `Registered ${memberName} (${relationship}) as a household member. I'll remember their voice for future sessions.`;
                  } else if (fc.name === 'update_trust') {
                    const personName = String(fc.args?.person_name || '');
                    const evidenceType = String(fc.args?.evidence_type || 'observed');
                    const description = String(fc.args?.description || '');
                    const impact = Number(fc.args?.impact || 0);
                    const domain = fc.args?.domain ? String(fc.args.domain) : undefined;
                    console.log('[GeminiLive] Trust update:', personName, evidenceType, impact);
                    const result = await window.eve.trustGraph.updateEvidence(personName, {
                      type: evidenceType, description, impact, domain,
                    });
                    if (result.ok) {
                      resultText = `Updated trust profile for ${personName} — recorded ${evidenceType} evidence (impact: ${impact > 0 ? '+' : ''}${impact}).`;
                    } else {
                      resultText = `Could not update trust for ${personName}: ${result.error || 'unknown error'}`;
                    }
                  } else if (fc.name === 'lookup_person') {
                    const personName = String(fc.args?.person_name || '');
                    console.log('[GeminiLive] Trust lookup:', personName);
                    const resolution = await window.eve.trustGraph.lookup(personName);
                    if (resolution.person) {
                      const context = await window.eve.trustGraph.getContext(resolution.person.id);
                      resultText = context || `Found ${resolution.person.primaryName} but no detailed context available yet.`;
                    } else {
                      resultText = `No person named "${personName}" found in the trust graph. They may be someone new — I'll start tracking them when more information comes up.`;
                    }
                  } else if (fc.name === 'note_interaction') {
                    const personName = String(fc.args?.person_name || '');
                    const channel = String(fc.args?.channel || 'conversation');
                    const direction = String(fc.args?.direction || 'bidirectional') as 'inbound' | 'outbound' | 'bidirectional';
                    const summary = String(fc.args?.summary || '');
                    const sentiment = Number(fc.args?.sentiment || 0);
                    console.log('[GeminiLive] Trust interaction:', personName, channel, direction);
                    const result = await window.eve.trustGraph.logComm(personName, {
                      channel, direction, summary, sentiment,
                    });
                    if (result.ok) {
                      resultText = `Logged ${channel} interaction with ${personName} (${direction}).`;
                    } else {
                      resultText = `Could not log interaction with ${personName}: ${result.error || 'unknown error'}`;
                    }
                  } else if (fc.name === 'create_meeting') {
                    // Meeting Intelligence — create a meeting
                    const meetingName = String(fc.args?.name || 'New Meeting');
                    const description = fc.args?.description ? String(fc.args.description) : undefined;
                    const attendees = Array.isArray(fc.args?.attendees) ? fc.args.attendees.map(String) : undefined;
                    const meetingUrl = fc.args?.meeting_url ? String(fc.args.meeting_url) : undefined;
                    const scheduledStart = fc.args?.scheduled_start ? String(fc.args.scheduled_start) : undefined;
                    const scheduledEnd = fc.args?.scheduled_end ? String(fc.args.scheduled_end) : undefined;
                    const tags = Array.isArray(fc.args?.tags) ? fc.args.tags.map(String) : undefined;
                    const projectName = fc.args?.project_name ? String(fc.args.project_name) : undefined;
                    console.log('[GeminiLive] Creating meeting:', meetingName);
                    try {
                      const meeting = await window.eve.meetingIntel.create({
                        name: meetingName, description, attendees, meetingUrl, scheduledStart, scheduledEnd, tags, projectName,
                      });
                      const attendeeCount = meeting.attendees?.length || 0;
                      const intelCount = meeting.attendeeIntel?.filter((a: any) => a.trustProfile)?.length || 0;
                      resultText = `Created meeting "${meetingName}" (ID: ${meeting.id}). ${attendeeCount} attendees tracked${intelCount > 0 ? `, ${intelCount} with trust intelligence` : ''}. Status: upcoming. Say "start the meeting" when ready.`;
                    } catch (err) {
                      resultText = `Failed to create meeting: ${err instanceof Error ? err.message : String(err)}`;
                    }
                  } else if (fc.name === 'meeting_note') {
                    // Meeting Intelligence — add a note to the active meeting
                    const content = String(fc.args?.content || '');
                    const noteType = (fc.args?.note_type as string) || 'note';
                    console.log('[GeminiLive] Meeting note:', noteType, content.slice(0, 50));
                    try {
                      const note = await window.eve.meetingIntel.addNoteActive(content, noteType);
                      if (note) {
                        resultText = `Noted${noteType !== 'note' ? ` [${noteType}]` : ''}: "${content.slice(0, 80)}${content.length > 80 ? '...' : ''}"`;
                      } else {
                        resultText = 'No active meeting to add note to. Create and start a meeting first.';
                      }
                    } catch (err) {
                      resultText = `Failed to add note: ${err instanceof Error ? err.message : String(err)}`;
                    }
                  } else if (fc.name === 'end_current_meeting') {
                    // Meeting Intelligence — end the active meeting
                    console.log('[GeminiLive] Ending current meeting');
                    try {
                      const meeting = await window.eve.meetingIntel.endActive();
                      if (meeting) {
                        const durationMins = meeting.startedAt && meeting.endedAt
                          ? Math.round((meeting.endedAt - meeting.startedAt) / 60000)
                          : 0;
                        const noteCount = meeting.notes?.length || 0;
                        resultText = `Meeting "${meeting.name}" ended after ${durationMins} minutes with ${noteCount} notes. Post-meeting processing started — summary and action items will be generated automatically.`;
                      } else {
                        resultText = 'No active meeting to end.';
                      }
                    } catch (err) {
                      resultText = `Failed to end meeting: ${err instanceof Error ? err.message : String(err)}`;
                    }
                  } else if (fc.name === 'get_meeting_history') {
                    // Meeting Intelligence — search or list meeting history
                    const search = fc.args?.search ? String(fc.args.search) : undefined;
                    const count = fc.args?.count ? Number(fc.args.count) : 5;
                    console.log('[GeminiLive] Meeting history:', search || 'recent', count);
                    try {
                      if (search) {
                        const results = await window.eve.meetingIntel.search(search, count);
                        if (results.length === 0) {
                          resultText = `No meetings found matching "${search}".`;
                        } else {
                          const lines = results.map((m: any) =>
                            `- "${m.name}" (${m.status}) — ${m.summary || 'no summary'}${m.actionItems?.length ? ` | Actions: ${m.actionItems.join('; ')}` : ''}`
                          );
                          resultText = `Found ${results.length} meeting(s):\n${lines.join('\n')}`;
                        }
                      } else {
                        const summaries = await window.eve.meetingIntel.recentSummaries(count);
                        if (summaries.length === 0) {
                          resultText = 'No meeting history yet.';
                        } else {
                          const lines = summaries.map((s: any) =>
                            `- "${s.name}" (${s.date}, ${s.attendeeCount} attendees) — ${s.summary}${s.actionItems?.length ? ` | Actions: ${s.actionItems.join('; ')}` : ''}`
                          );
                          resultText = `Recent meetings:\n${lines.join('\n')}`;
                        }
                      }
                    } catch (err) {
                      resultText = `Failed to get meeting history: ${err instanceof Error ? err.message : String(err)}`;
                    }
                  } else if (['operate_computer', 'browser_task', 'take_screenshot', 'click_screen', 'type_text', 'press_keys'].includes(fc.name)) {
                    // Route to Self-Operating Computer / Browser-Use tools
                    console.log('[GeminiLive] SOC tool:', fc.name);
                    try {
                      const socResult = await window.eve.soc.callTool(fc.name, fc.args || {});
                      if (socResult && typeof socResult === 'object' && 'error' in socResult) {
                        resultText = `SOC Error: ${(socResult as any).error}`;
                      } else if (fc.name === 'take_screenshot' && socResult && typeof socResult === 'object' && 'image' in socResult) {
                        // Send screenshot as image for Gemini to see
                        try {
                          if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
                            wsRef.current.send(JSON.stringify({
                              realtime_input: {
                                media_chunks: [{ data: (socResult as any).image, mime_type: 'image/png' }],
                              },
                            }));
                          }
                          resultText = `Screenshot captured (${(socResult as any).width}x${(socResult as any).height}). Image sent for visual analysis.`;
                        } catch {
                          resultText = `Screenshot captured (${(socResult as any).width}x${(socResult as any).height}) but could not send image.`;
                        }
                      } else {
                        resultText = typeof socResult === 'string' ? socResult : JSON.stringify(socResult);
                      }
                    } catch (socErr: unknown) {
                      resultText = `SOC Error: ${socErr instanceof Error ? socErr.message : String(socErr)}`;
                    }
                  } else if (fc.name.startsWith('git_')) {
                    // Route to GitLoader tools
                    console.log('[GeminiLive] GitLoader tool:', fc.name);
                    try {
                      const gitResult = await window.eve.gitLoader.callTool(fc.name, fc.args || {});
                      if (gitResult && typeof gitResult === 'object' && 'error' in gitResult) {
                        resultText = `GitLoader Error: ${(gitResult as any).error}`;
                      } else {
                        resultText = typeof gitResult === 'string' ? gitResult : JSON.stringify(gitResult);
                      }
                      // Truncate very large results (repo trees can be huge)
                      if (resultText.length > 30000) {
                        resultText = resultText.slice(0, 30000) + '\n\n... [truncated — result too large. Use git_search or git_get_file for specific files]';
                      }
                    } catch (gitErr: unknown) {
                      resultText = `GitLoader Error: ${gitErr instanceof Error ? gitErr.message : String(gitErr)}`;
                    }
                  } else if (fc.name.startsWith('browser_')) {
                    // Route to browser automation tools
                    console.log('[GeminiLive] Browser tool:', fc.name);
                    resultText = await window.eve.browser.callTool(fc.name, fc.args || {});

                    // Special handling: send screenshots as images so Gemini can SEE them
                    if (fc.name === 'browser_screenshot' && resultText && resultText.length > 1000) {
                      // resultText is base64 JPEG — send as image via realtime_input before tool response
                      try {
                        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
                          wsRef.current.send(JSON.stringify({
                            realtime_input: {
                              media_chunks: [{ data: resultText, mime_type: 'image/jpeg' }],
                            },
                          }));
                        }
                        resultText = 'Screenshot captured and sent to you for visual analysis. Describe what you see on the page — the layout, text, buttons, forms, and any relevant elements. Use this to decide your next action.';
                      } catch (imgErr) {
                        console.warn('[GeminiLive] Failed to send screenshot image:', imgErr);
                        resultText = 'Screenshot taken but could not send image for analysis. Use browser_read_page to get text content instead.';
                      }
                    }
                  } else {
                    // Check if this is a connector tool (dynamic software-mastery tools)
                    let isConnector = false;
                    try {
                      isConnector = await window.eve.connectors.isConnectorTool(fc.name);
                    } catch {
                      // Connector system not available — fall through to desktop tools
                    }

                    if (isConnector) {
                      // Route to connector registry (PowerShell, VS Code, Git, Office, Adobe, etc.)
                      console.log('[GeminiLive] Connector tool:', fc.name);
                      const result = await window.eve.connectors.callTool(fc.name, fc.args || {});
                      if (result.error) {
                        resultText = `Error: ${result.error}`;
                      } else {
                        resultText = result.result || 'Done.';
                      }
                    } else if (mcpToolNamesRef.current.has(fc.name)) {
                      // Route to MCP servers (Desktop Commander, user-added servers, etc.)
                      console.log('[GeminiLive] MCP tool:', fc.name);
                      try {
                        const mcpResult = await window.eve.mcp.callTool(fc.name, fc.args || {});
                        // MCP returns content array — extract text
                        if (Array.isArray(mcpResult)) {
                          resultText = mcpResult
                            .map((c: any) => (c.type === 'text' ? c.text : JSON.stringify(c)))
                            .join('\n');
                        } else {
                          resultText = typeof mcpResult === 'string' ? mcpResult : JSON.stringify(mcpResult);
                        }
                      } catch (mcpErr: unknown) {
                        const mcpMsg = mcpErr instanceof Error ? mcpErr.message : String(mcpErr);
                        resultText = `MCP Error: ${mcpMsg}`;
                      }
                    } else {
                      // Route to desktop tools (includes file system, keyboard sim, screen reading)
                      const result = await window.eve.desktop.callTool(fc.name, fc.args || {});
                      if (result.error) {
                        resultText = `Error: ${result.error}`;
                      } else {
                        resultText = result.result || 'Done.';
                      }
                    }
                  }

                  const durationMs = Date.now() - toolStartTime;
                  optionsRef.current.onToolEnd?.(actionId, fc.name, true);
                  // Record tool call metrics for session health
                  try { window.eve.sessionHealth.recordToolCall(fc.name, true, durationMs); } catch { /* ignored */ }
                  return {
                    response: { result: resultText },
                    id: fc.id,
                  };
                } catch (err: unknown) {
                  success = false;
                  const durationMs = Date.now() - toolStartTime;
                  const msg = err instanceof Error ? err.message : String(err);
                  optionsRef.current.onToolEnd?.(actionId, fc.name, false);
                  // Record tool failure for session health
                  try {
                    window.eve.sessionHealth.recordToolCall(fc.name, false, durationMs);
                    window.eve.sessionHealth.recordError(fc.name, msg);
                  } catch { /* ignored */ }
                  console.error(`[GeminiLive] Tool "${fc.name}" failed (${durationMs}ms):`, msg);
                  return { response: { error: `Tool error (${fc.name}): ${msg}` }, id: fc.id };
                }
              });

              const responses = await Promise.all(responsePromises);
              ws.send(JSON.stringify({ toolResponse: { functionResponses: responses } }));
            }
          } catch (err) {
            console.warn('[GeminiLive] Message parse error:', err);
          }
        };

        ws.onclose = (event) => {
          clearTimeout(timeout);
          if (keepaliveRef.current) {
            clearInterval(keepaliveRef.current);
            keepaliveRef.current = null;
          }
          const reason = event.reason || `code ${event.code}`;
          console.log('[GeminiLive] WebSocket closed:', reason);
          // Track close reason in session health
          try { window.eve.sessionHealth.recordWsClose(event.code, reason); } catch { /* ignored */ }

          // During SM-managed reconnects, DON'T end session or stop idle — SM handles it
          if (!smReconnectingRef.current) {
            sessionManagerRef.current?.sessionEnded();
            idleBehaviorRef.current?.stop();
          }

          const wasConnected = stateRef.current.isConnected;
          setState((s) => ({
            ...s,
            isConnected: false,
            isConnecting: false,
            // CRITICAL: Preserve isListening during SM reconnects — mic pipeline stays alive
            isListening: smReconnectingRef.current ? s.isListening : (intentionalDisconnectRef.current ? false : s.isListening),
            error: wasConnected ? '' : `Connection closed: ${reason}`,
          }));

          // Auto-reconnect on unexpected disconnect (not user-initiated)
          // Self-managed retry loop — does NOT rely on onclose re-firing (prevents rapid-fire loop)
          // smReconnectingRef blocks this when SessionManager is handling its own reconnect
          if (wasConnected && !intentionalDisconnectRef.current && !smReconnectingRef.current && !isAutoReconnectingRef.current) {
            isAutoReconnectingRef.current = true;
            sessionManagerRef.current?.sessionEnded(); // Pause 13-min timer during recovery

            const MAX_AUTO_RECONNECT = 15;

            const attemptReconnect = async () => {
              wsReconnectAttemptsRef.current++;
              const attempt = wsReconnectAttemptsRef.current;

              // Hard limit — stop trying and let the user manually reconnect via the orb
              if (attempt > MAX_AUTO_RECONNECT) {
                console.warn(`[GeminiLive] All ${MAX_AUTO_RECONNECT} reconnect attempts exhausted`);
                isAutoReconnectingRef.current = false;
                setState((s) => ({ ...s, error: 'Connection lost — tap the orb to reconnect' }));
                return;
              }

              // Network-aware: if offline, wait for online event instead of hammering
              if (!navigator.onLine) {
                console.log('[GeminiLive] Network offline — waiting for connectivity...');
                setState((s) => ({ ...s, error: 'Network offline — will reconnect automatically' }));
                const onlineHandler = () => {
                  window.removeEventListener('online', onlineHandler);
                  console.log('[GeminiLive] Network back online — resuming reconnect');
                  attemptReconnect();
                };
                window.addEventListener('online', onlineHandler);
                return;
              }

              const delay = Math.min(attempt * 3000, 30000); // 3s, 6s, 9s, ... capped at 30s
              console.log(`[GeminiLive] Auto-reconnect attempt ${attempt}/${MAX_AUTO_RECONNECT} in ${delay}ms`);
              setState((s) => ({
                ...s,
                error: attempt <= 5
                  ? `Reconnecting... (attempt ${attempt}/${MAX_AUTO_RECONNECT})`
                  : `Reconnecting... (attempt ${attempt}/${MAX_AUTO_RECONNECT}) — tap orb to retry now`,
              }));

              await new Promise((r) => setTimeout(r, delay));

              // Bail out if user manually disconnected or SessionManager took over during the wait
              if (intentionalDisconnectRef.current || smReconnectingRef.current) {
                isAutoReconnectingRef.current = false;
                return;
              }

              try {
                const instruction = await window.eve.getLiveSystemInstruction();
                const conversationSummary = sessionManagerRef.current?.buildConversationSummary() || '';
                const accentDesc = agentAccentRef.current || 'American';
                const nameDesc = agentNameRef.current || 'the agent';
                const voiceAnchor = `\n\nCRITICAL: You are reconnecting mid-conversation. Maintain your ${accentDesc} accent and vocal identity EXACTLY as before. Do NOT change voice, accent, or character. You are ${nameDesc} — pick up seamlessly.`;
                const fullInstruction = conversationSummary
                  ? `${instruction}\n\n${conversationSummary}${voiceAnchor}`
                  : `${instruction}${voiceAnchor}`;
                await connect(fullInstruction, toolsRef.current, voiceNameRef.current);
                // Success!
                wsReconnectAttemptsRef.current = 0;
                isAutoReconnectingRef.current = false;
                sessionManagerRef.current?.sessionStarted();
                try {
                  window.eve.sessionHealth.recordReconnect('auto-retry', true);
                  window.eve.sessionHealth.recordVoiceAnchor();
                } catch { /* ignored */ }
                // Safety net: only restart mic if the pipeline somehow died
                if (!stateRef.current.isListening || !audioContextRef.current || audioContextRef.current.state === 'closed') {
                  console.log('[GeminiLive] Mic pipeline down after auto-reconnect — restarting');
                  startListeningRef.current?.();
                } else {
                  console.log('[GeminiLive] Mic pipeline alive through auto-reconnect — seamless');
                }
                console.log('[GeminiLive] Auto-reconnect successful');
              } catch (err) {
                console.warn(`[GeminiLive] Auto-reconnect attempt ${attempt} failed:`, err);
                try { window.eve.sessionHealth.recordReconnect('auto-retry', false); } catch { /* ignored */ }
                // Self-loop — NOT relying on onclose to re-trigger (prevents rapid-fire)
                attemptReconnect();
              }
            };

            attemptReconnect();
          }

          reject(new Error(`WebSocket closed: ${reason}`));
        };

        ws.onerror = (event) => {
          clearTimeout(timeout);
          const detail = navigator.onLine ? 'API key may be invalid or Gemini service is down' : 'device appears offline';
          const msg = `WebSocket connection failed — ${detail}`;
          console.error('[GeminiLive] WebSocket error event:', event, '| Online:', navigator.onLine);
          setState((s) => ({ ...s, isConnecting: false, error: msg }));
          optionsRef.current.onError?.(msg);
          reject(new Error(msg));
        };
      });
    },
    [getApiBase]
  );

  // --- Send text into the Gemini session (for reminders, predictions, etc.) ---
  const sendTextToGemini = useCallback((text: string) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;

    wsRef.current.send(
      JSON.stringify({
        client_content: {
          turns: [{ role: 'user', parts: [{ text }] }],
          turn_complete: true,
        },
      })
    );

    // Record user turn in session manager for context continuity
    if (!text.startsWith('[SYSTEM') && !text.startsWith('[IDLE')) {
      sessionManagerRef.current?.addEntry('user', text);
      // User interaction resets idle behavior
      idleBehaviorRef.current?.resetActivity();
      setState((s) => ({ ...s, idleTier: 0 }));
    }
  }, []);

  // Keep sendTextRef updated so tool handlers can send text
  sendTextRef.current = sendTextToGemini;

  // --- Start mic capture + screen sharing ---
  const startListening = useCallback(async () => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      console.warn('[GeminiLive] Cannot start listening — not connected');
      return;
    }

    // Ensure any previous mic pipeline is fully torn down before recreating
    // (critical for reconnect scenarios — prevents orphaned AudioContexts)
    if (audioContextRef.current) {
      console.log('[GeminiLive] Tearing down stale audio context before restart');
      try { workletNodeRef.current?.disconnect(); } catch { /* teardown */ }
      try { processorRef.current?.disconnect(); } catch { /* teardown */ }
      try { audioContextRef.current.close(); } catch { /* teardown */ }
      audioContextRef.current = null;
      workletNodeRef.current = null;
      processorRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }

    try {
      // Flush any in-progress audio playback so Friday stops talking immediately
      playbackEngineRef.current?.flush();

      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          sampleRate: 16000,
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,       // Stabilizes mic levels over long sessions
        },
      });
      streamRef.current = stream;

      const audioContext = new AudioContext({ sampleRate: 16000 });
      audioContextRef.current = audioContext;
      const source = audioContext.createMediaStreamSource(stream);

      // Create mic analyser for audio reactivity
      const micAnalyser = audioContext.createAnalyser();
      micAnalyser.fftSize = 256;
      micAnalyser.smoothingTimeConstant = 0.8;
      source.connect(micAnalyser);
      micAnalyserRef.current = micAnalyser;
      micAnalyserDataRef.current = new Uint8Array(micAnalyser.frequencyBinCount);

      // Try AudioWorklet first, fall back to ScriptProcessorNode
      let workletLoaded = false;
      try {
        await audioContext.audioWorklet.addModule('./pcm-capture-processor.js');
        const workletNode = new AudioWorkletNode(audioContext, 'pcm-capture-processor');
        workletNodeRef.current = workletNode;

        workletNode.port.onmessage = (e: MessageEvent<ArrayBuffer>) => {
          // Guard: don't send audio until Gemini has confirmed setup (prevents pre-setup contamination on reconnect)
          if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN || !setupCompleteRef.current) return;
          const b64 = arrayBufferToBase64(e.data);
          wsRef.current.send(
            JSON.stringify({
              realtime_input: {
                media_chunks: [{ data: b64, mime_type: 'audio/pcm;rate=16000' }],
              },
            })
          );
        };

        source.connect(workletNode);
        workletNode.connect(audioContext.destination);
        workletLoaded = true;
        console.log('[GeminiLive] Using AudioWorklet for mic capture');
      } catch (workletErr) {
        console.warn('[GeminiLive] AudioWorklet unavailable, falling back to ScriptProcessor:', workletErr);
      }

      if (!workletLoaded) {
        const processor = audioContext.createScriptProcessor(4096, 1, 1);
        processorRef.current = processor;

        processor.onaudioprocess = (event) => {
          // Guard: don't send audio until Gemini has confirmed setup (prevents pre-setup contamination on reconnect)
          if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN || !setupCompleteRef.current) return;

          const input = event.inputBuffer.getChannelData(0);
          const pcm16 = float32ToInt16(input);
          const b64 = arrayBufferToBase64(pcm16.buffer as ArrayBuffer);

          wsRef.current.send(
            JSON.stringify({
              realtime_input: {
                media_chunks: [{ data: b64, mime_type: 'audio/pcm;rate=16000' }],
              },
            })
          );
        };

        source.connect(processor);
        processor.connect(audioContext.destination);
        console.log('[GeminiLive] Using ScriptProcessorNode for mic capture (fallback)');
      }

      // Start screen capture and forward frames to Gemini (skip if already running from auto-start)
      if (window.eve.screenCapture && !screenFrameCleanupRef.current) {
        await window.eve.screenCapture.start();
        const cleanup = window.eve.screenCapture.onFrame((frame: string) => {
          // Guard: don't send frames until Gemini has confirmed setup
          if (wsRef.current?.readyState === WebSocket.OPEN && setupCompleteRef.current) {
            wsRef.current.send(
              JSON.stringify({
                realtime_input: {
                  media_chunks: [{ data: frame, mime_type: 'image/jpeg' }],
                },
              })
            );
          }
        });
        screenFrameCleanupRef.current = cleanup;
      }

      setState((s) => ({ ...s, isListening: true }));
      const wsState = wsRef.current?.readyState === WebSocket.OPEN ? 'OPEN' : 'CLOSED';
      const acState = audioContextRef.current?.state || 'none';
      const micTracks = streamRef.current?.getAudioTracks().length || 0;
      console.log(`[GeminiLive] Listening started — ws:${wsState} audioCtx:${acState} micTracks:${micTracks}`);
    } catch (err) {
      console.error('[GeminiLive] Mic access error:', err);
      optionsRef.current.onError?.('Microphone access denied');
    }
  }, []);

  // Keep ref in sync for auto-reconnect closure
  startListeningRef.current = startListening;

  // --- Stop mic capture + screen sharing ---
  const stopListening = useCallback(() => {
    workletNodeRef.current?.disconnect();
    workletNodeRef.current = null;

    processorRef.current?.disconnect();
    processorRef.current = null;

    // Close mic AudioContext — wait for it to fully close before allowing restart
    try { audioContextRef.current?.close(); } catch { /* teardown */ }
    audioContextRef.current = null;

    // Fully stop all mic media tracks so the browser releases the device
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;

    // Clear mic analyser so startListening creates fresh ones
    micAnalyserRef.current = null;
    micAnalyserDataRef.current = null;

    screenFrameCleanupRef.current?.();
    screenFrameCleanupRef.current = null;

    window.eve.screenCapture?.stop();

    // Auto-cleanup webcam if active
    if (webcamIntervalRef.current) {
      clearInterval(webcamIntervalRef.current);
      webcamIntervalRef.current = null;
    }
    webcamStreamRef.current?.getTracks().forEach((t) => t.stop());
    webcamStreamRef.current = null;
    if (webcamVideoRef.current) {
      webcamVideoRef.current.remove();
      webcamVideoRef.current = null;
    }
    webcamCanvasRef.current = null;

    // Auto-cleanup call mode if active
    if (playbackEngineRef.current?.getCurrentSinkId()) {
      playbackEngineRef.current.resetOutputDevice().catch(() => {});
      window.eve.callIntegration.exitCallMode().catch(() => {});
    }

    setState((s) => ({ ...s, isListening: false, isWebcamActive: false, isInCall: false }));
  }, []);

  // --- Full disconnect ---
  const disconnect = useCallback(() => {
    intentionalDisconnectRef.current = true;
    isAutoReconnectingRef.current = false;

    // Create episodic memory from this session before tearing down
    const sm = sessionManagerRef.current;
    if (sm) {
      const history = sm.getConversationHistory();
      const duration = sm.getSessionDuration();
      if (history.length >= 4 && duration >= 60) {
        const now = Date.now();
        const startTime = now - duration * 1000;
        const transcript = history.map((h: { role: string; content: string }) => ({
          role: h.role,
          text: h.content,
        }));
        window.eve.episodic
          .create(transcript, startTime, now)
          .then((episode: any) => {
            if (episode) {
              console.log(`[GeminiLive] Episodic memory created: ${episode.id.slice(0, 8)}`);
            }
          })
          .catch((err: unknown) => {
            console.warn('[GeminiLive] Episodic memory creation failed:', err);
          });
      }
    }

    stopListening();
    wsRef.current?.close();
    wsRef.current = null;
    playbackEngineRef.current?.flush();
    sessionManagerRef.current?.reset();
    idleBehaviorRef.current?.stop();
    if (keepaliveRef.current) {
      clearInterval(keepaliveRef.current);
      keepaliveRef.current = null;
    }
    setState({ isConnected: false, isConnecting: false, isListening: false, isSpeaking: false, isWebcamActive: false, isInCall: false, transcript: '', error: '', idleTier: 0 });
  }, [stopListening]);

  // --- Reset idle activity (call from App on any user interaction) ---
  const resetIdleActivity = useCallback(() => {
    idleBehaviorRef.current?.resetActivity();
    setState((s) => (s.idleTier !== 0 ? { ...s, idleTier: 0 } : s));
  }, []);

  // Wire session manager reconnect callbacks
  useEffect(() => {
    const sm = sessionManagerRef.current;
    if (!sm) return;

    sm.setCallbacks({
      getSystemInstruction: () => window.eve.getLiveSystemInstruction(),
      closeConnection: () => {
        // Block auto-reconnect: smReconnectingRef stays true until connect() completes
        // This prevents the race where onclose fires before connect() finishes
        smReconnectingRef.current = true;
        intentionalDisconnectRef.current = true;
        // CRITICAL: Gate mic/screen/webcam BEFORE closing WS — prevents pre-setup contamination
        setupCompleteRef.current = false;
        wsRef.current?.close();
        wsRef.current = null;
        // Flush playback completely — kills in-flight audio sources and resets speaking state
        playbackEngineRef.current?.flush();
        // Explicitly reset speaking state in case flush didn't trigger the callback
        setState((s) => (s.isSpeaking ? { ...s, isSpeaking: false } : s));
        if (keepaliveRef.current) {
          clearInterval(keepaliveRef.current);
          keepaliveRef.current = null;
        }
        // NOTE: Mic pipeline intentionally NOT stopped — it stays alive.
        // Mic/screen/webcam callbacks all reference wsRef, so they auto-switch
        // to the new WebSocket once reconnect completes. During the brief gap
        // (~1-2s), frames are silently dropped because wsRef.current is null.
      },
      reconnect: async (instruction: string) => {
        // Reset the flag so future unexpected disconnects can still auto-reconnect
        intentionalDisconnectRef.current = false;
        await connect(instruction, undefined, voiceNameRef.current);
        // Unblock auto-reconnect ONLY after connect succeeds
        smReconnectingRef.current = false;

        // Fix E: Idle stabilization — prevent idle cues during first 5s after reconnect
        reconnectStabilizingRef.current = true;
        setTimeout(() => {
          reconnectStabilizingRef.current = false;
        }, 5000);
      },
      startListening: async () => {
        // Safety net only — mic should still be alive through the reconnect.
        // Only restart if the pipeline somehow died (AudioContext closed, etc.)
        if (!stateRef.current.isListening || !audioContextRef.current || audioContextRef.current.state === 'closed') {
          console.log('[GeminiLive] SM reconnect: mic pipeline down — restarting');
          await startListening();
        } else {
          console.log('[GeminiLive] SM reconnect: mic pipeline alive — seamless');
        }
      },
      isSpeaking: () => stateRef.current.isSpeaking,
    });
  }, [connect, startListening]);

  // Wire idle behavior callbacks
  useEffect(() => {
    const ib = idleBehaviorRef.current;
    if (!ib) return;

    ib.setCallbacks({
      sendSystemText: (text: string) => {
        sendTextToGemini(text);
        // Update idle tier in state for UI reactivity
        const tier = idleBehaviorRef.current?.getTier() ?? 0;
        setState((s) => ({ ...s, idleTier: tier }));
      },
      getAmbientContext: () => ambientContextCacheRef.current,
      // Fix E: Block idle cues during reconnect stabilization period (first 5s after reconnect)
      isActive: () => stateRef.current.isConnected && stateRef.current.isListening && !reconnectStabilizingRef.current,
      isSpeaking: () => stateRef.current.isSpeaking,
    });
  }, [sendTextToGemini]);

  // Poll ambient context from main process and cache it for synchronous access
  useEffect(() => {
    const poll = async () => {
      try {
        ambientContextCacheRef.current = await window.eve.ambient.getContextString();
      } catch {
        // Non-critical — keep last cached value
      }
    };
    poll();
    const interval = setInterval(poll, 15_000); // Refresh every 15s
    return () => clearInterval(interval);
  }, []);

  // Start/stop idle behavior based on listening state
  useEffect(() => {
    const ib = idleBehaviorRef.current;
    if (!ib) return;

    if (state.isConnected && state.isListening) {
      ib.start();
    } else {
      ib.stop();
      setState((s) => (s.idleTier !== 0 ? { ...s, idleTier: 0 } : s));
    }
  }, [state.isConnected, state.isListening]);

  // Reset idle timer when Friday finishes speaking (conversation is active)
  useEffect(() => {
    if (!state.isSpeaking) {
      // Friday just stopped speaking — user might respond, reset idle timer
      idleBehaviorRef.current?.resetActivity();
    }
  }, [state.isSpeaking]);

  // Proactive agent result surfacing — poll completed agents and inject results into conversation
  useEffect(() => {
    if (!state.isConnected || !state.isListening) return;

    const surfacedAgentsRef = new Set<string>();
    const AGENT_POLL_INTERVAL = 15_000; // Check every 15s

    const poll = async () => {
      try {
        const tasks = await window.eve.agents.list('completed');
        if (!Array.isArray(tasks)) return;

        for (const task of tasks) {
          if (surfacedAgentsRef.has(task.id)) continue;
          surfacedAgentsRef.add(task.id);

          // Only surface recent results (completed within last 2 minutes)
          const completedAt = task.completedAt || 0;
          if (Date.now() - completedAt > 120_000) continue;

          // Don't interrupt if Friday is currently speaking
          if (stateRef.current.isSpeaking) {
            // Retry on next poll cycle
            surfacedAgentsRef.delete(task.id);
            continue;
          }

          const resultPreview = task.result
            ? String(task.result).slice(0, 1500)
            : 'No result returned.';

          const injection = `[SYSTEM: Background agent "${task.description}" (type: ${task.agentType}) has completed. Here is the result — share it with the user naturally when appropriate, don't interrupt if they're mid-thought:\n\n${resultPreview}${String(task.result || '').length > 1500 ? '\n\n(Result truncated — full result available via check_agent tool)' : ''}]`;

          console.log(`[GeminiLive] Surfacing agent result: ${task.id.slice(0, 8)} — ${task.description}`);
          sendTextToGemini(injection);
        }
      } catch (err) {
        // Non-critical — silent fail
      }
    };

    const interval = setInterval(poll, AGENT_POLL_INTERVAL);
    // Run once immediately on mount
    poll();

    return () => clearInterval(interval);
  }, [state.isConnected, state.isListening, sendTextToGemini]);

  // --- System sleep/resume recovery ---
  // When the system wakes from sleep, WebSockets are dead but onclose may not fire.
  // We detect wake-up via a timer gap and trigger reconnection if needed.
  useEffect(() => {
    if (!state.isConnected) return;

    let lastHeartbeat = Date.now();
    const HEARTBEAT_INTERVAL = 5000; // Check every 5 seconds
    const SLEEP_THRESHOLD = 15000;   // If 15s+ passed since last heartbeat, we probably slept

    const heartbeat = setInterval(() => {
      const now = Date.now();
      const gap = now - lastHeartbeat;
      lastHeartbeat = now;

      if (gap > SLEEP_THRESHOLD) {
        console.warn(`[GeminiLive] Detected system wake-up (${Math.round(gap / 1000)}s gap) — checking connection health`);

        // The WebSocket is almost certainly dead after sleep
        const ws = wsRef.current;
        if (!ws || ws.readyState !== WebSocket.OPEN) {
          console.warn('[GeminiLive] WebSocket dead after wake — triggering reconnect');
          // Use session manager for a clean reconnect with conversation context
          const sm = sessionManagerRef.current;
          if (sm && !smReconnectingRef.current && !isAutoReconnectingRef.current) {
            intentionalDisconnectRef.current = true;
            sm.requestReconnect();
          }
        } else {
          // WS reports open but might be stale — send keepalive to test
          try {
            const silentPcm = new ArrayBuffer(320);
            const silentB64 = btoa(String.fromCharCode(...new Uint8Array(silentPcm)));
            ws.send(JSON.stringify({
              realtime_input: {
                media_chunks: [{ data: silentB64, mime_type: 'audio/pcm;rate=16000' }],
              },
            }));
          } catch {
            console.warn('[GeminiLive] Post-wake keepalive failed — triggering reconnect');
            const sm = sessionManagerRef.current;
            if (sm && !smReconnectingRef.current) {
              intentionalDisconnectRef.current = true;
              sm.requestReconnect();
            }
          }
        }
      }
    }, HEARTBEAT_INTERVAL);

    return () => clearInterval(heartbeat);
  }, [state.isConnected]);

  // --- Tab focus recovery ---
  // Electron can suspend AudioContexts when the window loses focus for extended periods.
  // Resume them immediately when the user returns.
  useEffect(() => {
    if (!state.isConnected) return;

    const onVisibilityChange = async () => {
      if (document.visibilityState === 'visible') {
        // User returned — resume all AudioContexts
        const mic = audioContextRef.current;
        if (mic && mic.state === 'suspended') {
          console.log('[GeminiLive] Window regained focus — resuming mic AudioContext');
          try { await mic.resume(); } catch { /* ignored */ }
        }
        const playback = playbackEngineRef.current;
        if (playback) {
          try { await playback.resumeIfSuspended(); } catch { /* ignored */ }
        }
      }
    };

    document.addEventListener('visibilitychange', onVisibilityChange);
    return () => document.removeEventListener('visibilitychange', onVisibilityChange);
  }, [state.isConnected]);

  // --- Mic + AudioContext health monitor ---
  // Detects dead mic tracks (system sleep/resume, USB unplug, permission revocation)
  // and suspended AudioContexts (browser autoplay policy, prolonged background tab)
  useEffect(() => {
    if (!state.isConnected || !state.isListening) return;

    const HEALTH_CHECK_INTERVAL = 10_000; // Every 10 seconds

    const healthCheck = async () => {
      // 1. Check if mic stream tracks are still alive
      const stream = streamRef.current;
      if (stream) {
        const tracks = stream.getAudioTracks();
        const hasLiveTrack = tracks.some((t) => t.readyState === 'live' && t.enabled);
        if (!hasLiveTrack && tracks.length > 0) {
          console.warn('[GeminiLive] Mic track died (sleep/unplug?) — restarting mic pipeline');
          try {
            await startListening();
          } catch (err) {
            console.error('[GeminiLive] Mic restart failed:', err);
          }
          return;
        }
      }

      // 2. Check if mic AudioContext got suspended (browser tab background, screen lock)
      const ctx = audioContextRef.current;
      if (ctx && ctx.state === 'suspended') {
        console.warn('[GeminiLive] Mic AudioContext suspended — resuming');
        try {
          await ctx.resume();
          console.log('[GeminiLive] Mic AudioContext resumed successfully');
        } catch (err) {
          console.warn('[GeminiLive] Mic AudioContext resume failed — restarting pipeline:', err);
          await startListening();
        }
      }

      // 3. Check if AudioContext was closed unexpectedly
      if (ctx && ctx.state === 'closed') {
        console.warn('[GeminiLive] Mic AudioContext closed unexpectedly — restarting pipeline');
        await startListening();
      }

      // 4. Ensure playback AudioContext is also alive (it can suspend independently)
      const playback = playbackEngineRef.current;
      if (playback) {
        try {
          await playback.resumeIfSuspended();
        } catch {
          // Non-critical — playback will auto-resume when next chunk arrives
        }
      }
    };

    const interval = setInterval(healthCheck, HEALTH_CHECK_INTERVAL);
    // Run once immediately
    healthCheck();

    return () => clearInterval(interval);
  }, [state.isConnected, state.isListening, startListening]);

  // Periodic memory extraction — every 5 min during connected sessions
  useEffect(() => {
    if (!state.isConnected) return;

    const EXTRACT_INTERVAL = 5 * 60 * 1000; // 5 min
    const timer = setInterval(() => {
      const sm = sessionManagerRef.current;
      if (!sm) return;

      const history = sm.getConversationHistory();
      if (history.length >= 4) {
        console.log('[GeminiLive] Running periodic memory extraction...');
        window.eve.memory.extract(history).catch((err: unknown) => {
          console.warn('[GeminiLive] Memory extraction failed:', err);
        });
      }
    }, EXTRACT_INTERVAL);

    return () => clearInterval(timer);
  }, [state.isConnected]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      disconnect();
    };
  }, [disconnect]);

  /** Get current mic input level (0–1) — call from RAF loop, not React render */
  const getMicLevel = useCallback((): number => {
    const analyser = micAnalyserRef.current;
    const data = micAnalyserDataRef.current;
    if (!analyser || !data) return 0;
    const buf = data as Uint8Array<ArrayBuffer>;
    analyser.getByteFrequencyData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) {
      const v = buf[i] / 255;
      sum += v * v;
    }
    return Math.sqrt(sum / buf.length);
  }, []);

  /** Get current playback output level (0–1) — call from RAF loop, not React render */
  const getOutputLevel = useCallback((): number => {
    return playbackEngineRef.current?.getOutputLevel() ?? 0;
  }, []);

  return {
    ...state,
    connect,
    startListening,
    stopListening,
    disconnect,
    sendTextToGemini,
    getMicLevel,
    getOutputLevel,
    resetIdleActivity,
    sessionManager: sessionManagerRef.current,
  };
}

// --- Audio helpers ---

function float32ToInt16(f32: Float32Array): Int16Array {
  const i16 = new Int16Array(f32.length);
  for (let i = 0; i < f32.length; i++) {
    const s = Math.max(-1, Math.min(1, f32[i]));
    i16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return i16;
}

function base64ToFloat32(b64: string): Float32Array {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const i16 = new Int16Array(bytes.buffer);
  const f32 = new Float32Array(i16.length);
  for (let i = 0; i < i16.length; i++) {
    f32[i] = i16[i] / (i16[i] < 0 ? 0x8000 : 0x7fff);
  }
  return f32;
}

function arrayBufferToBase64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let bin = '';
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin);
}
