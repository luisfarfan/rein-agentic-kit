---
name: loop
description: "Run the Rein change loop on a planned change: bounded fresh-agent implementation steps, then an independent review gate, driven by this project's flow.config.json. PHASE 0: resolves config and stack only. Use when the user asks to execute/implement an already-planned change with /rein:loop."
license: MIT
---

# /rein:loop

Executes a planned change as a **bounded loop of fresh, short agents** rather than
one long-running agent.

## Why it is shaped this way

Measured on real transcripts: ~90% of an agent run's token spend is `cache_read` —
every turn re-reads the entire accumulated context, so **cost ≈ turns × context
size**. Output is ~0.3%. A single agent that ran 241 turns re-reading ~234k of
context each turn accounted for most of a 112M-token run.

Claude Code has **no native eviction of old tool results** (only `/compact`, which
is lossy and breaks the cache), so the context of a long agent only grows. The fix
is structural: cut the agent at a boundary and hand the next one a **compact
ledger** (`progress` / `remaining` / `filesTouched` / `verification`) instead of a
transcript. Context resets at every boundary; spend stops growing without a ceiling.

Result of applying this: 241 turns → 26, Opus usage 100% → 0%, ~7× less context per
turn.

## Status: PHASE 0 STUB

The workflow currently **resolves configuration and stack, and implements nothing**.
It exists to verify the plumbing: that a plugin-shipped workflow resolves and runs,
and that it reads the *consuming* project's config.

## Steps

1. Resolve the plugin's install path and this project's setup in one step:

   ```bash
   R=$(command -v rein || ls -d ~/.claude/plugins/cache/*/rein/*/bin/rein 2>/dev/null | tail -1); echo "$R"; "$R" detect
   ```

   Keep `$R` — every later `rein` call uses it. The bare `rein` only exists once
   Claude Code has added the plugin's `bin/` to `$PATH`, which happens at session
   start, so the fallback is the reliable path.

2. Run the workflow **by absolute path**. Workflows are not a plugin component
   type, so `Workflow({name: ...})` cannot see it, and `${CLAUDE_PLUGIN_ROOT}` is
   not interpolated by the Workflow tool. Derive the script path from `$R`
   (`<plugin root>/workflows/loop.js`) and pass it literally:

   ```
   Workflow({ scriptPath: '<plugin root>/workflows/loop.js', args: { ... } })
   ```

3. After the run, record and show the real cost:

   ```bash
   "$R" token-report
   ```

   Call out the three numbers that predict cost: **turns/agent**, **ctx_max/turn**,
   and **% of tokens on Opus**. Do not report a "saving" unless a baseline run has
   been marked — without one, it is a trend, not a saving.

## Guardrails

- This skill does not plan. If there is no plan, stop and point at `/rein:plan`.
- Never report a run as successful based on the workflow's return value alone;
  the token report is the objective evidence.
