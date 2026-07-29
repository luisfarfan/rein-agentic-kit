"""Tests for the deterministic serve probe.

Fixture is `python3 -m http.server` over a temp directory -- no npm, no
network. Covers: a server that comes up, a command that exits immediately
(typo / missing binary), a command that never listens (timeout), and that
terminate() really kills the whole process group (no listener survives).
"""

import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REIN_BIN = os.path.join(REPO_ROOT, "plugins", "rein", "bin", "rein")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins", "rein", "lib"))

import serve  # noqa: E402


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


class TestServeProbeReady(unittest.TestCase):
    def test_ready_within_timeout(self):
        port = _free_port()
        with tempfile.TemporaryDirectory() as tmpdir:
            command = f"{sys.executable} -m http.server {port} --bind 127.0.0.1"
            with serve.serve_probe(command, tmpdir, f"http://127.0.0.1:{port}", timeout=10) as result:
                self.assertTrue(result.ready, msg=result.error)
                self.assertEqual(result.error, "")
                self.assertEqual(result.stderr_tail, [])
                self.assertGreaterEqual(result.elapsed_ms, 0)
                self.assertTrue(_listening(port))
            # after the context manager exits, the whole group must be gone
            self.assertFalse(_listening(port))


class TestServeProbeImmediateExit(unittest.TestCase):
    def test_missing_binary_fails_fast(self):
        port = _free_port()
        with tempfile.TemporaryDirectory() as tmpdir:
            started = time.monotonic()
            with serve.serve_probe(
                "this-binary-does-not-exist-xyz", tmpdir, f"http://127.0.0.1:{port}", timeout=10
            ) as result:
                elapsed = time.monotonic() - started
                self.assertFalse(result.ready)
                self.assertIn("exited immediately", result.error)
            # reported well within the 10s timeout budget
            self.assertLess(elapsed, 5)


class TestServeProbeTimeout(unittest.TestCase):
    def test_never_listens_times_out_with_stderr_tail(self):
        port = _free_port()
        with tempfile.TemporaryDirectory() as tmpdir:
            # a process that stays alive, writes to stderr, but never listens
            command = (
                f"{sys.executable} -c \"import sys, time; "
                "sys.stderr.write('hello from stderr\\n'); sys.stderr.flush(); "
                "time.sleep(5)\""
            )
            with serve.serve_probe(command, tmpdir, f"http://127.0.0.1:{port}", timeout=1) as result:
                self.assertFalse(result.ready)
                self.assertIn("timed out", result.error)
                self.assertTrue(any("hello from stderr" in line for line in result.stderr_tail))


class TestServeProbeTerminatesGroup(unittest.TestCase):
    def test_no_listener_remains_after_exit_even_on_exception(self):
        port = _free_port()
        with tempfile.TemporaryDirectory() as tmpdir:
            command = f"{sys.executable} -m http.server {port} --bind 127.0.0.1"
            with self.assertRaises(RuntimeError):
                with serve.serve_probe(command, tmpdir, f"http://127.0.0.1:{port}", timeout=10) as result:
                    self.assertTrue(result.ready, msg=result.error)
                    self.assertTrue(_listening(port))
                    raise RuntimeError("boom")
            # terminate() must run even though the body raised
            self.assertFalse(_listening(port))

    def test_grandchild_listener_is_killed_via_process_group(self):
        """The listener here is a GRANDCHILD of proc.pid, not proc.pid itself.

        `sh -c "(python -m http.server & ); sleep 30"` forces the shell to
        stay alive as an interpreter (subshell + backgrounding + sequencing
        means it can't exec straight into the server the way a bare command
        does), so proc.pid is the `sh`/`sleep` process and the real listener
        is a separate pid still sharing its process group. A naive
        `os.kill(proc.pid, SIGTERM)` would leave this listener running; only
        signalling the whole group (-pgid) reaches it -- the exact case T001's
        group-kill AC exists for.
        """
        port = _free_port()
        with tempfile.TemporaryDirectory() as tmpdir:
            command = (
                f"({sys.executable} -m http.server {port} --bind 127.0.0.1 &) ; sleep 30"
            )
            with serve.serve_probe(command, tmpdir, f"http://127.0.0.1:{port}", timeout=10) as result:
                self.assertTrue(result.ready, msg=result.error)
                self.assertTrue(_listening(port))
            self.assertFalse(_listening(port))


class TestServeProbeStartStop(unittest.TestCase):
    """T002/finding-2: the hold mode a render actually needs -- start leaves
    the server running past the call, stop tears down what start left."""

    def test_start_leaves_server_running_until_stop(self):
        port = _free_port()
        with tempfile.TemporaryDirectory() as tmpdir:
            pidfile = os.path.join(tmpdir, "serve.pid")
            command = f"{sys.executable} -m http.server {port} --bind 127.0.0.1"
            result = serve.start(command, tmpdir, f"http://127.0.0.1:{port}", 10, pidfile)
            try:
                self.assertTrue(result.ready, msg=result.error)
                # unlike probe()/serve_probe(), the server is still up after start() returns
                self.assertTrue(_listening(port))
                self.assertTrue(os.path.exists(pidfile))
            finally:
                stopped, error = serve.stop(pidfile)
            self.assertTrue(stopped, msg=error)
            self.assertFalse(_listening(port))
            self.assertFalse(os.path.exists(pidfile))

    def test_start_failure_tears_itself_down_and_writes_no_pidfile(self):
        port = _free_port()
        with tempfile.TemporaryDirectory() as tmpdir:
            pidfile = os.path.join(tmpdir, "serve.pid")
            result = serve.start(
                "this-binary-does-not-exist-xyz", tmpdir, f"http://127.0.0.1:{port}", 10, pidfile
            )
            self.assertFalse(result.ready)
            self.assertIn("exited immediately", result.error)
            self.assertFalse(os.path.exists(pidfile))
            self.assertFalse(_listening(port))

    def test_start_timeout_carries_the_servers_own_stderr(self):
        # Finding 6: the shipped render path (T002/T003) uses ONLY --start, so
        # a dev server that fails to boot must not report a bare "timed out"
        # -- the fix agent needs the server's own compile/bind error.
        port = _free_port()
        with tempfile.TemporaryDirectory() as tmpdir:
            pidfile = os.path.join(tmpdir, "serve.pid")
            command = (
                f"{sys.executable} -c \"import sys, time; "
                "sys.stderr.write('boom: address already in use\\n'); sys.stderr.flush(); "
                "time.sleep(5)\""
            )
            result = serve.start(command, tmpdir, f"http://127.0.0.1:{port}", 1, pidfile)
            self.assertFalse(result.ready)
            self.assertIn("timed out", result.error)
            self.assertFalse(os.path.exists(pidfile))
            self.assertTrue(
                any("boom: address already in use" in line for line in result.stderr_tail),
                msg=f"stderr_tail was {result.stderr_tail!r}",
            )

    def test_stop_with_missing_pidfile_reports_not_stopped_not_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stopped, error = serve.stop(os.path.join(tmpdir, "does-not-exist.pid"))
            self.assertFalse(stopped)
            self.assertTrue(error)


class TestResolveHostPort(unittest.TestCase):
    """Finding 6: a schemeless url must never silently fall back to port 80."""

    def test_schemeless_url_raises(self):
        with self.assertRaises(serve.InvalidUrlError):
            serve._resolve_host_port("localhost:3000")

    def test_scheme_missing_host_raises(self):
        with self.assertRaises(serve.InvalidUrlError):
            serve._resolve_host_port("http://")

    def test_non_http_scheme_raises(self):
        with self.assertRaises(serve.InvalidUrlError):
            serve._resolve_host_port("ftp://localhost:21")

    def test_valid_http_url_parses(self):
        self.assertEqual(serve._resolve_host_port("http://localhost:3000"), ("localhost", 3000))

    def test_https_url_defaults_to_443(self):
        self.assertEqual(serve._resolve_host_port("https://example.com"), ("example.com", 443))


class TestServeProbeCliStartStopSurvivesLauncherExit(unittest.TestCase):
    """`start()`/`stop()` through the REAL `bin/rein` CLI, as two genuinely
    separate OS processes -- exactly how a render agent uses it (one bash
    call for --start, a different tool entirely for the render, another bash
    call for --stop). A library-level call to `serve.start()` from inside
    this same test process would NOT catch a real regression here: it stayed
    reachable purely because Python's file objects for the pipe hadn't been
    closed by process exit yet. Once the whole `--start` PROCESS exits (not
    just the Popen handle going out of scope), the OS closes every fd that
    process held; if the server's stderr were still a PIPE, the server would
    get SIGPIPE and die the next time it logged a request -- a `ready: true`
    that reports a server already dead by the time anything renders against
    it. This is a subprocess-level test specifically so that failure mode is
    reachable.
    """

    def test_server_answers_a_real_http_request_after_the_start_process_has_exited(self):
        port = _free_port()
        with tempfile.TemporaryDirectory() as tmpdir:
            pidfile = os.path.join(tmpdir, "serve.pid")
            command = f"{sys.executable} -m http.server {port} --bind 127.0.0.1"
            start = subprocess.run(
                [
                    sys.executable, REIN_BIN, "serve-probe",
                    "--command", command, "--url", f"http://127.0.0.1:{port}",
                    "--start", "--pidfile", pidfile,
                ],
                capture_output=True, text=True, timeout=30,
            )
            self.addCleanup(
                subprocess.run,
                [sys.executable, REIN_BIN, "serve-probe", "--stop", "--pidfile", pidfile],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(start.returncode, 0, msg=start.stdout + start.stderr)
            self.assertIn('"ready": true', start.stdout)
            # The --start process has now FULLY exited (subprocess.run blocked
            # until it did) -- this is the real test of survival across a
            # launcher's own exit, not just a Popen object going out of scope.
            import urllib.request
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
                self.assertEqual(resp.status, 200)

            stop = subprocess.run(
                [sys.executable, REIN_BIN, "serve-probe", "--stop", "--pidfile", pidfile],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(stop.returncode, 0, msg=stop.stdout + stop.stderr)
            self.assertFalse(_listening(port))
            self.assertFalse(os.path.exists(pidfile))


class TestServeProbeRejectsSchemelessUrl(unittest.TestCase):
    def test_schemeless_url_is_reported_not_ready_never_guesses_port_80(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            started = time.monotonic()
            with serve.serve_probe("true", tmpdir, "localhost:3000", timeout=5) as result:
                elapsed = time.monotonic() - started
                self.assertFalse(result.ready)
                self.assertIn("scheme", result.error.lower())
            # short-circuited before ever starting `command` or polling --
            # nowhere near the 5s timeout budget the poll loop would burn.
            self.assertLess(elapsed, 1)


if __name__ == "__main__":
    unittest.main()
