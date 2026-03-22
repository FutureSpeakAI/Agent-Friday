/**
 * Tool constant declarations and schema helpers for Gemini Live.
 *
 * Every tool object that Gemini can call is defined here, along with
 * sanitizeSchema() and buildFunctionDeclarations().
 */

// ── Sanitise tool schemas for Gemini compatibility ──

export function sanitizeSchema(schema: unknown): Record<string, unknown> {
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

// ── Claude Opus tool declaration ──

export const ASK_CLAUDE_TOOL = {
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

// ── Save memory tool ──

export const SAVE_MEMORY_TOOL = {
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

// ── Setup intelligence tool ──

export const SETUP_INTELLIGENCE_TOOL = {
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

// ── Self-improvement tools ──

export const SELF_IMPROVE_TOOLS = [
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

// ── Webcam tools ──

export const WEBCAM_TOOLS = [
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

// ── Household voice recognition tools ──

export const HOUSEHOLD_TOOLS = [
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

// ── Live call participation tools ──

export const CALL_TOOLS = [
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

// ── Meeting Intelligence tools ──

export const MEETING_INTEL_TOOLS = [
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

// ── Trust Graph tools ──

export const TRUST_GRAPH_TOOLS = [
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

// ── Multimedia creation tools ──

export const MULTIMEDIA_TOOLS = [
  {
    name: 'create_podcast',
    description:
      'Create a multi-speaker podcast from sources (files, URLs, conversation topics, or memories). Generates a WAV audio file with distinct speakers discussing the content. Use when the user wants to turn content into an engaging audio discussion, create a podcast-style summary, or explore a topic through conversation format. The podcast will have multiple speakers with different voices.',
    parameters: {
      type: 'object',
      properties: {
        topic: {
          type: 'string',
          description: 'Main topic or title for the podcast episode.',
        },
        sources: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              type: { type: 'string', enum: ['text', 'url', 'file', 'conversation', 'memory'] },
              content: { type: 'string', description: 'The source content, URL, file path, or topic text.' },
            },
          },
          description: 'Content sources to base the podcast on.',
        },
        style: {
          type: 'string',
          enum: ['deep-dive', 'debate', 'summary', 'interview', 'explainer', 'storytelling'],
          description: 'Podcast format/style. Defaults to deep-dive.',
        },
        duration_minutes: {
          type: 'number',
          description: 'Target duration in minutes (5-30). Defaults to 10.',
        },
      },
      required: ['topic'],
    },
  },
  {
    name: 'create_visual',
    description:
      'Create a visual artifact — infographic, diagram, chart, timeline, dashboard, or any visual content. Generates self-contained HTML/CSS/SVG rendered to an image file. Use when the user asks you to visualize data, create an infographic, make a diagram, or produce any visual content.',
    parameters: {
      type: 'object',
      properties: {
        prompt: {
          type: 'string',
          description: 'What to create — be descriptive about the visual content, data, layout, and style.',
        },
        type: {
          type: 'string',
          enum: ['infographic', 'diagram', 'chart', 'timeline', 'dashboard', 'poster', 'card', 'other'],
          description: 'Type of visual to create.',
        },
        data: {
          type: 'string',
          description: 'Optional structured data to visualize (JSON, CSV, or text).',
        },
      },
      required: ['prompt', 'type'],
    },
  },
  {
    name: 'create_audio_message',
    description:
      'Create a polished audio message or voice note using text-to-speech. Generates a WAV file with the agent\'s voice speaking the given text. Use when the user wants to send a voice message, create an audio memo, or produce spoken content.',
    parameters: {
      type: 'object',
      properties: {
        text: {
          type: 'string',
          description: 'The text to convert to speech.',
        },
        voice: {
          type: 'string',
          description: 'Voice name to use (default: agent\'s configured voice). Options: Kore, Puck, Charon, Fenrir, Leda, Orus, Zephyr, Aoede.',
        },
      },
      required: ['text'],
    },
  },
  {
    name: 'create_music',
    description:
      'Generate a short music piece or sound design. Creates a WAV file with AI-generated audio. Use when the user asks for background music, a jingle, ambient sounds, or any musical creation.',
    parameters: {
      type: 'object',
      properties: {
        prompt: {
          type: 'string',
          description: 'Description of the music to create (genre, mood, instruments, tempo, etc.).',
        },
        duration_seconds: {
          type: 'number',
          description: 'Duration in seconds (5-60). Default: 15.',
        },
      },
      required: ['prompt'],
    },
  },
];

// ── Episodic memory tool ──

export const SEARCH_EPISODES_TOOL = {
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

// ── Background agent tools ──

export const AGENT_TOOLS = [
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

// ── Document tools ──

export const DOCUMENT_TOOLS = [
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

// ── Project tools ──

export const PROJECT_TOOLS = [
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

// ── Calendar tools ──

export const CALENDAR_TOOLS = [
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

// ── Communications tool ──

export const COMMUNICATIONS_TOOL = {
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

// ── Scheduler tools ──

export const SCHEDULER_TOOLS = [
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

// ── Build the full functionDeclarations array for the Gemini setup message ──

export interface BuildFunctionDeclarationsOptions {
  onboardingMode: boolean;
  mappedExternalTools: Array<{ name: string; description: string; parameters: Record<string, unknown> }>;
  browserToolDecls: Array<{ name: string; description: string; parameters: Record<string, unknown> }>;
  socToolDecls: Array<{ name: string; description: string; parameters: Record<string, unknown> }>;
  gitToolDecls: Array<{ name: string; description: string; parameters: Record<string, unknown> }>;
  connectorToolDecls: Array<{ name: string; description: string; parameters: unknown }>;
  mcpToolDecls: Array<{ name: string; description: string; parameters: unknown }>;
}

export function buildFunctionDeclarations(opts: BuildFunctionDeclarationsOptions) {
  if (opts.onboardingMode) {
    return [...opts.mappedExternalTools];
  }

  return [
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
    ...MULTIMEDIA_TOOLS,
    ...CALL_TOOLS,
    ...MEETING_INTEL_TOOLS,
    ...SCHEDULER_TOOLS,
    ...CALENDAR_TOOLS,
    COMMUNICATIONS_TOOL,
    ...opts.browserToolDecls,
    ...opts.socToolDecls,
    ...opts.gitToolDecls,
    ...opts.connectorToolDecls,
    ...opts.mcpToolDecls,
    ...opts.mappedExternalTools,
  ];
}
