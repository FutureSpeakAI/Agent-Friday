/**
 * Scenario harness — testing Friday the way she is actually used.
 *
 * The mechanical tier asks "is this control wired up". This tier asks "does a
 * real task, of the kind Stephen does daily, actually come out right end to
 * end". Those scenarios mostly have no single correct answer, so they are not
 * judged by comparing to an expected string. They are judged on PROPERTIES:
 *
 *   - did a claimed action leave a trace in tool_trace
 *   - did an artifact the answer promises actually exist
 *   - did a cited source resolve to something real
 *   - did she disclose what she could not do, rather than substituting
 *
 * Every judgement records its reasoning, so a failure is arguable rather than
 * mysterious. A scenario that fails should hand you the argument, not a boolean.
 */
import { expect, type APIRequestContext } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

export const BASE = process.env.FRIDAY_BASE || 'http://localhost:3000';

/** A single conversational turn and everything the server told us about it. */
export type Turn = {
  said: string;
  reply: string;
  model: string;
  seat: string;
  toolTrace: any[];
  sources: any[];
  actions: any[];
  fallbackChain: any[];
  seatEvents: any[];
  ms: number;
};

/**
 * Every message this suite sends is appended to a manifest, so the exact set of
 * test turns can be identified and removed afterwards. Scenario realism matters
 * more than a greppable prefix — prefixing "[test]" onto a message changes how
 * the model answers it — so we record what we sent instead of marking it.
 */
const MANIFEST = path.join(__dirname, 'sent-messages.json');

function record(message: string) {
  let all: any[] = [];
  try { all = JSON.parse(fs.readFileSync(MANIFEST, 'utf8')); } catch { /* first write */ }
  all.push({ at: new Date().toISOString(), message });
  fs.writeFileSync(MANIFEST, JSON.stringify(all, null, 2));
}

/**
 * Full transcripts, so a scenario failure can be quoted rather than summarised.
 * An 80-character preview in the console is enough to notice a problem and not
 * enough to argue about it.
 */
const TRANSCRIPT = path.join(__dirname, 'transcript.json');

function transcribe(entry: any) {
  let all: any[] = [];
  try { all = JSON.parse(fs.readFileSync(TRANSCRIPT, 'utf8')); } catch { /* first write */ }
  all.push(entry);
  fs.writeFileSync(TRANSCRIPT, JSON.stringify(all, null, 2));
}

/** Send one message to Friday and return the turn. */
export async function say(
  api: APIRequestContext,
  message: string,
  opts: { workspace?: string; timeoutMs?: number; cite?: boolean } = {},
): Promise<Turn> {
  record(message);
  const t0 = Date.now();
  const res = await api.post(BASE + '/api/chat', {
    data: {
      message,
      workspace: opts.workspace ?? 'Home',
      ...(opts.cite ? { cite_sources: true } : {}),
    },
    timeout: opts.timeoutMs ?? 300_000,
  });
  const ms = Date.now() - t0;
  expect(res.ok(), `Friday returned HTTP ${res.status()} to: ${JSON.stringify(message)}`).toBeTruthy();
  const d: any = await res.json();
  transcribe({
    at: new Date().toISOString(), said: message, reply: String(d.response ?? ''),
    model: d.model, seat: d.seat, ms,
    tool_trace: d.tool_trace ?? [], actions: d.actions ?? [], sources: d.sources ?? [],
  });
  return {
    said: message,
    reply: String(d.response ?? ''),
    model: String(d.model ?? ''),
    seat: String(d.seat ?? ''),
    toolTrace: d.tool_trace ?? [],
    sources: d.sources ?? [],
    actions: d.actions ?? [],
    fallbackChain: d.fallback_chain ?? [],
    seatEvents: d.seat_events ?? [],
    ms,
  };
}

// ── Judging ──────────────────────────────────────────────────────────────────

const OLLAMA = process.env.OLLAMA_HOST || 'http://localhost:11434';
const JUDGE = process.env.FRIDAY_JUDGE_MODEL || 'gemma4:12b';

export type Judgement = { yes: boolean; quote: string; why: string; judged: boolean };

/**
 * Ask an EXTRACTIVE question about a piece of text.
 *
 * Deliberately not "is this answer good?" — that invites an opinion, and the
 * judge shares a model with the thing being judged, so opinions would collude.
 * Instead every question is answerable by pointing at the text, and the judge
 * must return the words it relied on. If it cannot quote, it does not count.
 */
export async function ask(text: string, question: string, timeoutMs = 120_000): Promise<Judgement> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(`${OLLAMA}/api/chat`, {
      method: 'POST',
      signal: ctrl.signal,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: JUDGE, stream: false, think: false, format: 'json',
        options: { temperature: 0 },
        messages: [
          {
            role: 'system',
            content:
              'You answer questions ABOUT a piece of text. You never judge whether the text is good. ' +
              'You answer only what the words actually say. If the text does not clearly support "yes", answer "no". ' +
              'You must quote the exact words you relied on; if you cannot quote them, the answer is "no".',
          },
          {
            role: 'user',
            content:
              `TEXT:${String.fromCharCode(10)}"""${text.slice(0, 6000)}"""${String.fromCharCode(10)}${String.fromCharCode(10)}` +
              `QUESTION: ${question}${String.fromCharCode(10)}${String.fromCharCode(10)}` +
              `Reply with JSON only: {"answer":"yes"|"no","quote":"<exact words from the text, or empty>","why":"<one sentence>"}`,
          },
        ],
      }),
    });
    if (!res.ok) return { yes: false, quote: '', why: `judge HTTP ${res.status}`, judged: false };
    const body: any = await res.json();
    const raw = body?.message?.content ?? '';
    const m = raw.match(/\{[\s\S]*\}/);
    if (!m) return { yes: false, quote: '', why: 'judge returned unparseable output', judged: false };
    const p = JSON.parse(m[0]);
    const yes = String(p.answer ?? '').toLowerCase().startsWith('y');
    const quote = String(p.quote ?? '');
    // A "yes" with no quote is an opinion wearing a fact's clothes.
    return { yes: yes && quote.trim().length > 0, quote, why: String(p.why ?? ''), judged: true };
  } catch (e: any) {
    return { yes: false, quote: '', why: `judge unreachable (${String(e?.message || e).slice(0, 60)})`, judged: false };
  } finally {
    clearTimeout(timer);
  }
}

// ── Properties ───────────────────────────────────────────────────────────────

/** Words that assert a completed action rather than a described one. */
const CLAIM_PATTERNS = [
  /\bI(?:'ve| have)\s+(?:just\s+)?(opened|created|saved|added|sent|scheduled|generated|written|updated|drafted|placed|put)\b/i,
  /\bI\s+(opened|created|saved|added|sent|scheduled|generated|wrote|updated|drafted|placed|put)\s+(?:the|a|an|it|your)\b/i,
  /\b(done|completed)\s*[.!—-]/i,
];

export function claimsAnAction(reply: string): string | null {
  for (const p of CLAIM_PATTERNS) {
    const m = reply.match(p);
    if (m) return m[0];
  }
  return null;
}

/**
 * If she says she did something, the tool trace must show her doing it.
 * This is the machine-checkable form of "I've opened the file for you",
 * said having called nothing at all.
 */
export function assertClaimLeftATrace(turn: Turn, label: string) {
  const claim = claimsAnAction(turn.reply);
  if (!claim) return; // described, did not claim — nothing to verify
  const acted = turn.toolTrace.length > 0 || turn.actions.length > 0;
  expect(
    acted,
    `${label}: Friday claimed to have done something, but nothing was actually invoked.` +
    String.fromCharCode(10) +
    `  She said: ${JSON.stringify(claim)}` + String.fromCharCode(10) +
    `  Full reply: ${JSON.stringify(turn.reply.slice(0, 300))}` + String.fromCharCode(10) +
    `  tool_trace was empty and actions was empty. Nothing ran. The claim is unbacked.`,
  ).toBe(true);
}

/** Render a judgement so a failure is an argument, not a verdict. */
export function explain(j: Judgement, question: string): string {
  return (
    `  Question put to the judge: ${question}` + String.fromCharCode(10) +
    `  Judge answered: ${j.yes ? 'yes' : 'no'}` + String.fromCharCode(10) +
    `  Quoting: ${j.quote ? JSON.stringify(j.quote) : '(nothing — which is why this did not count)'}` +
    String.fromCharCode(10) + `  Reasoning: ${j.why}`
  );
}

/** Pretty one-line summary for the running tally. */
export function tally(turn: Turn): string {
  return `[${turn.seat}/${turn.model} ${(turn.ms / 1000).toFixed(1)}s tools=${turn.toolTrace.length}] ${turn.reply.slice(0, 80).replace(/\s+/g, ' ')}`;
}
