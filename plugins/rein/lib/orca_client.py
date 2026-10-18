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
    def __init__(self, code: str, message: str, argv: Optional[list] = None,
                 envelope: Optional[dict] = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.argv = list(argv or [])
        # The whole failure envelope: a failed `worker-start` reports what it
        # left behind (`residualResources`, `failedStage`) alongside the error.
        self.envelope = envelope if isinstance(envelope, dict) else {}


def _default_runner(argv: list) -> tuple:
    """`(exit code, stdout, stderr)`. A custom runner may return just `(code, stdout)`."""
    proc = subprocess.run(argv, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


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
            ran = self._runner(argv)
        except OSError as exc:
            raise OrcaError("unavailable", f"cannot run {self.executable}: {exc}", argv)
        code, out = ran[0], ran[1]
        err_text = ran[2] if len(ran) > 2 else ""
        try:
            envelope = json.loads(out)
        except (ValueError, TypeError):
            envelope = None
        if not isinstance(envelope, dict):
            if code != 0:
                detail = (err_text or "").strip() or (out or "").strip()
                raise OrcaError(f"exit_{code}", detail[:2000], argv)
            raise OrcaError("bad_json", "orca did not return a JSON object", argv)
        if code != 0 or not envelope.get("ok", False):
            err = envelope.get("error")
            err = err if isinstance(err, dict) else {}
            raise OrcaError(
                str(err.get("code") or f"exit_{code}"),
                str(err.get("message") or "orca command failed"),
                argv,
                envelope,
            )
        result = envelope.get("result")
        return result if isinstance(result, dict) else {}

    def call(self, *args: str) -> dict:
        """Run one orca subcommand given as argv tail (without `orca`/`--json`).

        For executing an argv Orca itself proposed (a worker's `nextAction`);
        the caller is responsible for allow-listing what it passes.
        """
        return self._call(*args)

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
        return self._call("orchestration", "run-use", "--id", run_id)

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

    def worker_stop(self, dispatch_id: str) -> dict:
        """Ask Orca to stop the worker. Not proof that it exited: confirm with `worker_show`."""
        return self._call("orchestration", "worker-stop", "--dispatch", dispatch_id)

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
        args = ["orchestration", "gate-create", "--task", task_id, "--question", question]
        if options:  # a free-text question has no options; an empty array is not a valid choice list
            args += ["--options", json.dumps(list(options))]
        return self._call(*args)

    def gate_list(self, run_id: str = "") -> dict:
        args = ["orchestration", "gate-list"]
        if run_id:  # a caller outside the run's terminal must name the run
            args += ["--run", run_id]
        return self._call(*args)

    def gate_resolve(self, gate_id: str, resolution: str, from_handle: str = "") -> dict:
        """`from_handle` must be the terminal the run is bound to when the caller is not that terminal."""
        args = ["orchestration", "gate-resolve", "--id", gate_id, "--resolution", resolution]
        if from_handle:
            args += ["--from", from_handle]
        return self._call(*args)

    def run_show(self, run_id: str) -> dict:
        return self._call("orchestration", "run-show", "--id", run_id)

    # -- terminals --------------------------------------------------------
    def terminal_create(self, worktree: str, title: str, command: str) -> dict:
        """`worktree` is an Orca selector such as `path:/abs/dir`."""
        return self._call("terminal", "create", "--worktree", worktree, "--title", title,
                          "--command", command)

    def terminal_list(self, worktree: str = "") -> dict:
        args = ["terminal", "list"]
        if worktree:  # an Orca selector such as `path:/abs/dir`
            args += ["--worktree", worktree]
        return self._call(*args)

    # -- worktrees --------------------------------------------------------
    def worktree_set(
        self, selector: str, *, workspace_status: str = "", comment: str = ""
    ) -> dict:
        """`workspace_status` is a workspace-status id (todo, in-progress, in-review, completed, ...)."""
        args = ["worktree", "set", "--worktree", selector]
        if workspace_status:
            args += ["--workspace-status", workspace_status]
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


def residual_resources_of(error) -> list:
    """What a failed `worker-start` left behind, as `kind id` strings.

    Orca documents `residualResources[]` for a failed start; where in the error
    envelope it sits is not pinned down by the recorded fixtures, so the usual
    places are all searched.
    """
    envelope = _dict(getattr(error, "envelope", error))
    err = _dict(envelope.get("error"))
    holders = [err, _dict(err.get("details")), _dict(err.get("data")), _dict(envelope.get("result")), envelope]
    for holder in holders:
        found = holder.get("residualResources")
        if isinstance(found, list) and found:
            return [
                " ".join(str(part) for part in (r.get("kind"), r.get("id")) if part) or json.dumps(r, sort_keys=True)
                for r in found if isinstance(r, dict)
            ]
    return []


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


def terminal_handle_of(terminal_create_result: dict) -> str:
    return _str(_dict(_dict(terminal_create_result).get("terminal")).get("handle"))


def terminals_of(terminal_list_result: dict) -> list:
    items = _dict(terminal_list_result).get("terminals")
    return [t for t in items if isinstance(t, dict)] if isinstance(items, list) else []


def coordinator_of(run_show_result: dict) -> str:
    """The terminal handle a run is bound to: `gate-resolve --from` must be this."""
    return _str(_dict(_dict(run_show_result).get("run")).get("coordinator_handle"))
