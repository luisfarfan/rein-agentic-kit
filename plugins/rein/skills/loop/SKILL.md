---
name: loop
description: "Execute a planned change with the Rein loop: bounded fresh-agent implementation steps, then an independent review gate, driven by this project's flow.config.json. Use when the user asks to implement, execute or run an already-planned change, or invokes /rein:loop."
license: MIT
---

# /rein:loop

Executes a plan as a **bounded loop of fresh, short agents**, then an independent
review gate. Implements only what the plan says; it does not decide scope.

## Why it is shaped this way

Measured on real transcripts: ~90% of an agent run's spend is `cache_read` — every
turn re-reads the whole accumulated context, so **cost ≈ turns × context size**.
Output is ~0.3%. One agent that ran 241 turns re-reading ~234k each turn was most
of a 112M-token run.

Claude Code has **no native eviction of stale tool results**, so a long agent's
context only grows. The fix is structural: cut at a boundary and hand the next
agent a **compact ledger** (`progress` / `remaining` / `filesTouched` /
`verification`) instead of a transcript. Measured effect: **241 turns → 26**, Opus
**100% → 0%**, ~7× less context per turn.

## Steps

1. **Resolve the CLI and the plan** in one call:

   ```bash
   R=$(command -v rein || ls -d ~/.claude/plugins/cache/*/rein/*/bin/rein 2>/dev/null | tail -1); echo "$R"; "$R" context .
   ```

   Keep `$R` — later steps reuse it. If the plan does not exist, stop and point at
   `/rein:plan`. If commands are missing, say which and that they belong in
   `flow.config.json`; do not invent them.

2. **Show the user what will run** before running it: pending tasks in dependency
   order, the resolved verification commands, the model routing, and the caps
   (`maxTaskSteps`, `maxReviewRounds`). For anything non-trivial, pass
   `dryRun: true` first and show that instead.

3. **Run the workflow by absolute path.** Workflows are not a plugin component
   type, so `Workflow({name: ...})` cannot see it, and `${CLAUDE_PLUGIN_ROOT}` is
   not interpolated by the Workflow tool. Derive the script path from `$R`
   (`<plugin root>/workflows/loop.js`) and pass it literally:

   ```
   Workflow({ scriptPath: '<plugin root>/workflows/loop.js', args: { ... } })
   ```

   Useful `args`: `change` (openspec plans), `taskIds` (a subset), `worktree:
   false` (work in place), `autoHumanReview: true` (delegate supervised tasks),
   `dryRun: true`, and `modelAux` / `modelImpl` / `modelReview` overrides.

4. **Report the outcome honestly, then the cost:**

   ```bash
   "$R" token-report
   ```

   Lead with what happened — approved or not, what landed, what is blocked, where
   the worktree is. Then the three numbers that predict cost: **turns/agent**,
   **ctx_max/turn**, **% of tokens on Opus**.

## Guardrails

- Never report success from the workflow's return value alone. `approved: true`
  with a red gate is the exact false green this loop exists to prevent — check
  `gateOutput`.
- Unapproved work is never merged. If the run ends without approval, say where the
  branch is; do not merge it to be helpful.
- If the reviewer returns `needsHumanDecision`, surface it as a question for the
  user. Do not answer it on their behalf.
- Do not report a token "saving" — without a marked baseline these numbers are a
  trend, not a saving.
