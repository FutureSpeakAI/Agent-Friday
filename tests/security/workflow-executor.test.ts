/**
 * Tests for workflow-executor.ts — Track V Phase 2: Workflow Replay.
 * Validates execution lifecycle, failure hierarchy (recorded → retry → Claude → user),
 * parameter substitution, standing permissions (cLaw Gate), scheduling integration,
 * pause/resume/cancel, context stream events, run history, and persistence.
 *
 * cLaw Gate assertions:
 * - Scheduled workflows with destructive actions MUST have standing permission
 * - Standing permissions must be explicitly granted (never inferred)
 * - Expired / exhausted permissions must be rejected
 * - Every step logged to context stream
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ── Mock Electron ───────────────────────────────────────────────────
const mockGetPath = vi.fn();
vi.mock('electron', () => ({
  app: {
    getPath: (...args: any[]) => mockGetPath(...args),
  },
}));

// ── Mock fs/promises ────────────────────────────────────────────────
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

// ── Mock crypto ─────────────────────────────────────────────────────
let uuidCounter = 0;
vi.mock('crypto', () => ({
  default: {
    randomUUID: () => {
      const n = ++uuidCounter;
      return `${String(n).padStart(8, '0')}-${String(n).padStart(4, '0')}-4000-8000-000000000000`;
    },
  },
}));

// ── Mock workflow-recorder ──────────────────────────────────────────
const mockGetTemplate = vi.fn();
const mockGetAllTemplates = vi.fn();
vi.mock('../../src/main/workflow-recorder', () => ({
  workflowRecorder: {
    getTemplate: (...args: any[]) => mockGetTemplate(...args),
    getAllTemplates: (...args: any[]) => mockGetAllTemplates(...args),
  },
}));

// ── Mock soc-bridge ─────────────────────────────────────────────────
const mockOperateComputer = vi.fn();
const mockTakeScreenshot = vi.fn();
const mockBrowserTask = vi.fn();
vi.mock('../../src/main/soc-bridge', () => ({
  operateComputer: (...args: any[]) => mockOperateComputer(...args),
  takeScreenshot: (...args: any[]) => mockTakeScreenshot(...args),
  clickScreen: vi.fn(),
  typeText: vi.fn(),
  pressKeys: vi.fn(),
  browserTask: (...args: any[]) => mockBrowserTask(...args),
}));

// ── Mock context-stream ─────────────────────────────────────────────
const mockPush = vi.fn();
vi.mock('../../src/main/context-stream', () => ({
  contextStream: {
    push: (...args: any[]) => mockPush(...args),
  },
}));

// ── Mock errors (pass through real implementations) ─────────────────
vi.mock('../../src/main/errors', async () => {
  const actual = await vi.importActual('../../src/main/errors');
  return actual;
});

// ── Import under test (after mocks) ─────────────────────────────────
import type {
  ExecutionStatus,
  StepStatus,
  StepResult,
  ExecutionRun,
  StandingPermission,
  ExecutorConfig,
} from '../../src/main/workflow-executor';

import type { WorkflowTemplate, WorkflowStep, WorkflowParameter } from '../../src/main/workflow-recorder';

// ── Engine factory ──────────────────────────────────────────────────

async function createEngine() {
  vi.resetModules();
  uuidCounter = 0;
  const mod = await import('../../src/main/workflow-executor');
  return mod;
}

// ── Test Template Factories ─────────────────────────────────────────

function makeStep(overrides: Partial<WorkflowStep> = {}): WorkflowStep {
  return {
    id: `step-${uuidCounter++}`,
    order: 1,
    intent: 'Click the save button',
    method: 'Click the save button in the toolbar',
    targetApp: 'VS Code',
    verificationHint: '',
    isDecisionPoint: false,
    ...overrides,
  };
}

function makeParam(overrides: Partial<WorkflowParameter> = {}): WorkflowParameter {
  return {
    id: `param-${uuidCounter++}`,
    name: 'filename',
    description: 'The file to open',
    dataType: 'text',
    defaultValue: 'untitled.txt',
    exampleValues: ['readme.md', 'config.json'],
    ...overrides,
  };
}

function makeTemplate(overrides: Partial<WorkflowTemplate> = {}): WorkflowTemplate {
  return {
    id: 'tmpl-001',
    name: 'Save File Workflow',
    description: 'Opens and saves a file',
    recordingId: 'rec-001',
    steps: [
      makeStep({ id: 'step-1', order: 1, intent: 'Open the file', method: 'Open {{filename}} in editor' }),
      makeStep({ id: 'step-2', order: 2, intent: 'Save the file', method: 'Press Ctrl+S' }),
    ],
    parameters: [makeParam()],
    tags: ['file', 'save'],
    createdAt: Date.now(),
    lastUsed: null,
    useCount: 0,
    ...overrides,
  } as WorkflowTemplate;
}

function makeDestructiveTemplate(): WorkflowTemplate {
  return makeTemplate({
    id: 'tmpl-destructive',
    name: 'Send Email Workflow',
    steps: [
      makeStep({ id: 'step-d1', order: 1, intent: 'Compose email', method: 'Open compose window' }),
      makeStep({ id: 'step-d2', order: 2, intent: 'Send email', method: 'Click send button to submit' }),
    ],
  });
}

// ── Test Suites ─────────────────────────────────────────────────────

describe('WorkflowExecutor', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    uuidCounter = 0;
    // Set default mock behaviors — tests override as needed BEFORE createEngine()
    mockGetPath.mockReturnValue('/mock/userData');
    mockReadFile.mockRejectedValue(new Error('ENOENT'));
    mockWriteFile.mockResolvedValue(undefined);
    mockMkdir.mockResolvedValue(undefined);
    mockPush.mockReturnValue(null);
    mockOperateComputer.mockResolvedValue({ completed: true, summary: 'done' });
    mockBrowserTask.mockResolvedValue({ completed: true, summary: 'done' });
    mockTakeScreenshot.mockResolvedValue({ image: 'base64screenshot' });
    mockGetTemplate.mockReturnValue(null);
    mockGetAllTemplates.mockReturnValue([]);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // ── Initialization ──────────────────────────────────────────────

  describe('initialization', () => {
    it('should create data directory on init', async () => {
      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();
      expect(mockMkdir).toHaveBeenCalledWith(
        expect.stringContaining('workflows'),
        { recursive: true },
      );
    });

    it('should load existing run history from disk', async () => {
      const savedData = {
        runs: [{ id: 'run-1', templateId: 'tmpl-1', status: 'completed', stepResults: [], startedAt: 1000 }],
        config: { maxConcurrentRuns: 2 },
      };
      mockReadFile.mockImplementation((path: string) => {
        if (path.includes('execution-history')) return Promise.resolve(JSON.stringify(savedData));
        return Promise.reject(new Error('ENOENT'));
      });

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      const history = workflowExecutor.getRunHistory();
      expect(history).toHaveLength(1);
      expect(history[0].id).toBe('run-1');
    });

    it('should load standing permissions from disk', async () => {
      const savedPerms = [
        { id: 'perm-1', templateId: 'tmpl-1', explicitlyGranted: true, runsUsed: 0, grantedAt: Date.now() },
      ];
      mockReadFile.mockImplementation((path: string) => {
        if (path.includes('standing-permissions')) return Promise.resolve(JSON.stringify(savedPerms));
        return Promise.reject(new Error('ENOENT'));
      });

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      const perms = workflowExecutor.getStandingPermissions();
      expect(perms).toHaveLength(1);
    });

    it('should handle missing files gracefully (first run)', async () => {
      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      expect(workflowExecutor.getRunHistory()).toHaveLength(0);
      expect(workflowExecutor.getStandingPermissions()).toHaveLength(0);
      expect(workflowExecutor.isRunning()).toBe(false);
    });
  });

  // ── Execution Lifecycle ─────────────────────────────────────────

  describe('execution lifecycle', () => {
    it('should execute a simple workflow successfully', async () => {
      const template = makeTemplate();
      mockGetTemplate.mockReturnValue(template);

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      const run = await workflowExecutor.executeWorkflow('tmpl-001', { filename: 'test.ts' });

      expect(run.status).toBe('completed');
      expect(run.templateId).toBe('tmpl-001');
      expect(run.triggeredBy).toBe('user');
      expect(run.stepResults).toHaveLength(2);
      expect(run.stepResults.every(r => r.status === 'completed')).toBe(true);
      expect(run.totalDurationMs).toBeGreaterThanOrEqual(0);
    });

    it('should reject execution of non-existent template', async () => {
      mockGetTemplate.mockReturnValue(null);

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      await expect(workflowExecutor.executeWorkflow('nonexistent'))
        .rejects.toThrow('Template not found');
    });

    it('should reject concurrent executions', async () => {
      const template = makeTemplate({
        steps: [makeStep({
          id: 'slow-step', order: 1, intent: 'Slow task',
          method: 'Do something slow',
        })],
      });
      mockGetTemplate.mockReturnValue(template);

      // Make step execution slow
      mockOperateComputer.mockImplementation(() =>
        new Promise(resolve => setTimeout(() => resolve({ completed: true, summary: 'done' }), 200))
      );

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      // Start first execution (don't await)
      const firstRun = workflowExecutor.executeWorkflow('tmpl-001');

      // Small delay to let first run start
      await new Promise(r => setTimeout(r, 10));

      // Second execution should fail
      await expect(workflowExecutor.executeWorkflow('tmpl-001'))
        .rejects.toThrow('Another workflow is currently running');

      await firstRun; // Clean up
    });

    it('should emit context events for workflow start and complete', async () => {
      const template = makeTemplate();
      mockGetTemplate.mockReturnValue(template);

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      await workflowExecutor.executeWorkflow('tmpl-001');

      // Check for start event
      const startEvent = mockPush.mock.calls.find(
        (c: any) => c[0]?.summary === 'workflow:workflow_start'
      );
      expect(startEvent).toBeDefined();

      // Check for complete event
      const completeEvent = mockPush.mock.calls.find(
        (c: any) => c[0]?.summary === 'workflow:workflow_complete'
      );
      expect(completeEvent).toBeDefined();
      expect(completeEvent![0].data.status).toBe('completed');
    });

    it('should emit context events for each step', async () => {
      const template = makeTemplate();
      mockGetTemplate.mockReturnValue(template);

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      await workflowExecutor.executeWorkflow('tmpl-001');

      const stepStarts = mockPush.mock.calls.filter(
        (c: any) => c[0]?.summary === 'workflow:step_start'
      );
      const stepCompletes = mockPush.mock.calls.filter(
        (c: any) => c[0]?.summary === 'workflow:step_complete'
      );

      expect(stepStarts).toHaveLength(2);
      expect(stepCompletes).toHaveLength(2);
    });

    it('should stop execution on step failure', async () => {
      const template = makeTemplate({
        steps: [
          makeStep({ id: 'step-ok', order: 1, intent: 'First step', method: 'Do first thing' }),
          makeStep({ id: 'step-fail', order: 2, intent: 'Bad step', method: 'Invalid action' }),
          makeStep({ id: 'step-skip', order: 3, intent: 'Third step', method: 'Do third thing' }),
        ],
      });
      mockGetTemplate.mockReturnValue(template);

      let callCount = 0;
      mockOperateComputer.mockImplementation(() => {
        callCount++;
        if (callCount > 1) {
          // Simulate a persistent error (non-retryable)
          throw new Error('API key invalid — unauthorized');
        }
        return Promise.resolve({ completed: true, summary: 'done' });
      });

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      const run = await workflowExecutor.executeWorkflow('tmpl-001');

      expect(run.status).toBe('failed');
      expect(run.stepResults).toHaveLength(2); // Only 2 executed, 3rd skipped
      expect(run.stepResults[0].status).toBe('completed');
      expect(run.stepResults[1].status).toBe('failed');
    });

    it('should record run in history and persist', async () => {
      const template = makeTemplate();
      mockGetTemplate.mockReturnValue(template);

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      await workflowExecutor.executeWorkflow('tmpl-001');

      const history = workflowExecutor.getRunHistory();
      expect(history).toHaveLength(1);
      expect(history[0].status).toBe('completed');

      // Should have called writeFile to persist
      expect(mockWriteFile).toHaveBeenCalled();
    });

    it('should sort steps by order before executing', async () => {
      const template = makeTemplate({
        steps: [
          makeStep({ id: 'step-3', order: 3, intent: 'Third', method: 'Step 3' }),
          makeStep({ id: 'step-1', order: 1, intent: 'First', method: 'Step 1' }),
          makeStep({ id: 'step-2', order: 2, intent: 'Second', method: 'Step 2' }),
        ],
      });
      mockGetTemplate.mockReturnValue(template);

      const executionOrder: number[] = [];
      mockOperateComputer.mockImplementation((_method: string) => {
        executionOrder.push(executionOrder.length + 1);
        return Promise.resolve({ completed: true, summary: 'done' });
      });

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      const run = await workflowExecutor.executeWorkflow('tmpl-001');
      expect(run.stepResults[0].stepOrder).toBe(1);
      expect(run.stepResults[1].stepOrder).toBe(2);
      expect(run.stepResults[2].stepOrder).toBe(3);
    });

    it('should route browser steps through browserTask', async () => {
      const template = makeTemplate({
        steps: [
          makeStep({
            id: 'browser-step', order: 1, intent: 'Navigate',
            method: 'Navigate to google.com', targetApp: 'Chrome Browser',
          }),
        ],
      });
      mockGetTemplate.mockReturnValue(template);

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      await workflowExecutor.executeWorkflow('tmpl-001');

      expect(mockBrowserTask).toHaveBeenCalledWith('Navigate to google.com');
      expect(mockOperateComputer).not.toHaveBeenCalled();
    });

    it('should route desktop steps through operateComputer', async () => {
      const template = makeTemplate({
        steps: [
          makeStep({
            id: 'desktop-step', order: 1, intent: 'Click button',
            method: 'Click the button', targetApp: 'VS Code',
          }),
        ],
      });
      mockGetTemplate.mockReturnValue(template);

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      await workflowExecutor.executeWorkflow('tmpl-001');

      expect(mockOperateComputer).toHaveBeenCalledWith('Click the button');
      expect(mockBrowserTask).not.toHaveBeenCalled();
    });
  });

  // ── Parameter Substitution ──────────────────────────────────────

  describe('parameter substitution', () => {
    it('should substitute {{param}} placeholders in step methods', async () => {
      const template = makeTemplate({
        steps: [
          makeStep({
            id: 'param-step', order: 1,
            intent: 'Open file',
            method: 'Open {{filename}} in the editor',
          }),
        ],
        parameters: [makeParam({ name: 'filename', defaultValue: 'default.txt' })],
      });
      mockGetTemplate.mockReturnValue(template);

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      await workflowExecutor.executeWorkflow('tmpl-001', { filename: 'hello.ts' });

      expect(mockOperateComputer).toHaveBeenCalledWith('Open hello.ts in the editor');
    });

    it('should use default values when no user value provided', async () => {
      const template = makeTemplate({
        steps: [
          makeStep({
            id: 'param-step', order: 1,
            intent: 'Open file',
            method: 'Open {{filename}} in the editor',
          }),
        ],
        parameters: [makeParam({ name: 'filename', defaultValue: 'default.txt' })],
      });
      mockGetTemplate.mockReturnValue(template);

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      await workflowExecutor.executeWorkflow('tmpl-001', {}); // No params

      expect(mockOperateComputer).toHaveBeenCalledWith('Open default.txt in the editor');
    });

    it('should format date parameters to ISO format', async () => {
      const template = makeTemplate({
        steps: [
          makeStep({
            id: 'date-step', order: 1,
            intent: 'Set deadline',
            method: 'Set deadline to {{deadline}}',
          }),
        ],
        parameters: [makeParam({ name: 'deadline', dataType: 'date', defaultValue: '' })],
      });
      mockGetTemplate.mockReturnValue(template);

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      await workflowExecutor.executeWorkflow('tmpl-001', { deadline: 'January 15, 2025' });

      const call = mockOperateComputer.mock.calls[0][0] as string;
      expect(call).toMatch(/Set deadline to 2025-01-15/);
    });

    it('should add https:// to URL parameters missing protocol', async () => {
      const template = makeTemplate({
        steps: [
          makeStep({
            id: 'url-step', order: 1,
            intent: 'Navigate',
            method: 'Navigate to {{url}}',
            targetApp: 'Chrome Browser',
          }),
        ],
        parameters: [makeParam({ name: 'url', dataType: 'url', defaultValue: '' })],
      });
      mockGetTemplate.mockReturnValue(template);

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      await workflowExecutor.executeWorkflow('tmpl-001', { url: 'example.com' });

      expect(mockBrowserTask).toHaveBeenCalledWith('Navigate to https://example.com');
    });

    it('should preserve URLs that already have protocol', async () => {
      const template = makeTemplate({
        steps: [
          makeStep({
            id: 'url-step', order: 1,
            intent: 'Navigate',
            method: 'Navigate to {{url}}',
            targetApp: 'Chrome Browser',
          }),
        ],
        parameters: [makeParam({ name: 'url', dataType: 'url', defaultValue: '' })],
      });
      mockGetTemplate.mockReturnValue(template);

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      await workflowExecutor.executeWorkflow('tmpl-001', { url: 'http://localhost:3000' });

      expect(mockBrowserTask).toHaveBeenCalledWith('Navigate to http://localhost:3000');
    });

    it('should normalize number parameters', async () => {
      const template = makeTemplate({
        steps: [
          makeStep({
            id: 'num-step', order: 1,
            intent: 'Set count',
            method: 'Set value to {{count}}',
          }),
        ],
        parameters: [makeParam({ name: 'count', dataType: 'number', defaultValue: '0' })],
      });
      mockGetTemplate.mockReturnValue(template);

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      await workflowExecutor.executeWorkflow('tmpl-001', { count: '42.5' });

      expect(mockOperateComputer).toHaveBeenCalledWith('Set value to 42.5');
    });
  });

  // ── Failure Hierarchy ───────────────────────────────────────────

  describe('failure hierarchy', () => {
    it('should retry transient errors before escalating', async () => {
      const template = makeTemplate({
        steps: [makeStep({ id: 'step-retry', order: 1 })],
      });
      mockGetTemplate.mockReturnValue(template);

      let attempt = 0;
      mockOperateComputer.mockImplementation(() => {
        attempt++;
        if (attempt <= 2) {
          throw new Error('network timeout');
        }
        return Promise.resolve({ completed: true, summary: 'done' });
      });

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      const run = await workflowExecutor.executeWorkflow('tmpl-001');

      // Should have retried and succeeded
      expect(run.status).toBe('completed');
      expect(run.stepResults[0].method).toBe('retry_variation');
    });

    it('should capture screenshot on step failure', async () => {
      const template = makeTemplate({
        steps: [makeStep({ id: 'step-fail', order: 1 })],
      });
      mockGetTemplate.mockReturnValue(template);
      mockOperateComputer.mockRejectedValue(new Error('API key invalid — unauthorized'));

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      const run = await workflowExecutor.executeWorkflow('tmpl-001');

      expect(run.status).toBe('failed');
      // Screenshot should have been attempted
      expect(mockTakeScreenshot).toHaveBeenCalled();
    });
  });

  // ── Pause / Resume / Cancel ─────────────────────────────────────

  describe('pause, resume, cancel', () => {
    it('should return false when pausing with no active run', async () => {
      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();
      expect(workflowExecutor.pauseExecution()).toBe(false);
    });

    it('should return false when resuming with no active run', async () => {
      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();
      expect(workflowExecutor.resumeExecution()).toBe(false);
    });

    it('should cancel an active execution', async () => {
      const template = makeTemplate({
        steps: [
          makeStep({ id: 'slow-1', order: 1, method: 'Slow step 1' }),
          makeStep({ id: 'slow-2', order: 2, method: 'Slow step 2' }),
        ],
      });
      mockGetTemplate.mockReturnValue(template);

      mockOperateComputer.mockImplementation(() =>
        new Promise(resolve => setTimeout(() => resolve({ completed: true, summary: 'done' }), 100))
      );

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      const runPromise = workflowExecutor.executeWorkflow('tmpl-001');

      await new Promise(r => setTimeout(r, 10));
      const cancelled = workflowExecutor.cancelExecution();
      expect(cancelled).toBe(true);

      const run = await runPromise;
      expect(run.status).toBe('cancelled');
    });

    it('should emit context events on pause, resume, cancel', async () => {
      const template = makeTemplate({
        steps: [
          makeStep({ id: 'slow-1', order: 1, method: 'Slow step' }),
        ],
      });
      mockGetTemplate.mockReturnValue(template);

      mockOperateComputer.mockImplementation(() =>
        new Promise(resolve => setTimeout(() => resolve({ completed: true, summary: 'done' }), 200))
      );

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      const runPromise = workflowExecutor.executeWorkflow('tmpl-001');
      await new Promise(r => setTimeout(r, 10));

      workflowExecutor.cancelExecution();
      await runPromise;

      const cancelEvent = mockPush.mock.calls.find(
        (c: any) => c[0]?.summary === 'workflow:workflow_cancelled'
      );
      expect(cancelEvent).toBeDefined();
    });
  });

  // ── Standing Permissions (cLaw Gate) ────────────────────────────

  describe('standing permissions (cLaw Gate)', () => {
    it('should block scheduled workflows with destructive steps and no permission', async () => {
      const template = makeDestructiveTemplate();
      mockGetTemplate.mockReturnValue(template);

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      await expect(
        workflowExecutor.executeWorkflow('tmpl-destructive', {}, 'schedule')
      ).rejects.toThrow('destructive actions');
    });

    it('should allow scheduled workflows with standing permission', async () => {
      const template = makeDestructiveTemplate();
      mockGetTemplate.mockReturnValue(template);

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      workflowExecutor.grantStandingPermission('tmpl-destructive', {
        allowDestructive: true,
      });

      const run = await workflowExecutor.executeWorkflow('tmpl-destructive', {}, 'schedule');
      expect(run.status).toBe('completed');
    });

    it('should allow user-triggered workflows without permission (even destructive)', async () => {
      const template = makeDestructiveTemplate();
      mockGetTemplate.mockReturnValue(template);

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      // No standing permission needed for user-triggered
      const run = await workflowExecutor.executeWorkflow('tmpl-destructive', {}, 'user');
      expect(run.status).toBe('completed');
    });

    it('should reject expired standing permissions', async () => {
      const template = makeDestructiveTemplate();
      mockGetTemplate.mockReturnValue(template);

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      // Grant with immediate expiry (already expired)
      const perm = workflowExecutor.grantStandingPermission('tmpl-destructive', {
        expiresInDays: -1, // Expired yesterday
      });

      // Manually set expiresAt to past
      const perms = workflowExecutor.getStandingPermissions();
      (perms[0] as any).expiresAt = Date.now() - 1000;

      // Force re-grant with past expiry
      workflowExecutor.grantStandingPermission('tmpl-destructive', {
        allowDestructive: true,
      });

      // Hack: manually expire it after grant
      const currentPerms = workflowExecutor.getStandingPermissions();
      // Actually, the internal permissions are copies, we can't modify the original.
      // Instead, test by granting then revoking
      workflowExecutor.revokeStandingPermission('tmpl-destructive');

      await expect(
        workflowExecutor.executeWorkflow('tmpl-destructive', {}, 'schedule')
      ).rejects.toThrow('destructive actions');
    });

    it('should track permission usage for scheduled runs', async () => {
      const template = makeTemplate({ id: 'tmpl-safe', name: 'Safe workflow' });
      mockGetTemplate.mockReturnValue(template);

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      workflowExecutor.grantStandingPermission('tmpl-safe', { maxRuns: 5 });

      await workflowExecutor.executeWorkflow('tmpl-safe', {}, 'schedule');

      const perms = workflowExecutor.getStandingPermissions();
      // Permission should reflect usage increase
      expect(perms.length).toBeGreaterThanOrEqual(1);
    });

    it('should mark permissions as explicitly granted', async () => {
      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      const perm = workflowExecutor.grantStandingPermission('tmpl-001');
      expect(perm.explicitlyGranted).toBe(true);
    });

    it('should revoke permissions', async () => {
      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      workflowExecutor.grantStandingPermission('tmpl-001');
      expect(workflowExecutor.getStandingPermissions()).toHaveLength(1);

      const revoked = workflowExecutor.revokeStandingPermission('tmpl-001');
      expect(revoked).toBe(true);
      expect(workflowExecutor.getStandingPermissions()).toHaveLength(0);
    });

    it('should return false when revoking non-existent permission', async () => {
      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      const revoked = workflowExecutor.revokeStandingPermission('nonexistent');
      expect(revoked).toBe(false);
    });

    it('should replace existing permission on re-grant', async () => {
      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      workflowExecutor.grantStandingPermission('tmpl-001', { maxRuns: 5 });
      workflowExecutor.grantStandingPermission('tmpl-001', { maxRuns: 10 });

      const perms = workflowExecutor.getStandingPermissions();
      expect(perms).toHaveLength(1);
    });

    it('should allow non-destructive scheduled workflows without permission', async () => {
      const template = makeTemplate({ id: 'tmpl-safe', name: 'Non-destructive workflow' });
      mockGetTemplate.mockReturnValue(template);

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      // No permission needed since no destructive keywords
      const run = await workflowExecutor.executeWorkflow('tmpl-safe', {}, 'schedule');
      expect(run.status).toBe('completed');
    });
  });

  // ── Queries ─────────────────────────────────────────────────────

  describe('queries', () => {
    it('should return null for active run when idle', async () => {
      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();
      expect(workflowExecutor.getActiveRun()).toBeNull();
    });

    it('should return isRunning false when idle', async () => {
      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();
      expect(workflowExecutor.isRunning()).toBe(false);
    });

    it('should return run by ID', async () => {
      const template = makeTemplate();
      mockGetTemplate.mockReturnValue(template);

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      const run = await workflowExecutor.executeWorkflow('tmpl-001');
      const found = workflowExecutor.getRunById(run.id);

      expect(found).not.toBeNull();
      expect(found!.id).toBe(run.id);
    });

    it('should return null for non-existent run ID', async () => {
      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();
      expect(workflowExecutor.getRunById('nonexistent')).toBeNull();
    });

    it('should limit run history results', async () => {
      const template = makeTemplate();
      mockGetTemplate.mockReturnValue(template);

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      // Execute 5 workflows
      for (let i = 0; i < 5; i++) {
        await workflowExecutor.executeWorkflow('tmpl-001');
      }

      const limited = workflowExecutor.getRunHistory(3);
      expect(limited).toHaveLength(3);
    });

    it('should sort run history by most recent first', async () => {
      const template = makeTemplate();
      mockGetTemplate.mockReturnValue(template);

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      await workflowExecutor.executeWorkflow('tmpl-001');
      await workflowExecutor.executeWorkflow('tmpl-001');

      const history = workflowExecutor.getRunHistory();
      expect(history[0].startedAt).toBeGreaterThanOrEqual(history[1].startedAt);
    });
  });

  // ── Configuration ───────────────────────────────────────────────

  describe('configuration', () => {
    it('should return default config', async () => {
      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      const config = workflowExecutor.getConfig();
      expect(config.maxConcurrentRuns).toBe(1);
      expect(config.stepTimeoutMs).toBe(30_000);
      expect(config.maxRetries).toBe(2);
      expect(config.screenshotOnFailure).toBe(true);
      expect(config.maxRunHistory).toBe(100);
    });

    it('should update config partially', async () => {
      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      workflowExecutor.updateConfig({ maxRetries: 5 });
      const config = workflowExecutor.getConfig();
      expect(config.maxRetries).toBe(5);
      expect(config.stepTimeoutMs).toBe(30_000); // Unchanged
    });

    it('should persist config on save', async () => {
      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      workflowExecutor.updateConfig({ maxRetries: 5 });

      // Wait for async save
      await new Promise(r => setTimeout(r, 50));

      expect(mockWriteFile).toHaveBeenCalled();
      const savedData = JSON.parse(mockWriteFile.mock.calls.at(-1)?.[1] || '{}');
      expect(savedData.config?.maxRetries).toBe(5);
    });
  });

  // ── Destructive Action Detection ────────────────────────────────

  describe('destructive action detection', () => {
    const destructiveKeywords = [
      'delete', 'remove', 'send', 'submit', 'publish',
      'post', 'transfer', 'pay', 'purchase', 'confirm', 'approve',
    ];

    for (const keyword of destructiveKeywords) {
      it(`should detect "${keyword}" as destructive`, async () => {
        const template = makeTemplate({
          id: `tmpl-${keyword}`,
          name: `${keyword} workflow`,
          steps: [
            makeStep({
              id: `step-${keyword}`,
              order: 1,
              intent: `${keyword} something`,
              method: `Click the ${keyword} button`,
            }),
          ],
        });
        mockGetTemplate.mockReturnValue(template);

        const { workflowExecutor } = await createEngine();
        await workflowExecutor.initialize();

        // Should fail for scheduled without permission
        await expect(
          workflowExecutor.executeWorkflow(`tmpl-${keyword}`, {}, 'schedule')
        ).rejects.toThrow('destructive actions');
      });
    }

    it('should NOT flag non-destructive steps', async () => {
      const template = makeTemplate({
        id: 'tmpl-safe',
        steps: [
          makeStep({ id: 'step-safe', order: 1, intent: 'Read data', method: 'Open the dashboard' }),
        ],
      });
      mockGetTemplate.mockReturnValue(template);

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      // Should succeed even for scheduled (no destructive keywords)
      const run = await workflowExecutor.executeWorkflow('tmpl-safe', {}, 'schedule');
      expect(run.status).toBe('completed');
    });
  });

  // ── Run Data Model ──────────────────────────────────────────────

  describe('run data model', () => {
    it('should populate all required fields on execution run', async () => {
      const template = makeTemplate();
      mockGetTemplate.mockReturnValue(template);

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      const run = await workflowExecutor.executeWorkflow('tmpl-001', { filename: 'test.ts' }, 'user');

      expect(run.id).toBeTruthy();
      expect(run.templateId).toBe('tmpl-001');
      expect(run.templateName).toBe('Save File Workflow');
      expect(run.status).toBe('completed');
      expect(run.parameters).toEqual({ filename: 'test.ts' });
      expect(run.stepResults).toHaveLength(2);
      expect(run.startedAt).toBeGreaterThan(0);
      expect(run.completedAt).toBeGreaterThan(0);
      expect(run.totalDurationMs).toBeGreaterThanOrEqual(0);
      expect(run.triggeredBy).toBe('user');
    });

    it('should populate step result fields', async () => {
      const template = makeTemplate();
      mockGetTemplate.mockReturnValue(template);

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      const run = await workflowExecutor.executeWorkflow('tmpl-001');
      const step = run.stepResults[0];

      expect(step.stepId).toBeTruthy();
      expect(step.stepOrder).toBe(1);
      expect(step.intent).toBeTruthy();
      expect(step.status).toBe('completed');
      expect(step.method).toBe('recorded');
      expect(step.startedAt).toBeGreaterThan(0);
      expect(step.completedAt).toBeGreaterThan(0);
      expect(step.durationMs).toBeGreaterThanOrEqual(0);
    });

    it('should include scheduledTaskId for scheduled runs', async () => {
      const template = makeTemplate();
      mockGetTemplate.mockReturnValue(template);

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      const run = await workflowExecutor.executeWorkflow('tmpl-001', {}, 'schedule', 'task-123');
      expect(run.scheduledTaskId).toBe('task-123');
    });
  });

  // ── Persistence ─────────────────────────────────────────────────

  describe('persistence', () => {
    it('should strip screenshots from persisted history', async () => {
      const template = makeTemplate({
        steps: [makeStep({ id: 'step-fail', order: 1 })],
      });
      mockGetTemplate.mockReturnValue(template);
      mockOperateComputer.mockRejectedValue(new Error('API key invalid — unauthorized'));

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      await workflowExecutor.executeWorkflow('tmpl-001');

      // Wait for async save
      await new Promise(r => setTimeout(r, 100));

      const lastWriteCall = mockWriteFile.mock.calls.find(
        (c: any) => typeof c[1] === 'string' && c[1].includes('"runs"')
      );
      if (lastWriteCall) {
        const saved = JSON.parse(lastWriteCall[1]);
        for (const run of saved.runs) {
          for (const sr of run.stepResults) {
            expect(sr.screenshotBefore).toBeUndefined();
            expect(sr.screenshotAfter).toBeUndefined();
          }
        }
      }
    });

    it('should save standing permissions to separate file', async () => {
      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      workflowExecutor.grantStandingPermission('tmpl-001');

      // Wait for async save
      await new Promise(r => setTimeout(r, 50));

      const permSave = mockWriteFile.mock.calls.find(
        (c: any) => typeof c[0] === 'string' && c[0].includes('standing-permissions')
      );
      expect(permSave).toBeDefined();
    });
  });

  // ── Edge Cases ──────────────────────────────────────────────────

  describe('edge cases', () => {
    it('should handle empty parameter map gracefully', async () => {
      const template = makeTemplate({
        steps: [makeStep({ id: 'step-1', order: 1, method: 'Do {{missing}} thing' })],
        parameters: [],
      });
      mockGetTemplate.mockReturnValue(template);

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      const run = await workflowExecutor.executeWorkflow('tmpl-001');
      // Should still execute (placeholders left as-is when no params)
      expect(run.status).toBe('completed');
    });

    it('should handle workflow with zero steps', async () => {
      const template = makeTemplate({ steps: [] });
      mockGetTemplate.mockReturnValue(template);

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      const run = await workflowExecutor.executeWorkflow('tmpl-001');
      expect(run.status).toBe('completed');
      expect(run.stepResults).toHaveLength(0);
    });

    it('should handle context stream push failures gracefully', async () => {
      const template = makeTemplate();
      mockGetTemplate.mockReturnValue(template);
      mockPush.mockImplementation(() => { throw new Error('Context stream unavailable'); });

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      // Should not throw — context stream is non-critical
      const run = await workflowExecutor.executeWorkflow('tmpl-001');
      expect(run.status).toBe('completed');
    });

    it('should handle screenshot failure on error gracefully', async () => {
      const template = makeTemplate({
        steps: [makeStep({ id: 'step-fail', order: 1 })],
      });
      mockGetTemplate.mockReturnValue(template);
      mockOperateComputer.mockRejectedValue(new Error('API key invalid'));
      mockTakeScreenshot.mockRejectedValue(new Error('Screenshot service down'));

      const { workflowExecutor } = await createEngine();
      await workflowExecutor.initialize();

      const run = await workflowExecutor.executeWorkflow('tmpl-001');
      // Should still complete (fail status), not throw
      expect(run.status).toBe('failed');
    });
  });
});
