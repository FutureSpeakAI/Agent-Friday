/**
 * Workflow Recorder — Track V Phase 1: Workflow Learning.
 *
 * Records user actions (app switches, clipboard events, timing) into
 * structured workflow recordings.  A post-recording analysis phase
 * abstracts raw events into a parameterised WorkflowTemplate that can
 * be replayed (Phase 2) with different inputs.
 *
 * ┌─────────────────────────────────────────────────────────┐
 * │  cLaw Gate                                              │
 * │  • Recording requires EXPLICIT user activation.         │
 * │  • A persistent visual indicator runs for the duration. │
 * │  • No screen content is transmitted externally — all    │
 * │    data stays local in {userData}/workflows/.           │
 * │  • Templates are inert data objects — replay (Phase 2)  │
 * │    requires separate user approval per execution.       │
 * └─────────────────────────────────────────────────────────┘
 */

import { app } from 'electron';
import fs from 'fs/promises';
import path from 'path';
import crypto from 'crypto';

// ── Data Model ──────────────────────────────────────────────────────

export type RecordingStatus = 'idle' | 'recording' | 'analysing';

/** A single observed action during recording. */
export interface RecordedEvent {
  id: string;
  timestamp: number;
  type: EventType;
  /** Human-readable description of what happened. */
  description: string;
  /** Application that was active when the event fired. */
  activeApp: string;
  /** Window title at event time. */
  windowTitle: string;
  /** Optional payload (clipboard text, URL, etc.) — truncated to 500 chars. */
  payload?: string;
  /** Duration in ms if the event represents a period (e.g. typing). */
  durationMs?: number;
}

export type EventType =
  | 'app_switch'
  | 'clipboard_copy'
  | 'clipboard_paste'
  | 'url_navigation'
  | 'typing_burst'
  | 'idle_gap'
  | 'screenshot'
  | 'user_annotation'
  | 'custom';

/** A complete raw recording session. */
export interface WorkflowRecording {
  id: string;
  name: string;
  description: string;
  startedAt: number;
  endedAt: number;
  events: RecordedEvent[];
  /** Screen-capture key-frames (base64 JPEG, max 20). */
  keyFrames: KeyFrame[];
  status: 'complete' | 'cancelled';
  metadata: RecordingMetadata;
}

export interface KeyFrame {
  timestamp: number;
  /** base64-encoded JPEG — NOT persisted to main JSON, stored as separate files. */
  filePath: string;
  /** Which app was in focus. */
  activeApp: string;
}

export interface RecordingMetadata {
  durationMs: number;
  eventCount: number;
  appsUsed: string[];
  clipboardOps: number;
  annotationCount: number;
}

// ── Workflow Template (abstracted from recording) ───────────────────

export interface WorkflowTemplate {
  id: string;
  name: string;
  description: string;
  /** Source recording this was derived from. */
  sourceRecordingId: string;
  createdAt: number;
  updatedAt: number;
  steps: WorkflowStep[];
  parameters: WorkflowParameter[];
  /** Estimated duration in ms based on original recording. */
  estimatedDurationMs: number;
  /** Tags for categorisation. */
  tags: string[];
}

export interface WorkflowStep {
  id: string;
  order: number;
  /** Intent of the step (what the user was trying to achieve). */
  intent: string;
  /** Mechanical method recorded. */
  method: string;
  /** Application required for this step. */
  targetApp: string;
  /** Parameters referenced by this step (by parameter id). */
  parameterRefs: string[];
  /** Whether this step is a decision point (not mechanical). */
  isDecisionPoint: boolean;
  /** Duration in the original recording. */
  originalDurationMs: number;
  /** Verification hint: how to confirm the step succeeded. */
  verificationHint?: string;
}

export interface WorkflowParameter {
  id: string;
  name: string;
  description: string;
  /** The value used in the original recording. */
  defaultValue: string;
  /** How this parameter was detected. */
  source: 'user_annotated' | 'inferred';
  /** Data type hint. */
  dataType: 'text' | 'url' | 'email' | 'date' | 'number' | 'filepath';
}

// ── Configuration ───────────────────────────────────────────────────

export interface WorkflowRecorderConfig {
  /** Maximum recording duration in ms (default: 30 min). */
  maxRecordingDurationMs: number;
  /** Interval for app-switch polling during recording (ms). */
  pollIntervalMs: number;
  /** Maximum events per recording. */
  maxEvents: number;
  /** Maximum key-frames to capture. */
  maxKeyFrames: number;
  /** Maximum stored recordings. */
  maxRecordings: number;
  /** Maximum stored templates. */
  maxTemplates: number;
  /** Idle gap threshold (ms) — gaps longer than this become idle_gap events. */
  idleGapThresholdMs: number;
}

const DEFAULT_CONFIG: WorkflowRecorderConfig = {
  maxRecordingDurationMs: 30 * 60 * 1000,
  pollIntervalMs: 5_000,
  maxEvents: 500,
  maxKeyFrames: 20,
  maxRecordings: 50,
  maxTemplates: 100,
  idleGapThresholdMs: 30_000,
};

// ── Engine ──────────────────────────────────────────────────────────

class WorkflowRecorderEngine {
  private recordings: WorkflowRecording[] = [];
  private templates: WorkflowTemplate[] = [];
  private config: WorkflowRecorderConfig = { ...DEFAULT_CONFIG };
  private dataDir = '';
  private initialized = false;
  private saveQueued = false;

  // ── Recording state ──
  private status: RecordingStatus = 'idle';
  private currentRecording: Partial<WorkflowRecording> | null = null;
  private currentEvents: RecordedEvent[] = [];
  private currentKeyFrames: KeyFrame[] = [];
  private pollTimer: ReturnType<typeof setInterval> | null = null;
  private maxDurationTimer: ReturnType<typeof setTimeout> | null = null;
  private lastAppName = '';
  private lastWindowTitle = '';
  private lastEventTime = 0;
  private recordingName = '';

  // ── Lifecycle ─────────────────────────────────────────────────────

  async initialize(config?: Partial<WorkflowRecorderConfig>): Promise<void> {
    this.config = { ...DEFAULT_CONFIG, ...config };
    this.dataDir = path.join(app.getPath('userData'), 'workflows');

    try {
      await fs.mkdir(this.dataDir, { recursive: true });
    } catch { /* directory exists */ }

    // Load recordings index
    try {
      const raw = await fs.readFile(
        path.join(this.dataDir, 'recordings.json'),
        'utf-8'
      );
      const data = JSON.parse(raw);
      this.recordings = Array.isArray(data.recordings) ? data.recordings : [];
    } catch {
      this.recordings = [];
    }

    // Load templates
    try {
      const raw = await fs.readFile(
        path.join(this.dataDir, 'templates.json'),
        'utf-8'
      );
      const data = JSON.parse(raw);
      this.templates = Array.isArray(data.templates) ? data.templates : [];
    } catch {
      this.templates = [];
    }

    this.pruneRecordings();
    this.pruneTemplates();
    this.initialized = true;

    console.log(
      `[WorkflowRecorder] Initialized: ${this.recordings.length} recordings, ` +
      `${this.templates.length} templates`
    );
  }

  // ── Recording Control ─────────────────────────────────────────────

  /**
   * Start recording a new workflow.
   * cLaw Gate: requires explicit user activation (enforced at IPC layer).
   */
  startRecording(name: string): {
    success: boolean;
    recordingId?: string;
    error?: string;
  } {
    if (this.status !== 'idle') {
      return { success: false, error: `Cannot start: currently ${this.status}` };
    }
    if (!name || typeof name !== 'string') {
      return { success: false, error: 'Recording name is required' };
    }

    const id = crypto.randomUUID().slice(0, 12);
    this.currentRecording = {
      id,
      name: name.slice(0, 100),
      description: '',
      startedAt: Date.now(),
      endedAt: 0,
      events: [],
      keyFrames: [],
      status: 'complete',
      metadata: {
        durationMs: 0,
        eventCount: 0,
        appsUsed: [],
        clipboardOps: 0,
        annotationCount: 0,
      },
    };
    this.currentEvents = [];
    this.currentKeyFrames = [];
    this.recordingName = name.slice(0, 100);
    this.lastEventTime = Date.now();
    this.status = 'recording';

    // Start polling for app switches
    this.pollTimer = setInterval(() => this.pollAppContext(), this.config.pollIntervalMs);

    // Safety: auto-stop after max duration
    this.maxDurationTimer = setTimeout(() => {
      if (this.status === 'recording') {
        this.stopRecording();
      }
    }, this.config.maxRecordingDurationMs);

    console.log(`[WorkflowRecorder] Recording started: "${name}" (${id})`);
    return { success: true, recordingId: id };
  }

  /**
   * Stop the current recording and finalise it.
   */
  stopRecording(): WorkflowRecording | null {
    if (this.status !== 'recording' || !this.currentRecording) {
      return null;
    }

    this.clearTimers();

    const now = Date.now();
    const recording: WorkflowRecording = {
      id: this.currentRecording.id!,
      name: this.currentRecording.name!,
      description: this.currentRecording.description || '',
      startedAt: this.currentRecording.startedAt!,
      endedAt: now,
      events: [...this.currentEvents],
      keyFrames: [...this.currentKeyFrames],
      status: 'complete',
      metadata: this.buildMetadata(now),
    };

    this.recordings.push(recording);
    this.pruneRecordings();
    this.queueSave();

    this.status = 'idle';
    this.currentRecording = null;
    this.currentEvents = [];
    this.currentKeyFrames = [];

    console.log(
      `[WorkflowRecorder] Recording stopped: "${recording.name}" — ` +
      `${recording.events.length} events, ` +
      `${((recording.endedAt - recording.startedAt) / 1000).toFixed(0)}s`
    );

    return recording;
  }

  /**
   * Cancel the current recording without saving.
   */
  cancelRecording(): boolean {
    if (this.status !== 'recording') return false;

    this.clearTimers();
    this.status = 'idle';
    this.currentRecording = null;
    this.currentEvents = [];
    this.currentKeyFrames = [];

    console.log('[WorkflowRecorder] Recording cancelled');
    return true;
  }

  // ── Event Capture ─────────────────────────────────────────────────

  /**
   * Record an observed event during an active recording session.
   */
  recordEvent(event: Omit<RecordedEvent, 'id' | 'timestamp'>): boolean {
    if (this.status !== 'recording') return false;
    if (this.currentEvents.length >= this.config.maxEvents) return false;

    const now = Date.now();

    // Insert idle gap event if significant pause detected
    if (
      this.lastEventTime > 0 &&
      now - this.lastEventTime > this.config.idleGapThresholdMs
    ) {
      this.currentEvents.push({
        id: crypto.randomUUID().slice(0, 12),
        timestamp: this.lastEventTime,
        type: 'idle_gap',
        description: `Idle for ${Math.round((now - this.lastEventTime) / 1000)}s`,
        activeApp: event.activeApp || this.lastAppName,
        windowTitle: event.windowTitle || this.lastWindowTitle,
        durationMs: now - this.lastEventTime,
      });
    }

    this.currentEvents.push({
      ...event,
      id: crypto.randomUUID().slice(0, 12),
      timestamp: now,
      payload: event.payload?.slice(0, 500),
    });

    this.lastEventTime = now;
    this.lastAppName = event.activeApp || this.lastAppName;
    this.lastWindowTitle = event.windowTitle || this.lastWindowTitle;

    return true;
  }

  /**
   * Add a user annotation to the recording (parameter hint, note, etc.).
   */
  addAnnotation(text: string): boolean {
    if (this.status !== 'recording') return false;
    return this.recordEvent({
      type: 'user_annotation',
      description: text.slice(0, 500),
      activeApp: this.lastAppName,
      windowTitle: this.lastWindowTitle,
    });
  }

  /**
   * Record a key-frame screenshot reference.
   */
  addKeyFrame(filePath: string, activeApp: string): boolean {
    if (this.status !== 'recording') return false;
    if (this.currentKeyFrames.length >= this.config.maxKeyFrames) return false;

    this.currentKeyFrames.push({
      timestamp: Date.now(),
      filePath,
      activeApp,
    });
    return true;
  }

  // ── Template Creation ─────────────────────────────────────────────

  /**
   * Create a workflow template from a recording.
   * This is the abstraction step: raw events → intent-level steps.
   *
   * In a production system this would use Claude to analyse the recording.
   * For now we use a deterministic heuristic that groups events by app-switch
   * boundaries and identifies clipboard operations as potential parameters.
   */
  createTemplate(
    recordingId: string,
    overrides?: {
      name?: string;
      description?: string;
      parameters?: WorkflowParameter[];
      tags?: string[];
    }
  ): WorkflowTemplate | null {
    const recording = this.recordings.find(r => r.id === recordingId);
    if (!recording) return null;

    const steps = this.abstractSteps(recording);
    const inferredParams = this.inferParameters(recording);
    const userParams = overrides?.parameters || [];

    // Merge: user-annotated params override inferred ones
    const paramMap = new Map<string, WorkflowParameter>();
    for (const p of inferredParams) paramMap.set(p.id, p);
    for (const p of userParams) paramMap.set(p.id, p);
    const parameters = Array.from(paramMap.values());

    const template: WorkflowTemplate = {
      id: crypto.randomUUID().slice(0, 12),
      name: overrides?.name || recording.name,
      description: overrides?.description || recording.description,
      sourceRecordingId: recordingId,
      createdAt: Date.now(),
      updatedAt: Date.now(),
      steps,
      parameters,
      estimatedDurationMs: recording.metadata.durationMs,
      tags: overrides?.tags || [],
    };

    this.templates.push(template);
    this.pruneTemplates();
    this.queueSave();

    console.log(
      `[WorkflowRecorder] Template created: "${template.name}" — ` +
      `${steps.length} steps, ${parameters.length} parameters`
    );

    return template;
  }

  // ── Queries ───────────────────────────────────────────────────────

  getStatus(): {
    status: RecordingStatus;
    currentRecordingId?: string;
    currentEventCount?: number;
    elapsedMs?: number;
  } {
    const base = { status: this.status };
    if (this.status === 'recording' && this.currentRecording) {
      return {
        ...base,
        currentRecordingId: this.currentRecording.id,
        currentEventCount: this.currentEvents.length,
        elapsedMs: Date.now() - (this.currentRecording.startedAt || 0),
      };
    }
    return base;
  }

  getRecording(id: string): WorkflowRecording | null {
    return this.recordings.find(r => r.id === id) || null;
  }

  getAllRecordings(): WorkflowRecording[] {
    return this.recordings.map(r => ({ ...r }));
  }

  getRecentRecordings(limit = 10): WorkflowRecording[] {
    return this.recordings
      .slice(-limit)
      .reverse()
      .map(r => ({ ...r }));
  }

  getTemplate(id: string): WorkflowTemplate | null {
    return this.templates.find(t => t.id === id) || null;
  }

  getAllTemplates(): WorkflowTemplate[] {
    return this.templates.map(t => ({ ...t }));
  }

  getTemplatesByTag(tag: string): WorkflowTemplate[] {
    return this.templates
      .filter(t => t.tags.includes(tag))
      .map(t => ({ ...t }));
  }

  deleteRecording(id: string): boolean {
    const idx = this.recordings.findIndex(r => r.id === id);
    if (idx === -1) return false;
    this.recordings.splice(idx, 1);
    this.queueSave();
    return true;
  }

  deleteTemplate(id: string): boolean {
    const idx = this.templates.findIndex(t => t.id === id);
    if (idx === -1) return false;
    this.templates.splice(idx, 1);
    this.queueSave();
    return true;
  }

  getConfig(): WorkflowRecorderConfig {
    return { ...this.config };
  }

  // ── Internals ─────────────────────────────────────────────────────

  private pollAppContext(): void {
    // In a real implementation, this would call ambient.ts or desktop-tools
    // to get the current active app. During testing, events are pushed
    // externally via recordEvent().
    // This is a hook for Phase 2 integration.
  }

  /**
   * Abstract raw events into intent-level workflow steps.
   * Groups events by app boundaries and collapses mechanical actions.
   */
  private abstractSteps(recording: WorkflowRecording): WorkflowStep[] {
    const events = recording.events;
    if (events.length === 0) return [];

    const steps: WorkflowStep[] = [];
    let currentApp = events[0].activeApp;
    let stepEvents: RecordedEvent[] = [];
    let order = 1;

    const flushStep = () => {
      if (stepEvents.length === 0) return;

      const firstEvent = stepEvents[0];
      const lastEvent = stepEvents[stepEvents.length - 1];
      const hasClipboard = stepEvents.some(
        e => e.type === 'clipboard_copy' || e.type === 'clipboard_paste'
      );
      const hasAnnotation = stepEvents.some(e => e.type === 'user_annotation');
      const isDecision = hasAnnotation || stepEvents.length > 5;

      // Build intent from annotations or event descriptions
      const annotations = stepEvents
        .filter(e => e.type === 'user_annotation')
        .map(e => e.description);
      const intent = annotations.length > 0
        ? annotations.join('; ')
        : `Work in ${currentApp}: ${stepEvents.map(e => e.type).join(', ')}`;

      const method = stepEvents
        .filter(e => e.type !== 'idle_gap' && e.type !== 'user_annotation')
        .map(e => e.description)
        .join(' → ');

      steps.push({
        id: crypto.randomUUID().slice(0, 12),
        order: order++,
        intent,
        method: method.slice(0, 500),
        targetApp: currentApp,
        parameterRefs: hasClipboard ? ['clipboard_data'] : [],
        isDecisionPoint: isDecision,
        originalDurationMs: lastEvent.timestamp - firstEvent.timestamp,
      });
    };

    for (const event of events) {
      if (event.type === 'idle_gap') continue;

      if (event.activeApp !== currentApp && event.type !== 'user_annotation') {
        flushStep();
        currentApp = event.activeApp;
        stepEvents = [];
      }
      stepEvents.push(event);
    }
    flushStep();

    return steps;
  }

  /**
   * Infer potential parameters from the recording.
   * Clipboard contents and user annotations are likely candidates.
   */
  private inferParameters(recording: WorkflowRecording): WorkflowParameter[] {
    const params: WorkflowParameter[] = [];
    const seen = new Set<string>();

    for (const event of recording.events) {
      if (event.type === 'clipboard_copy' && event.payload) {
        const key = `clip_${params.length + 1}`;
        if (!seen.has(event.payload.slice(0, 50))) {
          seen.add(event.payload.slice(0, 50));
          params.push({
            id: key,
            name: `Clipboard ${params.length + 1}`,
            description: `Copied text: "${event.payload.slice(0, 80)}"`,
            defaultValue: event.payload,
            source: 'inferred',
            dataType: this.guessDataType(event.payload),
          });
        }
      }

      if (event.type === 'user_annotation' && event.description.includes('=')) {
        // Simple "name=value" annotation pattern
        const eqIndex = event.description.indexOf('=');
        const name = event.description.slice(0, eqIndex).trim();
        const value = event.description.slice(eqIndex + 1).trim();
        if (name && value) {
          params.push({
            id: `param_${params.length + 1}`,
            name,
            description: `User annotated: ${name}`,
            defaultValue: value,
            source: 'user_annotated',
            dataType: this.guessDataType(value),
          });
        }
      }
    }

    return params;
  }

  private guessDataType(value: string): WorkflowParameter['dataType'] {
    if (/^https?:\/\//i.test(value)) return 'url';
    if (/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) return 'email';
    if (/^\d{4}-\d{2}-\d{2}/.test(value)) return 'date';
    if (/^\d+(\.\d+)?$/.test(value)) return 'number';
    if (/^[A-Z]:\\|^\//.test(value)) return 'filepath';
    return 'text';
  }

  private buildMetadata(endTime: number): RecordingMetadata {
    const appsUsed = [...new Set(this.currentEvents.map(e => e.activeApp).filter(Boolean))];
    const clipboardOps = this.currentEvents.filter(
      e => e.type === 'clipboard_copy' || e.type === 'clipboard_paste'
    ).length;
    const annotationCount = this.currentEvents.filter(
      e => e.type === 'user_annotation'
    ).length;

    return {
      durationMs: endTime - (this.currentRecording?.startedAt || endTime),
      eventCount: this.currentEvents.length,
      appsUsed,
      clipboardOps,
      annotationCount,
    };
  }

  private clearTimers(): void {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
    if (this.maxDurationTimer) {
      clearTimeout(this.maxDurationTimer);
      this.maxDurationTimer = null;
    }
  }

  // ── Persistence ───────────────────────────────────────────────────

  private queueSave(): void {
    if (this.saveQueued) return;
    this.saveQueued = true;
    setTimeout(() => {
      this.saveQueued = false;
      this.save().catch(err =>
        // Crypto Sprint 17: Sanitize error output.
        console.error('[WorkflowRecorder] Save error:', err instanceof Error ? err.message : 'Unknown error')
      );
    }, 2000);
  }

  private async save(): Promise<void> {
    await fs.writeFile(
      path.join(this.dataDir, 'recordings.json'),
      JSON.stringify({ recordings: this.recordings }, null, 2),
      'utf-8'
    );
    await fs.writeFile(
      path.join(this.dataDir, 'templates.json'),
      JSON.stringify({ templates: this.templates }, null, 2),
      'utf-8'
    );
  }

  private pruneRecordings(): void {
    if (this.recordings.length > this.config.maxRecordings) {
      this.recordings = this.recordings.slice(-this.config.maxRecordings);
    }
  }

  private pruneTemplates(): void {
    if (this.templates.length > this.config.maxTemplates) {
      this.templates = this.templates.slice(-this.config.maxTemplates);
    }
  }
}

export const workflowRecorder = new WorkflowRecorderEngine();
