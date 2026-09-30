# Frames, origins and the broker

> **Status:** engineering guidance and the contract for frame authors · **Last verified against the code:** 2026-09-30

Nothing outside Friday's own page can reach the vault, credentials or
`run_command`. Loopback is trusted, so "it came from this machine" says nothing
about *which document* in the browser is speaking. Four layers answer that, each
enforced on its own, so one failing does not open the rest.

## The layers

| Layer | Where | What it refuses |
|---|---|---|
| Host gate | `origin_gate.host_refusal`, `core.check_auth` | Any request from this machine, read or write, whose `Host` header is not a loopback name or one of Friday's own names (`code: foreign_host`). A DNS-rebound page looks same-origin to the browser and sends no foreign `Origin`; its Host is the one thing it cannot change. This runs before loopback trust and covers `/`, which embeds the token, and `/api/session/token`. |
| Session token on browser state changes and socket upgrades | `services/origin_gate.py`, `core.check_auth` | A browser `POST/PUT/PATCH/DELETE` or WebSocket upgrade with no token, or from another site. Fails closed. |
| Frame gate | `origin_gate.frame_refusal` | Under `/api/` and `/ws/`, any request whose metadata says another document sent it (`Sec-Fetch-Site: cross-site` or `same-site`, `Origin: null` from a sandbox, an `Origin` that is not Friday's), for every method, **even with a valid token**. Exceptions that script cannot read: a top-level navigation, and passive media loads. `/api/health` stays public. |
| Sandboxed markup | `core._isolation_headers` | Every route that returns HTML, XHTML, SVG or XML, except the pages named in `core.OWN_PAGE_ENDPOINTS`, carries `Content-Security-Policy: sandbox ...` with no `allow-same-origin` and no top-navigation token. An existing `sandbox` directive is only ever narrowed. A new route is sandboxed by default. |
| Friday's own pages | `core.OWN_PAGE_CSP` | `frame-ancestors 'self'`, `object-src 'none'`, `base-uri 'self'`, `form-action 'self'`, scripts from this origin (and the one pinned CDN hand tracking uses), `no-store`. |

The session token also rotates (24 h by default). A tab that outlives a rotation
asks `/api/session/token`, which is reachable only by a request that names
Friday in its Host header and passes the frame gate, and retries a
refused request once; the refusal carries `"code": "session_token_required"`
for exactly that case.

In the page, every iframe that shows something Friday did not write has a
`sandbox` attribute without `allow-same-origin` (chat and Studio HTML, file
previews, Studio creations). A structural test pins that for `index.html` and
`ui_parts/app.html`.

Workspace customizations are CSS only. The sanitizer removes `<`, backslashes,
`@import`, script URLs and every `url()` except inline images, repeating until
nothing is left to remove, and stored customizations are sanitized again when
read.

## The broker

`static/js/friday_frame_broker.js` is the only door from a sandboxed frame back
to the page. A frame asks with `postMessage`; the page answers.

```
request  (frame -> parent): { protocol: "friday-frame/1", id, cap, args }
reply    (parent -> frame): { protocol: "friday-frame/1", id, ok: true,  result }
                            { protocol: "friday-frame/1", id, ok: false, error }
```

The parent accepts a message only from an iframe Friday registered (its
`ref`), only when the frame is sandboxed without `allow-same-origin`, and only
when the message origin is `"null"`. At most 30 requests per second per page.

| Capability | Args | Result |
|---|---|---|
| `ping` | none | the protocol name and this list |
| `theme.get` | none | the brand colours and type faces |
| `frame.resize` | `{ height }` 40 to 4000, only for an iframe with `data-friday-resizable` | `{ height }` |
| `link.open` | `{ url }` http or https, only right after a click or keypress | `{ opened }`, opens with `noopener,noreferrer` |
| `clipboard.write` | `{ text }` up to 100000 characters, only right after a click or keypress | `{ copied }` |

The list is closed. There is no capability that reads the vault, touches
credentials, reads or writes settings, calls an API route or runs a command, and
the broker holds no session token and makes no requests of its own. A test
asserts both. Adding a capability means adding an entry to `CAPABILITIES` and a
row above, and it must not carry authority the frame does not already have as a
web page.

From a frame:

```js
const id = crypto.randomUUID();
addEventListener('message', (e) => {
  if (e.source === parent && e.data && e.data.id === id) console.log(e.data);
});
parent.postMessage({ protocol: 'friday-frame/1', id, cap: 'theme.get' }, '*');
```
