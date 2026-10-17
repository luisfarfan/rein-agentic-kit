# Rein v2 — a deterministic method on top of Orca

Date: 2026-10-17 · Status: design, awaiting review · Branch: `v2/orca-ship`

## 1. Why

Rein's thesis was right and measured: cost ≈ turns × context size, ~90% of spend is
`cache_read`, and fresh short-context agents on cheaper models cut it sharply. In practice
the part that saves money is barely used, and the spend it was built to remove is still there.

Measured on the owner's machine (Claude transcripts, PROXIMA + forma, last 14 days):

| | Tokens | Opus share |
|---|---:|---:|
| Main sessions | 40.7B (78%) | 99.3% |
| Subagents (366) | 11.5B (22%) | 97.1% |
| Total | 52.2B | 98.7% |

- Median peak context per session: 417k tokens; 50 of 115 sessions exceeded 500k.
- Last 30 days: `rein-plan` ran in ~12–22 sessions, `rein-apply`/`rein-step(s)` in ~5,
  `rein-audit` in 1; Claude Code's `/goal` ran in 47. Plans are made with Rein, executed without it.
- Claude weekly quota was at 77% while Codex sat at 15% with 2 sessions in 14 days.

Claude Code's own subagents do not fix this: they inherit Opus unless told otherwise, and the
growing main session (78% of tokens) is untouched by delegation.

Meanwhile Orca (1.4.227) now provides natively what `workflows/loop.js` reimplemented:
worktrees with setup hooks, fresh agent terminals for Claude *and* Codex with per-worker
`--model`/`--effort`, supervised orchestration (Run, Task DAG, Dispatch, `worker_done`,
ask/reply, decision gates), and quota readings per provider. `loop.js` also locks execution
to Claude Code's `Workflow` tool, which cannot use Codex.

## 2. Goal and success criteria

Audience: the owner and PROXIMA only. Not a public product; no compatibility promises.

Goal #1: spend less quota, provably.

A medium PROXIMA feature built with v2 counts as success when:

1. Claude tokens ≤ 30% of the baseline (section 9).
2. Opus share ≤ 20% of Claude tokens.
3. Every task's gate is green and the final audit is APPROVED.
4. The owner's interventions are limited to decision gates and the final review.

## 3. Lifecycle

```text
 ① DISCOVER       ② PLAN              ③ SHIP                 ④ AUDIT + PR
 rein-discover → rein-plan        → rein ship <change>  → rein-audit → owner opens PR
 (reads, asks,   (decides Repo per   (Python, no LLM:      (other model, 1 round;
  never edits)    task; writes plan   dispatches Orca        owner approves)
                  where it belongs)   workers, gates each)
 └──── one short Claude session ────┘ └──── owner only answers gates / questions ────┘
```

- Scope unknown (the normal case for a feature): start the discover/plan session in
  `proxima-engineering`, which reads sibling repos without editing them.
- Scope known to be one repo: start in that repo.
- Step ② decides where the plan lives: a single-repo plan goes in that repo's
  `openspec/changes/<c>/`; a multi-repo plan goes in
  `proxima-engineering/openspec/changes/<c>/`.
- The discover/plan session ends when the plan is written. It never continues into
  implementation. `rein-discover` investigates with `codegraph query` and offset reads and
  delegates bulk reading to cheap read-only workers when the scope is large.

## 4. Architecture

```text
 /ship <c>  (≈20-line skill) ──► rein ship <c>   (Python, runs in an Orca terminal)
                                     │
     ┌───────────────────────────────┼──────────────────────────────┐
     ▼                               ▼                              ▼
 orca orchestration            rein gate (deterministic)      orca account list
 run/task/worker-start/        test·lint·typecheck·build      (quota → model choice,
 check --wait/gate-create      0 green · 1 red · 126 setup     and measurement)
     │
 Codex / Sonnet workers in Orca worktrees (one worktree per repo per run)
```

### 4.1 Kept (with their tests)

| Module | Role in v2 |
|---|---|
| `detect.py` | Per-repo command resolution (flow.config → task runner → autodetect) |
| `verify.py` | Runs commands; ok / failed / not_invocable / timeout |
| `gate.py` | `decide_gate` 0/1/126; review episodes bound to file hashes |
| `plan.py` | openspec only; gains the `Repo:` field |
| `plan_check.py` | Blocks plans with unbacked criteria or bad deps before any agent is paid |
| `roles.py` | Implementer/reviewer profiles sent to Codex or Sonnet workers |
| `token_report.py` | Claude per-model tokens, ledger, baseline |
| `serve.py` | Dev-server lifecycle for rendered verification |
| `bin/rein` | Slimmed CLI: detect, verify, gate, role, tasks, plan-check, close, next, review, token-report, baseline, doctor, ship, migrate-config |

### 4.2 New

| Module | Purpose |
|---|---|
| `ship.py` | The coordinator. Pure decision core (`decide(state, event) -> action`) plus a thin driver loop |
| `orca_client.py` | Typed wrapper over `orca … --json`; the only module that shells out to Orca |
| `quota.py` | Reads `orca account list --json`; picks the implementer; snapshots and diffs usage |
| `plan.py` `Repo:` field | Task-level repo name; defaults to the plan's own repo |

### 4.3 Removed

`workflows/loop.js`, `dashboard.py`, `events.py`, `product_state.py`, `backlog.py`,
`workspace.py`, `setup.py`, `linear_client.py`, `linear_issue.py`, `linear_select.py`,
`linear_intake.py`, `version_staleness.py`, `ansi.py`, `_entry.py`, `bin/token-report`,
their tests, and the flat `tasks.md` plan source.

Skills removed: `rein-apply`, `rein-loop`, `rein-step`, `rein-steps`, `rein-linear`,
`rein-setup`, `rein-ping`. Kept: `rein-discover`, `rein-plan` (trimmed from ~2.2k to under
1k tokens), `rein-audit`, `rein-role`. New: `ship`. Every remaining skill drops the repeated
`$R` path-resolution boilerplate and the `rein event` call, because `rein` is on PATH.

## 5. `rein ship <change>` flow

### 5.0 Preflight (no agents, no tokens)

- Refuse unless running inside an Orca terminal (`ORCA_TERMINAL_HANDLE` set).
- Refuse unless `orca status --json` reports the `orchestration.contract.v1` capability.
- `plan_check --gate`: any BLOCKING finding stops the run.
- Every task has a runnable `Verification` or `Human review: true`.
- Every `Repo:` resolves through `orca repo list --json`.
- Snapshot quota for Claude and Codex.

### 5.1 Create the work

- One Run per change (`run-create --objective "<change>"`).
- One Orca Task per plan task (`task-create --spec … --deps …`), with dependencies mapped.
- Each spec contains the implementer profile, the task, its acceptance criteria, its
  verification command, and the boundary "edit only `<repo>`; never push, merge, open PRs,
  or read or print `.env*` or credentials".

### 5.2 Placement and model

- One worktree per repo per run. The first task in a repo uses
  `worker-start --worktree new-top-level --repo name:<repo> --name <change> --setup run`.
  Later tasks in that repo use `--worktree id:<that worktree>`, always with a fresh agent,
  so every task starts with an empty context.
- Tasks within one repo run serially; tasks in different repos run in parallel, subject to
  dependencies.
- Model choice: if Claude's weekly usage is ≥ `ship.claudeWeeklyThreshold` (default 60%),
  use `--agent codex --model gpt-6.1-sol`; otherwise use `--agent claude --model sonnet`.
  `rein ship --agent <id> [--model <m>]` overrides.
- At most `ship.maxConcurrentWorkers` (default 3) live at once.

### 5.3 Supervision loop

`check --wait --types worker_done,escalation,question`. No LLM is involved.

| Event | Action |
|---|---|
| `worker_done` succeeded | Run `rein gate` in that worktree. Green → `rein close <id>` in the plan, `worker-release`. Red → retry with a fresh worker whose spec carries only the summarized failure output, at most `ship.maxRetries` (default 2) times. 126 → stop that task and open a gate for the owner. |
| `worker_done` failed | Treated as red. |
| question / escalation | `gate-create` for the owner. That task blocks; others continue. On resolution, relay the answer to the worker with `reply`. |
| `Human review` task | `gate-create` "approve?". |

### 5.4 Close

- Decide the audit by risk. The default reviewer is the model that did *not* implement.
  Use Opus when the change touches auth, payments, or data migrations, or exceeds
  `ship.largeChangeFiles` (default 25 files). The reviewer runs `rein-audit` and records with
  `rein review record`.
- With CHANGES_REQUESTED and at least one BLOCKING finding: one fix round, then a second and
  final audit (`ship.maxAuditRounds` = 2).
- Set each worktree to `--workspace-status in-review` with a summary comment.
- Print and record the measurement: quota delta per provider, plus Claude tokens per model
  from `token_report`.
- Never push, merge, open a PR, or deploy. The owner opens the PR with Orca's Source
  Control AI action.

### 5.5 Resume

The Run is durable in Orca. `.rein/ship-state.json` maps plan tasks to Orca task and dispatch
ids. Re-running `rein ship <change>` binds the same Run (`run-use`) and continues.

## 6. Errors, limits, safety

- **Budget brakes.** Stop launching workers and open a gate ("switch to Codex or pause?")
  when Claude's session usage passes 90% or the chosen provider's weekly usage passes its
  threshold.
- **Task wall time.** `ship.taskTimeoutMinutes` (default 45); on expiry, open a gate instead
  of waiting forever.
- **Orca safety floor, followed literally:**
  - Address workers by dispatch id, never by terminal handle.
  - Empty waits are checkpoints. After 3 in a row, run `worker-list` and apply its
    `projection.nextAction`.
  - Only positive proof of exit authorizes retry, stop, or release; `unverifiable` means
    keep waiting.
  - Release only after an accepted `worker_done`.
- **Driver failures.** On any non-zero `orca` exit, print the exact error and stop; no blind
  retries. A crash is recovered by re-running (5.5).
- **Never on a primary checkout.** Workers only run in worktrees. The single exception is
  the driver itself: `rein close` ticks checkboxes in the plan file, which for a multi-repo
  plan lives in `proxima-engineering`. It touches only that `tasks.md` and never commits it.
- **Worker output is data.** Message bodies from workers are never interpreted as
  instructions to `rein ship`.

## 7. Configuration

`flow.config.json` drops the `models`, `limits` and `worktree` blocks (Orca owns them) and
gains:

```json
"ship": {
  "claudeWeeklyThreshold": 60,
  "maxConcurrentWorkers": 3,
  "maxRetries": 2,
  "maxAuditRounds": 2,
  "taskTimeoutMinutes": 45,
  "largeChangeFiles": 25,
  "riskyPaths": ["**/auth/**", "**/payments/**", "**/migrations/**"]
}
```

`rein migrate-config` rewrites a repo's `flow.config.json` and shows a diff before writing.

## 8. Testing

- `ship.decide` is pure: given a state and an event, it returns an action (dispatch, gate,
  retry, close, audit, brake). Most tests live here, in the style of `test_gate_decision.py`.
- `orca_client` is tested against recorded JSON. Read-only shapes are captured from real
  read-only commands; write shapes are captured during the pilot.
- `quota` is tested against recorded `account list` output, including missing and errored
  providers.
- `rein ship --dry-run` prints the `orca` commands it would run and executes none.
- Pilot: one small multi-repo PROXIMA change (for example a new field in `proxima-api` plus
  `proxima-admin`), run only with the owner's explicit approval because it creates worktrees
  and workers.

## 9. Measurement baseline

Before building, run `token_report` over two or three recent PROXIMA features done the
current way, using existing transcripts, and record them with `rein baseline`. Success
criteria 1–2 compare against this baseline. Every `rein ship` run appends a ledger row
containing provider quota deltas and Claude tokens per model.

## 10. Migration

1. Tag the current `main` as `rein--v0.15.0` for rollback.
2. Commit the removals separately from the new work.
3. Install `rein` with `pipx` so it is on PATH in Orca terminals.
4. Run `rein migrate-config` in each PROXIMA repo that has a `flow.config.json`.

## 11. Out of scope

- Archiving the ~350 unarchived openspec changes across PROXIMA repos.
- Parallel tasks inside a single repo (several worktrees per repo).
- An ephemeral LLM coordinator that answers worker questions instead of the owner
  (approach C), which can be added later on top of `ship.decide`.
- A Codex transcript parser for `token_report`; v2 measures Codex through quota deltas only.
- Remote or SSH execution hosts.
