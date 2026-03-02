/**
 * PageIndex Connector — Vectorless document intelligence for Agent Friday.
 *
 * Integrates the PageIndex tree-based RAG system as a connector, giving the
 * agent the ability to deeply index PDF documents and answer questions about
 * them with ~99% accuracy — without any vector database.
 *
 * Based on PageIndex by Vectify AI (MIT License):
 *   https://github.com/VectifyAI/PageIndex
 *   Authors: Mingtian Zhang, Yu Tang, and the PageIndex Team
 *
 * Tools:
 *   - index_document:    Build a PageIndex tree from a PDF file
 *   - search_document:   Search an indexed document for relevant content
 *   - ask_document:      Ask a question and get an answer from an indexed document
 *   - list_indexed_docs: List all documents that have been indexed
 *   - get_document_tree: Get the full tree structure of an indexed document
 *
 * Exports: TOOLS, execute, detect
 */

import * as fs from 'fs';
import * as path from 'path';
import { app } from 'electron';
import { ToolDeclaration, ToolResult } from './registry';
import { settingsManager } from '../settings';
import { buildPageIndex } from '../pageindex/tree-builder';
import { searchTree, searchAndAnswer } from '../pageindex/tree-search';
import type { PageIndexTree, PageIndexConfig } from '../pageindex/types';
import { DEFAULT_CONFIG } from '../pageindex/types';

// ── Constants ────────────────────────────────────────────────────────

const INDEX_DIR_NAME = 'pageindex-store';
const MAX_RESPONSE_CHARS = 25_000;

// ── Helpers ──────────────────────────────────────────────────────────

function ok(text: string): ToolResult {
  return { result: text.trim() || '(no output)' };
}

function fail(msg: string): ToolResult {
  return { error: msg };
}

function truncate(text: string, maxLen: number): string {
  if (!text) return '';
  if (text.length <= maxLen) return text;
  return text.slice(0, maxLen) + `\n\n…[truncated — ${text.length} chars total]`;
}

/** Get the directory where indexed documents are stored */
function getIndexDir(): string {
  const dir = path.join(app.getPath('userData'), INDEX_DIR_NAME);
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
  return dir;
}

/** Get the index file path for a document */
function getIndexPath(docName: string): string {
  const safeName = docName.replace(/[^a-zA-Z0-9._-]/g, '_');
  return path.join(getIndexDir(), `${safeName}.pageindex.json`);
}

/** Load an existing index */
function loadIndex(docName: string): PageIndexTree | null {
  const indexPath = getIndexPath(docName);
  if (!fs.existsSync(indexPath)) return null;
  try {
    const data = fs.readFileSync(indexPath, 'utf-8');
    return JSON.parse(data) as PageIndexTree;
  } catch (err) {
    // Crypto Sprint 17: Sanitize error output.
    console.warn(`[PageIndex] Failed to load index for ${docName}:`, err instanceof Error ? err.message : 'Unknown error');
    return null;
  }
}

/** Save an index */
function saveIndex(tree: PageIndexTree): void {
  const indexPath = getIndexPath(tree.doc_name);
  fs.writeFileSync(indexPath, JSON.stringify(tree, null, 2), 'utf-8');
  console.log(`[PageIndex] Saved index: ${indexPath}`);
}

/** List all saved indexes */
function listIndexes(): Array<{ doc_name: string; total_pages: number; created_at: string; model: string; description?: string }> {
  const dir = getIndexDir();
  const files = fs.readdirSync(dir).filter(f => f.endsWith('.pageindex.json'));
  const results: Array<{ doc_name: string; total_pages: number; created_at: string; model: string; description?: string }> = [];

  for (const file of files) {
    try {
      const data = fs.readFileSync(path.join(dir, file), 'utf-8');
      const tree = JSON.parse(data) as PageIndexTree;
      results.push({
        doc_name: tree.doc_name,
        total_pages: tree.total_pages,
        created_at: tree.created_at,
        model: tree.model,
        description: tree.doc_description,
      });
    } catch {
      // Skip corrupted indexes
    }
  }

  return results;
}

/** Get the model to use, with fallback */
function getModel(): string {
  const settings = settingsManager.get();
  // Use GPT-4o for PageIndex (it's optimized for it)
  // But allow override if the user has a preferred model
  return (settings as any).pageindexModel || DEFAULT_CONFIG.model;
}

// ── Tool implementations ─────────────────────────────────────────────

async function indexDocument(args: Record<string, unknown>): Promise<string> {
  const pdfPath = typeof args.pdf_path === 'string' ? args.pdf_path : '';
  if (!pdfPath) return 'ERROR: pdf_path is required.';

  if (!fs.existsSync(pdfPath)) {
    return `ERROR: PDF file not found: ${pdfPath}`;
  }

  if (!pdfPath.toLowerCase().endsWith('.pdf')) {
    return 'ERROR: Only PDF files are supported for indexing.';
  }

  const docName = path.basename(pdfPath);
  const existingIndex = loadIndex(docName);

  // Check for re-index flag
  const forceReindex = args.force === true || args.force === 'true';
  if (existingIndex && !forceReindex) {
    return `## Document Already Indexed\n\n**${docName}** was indexed on ${existingIndex.created_at}.\n` +
      `Pages: ${existingIndex.total_pages} | Sections: ${countNodes(existingIndex.structure)}\n` +
      (existingIndex.doc_description ? `\nDescription: ${existingIndex.doc_description}\n` : '') +
      `\nUse \`search_document\` or \`ask_document\` to query it.\n` +
      `Pass \`force: true\` to re-index.`;
  }

  const model = getModel();
  const addText = args.include_text === true || args.include_text === 'true';
  const addSummaries = args.skip_summaries !== true && args.skip_summaries !== 'true';

  const config: Partial<PageIndexConfig> = {
    model,
    addNodeSummary: addSummaries,
    addDocDescription: true,
    addNodeText: addText,
  };

  try {
    console.log(`[PageIndex] Starting indexing of ${docName} with model ${model}...`);
    const startTime = Date.now();

    const tree = await buildPageIndex(pdfPath, config, (status) => {
      console.log(`[PageIndex] ${status.phase} (${status.progress}%): ${status.message}`);
    });

    const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
    saveIndex(tree);

    const nodeCount = countNodes(tree.structure);
    const topLevel = tree.structure.map(n => `  - ${n.structure}. ${n.title}`).join('\n');

    return `## Document Indexed Successfully\n\n` +
      `**Document:** ${tree.doc_name}\n` +
      `**Pages:** ${tree.total_pages}\n` +
      `**Sections:** ${nodeCount}\n` +
      `**Model:** ${tree.model}\n` +
      `**Time:** ${elapsed}s\n` +
      (tree.doc_description ? `**Description:** ${tree.doc_description}\n` : '') +
      `\n### Top-Level Structure\n${topLevel}\n` +
      `\nThe document is now ready for search. Use \`search_document\` or \`ask_document\` to query it.`;
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    return `ERROR: Indexing failed for ${docName}: ${msg}`;
  }
}

async function searchDocument(args: Record<string, unknown>): Promise<string> {
  const query = typeof args.query === 'string' ? args.query : '';
  const docName = typeof args.document === 'string' ? args.document : '';

  if (!query) return 'ERROR: query is required.';
  if (!docName) return 'ERROR: document name is required. Use list_indexed_docs to see available documents.';

  const tree = loadIndex(docName);
  if (!tree) {
    return `ERROR: No index found for "${docName}". Index it first with index_document.`;
  }

  // Ensure nodes have text content for retrieval
  if (!hasText(tree.structure)) {
    return `ERROR: The index for "${docName}" was built without text content. ` +
      `Re-index with include_text: true, or use ask_document which works with summaries.`;
  }

  const model = getModel();

  try {
    const result = await searchTree(query, tree, model);

    if (!result.content.trim()) {
      return `## No Relevant Content Found\n\n` +
        `Query: "${query}"\nDocument: ${docName}\n\n` +
        `The LLM didn't identify any sections as relevant. Try rephrasing the query.`;
    }

    return truncate(
      `## Search Results: ${docName}\n\n` +
      `**Query:** ${query}\n` +
      `**Matched nodes:** ${result.node_ids.join(', ')}\n` +
      `**Context tokens:** ${result.content_tokens.toLocaleString()}\n\n` +
      `### LLM Reasoning\n${result.thinking}\n\n` +
      `### Retrieved Content\n\n${result.content}`,
      MAX_RESPONSE_CHARS,
    );
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    return `ERROR: Search failed: ${msg}`;
  }
}

async function askDocument(args: Record<string, unknown>): Promise<string> {
  const question = typeof args.question === 'string' ? args.question : '';
  const docName = typeof args.document === 'string' ? args.document : '';

  if (!question) return 'ERROR: question is required.';
  if (!docName) return 'ERROR: document name is required. Use list_indexed_docs to see available documents.';

  const tree = loadIndex(docName);
  if (!tree) {
    return `ERROR: No index found for "${docName}". Index it first with index_document.`;
  }

  const model = getModel();

  try {
    const { answer, search } = await searchAndAnswer(question, tree, model);

    return truncate(
      `## Answer from: ${docName}\n\n` +
      `**Question:** ${question}\n\n` +
      `${answer}\n\n` +
      `---\n*Sources: nodes ${search.node_ids.join(', ')} | ` +
      `${search.content_tokens.toLocaleString()} context tokens*`,
      MAX_RESPONSE_CHARS,
    );
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    return `ERROR: Failed to answer question: ${msg}`;
  }
}

async function listIndexedDocs(): Promise<string> {
  const indexes = listIndexes();

  if (indexes.length === 0) {
    return '## No Indexed Documents\n\nNo documents have been indexed yet. Use `index_document` with a PDF path to get started.';
  }

  const lines = indexes.map((idx, i) =>
    `${i + 1}. **${idx.doc_name}** — ${idx.total_pages} pages, indexed ${idx.created_at.split('T')[0]}` +
    (idx.description ? `\n   ${idx.description}` : '')
  );

  return `## Indexed Documents (${indexes.length})\n\n${lines.join('\n')}`;
}

async function getDocumentTree(args: Record<string, unknown>): Promise<string> {
  const docName = typeof args.document === 'string' ? args.document : '';
  if (!docName) return 'ERROR: document name is required.';

  const tree = loadIndex(docName);
  if (!tree) {
    return `ERROR: No index found for "${docName}".`;
  }

  const structureView = formatTree(tree.structure, 0);

  return truncate(
    `## Document Tree: ${tree.doc_name}\n\n` +
    `**Pages:** ${tree.total_pages} | **Model:** ${tree.model}\n` +
    (tree.doc_description ? `**Description:** ${tree.doc_description}\n` : '') +
    `\n### Structure\n\n${structureView}`,
    MAX_RESPONSE_CHARS,
  );
}

// ── Tree helpers ─────────────────────────────────────────────────────

function countNodes(nodes: PageIndexTree['structure']): number {
  let count = 0;
  for (const node of nodes) {
    count++;
    if (node.nodes && node.nodes.length > 0) {
      count += countNodes(node.nodes);
    }
  }
  return count;
}

function hasText(nodes: PageIndexTree['structure']): boolean {
  for (const node of nodes) {
    if (node.text && node.text.trim().length > 0) return true;
    if (node.summary && node.summary.trim().length > 0) return true;
    if (node.nodes && node.nodes.length > 0 && hasText(node.nodes)) return true;
  }
  return false;
}

function formatTree(nodes: PageIndexTree['structure'], depth: number): string {
  const lines: string[] = [];
  const indent = '  '.repeat(depth);

  for (const node of nodes) {
    const pages = node.start_index === node.end_index
      ? `p${node.start_index}`
      : `p${node.start_index}–${node.end_index}`;

    lines.push(`${indent}- **${node.structure}** ${node.title} [${pages}]`);

    if (node.summary) {
      lines.push(`${indent}  _${node.summary.substring(0, 120)}${node.summary.length > 120 ? '...' : ''}_`);
    }

    if (node.nodes && node.nodes.length > 0) {
      lines.push(formatTree(node.nodes, depth + 1));
    }
  }

  return lines.join('\n');
}

// ── Tool declarations ────────────────────────────────────────────────

export const TOOLS: ReadonlyArray<ToolDeclaration> = [
  {
    name: 'index_document',
    description:
      'Index a PDF document using PageIndex — a vectorless, reasoning-based document intelligence system. ' +
      'Creates a hierarchical tree structure that enables highly accurate document search and Q&A. ' +
      'Best for: analyzing contracts, research papers, technical documentation, financial reports, legal documents, ' +
      'manuals, books, or any PDF the user wants to deeply understand. ' +
      'Indexing uses the OpenAI API (GPT-4o) and may take 1-5 minutes depending on document size. ' +
      'Once indexed, use search_document or ask_document to query the content.',
    parameters: {
      type: 'object',
      properties: {
        pdf_path: {
          type: 'string',
          description: 'Absolute path to the PDF file to index.',
        },
        force: {
          type: 'boolean',
          description: 'If true, re-index even if the document was already indexed. Default: false.',
        },
        include_text: {
          type: 'boolean',
          description: 'If true, store raw page text in the index for direct search. Increases index size. Default: false.',
        },
        skip_summaries: {
          type: 'boolean',
          description: 'If true, skip generating node summaries. Faster but less accurate search. Default: false.',
        },
      },
      required: ['pdf_path'],
    },
  },
  {
    name: 'search_document',
    description:
      'Search an indexed document for content relevant to a query. Uses LLM reasoning to navigate the ' +
      'document tree structure and find the most relevant sections. Returns the raw text from matched sections. ' +
      'The document must be indexed first with index_document. Use list_indexed_docs to see available documents.',
    parameters: {
      type: 'object',
      properties: {
        query: {
          type: 'string',
          description: 'The search query — what information are you looking for?',
        },
        document: {
          type: 'string',
          description: 'The document filename to search (e.g., "report.pdf"). Use list_indexed_docs to see available documents.',
        },
      },
      required: ['query', 'document'],
    },
  },
  {
    name: 'ask_document',
    description:
      'Ask a question about an indexed document and get a direct answer. Combines tree search with answer ' +
      'generation — finds relevant sections, then synthesizes an answer based on the retrieved context. ' +
      'Best for: specific questions about document content, extracting facts, summarizing sections. ' +
      'The document must be indexed first with index_document.',
    parameters: {
      type: 'object',
      properties: {
        question: {
          type: 'string',
          description: 'The question to answer from the document.',
        },
        document: {
          type: 'string',
          description: 'The document filename to query (e.g., "report.pdf"). Use list_indexed_docs to see available documents.',
        },
      },
      required: ['question', 'document'],
    },
  },
  {
    name: 'list_indexed_docs',
    description:
      'List all documents that have been indexed with PageIndex. Shows document name, page count, ' +
      'index date, and description. Use this to see what documents are available for search and Q&A.',
    parameters: {
      type: 'object',
      properties: {},
    },
  },
  {
    name: 'get_document_tree',
    description:
      'Get the full hierarchical tree structure of an indexed document. Shows all sections, subsections, ' +
      'and their page ranges. Useful for understanding document organization before searching.',
    parameters: {
      type: 'object',
      properties: {
        document: {
          type: 'string',
          description: 'The document filename (e.g., "report.pdf").',
        },
      },
      required: ['document'],
    },
  },
];

// ── Public exports ───────────────────────────────────────────────────

export async function execute(
  toolName: string,
  args: Record<string, unknown>,
): Promise<ToolResult> {
  try {
    switch (toolName) {
      case 'index_document':
        return ok(await indexDocument(args));
      case 'search_document':
        return ok(await searchDocument(args));
      case 'ask_document':
        return ok(await askDocument(args));
      case 'list_indexed_docs':
        return ok(await listIndexedDocs());
      case 'get_document_tree':
        return ok(await getDocumentTree(args));
      default:
        return fail(`Unknown pageindex tool: ${toolName}`);
    }
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    return fail(`pageindex "${toolName}" failed: ${message}`);
  }
}

export async function detect(): Promise<boolean> {
  // PageIndex requires an OpenAI API key for the LLM calls
  const key = settingsManager.getOpenaiApiKey();
  return !!key && key.length > 0;
}
