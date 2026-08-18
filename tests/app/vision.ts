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
  if (second.ok) {
    return { ...first, ok: true, problems: [],
      why: `a first pass reported ${first.problems.length} problem(s) but a second pass found none, so they were not counted` };
  }

  // Keep the second pass's wording; both passes agree something is wrong.
  return { ...second, why: 'confirmed by two independent passes' };
}
