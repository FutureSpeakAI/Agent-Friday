import { test, expect } from '../fixtures';
import { BASE } from '../harness';
import { quietPage } from '../helpers/quiet_page';

// F7: the Settings walk clicks hundreds of controls on a 2-4 core runner; its trace showed every round trip taking
// 6 to 9 s while the page rendered the 3D scene in software. The walk does not need the scene, so it starts the page
// with 3D off (the same switch three_d_off.spec.ts uses).

test('a page started through quietPage renders with 3D off', async ({ page }) => {
  await quietPage(page);
  await page.goto(`${BASE}/w/settings`, { waitUntil: 'domcontentloaded' });
  await expect(page.locator('html')).toHaveAttribute('data-render3d', 'off');
});
