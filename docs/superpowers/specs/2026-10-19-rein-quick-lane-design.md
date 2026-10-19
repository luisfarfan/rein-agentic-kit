# Rein quick lane — design

Date: 2026-10-19. Status: implemented.

## 1. Why

Small changes (a simple bug, a design tweak) do not deserve a plan, but today the owner does
them in an interactive Claude session that runs on Opus. Opus sessions are what drain the Claude
Max 20x pool: 316–622M Claude tokens per feature at 100% Opus, against 0.8–4.5M for a `/ship`
run on Sonnet workers. `/ship` itself is too heavy for a ten-line change: a fresh worktree with
setup, the full suite as the gate (about 6 minutes in proxima-api), and an audit worker.

The quick lane hands a small change to a cheap agent chosen by quota, in a conversation the owner
drives, and closes it with a light, deterministic check.

**What the owner asked for**

- No spec or plan; started from one sentence, with no command to remember.
- Never Opus: Sonnet, Codex or Antigravity, chosen by quota.
- The owner talks with the agent and iterates. It is not fire-and-forget.
- The owner chooses where the change happens: its own worktree (default) or the main checkout,
  and the base branch (`develop` by default, `main` for a hotfix).
- Closing runs a light gate, plus an audit only when the change touches risky paths.

**Success**: a quick task costs a small fraction of an Opus session, the agent is in front of the
owner in seconds when the repo's main checkout is used (and in setup time for a new worktree), and
"listo" gives a verdict from exit codes, not from the agent's impression.

## 2. Out of scope

- Changes spanning two repos. They go through `/rein:rein-plan` and `/ship`; the skill says so
  when the sentence names more than one repo.
- Pushing, merging, opening PRs or deploying. Rein still never does any of these. The agent does
  them only when the owner asks, and asks again before touching `main`.
- Visual verification by the agent. The owner looks at the result; the pilot showed workers
  cannot reliably validate a rendered page.
- Cleaning up quick worktrees after they land.

## 3. Shape

Two commands and one skill. No coordinator, no state machine, no Orca Run.

| Piece | Role |
|---|---|
| `skills/quick/SKILL.md` | Turns the owner's sentence into one `rein quick` call. Triggers: "tarea rápida", "arréglalo rápido", "cambio rápido", `/quick`. |
| `lib/quick.py` | Pure decisions: placement and the Orca command, the brief, related tests, the audit decision, the summary. No subprocess, no Orca. |
| `lib/quick_driver.py` | Calls Orca and the gate runners and reads and writes `.rein/quick.json`. |
| `bin/rein quick …` | `rein quick "<sentence>" …` starts a task; `rein quick done` closes it. |

Reused unchanged: `quota.parse_rate_limits`, `quota.choose_implementer`, `ship.changed_files`,
`verify.run_one`, `quota.first_unbraked`, the `OrcaClient` and the `rein-audit` reviewer
profile. `/ship` is not modified. Two behaviour-preserving additions serve the lane:
`verify.CommandResult.output` (the whole decoded capture, off `to_dict`, so `done` can show the
error lines of a red slot) and `ship_decide.touches_risky` (the risky-path test `audit_model`
already applied, extracted so `done` can ask it too).

## 4. Start: `rein quick`

```
rein quick "<sentence>" --repo <name> [--base <branch>] [--here [--dirty-ok]]
           [--agent <id> [--model <m>]] [--linear <PPR-123>]
```

The skill maps the owner's words to flags: a repo name → `--repo` (default: the repo containing
the current directory), "en main" → `--base main`, "aquí mismo" / "en el checkout" → `--here`,
"con codex" → `--agent codex`, a `PPR-…` id → `--linear`.

Steps, all deterministic:

1. **Repo.** Resolve `--repo` against `orca repo list` by display name; unknown → exit 2 listing
   the known repos.
2. **Model.** `--agent`/`--model` if given; otherwise
   `choose_implementer(limits, ship.claudeWeeklyThreshold)`. When that pick is itself braked
   (every candidate at 90%), exit 1 naming each provider: nothing is launched.
3. **Placement.**
   - Default: `orca worktree create --repo id:<repo> --name quick-<slug> --base-branch <base>
     --agent <a> --model <m> --prompt <brief> --setup run --activate --json` (plus
     `--linear-issue <id>` when given). `<base>` defaults to `develop`. Before creating, it runs
     `git fetch --quiet origin <base>` in the repo's main checkout and passes `origin/<base>` as
     `--base-branch` when that remote ref exists (else `<base>`), so the worktree never starts
     from a stale local branch; a failed fetch (offline, no `origin`) prints one line and falls
     back to the local `<base>`. `<slug>` is the sentence
     lowercased, ASCII-folded, non-alphanumerics to `-`, cut to 30 characters; on a name clash,
     append `-2`, `-3`, ….
   - `--here`: `orca agent launch --worktree path:<repo path> --agent <a> --model <m>
     --prompt <brief> --focus --json` on the main checkout's current branch. A dirty tree refuses
     (exit 2) unless `--dirty-ok`, because the task's diff would mix with the owner's work.
     `--here` with `--base` refuses: the main checkout stays on its branch.
4. **Record.** Write `<workspace>/.rein/quick.json`:
   `{"version": 1, "repo", "workspace", "base", "base_sha", "agent", "model", "sentence",
   "linear", "started_at", "audit_rounds": 0}`. `base` is the branch name asked for (not `origin/<base>`); `base_sha` is the
   merge base at start (for `--here`, `HEAD` at start), so `done` diffs against a fixed point.
   An existing `quick.json` in that workspace refuses (exit 2): one quick task per workspace.
   For a new worktree, the record is written once `orca worktree create` returns its path, before
   anything else.
5. **Report.** Print the agent, model, workspace path and tab, and exit 0.

**The brief** (the agent's first message, in Spanish, kept under about 150 words):

- The task, verbatim.
- "Iteramos juntos: propón, aplica, y espera mi visto bueno antes de seguir."
- When the sentence reads like a bug: "Primero escribe un test que lo reproduzca."
- "Cuando diga *listo*, ejecuta `rein quick done` y muéstrame el resultado."
- "No hagas push, merge ni PR salvo que yo lo pida; si la rama destino es main, pide
  confirmación antes."
- The commit rule: with `--linear`, the id goes in the message; without it, a conventional commit
  with no id, never invented or asked for.
- "No leas `.env*` ni credenciales."

Repo rules are not copied: agents load `AGENTS.md` at startup, which was verified for Antigravity
on 2026-10-19 and is the norm for Claude and Codex.

## 5. Close: `rein quick done`

Run by the agent, in its own workspace, when the owner says "listo".

1. **Load** `.rein/quick.json` from the current workspace (walking up to the git root). None →
   exit 2: "no quick task here; start one with `rein quick`".
2. **Changed files**: `ship.changed_files(workspace, base_sha)`, which unites commits since the
   base, uncommitted edits and untracked files, excluding `.rein/`. None → exit 0 with
   "nothing changed".
3. **Light gate.** Using the repo's `flow.config.json` commands and `ship.gateTimeoutSeconds` as
   each command's timeout:
   - `lint` and `typecheck` when configured.
   - **Related tests**: changed test files, plus existing same-directory siblings of changed
     source files: `foo.ts` → `foo.spec.ts` / `foo.test.ts`; `x.py` → `test_x.py` beside it or
     under `tests/` with the same relative path. Each runs through `testOne` with
     `{target}` substituted.
   - No related tests: the gate does not fail; the summary says "sin tests relacionados".
   - More than 10 related tests, or no `testOne` configured: run the `test` command once (the
     repo's suite, slower) and say so.
   - Verdict: every slot exit 0 → green; any non-zero → red; a missing command, timeout or
     environment failure (the same classification `rein gate` uses) → setup.
   - More changed files than `ship.largeChangeFiles`: a warning in the summary suggesting a plan
     next time. It does not block and does not trigger the audit.
4. **Audit, only for risky changes.** If any changed file matches `ship.riskyPaths` and the
   gate is green:
   - Reviewer: Opus, as `/ship` does for risky paths. This is the lane's only use of Opus. When
     Claude is braked (90% session or weekly), the reviewer is the first unbraked of Codex and
     Sonnet instead (`quota.first_unbraked`); when every one is braked, `done` exits 126 saying
     the audit cannot run.
   - Launched as a supervised Orca worker in the same workspace with the `rein-audit` brief; the
     verdict is recorded with `rein review record` under the workspace, as `/ship` does. The
     agent's Orca terminal is the coordinator (`ORCA_TERMINAL_HANDLE`); without one (for example
     an agent opened as an Orca chat), `done` exits 126 explaining that the audit needs a
     terminal.
   - **A standing approval is not audited again.** Before launching anything, `done` checks the
     workspace's latest `quick-<workspace>` review (`gate.check_review`): when it is APPROVED, every
     file it reviewed still hashes the same, and every risky changed file (`touches_risky` on that
     single file) is one of the files it reviewed, `done` prints "auditoría vigente: aprobada" and
     exits 0 without launching a worker. A risky file added after the approval is not covered, so
     it is audited; a tweak to a non-risky file leaves the approval standing.
   - `done` waits up to 20 minutes. Approved → green. Changes requested → red with the BLOCKING
     findings; `audit_rounds` increments and the BLOCKING lines are kept in the record as
     `last_blocking`. Only CHANGES_REQUESTED spends a round: a timeout, a failed worker, a
     question or an approval does not. After 2 rounds, `done` reports the findings of the last
     CHANGES_REQUESTED review (`last_blocking`) and leaves the decision to the owner (exit 1 with
     that sentence).
   - Timeout or a failed audit worker → setup, with the reason.
5. **Summary.** A few lines: verdict, each slot with its exit code, and for red slots only the
   error lines (lines matching error/FAIL/✗/×/AssertionError, with two lines of context, capped
   at 40 lines). The raw tail of the whole run is not shown: that buried the real error in the
   pilot. A green gate that an audit still has to confirm prints "gate: green — auditoría
   pendiente" in place of "verdict: green", so it is not read as the final word.
6. **Exit codes**: 0 green (the agent commits), 1 red (fix with the owner), 126 setup (tell the
   owner; not a verdict on the code), 2 usage. `done` never commits, pushes or deletes the
   record. The record stays so a second `done` can run after fixes.

## 6. Errors and limits

- Orca unreachable at start → exit 126 with Orca's message. Nothing half-created: the record is
  written only after Orca reports success.
- `orca worktree create` fails after creating the worktree → print the `residualResources` Orca
  returns and exit 126, without a record.
- Every provider braked → exit 1, nothing launched.
- Non-git workspace, or `base_sha` no longer reachable → exit 126 naming the workspace.
- Parallel quick tasks: one per workspace (enforced by the record). Several worktrees can each
  hold one.
- `.rein/` must not be committed: `rein quick` checks that `.rein/` is ignored in the workspace
  (`git check-ignore`) and, when it is not, prints the line to add to `.git/info/exclude` without
  writing it, and continues.

## 7. Testing

`unittest`, stdlib only, Python ≥3.9, like the rest of Rein.

- `quick.py` (pure): slug and clash suffixes; flags → Orca argv for the default, `--base main`,
  `--here`, `--linear` and `--agent` cases; the `--here` + `--base` and dirty refusals; the brief
  (bug vs. tweak, with and without Linear id, word budget); related tests for TypeScript and
  Python layouts, the cap of 10 and the missing-`testOne` fallback; the audit decision; the
  summary's error-line extraction; the reviewer choice (Opus, braked
  Claude → Codex/Sonnet, all braked → setup).
- `quick_driver.py`: with the existing `FakeOrca` and fixtures, the exact `worktree create` and
  `agent launch` argv, the record written after success only, `done` against a temporary git
  repo (green, red, setup, nothing changed, no record), and the audit path through a fake worker
  verdict.
- Live check before calling it done: one small real quick task in proxima-admin, in a worktree,
  on whichever model quota picks, up to the commit, without pushing.

## 8. Decisions

- **D1** Interactive agent, not a supervised worker: the owner iterates by talking. This is why
  the lane does not reuse `/ship`'s state machine.
- **D2** Default placement is a new worktree from `develop`; `--here` and `--base` are explicit
  choices made in words.
- **D3** The implementer is chosen in code (`choose_implementer`) and is never Opus unless the
  owner names it.
- **D4** The gate is light (lint, typecheck, related tests); the full suite runs only as a
  fallback.
- **D5** Audit only for risky paths, on Opus (Codex or Sonnet when Claude is braked);
  otherwise the owner reviews the diff. A large change only warns.
- **D6** Rein never commits or pushes in this lane; the agent commits after a green `done`, and
  pushes only on the owner's request.
