#!/usr/bin/env python3
"""Verify that resolved commands actually run -- D2 made concrete.

`detect.resolve()` produces an INFERENCE: "this project probably runs `pytest
-q`". Nobody has actually run it. This module is the thing that runs it, once,
and reports the truth -- never repairing, never installing, never fixing
anything itself (D2). Verification happens where it is cheap (D3): here, at
`rein verify`, before an implementer or reviewer is ever paid for work that
was always going to hit a broken gate.

QUALIFIED, not absolute, on writes: THIS module never writes to the repo --
`run_one` only executes and captures output, `verify_commands` only reports.
But the resolved commands it runs are the PROJECT'S OWN real `test`/`lint`/
`typecheck` (etc.), and those routinely write whatever they normally write --
`.pytest_cache`, `__pycache__`, coverage output, tsc build info, node caches.
`rein verify` on a project's main checkout can leave it dirtier than it found
it; that is a property of the commands being verified, not a promise this
module breaks.

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

LIMITATION: the exit-code heuristic only catches a missing OUTER binary --
the shell itself failing to exec `command`. It is blind to a wrapper runner
(`poetry run`, `uv run`, `npm run`) that invokes CLEANLY and then exits with
an ordinary non-zero code (1, 2 -- not 126/127) because the thing it was
asked to run is missing from ITS world: `poetry run pytest` with pytest
absent from the venv, `uv run pytest` with nothing to run, `npm run test`
with no "test" script. Each of these is a SETUP problem read as `failed` (a
CODE problem) by exit code alone -- precisely the misdirection this module
exists to remove, for precisely the wrapper-runner commands `detect` prefers.
`_setup_failure_signal()` is a second, narrower signal that greps the first
lines of output for a short list of well-known wrapper phrasings ("command
not found", "missing script:", "failed to spawn", ...) before a non-126/127
non-zero exit is accepted as a genuine CODE failure. It is deliberately
narrow -- catching known wrapper wording, not second-guessing every
non-zero exit -- so it stays a signal, not a new source of misdiagnosis.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
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

# A second, narrower signal (module docstring's LIMITATION section) for a
# wrapper runner that exits cleanly-but-nonzero because the thing IT was
# asked to run is missing -- not the exit code the shell uses for "I could
# not exec this at all". Matched against the command's own first lines of
# output, case-insensitively.
_SETUP_FAILURE_PATTERNS = (
    ("failed to spawn", re.compile(r"failed to spawn", re.IGNORECASE)),  # `uv run <missing>`
    ("command not found", re.compile(r"\bcommand not found\b", re.IGNORECASE)),  # `poetry run <missing>`
    ("missing script", re.compile(r"missing script", re.IGNORECASE)),  # `npm run <missing-script>`
    ("script not found", re.compile(r'command ".*?" not found', re.IGNORECASE)),  # `yarn <missing-script>`
)


def _setup_failure_signal(text: str) -> str:
    """Name of the matched wrapper-runner phrasing, or "" when none matched."""
    for name, pattern in _SETUP_FAILURE_PATTERNS:
        if pattern.search(text):
            return name
    return ""


OUTCOME_OK = "ok"
OUTCOME_FAILED = "failed"
OUTCOME_NOT_INVOCABLE = "not_invocable"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_SKIPPED = "skipped"
# `testOne`'s cheap `{target}` is a real, existing file, but it is a synthetic
# `.tmp` path no runner's own test suite owns -- most runners report "found
# nothing to run" for it (pytest: "not found: <path>"; jest/vitest: "no test
# files found"), which is a fact about the SYNTHETIC target, not about
# whether the operator's configured testOne command works. Reporting that as
# `failed` (a CODE problem) is the exact misdirection this module exists to
# remove, so it gets its own outcome instead.
OUTCOME_INCONCLUSIVE = "inconclusive"

OUTPUT_HEAD_LINES = 20

# Matched against `testOne`'s output ONLY when it ran against the synthetic
# `_cheap_target()` and exited non-zero: known "found nothing to run" phrasing
# from mainstream test runners, distinct from `_SETUP_FAILURE_PATTERNS`
# (which says the RUNNER itself is missing, not that it ran and found nothing).
_NO_REAL_TARGET_PATTERNS = (
    re.compile(r"no tests? (?:ran|found|collected)", re.IGNORECASE),  # jest/vitest "no tests found"
    re.compile(r"no test files found", re.IGNORECASE),  # vitest
    re.compile(r"collected 0 items", re.IGNORECASE),  # pytest, with a valid-looking but empty file
    re.compile(r"not found: ", re.IGNORECASE),  # pytest usage error naming the bogus path
)


def _looks_like_synthetic_target_noise(text: str) -> bool:
    return any(p.search(text) for p in _NO_REAL_TARGET_PATTERNS)

# `serve` is a long-running dev server, not a run-to-completion command --
# running it here would just burn the whole timeout every time. `rein
# serve-probe` already verifies it (TCP-ready polling), so `verify` reports it
# as skipped rather than pretending a hang is a timeout.
_NOT_RUN_TO_COMPLETION = ("serve",)

STATE_DIR = os.path.expanduser("~/.claude/rein/verify")


class CommandResult:
    """Outcome of actually invoking one resolved command.

    Carries both the CONFIGURED command (what `detect.resolve()` produced --
    still has `{target}` literal for `testOne`) and the EXECUTED one (what
    actually ran, after substitution). `doctor` compares the persisted
    `configuredCommand` against the currently-resolved command to decide
    whether a report is still current -- comparing against the executed
    command would never match for `testOne`, whose target is a fresh temp
    path every run, and would report every `testOne` verification as stale
    forever.
    """

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
        executed_command: str | None = None,
    ):
        self.slot = slot
        self.command = command
        self.executed_command = command if executed_command is None else executed_command
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
            "executedCommand": self.executed_command,
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


def run_one(
    slot: str, command: str, cwd: str, timeout: float = DEFAULT_TIMEOUT, configured: str | None = None
) -> CommandResult:
    """Actually invoke `command` in `cwd` via the shell, once, with a hard timeout.

    `command` is what actually EXECUTES (already `{target}`-substituted, if
    applicable). `configured` is the pre-substitution command as resolved by
    `detect.resolve()` -- defaults to `command` when the caller has no
    substitution to report (the common case). Both travel into the
    `CommandResult` (see its docstring for why the distinction matters).

    `start_new_session=True` puts the shell in its own process group so a
    timeout can kill the whole group -- a compound command (`a && b`) forks a
    real child process for each part, and killing only the shell's own pid
    would leave that child running past the timeout this function promised.
    """
    configured_cmd = command if configured is None else configured
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
        return CommandResult(
            slot, configured_cmd, False, OUTCOME_NOT_INVOCABLE, None, [], elapsed_ms,
            error=str(exc), executed_command=command,
        )

    try:
        raw_stdout, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        with contextlib.suppress(Exception):
            proc.communicate(timeout=5)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return CommandResult(
            slot, configured_cmd, True, OUTCOME_TIMEOUT, None, [], elapsed_ms,
            error=f"timed out after {timeout}s -- the whole process group was killed",
            executed_command=command,
        )

    elapsed_ms = int((time.monotonic() - started) * 1000)
    text = (raw_stdout or b"").decode("utf-8", errors="replace")
    lines = _first_lines(text)
    code = proc.returncode

    if code in NOT_INVOCABLE_EXIT_CODES:
        return CommandResult(
            slot, configured_cmd, False, OUTCOME_NOT_INVOCABLE, code, lines, elapsed_ms,
            error=f"shell exit {code}: command not found or not executable",
            executed_command=command,
        )
    if code == 0:
        return CommandResult(slot, configured_cmd, True, OUTCOME_OK, code, lines, elapsed_ms, executed_command=command)

    # Matched against `lines`, NOT the whole capture: a wrapper runner that
    # cannot find its target says so and stops, on the first line. The same
    # phrasing appearing DEEP in a long run belongs to something the suite
    # itself shelled out to -- a suite that ran and reported failures, which
    # is a CODE problem. Grepping the whole capture turned that into a SETUP
    # problem and stopped the run. And `lines` is the only output the operator
    # is ever shown, so matching anything else cites evidence they cannot see.
    setup_signal = _setup_failure_signal("\n".join(lines))
    if setup_signal:
        # A wrapper runner (poetry/uv/npm/yarn) exited cleanly-but-nonzero
        # because ITS target was missing -- a SETUP problem the 126/127
        # exit-code check alone cannot see (module docstring's LIMITATION).
        return CommandResult(
            slot, configured_cmd, False, OUTCOME_NOT_INVOCABLE, code, lines, elapsed_ms,
            error=f"exit {code}, output matched a known wrapper-runner {setup_signal!r} message",
            executed_command=command,
        )
    return CommandResult(
        slot, configured_cmd, True, OUTCOME_FAILED, code, lines, elapsed_ms, executed_command=command
    )


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


def verify_commands(resolved: dict, timeout: float = DEFAULT_TIMEOUT, only: set[str] | None = None) -> dict:
    """Run every resolved command slot for real and report per-slot outcome.

    This function itself never repairs, installs, or writes anything -- it
    only executes and reports. But the commands it runs are the PROJECT'S
    OWN real test/lint/typecheck etc., in the caller's actual `root`, and
    those may write whatever they normally write (caches, build info,
    coverage files). That is not this function's write, but it is a real
    effect of calling it against a project's main checkout.

    Takes the dict `detect.resolve()` produces (or anything with the same
    `root` / `commands` shape) rather than a root path + calling resolve()
    itself, so callers that already resolved once do not pay for it twice.

    `only`, when given, restricts execution to that set of slots. The loop's
    Prepare precheck (`decideGatePrecheck`) reads only test/lint/typecheck --
    without this, `rein verify` also ran `build` (and the whole test suite
    again via `test`) inside the operator's MAIN checkout before Isolate even
    started, for information the precheck never consults (finding 3).
    """
    root = resolved["root"]
    commands = resolved.get("commands") or {}
    results: dict[str, dict] = {}
    tmp_target: str | None = None
    try:
        for slot in sorted(commands):
            if only is not None and slot not in only:
                continue
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
            used_synthetic_target = "{target}" in cmd
            if used_synthetic_target:
                if tmp_target is None:
                    tmp_target = _cheap_target()
                actual_cmd = cmd.replace("{target}", shlex.quote(tmp_target))
            result = run_one(slot, actual_cmd, root, timeout, configured=cmd)
            if (
                used_synthetic_target
                and result.outcome == OUTCOME_FAILED
                and _looks_like_synthetic_target_noise("\n".join(result.output_head))
            ):
                # The runner ran fine and reported, in its own well-known
                # words, that the SYNTHETIC target had nothing to run -- that
                # is not evidence the configured testOne command is broken.
                result = CommandResult(
                    result.slot, result.command, result.invocable, OUTCOME_INCONCLUSIVE, result.exit_code,
                    result.output_head, result.elapsed_ms,
                    error="testOne ran against the synthetic {target} placeholder, which no real test "
                          "suite owns -- the runner reported nothing to run for it, which says nothing "
                          "about whether the CONFIGURED testOne command works on a real target",
                    executed_command=result.executed_command,
                )
            results[slot] = result.to_dict()
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
# is never mistaken for "this module never touches `root` itself" (D2):
# nothing in THIS module ever writes under `root` -- the resolved commands it
# executes may, per their own project's normal behaviour (see verify_commands'
# docstring above).


# A plan's per-task `Verification` is the command that will be asked to prove
# that task's criteria. Nobody ran them before implementers were paid, and it
# cost three hours: two tasks in a real plan named a test module that did not
# exist, with `-k` filters that selected nothing, so they "passed" against
# `no tests collected` and the defect surfaced two hours later, at review.
OUTCOME_PROVES_NOTHING = "proves_nothing"


_TEST_MODULE_RE = re.compile(r"\btest_[a-z0-9_]+")
_MODULE_DIRS = ("tests", "tests/unit", "tests/integration", "test", "")


def unbacked_verifications(root: str, tasks: list) -> list:
    """Verifications naming a test module that neither EXISTS nor is PROMISED.

    Running a plan's verifications cannot gate a run: before the work exists,
    a task whose test module is part of its own deliverable reports "proves
    nothing" and is perfectly legitimate. Measured -- a plan reading `a new
    tests/test_x.py covers the case` is flagged by execution alone.

    Neither half of the rule works by itself, and both were measured over the
    63 plan texts in this repo's history:

      "the module does not exist"                    -> flags every new test
      "no criterion of this task names the module"   -> flagged 46 of 63

    Together they flag 0 of 63, and still catch the real defect: a plan whose
    verification named `tests/unit/test_tech_video_rubric.py`, a module that
    did not exist and that no criterion promised to create. That one cost
    three hours.
    """
    root = os.path.abspath(root)
    out = []
    for task in tasks or []:
        match = _TEST_MODULE_RE.search(task.get("verification") or "")
        if not match:
            continue
        module = match.group(0)
        exists = any(
            os.path.exists(os.path.join(root, d, f"{module}.py")) for d in _MODULE_DIRS
        )
        promised = module in " ".join(task.get("acceptance") or [])
        if not exists and not promised:
            out.append({
                "taskId": task.get("id") or "?",
                "module": module,
                "verification": (task.get("verification") or "").strip(),
                "reason": (
                    f"{module} does not exist and no criterion of this task says it will be "
                    f"created -- this verification can never confirm anything"
                ),
            })
    return out


def verify_plan(root: str, tasks: list, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Run each task's own Verification command and report what it can prove.

    The outcome that matters is not pass/fail -- a plan's verification is
    EXPECTED to fail before the work exists. What must never happen is a
    command that cannot prove anything at all: a missing test module, a `-k`
    that selects nothing, a path that does not exist. Those exit as if they
    were fine (pytest exits 5 on "no tests ran"), so an implementer "passes"
    verification without a single criterion being confirmed.

    So `failed` is FINE here and `proves_nothing` is not. This function never
    repairs and never writes -- it runs and reports, like `verify_commands`.
    """
    root = os.path.abspath(root)
    results = {}
    for task in tasks or []:
        tid = task.get("id") or "?"
        cmd = (task.get("verification") or "").strip().strip("`")
        if not cmd:
            results[tid] = {
                "command": "", "outcome": OUTCOME_SKIPPED,
                "provesNothing": False,
                "reason": "the task declares no verification command",
            }
            continue
        res = run_one(tid, cmd, root, timeout=timeout)
        text = "\n".join(res.output_head)
        # Ran, but selected nothing: the one state that makes a green
        # verification meaningless.
        #
        # Two signals, because one is not enough. The phrase match reads the
        # REPORTED head only (matching the whole capture is how a suite that
        # ran and failed gets misread as a broken runner -- fixed once
        # already), and a real pytest run buries "no tests ran" under plugin
        # warnings far past those 20 lines. So the decisive signal is pytest's
        # own documented exit code 5, EXIT_NOTESTSCOLLECTED, which it uses for
        # exactly this and nothing else. Scoped to commands that name pytest,
        # since 5 means something different in every other runner.
        looks_like_pytest = "pytest" in cmd
        proves_nothing = res.outcome != OUTCOME_NOT_INVOCABLE and (
            _looks_like_synthetic_target_noise(text)
            or (looks_like_pytest and res.exit_code == 5)
        )
        outcome = OUTCOME_PROVES_NOTHING if proves_nothing else res.outcome
        results[tid] = {
            "command": cmd,
            "outcome": outcome,
            "provesNothing": proves_nothing,
            "exitCode": res.exit_code,
            "outputHead": res.output_head,
            "reason": (
                "ran but selected no tests -- it cannot confirm a single criterion"
                if proves_nothing else (res.error or "")
            ),
        }
    unusable = [t for t, r in results.items()
                if r["outcome"] in (OUTCOME_PROVES_NOTHING, OUTCOME_NOT_INVOCABLE)]
    return {
        "root": root,
        "results": results,
        "unusable": unusable,
        # `failed` is deliberately NOT here: a plan's verification failing
        # before the work exists is the normal state of a plan.
        "allUsable": not unusable,
    }


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
