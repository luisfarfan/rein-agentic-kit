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
import os
import select
import signal
import socket
import subprocess
import time
from urllib.parse import urlsplit

DEFAULT_TIMEOUT = 30.0
POLL_INTERVAL = 0.1
STDERR_TAIL_LINES = 20


def _resolve_host_port(url: str) -> tuple[str, int]:
    parsed = urlsplit(url)
    host = parsed.hostname or "localhost"
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


def _terminate_group(proc: subprocess.Popen) -> None:
    """Kill the whole process group, not just the leader.

    `npm run dev` (and most dev-server wrappers) fork a child that keeps
    listening on the port even after the direct child we spawned is killed.
    Because we started the process with start_new_session=True, its pid IS
    the process group id, so signalling -pgid reaches every descendant.
    """
    try:
        if proc.poll() is None:
            try:
                pgid = os.getpgid(proc.pid)
            except ProcessLookupError:
                pgid = None
            if pgid is not None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(pgid, signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(pgid, signal.SIGKILL)
                    with contextlib.suppress(subprocess.TimeoutExpired):
                        proc.wait(timeout=5)
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
    """
    host, port = _resolve_host_port(url)
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
