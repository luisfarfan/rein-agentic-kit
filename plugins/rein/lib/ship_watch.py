"""`rein watch`: block until a `rein ship` run needs the owner, print it, exit.

The planning session runs this as a background command right after `/ship`, so
the harness wakes it when Rein asks something. Gates are listed through
ship_answer (the same logic `rein answer` uses). The first poll prints EVERY open
gate that has no queued answer as PENDING, so nothing the owner never answered is
hidden across restarts; afterwards only gates this watcher has not printed yet
are announced (as QUESTION).
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from typing import Callable, Optional

import ship_answer as sa
import ship_state as ss
from orca_client import OrcaClient, OrcaError

WATCH_POLL_SECONDS = 15
# A driver this watcher has never seen gets this many polls (2 minutes) to show up: an Orca tab
# with a slow shell profile can take a while to start `rein ship`. Once seen, two misses stop it.
STARTUP_GRACE_POLLS = 8
DEAD_POLLS = 2
_PYTHON = re.compile(r"^python(\d+(\.\d+)*)?w?$", re.IGNORECASE)
_SHIP_VALUE_FLAGS = ("--root", "--agent", "--model")
_SHIP_BOOL_FLAGS = ("--dry-run", "--detach")


def _ps_lines() -> list:
    try:
        out = subprocess.run(["ps", "-axo", "pid=,args="], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    return out.stdout.splitlines()


def _ship_flags(rest: list) -> Optional[dict]:
    """`{flag: value}` of what follows `rein ship <change>`, or None when it is not just ship's flags.

    `ps` joins argv with spaces, so a value runs up to the next `--` token (a `--root` with spaces)."""
    flags, i = {}, 0
    while i < len(rest):
        name, eq, inline = rest[i].partition("=")
        i += 1
        if name in _SHIP_BOOL_FLAGS and not eq:
            flags[name] = ""
        elif name in _SHIP_VALUE_FLAGS:
            value = [inline] if eq else []
            while i < len(rest) and not rest[i].startswith("--"):
                value.append(rest[i])
                i += 1
            if not value:
                return None
            flags[name] = " ".join(value)
        else:
            return None
    return flags


def _driver_root(argv: list, change: str) -> Optional[tuple]:
    """`(True, root or "")` when argv is a `rein ship <change>` process: the rein script run directly
    or by a python interpreter (with only interpreter options in between), `ship <change>` right
    after it and nothing but ship's own flags. None for anything else (grep, tail, an editor, a
    prompt that merely mentions the command, `rein watch`, `python -c ...`)."""
    for i, word in enumerate(argv):
        if os.path.basename(word) != "rein":
            continue
        if i > 0 and not (_PYTHON.match(os.path.basename(argv[0]))
                          and all(a.startswith("-") and a != "-c" for a in argv[1:i])):
            continue
        if argv[i + 1:i + 3] != ["ship", change]:
            continue
        flags = _ship_flags(argv[i + 3:])
        if flags is not None:
            return True, flags.get("--root", "")
    return None


def driver_alive(change: str, ps: Callable[[], list] = _ps_lines, own_pid: Optional[int] = None,
                 root: Optional[str] = None) -> bool:
    """Is a `rein ship <change>` driver running (for the plan repo `root`, when given)?

    Never matches this process. A driver whose `--root` is missing or relative cannot be placed,
    so it counts as a match: a false "alive" only delays a STOPPED, a false "dead" lies."""
    own_pid = os.getpid() if own_pid is None else own_pid
    want = os.path.realpath(root) if root else ""
    for line in ps():
        parts = line.split()
        if len(parts) < 2 or parts[0] == str(own_pid):
            continue
        found = _driver_root(parts[1:], change)
        if found is None:
            continue
        theirs = found[1]
        if not want or not theirs or not os.path.isabs(theirs) or os.path.realpath(theirs) == want:
            return True
    return False


_find_driver = driver_alive  # run_watch's `driver_alive` parameter shadows the name


def _mtime(path: str) -> Optional[float]:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


def _announce(gates: list, label: str) -> None:
    for g in gates:
        print("%s %s (%s) task %s" % (label, g["id"], g["kind"], g["task"]))
        print("question: %s" % g.get("question"))
        print("options: %s" % (", ".join(map(str, g["options"])) if g.get("options") else "(free text)"))


def _poll(root: str, change: str, client: OrcaClient, printed: set, first: bool):
    """`(code, stopped_candidate)`: code is an exit code once something was printed, else None."""
    state = sa._read_state(ss.state_path(root, change))
    phase = str((state or {}).get("phase") or "none")
    if state is not None and phase == "done":
        tasks = state.get("tasks") if isinstance(state.get("tasks"), dict) else {}
        line = ", ".join("%s %s" % (tid, (t or {}).get("status", "?")) for tid, t in sorted(tasks.items()))
        print("FINISHED %s: audit %s" % (change, state.get("audit_result") or "none"))
        print("tasks: %s" % (line or "none"))
        return 0, phase
    if state is not None:
        queued = {a["gate"] for a in ss.read_answers(root, change)}
        new = [g for g in sa._open_gates(state, client) if g["id"] not in queued and g["id"] not in printed]
        if new:
            _announce(new, "PENDING" if first else "QUESTION")
            printed.update(g["id"] for g in new)
            return 0, phase
    return None, phase


def run_watch(root: str, change: str, client: OrcaClient, once: bool = False,
              sleep: Callable[[float], None] = time.sleep,
              driver_alive: Optional[Callable[[str], bool]] = None, skip: frozenset = frozenset()) -> int:
    """Exit 0 once something was printed; 1 when `once` found nothing pending; 126 when `once`
    could not ask Orca.

    Gate ids in `skip` (deferred by the owner) are never printed by this run. STOPPED needs the
    driver missing for DEAD_POLLS polls in a row once it was seen (STARTUP_GRACE_POLLS before),
    and a state file that is still being written counts as a live driver."""
    root = os.path.abspath(root)
    alive_here = driver_alive or (lambda c: _find_driver(c, root=root))
    state_file = ss.state_path(root, change)
    dead_polls, seen, last_mtime = 0, False, _mtime(state_file)
    printed, first = set(skip), True
    while True:
        try:
            code, phase = _poll(root, change, client, printed, first)
            first = False
        except OrcaError as exc:
            if once:
                print("orca error %s: %s" % (exc.code, exc.message))
                return 126
            code, phase = None, "unknown"  # Orca may be restarting; keep waiting
        if code is not None:
            return code
        if once:
            return 1
        mtime = _mtime(state_file)
        if alive_here(change):
            seen, dead_polls = True, 0
        elif mtime is not None and mtime != last_mtime:
            dead_polls = 0  # something is still writing the state: the driver is at work
        else:
            dead_polls += 1
        last_mtime = mtime
        if dead_polls >= (DEAD_POLLS if seen else STARTUP_GRACE_POLLS):
            print('STOPPED %s: the ship driver is not running (phase %s); resume it in the Orca tab '
                  '"rein ship %s"' % (change, phase, change))
            return 0
        sleep(WATCH_POLL_SECONDS)
