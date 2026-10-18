---
name: ship
description: "Implement a planned change: opens a detached Orca tab that runs `rein ship <change>` (workers implement, each task is gated, then an audit). Use when the user says ship, lánzalo, implementa el plan, or invokes /ship."
license: MIT
---

# /ship

Hands a planned change to `rein ship` in its own Orca tab. Reachable as `/ship` (`/rein:ship`
when namespaced). **Never edits code or plans, pushes, merges or opens a PR.**

Usage: `/ship <change> [--root <plan repo>]`

## Steps

1. Confirm the plan exists: `rein tasks <root> --change <change>` (root `.` or the user's
   `--root`). None: stop, suggest `/rein:rein-plan`.
2. Run `rein ship <change> --root <root> --detach`. It opens the Orca tab
   `rein ship <change>` and returns at once.
3. Report the tab name and tell the user they can close this session. Worker questions arrive
   as Orca decision gates; they answer by asking "¿qué me pregunta rein?" (`/rein:answer`).
4. If it refuses (a tab is already running, or a preflight error), show its output; never
   retry, and never run the loop in the foreground or background of this session.
   `--dry-run` previews and cannot be detached.
