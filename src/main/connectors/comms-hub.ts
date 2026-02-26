/**
 * comms-hub.ts  —  Communication Hub connector for Agent Friday (Nexus OS)
 *
 * Provides webhook, email, HTTP, and notification tools using only Node.js
 * built-in modules (https, http, net, tls, child_process). Zero external deps.
 *
 * Exports:
 *   TOOLS    — tool declarations array
 *   execute  — async tool dispatcher
 *   detect   — capability check (always true; webhooks are web APIs)
 */

import * as https from 'https';
import * as http from 'http';
import * as net from 'net';
import * as tls from 'tls';
import { execFile } from 'child_process';

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface ToolDeclaration {
  name: string;
  description: string;
  parameters: {
    type: string;
    properties: Record<string, unknown>;
    required?: string[];
  };
}

interface ToolResult {
  result?: string;
  error?: string;
}

/* ------------------------------------------------------------------ */
/*  Constants                                                          */
/* ------------------------------------------------------------------ */

/** Maximum bytes we will buffer from any HTTP response body. */
const MAX_RESPONSE_BYTES = 32 * 1024; // 32 KB

/** Default timeout for HTTP requests (15 seconds). */
const HTTP_TIMEOUT_MS = 15_000;

/** Default timeout for SMTP connections (30 seconds). */
const SMTP_TIMEOUT_MS = 30_000;

/** Default timeout for PowerShell child processes (15 seconds). */
const PS_TIMEOUT_MS = 15_000;

/* ------------------------------------------------------------------ */
/*  TOOLS declaration                                                  */
/* ------------------------------------------------------------------ */

export const TOOLS: ReadonlyArray<ToolDeclaration> = [
  /* 1 — Slack webhook ------------------------------------------------ */
  {
    name: 'slack_send_webhook',
    description:
      'Send a message to a Slack channel via an incoming webhook URL.',
    parameters: {
      type: 'object',
      properties: {
        url: {
          type: 'string',
          description: 'Slack incoming-webhook URL (must be HTTPS).',
        },
        text: {
          type: 'string',
          description: 'Plain-text message to send.',
        },
        blocks: {
          type: 'string',
          description:
            'Optional Slack Block Kit JSON string. If provided, this is parsed and sent as the "blocks" field alongside text.',
        },
      },
      required: ['url', 'text'],
    },
  },

  /* 2 — Discord webhook ---------------------------------------------- */
  {
    name: 'discord_send_webhook',
    description: 'Send a message to a Discord channel via a webhook URL.',
    parameters: {
      type: 'object',
      properties: {
        url: {
          type: 'string',
          description: 'Discord webhook URL (must be HTTPS).',
        },
        content: {
          type: 'string',
          description: 'Message content to send.',
        },
        embeds: {
          type: 'string',
          description:
            'Optional JSON string representing an array of Discord embed objects.',
        },
      },
      required: ['url', 'content'],
    },
  },

  /* 3 — Teams webhook ------------------------------------------------ */
  {
    name: 'teams_send_webhook',
    description:
      'Send a message to a Microsoft Teams channel via an incoming webhook URL.',
    parameters: {
      type: 'object',
      properties: {
        url: {
          type: 'string',
          description: 'Teams incoming-webhook URL (must be HTTPS).',
        },
        text: {
          type: 'string',
          description: 'Message body text.',
        },
        card: {
          type: 'string',
          description:
            'Optional Adaptive Card or MessageCard JSON string. If provided, this replaces the default MessageCard payload.',
        },
      },
      required: ['url', 'text'],
    },
  },

  /* 4 — SMTP email --------------------------------------------------- */
  {
    name: 'smtp_send_email',
    description:
      'Send an email via SMTP with STARTTLS + AUTH LOGIN. Credentials are passed per-call and never stored.',
    parameters: {
      type: 'object',
      properties: {
        host: { type: 'string', description: 'SMTP server hostname.' },
        port: {
          type: 'number',
          description: 'SMTP server port (default 587).',
        },
        user: { type: 'string', description: 'SMTP auth username.' },
        pass: { type: 'string', description: 'SMTP auth password or app password.' },
        from: { type: 'string', description: 'Sender email address.' },
        to: {
          type: 'string',
          description:
            'Recipient email address. For multiple recipients, comma-separate them.',
        },
        subject: { type: 'string', description: 'Email subject line.' },
        body: { type: 'string', description: 'Email body content.' },
        html: {
          type: 'boolean',
          description:
            'If true, the body is sent as text/html; otherwise text/plain. Default false.',
        },
      },
      required: ['host', 'user', 'pass', 'from', 'to', 'subject', 'body'],
    },
  },

  /* 5 — Generic HTTP request ----------------------------------------- */
  {
    name: 'http_request',
    description:
      'Make an arbitrary HTTP/HTTPS request. Useful for calling any REST API, fetching data, or posting to custom endpoints.',
    parameters: {
      type: 'object',
      properties: {
        method: {
          type: 'string',
          description:
            'HTTP method: GET, POST, PUT, PATCH, DELETE, HEAD. Default POST.',
        },
        url: {
          type: 'string',
          description: 'Target URL (HTTPS strongly recommended).',
        },
        headers: {
          type: 'object',
          description:
            'Optional request headers as key-value pairs (e.g. {"Authorization": "Bearer xxx"}).',
        },
        body: {
          type: 'string',
          description:
            'Optional request body string. For JSON payloads, set Content-Type header to application/json.',
        },
      },
      required: ['url'],
    },
  },

  /* 6 — Generic webhook sender --------------------------------------- */
  {
    name: 'webhook_send',
    description:
      'Send a payload to any webhook endpoint. A convenience wrapper with webhook-friendly defaults (POST, JSON content type, HTTPS validation).',
    parameters: {
      type: 'object',
      properties: {
        url: {
          type: 'string',
          description: 'Webhook URL (must be HTTPS).',
        },
        method: {
          type: 'string',
          description: 'HTTP method. Default POST.',
        },
        headers: {
          type: 'object',
          description: 'Optional additional headers.',
        },
        body: {
          type: 'string',
          description:
            'Request body string. Typically JSON. Content-Type defaults to application/json if not specified.',
        },
      },
      required: ['url'],
    },
  },

  /* 7 — Windows toast notification ----------------------------------- */
  {
    name: 'notification_toast',
    description:
      'Show a Windows 10/11 toast notification via PowerShell. Appears in the Action Center.',
    parameters: {
      type: 'object',
      properties: {
        title: { type: 'string', description: 'Toast notification title.' },
        body: { type: 'string', description: 'Toast notification body text.' },
      },
      required: ['title', 'body'],
    },
  },
];

/* ------------------------------------------------------------------ */
/*  Safety helpers                                                     */
/* ------------------------------------------------------------------ */

/**
 * Validate that a URL is HTTPS and does not point at localhost or private IPs.
 * Throws on invalid or unsafe URLs.
 */
function validateWebhookUrl(raw: string): void {
  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    throw new Error(`Invalid URL: ${raw}`);
  }

  if (parsed.protocol !== 'https:') {
    throw new Error(
      `Webhook URL must use HTTPS. Got: ${parsed.protocol}`,
    );
  }

  const hostname = parsed.hostname.toLowerCase();

  // Block localhost variants
  if (
    hostname === 'localhost' ||
    hostname === '127.0.0.1' ||
    hostname === '::1' ||
    hostname === '0.0.0.0' ||
    hostname.endsWith('.local')
  ) {
    throw new Error(
      'Webhook URL must not target localhost or local addresses.',
    );
  }

  // Block RFC-1918 / link-local / loopback IP ranges
  const ipv4Match = hostname.match(
    /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/,
  );
  if (ipv4Match) {
    const octets = ipv4Match.slice(1).map(Number);
    const [a, b] = octets;
    if (
      a === 10 ||                           // 10.0.0.0/8
      a === 127 ||                           // 127.0.0.0/8
      (a === 172 && b >= 16 && b <= 31) ||   // 172.16.0.0/12
      (a === 192 && b === 168) ||            // 192.168.0.0/16
      (a === 169 && b === 254)               // 169.254.0.0/16 link-local
    ) {
      throw new Error(
        'Webhook URL must not target internal/private IP ranges.',
      );
    }
  }
}

/**
 * Escape a string for safe inclusion inside a PowerShell single-quoted string.
 * In PS single-quoted strings the only character that needs doubling is the
 * single-quote itself.
 */
function psEscape(s: string): string {
  return s.replace(/'/g, "''");
}

/* ------------------------------------------------------------------ */
/*  HTTP helper  (Node.js built-in modules only)                       */
/* ------------------------------------------------------------------ */

interface HttpResponse {
  statusCode: number;
  headers: http.IncomingHttpHeaders;
  body: string;
}

/**
 * Make an HTTP/HTTPS request using Node.js built-in modules.
 * - Supports both http and https protocols.
 * - Enforces a configurable timeout (default 15 s).
 * - Caps response body buffering at MAX_RESPONSE_BYTES.
 */
function httpRequest(
  targetUrl: string,
  options: {
    method?: string;
    headers?: Record<string, string>;
    body?: string | Buffer;
    timeoutMs?: number;
  } = {},
): Promise<HttpResponse> {
  return new Promise((resolve, reject) => {
    const parsed = new URL(targetUrl);
    const isHttps = parsed.protocol === 'https:';
    const transport = isHttps ? https : http;

    const reqOptions: https.RequestOptions = {
      hostname: parsed.hostname,
      port: parsed.port || (isHttps ? 443 : 80),
      path: parsed.pathname + parsed.search,
      method: options.method ?? 'POST',
      headers: { ...(options.headers ?? {}) },
    };

    if (options.body != null) {
      const buf =
        typeof options.body === 'string'
          ? Buffer.from(options.body, 'utf-8')
          : options.body;
      (reqOptions.headers as Record<string, string>)['Content-Length'] =
        String(buf.length);
    }

    const timeout = options.timeoutMs ?? HTTP_TIMEOUT_MS;

    const req = transport.request(reqOptions, (res) => {
      const chunks: Buffer[] = [];
      let totalBytes = 0;

      res.on('data', (chunk: Buffer) => {
        totalBytes += chunk.length;
        if (totalBytes <= MAX_RESPONSE_BYTES) {
          chunks.push(chunk);
        }
      });

      res.on('end', () => {
        const bodyStr = Buffer.concat(chunks).toString('utf-8');
        resolve({
          statusCode: res.statusCode ?? 0,
          headers: res.headers,
          body:
            totalBytes > MAX_RESPONSE_BYTES
              ? bodyStr + '\n[...truncated at 32 KB]'
              : bodyStr,
        });
      });

      res.on('error', reject);
    });

    req.on('error', reject);
    req.setTimeout(timeout, () => {
      req.destroy(new Error(`HTTP request timed out (${timeout / 1000} s)`));
    });

    if (options.body != null) {
      req.write(options.body);
    }
    req.end();
  });
}

/* ------------------------------------------------------------------ */
/*  Minimal SMTP client  (STARTTLS + AUTH LOGIN)                       */
/* ------------------------------------------------------------------ */

/**
 * Send an email over a raw SMTP connection with STARTTLS upgrade and
 * AUTH LOGIN authentication.  Uses only Node.js built-in net/tls modules.
 */
function smtpSendEmail(params: {
  host: string;
  port: number;
  user: string;
  pass: string;
  from: string;
  to: string;
  subject: string;
  body: string;
  html: boolean;
}): Promise<string> {
  const {
    host,
    port,
    user,
    pass,
    from,
    to,
    subject,
    body: mailBody,
    html,
  } = params;

  return new Promise((resolve, reject) => {
    let socket: net.Socket | tls.TLSSocket = net.createConnection(
      { host, port },
      () => { /* connection opened */ },
    );

    let buffer = '';
    let step = 0;
    let upgraded = false;
    const CRLF = '\r\n';

    function send(line: string): void {
      socket.write(line + CRLF);
    }

    function buildMessage(): string {
      const contentType = html
        ? 'Content-Type: text/html; charset=UTF-8'
        : 'Content-Type: text/plain; charset=UTF-8';

      const lines = [
        `From: ${from}`,
        `To: ${to}`,
        `Subject: ${subject}`,
        'MIME-Version: 1.0',
        contentType,
        'Content-Transfer-Encoding: 7bit',
        `Date: ${new Date().toUTCString()}`,
        '',
        mailBody,
      ];
      return lines.join(CRLF);
    }

    function advance(code: number, text: string): void {
      try {
        switch (step) {
          /* 0: Server greeting */
          case 0:
            if (code !== 220)
              throw new Error(`SMTP greeting failed: ${code} ${text}`);
            step = 1;
            send('EHLO nexus-os');
            break;

          /* 1: EHLO response — check for STARTTLS */
          case 1:
            if (code !== 250)
              throw new Error(`EHLO failed: ${code} ${text}`);
            if (!upgraded && text.toUpperCase().includes('STARTTLS')) {
              step = 2;
              send('STARTTLS');
            } else {
              step = 3;
              send('AUTH LOGIN');
            }
            break;

          /* 2: STARTTLS response — upgrade the connection */
          case 2:
            if (code !== 220)
              throw new Error(`STARTTLS failed: ${code} ${text}`);
            {
              const tlsSocket = tls.connect(
                { socket: socket as net.Socket, host, servername: host },
                () => {
                  upgraded = true;
                  socket = tlsSocket;
                  buffer = '';
                  socket.on('data', onData);
                  step = 1;
                  send('EHLO nexus-os');
                },
              );
              tlsSocket.on('error', reject);
            }
            break;

          /* 3: AUTH LOGIN -> 334 (username prompt) */
          case 3:
            if (code !== 334)
              throw new Error(`AUTH LOGIN failed: ${code} ${text}`);
            step = 4;
            send(Buffer.from(user).toString('base64'));
            break;

          /* 4: Username accepted -> 334 (password prompt) */
          case 4:
            if (code !== 334)
              throw new Error(`AUTH user failed: ${code} ${text}`);
            step = 5;
            send(Buffer.from(pass).toString('base64'));
            break;

          /* 5: Password accepted -> 235 */
          case 5:
            if (code !== 235)
              throw new Error(`AUTH password failed: ${code} ${text}`);
            step = 6;
            send(`MAIL FROM:<${from}>`);
            break;

          /* 6: MAIL FROM accepted */
          case 6:
            if (code !== 250)
              throw new Error(`MAIL FROM failed: ${code} ${text}`);
            step = 7;
            send(`RCPT TO:<${to}>`);
            break;

          /* 7: RCPT TO accepted */
          case 7:
            if (code !== 250)
              throw new Error(`RCPT TO failed: ${code} ${text}`);
            step = 8;
            send('DATA');
            break;

          /* 8: DATA prompt -> 354 */
          case 8:
            if (code !== 354)
              throw new Error(`DATA failed: ${code} ${text}`);
            step = 9;
            {
              const msg = buildMessage();
              // Dot-stuff any line that starts with '.'
              const safeMsg = msg.replace(/\r\n\./g, '\r\n..');
              socket.write(safeMsg + CRLF + '.' + CRLF);
            }
            break;

          /* 9: Message accepted -> 250 */
          case 9:
            if (code !== 250)
              throw new Error(`Message send failed: ${code} ${text}`);
            step = 10;
            send('QUIT');
            break;

          /* 10: QUIT acknowledged */
          case 10:
            socket.end();
            resolve(`Email sent successfully to ${to}`);
            break;

          default:
            break;
        }
      } catch (err) {
        socket.destroy();
        reject(err);
      }
    }

    function onData(chunk: Buffer): void {
      buffer += chunk.toString('utf-8');
      const lines = buffer.split(CRLF);
      buffer = lines.pop() ?? '';

      let lastCode = 0;
      let fullText = '';

      for (const line of lines) {
        if (line.length < 3) continue;
        const code = parseInt(line.substring(0, 3), 10);
        fullText += line + '\n';
        // A space at position 3 (or end-of-string) means this is the final
        // line of a multi-line reply.
        if (line[3] === ' ' || line[3] === undefined) {
          lastCode = code;
        }
      }

      if (lastCode > 0) {
        advance(lastCode, fullText.trim());
      }
    }

    socket.on('data', onData);
    socket.on('error', reject);
    socket.setTimeout(SMTP_TIMEOUT_MS, () => {
      socket.destroy(
        new Error(`SMTP connection timed out (${SMTP_TIMEOUT_MS / 1000} s)`),
      );
    });
  });
}

/* ------------------------------------------------------------------ */
/*  PowerShell execution helper                                        */
/* ------------------------------------------------------------------ */

/**
 * Run an inline PowerShell script and return stdout.
 * Uses -NoProfile -NonInteractive -ExecutionPolicy Bypass for reliability.
 */
function runPowerShell(script: string): Promise<string> {
  return new Promise((resolve, reject) => {
    execFile(
      'powershell.exe',
      [
        '-NoProfile',
        '-NonInteractive',
        '-ExecutionPolicy',
        'Bypass',
        '-Command',
        script,
      ],
      { timeout: PS_TIMEOUT_MS },
      (err, stdout, stderr) => {
        if (err) {
          return reject(
            new Error(
              `PowerShell error: ${err.message}${
                stderr ? ' -- ' + stderr : ''
              }`,
            ),
          );
        }
        resolve((stdout ?? '').trim());
      },
    );
  });
}

/* ------------------------------------------------------------------ */
/*  Tool implementations                                               */
/* ------------------------------------------------------------------ */

// ── 1. Slack webhook ──────────────────────────────────────────────────

async function slackSendWebhook(
  args: Record<string, unknown>,
): Promise<string> {
  const webhookUrl = String(args.url ?? '');
  const text = String(args.text ?? '');
  if (!webhookUrl || !text)
    throw new Error('url and text are required.');

  validateWebhookUrl(webhookUrl);

  const payload: Record<string, unknown> = { text };

  // Parse optional blocks JSON
  if (args.blocks) {
    try {
      const parsed = JSON.parse(String(args.blocks));
      payload.blocks = parsed;
    } catch (e) {
      throw new Error(
        `Invalid blocks JSON: ${e instanceof Error ? e.message : String(e)}`,
      );
    }
  }

  const res = await httpRequest(webhookUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

  if (res.statusCode < 200 || res.statusCode >= 300) {
    throw new Error(
      `Slack webhook returned HTTP ${res.statusCode}: ${res.body}`,
    );
  }
  return `Slack message sent (HTTP ${res.statusCode}): ${res.body}`;
}

// ── 2. Discord webhook ───────────────────────────────────────────────

async function discordSendWebhook(
  args: Record<string, unknown>,
): Promise<string> {
  const webhookUrl = String(args.url ?? '');
  const content = String(args.content ?? '');
  if (!webhookUrl || !content)
    throw new Error('url and content are required.');

  validateWebhookUrl(webhookUrl);

  const payload: Record<string, unknown> = { content };

  // Parse optional embeds JSON
  if (args.embeds) {
    try {
      const parsed = JSON.parse(String(args.embeds));
      if (Array.isArray(parsed)) {
        payload.embeds = parsed;
      } else {
        payload.embeds = [parsed];
      }
    } catch (e) {
      throw new Error(
        `Invalid embeds JSON: ${e instanceof Error ? e.message : String(e)}`,
      );
    }
  }

  const res = await httpRequest(webhookUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

  // Discord returns 204 No Content on success
  if (res.statusCode < 200 || res.statusCode >= 300) {
    throw new Error(
      `Discord webhook returned HTTP ${res.statusCode}: ${res.body}`,
    );
  }
  return `Discord message sent (HTTP ${res.statusCode})${
    res.body ? ': ' + res.body : ''
  }`;
}

// ── 3. Teams webhook ─────────────────────────────────────────────────

async function teamsSendWebhook(
  args: Record<string, unknown>,
): Promise<string> {
  const webhookUrl = String(args.url ?? '');
  const text = String(args.text ?? '');
  if (!webhookUrl || !text)
    throw new Error('url and text are required.');

  validateWebhookUrl(webhookUrl);

  let payload: Record<string, unknown>;

  // If a custom card JSON is provided, parse and use it directly
  if (args.card) {
    try {
      payload = JSON.parse(String(args.card));
    } catch (e) {
      throw new Error(
        `Invalid card JSON: ${e instanceof Error ? e.message : String(e)}`,
      );
    }
  } else {
    // Default: build a simple MessageCard
    payload = {
      '@type': 'MessageCard',
      '@context': 'http://schema.org/extensions',
      summary: text.slice(0, 80),
      text,
    };
  }

  const res = await httpRequest(webhookUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

  if (res.statusCode < 200 || res.statusCode >= 300) {
    throw new Error(
      `Teams webhook returned HTTP ${res.statusCode}: ${res.body}`,
    );
  }
  return `Teams message sent (HTTP ${res.statusCode}): ${res.body}`;
}

// ── 4. SMTP email ────────────────────────────────────────────────────

async function smtpSendEmailTool(
  args: Record<string, unknown>,
): Promise<string> {
  const host = String(args.host ?? '');
  const port = Number(args.port ?? 587);
  const user = String(args.user ?? '');
  const pass = String(args.pass ?? '');
  const from = String(args.from ?? '');
  const to = String(args.to ?? '');
  const subject = String(args.subject ?? '');
  const body = String(args.body ?? '');
  const html = Boolean(args.html ?? false);

  if (!host || !user || !pass || !from || !to || !subject || !body) {
    throw new Error(
      'host, user, pass, from, to, subject, and body are all required.',
    );
  }

  return smtpSendEmail({ host, port, user, pass, from, to, subject, body, html });
}

// ── 5. Generic HTTP request ──────────────────────────────────────────

async function httpRequestTool(
  args: Record<string, unknown>,
): Promise<string> {
  const targetUrl = String(args.url ?? '');
  if (!targetUrl) throw new Error('url is required.');

  const method = String(args.method ?? 'POST').toUpperCase();
  const allowedMethods = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD'];
  if (!allowedMethods.includes(method)) {
    throw new Error(
      `Unsupported HTTP method: ${method}. Allowed: ${allowedMethods.join(', ')}`,
    );
  }

  const headers: Record<string, string> = {};
  if (args.headers && typeof args.headers === 'object') {
    for (const [k, v] of Object.entries(
      args.headers as Record<string, unknown>,
    )) {
      headers[k] = String(v);
    }
  }

  const res = await httpRequest(targetUrl, {
    method,
    headers,
    body: args.body != null ? String(args.body) : undefined,
  });

  const headerSummary = Object.entries(res.headers)
    .slice(0, 10)
    .map(([k, v]) => `  ${k}: ${v}`)
    .join('\n');

  return [
    `HTTP ${method} ${targetUrl} -> ${res.statusCode}`,
    '',
    'Response headers (first 10):',
    headerSummary,
    '',
    'Body:',
    res.body,
  ].join('\n');
}

// ── 6. Generic webhook sender ────────────────────────────────────────

async function webhookSend(
  args: Record<string, unknown>,
): Promise<string> {
  const webhookUrl = String(args.url ?? '');
  if (!webhookUrl) throw new Error('url is required.');

  validateWebhookUrl(webhookUrl);

  const method = String(args.method ?? 'POST').toUpperCase();
  const allowedMethods = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'];
  if (!allowedMethods.includes(method)) {
    throw new Error(`Unsupported HTTP method: ${method}`);
  }

  const headers: Record<string, string> = {};
  if (args.headers && typeof args.headers === 'object') {
    for (const [k, v] of Object.entries(
      args.headers as Record<string, unknown>,
    )) {
      headers[k] = String(v);
    }
  }

  // Default to JSON content type for webhooks if not specified
  if (!headers['Content-Type'] && !headers['content-type']) {
    headers['Content-Type'] = 'application/json';
  }

  const res = await httpRequest(webhookUrl, {
    method,
    headers,
    body: args.body != null ? String(args.body) : undefined,
  });

  if (res.statusCode < 200 || res.statusCode >= 300) {
    throw new Error(
      `Webhook returned HTTP ${res.statusCode}: ${res.body}`,
    );
  }
  return `Webhook ${method} -> ${res.statusCode}${res.body ? ': ' + res.body : ''}`;
}

// ── 7. Windows toast notification ────────────────────────────────────

async function notificationToast(
  args: Record<string, unknown>,
): Promise<string> {
  const title = String(args.title ?? '');
  const body = String(args.body ?? '');
  if (!title || !body) throw new Error('title and body are required.');

  // Use the Windows WinRT toast notification API via PowerShell.
  // This produces a real toast notification visible in the Action Center.
  //
  // The toast XML uses double-quoted attribute values so we can embed it
  // inside a PowerShell single-quoted here-string without quoting conflicts.
  const escapedTitle = psEscape(title);
  const escapedBody = psEscape(body);

  const script = [
    '[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null',
    '[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime] | Out-Null',
    '',
    '$xml = New-Object Windows.Data.Xml.Dom.XmlDocument',
    // Use a PS here-string (@"..."@) to avoid quote-escaping inside the XML
    '$toastXml = @"',
    '<toast>',
    '  <visual>',
    '    <binding template="ToastGeneric">',
    `      <text>${escapedTitle}</text>`,
    `      <text>${escapedBody}</text>`,
    '    </binding>',
    '  </visual>',
    '</toast>',
    '"@',
    '$xml.LoadXml($toastXml)',
    '',
    // Use the PowerShell AppUserModelId as the notifier identity
    "$appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe'",
    '$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)',
    '$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId)',
    '$notifier.Show($toast)',
    "Write-Output 'Toast notification shown.'",
  ].join('\n');

  try {
    const result = await runPowerShell(script);
    return result || 'Toast notification displayed.';
  } catch {
    // Fallback: use the older BalloonTip approach via System.Windows.Forms
    const fallbackScript = [
      'Add-Type -AssemblyName System.Windows.Forms',
      '',
      '$notify = New-Object System.Windows.Forms.NotifyIcon',
      '$notify.Icon = [System.Drawing.SystemIcons]::Information',
      `$notify.BalloonTipTitle = '${psEscape(title)}'`,
      `$notify.BalloonTipText = '${psEscape(body)}'`,
      '$notify.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Info',
      '$notify.Visible = $true',
      '$notify.ShowBalloonTip(5000)',
      'Start-Sleep -Milliseconds 5500',
      '$notify.Dispose()',
      "Write-Output 'Balloon notification shown (fallback).'",
    ].join('\n');

    const fallbackResult = await runPowerShell(fallbackScript);
    return fallbackResult || 'Balloon notification displayed (fallback).';
  }
}

/* ------------------------------------------------------------------ */
/*  execute() — main tool dispatcher                                   */
/* ------------------------------------------------------------------ */

export async function execute(
  toolName: string,
  args: Record<string, unknown>,
): Promise<ToolResult> {
  try {
    let result: string;

    switch (toolName) {
      case 'slack_send_webhook':
        result = await slackSendWebhook(args);
        break;
      case 'discord_send_webhook':
        result = await discordSendWebhook(args);
        break;
      case 'teams_send_webhook':
        result = await teamsSendWebhook(args);
        break;
      case 'smtp_send_email':
        result = await smtpSendEmailTool(args);
        break;
      case 'http_request':
        result = await httpRequestTool(args);
        break;
      case 'webhook_send':
        result = await webhookSend(args);
        break;
      case 'notification_toast':
        result = await notificationToast(args);
        break;
      default:
        return { error: `Unknown comms-hub tool: ${toolName}` };
    }

    return { result };
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    return { error: `comms-hub "${toolName}" failed: ${message}` };
  }
}

/* ------------------------------------------------------------------ */
/*  detect() — capability check                                        */
/* ------------------------------------------------------------------ */

/**
 * Communications tools are web-based APIs (webhooks, SMTP) that work
 * on any system with network access.  Always returns true.
 */
export async function detect(): Promise<boolean> {
  return true;
}
