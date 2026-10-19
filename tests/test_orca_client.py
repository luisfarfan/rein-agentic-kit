"""`orca_client`: argv shape, error envelope, and the one home of the JSON paths.

No test shells out to `orca`; a fake runner records argv and replays either a
canned envelope or a recorded fixture from tests/fixtures/orca.
"""

from __future__ import annotations

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import orca_client  # noqa: E402
from orca_client import OrcaClient, OrcaError  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures", "orca")


def _fixture(name):
    with open(os.path.join(FIXTURES, name + ".json"), encoding="utf-8") as fh:
        return json.load(fh)["result"]


class _Fake:
    def __init__(self, envelope=None, code=0):
        self.calls = []
        self.envelope = envelope if envelope is not None else {"ok": True, "result": {}}
        self.code = code

    def __call__(self, argv):
        self.calls.append(list(argv))
        return self.code, json.dumps(self.envelope)


def _client(fake):
    return OrcaClient(executable="ORCA", runner=fake)


class ArgvTests(unittest.TestCase):
    def test_methods_build_documented_argv(self):
        fake = _Fake()
        c = _client(fake)
        c.worker_start(
            "t1", worktree="new-top-level", agent="codex", model="gpt-6.1-sol",
            repo="name:proxima-api", name="c", setup="run",
        )
        self.assertEqual(fake.calls[-1], [
            "ORCA", "orchestration", "worker-start", "--task", "t1",
            "--worktree", "new-top-level", "--agent", "codex", "--model", "gpt-6.1-sol",
            "--repo", "name:proxima-api", "--name", "c", "--setup", "run", "--json",
        ])

    def test_worker_start_omits_blank_optionals(self):
        fake = _Fake()
        _client(fake).worker_start("t1", worktree="current", agent="claude", model="haiku")
        self.assertEqual(fake.calls[-1], [
            "ORCA", "orchestration", "worker-start", "--task", "t1",
            "--worktree", "current", "--agent", "claude", "--model", "haiku", "--json",
        ])

    def test_simple_methods(self):
        fake = _Fake()
        c = _client(fake)
        c.status()
        c.account_list()
        c.repo_list()
        c.run_create("obj")
        c.run_use("run_1")
        c.worker_show("d1")
        c.worker_list()
        c.worker_list("run_1")
        c.worker_release("d1")
        c.worker_stop("d1")
        c.gate_list()
        self.assertEqual(fake.calls, [
            ["ORCA", "status", "--json"],
            ["ORCA", "account", "list", "--json"],
            ["ORCA", "repo", "list", "--json"],
            ["ORCA", "orchestration", "run-create", "--objective", "obj", "--json"],
            ["ORCA", "orchestration", "run-use", "--id", "run_1", "--json"],
            ["ORCA", "orchestration", "worker-show", "--dispatch", "d1", "--json"],
            ["ORCA", "orchestration", "worker-list", "--json"],
            ["ORCA", "orchestration", "worker-list", "--run", "run_1", "--json"],
            ["ORCA", "orchestration", "worker-release", "--dispatch", "d1", "--json"],
            ["ORCA", "orchestration", "worker-stop", "--dispatch", "d1", "--json"],
            ["ORCA", "orchestration", "gate-list", "--json"],
        ])

    def test_task_create_gate_create_reply_worktree_set(self):
        fake = _Fake()
        c = _client(fake)
        c.task_create("the spec", "T001", ["task_a", "task_b"])
        c.task_create("s", "T002", [])
        c.gate_create("task_a", "ok?", ["yes", "no"])
        c.gate_create("task_a", "free text?", [])
        c.reply("msg_1", "go ahead")
        c.worktree_set("id:wt1", workspace_status="in-review", comment="auditing")
        c.worktree_set("id:wt1")
        self.assertEqual(fake.calls, [
            ["ORCA", "orchestration", "task-create", "--task-title", "T001",
             "--spec", "the spec", "--deps", '["task_a", "task_b"]', "--json"],
            ["ORCA", "orchestration", "task-create", "--task-title", "T002",
             "--spec", "s", "--json"],
            ["ORCA", "orchestration", "gate-create", "--task", "task_a",
             "--question", "ok?", "--options", '["yes", "no"]', "--json"],
            ["ORCA", "orchestration", "gate-create", "--task", "task_a",
             "--question", "free text?", "--json"],
            ["ORCA", "orchestration", "reply", "--id", "msg_1", "--body", "go ahead", "--json"],
            ["ORCA", "worktree", "set", "--worktree", "id:wt1",
             "--workspace-status", "in-review", "--comment", "auditing", "--json"],
            ["ORCA", "worktree", "set", "--worktree", "id:wt1", "--json"],
        ])

    def test_check_flags(self):
        fake = _Fake()
        c = _client(fake)
        c.check()
        c.check(wait=False, types="worker_done", timeout_ms=1000, ack="delivery_9")
        self.assertEqual(fake.calls[0], [
            "ORCA", "orchestration", "check", "--wait",
            "--types", "worker_done,escalation,question", "--timeout-ms", "900000", "--json",
        ])
        self.assertEqual(fake.calls[1], [
            "ORCA", "orchestration", "check", "--types", "worker_done",
            "--timeout-ms", "1000", "--ack", "delivery_9", "--json",
        ])

    def test_default_executable_honours_env(self):
        old = os.environ.get("ORCA_CLI_COMMAND")
        try:
            os.environ["ORCA_CLI_COMMAND"] = "orca-dev"
            fake = _Fake()
            OrcaClient(runner=fake).status()
            self.assertEqual(fake.calls[0][0], "orca-dev")
            del os.environ["ORCA_CLI_COMMAND"]
            fake = _Fake()
            OrcaClient(runner=fake).status()
            self.assertEqual(fake.calls[0][0], "orca")
        finally:
            if old is None:
                os.environ.pop("ORCA_CLI_COMMAND", None)
            else:
                os.environ["ORCA_CLI_COMMAND"] = old

    def test_returns_result(self):
        fake = _Fake({"ok": True, "result": {"run": {"id": "r1"}}})
        self.assertEqual(_client(fake).run_create("x"), {"run": {"id": "r1"}})


class ErrorTests(unittest.TestCase):
    def test_error_envelope_raises(self):
        fake = _Fake({"ok": False, "error": {"code": "x", "message": "m"}})
        with self.assertRaises(OrcaError) as cm:
            _client(fake).status()
        self.assertEqual(cm.exception.code, "x")
        self.assertEqual(cm.exception.message, "m")
        self.assertEqual(cm.exception.argv, ["ORCA", "status", "--json"])

    def test_nonzero_exit_with_envelope_keeps_code(self):
        fake = _Fake({"ok": False, "error": {"code": "stage", "message": "boom"}}, code=1)
        with self.assertRaises(OrcaError) as cm:
            _client(fake).worker_show("d")
        self.assertEqual(cm.exception.code, "stage")

    def test_the_failure_envelope_is_kept_and_residual_resources_are_found(self):
        residual = [{"kind": "terminal", "id": "term_9"}, {"kind": "worktree", "id": "wt_3"}]
        for envelope in (
            {"ok": False, "error": {"code": "stage", "message": "m", "residualResources": residual}},
            {"ok": False, "error": {"code": "stage", "message": "m", "details": {"residualResources": residual}}},
            {"ok": False, "error": {"code": "stage", "message": "m"}, "result": {"residualResources": residual}},
        ):
            with self.subTest(envelope):
                with self.assertRaises(OrcaError) as cm:
                    _client(_Fake(envelope, code=1)).worker_show("d")
                self.assertEqual(orca_client.residual_resources_of(cm.exception),
                                 ["terminal term_9", "worktree wt_3"])
        self.assertEqual(orca_client.residual_resources_of(OrcaError("x", "y")), [])

    def test_nonzero_exit_unparseable(self):
        def runner(argv):
            return 3, "not json at all"
        with self.assertRaises(OrcaError) as cm:
            OrcaClient(executable="ORCA", runner=runner).status()
        self.assertEqual(cm.exception.code, "exit_3")

    def test_nonzero_exit_unparseable_includes_stderr(self):
        def runner(argv):
            return 2, "", "unknown option --bogus\n" + "x" * 3000
        with self.assertRaises(OrcaError) as cm:
            OrcaClient(executable="ORCA", runner=runner).status()
        self.assertEqual(cm.exception.code, "exit_2")
        self.assertTrue(cm.exception.message.startswith("unknown option --bogus"))
        self.assertEqual(len(cm.exception.message), 2000)

    def test_zero_exit_unparseable(self):
        def runner(argv):
            return 0, "garbage"
        with self.assertRaises(OrcaError) as cm:
            OrcaClient(executable="ORCA", runner=runner).status()
        self.assertEqual(cm.exception.code, "bad_json")

    def test_missing_executable(self):
        def runner(argv):
            raise FileNotFoundError("orca")
        with self.assertRaises(OrcaError) as cm:
            OrcaClient(executable="ORCA", runner=runner).status()
        self.assertEqual(cm.exception.code, "unavailable")


class AccessorTests(unittest.TestCase):
    def test_accessors_read_fixtures(self):
        run = _fixture("run_create")
        self.assertEqual(orca_client.run_id_of(run), "run_bc7b9b8da771")
        self.assertEqual(orca_client.task_id_of(_fixture("task_create")), "task_ffb73325fd13")
        start = _fixture("worker_start")
        self.assertEqual(orca_client.dispatch_id_of(start), "ctx_200d25f84c8b")
        wt = orca_client.worktree_of(start)
        self.assertTrue(wt.startswith("432cc57d"))
        self.assertTrue(orca_client.worktree_of(_fixture("worker_show")).startswith("432cc57d"))
        self.assertEqual(
            orca_client.worktree_path_of(wt), "/HOME/Developer/labs/agents/rein")
        self.assertEqual(
            orca_client.delivery_id_of(_fixture("check_worker_done")), "delivery_52d97eb7c63b")
        self.assertEqual(orca_client.gate_id_of(_fixture("gate_create")), "gate_655a4af5bd85")

    def test_effective_launch(self):
        self.assertEqual(
            orca_client.effective_launch_of(_fixture("worker_start")),
            {"agent": "claude", "model": "haiku", "effort": None})

    def test_messages_and_payload_decode_strings(self):
        msgs = orca_client.messages_of(_fixture("check_worker_done"))
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["type"], "worker_done")
        self.assertEqual(orca_client.payload_of(msgs[0]), {
            "taskId": "task_ffb73325fd13", "dispatchId": "ctx_200d25f84c8b",
            "outcome": "succeeded"})

    def test_payload_tolerates_decoded_empty_and_bad(self):
        self.assertEqual(orca_client.payload_of({"payload": {"a": 1}}), {"a": 1})
        self.assertEqual(orca_client.payload_of({"payload": None}), {})
        self.assertEqual(orca_client.payload_of({}), {})
        self.assertEqual(orca_client.payload_of({"payload": ""}), {})
        self.assertEqual(orca_client.payload_of({"payload": "{nope"}), {})
        self.assertEqual(orca_client.payload_of({"payload": "[1]"}), {})

    def test_no_messages_has_null_delivery(self):
        empty = {"deliveryId": None, "messages": []}
        self.assertEqual(orca_client.messages_of(empty), [])
        self.assertEqual(orca_client.delivery_id_of(empty), "")

    def test_gates_of(self):
        gates = orca_client.gates_of(_fixture("gate_list"))
        self.assertEqual(len(gates), 1)
        g = gates[0]
        self.assertEqual(g["id"], "gate_655a4af5bd85")
        self.assertEqual(g["task_id"], "task_3b0049f8e017")
        self.assertEqual(g["status"], "pending")
        self.assertEqual(g["options"], ["yes", "no"])
        resolved = {"gates": [{"id": "g", "task_id": "t", "status": "resolved",
                               "resolution": "yes", "options": None}]}
        r = orca_client.gates_of(resolved)[0]
        self.assertEqual((r["status"], r["resolution"], r["options"]), ("resolved", "yes", []))

    def test_missing_paths_give_empty_string(self):
        self.assertEqual(orca_client.run_id_of({}), "")
        self.assertEqual(orca_client.dispatch_id_of({}), "")
        self.assertEqual(orca_client.worktree_of({}), "")
        self.assertEqual(orca_client.worktree_path_of(""), "")
        self.assertEqual(orca_client.worktree_path_of("noseparator"), "")
        self.assertEqual(orca_client.effective_launch_of({}), {})


class QuickLaneTests(unittest.TestCase):
    def test_worktree_create_argv(self):
        fake = _Fake()
        c = _client(fake)
        c.worktree_create("path:/r", "q1", "main", "claude", "haiku", "do it")
        self.assertEqual(fake.calls[-1], [
            "ORCA", "worktree", "create", "--repo", "path:/r", "--name", "q1",
            "--base-branch", "main", "--agent", "claude", "--model", "haiku",
            "--prompt", "do it", "--setup", "run", "--activate", "--no-parent", "--json",
        ])
        c.worktree_create("path:/r", "q1", "main", "claude", "haiku", "do it", linear="ENG-1")
        self.assertEqual(fake.calls[-1], [
            "ORCA", "worktree", "create", "--repo", "path:/r", "--name", "q1",
            "--base-branch", "main", "--agent", "claude", "--model", "haiku",
            "--prompt", "do it", "--setup", "run", "--activate", "--no-parent",
            "--linear-issue", "ENG-1", "--json",
        ])

    def test_agent_launch_argv(self):
        fake = _Fake()
        _client(fake).agent_launch("path:/w", "claude", "haiku", "go")
        self.assertEqual(fake.calls[-1], [
            "ORCA", "agent", "launch", "--worktree", "path:/w", "--agent", "claude",
            "--model", "haiku", "--prompt", "go", "--focus", "--json",
        ])

    def test_created_worktree_path_of_reads_the_fixture(self):
        self.assertEqual(
            orca_client.created_worktree_path_of(_fixture("worktree_create")),
            "/HOME/orca/workspaces/rein/rein-fixture-quick",
        )

    def test_launch_handle_of_reads_the_fixture(self):
        self.assertEqual(
            orca_client.launch_handle_of(_fixture("agent_launch")),
            "term_c3fe16fe-d15b-430c-a149-03732fd867cc",
        )

    def test_launch_handle_of_is_empty_for_a_chat(self):
        # Synthetic (hand-built, not recorded): a chat launch has no terminal handle.
        chat = {"outcome": {"kind": "chat", "chatId": "c1"}}
        self.assertEqual(orca_client.launch_handle_of(chat), "")

    def test_launch_handle_of_reads_the_nested_launch_block(self):
        # Synthetic (hand-built, not recorded): the shape `orca worktree create --help`
        # documents for `--agent`, nested under `launch`.
        nested = {"launch": {"outcome": {"kind": "terminal", "handle": "term_x"}}}
        self.assertEqual(orca_client.launch_handle_of(nested), "term_x")
        chat = {"launch": {"outcome": {"kind": "chat", "chatId": "c1"}}}
        self.assertEqual(orca_client.launch_handle_of(chat), "")

    def test_accessors_return_empty_on_empty_result(self):
        self.assertEqual(orca_client.created_worktree_path_of({}), "")
        self.assertEqual(orca_client.launch_handle_of({}), "")


if __name__ == "__main__":
    unittest.main()
