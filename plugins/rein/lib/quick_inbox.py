"""A quick task's line to the orchestrator: questions, answers and the watch that relays them.

`rein quick` hands a task to an agent in its own tab and returns; nobody supervises it the
way the ship driver supervises a worker. So the agent asks through Rein, never only in its
terminal: `rein quick ask` records the question in `.rein/quick.json` (the one file
`rein quick close` deletes), the orchestrator's `rein quick watch` prints it, and
`rein quick answer` records the owner's answer and types it into the agent's terminal.

The watch also notices an agent that never started: no commit, no edit and no question
`ship.stallMinutes` (10) after its launch -- typically a startup dialog (a trust or
hooks-review prompt) that swallowed its brief. It says so once per window; `rein quick
resend` types the brief again once the dialog is gone.
"""

from __future__ import annotations

import json
import os
import time
from typing import Callable

import detect as _detect
import gate_env as _gate_env
import orca_client as oc
import quick
import quick_driver as qd
import ship_decide as _sd
from orca_client import OrcaClient, OrcaError

WATCH_POLL_SECONDS = 10


# --- the record ----------------------------------------------------------------

def workspace_of(cwd: str) -> str:
    code, top = qd.run_git(cwd or ".", "rev-parse", "--show-toplevel")
    return top.strip() if code == 0 else ""


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
    if not workspace:
        say("not inside a git checkout")
        return 2
    added = {}

    def add(record):
        asked = questions(record)
        added["id"] = "q%d" % (len(asked) + 1)
        record["questions"] = asked + [{"id": added["id"], "text": text.strip(), "asked_at": now(),
                                        "answer": None, "answered_at": None}]
    record, why = qd.update_record(workspace, add)
    if record is None:
        say(why)
        return 2
    qid = added["id"]
    say("pregunta %s registrada: el orquestador avisa al dueño. Detente y espera su respuesta, que llegará "
        "a esta terminal; no sigas con lo que dependa de ella." % qid)
    return 0


REPORT_FILE = ".rein/quick-report.md"
REPORT_CAP = 20000  # characters kept of one report


def reports(record: dict) -> list:
    found = record.get("reports")
    return [r for r in found if isinstance(r, dict)] if isinstance(found, list) else []


def report(cwd: str, text: str, *, now: Callable[[], float] = time.time,
           say: Callable[[str], None] = print) -> int:
    """`rein quick report "<text or file>"`: the agent's final report, as text. A path to a file
    reads that file. Kept in the record (`rein quick watch` prints it) and in `.rein/quick-report.md`,
    so nobody has to read the agent's screen. 0 recorded, 2 nothing to report or no quick task here."""
    text = str(text or "")
    if os.path.isfile(text.strip()):
        try:
            with open(text.strip(), encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            say("cannot read %s: %s" % (text.strip(), exc))
            return 2
    text = text.strip()[:REPORT_CAP]
    if not text:
        say("rein quick report needs the report text (or the path of a file that holds it)")
        return 2
    workspace = workspace_of(cwd)
    if not workspace:
        say("not inside a git checkout")
        return 2
    added = {}

    def add(record):
        added["id"] = "r%d" % (len(reports(record)) + 1)
        record["reports"] = reports(record) + [{"id": added["id"], "text": text, "at": now()}]
    record, why = qd.update_record(workspace, add)
    if record is None:
        say(why)
        return 2
    try:
        with open(os.path.join(workspace, REPORT_FILE), "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    except OSError as exc:  # the record has it: the file is a convenience
        say("could not write %s: %s" % (REPORT_FILE, exc))
    say("informe %s registrado: el orquestador lo lee con `rein quick watch`." % added["id"])
    return 0


# --- the orchestrator's side ---------------------------------------------------

def allow_config(arg: str, paths: str, client: OrcaClient, *, cwd: str = ".",
                 say: Callable[[str], None] = print) -> int:
    """`rein quick allow-config <workspace> <path,...>`: let a quick task already running change those
    gate-config files, like `--allow-config` at its start. Updates the record (the guard reads it when
    `rein quick done` runs) and tells the agent in its terminal. 0 done, 1 no quick task, 2 usage or
    ambiguous, 126 recorded but the agent's terminal could not be reached."""
    granted = qd._guard.declared_paths(paths)
    if not granted:
        say("rein quick allow-config needs the config paths to allow, e.g. flow.config.json,mise.toml")
        return 2
    found = targets(arg, client, cwd)
    if not found:
        say("no quick task%s" % (" for %r" % arg if arg else " here"))
        return 1
    if len(found) > 1:
        say("several quick tasks; name one: %s" % ", ".join(found))
        return 2
    workspace = found[0]

    def merge(record):
        have = qd._guard.declared_paths(record.get("allow_config") or [])
        record["allow_config"] = have + [p for p in granted if p not in have]
    record, why = qd.update_record(workspace, merge)
    if record is None:
        say(why)
        return 1
    message = ("El dueño autoriza ahora cambiar estos archivos de config en esta tarea: %s "
               "(--allow-config). Ya no hace falta preguntar por ellos; cualquier otro archivo de "
               "config sigue prohibido." % ", ".join(granted))
    handle = str(record.get("terminal") or "")
    if not handle:
        say("allowed %s in %s, but the start recorded no terminal: tell the agent yourself:\n%s"
            % (", ".join(granted), workspace, message))
        return 126
    try:
        client.terminal_send(handle, message, enter=True)
    except OrcaError as exc:
        say("allowed %s in %s, but orca could not type it into %s (%s): tell the agent yourself:\n%s"
            % (", ".join(granted), workspace, handle, exc.message, message))
        return 126
    say("allowed %s in %s (the agent was told in %s)" % (", ".join(granted), workspace, handle))
    return 0

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
    picked, pending_ids = {}, []

    def mark(record):
        pending = open_questions(record)
        pending_ids[:] = [q.get("id") for q in pending]
        chosen = next((q for q in pending if q.get("id") == qid), None) if qid else (pending or [None])[0]
        if chosen is None:
            return False
        chosen["answer"], chosen["answered_at"] = text.strip(), now()
        picked["q"] = chosen
    record, why = qd.update_record(workspace, mark)
    if record is None or "q" not in picked:
        say(why or "no open question %r in %s; open: %s" % (qid, workspace, ", ".join(map(str, pending_ids))))
        return 2 if record is not None and pending_ids else 1
    chosen = picked["q"]
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


def _brief_of(workspace: str, record: dict) -> str:
    """The brief `rein quick` sent; rebuilt from the record when it predates keeping it."""
    if isinstance(record.get("brief"), str) and record["brief"].strip():
        return record["brief"]
    sentence = str(record.get("sentence") or "")
    env = _gate_env.export_lines(_gate_env.for_worktree(workspace, repo=str(record.get("repo") or "")))
    return quick.build_brief(sentence, str(record.get("linear") or ""), quick.looks_like_bug(sentence),
                             qd.ship.BIN, env, list(record.get("allow_config") or []))


def _agent_terminal(workspace: str, record: dict, client: OrcaClient) -> tuple:
    """`(handle, "")`, or `("", why)`: the recorded handle, else the workspace's only agent tab."""
    if record.get("terminal"):
        return str(record["terminal"]), ""
    try:
        rows = oc.terminals_of(client.terminal_list("path:" + workspace))
    except OrcaError as exc:
        return "", "orca cannot list the terminals of %s: %s" % (workspace, exc.message)
    here = os.path.realpath(workspace)
    found = [str(t["handle"]) for t in rows if t.get("handle")
             and os.path.realpath(str(t.get("worktreePath") or workspace)) == here
             and t.get("title") != oc.SETUP_TAB_TITLE and not str(t.get("title") or "").startswith("rein ")]
    if len(found) == 1:
        return found[0], ""
    return "", ("%s has %s; name the agent's with --terminal <handle>"
                % (workspace, "tabs %s" % ", ".join(found) if found else "no agent tab"))


def resend(arg: str, client: OrcaClient, *, cwd: str = ".", terminal: str = "",
           now: Callable[[], float] = time.time, say: Callable[[str], None] = print) -> int:
    """`rein quick resend [<workspace>] [--terminal <handle>]`: type the task's brief into the agent's
    terminal again (one lost behind a startup dialog). 0 sent, 1 no quick task, 2 ambiguous, 126 Orca."""
    found = targets(arg, client, cwd)
    if not found:
        say("no quick task%s" % (" for %r" % arg if arg else " here"))
        return 1
    if len(found) > 1:
        say("several quick tasks; name one: %s" % ", ".join(found))
        return 2
    workspace = found[0]
    record, why = qd._load_record(workspace)
    if record is None:
        say(why)
        return 1
    handle, why = (terminal, "") if terminal else _agent_terminal(workspace, record, client)
    if not handle:
        say(why)
        return 2
    try:
        client.terminal_send(handle, _brief_of(workspace, record), enter=True)
    except OrcaError as exc:
        say("orca could not type the brief into %s: %s" % (handle, exc.message))
        return 126
    def stamp(fresh):
        fresh["resent_at"] = now()
        if terminal:
            fresh["terminal"] = terminal
    qd.update_record(workspace, stamp)
    say("brief re-sent to %s (%s)" % (handle, workspace))
    return 0


def _announce_report(workspace: str, r: dict) -> None:
    print("REPORT %s %s" % (workspace, r.get("id")))
    print(str(r.get("text") or ""))


def _announce(workspace: str, q: dict) -> None:
    print("QUESTION %s %s" % (workspace, q.get("id")))
    print("question: %s" % q.get("text"))
    print('answer with: rein quick answer %s "<text>"' % workspace)


def stall(workspace: str, record: dict, now: float) -> str:
    """The alert for an agent with no sign of work past the stall window, else "".

    A sign of work (HEAD moved past the base, an edit, a question) marks the record `active`
    for good; an alert is given once per window, counted from the launch, the last alert or
    the last `rein quick resend`. Decided on the record as it is under the lock: `record` (what
    the caller read) only spares the lock when it already says `active`."""
    if record.get("active"):
        return ""
    found = {}

    def check(fresh):
        found["alert"] = _stall_of(workspace, fresh, now)
        return found["alert"] is not None
    qd.update_record(workspace, check)
    return found.get("alert") or ""


def _stall_of(workspace: str, record: dict, now: float):
    """The alert ("" when one is marked active), or None when nothing changes in the record."""
    if record.get("active"):
        return None
    base = str(record.get("base_sha") or "")
    code, head = qd.run_git(workspace, "rev-parse", "HEAD")
    code2, porcelain = qd.run_git(workspace, "status", "--porcelain", "--untracked-files=all")
    if (code == 0 and base and head.strip() != base) or (code2 == 0 and qd._is_dirty(porcelain)) \
            or questions(record):
        record["active"] = True
        return ""
    minutes = _sd.ShipConfig.from_flow_config(_detect.resolve(workspace)).stall_minutes
    since = max(float(record.get(k) or 0) for k in ("started_at", "stall_alerted_at", "resent_at"))
    if not minutes or minutes <= 0 or now - since < minutes * 60:
        return None
    record["stall_alerted_at"] = now
    return ("STALLED %s: the agent (%s/%s) has shown no sign of work for %g minutes (no commit, no edit, "
            "no question). It may be stuck on a startup dialog (a trust or hooks-review prompt) that "
            "swallowed its brief: look at its tab %s, clear the dialog, then: rein quick resend %s"
            % (workspace, record.get("agent") or "?", record.get("model") or "?", minutes,
               record.get("terminal") or "(unknown)", workspace))


def _poll(arg: str, client: OrcaClient, cwd: str, printed: set, now: float = 0.0) -> bool:
    shown = False
    for workspace in targets(arg, client, cwd):
        record, _ = qd._load_record(workspace)
        alert = stall(workspace, record, now) if record is not None and now else ""
        if alert:
            print(alert)
            shown = True
        for q in open_questions(record or {}):
            key = (os.path.realpath(workspace), q.get("id"))
            if key in printed:
                continue
            printed.add(key)
            _announce(workspace, q)
            shown = True
        for r in reports(record or {}):
            key = (os.path.realpath(workspace), "report", r.get("id"))
            if key in printed:
                continue
            printed.add(key)
            _announce_report(workspace, r)
            shown = True
    return shown


def watch(arg: str, client: OrcaClient, *, cwd: str = ".", once: bool = False,
          sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.time) -> int:
    """`rein quick watch [<workspace>]`: block until a quick task needs the owner, print it, exit 0.

    Every open question not yet printed by this watch is printed, and an agent that shows no sign
    of work past the stall window (STALLED). `once` polls a single time and exits 1 when nothing
    is pending."""
    printed = set()
    while True:
        if _poll(arg, client, cwd, printed, clock()):
            return 0
        if once:
            return 1
        sleep(WATCH_POLL_SECONDS)
