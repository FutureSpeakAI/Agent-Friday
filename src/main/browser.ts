import puppeteer, { Browser, Page } from 'puppeteer-core';
import path from 'path';
import { app } from 'electron';

const CHROME_PATHS = [
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  process.env.CHROME_PATH || '',
].filter(Boolean);

function findChrome(): string {
  const fs = require('fs');
  for (const p of CHROME_PATHS) {
    if (fs.existsSync(p)) return p;
  }
  throw new Error('Chrome not found. Set CHROME_PATH in .env');
}

class BrowserManager {
  private browser: Browser | null = null;
  private page: Page | null = null;
  private userDataDir: string;

  constructor() {
    this.userDataDir = path.join(app.getPath('userData'), 'chrome-profile');
  }

  async launch(): Promise<string> {
    if (this.browser && this.page) {
      return 'Browser already open.';
    }

    const chromePath = findChrome();
    this.browser = await puppeteer.launch({
      executablePath: chromePath,
      headless: false,
      userDataDir: this.userDataDir,
      defaultViewport: null,
      args: [
        '--no-first-run',
        '--no-default-browser-check',
        '--disable-features=TranslateUI',
        '--window-size=1280,800',
        '--window-position=100,100',
      ],
    });

    const pages = await this.browser.pages();
    this.page = pages[0] || await this.browser.newPage();

    this.browser.on('disconnected', () => {
      this.browser = null;
      this.page = null;
    });

    return 'Browser launched.';
  }

  async navigate(url: string): Promise<string> {
    const page = await this.ensurePage();
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 15000 });
    const title = await page.title();
    return `Navigated to "${title}" (${url})`;
  }

  async readPage(selector?: string): Promise<string> {
    const page = await this.ensurePage();
    if (selector) {
      const el = await page.$(selector);
      if (!el) return `No element found for selector: ${selector}`;
      const text = await el.evaluate((e) => (e as HTMLElement).innerText);
      return text.slice(0, 8000);
    }
    const text = await page.evaluate(() => document.body.innerText);
    return text.slice(0, 8000);
  }

  async click(selectorOrText: string): Promise<string> {
    const page = await this.ensurePage();

    // Try as CSS selector first
    try {
      await page.click(selectorOrText);
      return `Clicked element: ${selectorOrText}`;
    } catch {
      // Fall through to text search
    }

    // Try finding by text content
    const clicked = await page.evaluate((searchText) => {
      const elements = document.querySelectorAll('a, button, [role="button"], input[type="submit"], input[type="button"]');
      for (const el of elements) {
        if ((el as HTMLElement).innerText?.toLowerCase().includes(searchText.toLowerCase())) {
          (el as HTMLElement).click();
          return true;
        }
      }
      return false;
    }, selectorOrText);

    if (clicked) return `Clicked element with text: "${selectorOrText}"`;
    return `Could not find clickable element: "${selectorOrText}"`;
  }

  async type(text: string, selector?: string): Promise<string> {
    const page = await this.ensurePage();
    if (selector) {
      await page.click(selector).catch(() => {});
      await page.type(selector, text, { delay: 20 });
    } else {
      await page.keyboard.type(text, { delay: 20 });
    }
    return `Typed: "${text.slice(0, 100)}${text.length > 100 ? '...' : ''}"`;
  }

  async pressKey(key: string): Promise<string> {
    const page = await this.ensurePage();
    await page.keyboard.press(key as any);
    return `Pressed key: ${key}`;
  }

  async screenshot(): Promise<string> {
    const page = await this.ensurePage();
    const buffer = await page.screenshot({ encoding: 'base64', type: 'jpeg', quality: 70 });
    return buffer as string;
  }

  async goBack(): Promise<string> {
    const page = await this.ensurePage();
    await page.goBack({ waitUntil: 'domcontentloaded' }).catch(() => {});
    const title = await page.title();
    return `Went back to: "${title}"`;
  }

  async listTabs(): Promise<string> {
    if (!this.browser) return 'Browser not open.';
    const pages = await this.browser.pages();
    const tabs = await Promise.all(
      pages.map(async (p, i) => {
        const title = await p.title().catch(() => 'Untitled');
        const url = p.url();
        return `[${i}] ${title} — ${url}`;
      })
    );
    return tabs.join('\n');
  }

  async switchTab(index: number): Promise<string> {
    if (!this.browser) return 'Browser not open.';
    const pages = await this.browser.pages();
    if (index < 0 || index >= pages.length) return `Invalid tab index: ${index}`;
    this.page = pages[index];
    await this.page.bringToFront();
    const title = await this.page.title();
    return `Switched to tab [${index}]: "${title}"`;
  }

  async newTab(url?: string): Promise<string> {
    if (!this.browser) await this.launch();
    const page = await this.browser!.newPage();
    this.page = page;
    if (url) {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 15000 });
    }
    const title = await page.title();
    return `Opened new tab: "${title}"`;
  }

  async waitForText(text: string, timeoutMs = 10000): Promise<string> {
    const page = await this.ensurePage();
    try {
      await page.waitForFunction(
        (t) => document.body.innerText.includes(t),
        { timeout: timeoutMs },
        text
      );
      return `Text "${text}" appeared on page.`;
    } catch {
      return `Timed out waiting for text: "${text}"`;
    }
  }

  async closeTab(index?: number): Promise<string> {
    if (!this.browser) return 'Browser not open.';
    const pages = await this.browser.pages();
    if (index !== undefined) {
      if (index < 0 || index >= pages.length) return `Invalid tab index: ${index}`;
      const closingCurrent = pages[index] === this.page;
      await pages[index].close();
      if (closingCurrent) {
        const remaining = await this.browser.pages();
        this.page = remaining.length > 0 ? remaining[remaining.length - 1] : null;
      }
      return `Closed tab [${index}]`;
    }
    // Close current tab
    if (this.page) {
      await this.page.close();
      const remaining = await this.browser.pages();
      this.page = remaining.length > 0 ? remaining[remaining.length - 1] : null;
    }
    return 'Closed current tab.';
  }

  async closeOtherTabs(): Promise<string> {
    if (!this.browser || !this.page) return 'Browser not open.';
    const pages = await this.browser.pages();
    let closed = 0;
    for (const p of pages) {
      if (p !== this.page) {
        await p.close().catch(() => {});
        closed++;
      }
    }
    return `Closed ${closed} other tab(s).`;
  }

  async minimizeBrowser(): Promise<string> {
    if (!this.browser) return 'Browser not open.';
    try {
      // Get the browser's window handle via CDP and minimize it
      const pages = await this.browser.pages();
      if (pages.length === 0) return 'No pages open.';
      const client = await pages[0].createCDPSession();
      // Get the window ID for the current target
      const { windowId } = await client.send('Browser.getWindowForTarget') as { windowId: number };
      await client.send('Browser.setWindowBounds', {
        windowId,
        bounds: { windowState: 'minimized' },
      });
      await client.detach();
      return 'Browser minimized.';
    } catch (err) {
      // Crypto Sprint 17: Sanitize error output.
      console.warn('[Browser] Minimize failed, trying fallback:', err instanceof Error ? err.message : 'Unknown error');
      // Fallback: move the window off-screen
      try {
        const pages = await this.browser.pages();
        if (pages.length > 0) {
          const client = await pages[0].createCDPSession();
          const { windowId } = await client.send('Browser.getWindowForTarget') as { windowId: number };
          await client.send('Browser.setWindowBounds', {
            windowId,
            bounds: { left: -2000, top: -2000, width: 800, height: 600, windowState: 'normal' },
          });
          await client.detach();
          return 'Browser moved to background.';
        }
      } catch { /* minimize failed */ }
      return 'Could not minimize browser.';
    }
  }

  async scroll(direction: 'up' | 'down' = 'down', amount = 500): Promise<string> {
    const page = await this.ensurePage();
    if (direction === 'down') {
      await page.evaluate((px) => window.scrollBy(0, px), amount);
    } else {
      await page.evaluate((px) => window.scrollBy(0, -px), amount);
    }
    const scrollY = await page.evaluate(() => Math.round(window.scrollY));
    const maxScroll = await page.evaluate(() => Math.round(document.body.scrollHeight - window.innerHeight));
    return `Scrolled ${direction} by ${amount}px. Position: ${scrollY}/${maxScroll}px.`;
  }

  async getUrl(): Promise<string> {
    const page = await this.ensurePage();
    return page.url();
  }

  async selectOption(selector: string, value: string): Promise<string> {
    const page = await this.ensurePage();
    try {
      await page.select(selector, value);
      return `Selected option "${value}" in ${selector}`;
    } catch {
      // Try by visible text
      const selected = await page.evaluate(
        (sel, text) => {
          const selectEl = document.querySelector(sel) as HTMLSelectElement;
          if (!selectEl) return false;
          const options = Array.from(selectEl.options);
          const match = options.find(
            (o) => o.text.toLowerCase().includes(text.toLowerCase()) || o.value.toLowerCase().includes(text.toLowerCase())
          );
          if (match) {
            selectEl.value = match.value;
            selectEl.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
          }
          return false;
        },
        selector,
        value,
      );
      if (selected) return `Selected option matching "${value}" in ${selector}`;
      return `Could not select "${value}" in ${selector}`;
    }
  }

  async closeBrowser(): Promise<string> {
    if (this.browser) {
      await this.browser.close().catch(() => {});
      this.browser = null;
      this.page = null;
    }
    return 'Browser closed.';
  }

  private async ensurePage(): Promise<Page> {
    if (!this.browser || !this.page) {
      await this.launch();
    }
    return this.page!;
  }
}

export const browserManager = new BrowserManager();

// Tool definitions for Claude
export const browserToolDefs = [
  {
    name: 'browser_launch',
    description: 'Launch Chrome browser. Call this before any other browser action.',
    input_schema: { type: 'object' as const, properties: {}, required: [] },
  },
  {
    name: 'browser_navigate',
    description: 'Navigate to a URL in the browser.',
    input_schema: {
      type: 'object' as const,
      properties: { url: { type: 'string', description: 'URL to navigate to' } },
      required: ['url'],
    },
  },
  {
    name: 'browser_read_page',
    description: 'Read text content from the current page. Optionally pass a CSS selector to read a specific element.',
    input_schema: {
      type: 'object' as const,
      properties: { selector: { type: 'string', description: 'Optional CSS selector' } },
    },
  },
  {
    name: 'browser_click',
    description: 'Click an element by CSS selector or by visible text content.',
    input_schema: {
      type: 'object' as const,
      properties: { target: { type: 'string', description: 'CSS selector or text content of element to click' } },
      required: ['target'],
    },
  },
  {
    name: 'browser_type',
    description: 'Type text into a field. Optionally specify a CSS selector to target a specific input.',
    input_schema: {
      type: 'object' as const,
      properties: {
        text: { type: 'string', description: 'Text to type' },
        selector: { type: 'string', description: 'Optional CSS selector of input field' },
      },
      required: ['text'],
    },
  },
  {
    name: 'browser_press_key',
    description: 'Press a keyboard key (e.g. Enter, Tab, Escape, ArrowDown).',
    input_schema: {
      type: 'object' as const,
      properties: { key: { type: 'string', description: 'Key to press' } },
      required: ['key'],
    },
  },
  {
    name: 'browser_screenshot',
    description:
      'Take a screenshot of the current page. The screenshot image will be sent to you for visual analysis. ' +
      'ALWAYS take a screenshot after navigating to a new page, clicking elements, or when you need to verify what is on screen. ' +
      'This is your primary way of SEEING what the browser shows.',
    input_schema: { type: 'object' as const, properties: {} },
  },
  {
    name: 'browser_scroll',
    description: 'Scroll the page up or down. Use this to see more content, find elements below the fold, or navigate long pages.',
    input_schema: {
      type: 'object' as const,
      properties: {
        direction: { type: 'string', description: '"down" (default) or "up"' },
        amount: { type: 'number', description: 'Pixels to scroll (default 500)' },
      },
    },
  },
  {
    name: 'browser_get_url',
    description: 'Get the current page URL. Useful for checking where you are after navigation or redirects.',
    input_schema: { type: 'object' as const, properties: {} },
  },
  {
    name: 'browser_select',
    description: 'Select an option from a dropdown/select element by CSS selector and value or visible text.',
    input_schema: {
      type: 'object' as const,
      properties: {
        selector: { type: 'string', description: 'CSS selector of the select element' },
        value: { type: 'string', description: 'Option value or visible text to select' },
      },
      required: ['selector', 'value'],
    },
  },
  {
    name: 'browser_go_back',
    description: 'Navigate back in browser history.',
    input_schema: { type: 'object' as const, properties: {} },
  },
  {
    name: 'browser_list_tabs',
    description: 'List all open browser tabs with their titles and URLs.',
    input_schema: { type: 'object' as const, properties: {} },
  },
  {
    name: 'browser_switch_tab',
    description: 'Switch to a tab by its index (from browser_list_tabs).',
    input_schema: {
      type: 'object' as const,
      properties: { index: { type: 'number', description: 'Tab index' } },
      required: ['index'],
    },
  },
  {
    name: 'browser_new_tab',
    description: 'Open a new browser tab, optionally navigating to a URL.',
    input_schema: {
      type: 'object' as const,
      properties: { url: { type: 'string', description: 'Optional URL to open' } },
    },
  },
  {
    name: 'browser_wait_for_text',
    description: 'Wait until specific text appears on the page (up to 10 seconds).',
    input_schema: {
      type: 'object' as const,
      properties: { text: { type: 'string', description: 'Text to wait for' } },
      required: ['text'],
    },
  },
  {
    name: 'browser_close_tab',
    description: 'Close a browser tab by index. If no index is provided, closes the current tab. Use this to keep tabs tidy after reading pages.',
    input_schema: {
      type: 'object' as const,
      properties: { index: { type: 'number', description: 'Optional tab index to close (from browser_list_tabs)' } },
    },
  },
  {
    name: 'browser_close_other_tabs',
    description: 'Close all browser tabs except the current one. Great for cleaning up after multi-tab research.',
    input_schema: { type: 'object' as const, properties: {} },
  },
  {
    name: 'browser_minimize',
    description: 'Minimize the browser window so the user returns to their desktop or the Agent Friday app. Always call this after completing browser work.',
    input_schema: { type: 'object' as const, properties: {} },
  },
  {
    name: 'browser_close',
    description: 'Close the browser completely.',
    input_schema: { type: 'object' as const, properties: {} },
  },
];

/** Browser tool declarations in Gemini function_declarations format */
export const BROWSER_TOOL_DECLARATIONS = browserToolDefs.map((t) => ({
  name: t.name,
  description: t.description,
  parameters: {
    type: 'object' as const,
    properties: Object.fromEntries(
      Object.entries(t.input_schema.properties || {}).map(([k, v]) => [
        k,
        { type: (v as any).type, description: (v as any).description },
      ])
    ),
    ...(t.input_schema.required && t.input_schema.required.length > 0
      ? { required: t.input_schema.required }
      : {}),
  },
}));

// Execute a browser tool call
export async function executeBrowserTool(name: string, args: Record<string, unknown>): Promise<string> {
  switch (name) {
    case 'browser_launch': return browserManager.launch();
    case 'browser_navigate': return browserManager.navigate(args.url as string);
    case 'browser_read_page': return browserManager.readPage(args.selector as string | undefined);
    case 'browser_click': return browserManager.click(args.target as string);
    case 'browser_type': return browserManager.type(args.text as string, args.selector as string | undefined);
    case 'browser_press_key': return browserManager.pressKey(args.key as string);
    case 'browser_screenshot': return browserManager.screenshot();
    case 'browser_scroll': return browserManager.scroll(args.direction as 'up' | 'down' | undefined, args.amount as number | undefined);
    case 'browser_get_url': return browserManager.getUrl();
    case 'browser_select': return browserManager.selectOption(args.selector as string, args.value as string);
    case 'browser_go_back': return browserManager.goBack();
    case 'browser_list_tabs': return browserManager.listTabs();
    case 'browser_switch_tab': return browserManager.switchTab(args.index as number);
    case 'browser_new_tab': return browserManager.newTab(args.url as string | undefined);
    case 'browser_wait_for_text': return browserManager.waitForText(args.text as string);
    case 'browser_close_tab': return browserManager.closeTab(args.index as number | undefined);
    case 'browser_close_other_tabs': return browserManager.closeOtherTabs();
    case 'browser_minimize': return browserManager.minimizeBrowser();
    case 'browser_close': return browserManager.closeBrowser();
    default: return `Unknown browser tool: ${name}`;
  }
}
