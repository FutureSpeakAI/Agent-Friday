/**
 * prompt-budget.ts — System prompt size management.
 * Ensures the assembled system instruction stays within Gemini's context budget.
 * Trims lower-priority sections first when the prompt gets too large.
 */

type Priority = 'critical' | 'high' | 'medium' | 'low';

interface PromptSection {
  name: string;
  content: string;
  priority: Priority;
}

interface BudgetResult {
  prompt: string;
  totalChars: number;
  includedSections: string[];
  trimmedSections: string[];
  droppedSections: string[];
}

// ~30k chars ≈ ~7500 tokens — leaves room for conversation turns and tool results
const MAX_PROMPT_CHARS = 30_000;

// Within a priority tier, trim to these max lengths before dropping entirely
const TRIM_LIMITS: Record<Priority, number> = {
  critical: Infinity,  // Never trimmed
  high: 4000,
  medium: 2000,
  low: 1000,
};

const PRIORITY_ORDER: Priority[] = ['critical', 'high', 'medium', 'low'];

/**
 * Fit prompt sections into the character budget.
 * Strategy:
 * 1. Include all critical sections in full
 * 2. Include high-priority sections, trimming if needed
 * 3. Include medium/low only if budget allows, trimming as needed
 * 4. Drop lowest-priority sections first when over budget
 */
export function fitToBudget(sections: PromptSection[], maxChars = MAX_PROMPT_CHARS): BudgetResult {
  const included: Array<{ name: string; content: string; priority: Priority }> = [];
  const trimmed: string[] = [];
  const dropped: string[] = [];

  // Sort sections by priority
  const sorted = [...sections].sort((a, b) => {
    return PRIORITY_ORDER.indexOf(a.priority) - PRIORITY_ORDER.indexOf(b.priority);
  });

  let totalChars = 0;

  for (const section of sorted) {
    if (!section.content || section.content.trim().length === 0) {
      continue; // Skip empty sections
    }

    const remaining = maxChars - totalChars;

    if (section.priority === 'critical') {
      // Always include critical sections
      included.push(section);
      totalChars += section.content.length;
      continue;
    }

    if (remaining <= 0) {
      // No budget left — drop
      dropped.push(section.name);
      console.log(`[PromptBudget] Dropped "${section.name}" (${section.content.length} chars) — no budget remaining`);
      continue;
    }

    const trimLimit = TRIM_LIMITS[section.priority];

    if (section.content.length <= remaining) {
      // Fits entirely
      included.push(section);
      totalChars += section.content.length;
    } else if (remaining >= 200) {
      // Trim to fit
      const trimTo = Math.min(remaining, trimLimit);
      const trimmedContent = section.content.slice(0, trimTo) + '\n[... truncated due to context budget]';
      included.push({ ...section, content: trimmedContent });
      totalChars += trimmedContent.length;
      trimmed.push(section.name);
      console.log(`[PromptBudget] Trimmed "${section.name}" from ${section.content.length} to ${trimTo} chars`);
    } else {
      // Too little space even for a trimmed version
      dropped.push(section.name);
      console.log(`[PromptBudget] Dropped "${section.name}" (${section.content.length} chars) — insufficient budget (${remaining} remaining)`);
    }
  }

  const prompt = included.map(s => s.content).join('\n\n');

  if (trimmed.length > 0 || dropped.length > 0) {
    console.log(`[PromptBudget] Final: ${totalChars}/${maxChars} chars | Included: ${included.length} | Trimmed: ${trimmed.length} | Dropped: ${dropped.length}`);
  }

  return {
    prompt,
    totalChars,
    includedSections: included.map(s => s.name),
    trimmedSections: trimmed,
    droppedSections: dropped,
  };
}

export { MAX_PROMPT_CHARS, TRIM_LIMITS, type Priority, type PromptSection, type BudgetResult };
