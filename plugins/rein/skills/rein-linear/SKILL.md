---
name: rein-linear
description: "Bring issues in from the Linear board — filtered by repo, priority, flow or state — and hand one to the flow. Reads Linear and nothing else; never implements. Use when the user asks what to work on, wants the board filtered, or names an issue to pick up."
---

# /rein:rein-linear

**Linear is the source.** Not a mirror of it, not a document in another repo,
not a cached copy. Everything below reads the live board through `rein linear`,
which is a script because this is a *parse* — the judgement starts later, in
`/rein:rein-plan`.

This skill selects and reports. It does not implement, and it does not write a
plan.

## Steps

1. **Record this invocation** — never blocks, never fails. Shell state does NOT
   persist between tool calls, so `R` is resolved and used in the SAME block:
   ```bash
   R=$(command -v rein || ls -d ~/.claude/plugins/cache/*/rein/*/bin/rein 2>/dev/null | sort -V | tail -1); "$R" event rein-linear
   ```

2. **Check the key is there before anything else.** Every command here needs
   `REIN_LINEAR_API_KEY`. Without it the CLI exits 1 naming the variable — pass
   that message on rather than paraphrasing it, and stop. It is the user's to
   set, not yours.

3. **Read the board, filtered.** One round trip, never a loop over issues:
   ```bash
   R=$(command -v rein || ls -d ~/.claude/plugins/cache/*/rein/*/bin/rein 2>/dev/null | sort -V | tail -1)
   "$R" linear list --repo proxima-api --max-priority 3 --state Backlog
   ```

   | flag | selects |
   |---|---|
   | `--repo` | the owning repository, from the issue's label |
   | `--priority N` | exactly that level |
   | `--max-priority N` | **at least** that urgent (lower is more urgent) |
   | `--state` | `Backlog`, `Todo`, `In Progress`, `Done`, `Canceled` |
   | `--flow` | a journey id, e.g. `D10` |
   | `--label` / `--parent` | any label; a parent issue's identifier |
   | `--groupers` | include the index cards, normally hidden |
   | `--json` | the parsed records, for a script |

4. **Read one issue in full** before proposing anything about it:
   ```bash
   "$R" linear show PPR-86
   ```

5. **Report what the board says, not what you infer from it.** Give the user the
   issues and their real fields. If they asked "what should I work on", order is
   already urgency-first — say what is at the top and why, and stop there.

## What this skill will not do

- **It will not implement.** Picking an issue up is `/rein:rein-plan`, then
  `/rein:rein-apply`. Handing over means naming the issue and its owning repo,
  not opening files.
- **It will not read `proxima-qa`, or any repo, for issue content.** If the
  board is missing something, the board is what gets fixed. A second source
  that can be stale is how two answers to one question start.
- **It will not invent an identifier.** Every id it reports came from a command
  that ran in this session.

## Reading the output honestly

**`priority` of `none` is not urgent.** Linear numbers `1 urgent … 4 low` and
reserves `0` for "nobody rated this". The list already sorts unprioritised
last; do not re-read it as most important because the number is smallest.

**An empty result has two causes and the CLI tells you which.** A line naming
an unknown filter value ("no issue has repo 'proxima-app'") means the *filter*
is wrong. Silence before "no issues match" means the filter is right and there
is genuinely nothing. Never report the first as "you have no work".

**A `WARNING` line means two sources disagree** about which repo owns the bug —
its label and its body. Do not choose. Surface it: routing a fix on the wrong
one lands it in the wrong repository, and that is expensive to discover later.

**A `MISSING` line means the issue is malformed**, not that the field is empty.
Say which field, and let the user decide whether to fix the board or proceed.
