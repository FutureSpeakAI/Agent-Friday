"""Friday's own front door on this PC: https://agent.<name>, and http:// too.

A BYTE RELAY, NOT AN HTTP PROXY. It listens on the loopback interfaces only
(127.0.0.1 and ::1 -- never the LAN), terminates TLS with Friday's own
certificate (services/local_ca.py) and pipes each connection to Friday's server
on 127.0.0.1:<port>. Nothing is parsed or rewritten on the way through, so
streaming replies, Server-Sent Events and the voice WebSocket pass untouched,
and Friday sees exactly what a browser connecting to 127.0.0.1:3000 directly
would show it: a loopback peer, no forwarding headers, the browser's own Host.
That is why services/core `_is_local_request()` needs no special case for it,
and why it grants nothing a local program could not already get by connecting
to :3000 itself.

The one thing the plain-http listener does besides relaying: once the secure
address is set up and Windows trusts it, a request for http://agent.<name>
gets a temporary (307) redirect to https://. Temporary because trust can be
removed again from Settings, and a browser remembers a permanent redirect long
after the certificate it pointed at stopped being trusted.

asyncio rather than a second Werkzeug server: the dev server performs the TLS
handshake inside its accept loop, so one client that connected and never spoke
would stall every other connection. Here each handshake is its own task with
its own timeout.
"""
from __future__ import annotations

import asyncio
import socket
import ssl
import threading
import time
from agent_friday.user_errors import ExceptionText

HANDSHAKE_TIMEOUT_S = 10
HEAD_TIMEOUT_S = 10
UPSTREAM_WAIT_S = 15        # Friday is still binding :3000 when the relay starts
MAX_HEAD = 64 * 1024
LOOPBACK_NAMES = ("localhost", "127.0.0.1", "[::1]")
CRLF = bytes((13, 10))


def bare_host(host: str) -> str:
    """A Host header without its port: "agent.friday:80" -> "agent.friday",
    "[::1]:80" -> "[::1]"."""
    h = str(host or "").strip().lower()
    if h.startswith("["):
        return h.split("]", 1)[0] + "]"
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


def port_holder(port: int) -> str:
    """Who is listening on `port`, in words ("caddy.exe (PID 1848)"), or ''."""
    try:
        import psutil
        for c in psutil.net_connections(kind="tcp"):
            if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == port and c.pid:
                try:
                    name = psutil.Process(c.pid).name()
                except Exception:
                    name = "a program"
                return f"{name} (PID {c.pid})"
    except Exception:
        pass
    return ""


def _bind(addr: str, port: int) -> socket.socket:
    fam = socket.AF_INET6 if ":" in addr else socket.AF_INET
    s = socket.socket(fam, socket.SOCK_STREAM)
    try:
        # Windows lets a second socket bind a port another holds unless the
        # first asked for exclusivity; without it another program could take
        # over agent.<name> from under Friday.
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        if fam == socket.AF_INET6:
            s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        s.bind((addr, port))
        s.listen(128)
        s.setblocking(False)
        return s
    except Exception:
        s.close()
        raise


def server_context(cert_file: str, key_file: str) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert_file, key_file)
    ctx.set_alpn_protocols(["http/1.1"])
    return ctx


class LocalProxy:
    """Listeners for one host. start() returns what bound and what did not."""

    def __init__(self, *, host: str, upstream_port: int, https_port: int = 443,
                 http_port: int = 80, cert_file: str = "", key_file: str = "",
                 redirect_http=None, addrs=("127.0.0.1", "::1")):
        self.host = host.lower()
        self.upstream_port = int(upstream_port)
        self.https_port = int(https_port)
        self.http_port = int(http_port)
        self.cert_file = cert_file
        self.key_file = key_file
        self.redirect_http = redirect_http or (lambda: False)
        self.addrs = tuple(addrs)
        self._ctx = None
        self._loop = None
        self._thread = None
        self._servers = []
        self.listening = {"https": {}, "http": {}}   # addr -> "ok" | reason
        self.started_at = None

    # ── lifecycle ──────────────────────────────────────────────────────
    def start(self, timeout: float = 5.0) -> dict:
        if self._thread and self._thread.is_alive():
            return self.status()
        if self.cert_file and self.key_file:
            self._ctx = server_context(self.cert_file, self.key_file)
        ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(ready,),
                                        name="friday-local-address", daemon=True)
        self._thread.start()
        ready.wait(timeout)
        self.started_at = time.time()
        return self.status()

    def stop(self) -> None:
        loop = self._loop
        if not loop:
            return

        async def _close():
            for srv in self._servers:
                srv.close()
            # Open connections (a browser keeps them alive) end here too;
            # waiting for them to finish on their own could take minutes.
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            loop.stop()

        try:
            asyncio.run_coroutine_threadsafe(_close(), loop)
        except Exception:
            pass
        if self._thread:
            self._thread.join(3)
        self._servers = []
        self.listening = {"https": {}, "http": {}}

    def reload_certificate(self) -> None:
        """Pick up a renewed certificate for new connections (same context)."""
        if self._ctx and self.cert_file and self.key_file:
            self._ctx.load_cert_chain(self.cert_file, self.key_file)

    def status(self) -> dict:
        def one(kind, port):
            st = self.listening.get(kind) or {}
            return {"port": port, "addrs": dict(st),
                    "listening": any(v == "ok" for v in st.values())}
        return {"host": self.host, "https": one("https", self.https_port) if self._ctx else None,
                "http": one("http", self.http_port), "upstream_port": self.upstream_port}

    # ── the loop ───────────────────────────────────────────────────────
    def _run(self, ready: threading.Event) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)

        async def _open():
            plan = [("http", self.http_port, None)]
            if self._ctx is not None:
                plan.insert(0, ("https", self.https_port, self._ctx))
            for kind, port, ctx in plan:
                for addr in self.addrs:
                    try:
                        sock = _bind(addr, port)
                    except OSError as e:
                        who = port_holder(port)
                        self.listening[kind][addr] = (
                            f"port {port} is in use by {who}" if who else
                            ExceptionText(f"could not listen on port {port}: {e.strerror or e}"))
                        continue
                    handler = self._https_client if kind == "https" else self._http_client
                    if ctx is not None:
                        srv = await asyncio.start_server(
                            handler, sock=sock, ssl=ctx,
                            ssl_handshake_timeout=HANDSHAKE_TIMEOUT_S)
                    else:
                        srv = await asyncio.start_server(handler, sock=sock)
                    self._servers.append(srv)
                    self.listening[kind][addr] = "ok"

        try:
            loop.run_until_complete(_open())
        finally:
            ready.set()
        try:
            loop.run_forever()
        finally:
            try:
                loop.close()
            except Exception:
                pass

    # ── connections ────────────────────────────────────────────────────
    async def _upstream(self):
        deadline = time.monotonic() + UPSTREAM_WAIT_S
        while True:
            try:
                return await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", self.upstream_port), 5)
            except (OSError, asyncio.TimeoutError):
                if time.monotonic() >= deadline:
                    return None
                await asyncio.sleep(0.25)

    @staticmethod
    async def _plain(writer, status: str, body: str, extra=()) -> None:
        """A short plain-text answer, then the connection closes."""
        data = (body + chr(10)).encode("utf-8")
        lines = [f"HTTP/1.1 {status}", "Content-Type: text/plain; charset=utf-8",
                 f"Content-Length: {len(data)}"]
        lines += [f"{k}: {v}" for k, v in extra]
        lines += ["Connection: close", "", ""]
        head = CRLF.join(x.encode("latin-1") for x in lines)
        try:
            writer.write(head + data)
            await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _relay(self, reader, writer, prefix: bytes = b"") -> None:
        up = await self._upstream()
        if up is None:
            await self._plain(writer, "502 Bad Gateway",
                              "Friday is not answering on this PC right now. "
                              "If it is starting up, try again in a few seconds.")
            return
        up_reader, up_writer = up
        if prefix:
            up_writer.write(prefix)

        async def pump(r, w):
            try:
                while True:
                    data = await r.read(65536)
                    if not data:
                        break
                    w.write(data)
                    await w.drain()
            except Exception:
                pass
            finally:
                try:
                    if w.can_write_eof():
                        w.write_eof()
                    else:
                        w.close()
                except Exception:
                    pass

        await asyncio.gather(pump(reader, up_writer), pump(up_reader, writer))
        for w in (up_writer, writer):
            try:
                w.close()
            except Exception:
                pass

    async def _https_client(self, reader, writer) -> None:
        await self._relay(reader, writer)

    async def _http_client(self, reader, writer) -> None:
        """Plain http answers Friday's own name and loopback, and nothing else.

        A web page on some other site can point its own name at 127.0.0.1 (DNS
        rebinding) and would otherwise reach Friday through this port as if it
        were this PC's owner. Its requests carry that site's name in Host, so
        they are refused here before anything is relayed. https needs no such
        check: a browser sends nothing to this certificate for any name but
        agent.<name>.
        """
        try:
            head = await asyncio.wait_for(reader.readuntil(CRLF + CRLF), HEAD_TIMEOUT_S)
        except asyncio.LimitOverrunError:
            await self._plain(writer, "431 Request Header Fields Too Large", "Too large.")
            return
        except Exception:
            try:
                writer.close()
            except Exception:
                pass
            return
        line, _, rest = head.partition(CRLF)
        parts = line.split(b" ")
        target = parts[1].decode("latin-1", "replace") if len(parts) >= 2 else "/"
        host = ""
        for h in rest.split(CRLF):
            k, _, v = h.partition(b":")
            if k.strip().lower() == b"host":
                host = v.strip().decode("latin-1", "replace")
                break
        bare = bare_host(host)
        if bare != self.host and bare not in LOOPBACK_NAMES:
            await self._plain(writer, "421 Misdirected Request",
                              "This port on this PC serves Friday's own address only.")
            return
        try:
            wants_https = bool(self.redirect_http())
        except Exception:
            wants_https = False
        if bare != self.host or not wants_https or not target.startswith("/"):
            await self._relay(reader, writer, prefix=head)
            return
        port = "" if self.https_port == 443 else f":{self.https_port}"
        where = f"https://{self.host}{port}{target}"
        await self._plain(writer, "307 Temporary Redirect",
                          f"Friday's address on this PC is secure: {where}",
                          extra=[("Location", where), ("Cache-Control", "no-store")])
