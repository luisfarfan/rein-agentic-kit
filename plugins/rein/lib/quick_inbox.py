"""A quick task's line to the orchestrator: questions, answers and the watch that relays them.

`rein quick` hands a task to an agent in its own tab and returns; nobody supervises it the
way the ship driver supervises a worker. So the agent asks through Rein, never only in its
terminal: `rein quick ask` records the question in `.rein/quick.json` (the one file
`rein quick close` deletes), the orchestrator's `rein quick watch` prints it, and
`rein quick answer` records the owner's answer and types it into the agent's terminal.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Callable

import quick
import quick_driver as qd
from orca_client import OrcaClient, OrcaError

WATCH_POLL_SECONDS = 10


# --- the record ----------------------------------------------------------------

def workspace_of(cwd: str) -> str:
    code, top = qd.run_git(cwd or ".", "rev-parse", "--show-toplevel")
    return top.strip() if code == 0 else ""


def save(workspace: str, record: dict) -> None:
    """Replace the record atomically: the agent and the orchestrator both write it."""
    folder = os.path.join(workspace, os.path.dirname(quick.RECORD))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".quick.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, os.path.join(workspace, quick.RECORD))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def questions(record: dict) -> list:
    found = record.get("questions")
    return [q for q in found if isinstance(q, dict)] if isinstance(found, list) else []


def open_questions(record: dict) -> list:
    return [q for q in questions(record) if q.get("answer") is None]


owner_decisions = quick.owner_decisions


def targets(arg: str, client: OrcaClient, cwd: str = ".") -> list:
    """The quick workspaces `arg` names (a path or a `quick-*` worktree name), or, with no
    argument, every Orca worktree and the checkout `cwd` is in that holds a record."""
    if arg and os.path.isdir(arg):
        top = workspace_of(arg) or os.path.abspath(arg)
        return [top] if os.path.isfile(os.path.join(top, quick.RECORD)) else []
    try:
        listed = [str(w.get("path")) for w in client.worktree_list().get("worktrees") or []
                  if isinstance(w, dict) and w.get("path")]
    except OrcaError:
        listed = []
    if arg:
        return [p for p in listed if os.path.basename(p.rstrip("/")) == arg
                and os.path.isfile(os.path.join(p, quick.RECORD))]
    found, seen = [], set()
    for path in listed + [workspace_of(cwd)]:
        real = os.path.realpath(path) if path else ""
        if real and real not in seen and os.path.isfile(os.path.join(path, quick.RECORD)):
            seen.add(real)
            found.append(path)
    return found


# --- the agent's side ----------------------------------------------------------

def ask(cwd: str, text: str, *, now: Callable[[], float] = time.time,
        say: Callable[[str], None] = print) -> int:
    """`rein quick ask "<question>"`: 0 recorded, 2 no question or no quick task here."""
    if not text.strip():
        say("rein quick ask needs the question")
        return 2
    workspace = workspace_of(cwd)
    record, why = qd._load_record(workspace) if workspace else (None, "not inside a git checkout")
    if record is None:
        say(why)
        return 2
    asked = questions(record)
    qid = "q%d" % (len(asked) + 1)
    record["questions"] = asked + [{"id": qid, "text": text.strip(), "asked_at": now(), "answer": None,
                                    "answered_at": None}]
    save(workspace, record)
    say("pregunta %s registrada: el orquestador avisa al dueño. Detente y espera su respuesta, que llegará "
        "a esta terminal; no sigas con lo que dependa de ella." % qid)
    return 0


# --- the orchestrator's side ---------------------------------------------------

def answer(arg: str, text: str, client: OrcaClient, *, cwd: str = ".", qid: str = "",
           now: Callable[[], float] = time.time, say: Callable[[str], None] = print) -> int:
    """`rein quick answer [<workspace>] "<text>"`: 0 delivered, 1 nothing to answer, 2 usage,
    126 recorded but the agent's terminal could not be reached."""
    if not text.strip():
        say("rein quick answer needs the answer text")
        return 2
    found = [p for p in targets(arg, client, cwd) if open_questions(qd._load_record(p)[0] or {})]
    if not found:
        say("no open question%s" % (" in %s" % arg if arg else " in any quick task"))
        return 1
    if len(found) > 1:
        say("several quick tasks have open questions; name one: %s" % ", ".join(found))
        return 2
    workspace = found[0]
    record, _ = qd._load_record(workspace)
    pending = open_questions(record)
    chosen = next((q for q in pending if q.get("id") == qid), None) if qid else pending[0]
    if chosen is None:
        say("no open question %r in %s; open: %s" % (qid, workspace, ", ".join(q["id"] for q in pending)))
        return 2
    chosen["answer"], chosen["answered_at"] = text.strip(), now()
    save(workspace, record)
    handle = str(record.get("terminal") or "")
    message = "Respuesta del dueño a tu pregunta %s (%s): %s" % (chosen["id"], chosen.get("text"), text.strip())
    if not handle:
        say("answered %s in %s, but the start recorded no terminal: tell the agent yourself:\n%s"
            % (chosen["id"], workspace, message))
        return 126
    try:
        client.terminal_send(handle, message, enter=True)
    except OrcaError as exc:
        say("answered %s in %s, but orca could not type it into %s (%s): tell the agent yourself:\n%s"
            % (chosen["id"], workspace, handle, exc.message, message))
        return 126
    say("answered %s in %s (sent to %s)" % (chosen["id"], workspace, handle))
    return 0


def _announce(workspace: str, q: dict) -> None:
    print("QUESTION %s %s" % (workspace, q.get("id")))
    print("question: %s" % q.get("text"))
    print('answer with: rein quick answer %s "<text>"' % workspace)


def _poll(arg: str, client: OrcaClient, cwd: str, printed: set) -> bool:
    shown = False
    for workspace in targets(arg, client, cwd):
        record, _ = qd._load_record(workspace)
        for q in open_questions(record or {}):
            key = (os.path.realpath(workspace), q.get("id"))
            if key in printed:
                continue
            printed.add(key)
            _announce(workspace, q)
            shown = True
    return shown


def watch(arg: str, client: OrcaClient, *, cwd: str = ".", once: bool = False,
          sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.time) -> int:
    """`rein quick watch [<workspace>]`: block until a quick task needs the owner, print it, exit 0.

    Every open question not yet printed by this watch is printed. `once` polls a single time
    and exits 1 when nothing is pending."""
    printed = set()
    while True:
        if _poll(arg, client, cwd, printed):
            return 0
        if once:
            return 1
        sleep(WATCH_POLL_SECONDS)
