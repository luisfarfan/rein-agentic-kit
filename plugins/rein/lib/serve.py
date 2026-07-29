"""serve -- a deterministic probe for "is the dev server up yet".

The problem this exists to solve: an agent that wants to render a page has to
start a server, wait for it, and tear it down -- and every one of those three
steps has a way to go wrong silently. `time.sleep(N)` guesses; a bare
`subprocess.Popen` for `npm run dev` leaves an orphaned child listening on the
port after the parent is killed (npm's dev-server child outlives its parent
unless the whole process *group* is signalled); and a typo'd command hangs
around for the full timeout instead of failing fast.

`serve_probe()` is a context manager: it starts `command` in `cwd` as the
leader of a new process group, polls the host/port parsed from `url` with a
real TCP connect (never a sleep), and on exit -- normal or via exception --
kills the whole group. `probe()` is the non-context-manager version the CLI
uses: start, wait for ready-or-timeout-or-early-exit, tear down, report.

Python 3 standard library only.
"""

from __future__ import annotations

import contextlib
import json
import os
import select
import signal
import socket
import subprocess
import tempfile
import time
from urllib.parse import urlsplit

DEFAULT_TIMEOUT = 30.0
POLL_INTERVAL = 0.1
STDERR_TAIL_LINES = 20


class InvalidUrlError(ValueError):
    """url has no http/https scheme and hostname -- refuse to guess a port."""


def _resolve_host_port(url: str) -> tuple[str, int]:
    parsed = urlsplit(url)
    # A schemeless url like 'localhost:3000' parses as scheme='localhost',
    # hostname=None -- silently falling back to host 'localhost', port 80 would
    # probe a COMPLETELY different service and can report a false `ready` if
    # anything happens to listen on 80. Refuse the guess instead.
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise InvalidUrlError(
            f"url {url!r} needs an http/https scheme and a host, e.g. 'http://localhost:3000' "
            f"-- refusing to guess a port"
        )
    host = parsed.hostname
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return host, port


def _tcp_ready(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


STDERR_READ_BUDGET = 0.5  # seconds -- must never block past the caller's timeout


def _read_stderr_tail(
    proc: subprocess.Popen, max_lines: int = STDERR_TAIL_LINES, budget: float = STDERR_READ_BUDGET
) -> list[str]:
    """Drain whatever stderr has buffered, without blocking on a live process.

    A `.readlines()` call blocks until EOF -- fine for a process that already
    exited, but a server that is still alive and never closes its stderr pipe
    would hang this well past the caller's own timeout. select() bounds it to
    `budget` regardless of whether the process is still running.
    """
    if proc.stderr is None:
        return []
    fd = proc.stderr.fileno()
    with contextlib.suppress(OSError, ValueError):
        os.set_blocking(fd, False)
    chunks: list[bytes] = []
    deadline = time.monotonic() + budget
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            ready, _, _ = select.select([fd], [], [], remaining)
        except (OSError, ValueError):
            break
        if not ready:
            break
        try:
            chunk = os.read(fd, 65536)
        except (BlockingIOError, OSError):
            break
        if not chunk:
            break
        chunks.append(chunk)
    data = b"".join(chunks).decode("utf-8", errors="replace")
    lines = data.splitlines()
    return lines[-max_lines:]


def _tail_file_lines(path: str, max_lines: int = STDERR_TAIL_LINES) -> list[str]:
    """Last `max_lines` lines of a plain FILE -- used for `start()`'s stderr
    capture. Unlike `_read_stderr_tail`'s live-pipe case, by the time this
    runs the process has already been torn down (see the failure branch of
    `start()`), so a normal blocking read cannot hang -- no select() budget
    needed.
    """
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return []
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[-max_lines:]


def _pgid_alive(pgid: int) -> bool:
    """Is the group LEADER (pgid == its own pid, since we start it as session
    leader) still alive. A zombie still answers this as alive until reaped --
    see the reap step in `_kill_pgid` below.
    """
    try:
        os.kill(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists, just not signallable by us -- still alive as far as we know.
        return True


def _kill_pgid(pgid: int, wait_seconds: float = 5.0) -> None:
    """Kill the whole process group: SIGTERM, wait, SIGKILL if it outlives that.

    `npm run dev` (and most dev-server wrappers) fork a child that keeps
    listening on the port even after the direct child we spawned is killed.
    Because the group leader was started with start_new_session=True, its pid
    IS the process group id, so signalling -pgid reaches every descendant --
    the direct child AND any grandchild it backgrounded.

    Works whether or not the caller still holds the leader's Popen handle: if
    it happens to be our own child (true for `_terminate_group`, and true for
    `stop()` when `start()`/`stop()` are called from the same process, as the
    tests do), the WNOHANG reap keeps a SIGTERM'd zombie from reading as
    "still alive" and spinning this loop for the full timeout every time.
    When it is NOT our child (the normal cross-process start/stop split, one
    CLI invocation each), ChildProcessError from that reap attempt is simply
    swallowed and liveness is judged by signal 0 alone.
    """
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pgid, signal.SIGTERM)
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline and _pgid_alive(pgid):
        with contextlib.suppress(ChildProcessError, ProcessLookupError):
            os.waitpid(pgid, os.WNOHANG)
        time.sleep(0.05)
    if _pgid_alive(pgid):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pgid, signal.SIGKILL)
        with contextlib.suppress(ChildProcessError, ProcessLookupError):
            os.waitpid(pgid, os.WNOHANG)


def _terminate_group(proc: subprocess.Popen) -> None:
    """Kill the whole process group of a still-owned Popen handle.

    `_kill_pgid` already reaps its own child via WNOHANG as it goes (see
    above), so no separate `proc.wait()` here -- calling `Popen.wait()` after
    something else already reaped the same pid raises ChildProcessError.
    """
    try:
        if proc.poll() is None:
            try:
                pgid = os.getpgid(proc.pid)
            except ProcessLookupError:
                pgid = None
            if pgid is not None:
                _kill_pgid(pgid)
                # `_kill_pgid` may have already reaped this pid via WNOHANG,
                # outside Popen's own bookkeeping; poll() syncs it (Popen
                # treats a resulting ECHILD as "already exited") so Popen's
                # __del__ does not warn that the subprocess is still running.
                proc.poll()
    finally:
        if proc.stderr is not None:
            with contextlib.suppress(OSError):
                proc.stderr.close()


def wait_until_ready(
    proc: subprocess.Popen, host: str, port: int, timeout: float
) -> tuple[bool, str]:
    """Poll for a real TCP accept, but bail out fast if the process died.

    Returns (ready, error). error is "" when ready is True.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        exit_code = proc.poll()
        if exit_code is not None:
            return False, f"process exited immediately with code {exit_code}"
        if _tcp_ready(host, port):
            return True, ""
        time.sleep(POLL_INTERVAL)
    return False, f"timed out after {timeout}s waiting for {host}:{port}"


class ServeResult:
    """Outcome of a serve_probe() run -- what the CLI reports as JSON."""

    def __init__(self, ready: bool, url: str, elapsed_ms: int, error: str, stderr_tail: list[str]):
        self.ready = ready
        self.url = url
        self.elapsed_ms = elapsed_ms
        self.error = error
        self.stderr_tail = stderr_tail

    def to_dict(self) -> dict:
        return {
            "ready": self.ready,
            "url": self.url,
            "elapsedMs": self.elapsed_ms,
            "error": self.error,
            "stderrTail": self.stderr_tail,
        }


@contextlib.contextmanager
def serve_probe(command: str, cwd: str, url: str, timeout: float = DEFAULT_TIMEOUT):
    """Start `command` in `cwd`, block until `url` accepts a TCP connection.

    Yields a ServeResult. On timeout or immediate exit, the yielded result has
    ready=False and the loop body runs anyway (the caller decides what to do
    with a not-ready result) -- but the process group is ALWAYS terminated on
    the way out, including when the body raises.

    An invalid `url` (no http/https scheme + host) is reported the same way --
    ready=False with a clear error -- WITHOUT ever starting `command` or
    guessing a port to probe (see `_resolve_host_port`).
    """
    try:
        host, port = _resolve_host_port(url)
    except InvalidUrlError as e:
        yield ServeResult(False, url, 0, str(e), [])
        return
    started = time.monotonic()
    proc = subprocess.Popen(
        command,
        shell=True,
        cwd=cwd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        ready, error = wait_until_ready(proc, host, port, timeout)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        stderr_tail = [] if ready else _read_stderr_tail(proc)
        result = ServeResult(ready, url, elapsed_ms, error, stderr_tail)
        yield result
    finally:
        _terminate_group(proc)


def probe(command: str, cwd: str, url: str, timeout: float = DEFAULT_TIMEOUT) -> ServeResult:
    """Non-context-manager entry point for the CLI: run once, report, tear down."""
    with serve_probe(command, cwd, url, timeout) as result:
        return result


def start(command: str, cwd: str, url: str, timeout: float, pidfile: str) -> ServeResult:
    """Start `command`, wait for `url` to accept, and -- unlike probe() -- leave
    the process group RUNNING so a caller-owned render can happen against it.

    The group was already started with start_new_session=True (its own
    session, immune to this CLI invocation's own process exiting), so simply
    not tearing it down here is enough to hand it off; its pgid is written to
    `pidfile` so a later `stop(pidfile)` call -- the SAME deterministic CLI,
    invoked a second time -- can find and kill it (D2: one CLI owns start and
    stop, even across the two calls a render needs).

    On failure (process exits immediately, or never becomes ready), there is
    nothing to hand off: the group is torn down here, same as probe(), and no
    pidfile is written.

    stderr is redirected to a TEMP FILE -- neither DEVNULL nor a PIPE, and
    the distinction from both is load-bearing, not cosmetic (finding 6).
    `probe()`/`serve_probe()` can safely use a PIPE because the process is
    always torn down inside the SAME invocation that created it. `start()` is
    explicitly meant to outlive its own invocation -- this CLI process exits
    right after printing its JSON -- and when it does, the OS closes every fd
    it held, including a PIPE's read end. The next time the still-running
    server writes an ordinary access-log line to that now-read-end-closed
    pipe, it gets SIGPIPE and dies (subprocess.Popen restores default signal
    dispositions in the child) -- a --start that reports ready=true but is
    dead by the time anything tries to render against it. A plain FILE has no
    read end to lose -- the child's duplicated write fd stays valid no matter
    what the launcher process does afterwards -- so it survives the
    launcher's exit exactly like DEVNULL did, while still letting a FAILED
    start (torn down inside THIS invocation, same as probe()) report the
    server's own stderr instead of a bare "timed out".
    """
    try:
        host, port = _resolve_host_port(url)
    except InvalidUrlError as e:
        return ServeResult(False, url, 0, str(e), [])
    started = time.monotonic()
    stderr_fd, stderr_path = tempfile.mkstemp(prefix="rein-serve-stderr-", suffix=".log")
    with os.fdopen(stderr_fd, "wb") as stderr_file:
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=stderr_file,
            start_new_session=True,
        )
    # The `with` above closes OUR copy of the write fd as soon as Popen has
    # duplicated it into the child -- the child keeps writing to its own
    # duplicate regardless, same as it would with DEVNULL.
    ready, error = wait_until_ready(proc, host, port, timeout)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if not ready:
        _terminate_group(proc)
        stderr_tail = _tail_file_lines(stderr_path)
        with contextlib.suppress(OSError):
            os.remove(stderr_path)
        return ServeResult(False, url, elapsed_ms, error, stderr_tail)
    pgid = os.getpgid(proc.pid)
    # Record the stderr path alongside the pgid: on the SUCCESS path it was
    # previously dropped on the floor, so every render leaked one temp file
    # that the still-running dev server kept writing to, unbounded, for the
    # whole render — once per fix round, per run. stop() is the only place
    # that knows the server is gone, so it is the only place that can unlink
    # it. JSON, with the old bare-int format still readable by stop().
    with open(pidfile, "w", encoding="utf-8") as f:
        json.dump({"pgid": pgid, "stderrPath": stderr_path}, f)
    return ServeResult(True, url, elapsed_ms, "", [])


def stop(pidfile: str) -> tuple[bool, str]:
    """Tear down the process group a matching start() left running.

    Reads the pgid `start()` recorded, kills the whole group with the same
    -pgid logic `_terminate_group` uses, and removes the pidfile. Returns
    (stopped, error); stopped=False (with a message, never an exception) when
    the pidfile is missing or unreadable -- there being nothing left to stop
    is reported, not raised.
    """
    try:
        with open(pidfile, encoding="utf-8") as f:
            raw = f.read().strip()
    except OSError as e:
        return False, f"could not read pidfile {pidfile!r}: {e}"
    stderr_path = ""
    try:
        # Current format is JSON; a bare integer is the older shape and must
        # still tear down rather than leaving a group alive on an upgrade.
        record = json.loads(raw)
        pgid = int(record["pgid"])
        stderr_path = str(record.get("stderrPath") or "")
    except (ValueError, TypeError, KeyError):
        try:
            pgid = int(raw)
        except ValueError as e:
            return False, f"could not read pidfile {pidfile!r}: {e}"
    _kill_pgid(pgid)
    for path in (stderr_path, pidfile):
        if path:
            with contextlib.suppress(OSError):
                os.remove(path)
    return True, ""
