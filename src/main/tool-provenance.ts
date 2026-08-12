/**
 * tool-provenance.ts — FR-3 (provenance rules) from
 * dev/friday-orchestrator-integrity-spec.md.
 *
 * Tracks which URLs actually came from an executed tool result this turn, so
 * the renderer can linkify only provenanced URLs and render model-minted
 * URLs as inert plain text (FR-3a). Also flags replies that assert
 * calendar/email/search facts despite zero tools running this turn — a
 * heuristic tripwire, not a hard stop; FR-2's honest-failure path is the
 * hard stop (FR-3b).
 */

const URL_RE = /https?:\/\/[^\s<>"')\]]+/g;

/** Extract URLs appearing in a tool result string, for the per-turn provenance set. */
export function extractUrls(text: string): string[] {
  if (!text) return [];
  return text.match(URL_RE) || [];
}

/** Phrases suggesting a calendar/email/search fact is being asserted in the reply. */
const DATA_CLAIM_INDICATORS: RegExp[] = [
  /\bmeeting\b/i,
  /\bcalendar\b/i,
  /\bscheduled\b/i,
  /\bupcoming event/i,
  /\bemail(s|ed)?\b/i,
  /\binbox\b/i,
  /\bmessages? from\b/i,
  /\bsearch(ed|ing)?\s+(the web|for|results?)\b/i,
  /\baccording to (my|the) search\b/i,
];

/**
 * Heuristic tripwire — logs a warning when a reply asserts calendar/email/
 * search facts while zero tools executed this turn. Not a hard stop.
 */
export function warnIfUngroundedDataClaim(responseText: string, toolsExecutedThisTurn: number): void {
  if (toolsExecutedThisTurn > 0 || !responseText) return;
  const hit = DATA_CLAIM_INDICATORS.find((re) => re.test(responseText));
  if (hit) {
    console.warn(
      `[Provenance] Reply asserts a data claim (matched ${hit}) with zero tools executed this turn — ` +
      `possible ungrounded/fabricated fact: "${responseText.slice(0, 200)}"`
    );
  }
}
