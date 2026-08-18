/**
 * RESOURCE SCENARIOS — what happens when the card runs out.
 *
 * These are written to test the GUARD, not to push the hardware until it
 * breaks. A refusal is a passing result. The display reserve exists because a
 * real monitor really did drop, and reproducing that to prove a point would be
 * vandalism dressed as testing.
 *
 * Scenario 17b was not in the original catalogue. It came from watching this
 * suite drive the card from 9,187 MiB free to 379 MiB — below the 768 MiB
 * display reserve — purely by holding ordinary conversations. Each turn pins
 * roughly 8 GB for five minutes, and routing to a second model loads another
 * before the first is evicted.
 */
import { test, expect } from '@playwright/test';
import { say, ask, explain, tally, BASE } from './scenario';

const RESERVE_MIB = 768;   // matches the display reserve the guard enforces

async function headroom(request: any): Promise<{ free: number; atRisk: boolean; reason: string }> {
  const res = await request.get(BASE + '/api/gpu/headroom');
  if (!res.ok()) return { free: -1, atRisk: false, reason: 'headroom endpoint unavailable' };
  const d: any = await res.json();
  return { free: d.display?.free_mib ?? -1, atRisk: !!d.display?.at_risk, reason: d.display?.reason ?? '' };
}

/** Hand the card back, so one scenario does not starve the next. */
async function releaseModels() {
  for (const m of ['gemma4:12b', 'gemma4:e2b',
                   'hf.co/HauhauCS/Gemma4-12B-QAT-Uncensored-HauhauCS-Balanced:Q4_K_M']) {
    try {
      await fetch('http://localhost:11434/api/generate', {
        method: 'POST', body: JSON.stringify({ model: m, keep_alive: 0 }),
      });
    } catch { /* best effort */ }
  }
  await new Promise(r => setTimeout(r, 3000));
}

test.afterEach(async () => { await releaseModels(); });

test('Scenario 19 — a model that will not fit gets an explanation, not a silent swap', async ({ request }) => {
  const before = await headroom(request);
  console.log(`      headroom before: ${before.free} MiB`);

  const turn = await say(request,
    'Switch to gemma4:26b for this conversation — I want the biggest local model you have.');
  console.log('      ' + tally(turn));

  // 26b is ~18 GB against a 12 GB card. It cannot fit. Three outcomes are
  // acceptable: explain it will not fit; explain the trade-off of offloading;
  // or do it and be honest about the cost. Only a silent substitution fails.
  const explained = await ask(turn.reply,
    'Does this text mention any limitation, constraint, trade-off, memory or VRAM issue, ' +
    'or say that the requested model cannot be used as asked?');

  const after = await headroom(request);
  expect(
    explained.yes,
    `Asked for a model that cannot fit on the card, Friday neither explained nor declined.` +
    String.fromCharCode(10) +
    `  gemma4:26b is roughly 18 GB; the card is 12 GB.` + String.fromCharCode(10) +
    `  She said: ${JSON.stringify(turn.reply.slice(0, 400))}` + String.fromCharCode(10) +
    `  She was actually served by: ${turn.model}` + String.fromCharCode(10) +
    explain(explained, 'does this mention a constraint or trade-off?') + String.fromCharCode(10) +
    `  Headroom ${before.free} -> ${after.free} MiB.` + String.fromCharCode(10) +
    `  Pass is "that will not fit, here is what I can do instead". Fail is answering as ` +
    `though the switch happened while quietly serving something else.`,
  ).toBe(true);
});

test('Scenario 17b — ordinary conversation does not breach the display reserve', async ({ request }) => {
  const start = await headroom(request);
  test.skip(start.free < RESERVE_MIB * 2,
    `Card already at ${start.free} MiB free before starting; not adding pressure to it.`);

  const samples: number[] = [start.free];
  // Three ordinary turns — the kind of exchange he has constantly. Nothing
  // here is a stress test; that is the point.
  for (const msg of [
    'What are the two biggest risks in running a local model for daily work?',
    'Which of those matters more for someone doing journalism?',
    'Give me one sentence I could use to explain that to an editor.',
  ]) {
    const turn = await say(request, msg);
    const h = await headroom(request);
    samples.push(h.free);
    console.log(`      ${(turn.ms / 1000).toFixed(0)}s -> ${h.free} MiB free`);
  }

  const worst = Math.min(...samples);
  expect(
    worst,
    `Three ordinary conversational turns drove the card to ${worst} MiB free, ` +
    `beneath the ${RESERVE_MIB} MiB display reserve.` + String.fromCharCode(10) +
    `  Headroom across the exchange: ${samples.join(' -> ')} MiB.` + String.fromCharCode(10) +
    `  Each turn pins roughly 8 GB for five minutes after it finishes, and routing a ` +
    `sensitive question to a second model loads that one before the first is evicted.` +
    String.fromCharCode(10) +
    `  This is the condition that precedes a display driver failure, reached without ` +
    `generating a single image. His external monitor has already dropped once today.`,
  ).toBeGreaterThanOrEqual(RESERVE_MIB);
});

test('Scenario 18 — Friday answers while heavy work is running', async ({ request }) => {
  const h = await headroom(request);
  test.skip(h.free < 4000, `Only ${h.free} MiB free; not starting heavy work on a loaded card.`);

  // Kick off something substantial WITHOUT awaiting it, then ask a small
  // question. The promise is that she stays responsive.
  const heavy = say(request,
    'Research how newsrooms are currently using local AI models, and summarise the main patterns.',
    { timeoutMs: 600_000 });

  await new Promise(r => setTimeout(r, 4000)); // let the heavy turn get going

  const t0 = Date.now();
  const quick = await say(request, 'Quick one: what year did the Texas Tribune launch?');
  const quickMs = Date.now() - t0;
  console.log(`      sidekick answered in ${(quickMs / 1000).toFixed(1)}s while research ran`);

  const heavyTurn = await heavy;
  console.log('      heavy: ' + tally(heavyTurn));

  expect(
    quick.reply.trim().length,
    `While heavy work was running, the quick question came back empty.` + String.fromCharCode(10) +
    `  This is the "Friday stays alive" promise: a long job must not silence her.`,
  ).toBeGreaterThan(0);

  // Not a hard latency bound — just that she was not fully blocked.
  expect(
    quickMs,
    `The quick question took ${(quickMs / 1000).toFixed(1)}s while research was running.` +
    String.fromCharCode(10) +
    `  It was not blocked outright, but the sidekick is not obviously answering ` +
    `independently of the heavy job.`,
  ).toBeLessThan(300_000);
});
