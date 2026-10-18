/**
 * Machine-vision judging.
 *
 * Some defects cannot be expressed as a DOM assertion. "The model picker is
 * sloppy and bad" is a real defect report and no selector catches it. So we
 * screenshot the running app and have a model look at it.
 *
 * Three deliberate choices:
 *
 * 1. LOCAL BY DEFAULT. A screenshot of Friday contains real calendar entries,
 *    messages, family and health and finance data. Those must not leave the
 *    machine to satisfy a test. The judge runs on Ollama. Cloud judging exists
 *    but is opt-in via FRIDAY_VISION_CLOUD=1 and should only be pointed at
 *    screens you know are free of personal data.
 *
 * 2. JUDGED AGAINST STATED INTENT, NOT A GOLDEN IMAGE. A golden image freezes
 *    today's bugs as correct — the reverse-sorted gallery would have become
 *    the reference. We say what the screen is supposed to achieve and ask
 *    whether it does.
 *
 * 3. ASKED TO FIND FAULT. Asked "does this look fine?" a model says yes. It is
 *    asked to list what is wrong and must return a structured verdict.
 */

import { test, expect, type Locator, type Page } from '@playwright/test';

const OLLAMA = process.env.OLLAMA_HOST || 'http://localhost:11434';
const MODEL = process.env.FRIDAY_VISION_MODEL || 'gemma4:12b';

export type Verdict = {
  ok: boolean;
  problems: string[];
  sees: string;
  judged: boolean;   // false when no judge was available; the test then skips
  why?: string;
};

const RUBRIC = `You are inspecting a screenshot of a desktop application during an automated UI test.
Your job is to FIND FAULTS. A screenshot that looks broadly fine usually still has something wrong with it.

Report a problem ONLY if it is visible in the image. Look specifically for:
- text that is clipped, truncated, overlapping other text, or running outside its container
- text too low-contrast against its background to read
- a control (dropdown, button, list) that is unreadable, empty when it should have content, or visually broken
- images that failed to load (broken-image icon, empty frame, grey placeholder box)
- elements that are duplicated when there should be one
- layout that is visibly misaligned, cut off at an edge, or overlapping
- a screen that is blank or nearly blank when it should show content

Do NOT report: colour or style preferences, anything you merely suspect but cannot see,
or the absence of features you think ought to exist.`;

function extractJson(raw: string): any | null {
  // Local model output is a network boundary, not a promise. It may arrive
  // wrapped in prose, in a fenced block, or with a thinking preamble.
  if (!raw) return null;
  const fenced = raw.match(/```(?:json)?\s*([\s\S]*?)```/);
  const candidates = [fenced?.[1], raw];
  for (const c of candidates) {
    if (!c) continue;
    const start = c.indexOf('{');
    const end = c.lastIndexOf('}');
    if (start === -1 || end <= start) continue;
    try { return JSON.parse(c.slice(start, end + 1)); } catch { /* keep trying */ }
  }
  return null;
}

async function askOllama(pngBase64: string, intent: string, timeoutMs: number): Promise<Verdict> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(`${OLLAMA}/api/chat`, {
      method: 'POST',
      signal: ctrl.signal,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: MODEL,
        stream: false,
        think: false,
        format: 'json',
        options: { temperature: 0 },
        messages: [
          { role: 'system', content: RUBRIC },
          {
            role: 'user',
            images: [pngBase64],
            content:
              `This screen is supposed to: ${intent}\n\n` +
              `Reply with JSON only, exactly this shape:\n` +
              `{"sees": "<one sentence describing what is actually on screen>", ` +
              `"problems": ["<each visible problem, one short sentence>"]}\n` +
              `If nothing is visibly wrong, return an empty problems array.`,
          },
        ],
      }),
    });
    if (!res.ok) return { ok: true, problems: [], sees: '', judged: false, why: `judge HTTP ${res.status}` };
    const body: any = await res.json();
    const parsed = extractJson(body?.message?.content ?? '');
    if (!parsed) return { ok: true, problems: [], sees: '', judged: false, why: 'judge returned unparseable output' };
    const problems = Array.isArray(parsed.problems)
      ? parsed.problems.map((p: any) => String(p)).filter((p: string) => p.trim()).slice(0, 12)
      : [];
    return { ok: problems.length === 0, problems, sees: String(parsed.sees ?? '').slice(0, 300), judged: true };
  } catch (e: any) {
    return { ok: true, problems: [], sees: '', judged: false, why: `judge unreachable (${e?.name === 'AbortError' ? 'timed out' : String(e?.message || e).slice(0, 80)})` };
  } finally {
    clearTimeout(timer);
  }
}

/** Judge a screenshot against what the screen is meant to achieve. */
export async function judge(png: Buffer, intent: string, timeoutMs = 120000): Promise<Verdict> {
  if (process.env.FRIDAY_VISION === 'off') {
    return { ok: true, problems: [], sees: '', judged: false, why: 'vision judging disabled (FRIDAY_VISION=off)' };
  }
  return askOllama(png.toString('base64'), intent, timeoutMs);
}

// ── "Can a person actually see it?" ───────────────────────────────────────────
//
// Some defects pass every DOM assertion and still fail the person: the Activity chip
// reported data-overflow="false" while Windows fonts pushed it out of sight. expectVisible
// adds a second, independent look, IN ADDITION TO the DOM checks, never instead of them.
//
//  - OPT-IN: runs only with FRIDAY_VISION_CHECK=1 (Windows CI and anyone who asks).
//  - LOCAL ONLY: the screenshot goes to Ollama on this machine (OLLAMA_HOST must be a
//    loopback address); there is no cloud path here, whatever FRIDAY_VISION_CLOUD says.
//  - GRACEFUL: with no Ollama, no vision model pulled, or a model that will not answer,
//    the check is skipped and says why in the test's annotations; the test does not fail.
//  - 2 OF 3: the model is asked three times at temperature 0.2 and the answer counts only
//    if two agree; a split or an unreadable answer is "not judged", not a failure.

export type Seen = { judged: boolean; passed: boolean | null; reasoning: string; votes: (boolean | null)[] };

const LOOPBACK = /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(:\d+)?\/?$/i;

async function localVisionModel(): Promise<string | null> {
  if (!LOOPBACK.test(OLLAMA)) return null;          // never a remote judge
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 2000);
  try {
    const res = await fetch(`${OLLAMA}/api/tags`, { signal: ctrl.signal });
    if (!res.ok) return null;
    const body: any = await res.json();
    const names: string[] = (body?.models || []).map((m: any) => String(m?.name || m?.model || ''));
    const want = MODEL.includes(':') ? [MODEL] : [MODEL, MODEL + ':latest'];
    return names.some(n => want.includes(n)) ? MODEL : null;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

async function askVisible(png: Buffer, what: string, timeoutMs: number): Promise<{ visible: boolean | null; why: string }> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(`${OLLAMA}/api/chat`, {
      method: 'POST', signal: ctrl.signal, headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: MODEL, stream: false, think: false, format: 'json', options: { temperature: 0.2 },
        messages: [{
          role: 'user', images: [png.toString('base64')],
          content: `Look at this screenshot of part of a desktop application. Is the following fully visible and ` +
            `readable to a person looking at the screen, not cut off, not covered and not hidden: ${what}?\n` +
            `Reply with JSON only: {"visible": true or false, "why": "<one short sentence about what you see>"}`,
        }],
      }),
    });
    if (!res.ok) return { visible: null, why: `judge HTTP ${res.status}` };
    const parsed = extractJson(((await res.json()) as any)?.message?.content ?? '');
    if (!parsed || typeof parsed.visible !== 'boolean') return { visible: null, why: 'unreadable answer' };
    return { visible: parsed.visible, why: String(parsed.why ?? '').slice(0, 200) };
  } catch (e: any) {
    return { visible: null, why: e?.name === 'AbortError' ? 'timed out' : String(e?.message || e).slice(0, 80) };
  } finally {
    clearTimeout(timer);
  }
}

/** Ask the local model, three times, whether `what` is visible in the screenshot; two agreeing answers decide. */
export async function seeVisible(png: Buffer, what: string, timeoutMs = 90000): Promise<Seen> {
  if (process.env.FRIDAY_VISION_CHECK !== '1' || process.env.FRIDAY_VISION === 'off') {
    return { judged: false, passed: null, reasoning: 'vision check is opt-in (FRIDAY_VISION_CHECK=1)', votes: [] };
  }
  if (!(await localVisionModel())) {
    return { judged: false, passed: null, votes: [],
      reasoning: `no local vision model (${MODEL} on ${OLLAMA}); the DOM checks stand alone` };
  }
  const votes: (boolean | null)[] = [];
  const whys: string[] = [];
  for (let i = 0; i < 3; i++) {
    const a = await askVisible(png, what, timeoutMs);
    votes.push(a.visible); whys.push(a.why);
    const yes = votes.filter(v => v === true).length, no = votes.filter(v => v === false).length;
    if (yes >= 2 || no >= 2) {
      return { judged: true, passed: yes >= 2, votes, reasoning: whys.join(' | ') };
    }
  }
  return { judged: false, passed: null, votes, reasoning: 'the three looks did not agree: ' + whys.join(' | ') };
}

/**
 * In a spec, after the DOM assertions: screenshot `area` (the element, or a region around it) and, when the
 * opt-in local check runs, fail if two of three looks say `what` cannot be seen. Otherwise record why it did
 * not run in the test's annotations and carry on.
 */
export async function expectVisible(area: Locator | Page, what: string): Promise<Seen> {
  const png = await area.screenshot();
  const seen = await seeVisible(png, what);
  test.info().annotations.push({ type: 'vision-check',
    description: seen.judged ? `${seen.passed ? 'visible' : 'NOT visible'} (${seen.votes.join(',')}): ${seen.reasoning}` : seen.reasoning });
  if (seen.judged) expect(seen.passed, `A local model looked and says this cannot be seen: ${what}\n  ${seen.reasoning}`).toBe(true);
  return seen;
}

/** Render a verdict as something a person can read in test output. */
export function describeVerdict(v: Verdict, intent: string): string {
  return (
    `A model looked at this screen and found ${v.problems.length} problem${v.problems.length === 1 ? '' : 's'}.\n` +
    `  The screen is meant to: ${intent}\n` +
    `  What the model saw: ${v.sees || '(no description)'}\n` +
    v.problems.map(p => `  - ${p}`).join('\n')
  );
}

/**
 * Judge, and if anything is found, judge once more before believing it.
 *
 * A model looking at the same screen twice does not always say the same thing.
 * Left alone that makes the vision tier flaky, and a flaky tier trains people
 * to ignore red — which is worse than having no tier at all.
 *
 * So a problem must survive being found twice. This costs nothing on a clean
 * screen (the second pass only runs when the first found something) and it
 * filters the one-off misreadings while keeping anything genuinely visible.
 */
export async function judgeConfirmed(png: Buffer, intent: string, timeoutMs = 120000): Promise<Verdict> {
  const first = await judge(png, intent, timeoutMs);
  if (!first.judged || first.ok) return first;

  const second = await judge(png, intent, timeoutMs);
  if (!second.judged) return { ...first, judged: false, why: second.why };

  // A problem counts only if BOTH passes describe THE SAME problem. Requiring
  // merely that both passes find something let a hallucination through: one
  // pass complained about overlapping text on a model named nowhere on the
  // screen, the other pass complained about something unrelated, and the
  // disagreement was scored as confirmation (caught 2026-08-18). Same-problem
  // is approximated by content-word overlap, which is crude and good enough:
  // real defects get described with the same nouns twice.
  const words = (t: string) => new Set(
    t.toLowerCase().replace(/[^a-z0-9 ]/g, ' ').split(/\s+/)
      .filter(w => w.length > 3 && !['text','icon','image','screen','button','section','visible','appears','missing','broken'].includes(w)));
  const agrees = (a: string, b: string) => {
    const wa = words(a); const wb = words(b);
    let hit = 0; for (const w of wa) if (wb.has(w)) hit++;
    return hit >= 2;
  };
  const confirmed = second.problems.filter(p2 => first.problems.some(p1 => agrees(p1, p2)));
  if (confirmed.length === 0) {
    return { ...first, ok: true, problems: [],
      why: 'two passes each found problems but not the same ones — treated as noise, not signal' };
  }
  return { ok: false, problems: confirmed, sees: second.sees, judged: true,
           why: 'the same problem was independently described by two passes' };
}
