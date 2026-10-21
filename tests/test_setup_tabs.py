"""Idle "Setup" tabs: a worktree's setup script leaves its tab open at a shell prompt.

`close_idle_setup_tabs` closes only those (title exactly "Setup", this worktree, quiet for a
while); the ship driver and `rein quick done` call it once setup is certainly over.
No test shells out to `orca`: a fake runner answers `terminal list` / `terminal close`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import orca_client  # noqa: E402
import ship  # noqa: E402
import ship_decide as sd  # noqa: E402
from orca_client import OrcaClient  # noqa: E402

NOW = 1_800_000_000.0  # seconds


class _Orca:
    """Fake runner: `terminal list` returns `rows`; `terminal close` records the handle."""

    def __init__(self, rows=(), fail_list=False, fail_close=()):
        self.rows = list(rows)
        self.fail_list = fail_list
        self.fail_close = set(fail_close)
        self.calls = []
        self.closed = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        head = argv[1:3]
        if head == ["terminal", "list"]:
            if self.fail_list:
                return 1, json.dumps({"ok": False, "error": {"code": "unavailable", "message": "down"}})
            return 0, json.dumps({"ok": True, "result": {"terminals": self.rows}})
        if head == ["terminal", "close"]:
            handle = argv[argv.index("--terminal") + 1]
            if handle in self.fail_close:
                return 1, json.dumps({"ok": False, "error": {"code": "gone", "message": "no such terminal"}})
            self.closed.append(handle)
            return 0, json.dumps({"ok": True, "result": {}})
        return 1, json.dumps({"ok": False, "error": {"code": "unknown", "message": "no"}})

    def lists(self):
        return [c for c in self.calls if c[1:3] == ["terminal", "list"]]


def _row(handle, path, title="Setup", ago=600.0, iso=False, **extra):
    at = NOW - ago
    if iso:
        import datetime
        stamp = datetime.datetime.fromtimestamp(at, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    else:
        stamp = int(at * 1000)
    row = {"handle": handle, "title": title, "worktreePath": path, "lastOutputAt": stamp,
           "connected": True}
    row.update(extra)
    return row


class ClientArgvTests(unittest.TestCase):
    def test_terminal_list_and_tab_close_argv(self):
        fake = _Orca()
        c = OrcaClient(executable="ORCA", runner=fake)
        c.terminal_list()
        c.terminal_close("term_1", tab=True)
        self.assertEqual(fake.calls, [
            ["ORCA", "terminal", "list", "--json"],
            ["ORCA", "terminal", "close", "--terminal", "term_1", "--tab", "--json"],
        ])

    def test_default_runner_never_reads_the_callers_stdin(self):
        done = subprocess.CompletedProcess([], 0, "{}", "")
        with mock.patch.object(orca_client.subprocess, "run", return_value=done) as run:
            orca_client._default_runner(["orca", "status", "--json"], timeout=5)
        self.assertIs(run.call_args.kwargs.get("stdin"), subprocess.DEVNULL)


class CloseIdleSetupTabsTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.wt = os.path.join(self.root, "wt")
        self.other = os.path.join(self.root, "other")
        os.makedirs(self.wt)
        os.makedirs(self.other)
        self.logs = []

    def close(self, fake, path=None, **kw):
        client = OrcaClient(executable="ORCA", runner=fake)
        return orca_client.close_idle_setup_tabs(client, path or self.wt, now=lambda: NOW,
                                                 say=self.logs.append, **kw)

    def test_idle_setup_tab_is_closed(self):
        fake = _Orca([_row("t_ms", self.wt), _row("t_iso", self.wt, iso=True)])
        self.assertEqual(self.close(fake), ["t_ms", "t_iso"])
        self.assertEqual(fake.closed, ["t_ms", "t_iso"])

    def test_a_path_spelled_differently_is_the_same_worktree(self):
        link = os.path.join(self.root, "link")
        os.symlink(self.wt, link)
        fake = _Orca([_row("t1", self.wt + "/")])
        self.assertEqual(self.close(fake, path=link), ["t1"])

    def test_setup_tab_with_recent_output_is_kept(self):
        fake = _Orca([_row("t1", self.wt, ago=10), _row("t2", self.wt, ago=10, iso=True)])
        self.assertEqual(self.close(fake), [])
        self.assertEqual(fake.closed, [])

    def test_quiet_seconds_is_the_threshold(self):
        fake = _Orca([_row("t1", self.wt, ago=200)])
        self.assertEqual(self.close(fake, quiet_seconds=300), [])
        self.assertEqual(self.close(fake, quiet_seconds=100), ["t1"])

    def test_setup_tab_without_a_readable_last_output_is_kept(self):
        rows = [_row("t1", self.wt), _row("t2", self.wt), _row("t3", self.wt)]
        rows[0]["lastOutputAt"] = None
        rows[1]["lastOutputAt"] = "yesterday"
        del rows[2]["lastOutputAt"]
        fake = _Orca(rows)
        self.assertEqual(self.close(fake), [])

    def test_other_titles_are_kept(self):
        fake = _Orca([_row("t1", self.wt, title="claude"), _row("t2", self.wt, title="rein ship x"),
                      _row("t3", self.wt, title="Setup 2"), _row("t4", self.wt, title="setup"),
                      _row("t5", self.wt, title=" Setup")])
        self.assertEqual(self.close(fake), [])
        self.assertEqual(fake.closed, [])

    def test_setup_tab_of_another_worktree_is_kept(self):
        fake = _Orca([_row("t1", self.other), _row("t2", ""), {"handle": "t3", "title": "Setup",
                                                               "lastOutputAt": 1}])
        self.assertEqual(self.close(fake), [])
        self.assertEqual(fake.closed, [])

    def test_a_failed_list_is_logged_and_ignored(self):
        fake = _Orca(fail_list=True)
        self.assertEqual(self.close(fake), [])
        self.assertTrue(any("Setup" in line for line in self.logs), self.logs)

    def test_a_failed_close_is_logged_and_the_rest_still_close(self):
        fake = _Orca([_row("t1", self.wt), _row("t2", self.wt)], fail_close={"t1"})
        self.assertEqual(self.close(fake), ["t2"])
        self.assertTrue(any("t1" in line for line in self.logs), self.logs)

    def test_orca_missing_is_ignored(self):
        def boom(argv):
            raise OSError("no orca")
        self.assertEqual(self.close(boom), [])

    def test_an_empty_worktree_path_does_nothing(self):
        fake = _Orca([_row("t1", "")])
        client = OrcaClient(executable="ORCA", runner=fake)
        self.assertEqual(orca_client.close_idle_setup_tabs(client, "", now=lambda: NOW), [])
        self.assertEqual(fake.calls, [])


class _Pool:
    def __init__(self):
        self.submitted = []

    def submit(self, key, job):
        self.submitted.append(key)
        return True


class ShipDriverTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.api = os.path.join(self.root, "api")
        self.admin = os.path.join(self.root, "admin")
        os.makedirs(self.api)
        os.makedirs(self.admin)

    def driver(self, fake):
        drv = ship._Driver.__new__(ship._Driver)
        drv.client = OrcaClient(executable="ORCA", runner=fake)
        drv.pool = _Pool()
        drv.dry_run = False
        drv.root = self.root
        drv.gate_runner = lambda *a, **k: (0, "")
        drv.cfg = SimpleNamespace(gate_timeout_seconds=60)
        drv.say = lambda text: None
        task = SimpleNamespace(id="T1", repo="api", dispatch_id="d1", verification="", type="code",
                               base="", status="gating")
        drv.state = SimpleNamespace(worktree_paths={"api": self.api, "admin": self.admin},
                                    tasks={"T1": task}, base_refs={})
        return drv

    def test_gates_tidy_setup_tabs_once_per_worktree(self):
        fake = _Orca([_row("t_api", self.api), _row("t_admin", self.admin)])
        drv = self.driver(fake)
        with mock.patch.object(orca_client, "time") as clock, \
                mock.patch.object(drv, "task_base", return_value="origin/develop", create=True), \
                mock.patch.object(drv, "worktree_env", return_value={}, create=True):
            clock.time.return_value = NOW
            drv.execute(sd.RunGate("T1", True))
            drv.execute(sd.RunGate("T1", True))
            drv.execute(sd.RunRepoGate("api"))
            drv.execute(sd.RunRepoGate("admin"))
            drv.execute(sd.RunRepoGate("admin"))
        self.assertEqual(len(fake.lists()), 2)
        self.assertEqual(fake.closed, ["t_api", "t_admin"])
        self.assertEqual(len(drv.pool.submitted), 5)

    def test_an_orca_error_never_stops_the_gate(self):
        fake = _Orca(fail_list=True)
        drv = self.driver(fake)
        drv.execute(sd.RunRepoGate("api"))
        self.assertEqual(drv.pool.submitted, [("repo", "api")])

    def test_a_dry_run_lists_nothing(self):
        fake = _Orca([_row("t_api", self.api)])
        drv = self.driver(fake)
        drv.dry_run = True
        drv.execute(sd.RunRepoGate("api"))
        self.assertEqual(fake.calls, [])


class QuickDoneTests(unittest.TestCase):
    def test_done_tidies_setup_tabs_before_gating(self):
        import quick_driver
        ws = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, ws, True)
        order = []
        with mock.patch.object(quick_driver, "run_git", return_value=(0, ws + "\n")), \
                mock.patch.object(quick_driver, "_load_record", return_value=({"base_sha": "a" * 40}, "")), \
                mock.patch.object(quick_driver.ship, "changed_files",
                                  side_effect=lambda *a: order.append("gate-input") or []), \
                mock.patch.object(quick_driver.oc, "close_idle_setup_tabs",
                                  side_effect=lambda client, path, **kw: order.append(("tidy", path)) or []):
            code = quick_driver.done_quick(cwd=ws, client=OrcaClient(executable="ORCA", runner=_Orca()),
                                           env={}, say=lambda text: None)
        self.assertEqual(code, 0)
        self.assertEqual(order, [("tidy", ws), "gate-input"])


if __name__ == "__main__":
    unittest.main()
