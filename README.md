<div align="center">

# 🐎 Rein

**Keep your Claude Code agents lean and in control.**

A Claude Code plugin and a small CLI that turn a plan into shipped, independently-audited work —
by driving fresh Orca workers task by task, with no LLM in the middle.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/luisfarfan/rein-agentic-kit/actions/workflows/ci.yml/badge.svg)](https://github.com/luisfarfan/rein-agentic-kit/actions/workflows/ci.yml)
[![Dependencies](https://img.shields.io/badge/runtime%20deps-none-success.svg)](#)
[![Python](https://img.shields.io/badge/python-3.9%2B%20stdlib-blue.svg)](#)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-plugin-8A2BE2.svg)](https://claude.com/claude-code)

</div>

---

## 💸 The problem, measured

Optimizing agent cost by "making the model write less" does nothing. Over real Claude Code
transcripts:

| Where the tokens go | Share |
|---|---:|
| 🔴 `cache_read` — context re-read **every turn** | **~90%** |
| 🟠 `cache_write` | ~10% |
| 🟢 **output** — what a compressor would attack | **~0.3%** |
| ⚪ fresh input | ~0% |

**Cost ≈ turns × context size.** One agent that ran 241 turns, re-reading ~234k of context on
every single turn, was most of a 112M-token run.

And Claude Code has **no native eviction of stale tool results** — only `/compact`, which is
lossy and breaks the prompt cache. A long agent's context only grows.

Measured on the owner's machine over 14 days (52.2B tokens): **98.7% of all tokens ran on Opus**, the
median session peaked at **417k tokens of context**, and the part of Rein built to avoid this
(`rein-apply`) ran in ~5 sessions against 12–22 for `rein-plan` over 30 days. Plans were made with
Rein and executed without it. v2 fixes that by making execution one command.

> **Measured effect of the original loop: 241 turns → 26 · Opus 100% → 0% · ~7× less context per turn.**

---

## 🔁 The lifecycle

```
 ① DISCOVER        ② PLAN             ③ SHIP                  ④ AUDIT + PR
 rein-discover  →  rein-plan      →   rein ship <change>  →   rein-audit  →  you open the PR
 (reads, asks,     (verifiable        (Python, no LLM:        (a different
  never edits)      tasks, each        Orca workers, one       model reviews;
                    with a Repo)       gate per task)          one fix round)
 └──── one short Claude session ────┘ └─── you only answer Orca gates ───┘
```

1. **Discover** (`/rein:rein-discover`) thinks the problem through. It writes no plan.
2. **Plan** (`/rein:rein-plan`) turns it into tasks, each with acceptance criteria, a bounded
   verification command and a `Repo:`. A task that must change the gate's own configuration
   (flow.config.json, a Makefile/mise.toml/justfile/Taskfile a gate command uses) declares it
   with `Gate config: <path>`; Rein's guard refuses any undeclared change to it.
   Nothing is written without your confirmation.
3. **Ship** (`/ship <change>`, or `/rein:ship`) runs `rein ship <change>`. Python dispatches one
   fresh Orca worker per task (Sonnet while Claude's weekly quota is low, else Codex, else
   Antigravity), runs `rein gate` on each result, retries a red task with a fresh worker, and
   opens an **Orca decision gate** whenever a worker has a question or something needs your
   judgement.
4. **Audit** (`rein-audit`) is run by Sonnet or Codex, preferring the one that did *not* implement
   and is under its quota brake (Opus for risky paths or large changes). The final state is
   `in-review`: **Rein never pushes, merges, opens a PR or deploys.** You open the PR.

No agent approves its own work, and every worker starts with an empty context.

---

## 🚀 Quickstart

```bash
claude plugin marketplace add luisfarfan/rein-agentic-kit
```

```bash
claude plugin install rein@rein-agentic-kit --scope user
```

**Restart Claude Code**, then, per change:

```
/rein:rein-discover   →   think it through, until the unknowns that matter are closed
/rein:rein-plan       →   you confirm the plan
/ship <change>        →   workers implement, each task is gated, then the audit
```

> **`rein ship` must run inside an Orca terminal** (it refuses unless `ORCA_TERMINAL_HANDLE` is set,
> and unless `orca status` reports the orchestration contract); `rein ship --detach`, which `/ship` uses, opens that terminal for you. It also needs Orca's `orca` CLI
> (or `$ORCA_CLI_COMMAND`) and each task's repo registered in Orca.

### Want `rein` in your own terminal too?

Claude Code puts the plugin's `bin/` on the path of its own sessions only. For `rein ship`,
`rein gate` or `rein doctor` from an Orca terminal, install the CLI:

```bash
uv tool install rein-agentic-kit    # or: pipx install rein-agentic-kit
```

Same code, same version, zero dependencies. A test fails the build if the two channels'
version strings ever disagree.

> To update: `claude plugin marketplace update rein-agentic-kit` then
> `claude plugin update rein@rein-agentic-kit` — qualified with the marketplace,
> or it reports `Plugin "rein" not found`.

Coming from v1? `rein migrate-config` rewrites `flow.config.json` (drops `models`, `limits`,
`worktree`; adds `ship`) and shows a diff first.

---

## 🧰 What you get

### Skills

| | |
|---|---|
| 🔭 **`/rein:rein-discover`** | Think a problem through before committing to a shape. **Writes no plan, implements nothing** |
| 🧭 **`/rein:rein-plan`** | Plan into verifiable tasks — dry-run and **explicit confirmation** before writing |
| 🚢 **`/ship`** (`/rein:ship`) | Open a detached Orca tab running `rein ship <change>`, then watch it (`rein watch`) so Rein's questions and the final result appear in this session. Never edits code or opens a PR |
| 💬 **`/answer`** (`/rein:answer`) | "¿Qué me pregunta rein?" / "respóndele: …": show and answer the run's pending gates |
| ⚡ **`/quick`** (`/rein:quick`) | "Tarea rápida: …": hand a small change to a cheap agent in its own tab, no plan. You talk to it; "listo" runs the light gate. Never edits code itself |
| 🔍 **`/rein:rein-audit`** | The independent gate: mechanical checks first, then five-axis judgement |
| 🎭 **`/rein:rein-role`** | Assign this session's role: planner · implementer · reviewer |

### CLI — the deterministic half

```bash
rein ship <change> [--root <plan repo>] [--agent <id> [--model <m>]] [--dry-run | --detach]
                 # drive a plan through Orca workers: dispatch, gate each task, audit.
                 # A task's gate: lint, typecheck, the affected tests and its Verification;
                 # each repo's full gate (whole suite) runs once, after its tasks, before the
                 # audit and the re-audit. A red full gate becomes a GATE<n>-<repo> fix task.
                 # Gates run in the background: the loop keeps handling workers, gates and
                 # answers meanwhile. taskTimeoutMinutes covers a task's gating too.
                 # Resumes from .rein/ship/<change>.json. --dry-run prints every orca call and
                 # runs nothing. Never pushes, merges or deploys.
                 # Exit: 0 shipped and approved · 1 failed tasks or rejected audit
                 #       2 usage · 126 preflight or environment (e.g. not in an Orca terminal)
                 # --detach opens a new Orca tab "rein ship <change>" that runs the loop and
                 # returns; refuses if that tab is already live. Not combinable with --dry-run.
                 # Rings the terminal bell when a gate opens and when the run ends, so Orca
                 # notifies you from a background tab. If Orca restarts mid-run (an update),
                 # it waits up to 10 minutes for Orca and resumes by itself, up to 3 times.
rein answer [<change>] ["<text>"] [--gate <id>] [--root <plan repo>]
                 # No text: list the owner gates a running ship is waiting on (question,
                 # options). With text: resolve one (option gates take one of their options,
                 # case-insensitive). Never touches the ship state; the driver picks it up. From inside
                 # another Orca terminal (which Orca fences) the answer is queued under
                 # .rein/ship/<change>.answers/ and the driver applies it within ~15 s.
                 # Exit: 0 ok · 1 Orca error or nothing to answer · 2 usage (ambiguous or invalid)
rein watch <change> [--root <plan repo>] [--once] [--skip <id,id>]
                 # block until the run needs you: prints PENDING <gate> for every open gate
                 # on its first poll, then QUESTION <gate> (full text, options) for new ones,
                 # FINISHED (audit result, task statuses) or STOPPED (driver not running), then
                 # exits 0. /ship runs it in the background so questions reach the planning
                 # session. Nothing is hidden across restarts.
                 # --skip hides deferred gate ids (FINISHED/STOPPED/new gates still wake it).
                 # --once polls once, exit 1 if nothing pending · 2 usage
rein done <change> [--root <plan repo>] [--dry-run] [--yes] [--push]
                 # close a finished change: remove its Orca worktrees, close the "rein ship" tab,
                 # archive the openspec plan (local commit on the current branch), archive the ship
                 # state. --push (opt-in) pushes ONLY that commit, from the plan repo, to the branch
                 # of the same name (hooks run); it is a blocker, found before anything is removed,
                 # when the upstream has another name or other local commits are ahead. It NEVER
                 # pushes main, master or qa, in any repo: there the change still closes and the
                 # exact `git -C <repo> push <remote> <sha>:refs/heads/<branch>` is printed.
                 # Removes NOTHING if any worktree is dirty, a main checkout, or has commits (HEAD or its
                 # branch) not in origin/<base>. Gitignored files that would be lost need --yes.
                 # Exit: 0 closed · 1 blockers · 2 usage · 126 Orca error or a rejected --push
rein quick "<sentence>" [--repo <name>] [--base <branch>] [--here [--dirty-ok]] [--agent <id> [--model <m>]] [--linear <id>]
                 # hand a small change to a cheap agent chosen by quota, in a tab you talk to.
                 # Default: its own worktree off develop, or the remote's default branch when there is none; --here uses the main checkout
                 # (--dirty-ok if it has uncommitted work). Never pushes or merges.
                 # Exit: 0 started · 1 failed · 2 usage · 126 environment
rein quick done [--accept-outside] [--accept-path <abs path>]... [--accept-commit <sha>]...
                 # run by that agent on "listo": lint/typecheck plus the tests the change reaches
                 # (gate.affected), stopping at the first failure, with gate.env and the gate
                 # cache; a risky path runs the full suite instead, then an audit. Never commits.
                 # Exit: 0 green · 1 red · 2 no quick task here · 126 setup (not a verdict)
                 # --accept-path / --accept-commit: the owner's registered exit for a REAL change in a
                 # watched checkout that is another session's (see "Writes outside the worktree")
rein accept-outside [--root <plan repo>] (--path <abs path> | --commit <sha>)...
                 # ship's counterpart: records, in <plan repo>/.rein/outside-accept.json, changes in a
                 # watched checkout that are another session's, so no task of that plan is rejected for them
rein quick watch # tell the orchestrator what each quick needs: a QUESTION (told once; that the agent no longer
                 # waits on it is never inferred from other activity: close it with `rein quick resolve --id qN`,
                 # or the agent replaces it with `rein quick ask --replaces qN`),
                 # a REPORT, a STALLED agent. What was told and is still open is listed as STILL OPEN
                 # by `--once` (with what happened since, as a hint), never mixed with news. It never rewrites a record that is fine.
rein quick close [<workspace path or quick-<slug> name>] [--dry-run] [--yes]
                 # remove the worktrees of finished quick tasks (all when no argument) and their
                 # records, only if clean and integrated into origin/<base>; blocked ones are kept
                 # and listed. A --here task only loses its record. Exit: 0 · 1 blockers · 126 Orca
rein migrate-config # v1 -> v2 flow.config.json: drop models/limits/worktree, add `ship`.
                 # Prints a diff; writes only with --write
rein doctor      # 🩺 start here: stack, resolved commands, and the source of each
                 # --json: {"version", "pluginRoot", "project", "plan", "verifyState",
                 # "ledger"}
rein detect      # stack + commands, with the source of each
rein tasks       # the plan, parsed
rein context     # detect + plan in ONE round-trip
rein verify      # actually RUN each resolved command and report the truth — an inference is not a fact
rein gate        # ✅ did they PASS — 0 green · 1 the code is wrong · 126 the environment could not run them
                 # runs only lint, typecheck, build, test (never testOne or custom slots), cheap
                 # first; the first slot that does not pass stops it, the rest report `skipped`.
                 # An optional `testIntegration` command (integration/e2e suite) runs last;
                 # --unit-only leaves it out and drops the browser-render demand.
                 # A slot that passed on the same tree state (HEAD, git status, diff, untracked
                 # files), command and gate.env is reused and printed `cached` (--no-cache:
                 # run anyway); failures, timeouts and setup problems are never stored. Cache:
                 # $REIN_CACHE_DIR or ~/.cache/rein. Each slot's timeout is 2x its slowest
                 # recent pass (2-30 min); before it ever passed, --timeout or
                 # ship.gateTimeoutSeconds.
                 # --tier task --base <ref>: lint, typecheck and only the tests the change since
                 # <ref> reaches (see gate.affected); --tier full: every gate slot plus
                 # testIntegration. Both are command gates (no browser-render demand)
                 # --plan: run each TASK's own Verification before implementers are paid.
                 # Failing is fine (the work does not exist yet); proving NOTHING is not —
                 # a missing test module or a selector matching zero tests exits non-zero
rein gate-log <change>  # every gate a ship run ran (task and repo gates): tier, slots, time,
                 # cached, budget, gate.env names; kept next to its state, archived by `rein done`.
                 # `--path <checkout>`: the gates `rein gate` / `rein quick done` ran there
                 # (~/.claude/rein/verify/log). --json for the raw lines
rein env [root]  # `export` lines of the worktree's gate.env with its placeholders filled:
                 # `eval "$(rein env)"` gives a shell the env Rein's gates use there
rein plan-check  # mechanical findings on a drafted plan's own text. Always exits 0 (it runs
                 # inside the planning skill); `--gate` exits 1 on a BLOCKING finding, which
                 # is how `rein ship` refuses a bad plan. `--tasks T001,T002` scopes it.
                 # A task's Repo: is found by plan.repos, the sibling folder, else Orca's repos
rein role <name> # ✅ one role's operating profile (planner|implementer|reviewer) as markdown
rein next        # ✅ the gate: is there a task to claim, and may it be
rein close T001  # tick a checkbox deterministically — no agent hand-edits the plan
rein review      # record / check a verdict bound to a code state
rein token-report# what a run really cost, per agent and per model
rein ledger      # history across projects, with deltas vs a marked baseline
                 # --json: {"runs": [...]}
rein baseline    # mark the run everything is compared against
```

> **The rule that separates them:** if it's a *parse*, a script does it. If it's a *judgement*,
> an agent does it. Every fact an agent doesn't have to rediscover is turns you don't pay for.

---

## ⚖️ The three levers

**1️⃣ Fresh workers, one task each.** Every plan task gets a new Orca worker with an empty
context, so spend stops growing without a ceiling.

**2️⃣ Route by quota, not habit.** Claude's weekly usage ≥ `ship.claudeWeeklyThreshold` (60%) sends
implementation to Codex; otherwise Sonnet. A provider at 90% of its session or weekly quota is
skipped for the next one, Antigravity (Gemini) last. Opus is reserved for the audit of risky or large
changes. A brake opens a gate ("retry or pause?") only when every candidate is at 90%, or when an
`--agent` override is.

**3️⃣ Verifiable signals, not model judgement.** `rein gate` answers "did it pass" from exit codes;
`rein review check` answers "does this approval still apply" from content hashes. Neither asks a
model anything.

### ❌ Deliberately not included

Tried and discarded **with data**, not taste:

- **Output compressors** — they attack the 0.3%.
- **Multi-provider API swarms** — the saving doesn't apply on a subscription.
- **Cross-session memory tools** — orthogonal to the actual cost driver.
- **Cache-aware proxies** — no-op on already-cached traffic.
- **Persona prompts** — constraints that can be *violated* are useful; job titles are not.

---

## ⚙️ Configure

Everything is optional. Drop a `flow.config.json` at your project root to override:

```json
{
  "commands": {
    "test": "uv run pytest -q",
    "testOne": "uv run pytest -q {target}",
    "lint": "uv run ruff check .",
    "typecheck": "uv run mypy ."
  },
  "ship": {
    "claudeWeeklyThreshold": 60,
    "maxConcurrentWorkers": 3,
    "maxRetries": 2,
    "maxAuditRounds": 2,
    "taskTimeoutMinutes": 45,
    "stallMinutes": 10,
    "trustProbeSeconds": 30,
    "reviewDir": "",
    "gateTimeoutSeconds": 900,
    "largeChangeFiles": 25,
    "riskyPaths": ["**/auth/**", "**/payments/**", "**/migrations/**"]
  }
}
```

The `ship` block shows its defaults. `maxRetries` is how many times a red task is retried with a
fresh worker, `maxAuditRounds` bounds audit and fix rounds (when it runs out with blocking findings, the gate offers `fix` for one more fix round and audit, `accept` or `reject`), `taskTimeoutMinutes` opens a gate
instead of waiting forever (it counts the task's gating time too), `stallMinutes` opens a `stalled`
gate (wait / resend the brief / retry) for a worker with no sign of work that long after its launch
(no commit, no edit, no question) -- at once when Orca reports it parked on a prompt only a person can
answer, or its terminal (`orca terminal read`) shows its agent's startup dialog (Codex's "Hooks need
review", a folder-trust prompt): the gate names it ("the agent waits for you to trust its hooks in
<path>"). A worker that fails, or a launch Orca aborts, on such a dialog opens one `setup` question
instead of spending retries against it. When Orca aborts the start (it reports e.g.
`agent-hooks-review-prompt`) the question names that dialog, the repo and the worktree path, and
Rein opens a terminal of yours with the task's agent in that worktree (`orca terminal create
--worktree path:<wt> --command <agent>`; Orca does not close it) where you answer the dialog.
Meanwhile Rein probes with a fresh start after `trustProbeSeconds` (30, doubling up to 16x); the
first start that is no longer aborted carries on by itself, with no retry spent and no second bell
(the probes' questions repeat quietly). Rein never answers the dialog nor edits the agent's trust
settings: the owner does; `0` turns it off (`rein quick watch` says the same about
a quick task, and `rein quick resend` types its brief again), `gateTimeoutSeconds` is a gate slot's timeout until that slot has passed in the repo
(raise it for a slow suite); after that each slot gets twice its slowest of the last five passes,
between 2 and 30 minutes, so a hung suite is stopped long before the old 6× bound, and a change that touches `riskyPaths` or more than `largeChangeFiles`
files gets an Opus audit. `reviewDir` (inside the plan root) is where a `Human review` task's
review sheet `review-<task>.md` goes when its gate opens; by default the plan's own change folder
(`openspec/changes/<change>/`, else `.rein/review/<change>/`), never a scratch directory, and the
owner's question gives the path.

### Plan roots in a temporary directory

`rein ship` keeps its state, gate log and answers in `<root>/.rein/`; a root under `/tmp`,
`/private/tmp`, `/var/folders` or `$TMPDIR` loses all of it on reboot. `rein ship` refuses such a
root unless you pass `--allow-temp-root`; `rein quick` (from a temp checkout) and
`rein plan-check --root` warn.

### Per-worktree environment (`gate.env`)

Worktrees of one repo share whatever their tests reach outside the tree — usually a test
database — and two suites resetting the same schema at once fail each other. Declare the
variables once, in the repo's `flow.config.json` or in the plan root's (it then applies to every
repo of the change; a repo's own value wins):

```json
{ "gate": { "env": { "APP_TEST_DB": "app_test_{worktree}" } } }
```

`{worktree}` is a slug of the worktree's directory name (lowercase, `[a-z0-9_]`, at most 40
characters, the same on every run) and `{repo}` the repo's name. Rein applies the expanded values
to every gate and Verification it runs in that worktree, to `rein quick done`, and — Orca cannot
pass an environment to an agent — writes them as an `export` line at the top of the worker's and
the auditor's brief so their own test runs use the same database. Without `gate.env` nothing
changes. Creating the database on first use is the repo's job (its test setup).

### Writes outside the worktree (`gate.outsideIgnore`, `gate.outsideWatch`)

When a worker (or quick agent) is launched Rein notes the git state of the other checkouts **of its
task**: for a quick, the checkout of its repo; for ship, those of the repos the plan's tasks name and
the plan root's. The checkouts of every other repo Orca knows belong to other sessions and are not
watched. A sibling repo a task may really write into (an update script that reaches into it) is
declared with `gate.outsideWatch`, in the task repo's `flow.config.json` or the plan root's:
`{ "gate": { "outsideWatch": ["proxima-qa"] } }` (Orca repo names, or absolute directories). By the end of its task, file content that differs there fails it, with the files named
(a file dirty at launch and committed unchanged by someone else is not a difference). Other sessions
work in those checkouts too, so a commit only counts when the worker could have made it: created in
that checkout (its reflog), under the checkout's own `user.email`, and not already reachable from a
ref seen at launch. A fetch, or a fast-forward or merge of work that existed at launch, is not the
worker's. A commit made in that checkout by another identity may have swept in a file the worker
left pending there, which no comparison can then see: it is **unresolved**, never green. `rein quick
done` exits 1 ("not green") until the owner checks that checkout and runs `rein quick done
--accept-outside`; ship holds the task in an `outside` gate (accept / retry / skip). Odder but
harmless activity (HEAD switched to unrelated history, work made after launch that arrived by a
merge) is a visible `guard warning`.
A change that really happened in a watched checkout and is another session's work (a legitimate
integration, a regenerated file) is not the agent's to revert: the owner names it. `rein quick done
--accept-path /abs/checkout/path --accept-commit <sha>` (repeatable) and, for ship, `rein
accept-outside --root <plan repo> --path ... --commit ...` accept exactly those paths and commits,
only if they are real changes when the task is judged (anything else named is itself a red
verdict). Each acceptance is logged with the path's fingerprint or the commit (`guardAccepted`);
every other change still counts.
`rein quick done` runs this check even when the worktree itself has no diff (restoring only the local
work settles nothing), and logs an `--accept-outside` in the gate log (`guardAccepted`). In ship the
snapshot a task is judged against is kept through its retries while a write outside is rejected or
unresolved: a retry (the owner's, or the automatic one after a guard rejection) never makes that
write the new baseline; restore the other checkout or accept it. A repo's own git
hooks can write there without the worker doing anything: beads (`bd hooks run post-checkout`,
`pre-commit`, lefthook's `beads-export`) rewrites `.beads/` in the MAIN checkout on every commit
or checkout made in a worktree. Leave such paths out with globs relative to the watched repo, in
that repo's `flow.config.json` or in the plan root's (it then applies to every watched repo):

```json
{ "gate": { "outsideIgnore": [".beads/"] } }
```

A pattern excludes exactly what it declares. `dir/` is everything inside that folder, nothing
else: not a file named `dir`, and not a `dir/` deeper in the tree (each of its components is
matched on its own, so `*` there stays inside one name). Any other glob must match the whole
repo-relative path (`*` crosses `/`), and never covers what is under a folder it happens to
match: `.beads/*.jsonl` does not cover `.beads/x.jsonl/CLAUDE.md`. The patterns are read when
the worker is launched, so a config edited later widens nothing, and they never hide a new
commit in the other checkout. Rein checks up to 5000 changed or untracked paths per checkout,
ignored paths not counted; more than that makes the guard fail closed ("guard could not run"),
never pass. Without `gate.outsideIgnore` every change counts.

### Tiered gates (`gate.affected`)

A task's gate runs `lint`, `typecheck`, the tests its change reaches and the task's own
Verification; the full `test` slot (and `build`, and the optional `testIntegration`) runs once
per repo, after all its tasks and before the audit. How a change maps to tests:

```json
{ "gate": { "affected": { "python": "pytest", "node": "vitest" }, "affectedMax": 40 } }
```

- `pytest` — changed test files, `tests/**/test_<module>.py` for each changed module (the
  directory that mirrors the module's wins), and every test that imports the module (a grep of
  its dotted path), run through `commands.testOne` with all of them in `{target}`.
- `vitest` / `jest` — the runner's own graph: `vitest related --run <files>`,
  `jest --findRelatedTests <files>`.
- a command with `{tests}` (mapped test files, e.g. sibling `*.spec.ts`) or `{files}` (the changed
  files), e.g. `"npx ng test --watch=false --include {tests}"`.
- `testOne` (mapped test files through `commands.testOne`) or `full` (the `test` slot).

Without `gate.affected` it is detected from the `test` command (pytest, vitest, jest; a package
script such as `pnpm test` is read from `package.json`), else `testOne` when it takes `{target}`,
else `full`. `vitest related` / `jest --findRelatedTests` are only picked when the `test` command
is exactly ONE direct invocation of that runner -- `vitest run`, `npx jest`, `pnpm exec vitest run`,
`pnpm vitest`, `yarn jest`, `bunx vitest` -- with plain arguments on one line: a line break, `;`,
`&&`, `||`, `|`, `&`, backticks, `$(`, `<` or `>` anywhere disqualifies it, as do subshells, env
assignments or `cd` (a package invocation and its script's body must meet the same rule). Anything
else uses the repo's `testOne`, else the full suite: a repo tested with `ng test` gets its `testOne`
(detected as `ng test --watch=false --include={target}`). Nothing affected runs no tests at task level; more than `affectedMax` files (default 40)
or a change to dependencies or test configuration runs the full slot.

**Precedence:** `flow.config.json` › task runner (`justfile` / `Makefile` / `Taskfile` / `mise`) ›
autodetection. A project that already declares how it is built is never second-guessed.

## 🧱 Supported stacks

| Stack | Detected by | Verification |
|---|---|---|
| 🐍 Python | `pyproject.toml`, uv / poetry | pytest · ruff · mypy |
| 🟨 Node / TS | `package.json`, pnpm / npm / yarn / bun | vitest or jest · eslint · tsc |
| 🦀 Rust | `Cargo.toml` | cargo test · clippy · check |
| 🐹 Go | `go.mod` | go test · vet |
| 🎨 **Frontend** *(Next, Vite, Astro, SvelteKit, Nuxt, Remix)* | dependencies | **rendered verification** — unit tests alone don't catch "the tests pass but the UI is broken" |
| ☁️ Serverless / infra | `serverless.yml`, `sst.config.ts`, `*.tf` | `plan` / `validate` — **never deploy** |
| 📦 Monorepo | sub-projects one or two levels down, no root manifest | two or more candidates are reported — **it never picks one for you**; exactly one candidate is resolved directly with its path carried (`cd <dir> && ...`) |

> **A resolved command is an inference until something runs it.** `rein verify` executes each one
> and reports what happened, distinguishing *"the binary is missing"* (a **setup** problem) from
> *"the suite ran and failed"* (a **code** problem) — conflating them sends you to the wrong file.

## 📊 Measure

```bash
rein token-report && rein ledger
```

Reads Claude Code's own JSONL transcripts (including `cache_read`) and breaks a run down per
agent and **per model**. Every run is summarized into `~/.claude/rein/runs.jsonl`; `rein ship`
appends a row per repo it worked in and prints the usage delta per provider when it finishes.

**On the word "savings":** this measures *consumption*. A saving needs a baseline. Mark one with
`rein baseline mark` and every later run in that project gets a signed delta.

---

## 🙃 Honest limitations

- **v2 is unproven on real work.** The v2 success criteria (Claude tokens ≤ 30% of baseline, Opus
  share ≤ 20%) are measured against a baseline recorded before the build; a pilot on a small
  multi-repo change comes first.
- **It needs Orca.** Rein v2 has no runtime of its own; without an Orca terminal it refuses to run.
- **The reviewer is calibrated hard.** In the v1 loop, 8 of 9 runs used all 3 review rounds and the
  last round still found a real blocking issue in 4 — which is why there is no `--fast` that
  reviews less.
- **Audience.** The owner and PROXIMA. No compatibility promises.

See [docs/decisions.md](docs/decisions.md) for why, and the
[v2 design](docs/superpowers/specs/2026-10-17-rein-v2-orca-design.md) for how.

## 📄 License

MIT — see [LICENSE](LICENSE).

<div align="center">
<sub>Built with Claude Code, reviewed by an agent that wrote none of it.</sub>
</div>
