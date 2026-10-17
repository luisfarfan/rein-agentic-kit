---
name: ship
description: "Implement a planned change by running `rein ship <change>` in the current Orca terminal: workers implement, each task is gated, the owner answers Orca decision gates. Use when a plan exists and the user asks to implement or ship it, or invokes /ship."
license: MIT
---

# /ship

Hands a planned change to `rein ship`, which dispatches workers through Orca, gates every task
with `rein gate`, then audits. **This skill never edits code, never plans, and never pushes,
merges or opens a PR.**

Usage: `/ship <change> [--root <plan repo>] [--dry-run]`

## Steps

1. Confirm a plan exists: `rein tasks .`. None: stop and suggest `/rein:rein-plan`.
2. Run it in this Orca terminal and wait:
   ```bash
   rein ship <change>
   ```
   Use `--dry-run` first to see placement and models without spending tokens.
3. Tell the user worker questions arrive as **Orca decision gates**; they answer there.
4. Report the exit code: 0 approved, 1 failed tasks or rejected audit, 2 usage, 126 preflight
   error (not an Orca terminal, or an unresolvable `Repo:`). On failure show what `rein ship`
   printed; never retry or fix code yourself.
