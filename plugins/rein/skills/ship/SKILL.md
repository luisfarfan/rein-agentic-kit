---
name: ship
description: "Implement a planned change: opens a detached Orca tab that runs `rein ship <change>` (workers implement, each task is gated, then an audit). Use when the user says ship, lánzalo, implementa el plan, or invokes /ship."
license: MIT
---

# /ship

Hands a planned change to `rein ship` in its own Orca tab (`/rein:ship` when namespaced), then
keeps the owner in THIS session: Rein's questions come back here.
**Never edits code, pushes, merges or opens a PR.**

Usage: `/ship <change> [--root <plan repo>]`

Agent flag: "con codex" → `--agent codex`; "con antigravity" →
`--agent antigravity`; "con sonnet" / "con claude" → `--agent claude`; `--model` only if named.
It applies to every task.

## Steps

1. Confirm the plan exists: `rein tasks <root> --change <change>` (root `.` or the user's
   `--root`). None: stop, suggest `/rein:rein-plan`.
2. Run `rein ship <change> --root <root> --detach`. If it refuses (a tab is already running, or a
   preflight error), show its output; never retry, and never run the loop in this session.
3. Immediately run `rein watch <change> --root <root>` as a BACKGROUND command. Tell the owner
   to stay in this session: Rein's decision gate questions will appear here.
4. When the watcher wakes, act on its first word:
   - **QUESTION** / **PENDING** (handle ALL listed gates, one by one): read each question in
     full; look briefly (read-only) at the relevant files, diff or logs. Tell the owner in plain
     language what is asked, the options, and your recommendation with one-line reasons. Wait for
     the owner, then run `rein answer <change> "<text>" --gate <id> --root <root>` (option gates:
     exactly one listed option; never invent an answer). On exit 2, re-show the options and ask
     again. Start the watcher again only once every gate is answered or the owner defers.
   - **FINISHED**: summarise the tasks, the audit result and where the work is; suggest
     reviewing the diff and pushing to develop themselves. Do not restart the watcher.
   - **STOPPED**: tell the owner and offer to resume it in the Orca tab "rein ship <change>";
     restart the watcher after.
