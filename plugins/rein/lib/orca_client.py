"""Thin client over the `orca` CLI: argv builders, error envelope, JSON accessors.

This is the only module that shells out to `orca`. Every method appends
`--json`, parses stdout, raises `OrcaError` on a non-zero exit or
`"ok": false`, and returns the envelope's `result`.

The accessors at the bottom hold every JSON path recorded in
tests/fixtures/orca/README.md so the paths live in exactly one place. Several
fields (`messages[].payload`, `task.deps`, `gate.options`) are JSON *strings*
in Orca's output; the accessors decode them and tolerate values that are
already decoded, empty, or absent.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Callable, Optional


class OrcaError(Exception):
    def __init__(self, code: str, message: str, argv: Optional[list] = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.argv = list(argv or [])


def _default_runner(argv: list) -> tuple:
    proc = subprocess.run(argv, capture_output=True, text=True)
    return proc.returncode, proc.stdout


class OrcaClient:
    def __init__(
        self,
        executable: Optional[str] = None,
        runner: Optional[Callable[[list], tuple]] = None,
    ):
        self.executable = executable or os.environ.get("ORCA_CLI_COMMAND") or "orca"
        self._runner = runner or _default_runner

    def _call(self, *args: str) -> dict:
        argv = [self.executable, *args, "--json"]
        try:
            code, out = self._runner(argv)
        except OSError as exc:
            raise OrcaError("unavailable", f"cannot run {self.executable}: {exc}", argv)
        try:
            envelope = json.loads(out)
        except (ValueError, TypeError):
            envelope = None
        if not isinstance(envelope, dict):
            if code != 0:
                raise OrcaError(f"exit_{code}", (out or "").strip()[:500], argv)
            raise OrcaError("bad_json", "orca did not return a JSON object", argv)
        if code != 0 or not envelope.get("ok", False):
            err = envelope.get("error")
            err = err if isinstance(err, dict) else {}
            raise OrcaError(
                str(err.get("code") or f"exit_{code}"),
                str(err.get("message") or "orca command failed"),
                argv,
            )
        result = envelope.get("result")
        return result if isinstance(result, dict) else {}

    # -- environment ------------------------------------------------------
    def status(self) -> dict:
        return self._call("status")

    def account_list(self) -> dict:
        return self._call("account", "list")

    def repo_list(self) -> dict:
        return self._call("repo", "list")

    # -- runs and tasks ---------------------------------------------------
    def run_create(self, objective: str) -> dict:
        return self._call("orchestration", "run-create", "--objective", objective)

    def run_use(self, run_id: str) -> dict:
        return self._call("orchestration", "run-use", "--run", run_id)

    def task_create(self, spec: str, title: str, deps: list) -> dict:
        args = ["orchestration", "task-create", "--task-title", title, "--spec", spec]
        if deps:
            args += ["--deps", json.dumps(list(deps))]
        return self._call(*args)

    # -- workers ----------------------------------------------------------
    def worker_start(
        self,
        task_id: str,
        *,
        worktree: str,
        agent: str,
        model: str,
        repo: str = "",
        name: str = "",
        setup: str = "",
        comment: str = "",
    ) -> dict:
        args = [
            "orchestration", "worker-start", "--task", task_id,
            "--worktree", worktree, "--agent", agent, "--model", model,
        ]
        for flag, value in (
            ("--repo", repo), ("--name", name), ("--setup", setup), ("--comment", comment),
        ):
            if value:
                args += [flag, value]
        return self._call(*args)

    def worker_show(self, dispatch_id: str) -> dict:
        return self._call("orchestration", "worker-show", "--dispatch", dispatch_id)

    def worker_list(self, run_id: str = "") -> dict:
        args = ["orchestration", "worker-list"]
        if run_id:
            args += ["--run", run_id]
        return self._call(*args)

    def worker_release(self, dispatch_id: str) -> dict:
        return self._call("orchestration", "worker-release", "--dispatch", dispatch_id)

    # -- inbox ------------------------------------------------------------
    def check(
        self,
        *,
        wait: bool = True,
        types: str = "worker_done,escalation,question",
        timeout_ms: int = 900000,
        ack: str = "",
    ) -> dict:
        args = ["orchestration", "check"]
        if wait:
            args.append("--wait")
        args += ["--types", types, "--timeout-ms", str(timeout_ms)]
        if ack:
            args += ["--ack", ack]
        return self._call(*args)

    def reply(self, message_id: str, body: str) -> dict:
        return self._call("orchestration", "reply", "--id", message_id, "--body", body)

    # -- gates ------------------------------------------------------------
    def gate_create(self, task_id: str, question: str, options: list) -> dict:
        return self._call(
            "orchestration", "gate-create", "--task", task_id,
            "--question", question, "--options", json.dumps(list(options)),
        )

    def gate_list(self) -> dict:
        return self._call("orchestration", "gate-list")

    # -- worktrees --------------------------------------------------------
    def worktree_set(self, selector: str, *, status: str = "", comment: str = "") -> dict:
        args = ["worktree", "set", "--worktree", selector]
        if status:
            args += ["--status", status]
        if comment:
            args += ["--comment", comment]
        return self._call(*args)


# -- accessors: every JSON path from tests/fixtures/orca/README.md -----------

def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _str(value) -> str:
    return value if isinstance(value, str) else ""


def _decode(value):
    """JSON-in-a-string -> value; already-decoded values pass through."""
    if isinstance(value, str):
        if not value.strip():
            return None
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def run_id_of(run_create_result: dict) -> str:
    return _str(_dict(_dict(run_create_result).get("run")).get("id"))


def task_id_of(task_create_result: dict) -> str:
    return _str(_dict(_dict(task_create_result).get("task")).get("id"))


def dispatch_id_of(worker_start_result: dict) -> str:
    return _str(_dict(worker_start_result).get("dispatchId"))


def _effect_id(result: dict, kind: str) -> str:
    for effect in _dict(result).get("effects") or []:
        if isinstance(effect, dict) and effect.get("kind") == kind:
            return _str(effect.get("id"))
    return ""


def terminal_of(worker_start_result: dict) -> str:
    return _effect_id(worker_start_result, "terminal")


def worktree_of(result: dict) -> str:
    """Worktree id from a `worker-start` (effects[kind=worktree]) or `worker-show` result."""
    result = _dict(result)
    found = _effect_id(result, "worktree")
    return found or _str(_dict(result.get("worker")).get("worktreeId"))


def worktree_path_of(worktree_id: str) -> str:
    """The filesystem path: the part of the worktree id after `::`."""
    worktree_id = _str(worktree_id)
    if "::" not in worktree_id:
        return ""
    return worktree_id.split("::", 1)[1]


def effective_launch_of(worker_start_result: dict) -> dict:
    """What Orca actually launched: `{agent, model, effort}` (may differ from the request)."""
    launch = _dict(_dict(worker_start_result).get("launch"))
    return dict(_dict(launch.get("effective")))


def messages_of(check_result: dict) -> list:
    msgs = _dict(check_result).get("messages")
    return [m for m in msgs if isinstance(m, dict)] if isinstance(msgs, list) else []


def delivery_id_of(check_result: dict) -> str:
    return _str(_dict(check_result).get("deliveryId"))


def payload_of(message: dict) -> dict:
    decoded = _decode(_dict(message).get("payload"))
    return decoded if isinstance(decoded, dict) else {}


def gate_id_of(gate_create_result: dict) -> str:
    return _str(_dict(_dict(gate_create_result).get("gate")).get("id"))


def gates_of(gate_list_result: dict) -> list:
    """Each gate with `id, task_id, status, resolution` and `options` decoded to a list."""
    gates = _dict(gate_list_result).get("gates")
    out = []
    for gate in gates if isinstance(gates, list) else []:
        if not isinstance(gate, dict):
            continue
        item = dict(gate)
        options = _decode(gate.get("options"))
        item["options"] = options if isinstance(options, list) else []
        out.append(item)
    return out
