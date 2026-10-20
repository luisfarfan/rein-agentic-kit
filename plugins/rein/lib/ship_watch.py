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
import subprocess
import time
from typing import Callable, Optional

import ship_answer as sa
import ship_state as ss
from orca_client import OrcaClient, OrcaError

WATCH_POLL_SECONDS = 15


def _ps_lines() -> list:
    try:
        out = subprocess.run(["ps", "-axo", "pid=,args="], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    return out.stdout.splitlines()


def driver_alive(change: str, ps: Callable[[], list] = _ps_lines, own_pid: Optional[int] = None) -> bool:
    """Is a `rein ship <change>` process running? Never matches this watcher (or a `watch`)."""
    own_pid = os.getpid() if own_pid is None else own_pid
    for line in ps():
        parts = line.split()
        if len(parts) < 2 or parts[0] == str(own_pid):
            continue
        argv = parts[1:]
        for i, word in enumerate(argv[:-1]):
            if os.path.basename(word) == "rein" and argv[i + 1] == "ship" and change in argv[i + 2:]:
                return True
    return False


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
              driver_alive: Callable[[str], bool] = driver_alive, skip: frozenset = frozenset()) -> int:
    """Exit 0 once something was printed; 1 when `once` found nothing pending.

    Gate ids in `skip` (deferred by the owner) are never printed by this run."""
    root = os.path.abspath(root)
    dead_polls = 0
    printed, first = set(skip), True
    while True:
        try:
            code, phase = _poll(root, change, client, printed, first)
            first = False
        except OrcaError as exc:
            if once:
                print("orca error %s: %s" % (exc.code, exc.message))
                return 1
            code, phase = None, "unknown"  # Orca may be restarting; keep waiting
        if code is not None:
            return code
        if once:
            return 1
        dead_polls = 0 if driver_alive(change) else dead_polls + 1
        if dead_polls >= 2:
            print('STOPPED %s: the ship driver is not running (phase %s); resume it in the Orca tab '
                  '"rein ship %s"' % (change, phase, change))
            return 0
        sleep(WATCH_POLL_SECONDS)
