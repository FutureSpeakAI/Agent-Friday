/**
 * IPC handlers for Workflow Recorder — Track V Phase 1.
 * Exposes recording control, event capture, template management,
 * and query APIs to the renderer.
 *
 * cLaw Gate: Recording MUST be explicitly started by the user.
 * The recorder never activates autonomously. Template creation
 * is a read-only abstraction. No replay or execution occurs here.
 */

import { ipcMain } from 'electron';
import { workflowRecorder } from '../workflow-recorder';
import type { EventType } from '../workflow-recorder';

export function registerWorkflowRecorderHandlers(): void {
  // ── Recording Control ───────────────────────────────────────────

  ipcMain.handle('workflow:start-recording', (_event, name: string) => {
    if (typeof name !== 'string' || !name.trim()) {
      throw new Error('workflow:start-recording requires a non-empty name');
    }
    return workflowRecorder.startRecording(name.trim());
  });

  ipcMain.handle('workflow:stop-recording', () => {
    return workflowRecorder.stopRecording();
  });

  ipcMain.handle('workflow:cancel-recording', () => {
    return workflowRecorder.cancelRecording();
  });

  // ── Event Capture ─────────────────────────────────────────────

  ipcMain.handle(
    'workflow:record-event',
    (_event, type: EventType, description: string, payload?: {
      activeApp?: string;
      windowTitle?: string;
      data?: string;
      durationMs?: number;
    }) => {
      if (!type || !description) {
        throw new Error('workflow:record-event requires type and description');
      }
      return workflowRecorder.recordEvent({
        type,
        description,
        activeApp: payload?.activeApp || '',
        windowTitle: payload?.windowTitle || '',
        payload: payload?.data,
        durationMs: payload?.durationMs,
      });
    }
  );

  ipcMain.handle('workflow:add-annotation', (_event, text: string) => {
    if (typeof text !== 'string' || !text.trim()) {
      throw new Error('workflow:add-annotation requires non-empty text');
    }
    return workflowRecorder.addAnnotation(text.trim());
  });

  ipcMain.handle(
    'workflow:add-keyframe',
    (_event, filePath: string, activeApp: string) => {
      if (typeof filePath !== 'string' || !filePath) {
        throw new Error('workflow:add-keyframe requires a filePath');
      }
      return workflowRecorder.addKeyFrame(filePath, activeApp || '');
    }
  );

  // ── Template Management ───────────────────────────────────────

  ipcMain.handle(
    'workflow:create-template',
    (_event, recordingId: string, overrides?: Record<string, unknown>) => {
      if (typeof recordingId !== 'string' || !recordingId) {
        throw new Error('workflow:create-template requires a recordingId');
      }
      return workflowRecorder.createTemplate(
        recordingId,
        overrides as Parameters<typeof workflowRecorder.createTemplate>[1]
      );
    }
  );

  ipcMain.handle('workflow:delete-template', (_event, id: string) => {
    if (typeof id !== 'string' || !id) {
      throw new Error('workflow:delete-template requires an id');
    }
    return workflowRecorder.deleteTemplate(id);
  });

  // ── Queries ───────────────────────────────────────────────────

  ipcMain.handle('workflow:status', () => {
    return workflowRecorder.getStatus();
  });

  ipcMain.handle('workflow:get-recording', (_event, id: string) => {
    if (typeof id !== 'string' || !id) {
      throw new Error('workflow:get-recording requires an id');
    }
    return workflowRecorder.getRecording(id);
  });

  ipcMain.handle('workflow:get-all-recordings', () => {
    return workflowRecorder.getAllRecordings();
  });

  ipcMain.handle('workflow:get-recent-recordings', (_event, limit?: number) => {
    return workflowRecorder.getRecentRecordings(
      typeof limit === 'number' ? limit : 10
    );
  });

  ipcMain.handle('workflow:get-template', (_event, id: string) => {
    if (typeof id !== 'string' || !id) {
      throw new Error('workflow:get-template requires an id');
    }
    return workflowRecorder.getTemplate(id);
  });

  ipcMain.handle('workflow:get-all-templates', () => {
    return workflowRecorder.getAllTemplates();
  });

  ipcMain.handle('workflow:get-templates-by-tag', (_event, tag: string) => {
    if (typeof tag !== 'string' || !tag) {
      throw new Error('workflow:get-templates-by-tag requires a tag');
    }
    return workflowRecorder.getTemplatesByTag(tag);
  });

  ipcMain.handle('workflow:delete-recording', (_event, id: string) => {
    if (typeof id !== 'string' || !id) {
      throw new Error('workflow:delete-recording requires an id');
    }
    return workflowRecorder.deleteRecording(id);
  });

  ipcMain.handle('workflow:config', () => {
    return workflowRecorder.getConfig();
  });
}
