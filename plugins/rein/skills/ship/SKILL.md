---
name: ship
description: "Implement a planned change by running `rein ship <change>` in the current Orca terminal: workers implement, each task is gated, the owner answers Orca decision gates. Use when a plan exists and the user asks to implement or ship it, or invokes /ship."
license: MIT
---

# /ship

Hands a planned change to `rein ship`, which dispatches Orca workers, gates each task, then
audits. Reachable as `/ship` (`/rein:ship` when namespaced). **Never
edits code, plans, pushes, merges or opens a PR.**

Usage: `/ship <change> [--root <plan repo>] [--dry-run]`

## Steps

1. Confirm a plan: `rein tasks <root> --change <change>` (root `.` or the user's `--root`).
   None: stop, suggest `/rein:rein-plan`.
2. Run `rein ship <change> [--root <root>]` in the background (a long foreground call times
   out), or ask the user to run it in their Orca terminal; report progress from its output.
   Never re-run it while live; re-running after it exits resumes. `--dry-run` previews.
3. Tell the user worker questions arrive as **Orca decision gates**; they answer there.
4. Report the exit code: 0 approved, 1 failed tasks or rejected audit, 2 usage, 126 preflight
   error. On failure show its output; never retry or fix code yourself.
