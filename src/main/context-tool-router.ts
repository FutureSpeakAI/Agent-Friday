/**
 * Track III, Phase 3: Context-Aware Tool Routing
 *
 * The Executive Function. Maps work context from the Context Graph to
 * relevant tools, producing ranked tool suggestions and routing hints
 * for Gemini's system prompt. This is the bridge between "what is the
 * user doing?" (Context Graph) and "which tools should be ready?"
 *
 * Architecture:
 *   ContextStream → ContextGraph → ToolRouter → Prompt Injection
 *
 * The router does NOT filter tools away from Gemini — all tools remain
 * available. Instead, it produces a ranked "suggested tools" section
 * that biases Gemini toward contextually relevant tools.
 *
 * cLaw Gate: Read-only over context graph + tool registry. No new
 * data generated. No persistence. No tool execution.
 */

import { contextGraph, type EntityType, type WorkStream, type EntityRef } from './context-graph';

// ── Types ────────────────────────────────────────────────────────────

export type ToolCategory =
  | 'code'           // Coding, debugging, file operations
  | 'communication'  // Email, messaging, drafting
  | 'research'       // Web search, document reading, intelligence
  | 'system'         // Desktop control, clipboard, OS operations
  | 'meeting'        // Calendar, meeting intelligence, calls
  | 'memory'         // Memory save/search, episodes
  | 'project'        // Git, project watching, code review
  | 'automation'     // Screen control, browser automation
  | 'creative'       // Document creation, writing
  | 'trust'          // Trust graph, person lookup
  | 'general';       // Catch-all

export interface ToolProfile {
  name: string;
  category: ToolCategory;
  keywords: string[];           // Trigger words that boost relevance
  entityAffinities: EntityType[];  // Entity types that boost this tool
  taskAffinities: string[];     // Inferred tasks that boost this tool
  basePriority: number;         // 0-1 base importance
}

export interface ToolSuggestion {
  toolName: string;
  category: ToolCategory;
  score: number;                // 0-1 relevance score
  reason: string;               // Why this tool is suggested
}

export interface ToolRoutingSnapshot {
  suggestedTools: ToolSuggestion[];
  activeCategory: ToolCategory;
  contextSummary: string;
  totalToolsScored: number;
}

export interface ToolRoutingConfig {
  maxSuggestions: number;        // Max tools to suggest (default: 8)
  minScore: number;              // Minimum score to include (default: 0.2)
  boostActiveEntities: number;   // Boost for tools matching active entities (default: 0.3)
  boostActiveTask: number;       // Boost for tools matching active task (default: 0.25)
  boostRecentTool: number;       // Boost for recently used tools (default: 0.15)
}

export interface ToolRoutingStatus {
  totalProfiles: number;
  activeCategoryScores: Record<string, number>;
  lastRoutingTimestamp: number;
  suggestedToolCount: number;
}

// ── Constants ────────────────────────────────────────────────────────

const DEFAULT_CONFIG: ToolRoutingConfig = {
  maxSuggestions: 8,
  minScore: 0.2,
  boostActiveEntities: 0.3,
  boostActiveTask: 0.25,
  boostRecentTool: 0.15,
};

/**
 * Static tool profiles — maps known tools to categories, keywords,
 * and entity/task affinities. This is the router's knowledge base.
 */
const TOOL_PROFILES: ToolProfile[] = [
  // ── Code Tools ─────────────────────────────────────────
  {
    name: 'ask_claude',
    category: 'code',
    keywords: ['analyze', 'debug', 'explain', 'refactor', 'review', 'architecture', 'complex'],
    entityAffinities: ['file', 'project', 'tool'],
    taskAffinities: ['coding', 'debugging', 'reviewing'],
    basePriority: 0.8,
  },
  {
    name: 'read_own_source',
    category: 'code',
    keywords: ['source', 'code', 'read', 'inspect', 'self'],
    entityAffinities: ['file', 'project'],
    taskAffinities: ['coding', 'debugging', 'self-improvement'],
    basePriority: 0.5,
  },
  {
    name: 'list_own_files',
    category: 'code',
    keywords: ['files', 'directory', 'structure', 'list'],
    entityAffinities: ['file', 'project'],
    taskAffinities: ['coding', 'exploring'],
    basePriority: 0.4,
  },
  {
    name: 'propose_code_change',
    category: 'code',
    keywords: ['edit', 'change', 'fix', 'improve', 'modify', 'update'],
    entityAffinities: ['file', 'project'],
    taskAffinities: ['coding', 'debugging', 'self-improvement'],
    basePriority: 0.6,
  },
  {
    name: 'read_file',
    category: 'code',
    keywords: ['read', 'open', 'view', 'file', 'content'],
    entityAffinities: ['file'],
    taskAffinities: ['coding', 'reading', 'reviewing'],
    basePriority: 0.5,
  },
  {
    name: 'write_file',
    category: 'code',
    keywords: ['write', 'save', 'create', 'file'],
    entityAffinities: ['file'],
    taskAffinities: ['coding', 'writing'],
    basePriority: 0.5,
  },
  {
    name: 'list_directory',
    category: 'code',
    keywords: ['directory', 'folder', 'list', 'browse'],
    entityAffinities: ['file', 'project'],
    taskAffinities: ['coding', 'exploring'],
    basePriority: 0.3,
  },
  {
    name: 'run_command',
    category: 'code',
    keywords: ['command', 'terminal', 'shell', 'execute', 'run', 'script'],
    entityAffinities: ['tool', 'project'],
    taskAffinities: ['coding', 'debugging', 'deploying'],
    basePriority: 0.6,
  },

  // ── Git/Project Tools ──────────────────────────────────
  {
    name: 'git_load_repo',
    category: 'project',
    keywords: ['git', 'repository', 'repo', 'clone', 'load'],
    entityAffinities: ['project', 'url'],
    taskAffinities: ['coding', 'reviewing'],
    basePriority: 0.5,
  },
  {
    name: 'git_get_tree',
    category: 'project',
    keywords: ['tree', 'structure', 'git', 'repo'],
    entityAffinities: ['project', 'file'],
    taskAffinities: ['coding', 'exploring'],
    basePriority: 0.4,
  },
  {
    name: 'git_get_file',
    category: 'project',
    keywords: ['file', 'git', 'source', 'read'],
    entityAffinities: ['file', 'project'],
    taskAffinities: ['coding', 'reviewing'],
    basePriority: 0.5,
  },
  {
    name: 'git_search',
    category: 'project',
    keywords: ['search', 'find', 'grep', 'git'],
    entityAffinities: ['file', 'project', 'topic'],
    taskAffinities: ['coding', 'debugging', 'exploring'],
    basePriority: 0.5,
  },
  {
    name: 'watch_project',
    category: 'project',
    keywords: ['watch', 'monitor', 'project', 'track'],
    entityAffinities: ['project'],
    taskAffinities: ['coding', 'managing'],
    basePriority: 0.4,
  },
  {
    name: 'get_project_context',
    category: 'project',
    keywords: ['project', 'context', 'status', 'overview'],
    entityAffinities: ['project'],
    taskAffinities: ['coding', 'managing', 'reviewing'],
    basePriority: 0.5,
  },

  // ── Communication Tools ────────────────────────────────
  {
    name: 'draft_communication',
    category: 'communication',
    keywords: ['email', 'message', 'draft', 'write', 'compose', 'reply', 'send'],
    entityAffinities: ['person', 'channel'],
    taskAffinities: ['communicating', 'emailing', 'messaging'],
    basePriority: 0.7,
  },

  // ── Research Tools ─────────────────────────────────────
  {
    name: 'read_document',
    category: 'research',
    keywords: ['document', 'read', 'pdf', 'article', 'paper'],
    entityAffinities: ['file', 'url', 'topic'],
    taskAffinities: ['reading', 'researching', 'studying'],
    basePriority: 0.5,
  },
  {
    name: 'search_documents',
    category: 'research',
    keywords: ['search', 'find', 'document', 'look'],
    entityAffinities: ['file', 'topic'],
    taskAffinities: ['researching', 'exploring'],
    basePriority: 0.5,
  },
  {
    name: 'setup_intelligence',
    category: 'research',
    keywords: ['intelligence', 'briefing', 'monitor', 'watch', 'research'],
    entityAffinities: ['topic', 'person'],
    taskAffinities: ['researching', 'monitoring', 'planning'],
    basePriority: 0.4,
  },
  {
    name: 'spawn_agent',
    category: 'research',
    keywords: ['agent', 'research', 'summarize', 'review', 'background'],
    entityAffinities: ['topic', 'url', 'file'],
    taskAffinities: ['researching', 'reviewing', 'summarizing'],
    basePriority: 0.5,
  },
  {
    name: 'check_agent',
    category: 'research',
    keywords: ['agent', 'status', 'check', 'result'],
    entityAffinities: [],
    taskAffinities: ['researching'],
    basePriority: 0.3,
  },

  // ── Meeting Tools ──────────────────────────────────────
  {
    name: 'get_calendar',
    category: 'meeting',
    keywords: ['calendar', 'schedule', 'event', 'meeting', 'appointment'],
    entityAffinities: ['person'],
    taskAffinities: ['scheduling', 'planning', 'managing'],
    basePriority: 0.5,
  },
  {
    name: 'create_calendar_event',
    category: 'meeting',
    keywords: ['create', 'schedule', 'event', 'meeting', 'book'],
    entityAffinities: ['person'],
    taskAffinities: ['scheduling', 'planning'],
    basePriority: 0.5,
  },
  {
    name: 'join_meeting',
    category: 'meeting',
    keywords: ['join', 'meeting', 'call', 'audio'],
    entityAffinities: ['person', 'channel'],
    taskAffinities: ['meeting', 'collaborating'],
    basePriority: 0.6,
  },
  {
    name: 'leave_meeting',
    category: 'meeting',
    keywords: ['leave', 'end', 'meeting', 'call'],
    entityAffinities: [],
    taskAffinities: ['meeting'],
    basePriority: 0.3,
  },
  {
    name: 'create_meeting',
    category: 'meeting',
    keywords: ['meeting', 'start', 'create', 'intelligence'],
    entityAffinities: ['person'],
    taskAffinities: ['meeting', 'collaborating'],
    basePriority: 0.5,
  },
  {
    name: 'meeting_note',
    category: 'meeting',
    keywords: ['note', 'meeting', 'record', 'capture'],
    entityAffinities: ['person', 'topic'],
    taskAffinities: ['meeting', 'note-taking'],
    basePriority: 0.5,
  },
  {
    name: 'end_current_meeting',
    category: 'meeting',
    keywords: ['end', 'finish', 'meeting'],
    entityAffinities: [],
    taskAffinities: ['meeting'],
    basePriority: 0.3,
  },
  {
    name: 'get_meeting_history',
    category: 'meeting',
    keywords: ['history', 'past', 'meetings', 'previous'],
    entityAffinities: ['person'],
    taskAffinities: ['meeting', 'reviewing'],
    basePriority: 0.4,
  },

  // ── Memory Tools ───────────────────────────────────────
  {
    name: 'save_memory',
    category: 'memory',
    keywords: ['remember', 'save', 'memory', 'note', 'store'],
    entityAffinities: ['person', 'topic'],
    taskAffinities: ['remembering', 'learning'],
    basePriority: 0.6,
  },
  {
    name: 'search_episodes',
    category: 'memory',
    keywords: ['search', 'find', 'remember', 'past', 'conversation', 'episode'],
    entityAffinities: ['topic', 'person'],
    taskAffinities: ['remembering', 'researching'],
    basePriority: 0.5,
  },

  // ── Trust Tools ────────────────────────────────────────
  {
    name: 'update_trust',
    category: 'trust',
    keywords: ['trust', 'person', 'reliable', 'credibility', 'evidence'],
    entityAffinities: ['person'],
    taskAffinities: ['communicating', 'evaluating'],
    basePriority: 0.4,
  },
  {
    name: 'lookup_person',
    category: 'trust',
    keywords: ['person', 'who', 'lookup', 'profile', 'know'],
    entityAffinities: ['person'],
    taskAffinities: ['communicating', 'meeting', 'researching'],
    basePriority: 0.5,
  },
  {
    name: 'note_interaction',
    category: 'trust',
    keywords: ['interaction', 'communication', 'talked', 'met'],
    entityAffinities: ['person', 'channel'],
    taskAffinities: ['communicating'],
    basePriority: 0.3,
  },

  // ── System / Desktop Tools ─────────────────────────────
  {
    name: 'launch_app',
    category: 'system',
    keywords: ['launch', 'open', 'start', 'app', 'application'],
    entityAffinities: ['app'],
    taskAffinities: ['managing', 'switching'],
    basePriority: 0.4,
  },
  {
    name: 'focus_window',
    category: 'system',
    keywords: ['focus', 'switch', 'window', 'bring'],
    entityAffinities: ['app'],
    taskAffinities: ['managing', 'switching'],
    basePriority: 0.3,
  },
  {
    name: 'read_clipboard',
    category: 'system',
    keywords: ['clipboard', 'paste', 'copy', 'read'],
    entityAffinities: [],
    taskAffinities: ['coding', 'writing'],
    basePriority: 0.3,
  },
  {
    name: 'write_clipboard',
    category: 'system',
    keywords: ['clipboard', 'copy', 'write'],
    entityAffinities: [],
    taskAffinities: ['coding', 'writing'],
    basePriority: 0.3,
  },
  {
    name: 'read_screen',
    category: 'automation',
    keywords: ['screen', 'screenshot', 'capture', 'see', 'look'],
    entityAffinities: ['app'],
    taskAffinities: ['debugging', 'automating'],
    basePriority: 0.4,
  },

  // ── Automation Tools ───────────────────────────────────
  {
    name: 'mouse_click',
    category: 'automation',
    keywords: ['click', 'mouse', 'press', 'button'],
    entityAffinities: ['app'],
    taskAffinities: ['automating'],
    basePriority: 0.3,
  },
  {
    name: 'type_text',
    category: 'automation',
    keywords: ['type', 'text', 'input', 'keyboard'],
    entityAffinities: ['app'],
    taskAffinities: ['automating', 'writing'],
    basePriority: 0.3,
  },
  {
    name: 'send_keys',
    category: 'automation',
    keywords: ['keys', 'keyboard', 'shortcut', 'hotkey'],
    entityAffinities: ['app'],
    taskAffinities: ['automating'],
    basePriority: 0.3,
  },

  // ── Task / Scheduling Tools ────────────────────────────
  {
    name: 'create_task',
    category: 'meeting',
    keywords: ['task', 'todo', 'reminder', 'schedule', 'create'],
    entityAffinities: ['topic'],
    taskAffinities: ['planning', 'managing', 'scheduling'],
    basePriority: 0.4,
  },
  {
    name: 'list_tasks',
    category: 'meeting',
    keywords: ['tasks', 'todos', 'list', 'pending'],
    entityAffinities: [],
    taskAffinities: ['planning', 'managing'],
    basePriority: 0.3,
  },

  // ── Webcam / Hardware ──────────────────────────────────
  {
    name: 'enable_webcam',
    category: 'system',
    keywords: ['webcam', 'camera', 'video', 'see'],
    entityAffinities: [],
    taskAffinities: ['meeting', 'showing'],
    basePriority: 0.3,
  },
];

// Task → Category mapping for work stream inference
const TASK_CATEGORY_MAP: Record<string, ToolCategory[]> = {
  coding: ['code', 'project'],
  debugging: ['code', 'project', 'system'],
  reviewing: ['code', 'project', 'research'],
  browsing: ['research', 'general'],
  communicating: ['communication', 'trust', 'meeting'],
  emailing: ['communication', 'trust'],
  messaging: ['communication', 'trust'],
  meeting: ['meeting', 'trust', 'memory'],
  researching: ['research', 'memory'],
  writing: ['creative', 'communication'],
  reading: ['research'],
  planning: ['meeting', 'memory'],
  managing: ['system', 'meeting'],
  scheduling: ['meeting'],
  exploring: ['research', 'code', 'project'],
  deploying: ['code', 'system'],
  automating: ['automation', 'system'],
  designing: ['creative', 'automation'],
};

// ── ContextToolRouter Class ──────────────────────────────────────────

export class ContextToolRouter {
  private config: ToolRoutingConfig;
  private profiles: Map<string, ToolProfile>;
  private dynamicProfiles: Map<string, ToolProfile> = new Map();
  private lastRoutingTimestamp = 0;
  private cachedSuggestions: ToolSuggestion[] = [];
  private cacheValidMs = 5000; // Re-route every 5 seconds max

  constructor(config?: Partial<ToolRoutingConfig>) {
    this.config = { ...DEFAULT_CONFIG, ...config };
    this.profiles = new Map();

    // Index static profiles
    for (const profile of TOOL_PROFILES) {
      this.profiles.set(profile.name, profile);
    }
  }

  // ── Dynamic Tool Registration ──────────────────────────────────────

  /**
   * Register a dynamically-loaded tool (connectors, MCP, superpowers).
   * Called when tool lists are loaded at runtime.
   */
  registerTool(
    name: string,
    category: ToolCategory = 'general',
    keywords: string[] = [],
    entityAffinities: EntityType[] = [],
    taskAffinities: string[] = [],
  ): void {
    if (this.profiles.has(name)) return; // Don't override static profiles

    const profile: ToolProfile = {
      name,
      category,
      keywords,
      entityAffinities,
      taskAffinities,
      basePriority: 0.3, // Dynamic tools start lower
    };

    this.dynamicProfiles.set(name, profile);
  }

  /**
   * Bulk register tools with auto-categorization from description.
   * Used for connector/MCP tools that come with descriptions.
   */
  registerToolsFromDeclarations(
    tools: Array<{ name: string; description?: string }>,
  ): void {
    for (const tool of tools) {
      if (this.profiles.has(tool.name) || this.dynamicProfiles.has(tool.name)) {
        continue;
      }
      const category = this.inferCategory(tool.name, tool.description || '');
      const keywords = this.extractKeywords(tool.description || '');
      this.registerTool(tool.name, category, keywords);
    }
  }

  /**
   * Remove dynamically-registered tools (e.g., when a connector is unloaded).
   */
  unregisterTool(name: string): void {
    this.dynamicProfiles.delete(name);
  }

  // ── Core Routing ───────────────────────────────────────────────────

  /**
   * Score and rank tools based on current context graph state.
   * Returns top suggestions with relevance scores and reasons.
   */
  route(): ToolSuggestion[] {
    const now = Date.now();

    // Use cache if fresh enough
    if (now - this.lastRoutingTimestamp < this.cacheValidMs && this.cachedSuggestions.length > 0) {
      return this.cachedSuggestions;
    }

    const suggestions: ToolSuggestion[] = [];

    // Get context from the graph
    const activeStream = contextGraph.getActiveStream();
    const activeEntities = contextGraph.getActiveEntities(5 * 60 * 1000);
    const topEntities = contextGraph.getTopEntities(15);
    const recentStreams = contextGraph.getRecentStreams(3);

    // Determine active categories from task
    const activeCategories = this.inferActiveCategories(activeStream, recentStreams);

    // Score all known profiles
    const allProfiles = [
      ...this.profiles.values(),
      ...this.dynamicProfiles.values(),
    ];

    for (const profile of allProfiles) {
      const { score, reason } = this.scoreProfile(
        profile,
        activeStream,
        activeEntities,
        topEntities,
        activeCategories,
      );

      if (score >= this.config.minScore) {
        suggestions.push({
          toolName: profile.name,
          category: profile.category,
          score,
          reason,
        });
      }
    }

    // Sort by score descending, take top N
    suggestions.sort((a, b) => b.score - a.score);
    const result = suggestions.slice(0, this.config.maxSuggestions);

    // Cache results
    this.cachedSuggestions = result;
    this.lastRoutingTimestamp = now;

    return result;
  }

  /**
   * Get the primary tool category for the current context.
   */
  getActiveCategory(): ToolCategory {
    const activeStream = contextGraph.getActiveStream();
    if (!activeStream) return 'general';

    const categories = TASK_CATEGORY_MAP[activeStream.task];
    return categories?.[0] ?? 'general';
  }

  /**
   * Get category relevance scores for the current context.
   */
  getCategoryScores(): Record<ToolCategory, number> {
    const scores: Record<string, number> = {};
    const allCategories: ToolCategory[] = [
      'code', 'communication', 'research', 'system', 'meeting',
      'memory', 'project', 'automation', 'creative', 'trust', 'general',
    ];

    const activeStream = contextGraph.getActiveStream();
    const activeEntities = contextGraph.getActiveEntities(5 * 60 * 1000);

    for (const cat of allCategories) {
      scores[cat] = this.scoreCategoryRelevance(cat, activeStream, activeEntities);
    }

    return scores as Record<ToolCategory, number>;
  }

  // ── Context Generation ─────────────────────────────────────────────

  /**
   * Full markdown context for system prompt injection.
   * Lists suggested tools with relevance reasons.
   */
  getContextString(): string {
    const suggestions = this.route();
    if (suggestions.length === 0) return '';

    const lines: string[] = ['## Tool Suggestions'];
    lines.push(`Context: ${this.getActiveCategory()} workflow`);
    lines.push('');

    for (const s of suggestions) {
      const pct = Math.round(s.score * 100);
      lines.push(`- **${s.toolName}** (${pct}% relevant) — ${s.reason}`);
    }

    return lines.join('\n');
  }

  /**
   * Shorter budget-aware prompt context.
   * Single line of prioritized tool names.
   */
  getPromptContext(): string {
    const suggestions = this.route();
    if (suggestions.length === 0) return '';

    const category = this.getActiveCategory();
    const toolNames = suggestions.slice(0, 5).map(s => s.toolName);

    return `[TOOLS] ${category} mode | prefer: ${toolNames.join(', ')}`;
  }

  // ── Snapshot & Status ──────────────────────────────────────────────

  getSnapshot(): ToolRoutingSnapshot {
    const suggestions = this.route();
    const activeStream = contextGraph.getActiveStream();

    return {
      suggestedTools: suggestions,
      activeCategory: this.getActiveCategory(),
      contextSummary: activeStream
        ? `${activeStream.task} in ${activeStream.app}`
        : 'No active context',
      totalToolsScored: this.profiles.size + this.dynamicProfiles.size,
    };
  }

  getStatus(): ToolRoutingStatus {
    return {
      totalProfiles: this.profiles.size + this.dynamicProfiles.size,
      activeCategoryScores: this.getCategoryScores(),
      lastRoutingTimestamp: this.lastRoutingTimestamp,
      suggestedToolCount: this.cachedSuggestions.length,
    };
  }

  getConfig(): ToolRoutingConfig {
    return { ...this.config };
  }

  // ── Private Scoring ────────────────────────────────────────────────

  private scoreProfile(
    profile: ToolProfile,
    activeStream: WorkStream | null,
    activeEntities: EntityRef[],
    topEntities: EntityRef[],
    activeCategories: Set<ToolCategory>,
  ): { score: number; reason: string } {
    let score = profile.basePriority * 0.3; // Base contributes 30%
    const reasons: string[] = [];

    // 1. Category match — is this tool's category relevant?
    if (activeCategories.has(profile.category)) {
      score += 0.25;
      reasons.push(`${profile.category} workflow`);
    }

    // 2. Task affinity — does the active task match?
    if (activeStream && profile.taskAffinities.includes(activeStream.task)) {
      score += this.config.boostActiveTask;
      reasons.push(`task: ${activeStream.task}`);
    }

    // 3. Entity affinity — do active entities match tool affinities?
    let entityBoost = 0;
    const matchedEntityTypes = new Set<string>();
    for (const entity of activeEntities) {
      if (profile.entityAffinities.includes(entity.type)) {
        entityBoost += 0.05; // Each matching entity adds a small boost
        matchedEntityTypes.add(entity.type);
      }
    }
    entityBoost = Math.min(this.config.boostActiveEntities, entityBoost);
    if (entityBoost > 0) {
      score += entityBoost;
      reasons.push(`entities: ${Array.from(matchedEntityTypes).join(', ')}`);
    }

    // 4. Recent tool usage — was this tool used recently?
    const recentTools = activeEntities
      .filter(e => e.type === 'tool')
      .map(e => e.value);
    if (recentTools.includes(profile.name)) {
      score += this.config.boostRecentTool;
      reasons.push('recently used');
    }

    // 5. Top entity cross-reference — do top entities match?
    for (const entity of topEntities.slice(0, 5)) {
      if (profile.entityAffinities.includes(entity.type)) {
        score += 0.02;
      }
    }

    // Clamp to [0, 1]
    score = Math.max(0, Math.min(1, score));

    const reason = reasons.length > 0
      ? reasons.join('; ')
      : profile.category;

    return { score, reason };
  }

  private inferActiveCategories(
    activeStream: WorkStream | null,
    recentStreams: WorkStream[],
  ): Set<ToolCategory> {
    const categories = new Set<ToolCategory>();
    categories.add('general'); // Always include general

    if (activeStream) {
      const mapped = TASK_CATEGORY_MAP[activeStream.task];
      if (mapped) {
        for (const cat of mapped) categories.add(cat);
      }
    }

    // Also include categories from recent streams (lower weight handled in scoring)
    for (const stream of recentStreams.slice(0, 2)) {
      const mapped = TASK_CATEGORY_MAP[stream.task];
      if (mapped && mapped[0]) {
        categories.add(mapped[0]); // Only primary category from recent
      }
    }

    return categories;
  }

  private scoreCategoryRelevance(
    category: ToolCategory,
    activeStream: WorkStream | null,
    activeEntities: EntityRef[],
  ): number {
    let score = 0;

    // Task match
    if (activeStream) {
      const mapped = TASK_CATEGORY_MAP[activeStream.task];
      if (mapped) {
        const idx = mapped.indexOf(category);
        if (idx === 0) score += 0.5;       // Primary category
        else if (idx > 0) score += 0.25;   // Secondary category
      }
    }

    // Entity affinity
    const profilesInCategory = [...this.profiles.values(), ...this.dynamicProfiles.values()]
      .filter(p => p.category === category);

    let entityMatch = 0;
    for (const entity of activeEntities.slice(0, 10)) {
      for (const profile of profilesInCategory) {
        if (profile.entityAffinities.includes(entity.type)) {
          entityMatch += 0.03;
        }
      }
    }
    score += Math.min(0.3, entityMatch);

    return Math.min(1, score);
  }

  private inferCategory(name: string, description: string): ToolCategory {
    const text = `${name} ${description}`.toLowerCase();

    const categoryKeywords: Record<ToolCategory, string[]> = {
      code: ['code', 'file', 'edit', 'compile', 'debug', 'lint', 'format', 'syntax'],
      communication: ['email', 'message', 'chat', 'send', 'draft', 'slack', 'teams'],
      research: ['search', 'find', 'web', 'browse', 'fetch', 'scrape', 'crawl', 'read'],
      system: ['system', 'os', 'desktop', 'window', 'clipboard', 'volume', 'launch'],
      meeting: ['calendar', 'meeting', 'schedule', 'event', 'appointment', 'task', 'todo'],
      memory: ['memory', 'remember', 'store', 'recall', 'history'],
      project: ['git', 'repo', 'branch', 'commit', 'project', 'version'],
      automation: ['automate', 'click', 'mouse', 'keyboard', 'screenshot', 'screen'],
      creative: ['create', 'generate', 'design', 'write', 'compose', 'document'],
      trust: ['trust', 'person', 'credibility', 'relationship'],
      general: [],
    };

    let bestCategory: ToolCategory = 'general';
    let bestScore = 0;

    for (const [cat, keywords] of Object.entries(categoryKeywords)) {
      let hits = 0;
      for (const kw of keywords) {
        if (text.includes(kw)) hits++;
      }
      if (hits > bestScore) {
        bestScore = hits;
        bestCategory = cat as ToolCategory;
      }
    }

    return bestCategory;
  }

  private extractKeywords(description: string): string[] {
    if (!description) return [];
    return description
      .toLowerCase()
      .replace(/[^a-z0-9\s-]/g, ' ')
      .split(/\s+/)
      .filter(w => w.length > 3 && w.length < 20)
      .slice(0, 8);
  }
}

// ── Singleton ──────────────────────────────────────────────────────

export const contextToolRouter = new ContextToolRouter();
