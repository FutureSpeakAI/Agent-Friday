/**
 * PageIndex Tree Builder — Construct hierarchical document trees from PDFs.
 * TypeScript port of the core PageIndex indexing pipeline.
 *
 * Original: https://github.com/VectifyAI/PageIndex (MIT License)
 * Authors: Mingtian Zhang, Yu Tang, and the PageIndex Team at Vectify AI
 *
 * Pipeline:
 * 1. Parse PDF → page-by-page text
 * 2. Detect TOC (if present)
 * 3. Build tree from TOC or raw text
 * 4. Verify and fix page mappings
 * 5. Enrich with summaries
 */

import path from 'path';
import type {
  PageIndexNode,
  PageIndexTree,
  FlatTocEntry,
  PageData,
  PageIndexConfig,
  IndexingStatus,
} from './types';
import { DEFAULT_CONFIG } from './types';
import { parsePdf } from './pdf-parser';
import * as prompts from './prompts';
import {
  callLLM,
  callLLMWithHistory,
  extractJson,
  countTokens,
  wrapPageText,
  groupPages,
  listToTree,
  writeNodeIds,
  fixEndIndices,
  addTextToNodes,
  parsePhysicalIndex,
} from './utils';

type StatusCallback = (status: IndexingStatus) => void;

// ── Main Entry Point ──────────────────────────────────────────────────

/**
 * Build a PageIndex tree from a PDF file.
 */
export async function buildPageIndex(
  pdfPath: string,
  config: Partial<PageIndexConfig> = {},
  onStatus?: StatusCallback,
): Promise<PageIndexTree> {
  const cfg = { ...DEFAULT_CONFIG, ...config };
  const docName = path.basename(pdfPath);

  const report = (phase: IndexingStatus['phase'], progress: number, message: string) => {
    console.log(`[PageIndex] [${phase}] ${message}`);
    onStatus?.({ phase, progress, message });
  };

  // Phase 1: Parse PDF
  report('parsing', 5, `Parsing ${docName}...`);
  const pages = await parsePdf(pdfPath);

  if (pages.length === 0) {
    throw new Error(`PDF has no extractable text: ${pdfPath}`);
  }

  // Phase 2: Detect TOC
  report('toc-detection', 15, 'Scanning for table of contents...');
  const tocResult = await detectToc(pages, cfg);

  // Phase 3: Build tree
  report('tree-building', 30, 'Building document tree structure...');
  let flatEntries: FlatTocEntry[];

  if (tocResult.hasToc && tocResult.hasPageNumbers && tocResult.tocContent) {
    report('tree-building', 35, 'Processing TOC with page numbers...');
    flatEntries = await processTocWithPageNumbers(tocResult.tocContent, pages, cfg);
  } else if (tocResult.hasToc && tocResult.tocContent) {
    report('tree-building', 35, 'Processing TOC without page numbers...');
    flatEntries = await processTocNoPageNumbers(tocResult.tocContent, pages, cfg);
  } else {
    report('tree-building', 35, 'No TOC found — generating structure from text...');
    flatEntries = await processNoToc(pages, 0, pages.length - 1, cfg);
  }

  if (flatEntries.length === 0) {
    // Fallback: treat entire document as one node
    flatEntries = [{
      structure: '1',
      title: docName.replace('.pdf', ''),
      physical_index: 0,
    }];
  }

  // Phase 4: Convert to tree & verify
  report('verification', 55, 'Building and verifying tree structure...');
  let tree = listToTree(flatEntries);
  writeNodeIds(tree);
  fixEndIndices(tree, pages.length);

  // Verify a sample of entries
  const accuracy = await verifyTree(tree, pages, cfg);
  report('verification', 65, `Tree verification accuracy: ${(accuracy * 100).toFixed(0)}%`);

  if (accuracy < 0.6 && tocResult.hasToc) {
    report('verification', 67, 'Low accuracy — falling back to raw text processing...');
    flatEntries = await processNoToc(pages, 0, pages.length - 1, cfg);
    tree = listToTree(flatEntries);
    writeNodeIds(tree);
    fixEndIndices(tree, pages.length);
  }

  // Phase 5: Enrich with text and summaries
  report('enrichment', 70, 'Adding text content to nodes...');
  addTextToNodes(tree, pages);

  if (cfg.addNodeSummary) {
    report('enrichment', 75, 'Generating node summaries...');
    await generateAllSummaries(tree, cfg);
  }

  let docDescription: string | undefined;
  if (cfg.addDocDescription) {
    report('enrichment', 90, 'Generating document description...');
    docDescription = await generateDescription(tree, cfg);
  }

  // Strip text if not requested
  if (!cfg.addNodeText) {
    stripText(tree);
  }

  report('complete', 100, `Index complete: ${flatEntries.length} sections indexed`);

  return {
    doc_name: docName,
    doc_description: docDescription,
    total_pages: pages.length,
    structure: tree,
    created_at: new Date().toISOString(),
    model: cfg.model,
  };
}

// ── TOC Detection ─────────────────────────────────────────────────────

interface TocDetectionResult {
  hasToc: boolean;
  hasPageNumbers: boolean;
  tocContent: string | null;
  tocPages: number[];
}

async function detectToc(
  pages: PageData[],
  cfg: PageIndexConfig,
): Promise<TocDetectionResult> {
  const pagesToCheck = Math.min(cfg.tocCheckPageNum, pages.length);
  const tocPages: number[] = [];

  // Check each of the first N pages for TOC content
  for (let i = 0; i < pagesToCheck; i++) {
    if (pages[i].text.trim().length < 50) continue; // Skip near-empty pages

    const response = await callLLM(
      prompts.tocDetectorSinglePage(pages[i].text),
      cfg.model,
    );
    const result = extractJson<{ toc_detected: string }>(response);
    if (result?.toc_detected?.toLowerCase() === 'yes') {
      tocPages.push(i);
    }
  }

  if (tocPages.length === 0) {
    return { hasToc: false, hasPageNumbers: false, tocContent: null, tocPages: [] };
  }

  // Extract TOC content from identified pages
  const tocText = tocPages.map(i => pages[i].text).join('\n');
  const extractedToc = await extractTocContent(tocText, cfg);

  // Check if the TOC has page numbers
  const pageNumResponse = await callLLM(
    prompts.detectPageIndex(extractedToc),
    cfg.model,
  );
  const pageNumResult = extractJson<{ page_index_given_in_toc: string }>(pageNumResponse);
  const hasPageNumbers = pageNumResult?.page_index_given_in_toc?.toLowerCase() === 'yes';

  return {
    hasToc: true,
    hasPageNumbers,
    tocContent: extractedToc,
    tocPages,
  };
}

async function extractTocContent(tocText: string, cfg: PageIndexConfig): Promise<string> {
  const messages: Array<{ role: 'user' | 'assistant'; content: string }> = [
    { role: 'user', content: prompts.extractTocContent(tocText) },
  ];

  let fullContent = '';
  let maxContinuations = 5;

  while (maxContinuations > 0) {
    const result = await callLLMWithHistory(messages, cfg.model);
    fullContent += result.content;

    if (result.finishReason === 'stop') break;

    // Content was cut off — ask to continue
    messages.push({ role: 'assistant', content: result.content });
    messages.push({ role: 'user', content: prompts.CONTINUE_TOC_EXTRACTION });
    maxContinuations--;
  }

  return fullContent;
}

// ── Processing: TOC with page numbers ─────────────────────────────────

async function processTocWithPageNumbers(
  tocContent: string,
  pages: PageData[],
  cfg: PageIndexConfig,
): Promise<FlatTocEntry[]> {
  // Transform TOC to structured JSON
  const structured = await transformToc(tocContent, cfg);
  if (!structured || structured.length === 0) {
    // Fallback to no-TOC processing
    return processNoToc(pages, 0, pages.length - 1, cfg);
  }

  // Calculate page offset (logical page numbers → physical page indices)
  const offset = calculatePageOffset(structured, pages);

  // Apply offset to get physical indices
  const entries: FlatTocEntry[] = structured.map(item => ({
    structure: item.structure || '1',
    title: item.title,
    physical_index: Math.max(0, Math.min(pages.length - 1,
      (item.page ?? 0) + offset)),
  }));

  return entries;
}

async function transformToc(
  tocContent: string,
  cfg: PageIndexConfig,
): Promise<Array<{ structure: string; title: string; page?: number }> | null> {
  const messages: Array<{ role: 'user' | 'assistant'; content: string }> = [
    { role: 'user', content: prompts.tocTransformer(tocContent) },
  ];

  let fullContent = '';
  let maxContinuations = 5;

  while (maxContinuations > 0) {
    const result = await callLLMWithHistory(messages, cfg.model);
    fullContent += result.content;

    if (result.finishReason === 'stop') break;

    messages.push({ role: 'assistant', content: result.content });
    messages.push({
      role: 'user',
      content: prompts.tocTransformerContinue(tocContent, fullContent),
    });
    maxContinuations--;
  }

  const parsed = extractJson<{ table_of_contents: Array<{ structure: string; title: string; page?: number }> }>(fullContent);
  if (parsed?.table_of_contents) return parsed.table_of_contents;

  // Try parsing as direct array
  const arr = extractJson<Array<{ structure: string; title: string; page?: number }>>(fullContent);
  return arr;
}

function calculatePageOffset(
  entries: Array<{ page?: number }>,
  pages: PageData[],
): number {
  // Most entries don't need offset — just use 0 as default
  // The real PageIndex does a voting mechanism, but for simplicity
  // we assume page numbers map to 0-based indices (common case)
  const pagesWithNumbers = entries.filter(e => e.page != null && e.page > 0);
  if (pagesWithNumbers.length === 0) return 0;

  // If most page numbers are > total pages, they're likely 1-based
  const firstPage = pagesWithNumbers[0].page!;
  if (firstPage >= 1) return -1; // Convert 1-based to 0-based
  return 0;
}

// ── Processing: TOC without page numbers ──────────────────────────────

async function processTocNoPageNumbers(
  tocContent: string,
  pages: PageData[],
  cfg: PageIndexConfig,
): Promise<FlatTocEntry[]> {
  // Transform to structured JSON (without page numbers)
  const structured = await transformToc(tocContent, cfg);
  if (!structured || structured.length === 0) {
    return processNoToc(pages, 0, pages.length - 1, cfg);
  }

  // Use LLM to find where each section starts in the document
  const groups = groupPages(pages, 0, pages.length - 1, cfg.maxTokensPerNode);
  let currentStructure = structured.map(item => ({
    structure: item.structure || '',
    title: item.title,
    start: 'no' as string,
    physical_index: null as number | null,
  }));

  for (const group of groups) {
    const response = await callLLM(
      prompts.addPageNumberToToc(group.text, JSON.stringify(currentStructure, null, 2)),
      cfg.model,
    );
    const result = extractJson<Array<{
      structure: string;
      title: string;
      start: string;
      physical_index: string | null;
    }>>(response);

    if (result) {
      currentStructure = result.map(item => ({
        structure: item.structure,
        title: item.title,
        start: item.start,
        physical_index: item.physical_index ? parsePhysicalIndex(item.physical_index) : null,
      }));
    }
  }

  // Convert to flat entries
  return currentStructure
    .filter(item => item.physical_index !== null)
    .map(item => ({
      structure: item.structure,
      title: item.title,
      physical_index: item.physical_index!,
    }));
}

// ── Processing: No TOC (generate from raw text) ───────────────────────

async function processNoToc(
  pages: PageData[],
  startIdx: number,
  endIdx: number,
  cfg: PageIndexConfig,
): Promise<FlatTocEntry[]> {
  const groups = groupPages(pages, startIdx, endIdx, cfg.maxTokensPerNode);
  let allEntries: FlatTocEntry[] = [];

  for (let i = 0; i < groups.length; i++) {
    const group = groups[i];
    let response: string;

    if (i === 0) {
      // First chunk: generate initial structure
      response = await callLLM(prompts.generateTocInit(group.text), cfg.model);
    } else {
      // Subsequent chunks: continue structure
      response = await callLLM(
        prompts.generateTocContinue(JSON.stringify(allEntries, null, 2), group.text),
        cfg.model,
      );
    }

    const result = extractJson<Array<{
      structure: string;
      title: string;
      physical_index: string;
    }>>(response);

    if (result) {
      const newEntries = result.map(item => ({
        structure: item.structure,
        title: item.title,
        physical_index: parsePhysicalIndex(item.physical_index),
      })).filter(e => e.physical_index >= 0);

      if (i === 0) {
        allEntries = newEntries;
      } else {
        allEntries.push(...newEntries);
      }
    }
  }

  return allEntries;
}

// ── Verification ──────────────────────────────────────────────────────

async function verifyTree(
  tree: PageIndexNode[],
  pages: PageData[],
  cfg: PageIndexConfig,
): Promise<number> {
  // Sample up to 10 nodes for verification
  const allNodes = flattenNodes(tree);
  const sample = allNodes.length <= 10 ? allNodes :
    allNodes.filter((_, i) => i % Math.ceil(allNodes.length / 10) === 0).slice(0, 10);

  if (sample.length === 0) return 1;

  let correct = 0;

  const results = await Promise.all(
    sample.map(async (node) => {
      if (node.start_index < 0 || node.start_index >= pages.length) return false;
      const pageText = pages[node.start_index].text;
      if (!pageText.trim()) return true; // Empty page — benefit of doubt

      const response = await callLLM(
        prompts.checkTitleAppearance(node.title, pageText.substring(0, 2000)),
        cfg.model,
      );
      const result = extractJson<{ answer: string }>(response);
      return result?.answer?.toLowerCase() === 'yes';
    }),
  );

  correct = results.filter(Boolean).length;
  return correct / sample.length;
}

function flattenNodes(nodes: PageIndexNode[]): PageIndexNode[] {
  const result: PageIndexNode[] = [];
  for (const node of nodes) {
    result.push(node);
    if (node.nodes && node.nodes.length > 0) {
      result.push(...flattenNodes(node.nodes));
    }
  }
  return result;
}

// ── Enrichment ────────────────────────────────────────────────────────

async function generateAllSummaries(
  tree: PageIndexNode[],
  cfg: PageIndexConfig,
): Promise<void> {
  const allNodes = flattenNodes(tree);
  const nodesWithText = allNodes.filter(n => n.text && n.text.trim().length > 50);

  // Process in batches of 5 to avoid rate limits
  const batchSize = 5;
  for (let i = 0; i < nodesWithText.length; i += batchSize) {
    const batch = nodesWithText.slice(i, i + batchSize);
    await Promise.all(
      batch.map(async (node) => {
        try {
          // Truncate text to ~4000 tokens for summary generation
          const truncated = node.text!.substring(0, 12000);
          const summary = await callLLM(
            prompts.generateNodeSummary(truncated),
            cfg.model,
            512,
          );
          node.summary = summary.trim();
        } catch (err) {
          console.warn(`[PageIndex] Summary generation failed for node ${node.node_id}: ${err}`);
          node.summary = node.title;
        }
      }),
    );
  }
}

async function generateDescription(
  tree: PageIndexNode[],
  cfg: PageIndexConfig,
): Promise<string> {
  // Create a clean structure view for the description prompt
  const structureView = tree.map(node => ({
    title: node.title,
    nodes: node.nodes?.map(n => ({ title: n.title })),
  }));

  const response = await callLLM(
    prompts.generateDocDescription(JSON.stringify(structureView, null, 2)),
    cfg.model,
    256,
  );
  return response.trim();
}

function stripText(nodes: PageIndexNode[]): void {
  for (const node of nodes) {
    delete node.text;
    if (node.nodes && node.nodes.length > 0) {
      stripText(node.nodes);
    }
  }
}
