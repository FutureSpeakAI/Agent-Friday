/**
 * PageIndex PDF Parser — Extract text content page-by-page from PDF files.
 * Uses pdfjs-dist (Mozilla PDF.js) for reliable cross-platform PDF parsing.
 */

import fs from 'fs';
import path from 'path';
import type { PageData } from './types';
import { countTokens } from './utils';

/**
 * Parse a PDF file and extract text from each page.
 * Returns an array of PageData with page index, text, and token count.
 */
export async function parsePdf(filePath: string): Promise<PageData[]> {
  // Dynamically import pdfjs-dist (ESM module)
  const pdfjsLib = await import('pdfjs-dist/legacy/build/pdf.mjs');

  const absolutePath = path.resolve(filePath);
  if (!fs.existsSync(absolutePath)) {
    throw new Error(`PDF file not found: ${absolutePath}`);
  }

  const data = new Uint8Array(fs.readFileSync(absolutePath));
  const doc = await pdfjsLib.getDocument({ data }).promise;
  const totalPages = doc.numPages;
  const pages: PageData[] = [];

  console.log(`[PageIndex] Parsing PDF: ${path.basename(filePath)} (${totalPages} pages)`);

  for (let i = 1; i <= totalPages; i++) {
    const page = await doc.getPage(i);
    const textContent = await page.getTextContent();

    // Reconstruct text with proper spacing
    let text = '';
    let lastY: number | null = null;

    for (const item of textContent.items) {
      if ('str' in item) {
        const typedItem = item as { str: string; transform: number[] };
        const currentY = typedItem.transform[5];

        // Detect line breaks by y-coordinate changes
        if (lastY !== null && Math.abs(currentY - lastY) > 2) {
          text += '\n';
        } else if (text.length > 0 && !text.endsWith(' ') && !text.endsWith('\n')) {
          text += ' ';
        }

        text += typedItem.str;
        lastY = currentY;
      }
    }

    const cleanText = text.trim();
    pages.push({
      index: i - 1, // 0-based
      text: cleanText,
      tokens: countTokens(cleanText),
    });
  }

  const totalTokens = pages.reduce((sum, p) => sum + p.tokens, 0);
  console.log(`[PageIndex] Parsed ${totalPages} pages, ${totalTokens.toLocaleString()} total tokens`);

  return pages;
}
