/**
 * PageIndex Tree Search — Query indexed documents using LLM-guided tree traversal.
 * Original: https://github.com/VectifyAI/PageIndex (MIT License)
 * Authors: Mingtian Zhang, Yu Tang, and the PageIndex Team at Vectify AI
 *
 * Search strategy:
 * 1. Present the tree structure (without text) to the LLM
 * 2. LLM reasons about which nodes are relevant to the query
 * 3. Extract text from those nodes
 * 4. Optionally generate an answer from the context
 */

import type { PageIndexTree, PageIndexNode, SearchResult } from './types';
import {
  callLLM,
  extractJson,
  findNodeById,
  stripTextFromTree,
  countTokens,
} from './utils';
import * as prompts from './prompts';

/**
 * Search a PageIndex tree for content relevant to a query.
 * Returns the matched nodes' text as context for answering.
 */
export async function searchTree(
  query: string,
  tree: PageIndexTree,
  model: string,
  maxContextTokens = 30000,
): Promise<SearchResult> {
  // Build a lightweight view of the tree for the LLM (no raw text — just titles + summaries)
  const lightTree = stripTextFromTree(tree.structure);
  const treeJson = JSON.stringify(lightTree, null, 2);

  // Ask the LLM which nodes are relevant
  const response = await callLLM(
    prompts.treeSearch(query, treeJson),
    model,
  );

  const parsed = extractJson<{
    thinking: string;
    node_list: string[];
  }>(response);

  const thinking = parsed?.thinking || '';
  const nodeIds = parsed?.node_list || [];

  // Extract text from the identified nodes
  const textParts: string[] = [];
  let totalTokens = 0;

  for (const nodeId of nodeIds) {
    const node = findNodeById(tree.structure, nodeId);
    if (!node) continue;

    const nodeText = getNodeText(node);
    if (!nodeText) continue;

    const nodeTokens = countTokens(nodeText);

    // Stop if we'd exceed the context budget
    if (totalTokens + nodeTokens > maxContextTokens && textParts.length > 0) {
      break;
    }

    textParts.push(`## ${node.title} (${node.structure})\n\n${nodeText}`);
    totalTokens += nodeTokens;
  }

  const content = textParts.join('\n\n---\n\n');

  return {
    query,
    doc_name: tree.doc_name,
    thinking,
    node_ids: nodeIds,
    content,
    content_tokens: totalTokens,
  };
}

/**
 * Search and generate an answer in one step.
 */
export async function searchAndAnswer(
  query: string,
  tree: PageIndexTree,
  model: string,
  maxContextTokens = 30000,
): Promise<{ answer: string; search: SearchResult }> {
  const search = await searchTree(query, tree, model, maxContextTokens);

  if (!search.content.trim()) {
    return {
      answer: `I couldn't find relevant content in "${tree.doc_name}" for that query.`,
      search,
    };
  }

  const answer = await callLLM(
    prompts.generateAnswer(query, search.content),
    model,
  );

  return { answer: answer.trim(), search };
}

/**
 * Get text content from a node, preferring summary for large nodes.
 */
function getNodeText(node: PageIndexNode): string {
  // If the node has raw text, use it
  if (node.text && node.text.trim().length > 0) {
    return node.text;
  }

  // If it has a summary, use that
  if (node.summary && node.summary.trim().length > 0) {
    return node.summary;
  }

  // Recurse into children
  if (node.nodes && node.nodes.length > 0) {
    const childTexts = node.nodes
      .map(child => getNodeText(child))
      .filter(t => t.length > 0);
    return childTexts.join('\n\n');
  }

  return '';
}
