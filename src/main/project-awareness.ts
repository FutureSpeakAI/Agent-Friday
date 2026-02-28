/**
 * project-awareness.ts — Project Awareness Engine for EVE OS.
 *
 * Watches project directories for changes, detects project type from
 * config files (package.json, requirements.txt, .git, etc.), and builds
 * a project profile including structure, key files, recent changes,
 * and git branch/status.
 *
 * Exposes getContextString() for injection into personality.ts and
 * Gemini tools for watching/querying projects.
 */

import { BrowserWindow } from 'electron';
import fs from 'fs/promises';
import path from 'path';
import { exec } from 'child_process';
import { promisify } from 'util';

const execAsync = promisify(exec);

export interface ProjectProfile {
  id: string;
  name: string;
  rootPath: string;
  type: 'node' | 'python' | 'rust' | 'go' | 'java' | 'unknown';
  framework?: string;
  description?: string;
  gitBranch?: string;
  gitStatus?: string;
  recentChanges: string[];
  keyFiles: string[];
  structure: string[];
  lastScanned: number;
}

const MAX_PROJECTS = 5;
const RESCAN_INTERVAL_MS = 5 * 60 * 1000; // 5 minutes

class ProjectAwareness {
  private projects: Map<string, ProjectProfile> = new Map();
  private watchers: Map<string, fs.FileHandle | null> = new Map();
  private timer: ReturnType<typeof setInterval> | null = null;
  private mainWindow: BrowserWindow | null = null;

  initialize(mainWindow: BrowserWindow): void {
    this.mainWindow = mainWindow;

    // Periodic rescan of watched projects
    this.timer = setInterval(() => {
      for (const [, profile] of this.projects) {
        this.scanProject(profile.rootPath).catch(() => {});
      }
    }, RESCAN_INTERVAL_MS);

    console.log('[ProjectAwareness] Initialized');
  }

  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  getProjects(): ProjectProfile[] {
    return Array.from(this.projects.values());
  }

  getProject(rootPath: string): ProjectProfile | undefined {
    return this.projects.get(rootPath);
  }

  /**
   * Watch a project directory — scans it immediately and monitors for changes.
   */
  async watchProject(rootPath: string): Promise<ProjectProfile> {
    const normalized = path.resolve(rootPath);

    // Check directory exists
    const stat = await fs.stat(normalized);
    if (!stat.isDirectory()) {
      throw new Error(`Not a directory: ${normalized}`);
    }

    // Cap projects
    if (this.projects.size >= MAX_PROJECTS && !this.projects.has(normalized)) {
      // Remove oldest
      const oldest = Array.from(this.projects.entries())
        .sort(([, a], [, b]) => a.lastScanned - b.lastScanned)[0];
      if (oldest) {
        this.projects.delete(oldest[0]);
      }
    }

    const profile = await this.scanProject(normalized);

    // Emit to renderer
    this.mainWindow?.webContents.send('project:updated', profile);

    return profile;
  }

  /**
   * Scan a project directory and build/update its profile.
   */
  async scanProject(rootPath: string): Promise<ProjectProfile> {
    const normalized = path.resolve(rootPath);
    const name = path.basename(normalized);

    let type: ProjectProfile['type'] = 'unknown';
    let framework: string | undefined;
    let description: string | undefined;
    const keyFiles: string[] = [];
    const structure: string[] = [];

    // Detect project type from config files
    try {
      const entries = await fs.readdir(normalized, { withFileTypes: true });
      const fileNames = entries.map((e) => e.name);

      // Top-level structure
      for (const entry of entries.slice(0, 30)) {
        const prefix = entry.isDirectory() ? '[DIR]' : '[FILE]';
        structure.push(`${prefix} ${entry.name}`);
      }
      if (entries.length > 30) {
        structure.push(`... and ${entries.length - 30} more`);
      }

      // Node.js / TypeScript
      if (fileNames.includes('package.json')) {
        type = 'node';
        keyFiles.push('package.json');
        try {
          const pkg = JSON.parse(
            await fs.readFile(path.join(normalized, 'package.json'), 'utf-8')
          );
          description = pkg.description;
          // Detect framework
          const allDeps = { ...pkg.dependencies, ...pkg.devDependencies };
          if (allDeps['next']) framework = 'Next.js';
          else if (allDeps['nuxt']) framework = 'Nuxt';
          else if (allDeps['react']) framework = 'React';
          else if (allDeps['vue']) framework = 'Vue';
          else if (allDeps['svelte']) framework = 'Svelte';
          else if (allDeps['electron']) framework = 'Electron';
          else if (allDeps['express']) framework = 'Express';
          else if (allDeps['fastify']) framework = 'Fastify';
        } catch {
          // malformed package.json
        }
      }

      // Python
      if (fileNames.includes('requirements.txt') || fileNames.includes('pyproject.toml') || fileNames.includes('setup.py')) {
        type = 'python';
        if (fileNames.includes('requirements.txt')) keyFiles.push('requirements.txt');
        if (fileNames.includes('pyproject.toml')) keyFiles.push('pyproject.toml');
        // Detect framework
        try {
          const reqFile = fileNames.includes('requirements.txt')
            ? await fs.readFile(path.join(normalized, 'requirements.txt'), 'utf-8')
            : '';
          if (reqFile.includes('django')) framework = 'Django';
          else if (reqFile.includes('flask')) framework = 'Flask';
          else if (reqFile.includes('fastapi')) framework = 'FastAPI';
        } catch { /* file read failed */ }
      }

      // Rust
      if (fileNames.includes('Cargo.toml')) {
        type = 'rust';
        keyFiles.push('Cargo.toml');
      }

      // Go
      if (fileNames.includes('go.mod')) {
        type = 'go';
        keyFiles.push('go.mod');
      }

      // Java
      if (fileNames.includes('pom.xml') || fileNames.includes('build.gradle')) {
        type = 'java';
        if (fileNames.includes('pom.xml')) keyFiles.push('pom.xml');
        if (fileNames.includes('build.gradle')) keyFiles.push('build.gradle');
      }

      // Common key files
      const commonKeys = ['README.md', 'tsconfig.json', '.env', 'Dockerfile', '.gitignore', 'Makefile'];
      for (const f of commonKeys) {
        if (fileNames.includes(f)) keyFiles.push(f);
      }
    } catch (err) {
      console.warn(`[ProjectAwareness] Failed to read directory: ${normalized}`, err);
    }

    // Git info
    let gitBranch: string | undefined;
    let gitStatus: string | undefined;
    const recentChanges: string[] = [];

    try {
      const { stdout: branch } = await execAsync('git rev-parse --abbrev-ref HEAD', {
        cwd: normalized,
      });
      gitBranch = branch.trim();

      const { stdout: status } = await execAsync('git status --short', {
        cwd: normalized,
      });
      const statusLines = status.trim().split('\n').filter(Boolean);
      gitStatus = statusLines.length === 0
        ? 'clean'
        : `${statusLines.length} changed files`;

      // Recent commits
      const { stdout: log } = await execAsync(
        'git log --oneline -5 --format="%h %s"',
        { cwd: normalized }
      );
      recentChanges.push(
        ...log
          .trim()
          .split('\n')
          .filter(Boolean)
          .map((l) => l.trim())
      );
    } catch {
      // Not a git repo or git not available
    }

    const profile: ProjectProfile = {
      id: normalized.replace(/[^a-zA-Z0-9]/g, '-').toLowerCase(),
      name,
      rootPath: normalized,
      type,
      framework,
      description,
      gitBranch,
      gitStatus,
      recentChanges,
      keyFiles,
      structure,
      lastScanned: Date.now(),
    };

    this.projects.set(normalized, profile);

    console.log(
      `[ProjectAwareness] Scanned: ${name} (${type}${framework ? '/' + framework : ''}, branch: ${gitBranch || 'none'})`
    );

    return profile;
  }

  /**
   * Build context string for personality.ts injection.
   * Shows the most recently scanned project.
   */
  getContextString(): string {
    if (this.projects.size === 0) return '';

    // Get the most recently scanned project
    const project = Array.from(this.projects.values())
      .sort((a, b) => b.lastScanned - a.lastScanned)[0];

    if (!project) return '';

    const parts: string[] = ['## Active Project Context'];
    parts.push(`- Project: ${project.name} (${project.type}${project.framework ? '/' + project.framework : ''})`);

    if (project.description) {
      parts.push(`- Description: ${project.description}`);
    }

    if (project.gitBranch) {
      parts.push(`- Branch: ${project.gitBranch} (${project.gitStatus || 'unknown'})`);
    }

    if (project.recentChanges.length > 0) {
      parts.push(`- Recent commits: ${project.recentChanges.slice(0, 3).join(', ')}`);
    }

    if (project.keyFiles.length > 0) {
      parts.push(`- Key files: ${project.keyFiles.join(', ')}`);
    }

    return parts.join('\n');
  }
}

export const projectAwareness = new ProjectAwareness();
