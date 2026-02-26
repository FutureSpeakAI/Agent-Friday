/**
 * PageIndex — Vectorless, reasoning-based RAG for documents.
 *
 * TypeScript port of the PageIndex library.
 * Original: https://github.com/VectifyAI/PageIndex (MIT License)
 * Authors: Mingtian Zhang, Yu Tang, and the PageIndex Team at Vectify AI
 *
 * This module provides:
 * - buildPageIndex(): Index a PDF into a hierarchical tree structure
 * - searchTree(): Query an indexed document using LLM reasoning
 * - searchAndAnswer(): Search + generate an answer in one step
 *
 * The approach replaces traditional vector-based RAG with a tree-based
 * reasoning pipeline that achieves 98.7% accuracy on FinanceBench.
 */

export { buildPageIndex } from './tree-builder';
export { searchTree, searchAndAnswer } from './tree-search';
export { resetClient } from './utils';
export type {
  PageIndexTree,
  PageIndexNode,
  PageIndexConfig,
  SearchResult,
  IndexingStatus,
  FlatTocEntry,
  PageData,
} from './types';
export { DEFAULT_CONFIG } from './types';
