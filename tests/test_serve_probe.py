"""Tests for the deterministic serve probe.

Fixture is `python3 -m http.server` over a temp directory -- no npm, no
network. Covers: a server that comes up, a command that exits immediately
(typo / missing binary), a command that never listens (timeout), and that
terminate() really kills the whole process group (no listener survives).
"""

import os
import socket
import sys
import tempfile
import time
import unittest

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


if __name__ == "__main__":
    unittest.main()
