/**
 * PageIndex Utilities — Token counting, JSON extraction, tree traversal, LLM calls.
 * Original: https://github.com/VectifyAI/PageIndex (MIT License)
 * Authors: Mingtian Zhang, Yu Tang, and the PageIndex Team at Vectify AI
 */

import OpenAI from 'openai';
import { encode } from 'gpt-tokenizer';
import { settingsManager } from '../settings';
import type { PageIndexNode, FlatTocEntry, PageData } from './types';

// ── OpenAI Client ─────────────────────────────────────────────────────

let _client: OpenAI | null = null;

function getClient(): OpenAI {
  if (!_client) {
    const apiKey = settingsManager.get().openaiApiKey;
    if (!apiKey) throw new Error('OpenAI API key not configured — needed for PageIndex');
    _client = new OpenAI({ apiKey });
  }
  return _client;
}

/** Reset client (call when API key changes) */
export function resetClient(): void {
  _client = null;
}

// ── LLM Calls ─────────────────────────────────────────────────────────

const MAX_RETRIES = 5;
const RETRY_DELAY_MS = 1500;

/** Call OpenAI chat completion — single user message, temperature 0 */
export async function callLLM(
  prompt: string,
  model: string,
  maxTokens = 4096,
): Promise<string> {
  const client = getClient();

  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    try {
      const response = await client.chat.completions.create({
        model,
        messages: [{ role: 'user', content: prompt }],
        temperature: 0,
        max_tokens: maxTokens,
      });
      return response.choices[0]?.message?.content || '';
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[PageIndex] LLM call attempt ${attempt + 1}/${MAX_RETRIES} failed: ${msg}`);
      if (attempt < MAX_RETRIES - 1) {
        await sleep(RETRY_DELAY_MS);
      }
    }
  }
  throw new Error(`[PageIndex] LLM call failed after ${MAX_RETRIES} retries`);
}

/** Call LLM with chat history (for continuation prompts) */
export async function callLLMWithHistory(
  messages: Array<{ role: 'user' | 'assistant'; content: string }>,
  model: string,
  maxTokens = 4096,
): Promise<{ content: string; finishReason: string }> {
  const client = getClient();

  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    try {
      const response = await client.chat.completions.create({
        model,
        messages,
        temperature: 0,
        max_tokens: maxTokens,
      });
      return {
        content: response.choices[0]?.message?.content || '',
        finishReason: response.choices[0]?.finish_reason || 'stop',
      };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[PageIndex] LLM history call attempt ${attempt + 1} failed: ${msg}`);
      if (attempt < MAX_RETRIES - 1) await sleep(RETRY_DELAY_MS);
    }
  }
  throw new Error(`[PageIndex] LLM history call failed after ${MAX_RETRIES} retries`);
}

// ── Token Counting ────────────────────────────────────────────────────

/** Count tokens in a string using GPT tokenizer */
export function countTokens(text: string): number {
  return encode(text).length;
}

// ── JSON Extraction ───────────────────────────────────────────────────

/** Extract JSON from LLM response (handles markdown code blocks) */
export function extractJson<T = unknown>(text: string): T | null {
  // Try direct parse first
  try {
    return JSON.parse(text) as T;
  } catch {
    // Try extracting from markdown code blocks
  }

  // Try ```json ... ``` or ``` ... ```
  const codeBlockMatch = text.match(/```(?:json)?\s*\n?([\s\S]*?)\n?```/);
  if (codeBlockMatch) {
    try {
      return JSON.parse(codeBlockMatch[1].trim()) as T;
    } catch {
      // Continue to next strategy
    }
  }

  // Try finding first { or [ to last } or ]
  const firstBrace = text.indexOf('{');
  const firstBracket = text.indexOf('[');
  const start = firstBrace === -1 ? firstBracket :
    firstBracket === -1 ? firstBrace :
    Math.min(firstBrace, firstBracket);

  if (start === -1) return null;

  const isArray = text[start] === '[';
  const closingChar = isArray ? ']' : '}';
  const lastClose = text.lastIndexOf(closingChar);

  if (lastClose <= start) return null;

  try {
    return JSON.parse(text.substring(start, lastClose + 1)) as T;
  } catch {
    return null;
  }
}

// ── Page Text Utilities ───────────────────────────────────────────────

/** Wrap page text with physical index tags (as PageIndex does) */
export function wrapPageText(pages: PageData[], startIdx: number, endIdx: number): string {
  const parts: string[] = [];
  for (let i = startIdx; i <= endIdx && i < pages.length; i++) {
    parts.push(`<physical_index_${i}>\n${pages[i].text}\n<physical_index_${i}>`);
  }
  return parts.join('\n');
}

/** Group pages into chunks respecting a max token limit */
export function groupPages(
  pages: PageData[],
  startIdx: number,
  endIdx: number,
  maxTokens: number,
  overlap = 1,
): Array<{ text: string; startIdx: number; endIdx: number }> {
  const groups: Array<{ text: string; startIdx: number; endIdx: number }> = [];
  let currentStart = startIdx;

  while (currentStart <= endIdx) {
    let currentEnd = currentStart;
    let currentTokens = 0;

    // Add pages until we hit the token limit
    while (currentEnd <= endIdx) {
      const pageTokens = pages[currentEnd].tokens;
      if (currentTokens + pageTokens > maxTokens && currentEnd > currentStart) break;
      currentTokens += pageTokens;
      currentEnd++;
    }
    currentEnd--; // Back to last valid page

    const text = wrapPageText(pages, currentStart, currentEnd);
    groups.push({ text, startIdx: currentStart, endIdx: currentEnd });

    // Next group starts with overlap
    currentStart = Math.max(currentStart + 1, currentEnd - overlap + 1);
    if (currentStart <= currentEnd) currentStart = currentEnd + 1; // Prevent infinite loop
  }

  return groups;
}

// ── Tree Utilities ────────────────────────────────────────────────────

/** Convert a flat list of TOC entries to a nested tree */
export function listToTree(flatList: FlatTocEntry[]): PageIndexNode[] {
  if (flatList.length === 0) return [];

  const nodes: PageIndexNode[] = [];
  const stack: { node: PageIndexNode; depth: number }[] = [];

  for (const item of flatList) {
    const depth = item.structure ? item.structure.split('.').length : 1;
    const node: PageIndexNode = {
      title: item.title,
      structure: item.structure,
      node_id: '',
      start_index: item.physical_index,
      end_index: item.physical_index, // Will be fixed later
      nodes: [],
    };

    // Pop stack until we find the parent level
    while (stack.length > 0 && stack[stack.length - 1].depth >= depth) {
      stack.pop();
    }

    if (stack.length === 0) {
      nodes.push(node);
    } else {
      const parent = stack[stack.length - 1].node;
      if (!parent.nodes) parent.nodes = [];
      parent.nodes.push(node);
    }

    stack.push({ node, depth });
  }

  return nodes;
}

/** Assign sequential node IDs to all nodes in the tree */
export function writeNodeIds(nodes: PageIndexNode[], startId = 0): number {
  let currentId = startId;
  for (const node of nodes) {
    node.node_id = String(currentId).padStart(4, '0');
    currentId++;
    if (node.nodes && node.nodes.length > 0) {
      currentId = writeNodeIds(node.nodes, currentId);
    }
  }
  return currentId;
}

/** Fix end_index for all nodes based on sibling/total pages */
export function fixEndIndices(nodes: PageIndexNode[], totalPages: number): void {
  for (let i = 0; i < nodes.length; i++) {
    const node = nodes[i];
    // End index is start of next sibling - 1, or totalPages - 1 for last sibling
    if (i + 1 < nodes.length) {
      node.end_index = nodes[i + 1].start_index - 1;
    } else {
      node.end_index = totalPages - 1;
    }

    // Ensure end >= start
    if (node.end_index < node.start_index) {
      node.end_index = node.start_index;
    }

    // Recurse into children
    if (node.nodes && node.nodes.length > 0) {
      fixEndIndices(node.nodes, node.end_index + 1);
    }
  }
}

/** Get all nodes flattened (depth-first) */
export function getAllNodes(nodes: PageIndexNode[]): PageIndexNode[] {
  const result: PageIndexNode[] = [];
  for (const node of nodes) {
    result.push(node);
    if (node.nodes && node.nodes.length > 0) {
      result.push(...getAllNodes(node.nodes));
    }
  }
  return result;
}

/** Get all leaf nodes (nodes with no children) */
export function getLeafNodes(nodes: PageIndexNode[]): PageIndexNode[] {
  const result: PageIndexNode[] = [];
  for (const node of nodes) {
    if (!node.nodes || node.nodes.length === 0) {
      result.push(node);
    } else {
      result.push(...getLeafNodes(node.nodes));
    }
  }
  return result;
}

/** Find a node by its node_id */
export function findNodeById(nodes: PageIndexNode[], nodeId: string): PageIndexNode | null {
  for (const node of nodes) {
    if (node.node_id === nodeId) return node;
    if (node.nodes && node.nodes.length > 0) {
      const found = findNodeById(node.nodes, nodeId);
      if (found) return found;
    }
  }
  return null;
}

/** Create a tree copy without text (for search prompts — reduces token usage) */
export function stripTextFromTree(nodes: PageIndexNode[]): PageIndexNode[] {
  return nodes.map(node => ({
    ...node,
    text: undefined,
    nodes: node.nodes ? stripTextFromTree(node.nodes) : undefined,
  }));
}

/** Add raw text content to each node from page data */
export function addTextToNodes(nodes: PageIndexNode[], pages: PageData[]): void {
  for (const node of nodes) {
    const startIdx = Math.max(0, node.start_index);
    const endIdx = Math.min(pages.length - 1, node.end_index);
    const texts: string[] = [];
    for (let i = startIdx; i <= endIdx; i++) {
      texts.push(pages[i].text);
    }
    node.text = texts.join('\n');

    if (node.nodes && node.nodes.length > 0) {
      addTextToNodes(node.nodes, pages);
    }
  }
}

/** Extract physical_index number from tag like "<physical_index_5>" */
export function parsePhysicalIndex(tag: string): number {
  const match = tag.match(/physical_index_(\d+)/);
  return match ? parseInt(match[1], 10) : -1;
}

// ── Helpers ───────────────────────────────────────────────────────────

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}
