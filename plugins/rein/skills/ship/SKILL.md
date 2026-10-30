---
name: ship
description: "Implement a planned change: opens a detached Orca tab that runs `rein ship <change>` (workers, task gates, an audit). Use only when orchestration pays (parallel tasks, several repos, dependencies to recover, a long run needing saved state), not for a sequential plan one session can do; when the user says ship, lánzalo, implementa el plan, or /ship."
license: MIT
---

# /ship

Hands a planned change to `rein ship` in its own Orca tab (`/rein:ship` when namespaced), then
keeps the owner here.
**Never edits code, pushes, merges or opens a PR.**

Usage: `/ship <change> [--root <plan repo>]`

Agent flag: "con codex" → `--agent codex`; "con antigravity" →
`--agent antigravity`; "con sonnet" / "con claude" → `--agent claude`; `--model` only if named.

## Steps

1. Confirm the plan exists: `rein tasks <root> --change <change>` (root `.` or the user's
   `--root`). None: stop, suggest `/rein:rein-plan`.
2. Run `rein ship <change> --root <root> --detach`. If it refuses (a running tab, a preflight
   error, a temporary plan root), show its output; never retry, and never run the loop in this session. Only the owner
   may accept the temporary root: then add `--allow-temp-root`.
3. Immediately run `rein watch <change> --root <root>` as a BACKGROUND command. Tell the owner
   to stay in this session: Rein's decision gate questions will appear here.
4. When the watcher wakes, act on its first word:
   - **QUESTION** / **PENDING** (handle ALL listed gates, one by one): read each question in
     full; look briefly (read-only) at the relevant files or logs. Tell the owner in plain language
     what is asked, the options, and a recommendation with one-line reasons. Wait for
     the owner, then run `rein answer <change> "<text>" --gate <id> --root <root>` (option gates: one listed
     option; never invent an answer). On exit 2, re-show the options, ask again. Start the watcher again once every gate is answered or deferred; for deferred ones use
     `--skip <ids>` and say they can answer later with "¿qué me pregunta rein?".
   - **FINISHED**: summarise tasks, audit result and where the work is. An audit other than
     `approved` comes first: its blocking findings were not verified. Then offer: push to
     develop (only with the owner's yes: api first, then admin, respecting the hooks), and after
     that close it with `rein done <change> --root <root>` (`--dry-run` previews); ask whether to also
     push the plan-archive commit: on yes, `rein done <change> --root <root> --push` (on main/master/qa
     it never pushes: show the owner the push it prints). On blockers
     show them and stop. If it WARNs about gitignored files (.env ...), show the list and add `--yes` only after an explicit yes.
     Do not restart the watcher.
   - **STOPPED**: tell the owner and offer to resume it in the Orca tab "rein ship <change>";
     restart the watcher after.
