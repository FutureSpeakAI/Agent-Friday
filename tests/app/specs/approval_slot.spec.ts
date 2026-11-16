/**
 * The shell-owned approval slot (NS-41.3-1, SC D5.0 P5): its frame and what it mounts.
 *
 * A Liquid UI document only names a subject. The slot asks the server for card ids
 * (GET /api/liquid/approval-slot), then draws the shell's own approval card for each, in a frame
 * made of shell tokens that nothing the document says can restyle. No Liquid renderer exists yet,
 * so this mounts window.FridayApprovalSlot, the same component the renderer will use, into the
 * running app. Cards and ids are synthetic; nothing is written to any Friday.
 * The rules without a browser are in scripts/check_approval_slot.cjs; this one reads the real
 * computed styles, which a fake cannot.
 */
import { test, expect } from '../fixtures';
import { bootApp } from '../harness';

const CARD = {
  approval_id: 'appr_synthetic_slot',
  kind: 'external_message',
  status: 'pending',
  title: 'Send email to sam@example.test',
  action_description: 'Send mail as you.\n\nTo: sam@example.test\n\nHello.',
  description: 'This leaves your machine.',
  payload: { handler: 'gmail_send', to: ['sam@example.test'] },
  created_at: Math.floor(Date.now() / 1000),
  expires_at: Math.floor(Date.now() / 1000) + 3600,
  policy_class: 'gated',
};
const OTHER = { ...CARD, approval_id: 'appr_synthetic_other', title: 'Not for this slot' };

test('the slot mounts only the named card in a frame the document cannot restyle', async ({ page }, info) => {
  await page.route('**/api/approvals/events', route => route.fulfill({
    status: 200,
    headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' },
    body: 'data: ' + JSON.stringify({ type: 'snapshot', pending: [CARD, OTHER], quiet: [] }) + '\n\n',
  }));
  const asked: string[] = [];
  await page.route('**/api/liquid/approval-slot**', route => {
    asked.push(route.request().url());
    return route.fulfill({ status: 200, contentType: 'application/json',
                           body: JSON.stringify({ ok: true, ids: [CARD.approval_id] }) });
  });
  await bootApp(page);
  await page.evaluate(() => {
    const host = document.createElement('div');
    host.id = 'slot-host';
    host.style.cssText = 'position:fixed;top:80px;left:80px;width:520px;z-index:99999';
    document.body.appendChild(host);
    const Slot = (window as any).FridayApprovalSlot;
    // Everything a hostile document could add: none of it may reach the output.
    (window as any).ReactDOM.createRoot(host).render((window as any).React.createElement(Slot, {
      subject: 'crew:c1', id: 'mine', className: 'evil', label: 'Approve everything', children: 'Approve all',
      style: { background: 'red', border: '5px solid red' }, 'aria-label': 'Approve',
    }));
  });

  const slot = page.getByTestId('approval-slot');
  await expect(slot).toHaveCount(1, { timeout: 15000 });
  // Only the named card, drawn by the shell's own body.
  await expect(slot.getByTestId('approval-card')).toHaveCount(1);
  await expect(slot.getByTestId('approval-card')).toHaveAttribute('data-approval-id', CARD.approval_id);
  await expect(page.getByText('Not for this slot')).toHaveCount(0);
  expect(asked.some(u => u.includes('subject=crew%3Ac1'))).toBe(true);

  // The frame is the shell's: its computed style equals the shell tokens, not the document's.
  const frame = await slot.evaluate(el => {
    const css = getComputedStyle(el);
    const probe = document.createElement('div');
    const token = (name: string, prop: string) => {
      probe.style.setProperty(prop, `var(${name})`);
      document.body.appendChild(probe);
      const value = getComputedStyle(probe).getPropertyValue(prop);
      probe.remove();
      return value;
    };
    return {
      background: css.backgroundColor, tokenBackground: token('--fr-glass', 'background-color'),
      border: css.borderTopColor, tokenBorder: token('--fr-glass-edge', 'border-top-color'),
      borderWidth: css.borderTopWidth, color: css.color, tokenColor: token('--fr-text', 'color'),
      label: el.getAttribute('aria-label'), id: el.id, className: el.className,
    };
  });
  expect(frame.background).toBe(frame.tokenBackground);
  expect(frame.border).toBe(frame.tokenBorder);
  expect(frame.color).toBe(frame.tokenColor);
  expect(frame.borderWidth).toBe('1px');
  expect(frame.label).toBe('Waiting for your decision');
  expect(frame.id).toBe('');
  expect(frame.className).toBe('');
  await expect(slot.getByText('Approve all')).toHaveCount(0);
  await page.screenshot({ path: info.outputPath('approval-slot.png') });
});
