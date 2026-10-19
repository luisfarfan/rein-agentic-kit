"""`rein answer`: show and resolve the owner gates of a `rein ship` run in plain language.

Read-only on the ship state: the running driver notices a resolution by polling
`gate-list`, so this module only asks Orca. It reads `<root>/.rein/ship/*.json`
as plain JSON (never through `ship_state.load`, which quarantines files it
cannot parse) and resolves a gate as the run's coordinator terminal, which is
what `gate-resolve --from` requires when the caller is another terminal.
"""

from __future__ import annotations

import glob
import json
import os
from typing import Optional

import orca_client as oc
import ship_state as ss
from orca_client import OrcaClient, OrcaError

QUESTION_LIMIT = 500


def _read_state(path: str) -> Optional[dict]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _state_files(root: str) -> list:
    """`(change, state)` for every readable state file under `<root>/.rein/ship/`."""
    found = []
    for path in sorted(glob.glob(os.path.join(root, ".rein", "ship", "*.json"))):
        data = _read_state(path)
        if data is not None:
            found.append((str(data.get("change") or os.path.basename(path)[:-5]), data))
    return found


def _open_gates(state: dict, client: OrcaClient) -> list:
    """The pending gates this run opened, each merged with what the driver recorded about it."""
    mine = state.get("gates") if isinstance(state.get("gates"), dict) else {}
    if not mine:
        return []
    listed = oc.gates_of(client.gate_list(str(state.get("run_id") or "")))
    out = []
    for g in listed:
        if g.get("id") in mine and g.get("status") == "pending":
            meta = mine[g["id"]] if isinstance(mine[g["id"]], dict) else {}
            out.append({**g, "kind": str(meta.get("kind") or "?"), "task": meta.get("task_id") or "-"})
    return out


def _show(gates: list) -> None:
    for g in gates:
        question = str(g.get("question") or "")
        if len(question) > QUESTION_LIMIT:
            question = question[:QUESTION_LIMIT] + "..."
        print("%s (%s) task %s" % (g["id"], g["kind"], g["task"]))
        print("  question: %s" % question)
        print("  options: %s" % (", ".join(map(str, g["options"])) if g["options"] else "(free text)"))


def _pick_change(root: str, change: str, client: OrcaClient):
    """`(change, state, gates, code)`; `code` is None when a single change was settled on."""
    files = _state_files(root)
    if change:
        state = next((s for c, s in files if c == change), None)
        if state is None:
            state = _read_state(ss.state_path(root, change))
        if state is None:
            print("no ship run for %r under %s" % (change, root))
            return change, None, [], 1
        return change, state, _open_gates(state, client), None
    withgates = [(c, s, g) for c, s in files for g in [_open_gates(s, client)] if g]
    if not withgates:
        print("no questions pending")
        return "", None, [], 0
    if len(withgates) > 1:
        print("several changes have questions pending; pass the change name:")
        for c, _, g in withgates:
            print("  %s (%d open)" % (c, len(g)))
        return "", None, [], 2
    chosen = withgates[0]
    return chosen[0], chosen[1], chosen[2], None


def run_answer(root: str, change: str, text: Optional[str], gate_id: str, client: OrcaClient) -> int:
    """List the open gates (`text` empty) or resolve one with `text`. Exit 0 / 1 failure / 2 usage."""
    root = os.path.abspath(root)
    try:
        change, state, gates, code = _pick_change(root, change, client)
        if code is not None:
            return code
        if not text:
            if gates:
                _show(gates)
            else:
                print("no questions pending")
            return 0
        if not gates:
            print("no questions pending for %s; nothing to answer" % change)
            return 1
        if gate_id:
            chosen = next((g for g in gates if g["id"] == gate_id), None)
            if chosen is None:
                print("no open gate %r; open gates: %s" % (gate_id, ", ".join(g["id"] for g in gates)))
                return 2
        elif len(gates) > 1:
            print("several questions are open; pass --gate <id>:")
            _show(gates)
            return 2
        else:
            chosen = gates[0]
        resolution = text.strip()
        options = [str(o) for o in chosen["options"]]
        if options:
            match = next((o for o in options if o.lower() == resolution.lower()), None)
            if match is None:
                print("%r is not one of the options for %s: %s" % (text, chosen["id"], ", ".join(options)))
                return 2
            resolution = match
        run_id = str(state.get("run_id") or "")
        if not run_id:
            print("the ship state for %s has no run id yet; nothing to answer" % change)
            return 1
        coordinator = oc.coordinator_of(client.run_show(run_id))
        if not coordinator:
            print("Orca reports no coordinator terminal for run %s; cannot resolve as it" % run_id)
            return 1
        client.gate_resolve(chosen["id"], resolution, coordinator)
        print("answered %s (%s)" % (chosen["id"], chosen["kind"]))
        return 0
    except OrcaError as exc:
        print("orca error %s: %s" % (exc.code, exc.message))
        return 1
