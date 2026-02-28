/**
 * Track 5 — Apprentice: Workflow Recorder Engine tests.
 *
 * Validates the full lifecycle of the WorkflowRecorderEngine: recording
 * sessions, event capture, annotation, key-frame management, template
 * creation, query methods, and safety auto-stop.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('electron', () => ({
  app: { getPath: () => '/tmp/test' },
}));

vi.mock('fs/promises', () => ({
  default: {
    readFile: vi.fn().mockRejectedValue(new Error('ENOENT')),
    writeFile: vi.fn().mockResolvedValue(undefined),
    mkdir: vi.fn().mockResolvedValue(undefined),
  },
}));

vi.mock('crypto', () => ({
  default: {
    randomUUID: () => 'aaaabbbb-cccc-dddd-eeee-ffffffffffff',
  },
}));

import type {
  RecordedEvent,
  EventType,
  WorkflowRecording,
  WorkflowTemplate,
} from '../../src/main/workflow-recorder';

let recorder: any;

beforeEach(async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-03-01T12:00:00Z'));
  vi.resetModules();
  const mod = await import('../../src/main/workflow-recorder');
  recorder = mod.workflowRecorder;
  await recorder.initialize();
});

afterEach(() => {
  vi.useRealTimers();
});

// ── Helpers ──────────────────────────────────────────────────────────

function makeEvent(overrides: Partial<Omit<RecordedEvent, 'id' | 'timestamp'>> = {}) {
  return {
    type: 'app_switch' as EventType,
    description: 'Switched to VS Code',
    activeApp: 'VS Code',
    windowTitle: 'main.ts - nexus-os',
    ...overrides,
  };
}

/** Start a recording with a default name, returns the result. */
function startDefault() {
  return recorder.startRecording('Test Workflow');
}

/** Start, push one event, and stop. Returns the WorkflowRecording. */
function quickRecording(name = 'Quick') {
  recorder.startRecording(name);
  recorder.recordEvent(makeEvent());
  return recorder.stopRecording() as WorkflowRecording;
}

// ── 1. startRecording ────────────────────────────────────────────────

describe('startRecording', () => {
  it('returns success:true and a recordingId when idle', () => {
    const result = startDefault();
    expect(result.success).toBe(true);
    expect(result.recordingId).toBeDefined();
  });

  it('sets status to "recording"', () => {
    startDefault();
    expect(recorder.getStatus().status).toBe('recording');
  });

  it('returns an error if already recording', () => {
    startDefault();
    const second = recorder.startRecording('Another');
    expect(second.success).toBe(false);
    expect(second.error).toBeDefined();
  });

  it('returns an error if name is empty', () => {
    const result = recorder.startRecording('');
    expect(result.success).toBe(false);
    expect(result.error).toBeDefined();
  });

  it('returns an error if name is not a string', () => {
    const result = recorder.startRecording(undefined as any);
    expect(result.success).toBe(false);
    expect(result.error).toBeDefined();
  });

  it('caps the recording name at 100 characters', () => {
    const longName = 'A'.repeat(200);
    recorder.startRecording(longName);
    const recording = recorder.stopRecording() as WorkflowRecording;
    expect(recording.name.length).toBeLessThanOrEqual(100);
  });

  it('provides a truthy string recordingId', () => {
    const result = startDefault();
    expect(typeof result.recordingId).toBe('string');
    expect(result.recordingId!.length).toBeGreaterThan(0);
  });
});

// ── 2. recordEvent ───────────────────────────────────────────────────

describe('recordEvent', () => {
  it('returns true when recording is active', () => {
    startDefault();
    expect(recorder.recordEvent(makeEvent())).toBe(true);
  });

  it('returns false when not recording', () => {
    expect(recorder.recordEvent(makeEvent())).toBe(false);
  });

  it('assigns id and timestamp to the event', () => {
    startDefault();
    recorder.recordEvent(makeEvent());
    const recording = recorder.stopRecording() as WorkflowRecording;
    const ev = recording.events[0];
    expect(ev.id).toBeTruthy();
    expect(typeof ev.timestamp).toBe('number');
    expect(ev.timestamp).toBeGreaterThan(0);
  });

  it('truncates payload to 500 characters', () => {
    startDefault();
    const longPayload = 'X'.repeat(1000);
    recorder.recordEvent(makeEvent({ payload: longPayload }));
    const recording = recorder.stopRecording() as WorkflowRecording;
    expect(recording.events[0].payload!.length).toBeLessThanOrEqual(500);
  });

  it('returns false when maxEvents is reached', async () => {
    vi.resetModules();
    const mod = await import('../../src/main/workflow-recorder');
    const rec = mod.workflowRecorder;
    await rec.initialize({ maxEvents: 3 });

    rec.startRecording('Limited');
    expect(rec.recordEvent(makeEvent({ description: 'e1' }))).toBe(true);
    expect(rec.recordEvent(makeEvent({ description: 'e2' }))).toBe(true);
    expect(rec.recordEvent(makeEvent({ description: 'e3' }))).toBe(true);
    expect(rec.recordEvent(makeEvent({ description: 'e4' }))).toBe(false);
    rec.stopRecording();
  });

  it('accepts all EventType values', () => {
    const allTypes: EventType[] = [
      'app_switch',
      'clipboard_copy',
      'clipboard_paste',
      'url_navigation',
      'typing_burst',
      'idle_gap',
      'screenshot',
      'user_annotation',
      'custom',
    ];
    startDefault();
    for (const type of allTypes) {
      expect(recorder.recordEvent(makeEvent({ type }))).toBe(true);
    }
    const recording = recorder.stopRecording() as WorkflowRecording;
    // idle_gap events may be auto-inserted, so total may exceed allTypes.length
    expect(recording.events.length).toBeGreaterThanOrEqual(allTypes.length);
  });

  it('tracks the last app name and window title', () => {
    startDefault();
    recorder.recordEvent(
      makeEvent({ activeApp: 'Firefox', windowTitle: 'GitHub' }),
    );
    // Adding an annotation should inherit the last app context
    recorder.addAnnotation('note');
    const recording = recorder.stopRecording() as WorkflowRecording;
    const annotation = recording.events.find(
      (e: RecordedEvent) => e.type === 'user_annotation',
    );
    expect(annotation).toBeDefined();
    expect(annotation!.activeApp).toBe('Firefox');
    expect(annotation!.windowTitle).toBe('GitHub');
  });

  it('auto-inserts an idle_gap event when pause exceeds idleGapThresholdMs', () => {
    startDefault();
    recorder.recordEvent(makeEvent({ description: 'first' }));

    // Advance time beyond the 30 s default threshold
    vi.advanceTimersByTime(35_000);

    recorder.recordEvent(makeEvent({ description: 'second' }));
    const recording = recorder.stopRecording() as WorkflowRecording;
    const idleEvents = recording.events.filter(
      (e: RecordedEvent) => e.type === 'idle_gap',
    );
    expect(idleEvents.length).toBeGreaterThanOrEqual(1);
    expect(idleEvents[0].durationMs).toBeGreaterThanOrEqual(30_000);
  });
});

// ── 3. addAnnotation ─────────────────────────────────────────────────

describe('addAnnotation', () => {
  it('returns true and records a user_annotation event', () => {
    startDefault();
    expect(recorder.addAnnotation('This is a parameter hint')).toBe(true);
    const recording = recorder.stopRecording() as WorkflowRecording;
    const annotation = recording.events.find(
      (e: RecordedEvent) => e.type === 'user_annotation',
    );
    expect(annotation).toBeDefined();
    expect(annotation!.description).toBe('This is a parameter hint');
  });

  it('returns false when not recording', () => {
    expect(recorder.addAnnotation('note')).toBe(false);
  });

  it('truncates text to 500 characters', () => {
    startDefault();
    recorder.addAnnotation('Z'.repeat(1000));
    const recording = recorder.stopRecording() as WorkflowRecording;
    const annotation = recording.events.find(
      (e: RecordedEvent) => e.type === 'user_annotation',
    );
    expect(annotation!.description.length).toBeLessThanOrEqual(500);
  });
});

// ── 4. addKeyFrame ───────────────────────────────────────────────────

describe('addKeyFrame', () => {
  it('returns true when recording', () => {
    startDefault();
    expect(recorder.addKeyFrame('/tmp/shot1.jpg', 'VS Code')).toBe(true);
  });

  it('returns false when not recording', () => {
    expect(recorder.addKeyFrame('/tmp/shot.jpg', 'Chrome')).toBe(false);
  });

  it('returns false when maxKeyFrames is reached', async () => {
    vi.resetModules();
    const mod = await import('../../src/main/workflow-recorder');
    const rec = mod.workflowRecorder;
    await rec.initialize({ maxKeyFrames: 2 });

    rec.startRecording('KF Test');
    expect(rec.addKeyFrame('/tmp/1.jpg', 'App')).toBe(true);
    expect(rec.addKeyFrame('/tmp/2.jpg', 'App')).toBe(true);
    expect(rec.addKeyFrame('/tmp/3.jpg', 'App')).toBe(false);
    rec.stopRecording();
  });
});

// ── 5. stopRecording ─────────────────────────────────────────────────

describe('stopRecording', () => {
  it('returns a WorkflowRecording with complete data', () => {
    startDefault();
    recorder.recordEvent(makeEvent());
    const recording = recorder.stopRecording() as WorkflowRecording;
    expect(recording).toBeDefined();
    expect(recording.id).toBeTruthy();
    expect(recording.name).toBe('Test Workflow');
    expect(recording.startedAt).toBeGreaterThan(0);
    expect(recording.endedAt).toBeGreaterThanOrEqual(recording.startedAt);
    expect(recording.status).toBe('complete');
  });

  it('sets status back to "idle"', () => {
    startDefault();
    recorder.stopRecording();
    expect(recorder.getStatus().status).toBe('idle');
  });

  it('returns recording with correct events array', () => {
    startDefault();
    recorder.recordEvent(makeEvent({ description: 'A' }));
    recorder.recordEvent(makeEvent({ description: 'B' }));
    const recording = recorder.stopRecording() as WorkflowRecording;
    const descriptions = recording.events.map((e: RecordedEvent) => e.description);
    expect(descriptions).toContain('A');
    expect(descriptions).toContain('B');
  });

  it('includes metadata with eventCount and durationMs', () => {
    startDefault();
    recorder.recordEvent(makeEvent());
    vi.advanceTimersByTime(5000);
    const recording = recorder.stopRecording() as WorkflowRecording;
    expect(recording.metadata.eventCount).toBeGreaterThanOrEqual(1);
    expect(recording.metadata.durationMs).toBeGreaterThanOrEqual(5000);
  });

  it('tracks unique apps in metadata.appsUsed', () => {
    startDefault();
    recorder.recordEvent(makeEvent({ activeApp: 'VS Code' }));
    recorder.recordEvent(makeEvent({ activeApp: 'Chrome' }));
    recorder.recordEvent(makeEvent({ activeApp: 'VS Code' }));
    const recording = recorder.stopRecording() as WorkflowRecording;
    expect(recording.metadata.appsUsed).toContain('VS Code');
    expect(recording.metadata.appsUsed).toContain('Chrome');
    // Should be unique
    const unique = new Set(recording.metadata.appsUsed);
    expect(unique.size).toBe(recording.metadata.appsUsed.length);
  });

  it('counts clipboard operations in metadata.clipboardOps', () => {
    startDefault();
    recorder.recordEvent(makeEvent({ type: 'clipboard_copy' as EventType }));
    recorder.recordEvent(makeEvent({ type: 'clipboard_paste' as EventType }));
    recorder.recordEvent(makeEvent({ type: 'clipboard_copy' as EventType }));
    recorder.recordEvent(makeEvent({ type: 'app_switch' as EventType }));
    const recording = recorder.stopRecording() as WorkflowRecording;
    expect(recording.metadata.clipboardOps).toBe(3);
  });

  it('returns null when not recording', () => {
    expect(recorder.stopRecording()).toBeNull();
  });
});

// ── 6. cancelRecording ───────────────────────────────────────────────

describe('cancelRecording', () => {
  it('returns true when recording', () => {
    startDefault();
    expect(recorder.cancelRecording()).toBe(true);
  });

  it('sets status back to "idle" without saving', () => {
    startDefault();
    recorder.recordEvent(makeEvent());
    recorder.cancelRecording();
    expect(recorder.getStatus().status).toBe('idle');
  });

  it('returns false when not recording', () => {
    expect(recorder.cancelRecording()).toBe(false);
  });

  it('does not store the cancelled recording in getAllRecordings', () => {
    const before = recorder.getAllRecordings().length;
    startDefault();
    recorder.recordEvent(makeEvent());
    recorder.cancelRecording();
    expect(recorder.getAllRecordings().length).toBe(before);
  });
});

// ── 7. createTemplate ────────────────────────────────────────────────

describe('createTemplate', () => {
  it('creates a template with id, steps, and parameters', () => {
    const rec = quickRecording('Template Source');
    const tmpl = recorder.createTemplate(rec.id) as WorkflowTemplate;
    expect(tmpl).toBeDefined();
    expect(tmpl.id).toBeTruthy();
    expect(Array.isArray(tmpl.steps)).toBe(true);
    expect(Array.isArray(tmpl.parameters)).toBe(true);
  });

  it('has sourceRecordingId matching the recording', () => {
    const rec = quickRecording('Source');
    const tmpl = recorder.createTemplate(rec.id) as WorkflowTemplate;
    expect(tmpl.sourceRecordingId).toBe(rec.id);
  });

  it('returns null for non-existent recording', () => {
    expect(recorder.createTemplate('does-not-exist')).toBeNull();
  });

  it('allows template name to be overridden', () => {
    const rec = quickRecording('Original Name');
    const tmpl = recorder.createTemplate(rec.id, {
      name: 'Custom Name',
    }) as WorkflowTemplate;
    expect(tmpl.name).toBe('Custom Name');
  });

  it('templates appear in getAllTemplates', () => {
    const rec = quickRecording('For Template');
    recorder.createTemplate(rec.id);
    const all = recorder.getAllTemplates();
    expect(all.length).toBeGreaterThanOrEqual(1);
  });

  it('getTemplatesByTag filters correctly', () => {
    const rec = quickRecording('Tagged');
    recorder.createTemplate(rec.id, { tags: ['deploy', 'ci'] });
    recorder.createTemplate(rec.id, { tags: ['docs'] });

    const deploy = recorder.getTemplatesByTag('deploy');
    expect(deploy.length).toBe(1);
    expect(deploy[0].tags).toContain('deploy');

    const docs = recorder.getTemplatesByTag('docs');
    expect(docs.length).toBe(1);

    const none = recorder.getTemplatesByTag('nonexistent');
    expect(none.length).toBe(0);
  });
});

// ── 8. Query methods ─────────────────────────────────────────────────

describe('Query methods', () => {
  it('getRecording returns a specific recording by id', () => {
    const rec = quickRecording('Findable');
    const found = recorder.getRecording(rec.id);
    expect(found).toBeDefined();
    expect(found!.name).toBe('Findable');
  });

  it('getRecording returns null for unknown id', () => {
    expect(recorder.getRecording('unknown-id')).toBeNull();
  });

  it('getAllRecordings returns all completed recordings', () => {
    quickRecording('R1');
    quickRecording('R2');
    quickRecording('R3');
    const all = recorder.getAllRecordings();
    expect(all.length).toBeGreaterThanOrEqual(3);
    const names = all.map((r: WorkflowRecording) => r.name);
    expect(names).toContain('R1');
    expect(names).toContain('R2');
    expect(names).toContain('R3');
  });

  it('getTemplate returns a specific template', () => {
    const rec = quickRecording('T Source');
    const tmpl = recorder.createTemplate(rec.id) as WorkflowTemplate;
    const found = recorder.getTemplate(tmpl.id);
    expect(found).toBeDefined();
    expect(found!.id).toBe(tmpl.id);
  });

  it('getStatus returns status, currentRecordingId, recordingCount, templateCount', () => {
    // While idle
    const idle = recorder.getStatus();
    expect(idle.status).toBe('idle');

    // While recording
    const { recordingId } = startDefault();
    const active = recorder.getStatus();
    expect(active.status).toBe('recording');
    expect(active.currentRecordingId).toBe(recordingId);
    expect(typeof active.currentEventCount).toBe('number');
    expect(typeof active.elapsedMs).toBe('number');
    recorder.stopRecording();
  });
});

// ── 9. Safety — auto-stop ────────────────────────────────────────────

describe('Safety — auto-stop', () => {
  it('auto-stops after maxRecordingDurationMs', async () => {
    vi.resetModules();
    const mod = await import('../../src/main/workflow-recorder');
    const rec = mod.workflowRecorder;
    // Use a short max duration for testing
    await rec.initialize({ maxRecordingDurationMs: 10_000 });

    rec.startRecording('Auto Stop Test');
    rec.recordEvent(makeEvent());
    expect(rec.getStatus().status).toBe('recording');

    // Advance past the max duration
    vi.advanceTimersByTime(11_000);

    expect(rec.getStatus().status).toBe('idle');
    // The recording should have been saved
    const all = rec.getAllRecordings();
    expect(all.length).toBeGreaterThanOrEqual(1);
    const last = all[all.length - 1] as WorkflowRecording;
    expect(last.name).toBe('Auto Stop Test');
    expect(last.status).toBe('complete');
  });

  it('getStatus shows "idle" after auto-stop fires', async () => {
    vi.resetModules();
    const mod = await import('../../src/main/workflow-recorder');
    const rec = mod.workflowRecorder;
    await rec.initialize({ maxRecordingDurationMs: 5_000 });

    rec.startRecording('Idle After Auto');
    expect(rec.getStatus().status).toBe('recording');

    vi.advanceTimersByTime(6_000);

    const status = rec.getStatus();
    expect(status.status).toBe('idle');
    expect(status.currentRecordingId).toBeUndefined();
  });
});
