/**
 * PageIndex Types — TypeScript port of the PageIndex vectorless RAG system.
 * Original: https://github.com/VectifyAI/PageIndex (MIT License)
 * Authors: Mingtian Zhang, Yu Tang, and the PageIndex Team at Vectify AI
 */

/** A single node in the hierarchical document tree */
export interface PageIndexNode {
  /** Section title */
  title: string;
  /** Hierarchical structure index (e.g., "1", "1.1", "1.2.3") */
  structure: string;
  /** Unique zero-padded node ID (e.g., "0000", "0001") */
  node_id: string;
  /** First page index (0-based) */
  start_index: number;
  /** Last page index (0-based) */
  end_index: number;
  /** LLM-generated summary of this node's content */
  summary?: string;
  /** Prefix summary including parent context */
  prefix_summary?: string;
  /** Raw text content of this node's pages */
  text?: string;
  /** Child nodes */
  nodes?: PageIndexNode[];
}

/** The complete document index tree */
export interface PageIndexTree {
  /** Document filename */
  doc_name: string;
  /** One-sentence document description */
  doc_description?: string;
  /** Total number of pages */
  total_pages: number;
  /** Root-level structure nodes */
  structure: PageIndexNode[];
  /** Timestamp when the index was created */
  created_at: string;
  /** Model used for indexing */
  model: string;
}

/** Intermediate flat TOC entry during tree construction */
export interface FlatTocEntry {
  structure: string;
  title: string;
  physical_index: number;
  appear_start?: string;
}

/** Page data extracted from a PDF */
export interface PageData {
  /** Page index (0-based) */
  index: number;
  /** Raw text content */
  text: string;
  /** Token count */
  tokens: number;
}

/** Configuration for the PageIndex pipeline */
export interface PageIndexConfig {
  /** OpenAI model to use (default: gpt-4o) */
  model: string;
  /** Number of pages to check for TOC (default: 20) */
  tocCheckPageNum: number;
  /** Max pages per node before recursive splitting (default: 10) */
  maxPagesPerNode: number;
  /** Max tokens per node before recursive splitting (default: 20000) */
  maxTokensPerNode: number;
  /** Whether to generate node summaries (default: true) */
  addNodeSummary: boolean;
  /** Whether to generate document description (default: true) */
  addDocDescription: boolean;
  /** Whether to include raw text in nodes (default: false) */
  addNodeText: boolean;
}

/** Result from tree search retrieval */
export interface SearchResult {
  /** The query that was searched */
  query: string;
  /** Document name */
  doc_name: string;
  /** LLM's reasoning about which nodes are relevant */
  thinking: string;
  /** List of relevant node IDs */
  node_ids: string[];
  /** Extracted text content from relevant nodes */
  content: string;
  /** Total token count of retrieved content */
  content_tokens: number;
}

/** Status of the indexing process */
export interface IndexingStatus {
  phase: 'parsing' | 'toc-detection' | 'tree-building' | 'verification' | 'enrichment' | 'complete' | 'error';
  progress: number; // 0-100
  message: string;
}

export const DEFAULT_CONFIG: PageIndexConfig = {
  model: 'gpt-4o',
  tocCheckPageNum: 20,
  maxPagesPerNode: 10,
  maxTokensPerNode: 20000,
  addNodeSummary: true,
  addDocDescription: true,
  addNodeText: false,
};
