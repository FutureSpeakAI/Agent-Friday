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

ONE LOOP SERVES EVERY LISTENER, SO ITS HEALTH IS CHECKED, NOT ASSUMED. All
four sockets (https and http, on 127.0.0.1 and ::1) are served by one event
loop on one thread. The sockets stay bound whatever happens to that thread, and
the operating system keeps completing TCP handshakes on them, so a loop that
has died or is stuck looks, from outside, like a server that accepts and never
answers -- on every listener at once. The rules that follow:
  * Nothing that can block runs on the loop thread. A callback it makes
    (redirect_http) returns at once and takes no lock.
  * Each listener has its own accept loop, which survives a failed accept.
    asyncio's built-in one (create_server) closes the listening socket for good
    on the first accept error, and on Windows a client that resets before its
    accept is taken is such an error (WinError 64).
  * A port that will not bind is tried again by the loop, backing off, and
    counts as not listening until it binds.
  * The loop stamps a heartbeat every second. health() reports a listener as
    serving only while the thread lives, the heartbeat is fresh and that
    listener is bound and accepting; status() says listening on the same terms.
  * ensure_alive() restarts only a loop whose thread has ended or whose
    heartbeat has stood still for STALL_AFTER_S -- never one that is merely
    slow to answer, because a restart drops every open connection (the voice
    socket among them). It closes the old generation's listening and accepted
    sockets, logs the old thread's stack, and backs off between restarts.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import socket
import ssl
import sys
import threading
import time
import traceback
from agent_friday.user_errors import ExceptionText

_log = logging.getLogger("friday.local_address")

HANDSHAKE_TIMEOUT_S = 10
HEAD_TIMEOUT_S = 10
UPSTREAM_WAIT_S = 15        # Friday is still binding :3000 when the relay starts
MAX_HEAD = 64 * 1024
LOOPBACK_NAMES = ("localhost", "127.0.0.1", "[::1]")
CRLF = bytes((13, 10))
HEALTH_TIMEOUT_S = 0.5      # the direct question to the loop (diagnostics only)
HEARTBEAT_S = 1.0           # the loop stamps its heartbeat this often
STALL_AFTER_S = 25.0        # a heartbeat this old means a stuck loop (2-3 watchdog rounds)
ACCEPT_RETRY_S = 0.05       # pause after a failed accept, growing with errors in a row
ACCEPT_FAILING_AFTER = 20   # errors in a row before a listener counts as not accepting
REBIND_FIRST_S = 0.5        # a busy port is tried again after this, doubling ...
REBIND_MAX_S = 30.0         # ... up to this
RESTART_BACKOFF_S = 10.0    # restarts in a row wait this, doubling ...
RESTART_BACKOFF_MAX_S = 300.0   # ... up to this
RESTART_STREAK_RESET_S = 600.0  # this long healthy and the backoff starts over


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
        else:
            # Elsewhere a port whose previous listener still has accepted
            # connections or TIME_WAIT entries cannot be bound again without
            # SO_REUSEADDR, so a restart would leave it closed; there it does
            # not let a second live listener share the port. Never on
            # Windows, where SO_REUSEADDR lets another program take it over.
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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


def _close_all(socks) -> None:
    for s in list(socks):
        try:
            s.close()
        except Exception:
            pass


class _Run:
    """One generation of the serving loop: its loop, thread, sockets and tasks.
    A restart makes a new one, so the old thread can never touch the new
    sockets, however late it wakes up."""

    def __init__(self, generation: int):
        self.generation = generation
        self.loop = asyncio.new_event_loop()
        self.thread: threading.Thread | None = None
        self.socks: list = []            # listening sockets
        self.conn_socks: set = set()     # accepted sockets, until their connection ends
        self.accepts: dict = {}          # "https 127.0.0.1" -> its accept task
        self.failing: dict = {}          # "https 127.0.0.1" -> consecutive accept errors
        self.binds: dict = {}            # "https 127.0.0.1" -> its re-bind task
        self.conns: set = set()          # live connection tasks
        self.beat = time.monotonic()     # the loop's own heartbeat


class LocalProxy:
    """Listeners for one host. start() returns what bound and what did not."""

    # Instance-tunable so tests can use short ones.
    heartbeat_s = HEARTBEAT_S
    stall_after_s = STALL_AFTER_S
    rebind_first_s = REBIND_FIRST_S
    restart_backoff_s = RESTART_BACKOFF_S

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
        self._run_state: _Run | None = None
        self._generation = 0
        self._stopped = False
        self._lifecycle = threading.RLock()
        self._next_restart_at = 0.0
        self._restart_streak = 0
        self.listening = {"https": {}, "http": {}}   # addr -> "ok" | reason
        self.started_at = None
        self.stats = {"last_accept": {"https": None, "http": None},
                      "accept_errors": 0, "last_accept_error": "",
                      "loop_ended": "", "restarts": 0, "last_restart": None,
                      "last_restart_reason": "", "next_restart_in_s": 0.0}

    # The loop and thread of the current generation (tests and callers that
    # schedule work on the loop use these).
    @property
    def _loop(self):
        r = self._run_state
        return r.loop if r is not None else None

    @property
    def _thread(self):
        r = self._run_state
        return r.thread if r is not None else None

    def _plan(self) -> list:
        plan = [("http", self.http_port, None)]
        if self._ctx is not None:
            plan.insert(0, ("https", self.https_port, self._ctx))
        return plan

    # ── lifecycle ──────────────────────────────────────────────────────
    def start(self, timeout: float = 5.0) -> dict:
        with self._lifecycle:
            if self._thread and self._thread.is_alive():
                return self.status()
            self._stopped = False
            if self.cert_file and self.key_file:
                self._ctx = server_context(self.cert_file, self.key_file)
            self._spawn(timeout)
            self.started_at = time.time()
        return self.status()

    def _spawn(self, timeout: float) -> None:
        self._generation += 1
        run = _Run(self._generation)
        self.listening = {"https": {}, "http": {}}
        ready = threading.Event()
        run.thread = threading.Thread(target=self._run, args=(run, ready),
                                      name="friday-local-address", daemon=True)
        self._run_state = run
        run.thread.start()
        ready.wait(timeout)

    def stop(self) -> None:
        with self._lifecycle:
            self._stopped = True
            self._halt(self._run_state)
            self._run_state = None
            self.listening = {"https": {}, "http": {}}

    def _halt(self, run: _Run | None, join_s: float = 3.0) -> None:
        """End one generation. A loop that still answers closes its own
        connections; one that does not cannot, so its sockets -- the listening
        ones and every accepted one -- are closed from here. Accepted sockets
        matter too: on Windows an accepted connection of an exclusive listener
        can keep the port from being bound again."""
        if run is None:
            return
        loop = run.loop

        async def _close():
            # Open connections (a browser keeps them alive) end here too;
            # waiting for them to finish on their own could take minutes.
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            _close_all(run.socks)
            _close_all(run.conn_socks)
            loop.stop()

        if run.thread is not None and run.thread.is_alive() and not loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(_close(), loop)
            except Exception:
                pass
            run.thread.join(join_s)
        _close_all(run.socks)
        _close_all(run.conn_socks)

    def reload_certificate(self) -> None:
        """Pick up a renewed certificate for new connections (same context)."""
        if self._ctx and self.cert_file and self.key_file:
            self._ctx.load_cert_chain(self.cert_file, self.key_file)

    # ── health ─────────────────────────────────────────────────────────
    def health(self, timeout: float = HEALTH_TIMEOUT_S) -> dict:
        """Is the loop alive and advancing, and is every listener accepting?

        `advancing` comes from the loop's own heartbeat (it stamps the time
        every heartbeat_s): a loop whose stamp is older than stall_after_s is
        not running callbacks. `responsive` is a direct question within
        `timeout`, for diagnostics only: a busy process (a long C call holding
        the GIL, a big garbage collection, paging, waking from sleep) can miss
        it while the loop is fine. `accepting` names every planned listener
        and whether it is bound and taking connections; one that failed to
        bind, or whose accepts keep failing, is False. ok needs all three of
        thread alive, advancing and accepting everywhere.
        """
        run = self._run_state
        out = {"ok": False, "generation": run.generation if run else 0,
               "thread_alive": bool(run and run.thread and run.thread.is_alive()),
               "advancing": False, "heartbeat_age_s": None,
               "responsive": False, "pending_tasks": None, "connections": None,
               "accepting": {},
               "last_accept": dict(self.stats["last_accept"]),
               "accept_errors": self.stats["accept_errors"],
               "last_accept_error": self.stats["last_accept_error"],
               "loop_ended": self.stats["loop_ended"],
               "restarts": self.stats["restarts"],
               "last_restart": self.stats["last_restart"],
               "last_restart_reason": self.stats["last_restart_reason"],
               "next_restart_in_s": max(0.0, round(self._next_restart_at - time.monotonic(), 1)),
               "started_at": self.started_at}
        if run is None:
            return out
        accepting = {}
        for kind, _port, _ctx in self._plan():
            for addr in self.addrs:
                key = f"{kind} {addr}"
                task = run.accepts.get(key)
                accepting[key] = bool(task is not None and not task.done()
                                      and run.failing.get(key, 0) < ACCEPT_FAILING_AFTER)
        out["accepting"] = accepting
        if not out["thread_alive"] or run.loop.is_closed():
            return out
        age = time.monotonic() - run.beat
        out["heartbeat_age_s"] = round(age, 2)
        out["advancing"] = age < self.stall_after_s
        if timeout and timeout > 0:
            answer: concurrent.futures.Future = concurrent.futures.Future()

            def ask():
                try:
                    answer.set_result((len(asyncio.all_tasks(run.loop)), len(run.conns)))
                except Exception as e:          # pragma: no cover - defensive
                    answer.set_exception(e)
            try:
                run.loop.call_soon_threadsafe(ask)
                pending, conns = answer.result(timeout)
                out.update(responsive=True, pending_tasks=pending, connections=conns)
            except Exception:
                pass
        out["ok"] = bool(out["advancing"] and all(accepting.values()))
        return out

    def ensure_alive(self, timeout: float = HEALTH_TIMEOUT_S) -> dict:
        """Replace a dead or stalled loop with a fresh one. Returns health()
        afterwards, with `restarted` saying whether it had to.

        Only two things earn a restart: the loop thread has ended, or the
        loop's heartbeat has not moved for stall_after_s (several watchdog
        rounds). A listener that cannot bind is retried by the loop itself and
        a slow answer is not a stall, because a restart drops every open
        connection, the voice socket among them, and cures neither. Restarts
        back off exponentially, to at most RESTART_BACKOFF_MAX_S apart.
        """
        h = self.health(timeout)
        h["restarted"] = False
        run = self._run_state
        if self._stopped or run is None:
            return h
        if h["thread_alive"] and h["advancing"]:
            if (self._restart_streak and self.stats["last_restart"]
                    and time.time() - self.stats["last_restart"] > RESTART_STREAK_RESET_S):
                self._restart_streak = 0          # healthy for long enough
            return h
        if not h["thread_alive"]:
            why = "the loop thread has ended" + (
                f" ({self.stats['loop_ended']})" if self.stats["loop_ended"] else "")
        else:
            why = f"the loop has not run for {h['heartbeat_age_s']} s"
        wait = self._next_restart_at - time.monotonic()
        if wait > 0:
            _log.warning("Local address: listeners for %s need a restart (%s); "
                         "the next is allowed in %.0f s", self.host, why, wait)
            return h
        stack = self._stack_of(run.thread)
        with self._lifecycle:
            if self._stopped or self._run_state is not run:
                return self.health(0)            # stopped or replaced meanwhile
            self._restart_streak += 1
            backoff = min(RESTART_BACKOFF_MAX_S,
                          self.restart_backoff_s * (2 ** (self._restart_streak - 1)))
            self._next_restart_at = time.monotonic() + backoff
            self.stats["restarts"] += 1
            self.stats["last_restart"] = time.time()
            self.stats["last_restart_reason"] = why
            _log.warning("Local address: restarting Friday's listeners for %s (restart %d, "
                         "the next allowed after %.0f s): %s. Loop thread stack:%s%s",
                         self.host, self.stats["restarts"], backoff, why, chr(10), stack)
            self._halt(run, join_s=0.5)
            self._spawn(5.0)
        after = self.health(timeout)
        after["restarted"] = True
        _log.warning("Local address: listeners for %s restarted (generation %s): %s",
                     self.host, after["generation"], self._listening_words())
        return after

    @staticmethod
    def _stack_of(thread) -> str:
        if thread is None or thread.ident is None:
            return " (no thread)"
        frame = sys._current_frames().get(thread.ident)
        if frame is None:
            return " (the thread has ended)"
        return "".join(traceback.format_stack(frame))[-6000:]

    def _listening_words(self) -> str:
        return "; ".join(f"{k} {a}: {v}" for k in ("https", "http")
                         for a, v in (self.listening.get(k) or {}).items())

    def status(self, health_timeout: float = 0.5) -> dict:
        """What is bound, and whether it is being served right now."""
        h = self.health(health_timeout) if self._run_state is not None else None
        acc = (h or {}).get("accepting") or {}
        alive = bool(h and h.get("thread_alive") and h.get("advancing"))

        def one(kind, port):
            st = self.listening.get(kind) or {}
            return {"port": port, "addrs": dict(st),
                    "listening": alive and any(v == "ok" and acc.get(f"{kind} {a}")
                                               for a, v in st.items())}
        return {"host": self.host, "https": one("https", self.https_port) if self._ctx else None,
                "http": one("http", self.http_port), "upstream_port": self.upstream_port,
                "loop": _public_health(h)}

    # ── the loop ───────────────────────────────────────────────────────
    def _run(self, run: _Run, ready: threading.Event) -> None:
        loop = run.loop
        asyncio.set_event_loop(loop)
        loop.set_exception_handler(_quiet_connection_errors)
        try:
            try:
                loop.run_until_complete(self._open(run))
            finally:
                ready.set()
            loop.run_forever()
        except BaseException as e:
            # Nothing would otherwise say so: the sockets stay bound and the
            # thread just ends. ensure_alive() notices and starts another.
            self.stats["loop_ended"] = ExceptionText(f"{type(e).__name__}: {e}"[:200])
            _log.error("Local address: Friday's listener loop ended: %s", e, exc_info=True)
        finally:
            # A loop that is gone must not leave its ports bound: a client is
            # then refused at once instead of waiting on a socket nobody reads.
            _close_all(run.socks)
            _close_all(run.conn_socks)
            try:
                loop.close()
            except Exception:
                pass

    async def _heartbeat(self, run: _Run) -> None:
        while True:
            run.beat = time.monotonic()
            await asyncio.sleep(self.heartbeat_s)

    async def _open(self, run: _Run) -> None:
        loop = asyncio.get_running_loop()
        run.beat = time.monotonic()
        loop.create_task(self._heartbeat(run))
        for kind, port, ctx in self._plan():
            for addr in self.addrs:
                try:
                    sock = _bind(addr, port)
                except OSError as e:
                    who = port_holder(port)
                    self.listening[kind][addr] = (
                        f"port {port} is in use by {who}" if who else
                        ExceptionText(f"could not listen on port {port}: {e.strerror or e}"))
                    run.binds[f"{kind} {addr}"] = loop.create_task(
                        self._bind_later(run, kind, addr, port, ctx))
                    continue
                self._serve(run, sock, kind, addr, ctx)

    def _serve(self, run: _Run, sock, kind: str, addr: str, ctx) -> None:
        run.socks.append(sock)
        run.accepts[f"{kind} {addr}"] = asyncio.get_running_loop().create_task(
            self._accept_loop(run, sock, kind, addr, ctx))
        self.listening[kind][addr] = "ok"

    async def _bind_later(self, run: _Run, kind: str, addr: str, port: int, ctx) -> None:
        """Keep trying a port that was busy, backing off to REBIND_MAX_S: the
        holder may let go (a restart's previous generation, a web server being
        stopped). The reason it failed stays on show until it binds."""
        delay = self.rebind_first_s
        while True:
            await asyncio.sleep(delay)
            try:
                sock = _bind(addr, port)
            except OSError:
                delay = min(REBIND_MAX_S, delay * 2)
                continue
            self._serve(run, sock, kind, addr, ctx)
            _log.info("Local address: %s on %s port %s is open now", kind, addr, port)
            return

    async def _accept_loop(self, run: _Run, sock, kind: str, addr: str, ctx) -> None:
        """Take connections until the socket is closed. A failed accept (a
        client that reset first, a transient resource error) costs that one
        connection, never the listener. Errors in a row are counted; past
        ACCEPT_FAILING_AFTER health() reports this listener as not accepting."""
        loop = asyncio.get_running_loop()
        key = f"{kind} {addr}"
        handler = self._https_client if kind == "https" else self._http_client
        while True:
            try:
                conn, _addr = await loop.sock_accept(sock)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if sock.fileno() == -1:
                    return
                n = run.failing.get(key, 0) + 1
                run.failing[key] = n
                self.stats["accept_errors"] += 1
                self.stats["last_accept_error"] = ExceptionText(f"{kind}: {e}"[:200])
                await asyncio.sleep(min(1.0, ACCEPT_RETRY_S * n))
                continue
            run.failing[key] = 0
            self.stats["last_accept"][kind] = time.time()
            run.conn_socks.add(conn)
            task = loop.create_task(self._connection(run, conn, handler, ctx))
            run.conns.add(task)
            task.add_done_callback(run.conns.discard)

    async def _connection(self, run: _Run, conn, handler, ctx) -> None:
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader(limit=MAX_HEAD, loop=loop)
        protocol = asyncio.StreamReaderProtocol(reader, loop=loop)
        try:
            try:
                if ctx is not None:
                    transport, _ = await loop.connect_accepted_socket(
                        lambda: protocol, conn, ssl=ctx,
                        ssl_handshake_timeout=HANDSHAKE_TIMEOUT_S)
                else:
                    transport, _ = await loop.connect_accepted_socket(lambda: protocol, conn)
            except asyncio.CancelledError:
                conn.close()
                raise
            except Exception:
                conn.close()                # a failed or abandoned handshake
                return
            writer = asyncio.StreamWriter(transport, protocol, reader, loop)
            try:
                await handler(reader, writer)
            except Exception:
                pass
            finally:
                try:
                    writer.close()
                except Exception:
                    pass
        finally:
            run.conn_socks.discard(conn)

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

        try:
            await asyncio.gather(pump(reader, up_writer), pump(up_reader, writer))
        finally:
            # also when cancelled (a stop or a restart): the upstream
            # connection is closed, not left to Friday's server to time out
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


def _quiet_connection_errors(loop, context) -> None:
    """One client's network error (a reset before its accept was taken, a
    dropped TLS handshake) is that client's business: counted by the accept
    loop, logged at debug. Anything else goes to asyncio's default report."""
    if isinstance(context.get("exception"), OSError):
        _log.debug("Local address: %s: %s", context.get("message"), context.get("exception"))
        return
    loop.default_exception_handler(context)


def _public_health(h: dict | None) -> dict | None:
    """health() for the Settings card and diagnostics: times as seconds ago."""
    if h is None:
        return None
    now = time.time()

    def ago(t):
        return None if not t else round(now - t, 1)
    out = {k: h.get(k) for k in ("ok", "generation", "thread_alive", "advancing",
                                 "heartbeat_age_s", "responsive",
                                 "pending_tasks", "connections", "accepting",
                                 "accept_errors", "last_accept_error", "loop_ended",
                                 "restarts", "last_restart_reason", "next_restart_in_s")}
    out["last_accept_s_ago"] = {k: ago(v) for k, v in (h.get("last_accept") or {}).items()}
    out["last_restart_s_ago"] = ago(h.get("last_restart"))
    out["up_s"] = ago(h.get("started_at"))
    return out
