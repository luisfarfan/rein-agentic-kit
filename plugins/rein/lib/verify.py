#!/usr/bin/env python3
"""Verify that resolved commands actually run -- D2 made concrete.

`detect.resolve()` produces an INFERENCE: "this project probably runs `pytest
-q`". Nobody has actually run it. This module is the thing that runs it, once,
and reports the truth -- never repairing, never installing, never writing to
the repo (D2). Verification happens where it is cheap (D3): here, at `rein
verify`, before an implementer or reviewer is ever paid for work that was
always going to hit a broken gate.

The one distinction the whole task exists to preserve: a command that could
not be INVOKED at all (missing binary, a typo, a shell that cannot resolve
it -- a SETUP problem) is not the same thing as a command that ran and
reported failure (a CODE problem, and the ordinary state of a repo mid
change). Conflating the two sends whoever reads the report chasing the wrong
kind of fix. POSIX gives this distinction for free: a shell that cannot find
or exec a command exits 126 (found, not executable) or 127 (not found) --
codes no well-behaved test runner uses for its own failures -- so those two
exit codes are read as "not invocable" and everything else that actually ran
is read on its own merits.

A command can also neither pass nor fail: it can run out the clock. A timeout
is reported as `outcome: "timeout"`, its own third thing -- not a failure
(the command never got to report one) and not a pass.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shlex
import signal
import subprocess
import tempfile
import time

DEFAULT_TIMEOUT = 120.0

# POSIX shell convention, not a guess: 126 = command found but not executable,
# 127 = command not found. No test runner in ordinary use exits with either on
# its OWN behalf, which is what makes them safe to read as "the shell could
# not invoke this at all" rather than "the command ran and failed".
NOT_INVOCABLE_EXIT_CODES = (126, 127)

OUTCOME_OK = "ok"
OUTCOME_FAILED = "failed"
OUTCOME_NOT_INVOCABLE = "not_invocable"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_SKIPPED = "skipped"

OUTPUT_HEAD_LINES = 20

# `serve` is a long-running dev server, not a run-to-completion command --
# running it here would just burn the whole timeout every time. `rein
# serve-probe` already verifies it (TCP-ready polling), so `verify` reports it
# as skipped rather than pretending a hang is a timeout.
_NOT_RUN_TO_COMPLETION = ("serve",)

STATE_DIR = os.path.expanduser("~/.claude/rein/verify")


class CommandResult:
    """Outcome of actually invoking one resolved command."""

    def __init__(
        self,
        slot: str,
        command: str,
        invocable: bool,
        outcome: str,
        exit_code: int | None,
        output_head: list[str],
        elapsed_ms: int,
        error: str = "",
    ):
        self.slot = slot
        self.command = command
        self.invocable = invocable
        self.outcome = outcome
        self.exit_code = exit_code
        self.output_head = output_head
        self.elapsed_ms = elapsed_ms
        self.error = error

    def to_dict(self) -> dict:
        return {
            "slot": self.slot,
            "command": self.command,
            "invocable": self.invocable,
            "outcome": self.outcome,
            "exitCode": self.exit_code,
            "outputHead": self.output_head,
            "elapsedMs": self.elapsed_ms,
            "error": self.error,
        }


def _first_lines(text: str, n: int = OUTPUT_HEAD_LINES) -> list[str]:
    return text.splitlines()[:n]


def _kill_group(proc: subprocess.Popen) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)


def run_one(slot: str, command: str, cwd: str, timeout: float = DEFAULT_TIMEOUT) -> CommandResult:
    """Actually invoke `command` in `cwd` via the shell, once, with a hard timeout.

    `start_new_session=True` puts the shell in its own process group so a
    timeout can kill the whole group -- a compound command (`a && b`) forks a
    real child process for each part, and killing only the shell's own pid
    would leave that child running past the timeout this function promised.
    """
    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        # The shell itself could not be spawned -- as clear a SETUP problem as
        # exit 127, just caught one layer up instead of by exit code.
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return CommandResult(slot, command, False, OUTCOME_NOT_INVOCABLE, None, [], elapsed_ms, error=str(exc))

    try:
        raw_stdout, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        with contextlib.suppress(Exception):
            proc.communicate(timeout=5)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return CommandResult(
            slot, command, True, OUTCOME_TIMEOUT, None, [], elapsed_ms,
            error=f"timed out after {timeout}s -- the whole process group was killed",
        )

    elapsed_ms = int((time.monotonic() - started) * 1000)
    text = (raw_stdout or b"").decode("utf-8", errors="replace")
    lines = _first_lines(text)
    code = proc.returncode

    if code in NOT_INVOCABLE_EXIT_CODES:
        return CommandResult(
            slot, command, False, OUTCOME_NOT_INVOCABLE, code, lines, elapsed_ms,
            error=f"shell exit {code}: command not found or not executable",
        )
    if code == 0:
        return CommandResult(slot, command, True, OUTCOME_OK, code, lines, elapsed_ms)
    return CommandResult(slot, command, True, OUTCOME_FAILED, code, lines, elapsed_ms)


def _cheap_target() -> str:
    """A path guaranteed to exist and be cheap -- OUTSIDE the repo.

    `testOne`'s whole point is bounded verification: one file, never the
    suite. Substituting `{target}` with anything that lives inside the repo
    (even a file we create and remove) would risk a project's own test runner
    picking it up as a fixture, and would put a write inside the tree verify
    promises never to touch (D2). A file in the system temp dir is real,
    trivially fast for any runner to open-and-skip, and never inside `root`.
    """
    fd, path = tempfile.mkstemp(prefix="rein-verify-target-", suffix=".tmp")
    os.close(fd)
    return path


def verify_commands(resolved: dict, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Run every resolved command slot for real and report per-slot outcome.

    Takes the dict `detect.resolve()` produces (or anything with the same
    `root` / `commands` shape) rather than a root path + calling resolve()
    itself, so callers that already resolved once do not pay for it twice.
    """
    root = resolved["root"]
    commands = resolved.get("commands") or {}
    results: dict[str, dict] = {}
    tmp_target: str | None = None
    try:
        for slot in sorted(commands):
            cmd = (commands.get(slot) or "").strip()
            if not cmd:
                continue
            if slot in _NOT_RUN_TO_COMPLETION:
                results[slot] = CommandResult(
                    slot, cmd, True, OUTCOME_SKIPPED, None, [], 0,
                    error="a long-running server -- use `rein serve-probe` instead",
                ).to_dict()
                continue
            actual_cmd = cmd
            if "{target}" in cmd:
                if tmp_target is None:
                    tmp_target = _cheap_target()
                actual_cmd = cmd.replace("{target}", shlex.quote(tmp_target))
            results[slot] = run_one(slot, actual_cmd, root, timeout).to_dict()
    finally:
        if tmp_target and os.path.exists(tmp_target):
            with contextlib.suppress(OSError):
                os.remove(tmp_target)

    all_invocable = all(r["invocable"] for r in results.values())
    return {
        "root": root,
        "timeoutSeconds": timeout,
        "checkedAt": time.time(),
        "results": results,
        "allInvocable": all_invocable,
    }


# ------------------------------------------------------------------- state --
# Persisted OUTSIDE the repo (same convention as token_report's ledger under
# ~/.claude/rein/) so `rein doctor` can report the last-known verification
# state without running anything itself -- and so `verify` writing this file
# is never mistaken for the "never writes to the repo" guarantee it exists to
# uphold (D2): nothing here ever touches a path under `root`.


def _state_path(root: str) -> str:
    abs_root = os.path.abspath(root)
    digest = hashlib.sha256(abs_root.encode("utf-8")).hexdigest()[:16]
    base = os.path.basename(abs_root.rstrip(os.sep)) or "root"
    safe_base = "".join(c if c.isalnum() or c in "-_." else "-" for c in base)
    return os.path.join(STATE_DIR, f"{safe_base}-{digest}.json")


def write_state(root: str, report: dict) -> str:
    os.makedirs(STATE_DIR, exist_ok=True)
    path = _state_path(root)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    return path


def read_state(root: str) -> dict | None:
    path = _state_path(root)
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


if __name__ == "__main__":
    import sys

    HERE = os.path.dirname(os.path.abspath(os.path.realpath(__file__)))
    sys.path.insert(0, HERE)
    import detect as _detect  # noqa: E402

    _root = sys.argv[1] if len(sys.argv) > 1 else "."
    _report = verify_commands(_detect.resolve(_root))
    print(json.dumps(_report, indent=2))
