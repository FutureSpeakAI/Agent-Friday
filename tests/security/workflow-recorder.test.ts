/**
 * Tests for workflow-recorder.ts — Track V Phase 1: Workflow Recording.
 * Validates recording lifecycle, event capture (all event types, idle gap
 * detection, annotations, key-frames), template creation (step abstraction,
 * parameter inference, data-type guessing), queries, persistence, pruning,
 * and cLaw Gate compliance.
 *
 * cLaw Gate assertion: Recording MUST be explicitly started by the user.
 * The recorder never activates autonomously. Template creation is a
 * read-only abstraction. No replay or execution occurs here.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ── Mock Electron app ────────────────────────────────────────────────
vi.mock('electron', () => ({
  app: {
    getPath: vi.fn().mockReturnValue('/mock/userData'),
  },
}));

// ── Mock fs/promises ─────────────────────────────────────────────────
const mockReadFile = vi.fn();
const mockWriteFile = vi.fn();
const mockMkdir = vi.fn();
vi.mock('fs/promises', () => ({
  default: {
    readFile: (...args: any[]) => mockReadFile(...args),
    writeFile: (...args: any[]) => mockWriteFile(...args),
    mkdir: (...args: any[]) => mockMkdir(...args),
  },
}));

// ── Mock crypto ──────────────────────────────────────────────────────
let uuidCounter = 0;
vi.mock('crypto', () => ({
  default: {
    randomUUID: () => {
      const n = ++uuidCounter;
      return `${String(n).padStart(8, '0')}-${String(n).padStart(4, '0')}-4000-8000-000000000000`;
    },
  },
}));

// ── Import under test (after mocks) ─────────────────────────────────
import type {
  RecordedEvent,
  EventType,
  WorkflowRecording,
  WorkflowTemplate,
  WorkflowStep,
  WorkflowParameter,
  WorkflowRecorderConfig,
} from '../../src/main/workflow-recorder';

function createEngine() {
  vi.resetModules();
  uuidCounter = 0;
  return import('../../src/main/workflow-recorder');
}

// ── Helpers ──────────────────────────────────────────────────────────

function makeEvent(
  type: EventType,
  description: string,
  overrides: Partial<Omit<RecordedEvent, 'id' | 'timestamp'>> = {}
): Omit<RecordedEvent, 'id' | 'timestamp'> {
  return {
    type,
    description,
    activeApp: overrides.activeApp !== undefined ? overrides.activeApp : 'VS Code',
    windowTitle: overrides.windowTitle !== undefined ? overrides.windowTitle : 'editor.ts',
    payload: overrides.payload,
    durationMs: overrides.durationMs,
  };
}

// ── Test Suites ──────────────────────────────────────────────────────

describe('WorkflowRecorder', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 1, 26, 10, 0, 0, 0)); // 10:00 AM local
    mockReadFile.mockReset();
    mockWriteFile.mockResolvedValue(undefined);
    mockMkdir.mockResolvedValue(undefined);
    // Default: no existing data
    mockReadFile.mockRejectedValue(new Error('ENOENT'));
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  // ────────────────────────────────────────────────────────────────────
  // INITIALIZATION
  // ────────────────────────────────────────────────────────────────────

  describe('Initialization', () => {
    it('should initialize with empty state when no files exist', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();
      const status = workflowRecorder.getStatus();
      expect(status.status).toBe('idle');
      expect(workflowRecorder.getAllRecordings()).toEqual([]);
      expect(workflowRecorder.getAllTemplates()).toEqual([]);
    });

    it('should create data directory on initialize', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();
      expect(mockMkdir).toHaveBeenCalledWith(
        expect.stringContaining('workflows'),
        { recursive: true }
      );
    });

    it('should load existing recordings from disk', async () => {
      const existingRecording: WorkflowRecording = {
        id: 'rec-001',
        name: 'Test Flow',
        description: '',
        startedAt: Date.now() - 60000,
        endedAt: Date.now(),
        events: [],
        keyFrames: [],
        status: 'complete',
        metadata: {
          durationMs: 60000,
          eventCount: 0,
          appsUsed: [],
          clipboardOps: 0,
          annotationCount: 0,
        },
      };

      mockReadFile.mockImplementation((filePath: string) => {
        if (filePath.includes('recordings.json')) {
          return Promise.resolve(JSON.stringify({ recordings: [existingRecording] }));
        }
        if (filePath.includes('templates.json')) {
          return Promise.resolve(JSON.stringify({ templates: [] }));
        }
        return Promise.reject(new Error('ENOENT'));
      });

      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();
      expect(workflowRecorder.getAllRecordings()).toHaveLength(1);
      expect(workflowRecorder.getRecording('rec-001')).toBeTruthy();
    });

    it('should load existing templates from disk', async () => {
      const existingTemplate: WorkflowTemplate = {
        id: 'tpl-001',
        name: 'Login Flow',
        description: 'Steps to log in',
        sourceRecordingId: 'rec-001',
        createdAt: Date.now(),
        updatedAt: Date.now(),
        steps: [],
        parameters: [],
        estimatedDurationMs: 5000,
        tags: ['auth'],
      };

      mockReadFile.mockImplementation((filePath: string) => {
        if (filePath.includes('recordings.json')) {
          return Promise.resolve(JSON.stringify({ recordings: [] }));
        }
        if (filePath.includes('templates.json')) {
          return Promise.resolve(JSON.stringify({ templates: [existingTemplate] }));
        }
        return Promise.reject(new Error('ENOENT'));
      });

      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();
      expect(workflowRecorder.getAllTemplates()).toHaveLength(1);
      expect(workflowRecorder.getTemplate('tpl-001')).toBeTruthy();
    });

    it('should accept custom config overrides', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize({ maxEvents: 100, maxRecordings: 5 });
      const config = workflowRecorder.getConfig();
      expect(config.maxEvents).toBe(100);
      expect(config.maxRecordings).toBe(5);
      // Other defaults preserved
      expect(config.maxKeyFrames).toBe(20);
      expect(config.idleGapThresholdMs).toBe(30_000);
    });

    it('should handle corrupt JSON gracefully', async () => {
      mockReadFile.mockResolvedValue('{ invalid json');
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();
      expect(workflowRecorder.getAllRecordings()).toEqual([]);
      expect(workflowRecorder.getAllTemplates()).toEqual([]);
    });

    it('should prune recordings on load if over limit', async () => {
      const recordings = Array.from({ length: 60 }, (_, i) => ({
        id: `rec-${i}`,
        name: `Flow ${i}`,
        description: '',
        startedAt: Date.now() - 60000,
        endedAt: Date.now(),
        events: [],
        keyFrames: [],
        status: 'complete' as const,
        metadata: { durationMs: 60000, eventCount: 0, appsUsed: [], clipboardOps: 0, annotationCount: 0 },
      }));

      mockReadFile.mockImplementation((filePath: string) => {
        if (filePath.includes('recordings.json')) {
          return Promise.resolve(JSON.stringify({ recordings }));
        }
        return Promise.reject(new Error('ENOENT'));
      });

      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();
      expect(workflowRecorder.getAllRecordings().length).toBeLessThanOrEqual(50);
    });
  });

  // ────────────────────────────────────────────────────────────────────
  // RECORDING LIFECYCLE
  // ────────────────────────────────────────────────────────────────────

  describe('Recording Lifecycle', () => {
    it('should start recording successfully', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      const result = workflowRecorder.startRecording('Deploy to prod');
      expect(result.success).toBe(true);
      expect(result.recordingId).toBeTruthy();
      expect(result.error).toBeUndefined();

      const status = workflowRecorder.getStatus();
      expect(status.status).toBe('recording');
      expect(status.currentRecordingId).toBe(result.recordingId);
      expect(status.currentEventCount).toBe(0);
    });

    it('should reject starting when already recording', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Flow A');
      const second = workflowRecorder.startRecording('Flow B');
      expect(second.success).toBe(false);
      expect(second.error).toContain('currently recording');
    });

    it('should reject empty name', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      const result = workflowRecorder.startRecording('');
      expect(result.success).toBe(false);
      expect(result.error).toContain('required');
    });

    it('should truncate long names to 100 chars', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      const longName = 'A'.repeat(200);
      workflowRecorder.startRecording(longName);
      const recording = workflowRecorder.stopRecording();
      expect(recording).toBeTruthy();
      expect(recording!.name.length).toBe(100);
    });

    it('should stop recording and produce a WorkflowRecording', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Deploy Flow');
      vi.advanceTimersByTime(5000);

      const recording = workflowRecorder.stopRecording();
      expect(recording).toBeTruthy();
      expect(recording!.name).toBe('Deploy Flow');
      expect(recording!.status).toBe('complete');
      expect(recording!.endedAt).toBeGreaterThan(recording!.startedAt);
      expect(recording!.metadata.durationMs).toBeGreaterThan(0);

      const status = workflowRecorder.getStatus();
      expect(status.status).toBe('idle');
    });

    it('should return null when stopping with no active recording', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      const result = workflowRecorder.stopRecording();
      expect(result).toBeNull();
    });

    it('should cancel recording without saving', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Scratch Flow');
      workflowRecorder.recordEvent(makeEvent('app_switch', 'Opened browser'));
      const cancelled = workflowRecorder.cancelRecording();

      expect(cancelled).toBe(true);
      expect(workflowRecorder.getStatus().status).toBe('idle');
      expect(workflowRecorder.getAllRecordings()).toHaveLength(0);
    });

    it('should return false when cancelling with no active recording', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      expect(workflowRecorder.cancelRecording()).toBe(false);
    });

    it('should allow starting a new recording after cancellation', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Flow A');
      workflowRecorder.cancelRecording();
      const result = workflowRecorder.startRecording('Flow B');
      expect(result.success).toBe(true);
    });

    it('should allow starting a new recording after stopping', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Flow A');
      workflowRecorder.stopRecording();
      const result = workflowRecorder.startRecording('Flow B');
      expect(result.success).toBe(true);
    });

    it('should auto-stop after max duration', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize({ maxRecordingDurationMs: 5000 });

      workflowRecorder.startRecording('Long Flow');
      expect(workflowRecorder.getStatus().status).toBe('recording');

      vi.advanceTimersByTime(6000);
      expect(workflowRecorder.getStatus().status).toBe('idle');
      // Recording should have been saved
      expect(workflowRecorder.getAllRecordings()).toHaveLength(1);
    });

    it('should clear timers on stop', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Flow');
      workflowRecorder.stopRecording();

      // Advancing time past max duration should NOT cause issues
      vi.advanceTimersByTime(35 * 60 * 1000);
      expect(workflowRecorder.getStatus().status).toBe('idle');
      // Only the one recording from stopRecording
      expect(workflowRecorder.getAllRecordings()).toHaveLength(1);
    });
  });

  // ────────────────────────────────────────────────────────────────────
  // EVENT CAPTURE
  // ────────────────────────────────────────────────────────────────────

  describe('Event Capture', () => {
    it('should record events during an active session', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Event Test');
      const added = workflowRecorder.recordEvent(
        makeEvent('app_switch', 'Switched to Chrome', { activeApp: 'Chrome' })
      );
      expect(added).toBe(true);

      const status = workflowRecorder.getStatus();
      expect(status.currentEventCount).toBe(1);
    });

    it('should reject events when not recording', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      const added = workflowRecorder.recordEvent(
        makeEvent('app_switch', 'Switched to Chrome')
      );
      expect(added).toBe(false);
    });

    it('should reject events when maxEvents reached', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize({ maxEvents: 3 });

      workflowRecorder.startRecording('Max Events Test');
      expect(workflowRecorder.recordEvent(makeEvent('app_switch', 'E1'))).toBe(true);
      expect(workflowRecorder.recordEvent(makeEvent('app_switch', 'E2'))).toBe(true);
      expect(workflowRecorder.recordEvent(makeEvent('app_switch', 'E3'))).toBe(true);
      expect(workflowRecorder.recordEvent(makeEvent('app_switch', 'E4'))).toBe(false);
    });

    it('should truncate payload to 500 chars', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Payload Test');
      const longPayload = 'X'.repeat(1000);
      workflowRecorder.recordEvent(
        makeEvent('clipboard_copy', 'Copied long text', { payload: longPayload })
      );

      const recording = workflowRecorder.stopRecording();
      expect(recording!.events[0].payload!.length).toBe(500);
    });

    it('should capture all event types', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      const types: EventType[] = [
        'app_switch', 'clipboard_copy', 'clipboard_paste',
        'url_navigation', 'typing_burst', 'screenshot',
        'user_annotation', 'custom',
      ];

      workflowRecorder.startRecording('All Types Test');
      for (const t of types) {
        workflowRecorder.recordEvent(makeEvent(t, `Event: ${t}`));
      }

      const recording = workflowRecorder.stopRecording();
      expect(recording!.events.length).toBe(types.length);
      const recordedTypes = recording!.events.map(e => e.type);
      for (const t of types) {
        expect(recordedTypes).toContain(t);
      }
    });

    it('should record events with duration', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Duration Test');
      workflowRecorder.recordEvent(
        makeEvent('typing_burst', 'Typed code', { durationMs: 3500 })
      );

      const recording = workflowRecorder.stopRecording();
      expect(recording!.events[0].durationMs).toBe(3500);
    });

    it('should insert idle gap events for long pauses', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize({ idleGapThresholdMs: 10_000 });

      workflowRecorder.startRecording('Idle Test');

      workflowRecorder.recordEvent(makeEvent('app_switch', 'Step 1'));
      vi.advanceTimersByTime(15_000); // 15s gap > 10s threshold
      workflowRecorder.recordEvent(makeEvent('app_switch', 'Step 2'));

      const recording = workflowRecorder.stopRecording();
      // Should be: [step1, idle_gap, step2]
      expect(recording!.events).toHaveLength(3);
      expect(recording!.events[1].type).toBe('idle_gap');
      expect(recording!.events[1].description).toContain('Idle for');
      expect(recording!.events[1].durationMs).toBeGreaterThanOrEqual(15_000);
    });

    it('should NOT insert idle gap for short pauses', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize({ idleGapThresholdMs: 30_000 });

      workflowRecorder.startRecording('Short Pause');

      workflowRecorder.recordEvent(makeEvent('app_switch', 'Step 1'));
      vi.advanceTimersByTime(5_000); // 5s < 30s threshold
      workflowRecorder.recordEvent(makeEvent('app_switch', 'Step 2'));

      const recording = workflowRecorder.stopRecording();
      expect(recording!.events).toHaveLength(2);
      expect(recording!.events.every(e => e.type !== 'idle_gap')).toBe(true);
    });

    it('should track active app context from events', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Context Track');
      workflowRecorder.recordEvent(
        makeEvent('app_switch', 'Switched', { activeApp: 'Chrome', windowTitle: 'Google' })
      );

      // Annotation should pick up the last app context
      workflowRecorder.addAnnotation('Important note');
      const recording = workflowRecorder.stopRecording();
      const annotation = recording!.events.find(e => e.type === 'user_annotation');
      expect(annotation!.activeApp).toBe('Chrome');
      expect(annotation!.windowTitle).toBe('Google');
    });
  });

  // ────────────────────────────────────────────────────────────────────
  // ANNOTATIONS
  // ────────────────────────────────────────────────────────────────────

  describe('Annotations', () => {
    it('should add annotation to the current recording', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Annotation Test');
      const added = workflowRecorder.addAnnotation('This is important');
      expect(added).toBe(true);

      const recording = workflowRecorder.stopRecording();
      const annotation = recording!.events.find(e => e.type === 'user_annotation');
      expect(annotation).toBeTruthy();
      expect(annotation!.description).toBe('This is important');
    });

    it('should reject annotation when not recording', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      expect(workflowRecorder.addAnnotation('No recording')).toBe(false);
    });

    it('should truncate annotation to 500 chars', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Long Annotation');
      workflowRecorder.addAnnotation('X'.repeat(800));

      const recording = workflowRecorder.stopRecording();
      const annotation = recording!.events.find(e => e.type === 'user_annotation');
      expect(annotation!.description.length).toBe(500);
    });
  });

  // ────────────────────────────────────────────────────────────────────
  // KEY FRAMES
  // ────────────────────────────────────────────────────────────────────

  describe('Key Frames', () => {
    it('should add key-frame during recording', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('KeyFrame Test');
      const added = workflowRecorder.addKeyFrame('/screenshots/frame1.jpg', 'Chrome');
      expect(added).toBe(true);

      const recording = workflowRecorder.stopRecording();
      expect(recording!.keyFrames).toHaveLength(1);
      expect(recording!.keyFrames[0].filePath).toBe('/screenshots/frame1.jpg');
      expect(recording!.keyFrames[0].activeApp).toBe('Chrome');
    });

    it('should reject key-frame when not recording', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      expect(workflowRecorder.addKeyFrame('/frame.jpg', 'Chrome')).toBe(false);
    });

    it('should enforce maxKeyFrames limit', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize({ maxKeyFrames: 3 });

      workflowRecorder.startRecording('Keyframe Limit');
      expect(workflowRecorder.addKeyFrame('/f1.jpg', 'App')).toBe(true);
      expect(workflowRecorder.addKeyFrame('/f2.jpg', 'App')).toBe(true);
      expect(workflowRecorder.addKeyFrame('/f3.jpg', 'App')).toBe(true);
      expect(workflowRecorder.addKeyFrame('/f4.jpg', 'App')).toBe(false);

      const recording = workflowRecorder.stopRecording();
      expect(recording!.keyFrames).toHaveLength(3);
    });
  });

  // ────────────────────────────────────────────────────────────────────
  // METADATA
  // ────────────────────────────────────────────────────────────────────

  describe('Recording Metadata', () => {
    it('should compute metadata on stopRecording', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Metadata Test');
      workflowRecorder.recordEvent(
        makeEvent('app_switch', 'Opened browser', { activeApp: 'Chrome' })
      );
      workflowRecorder.recordEvent(
        makeEvent('clipboard_copy', 'Copied URL', { activeApp: 'Chrome', payload: 'https://example.com' })
      );
      workflowRecorder.recordEvent(
        makeEvent('clipboard_paste', 'Pasted URL', { activeApp: 'VS Code' })
      );
      workflowRecorder.addAnnotation('Parameter: API key');

      vi.advanceTimersByTime(10_000);
      const recording = workflowRecorder.stopRecording();

      expect(recording!.metadata.eventCount).toBe(4);
      expect(recording!.metadata.appsUsed).toContain('Chrome');
      expect(recording!.metadata.appsUsed).toContain('VS Code');
      expect(recording!.metadata.clipboardOps).toBe(2);
      expect(recording!.metadata.annotationCount).toBe(1);
      expect(recording!.metadata.durationMs).toBeGreaterThan(0);
    });

    it('should count only unique apps', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Apps Test');
      workflowRecorder.recordEvent(makeEvent('app_switch', 'E1', { activeApp: 'Chrome' }));
      workflowRecorder.recordEvent(makeEvent('app_switch', 'E2', { activeApp: 'Chrome' }));
      workflowRecorder.recordEvent(makeEvent('app_switch', 'E3', { activeApp: 'VS Code' }));

      const recording = workflowRecorder.stopRecording();
      expect(recording!.metadata.appsUsed).toEqual(['Chrome', 'VS Code']);
    });
  });

  // ────────────────────────────────────────────────────────────────────
  // TEMPLATE CREATION
  // ────────────────────────────────────────────────────────────────────

  describe('Template Creation', () => {
    async function createRecordingWithEvents() {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Template Source');
      // Chrome phase
      workflowRecorder.recordEvent(makeEvent('app_switch', 'Opened browser', { activeApp: 'Chrome', windowTitle: 'Google' }));
      workflowRecorder.recordEvent(makeEvent('url_navigation', 'Navigated to site', { activeApp: 'Chrome', windowTitle: 'Example' }));
      workflowRecorder.recordEvent(makeEvent('clipboard_copy', 'Copied data', { activeApp: 'Chrome', payload: 'https://api.example.com' }));
      // VS Code phase
      workflowRecorder.recordEvent(makeEvent('app_switch', 'Switched to editor', { activeApp: 'VS Code', windowTitle: 'config.ts' }));
      workflowRecorder.recordEvent(makeEvent('clipboard_paste', 'Pasted URL', { activeApp: 'VS Code', windowTitle: 'config.ts' }));
      workflowRecorder.recordEvent(makeEvent('typing_burst', 'Typed config', { activeApp: 'VS Code', windowTitle: 'config.ts', durationMs: 2000 }));
      workflowRecorder.addAnnotation('api_key=sk-test-123');
      // Terminal phase
      workflowRecorder.recordEvent(makeEvent('app_switch', 'Opened terminal', { activeApp: 'Terminal', windowTitle: 'bash' }));
      workflowRecorder.recordEvent(makeEvent('typing_burst', 'Ran deploy command', { activeApp: 'Terminal', windowTitle: 'bash', durationMs: 1000 }));

      vi.advanceTimersByTime(10_000);
      const recording = workflowRecorder.stopRecording()!;
      return { workflowRecorder, recording };
    }

    it('should create a template from a recording', async () => {
      const { workflowRecorder, recording } = await createRecordingWithEvents();

      const template = workflowRecorder.createTemplate(recording.id);
      expect(template).toBeTruthy();
      expect(template!.name).toBe('Template Source');
      expect(template!.sourceRecordingId).toBe(recording.id);
      expect(template!.steps.length).toBeGreaterThan(0);
    });

    it('should return null for non-existent recording', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      expect(workflowRecorder.createTemplate('nonexistent')).toBeNull();
    });

    it('should abstract events into steps grouped by app', async () => {
      const { workflowRecorder, recording } = await createRecordingWithEvents();

      const template = workflowRecorder.createTemplate(recording.id)!;
      // Should have steps for Chrome, VS Code, Terminal phases
      const apps = template.steps.map(s => s.targetApp);
      expect(apps).toContain('Chrome');
      expect(apps).toContain('VS Code');
      expect(apps).toContain('Terminal');
    });

    it('should assign sequential order to steps', async () => {
      const { workflowRecorder, recording } = await createRecordingWithEvents();

      const template = workflowRecorder.createTemplate(recording.id)!;
      for (let i = 0; i < template.steps.length; i++) {
        expect(template.steps[i].order).toBe(i + 1);
      }
    });

    it('should mark steps with annotations as decision points', async () => {
      const { workflowRecorder, recording } = await createRecordingWithEvents();

      const template = workflowRecorder.createTemplate(recording.id)!;
      const vsCodeStep = template.steps.find(s => s.targetApp === 'VS Code');
      expect(vsCodeStep!.isDecisionPoint).toBe(true);
    });

    it('should use annotation text as step intent', async () => {
      const { workflowRecorder, recording } = await createRecordingWithEvents();

      const template = workflowRecorder.createTemplate(recording.id)!;
      const vsCodeStep = template.steps.find(s => s.targetApp === 'VS Code');
      expect(vsCodeStep!.intent).toContain('api_key=sk-test-123');
    });

    it('should add clipboard_data parameterRef for steps with clipboard ops', async () => {
      const { workflowRecorder, recording } = await createRecordingWithEvents();

      const template = workflowRecorder.createTemplate(recording.id)!;
      const chromeStep = template.steps.find(s => s.targetApp === 'Chrome');
      expect(chromeStep!.parameterRefs).toContain('clipboard_data');
    });

    it('should infer parameters from clipboard_copy events', async () => {
      const { workflowRecorder, recording } = await createRecordingWithEvents();

      const template = workflowRecorder.createTemplate(recording.id)!;
      const clipParam = template.parameters.find(p => p.source === 'inferred');
      expect(clipParam).toBeTruthy();
      expect(clipParam!.defaultValue).toBe('https://api.example.com');
      expect(clipParam!.dataType).toBe('url');
    });

    it('should infer parameters from name=value annotations', async () => {
      const { workflowRecorder, recording } = await createRecordingWithEvents();

      const template = workflowRecorder.createTemplate(recording.id)!;
      const annotatedParam = template.parameters.find(p => p.source === 'user_annotated');
      expect(annotatedParam).toBeTruthy();
      expect(annotatedParam!.name).toBe('api_key');
      expect(annotatedParam!.defaultValue).toBe('sk-test-123');
    });

    it('should accept overrides for name, description, tags', async () => {
      const { workflowRecorder, recording } = await createRecordingWithEvents();

      const template = workflowRecorder.createTemplate(recording.id, {
        name: 'Custom Deploy',
        description: 'Automated deploy flow',
        tags: ['deploy', 'prod'],
      })!;

      expect(template.name).toBe('Custom Deploy');
      expect(template.description).toBe('Automated deploy flow');
      expect(template.tags).toEqual(['deploy', 'prod']);
    });

    it('should merge user parameters with inferred parameters', async () => {
      const { workflowRecorder, recording } = await createRecordingWithEvents();

      const userParam: WorkflowParameter = {
        id: 'custom_param',
        name: 'Server',
        description: 'Target server',
        defaultValue: 'prod-01',
        source: 'user_annotated',
        dataType: 'text',
      };

      const template = workflowRecorder.createTemplate(recording.id, {
        parameters: [userParam],
      })!;

      // Should have both inferred and user params
      expect(template.parameters.find(p => p.id === 'custom_param')).toBeTruthy();
      expect(template.parameters.length).toBeGreaterThan(1);
    });

    it('should create template with empty events recording', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Empty Flow');
      const recording = workflowRecorder.stopRecording()!;

      const template = workflowRecorder.createTemplate(recording.id);
      expect(template).toBeTruthy();
      expect(template!.steps).toHaveLength(0);
      expect(template!.parameters).toHaveLength(0);
    });

    it('should estimate template duration from recording', async () => {
      const { workflowRecorder, recording } = await createRecordingWithEvents();

      const template = workflowRecorder.createTemplate(recording.id)!;
      expect(template.estimatedDurationMs).toBe(recording.metadata.durationMs);
    });
  });

  // ────────────────────────────────────────────────────────────────────
  // DATA TYPE GUESSING
  // ────────────────────────────────────────────────────────────────────

  describe('Data Type Guessing', () => {
    it('should detect URL type', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Type Test');
      workflowRecorder.recordEvent(
        makeEvent('clipboard_copy', 'Copied URL', { payload: 'https://example.com/api/v1' })
      );
      const recording = workflowRecorder.stopRecording()!;
      const template = workflowRecorder.createTemplate(recording.id)!;
      const param = template.parameters.find(p => p.source === 'inferred');
      expect(param!.dataType).toBe('url');
    });

    it('should detect email type', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Email Test');
      workflowRecorder.recordEvent(
        makeEvent('clipboard_copy', 'Copied email', { payload: 'alice@example.com' })
      );
      const recording = workflowRecorder.stopRecording()!;
      const template = workflowRecorder.createTemplate(recording.id)!;
      const param = template.parameters.find(p => p.source === 'inferred');
      expect(param!.dataType).toBe('email');
    });

    it('should detect date type', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Date Test');
      workflowRecorder.recordEvent(
        makeEvent('clipboard_copy', 'Copied date', { payload: '2026-02-26' })
      );
      const recording = workflowRecorder.stopRecording()!;
      const template = workflowRecorder.createTemplate(recording.id)!;
      const param = template.parameters.find(p => p.source === 'inferred');
      expect(param!.dataType).toBe('date');
    });

    it('should detect number type', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Number Test');
      workflowRecorder.recordEvent(
        makeEvent('clipboard_copy', 'Copied number', { payload: '42.5' })
      );
      const recording = workflowRecorder.stopRecording()!;
      const template = workflowRecorder.createTemplate(recording.id)!;
      const param = template.parameters.find(p => p.source === 'inferred');
      expect(param!.dataType).toBe('number');
    });

    it('should detect filepath type', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Filepath Test');
      workflowRecorder.recordEvent(
        makeEvent('clipboard_copy', 'Copied path', { payload: 'C:\\Users\\test\\file.txt' })
      );
      const recording = workflowRecorder.stopRecording()!;
      const template = workflowRecorder.createTemplate(recording.id)!;
      const param = template.parameters.find(p => p.source === 'inferred');
      expect(param!.dataType).toBe('filepath');
    });

    it('should detect unix filepath type', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Unix Path Test');
      workflowRecorder.recordEvent(
        makeEvent('clipboard_copy', 'Copied path', { payload: '/home/user/document.md' })
      );
      const recording = workflowRecorder.stopRecording()!;
      const template = workflowRecorder.createTemplate(recording.id)!;
      const param = template.parameters.find(p => p.source === 'inferred');
      expect(param!.dataType).toBe('filepath');
    });

    it('should default to text type for plain strings', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Text Test');
      workflowRecorder.recordEvent(
        makeEvent('clipboard_copy', 'Copied text', { payload: 'Hello world this is some text' })
      );
      const recording = workflowRecorder.stopRecording()!;
      const template = workflowRecorder.createTemplate(recording.id)!;
      const param = template.parameters.find(p => p.source === 'inferred');
      expect(param!.dataType).toBe('text');
    });
  });

  // ────────────────────────────────────────────────────────────────────
  // QUERIES
  // ────────────────────────────────────────────────────────────────────

  describe('Queries', () => {
    it('should get recording by id', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Query Test');
      const recording = workflowRecorder.stopRecording()!;

      const found = workflowRecorder.getRecording(recording.id);
      expect(found).toBeTruthy();
      expect(found!.name).toBe('Query Test');
    });

    it('should return null for non-existent recording id', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      expect(workflowRecorder.getRecording('nonexistent')).toBeNull();
    });

    it('should get all recordings', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Flow A');
      workflowRecorder.stopRecording();
      workflowRecorder.startRecording('Flow B');
      workflowRecorder.stopRecording();

      expect(workflowRecorder.getAllRecordings()).toHaveLength(2);
    });

    it('should return defensive copies from getAllRecordings', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Copy Test');
      workflowRecorder.stopRecording();

      const recordings = workflowRecorder.getAllRecordings();
      recordings[0].name = 'MUTATED';
      expect(workflowRecorder.getAllRecordings()[0].name).toBe('Copy Test');
    });

    it('should get recent recordings in reverse order', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('First');
      workflowRecorder.stopRecording();
      workflowRecorder.startRecording('Second');
      workflowRecorder.stopRecording();
      workflowRecorder.startRecording('Third');
      workflowRecorder.stopRecording();

      const recent = workflowRecorder.getRecentRecordings(2);
      expect(recent).toHaveLength(2);
      expect(recent[0].name).toBe('Third');
      expect(recent[1].name).toBe('Second');
    });

    it('should get template by id', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Tmpl Query');
      const recording = workflowRecorder.stopRecording()!;
      const template = workflowRecorder.createTemplate(recording.id)!;

      const found = workflowRecorder.getTemplate(template.id);
      expect(found).toBeTruthy();
      expect(found!.name).toBe('Tmpl Query');
    });

    it('should return null for non-existent template id', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      expect(workflowRecorder.getTemplate('nonexistent')).toBeNull();
    });

    it('should get all templates', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Flow A');
      const recA = workflowRecorder.stopRecording()!;
      workflowRecorder.createTemplate(recA.id);

      workflowRecorder.startRecording('Flow B');
      const recB = workflowRecorder.stopRecording()!;
      workflowRecorder.createTemplate(recB.id);

      expect(workflowRecorder.getAllTemplates()).toHaveLength(2);
    });

    it('should get templates by tag', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Deploy');
      const recA = workflowRecorder.stopRecording()!;
      workflowRecorder.createTemplate(recA.id, { tags: ['deploy', 'prod'] });

      workflowRecorder.startRecording('Build');
      const recB = workflowRecorder.stopRecording()!;
      workflowRecorder.createTemplate(recB.id, { tags: ['build', 'ci'] });

      const deployTemplates = workflowRecorder.getTemplatesByTag('deploy');
      expect(deployTemplates).toHaveLength(1);
      expect(deployTemplates[0].name).toBe('Deploy');

      expect(workflowRecorder.getTemplatesByTag('unknown')).toHaveLength(0);
    });

    it('should return config', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize({ maxEvents: 200 });

      const config = workflowRecorder.getConfig();
      expect(config.maxEvents).toBe(200);
      expect(config.maxRecordingDurationMs).toBe(30 * 60 * 1000);
    });

    it('should return status while recording', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      const idleStatus = workflowRecorder.getStatus();
      expect(idleStatus.status).toBe('idle');
      expect(idleStatus.currentRecordingId).toBeUndefined();

      const { recordingId } = workflowRecorder.startRecording('Status Test');
      workflowRecorder.recordEvent(makeEvent('app_switch', 'E1'));
      workflowRecorder.recordEvent(makeEvent('app_switch', 'E2'));

      vi.advanceTimersByTime(5000);
      const activeStatus = workflowRecorder.getStatus();
      expect(activeStatus.status).toBe('recording');
      expect(activeStatus.currentRecordingId).toBe(recordingId);
      expect(activeStatus.currentEventCount).toBe(2);
      expect(activeStatus.elapsedMs).toBeGreaterThanOrEqual(5000);
    });
  });

  // ────────────────────────────────────────────────────────────────────
  // DELETION
  // ────────────────────────────────────────────────────────────────────

  describe('Deletion', () => {
    it('should delete a recording by id', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Delete Me');
      const recording = workflowRecorder.stopRecording()!;

      expect(workflowRecorder.deleteRecording(recording.id)).toBe(true);
      expect(workflowRecorder.getRecording(recording.id)).toBeNull();
      expect(workflowRecorder.getAllRecordings()).toHaveLength(0);
    });

    it('should return false when deleting non-existent recording', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      expect(workflowRecorder.deleteRecording('nonexistent')).toBe(false);
    });

    it('should delete a template by id', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Template Del');
      const recording = workflowRecorder.stopRecording()!;
      const template = workflowRecorder.createTemplate(recording.id)!;

      expect(workflowRecorder.deleteTemplate(template.id)).toBe(true);
      expect(workflowRecorder.getTemplate(template.id)).toBeNull();
    });

    it('should return false when deleting non-existent template', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      expect(workflowRecorder.deleteTemplate('nonexistent')).toBe(false);
    });
  });

  // ────────────────────────────────────────────────────────────────────
  // PERSISTENCE
  // ────────────────────────────────────────────────────────────────────

  describe('Persistence', () => {
    it('should queue save after stopping recording (debounced)', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();
      mockWriteFile.mockClear(); // ensure clean state

      workflowRecorder.startRecording('Save Test');
      workflowRecorder.stopRecording();

      // After 2s debounce, save fires (async to flush microtasks from save())
      await vi.advanceTimersByTimeAsync(3000);
      // Should have written at least recordings.json and templates.json
      const paths = mockWriteFile.mock.calls.map((c: any[]) => c[0]);
      expect(paths.some((p: string) => p.includes('recordings.json'))).toBe(true);
      expect(paths.some((p: string) => p.includes('templates.json'))).toBe(true);
    });

    it('should write both recordings.json and templates.json', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Persist Test');
      workflowRecorder.stopRecording();

      await vi.advanceTimersByTimeAsync(3000);

      const writeCalls = mockWriteFile.mock.calls;
      const paths = writeCalls.map((c: any[]) => c[0]);
      expect(paths.some((p: string) => p.includes('recordings.json'))).toBe(true);
      expect(paths.some((p: string) => p.includes('templates.json'))).toBe(true);
    });

    it('should debounce rapid saves', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();
      mockWriteFile.mockClear(); // ensure clean state

      workflowRecorder.startRecording('Flow A');
      workflowRecorder.stopRecording();
      // Second stop immediately after — should be debounced with first
      workflowRecorder.startRecording('Flow B');
      workflowRecorder.stopRecording();

      await vi.advanceTimersByTimeAsync(3000);

      // Both recordings should be saved in a single write (debounced)
      const recordingSaves = mockWriteFile.mock.calls.filter(
        (c: any[]) => c[0].includes('recordings.json')
      );
      // At most 1 write to recordings.json (debounce coalesces)
      expect(recordingSaves.length).toBeLessThanOrEqual(1);
      // But we should have the save content containing both recordings
      if (recordingSaves.length > 0) {
        const content = JSON.parse(recordingSaves[0][1]);
        expect(content.recordings).toHaveLength(2);
      }
    });

    it('should queue save after template creation', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Template Save');
      const recording = workflowRecorder.stopRecording()!;

      // Wait for recording save
      await vi.advanceTimersByTimeAsync(3000);
      mockWriteFile.mockClear();

      workflowRecorder.createTemplate(recording.id);
      await vi.advanceTimersByTimeAsync(3000);
      expect(mockWriteFile).toHaveBeenCalled();
    });

    it('should queue save after deletion', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Delete Save');
      const recording = workflowRecorder.stopRecording()!;

      await vi.advanceTimersByTimeAsync(3000);
      mockWriteFile.mockClear();

      workflowRecorder.deleteRecording(recording.id);
      await vi.advanceTimersByTimeAsync(3000);
      expect(mockWriteFile).toHaveBeenCalled();
    });
  });

  // ────────────────────────────────────────────────────────────────────
  // PRUNING
  // ────────────────────────────────────────────────────────────────────

  describe('Pruning', () => {
    it('should prune recordings when over maxRecordings', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize({ maxRecordings: 3 });

      for (let i = 0; i < 5; i++) {
        workflowRecorder.startRecording(`Flow ${i}`);
        workflowRecorder.stopRecording();
      }

      expect(workflowRecorder.getAllRecordings().length).toBeLessThanOrEqual(3);
      // Should keep the most recent
      const names = workflowRecorder.getAllRecordings().map(r => r.name);
      expect(names).toContain('Flow 4');
      expect(names).toContain('Flow 3');
    });

    it('should prune templates when over maxTemplates', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize({ maxTemplates: 2, maxRecordings: 10 });

      for (let i = 0; i < 4; i++) {
        workflowRecorder.startRecording(`Flow ${i}`);
        const recording = workflowRecorder.stopRecording()!;
        workflowRecorder.createTemplate(recording.id, { tags: [`tag-${i}`] });
      }

      expect(workflowRecorder.getAllTemplates().length).toBeLessThanOrEqual(2);
    });
  });

  // ────────────────────────────────────────────────────────────────────
  // DEDUPLICATION (clipboard parameter inference)
  // ────────────────────────────────────────────────────────────────────

  describe('Parameter Deduplication', () => {
    it('should not create duplicate params for identical clipboard content', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Dedup Test');
      workflowRecorder.recordEvent(
        makeEvent('clipboard_copy', 'Copy 1', { payload: 'same-value' })
      );
      workflowRecorder.recordEvent(
        makeEvent('clipboard_copy', 'Copy 2', { payload: 'same-value' })
      );
      workflowRecorder.recordEvent(
        makeEvent('clipboard_copy', 'Copy 3', { payload: 'different-value' })
      );
      const recording = workflowRecorder.stopRecording()!;

      const template = workflowRecorder.createTemplate(recording.id)!;
      const inferredParams = template.parameters.filter(p => p.source === 'inferred');
      // "same-value" should only produce 1 param
      expect(inferredParams).toHaveLength(2);
    });
  });

  // ────────────────────────────────────────────────────────────────────
  // cLaw GATE COMPLIANCE
  // ────────────────────────────────────────────────────────────────────

  describe('cLaw Gate Compliance', () => {
    it('should not auto-start recording (requires explicit user call)', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      // After init, should be idle — no autonomous recording
      expect(workflowRecorder.getStatus().status).toBe('idle');
      expect(workflowRecorder.getAllRecordings()).toHaveLength(0);
    });

    it('should store all data locally (data dir under userData)', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Local Data Test');
      workflowRecorder.stopRecording();

      await vi.advanceTimersByTimeAsync(3000);

      for (const call of mockWriteFile.mock.calls) {
        const writePath = call[0] as string;
        expect(writePath).toContain('workflows');
      }
    });

    it('templates should be inert data objects (no execute method)', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Inert Template');
      const recording = workflowRecorder.stopRecording()!;
      const template = workflowRecorder.createTemplate(recording.id)!;

      // Template is plain data — no execute/run/replay methods
      expect(typeof template).toBe('object');
      expect(template).not.toHaveProperty('execute');
      expect(template).not.toHaveProperty('run');
      expect(template).not.toHaveProperty('replay');
    });

    it('should not record events when in idle state', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      // Without starting, nothing should be captured
      expect(workflowRecorder.recordEvent(makeEvent('app_switch', 'Sneak'))).toBe(false);
      expect(workflowRecorder.addAnnotation('Sneak')).toBe(false);
      expect(workflowRecorder.addKeyFrame('/sneak.jpg', 'App')).toBe(false);
    });

    it('cancelled recordings should leave no trace', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Secret Flow');
      workflowRecorder.recordEvent(makeEvent('clipboard_copy', 'Sensitive', { payload: 'password123' }));
      workflowRecorder.addAnnotation('Secret note');
      workflowRecorder.cancelRecording();

      // No recordings, no events, no data
      expect(workflowRecorder.getAllRecordings()).toHaveLength(0);
      expect(workflowRecorder.getStatus().status).toBe('idle');
    });

    it('should enforce maxRecordingDuration as safety limit', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize({ maxRecordingDurationMs: 10_000 });

      workflowRecorder.startRecording('Runaway');
      vi.advanceTimersByTime(15_000);

      expect(workflowRecorder.getStatus().status).toBe('idle');
      const recordings = workflowRecorder.getAllRecordings();
      expect(recordings).toHaveLength(1);
      expect(recordings[0].name).toBe('Runaway');
    });
  });

  // ────────────────────────────────────────────────────────────────────
  // EDGE CASES
  // ────────────────────────────────────────────────────────────────────

  describe('Edge Cases', () => {
    it('should handle recording with no events', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Empty');
      const recording = workflowRecorder.stopRecording();

      expect(recording).toBeTruthy();
      expect(recording!.events).toHaveLength(0);
      expect(recording!.metadata.eventCount).toBe(0);
      expect(recording!.metadata.appsUsed).toEqual([]);
    });

    it('should handle rapid start/stop cycles', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      for (let i = 0; i < 5; i++) {
        const result = workflowRecorder.startRecording(`Rapid ${i}`);
        expect(result.success).toBe(true);
        workflowRecorder.stopRecording();
      }

      expect(workflowRecorder.getAllRecordings()).toHaveLength(5);
    });

    it('should handle events with empty activeApp', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Empty App');
      workflowRecorder.recordEvent({
        type: 'custom',
        description: 'No app',
        activeApp: '',
        windowTitle: '',
      });

      const recording = workflowRecorder.stopRecording();
      expect(recording).toBeTruthy();
      expect(recording!.events[0].activeApp).toBe('');
    });

    it('should handle events with empty payload', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('No Payload');
      workflowRecorder.recordEvent(makeEvent('clipboard_copy', 'Copied nothing'));

      const recording = workflowRecorder.stopRecording();
      expect(recording!.events[0].payload).toBeUndefined();
    });

    it('should handle template creation from recording with only idle gaps', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize({ idleGapThresholdMs: 1000 });

      workflowRecorder.startRecording('Idle Only');
      workflowRecorder.recordEvent(makeEvent('app_switch', 'Start', { activeApp: 'App' }));
      vi.advanceTimersByTime(5000);
      workflowRecorder.recordEvent(makeEvent('app_switch', 'End', { activeApp: 'App' }));

      const recording = workflowRecorder.stopRecording()!;
      const template = workflowRecorder.createTemplate(recording.id);
      expect(template).toBeTruthy();
      // Idle gaps are skipped in step abstraction — only the non-idle events form steps
      expect(template!.steps.length).toBeGreaterThanOrEqual(1);
    });

    it('should filter empty apps from metadata', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Filter Empty');
      workflowRecorder.recordEvent({
        type: 'custom',
        description: 'No app',
        activeApp: '',
        windowTitle: '',
      });
      workflowRecorder.recordEvent(makeEvent('custom', 'Has app', { activeApp: 'Chrome' }));

      const recording = workflowRecorder.stopRecording();
      // Empty strings should be filtered by the Boolean filter
      expect(recording!.metadata.appsUsed).toEqual(['Chrome']);
    });

    it('should handle getRecentRecordings with limit larger than total', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Only One');
      workflowRecorder.stopRecording();

      const recent = workflowRecorder.getRecentRecordings(100);
      expect(recent).toHaveLength(1);
    });

    it('should handle annotation with name=value but no value', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Bad Annotation');
      workflowRecorder.addAnnotation('key=');

      const recording = workflowRecorder.stopRecording()!;
      const template = workflowRecorder.createTemplate(recording.id)!;
      // "key=" has empty value, should NOT create a parameter
      const annotatedParams = template.parameters.filter(p => p.source === 'user_annotated');
      expect(annotatedParams).toHaveLength(0);
    });

    it('should handle annotation without = sign', async () => {
      const { workflowRecorder } = await createEngine();
      await workflowRecorder.initialize();

      workflowRecorder.startRecording('Plain Annotation');
      workflowRecorder.addAnnotation('Just a note without equals');

      const recording = workflowRecorder.stopRecording()!;
      const template = workflowRecorder.createTemplate(recording.id)!;
      const annotatedParams = template.parameters.filter(p => p.source === 'user_annotated');
      expect(annotatedParams).toHaveLength(0);
    });
  });
});
