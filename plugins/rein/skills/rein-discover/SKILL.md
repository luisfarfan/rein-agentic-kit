---
name: rein-discover
description: "Think a problem through before committing to a shape. Investigate, read, ask — produce understanding, never a plan. Use when the work is not yet clear enough to decompose, or before /rein:rein-plan."
---

# /rein:rein-discover

**A stance, not a workflow.** No fixed steps, no required output, no artifact.
You may read, search, run read-only commands and ask questions. You may not
implement, and you may not write a plan — that is `/rein:rein-plan`, and it
starts only once the unknowns that matter are closed.

## Steps

1. **Record this invocation** — never blocks, never fails. Shell state does NOT
   persist between tool calls, so `R` is resolved and used in the SAME block:
   ```bash
   R=$(command -v rein || ls -d ~/.claude/plugins/cache/*/rein/*/bin/rein 2>/dev/null | sort -V | tail -1); "$R" event rein-discover
   ```

2. **Orient in one round-trip**, not by exploring:
   ```bash
   R=$(command -v rein || ls -d ~/.claude/plugins/cache/*/rein/*/bin/rein 2>/dev/null | sort -V | tail -1); "$R" context .
   ```
   That is the stack, the resolved commands with the source of each, and the
   plan's state. Do not rediscover any of it by hand.

3. **Investigate with the cheap tools first.** This is the phase most likely to
   burn context, because exploring IS the point here — so the retrieval
   discipline matters MORE, not less. Cost is turns × context re-read every
   turn.
   - `codegraph query "<concept>" -p .` — symbols matching a concept, with
     file:line. Then `callers` / `callees` / `impact <symbol>`.
   - Read with offset/limit, only what the question needs. Never whole files
     "to get a feel".
   - `rein verify .` when a claim about the project's commands matters — an
     inference is not a fact.

4. **Say what you do not know.** The output of discovery is not a document; it
   is a shared understanding, and the useful half of it is the unknowns. Name
   them plainly: what is still ambiguous, what has more than one reasonable
   answer, what depends on a decision only the user can make.

5. **Ask.** A question now costs a sentence. The same ambiguity surviving into
   a plan costs a run: in a measured case, a criterion read
   "the description closes with the SAME CTA as the closing segment" and never
   said for which format — the implementer built it against the wrong one, and
   it took a review round and a fix agent to find out.

6. **Hand off when, and only when, it is clear.** Say what became clear, what
   is still open, and suggest `/rein:rein-plan`. Do not write the plan, do not
   draft tasks, do not start.

## What this exists to prevent

A plan whose criteria describe **the shape of an artifact** instead of
something reachable. Measured: a task read "a closed catalogue exists and the
policy lives in configuration". It was satisfied to the letter — the file
existed, it parsed, it had tests — and nothing in it said a real run reads
that file. The parameter was wired half way and nobody noticed until review.

Criteria like that are written when the shape is decided before the problem is
understood. No check catches them afterwards, because each one is individually
true. This is the phase where that is avoided instead.

## Boundaries

- Implement nothing. Not "one small fix while I am here".
- Write no plan and no task list.
- Change no file. If you find something that must change, say so and let the
  user decide when.
