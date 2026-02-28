/**
 * document-ingestion.ts — Document Ingestion Engine for Agent Friday.
 *
 * Reads .txt, .md, .pdf, .docx, .json, .csv files via appropriate parsers,
 * generates Claude summaries, and indexes content via semantic search.
 * File picker via Electron's dialog.showOpenDialog.
 */

import { dialog, BrowserWindow } from 'electron';
import fs from 'fs/promises';
import path from 'path';
import crypto from 'crypto';
import { semanticSearch } from './semantic-search';

export interface IngestedDocument {
  id: string;
  filename: string;
  filePath: string;
  mimeType: string;
  size: number;
  summary: string;
  content: string;
  ingestedAt: number;
}

const MAX_DOCUMENTS = 50;
const MAX_CONTENT_CHARS = 50_000;

class DocumentIngestion {
  private documents: Map<string, IngestedDocument> = new Map();
  private mainWindow: BrowserWindow | null = null;

  initialize(mainWindow: BrowserWindow): void {
    this.mainWindow = mainWindow;
    console.log('[DocumentIngestion] Initialized');
  }

  getAll(): IngestedDocument[] {
    return Array.from(this.documents.values());
  }

  getById(id: string): IngestedDocument | undefined {
    return this.documents.get(id);
  }

  search(query: string): IngestedDocument[] {
    const q = query.toLowerCase();
    return Array.from(this.documents.values())
      .filter(
        (d) =>
          d.filename.toLowerCase().includes(q) ||
          d.summary.toLowerCase().includes(q) ||
          d.content.toLowerCase().includes(q)
      )
      .slice(0, 10);
  }

  /**
   * Open file picker and ingest selected file(s).
   */
  async pickAndIngest(): Promise<IngestedDocument[]> {
    if (!this.mainWindow) throw new Error('No main window available');

    const result = await dialog.showOpenDialog(this.mainWindow, {
      title: 'Select documents to ingest',
      properties: ['openFile', 'multiSelections'],
      filters: [
        { name: 'Documents', extensions: ['txt', 'md', 'pdf', 'docx', 'json', 'csv'] },
        { name: 'All Files', extensions: ['*'] },
      ],
    });

    if (result.canceled || result.filePaths.length === 0) {
      return [];
    }

    const docs: IngestedDocument[] = [];
    for (const filePath of result.filePaths) {
      try {
        const doc = await this.ingestFile(filePath);
        if (doc) docs.push(doc);
      } catch (err) {
        console.warn(`[DocumentIngestion] Failed to ingest ${filePath}:`, err);
      }
    }

    return docs;
  }

  /**
   * Ingest a single file by path.
   */
  async ingestFile(filePath: string): Promise<IngestedDocument | null> {
    const stat = await fs.stat(filePath);
    if (!stat.isFile()) return null;

    // 50MB limit
    if (stat.size > 50 * 1024 * 1024) {
      throw new Error('File too large (max 50MB)');
    }

    const ext = path.extname(filePath).toLowerCase();
    const filename = path.basename(filePath);
    const mimeType = this.getMimeType(ext);

    let content = '';

    switch (ext) {
      case '.txt':
      case '.md':
      case '.csv':
        content = await fs.readFile(filePath, 'utf-8');
        break;

      case '.json':
        content = await fs.readFile(filePath, 'utf-8');
        // Pretty-print for readability
        try {
          content = JSON.stringify(JSON.parse(content), null, 2);
        } catch {
          // Already text, keep as is
        }
        break;

      case '.pdf':
        content = await this.extractPdf(filePath);
        break;

      case '.docx':
        content = await this.extractDocx(filePath);
        break;

      default:
        // Try as text
        try {
          content = await fs.readFile(filePath, 'utf-8');
        } catch {
          throw new Error(`Unsupported file type: ${ext}`);
        }
    }

    // Truncate extremely long content
    if (content.length > MAX_CONTENT_CHARS) {
      content = content.slice(0, MAX_CONTENT_CHARS) + '\n\n[... content truncated]';
    }

    // Generate summary via Claude
    const summary = await this.summarize(filename, content);

    const id = crypto.randomUUID();
    const doc: IngestedDocument = {
      id,
      filename,
      filePath,
      mimeType,
      size: stat.size,
      summary,
      content,
      ingestedAt: Date.now(),
    };

    // Store
    this.documents.set(id, doc);

    // Cap at max
    if (this.documents.size > MAX_DOCUMENTS) {
      const oldest = Array.from(this.documents.entries())
        .sort(([, a], [, b]) => a.ingestedAt - b.ingestedAt)[0];
      if (oldest) {
        this.documents.delete(oldest[0]);
        semanticSearch.remove(oldest[0]);
      }
    }

    // Index for semantic search
    const searchText = `${filename} ${summary} ${content.slice(0, 2000)}`;
    semanticSearch
      .index(id, searchText, 'document', {
        filename,
        summary,
        mimeType,
        size: stat.size,
      })
      .catch(() => {});

    console.log(`[DocumentIngestion] Ingested: ${filename} (${(stat.size / 1024).toFixed(1)}KB)`);

    return doc;
  }

  private async extractPdf(filePath: string): Promise<string> {
    try {
      const pdfParse = require('pdf-parse');
      const buffer = await fs.readFile(filePath);
      const data = await pdfParse(buffer);
      return data.text || '';
    } catch (err) {
      console.warn('[DocumentIngestion] PDF parse failed (is pdf-parse installed?):', err);
      return '[PDF text extraction unavailable — install pdf-parse]';
    }
  }

  private async extractDocx(filePath: string): Promise<string> {
    try {
      const mammoth = require('mammoth');
      const buffer = await fs.readFile(filePath);
      const result = await mammoth.extractRawText({ buffer });
      return result.value || '';
    } catch (err) {
      console.warn('[DocumentIngestion] DOCX parse failed (is mammoth installed?):', err);
      return '[DOCX text extraction unavailable — install mammoth]';
    }
  }

  private async summarize(filename: string, content: string): Promise<string> {
    try {
      const Anthropic = require('@anthropic-ai/sdk');
      const anthropic = new Anthropic.default({
        apiKey: process.env.ANTHROPIC_API_KEY,
      });

      const preview = content.slice(0, 4000);

      const response = await anthropic.messages.create({
        model: 'claude-sonnet-4-20250514',
        max_tokens: 200,
        messages: [
          {
            role: 'user',
            content: `Summarize this document in 2-3 sentences. Focus on what the document is about and key information.\n\nFilename: ${filename}\n\nContent:\n${preview}`,
          },
        ],
      });

      const text = response.content.find((b: any) => b.type === 'text')?.text || '';
      return text.trim() || `Document: ${filename}`;
    } catch {
      return `Document: ${filename} (${(content.length / 1024).toFixed(1)}KB of text)`;
    }
  }

  private getMimeType(ext: string): string {
    const map: Record<string, string> = {
      '.txt': 'text/plain',
      '.md': 'text/markdown',
      '.pdf': 'application/pdf',
      '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      '.json': 'application/json',
      '.csv': 'text/csv',
    };
    return map[ext] || 'application/octet-stream';
  }
}

export const documentIngestion = new DocumentIngestion();
