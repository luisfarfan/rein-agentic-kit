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

## 🧭 When to use Rein (and when not)

Claude Code or Codex on its own is the default. Rein steps in only where it measurably adds
something (`docs/research/2026-10-28-post-sdd/`):

| The work | Use |
|---|---|
| A small, low-risk change in one repo | **Claude Code or Codex alone.** No Rein |
| Real uncertainty, several requirements or several repos | `/rein:rein-plan`, then implement natively |
| Touches permissions, security, concurrency, durable state or contracts between services | add `/rein:rein-audit` |
| Several dependent tasks, or coordination across repos | `/ship` |

More audits or more test runs are not more quality by themselves: a measured audit round
that fixed nothing cost 35 minutes, and gate-fix workers spent 39 of a run's 65 minutes on a
flaky suite.

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
   `in-review`: **`rein ship` never pushes, merges, opens a PR or deploys.** You open the PR. (The only push Rein can make is `rein done --push`, opt-in, of the plan-archive commit; never to `main`, `master` or `qa`.)

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

Every command below exists in `rein --help`. The help is the command list, **not** a complete flag reference: some flags are parsed but not shown there, and the syntax below is the one the code accepts.

```bash
rein ship <change> [--root <plan repo>] [--agent <id> [--model <m>]] [--dry-run | --detach] [--allow-temp-root]
                 # drive a plan through Orca workers: dispatch, gate each task, audit.
                 # A task's gate: lint, typecheck, the affected tests and its Verification;
                 # each repo's full gate (whole suite) runs once, after its tasks, before the
                 # audit and the re-audit. A red full gate runs again; the same failure twice
                 # becomes a GATE<n>-<repo> fix task, anything else goes to you.
                 # Gates run in the background: the loop keeps handling workers, gates and
                 # answers meanwhile. taskTimeoutMinutes covers a task's gating too.
                 # Resumes from <root>/.rein/ship/<change>.json. --dry-run prints every orca call
                 # and runs nothing. Never pushes, merges or deploys.
                 # --agent alone uses that agent's default model; --model needs --agent.
                 # Exit: 0 shipped and approved · 1 failed tasks or rejected audit
                 #       2 usage · 126 preflight or environment (e.g. not in an Orca terminal)
                 # --detach opens a new Orca tab "rein ship <change>" that runs the loop and
                 # returns; refuses if that tab is already live. Not combinable with --dry-run.
                 # --allow-temp-root: run with a plan root that a reboot would wipe (see below).
                 # Rings the terminal bell when a gate opens and when the run ends, so Orca
                 # notifies you from a background tab. If Orca restarts mid-run (an update),
                 # it waits up to 10 minutes for Orca and resumes by itself, up to 3 times.
rein answer [<change>] ["<text>"] [--gate <id>] [--root <plan repo>]
                 # No text: list the owner gates a running ship is waiting on (question,
                 # options). With text: resolve one (option gates take one of their options,
                 # case-insensitive). Never touches the ship state; the driver picks it up. From inside
                 # another Orca terminal (which Orca fences) the answer is queued under
                 # .rein/ship/<change>.answers/ and the driver applies it within ~15 s.
                 # Exit: 0 ok (listing with nothing pending prints `no questions pending` and is still 0) ·
                 # 1 Orca error, or text given with no question pending · 2 usage (ambiguous or invalid)
rein watch <change> [--root <plan repo>] [--once] [--skip <id,id>]
                 # block until the run needs you: prints PENDING <gate> for every open gate
                 # on its first poll, then QUESTION <gate> (full text, options) for new ones,
                 # FINISHED (audit result, task statuses) or STOPPED (driver not running), then
                 # exits 0. /ship runs it in the background so questions reach the planning
                 # session. Nothing is hidden across restarts.
                 # --skip hides deferred gate ids (FINISHED/STOPPED/new gates still wake it).
                 # --once polls once: exit 1 if nothing pending · 126 Orca cannot be asked · 2 usage
rein accept-outside [--root <plan repo>] (--path <abs path> | --commit <sha> | --hook <sha>)...
                 # ship: record, in <plan repo>/.rein/outside-accept.json, that a change in a watched
                 # checkout is another session's work, so no task of that plan is held for it.
                 # Verified when each task is judged, and logged (see "Writes outside the worktree")
rein done <change> [--root <plan repo>] [--dry-run] [--yes] [--push]
                 # close a finished change: remove its Orca worktrees, close the "rein ship" tab,
                 # archive the openspec plan (local commit on the plan repo's current branch; its
                 # folder must be committed), archive the ship state. Removes NOTHING if any
                 # worktree is dirty, a main checkout, or has commits (HEAD or its branch) not in
                 # origin/<base>. Gitignored files that would be lost are listed and need --yes,
                 # except gate.regenerable ones. Each removed worktree then runs gate.cleanup.
                 # --push (opt-in) pushes ONLY the archive commit, from the plan repo, to the branch
                 # of the same name (hooks run). It is a blocker, found before anything is removed,
                 # when the upstream has another name, there is no remote branch, or other local
                 # commits are ahead. It NEVER pushes main, master or qa, in any repo: there the
                 # change still closes and the exact
                 # `git -C <repo> push <remote> <sha>:refs/heads/<branch>` is printed.
                 # Exit: 0 closed · 1 blockers · 2 usage · 126 Orca error or a rejected --push
rein quick "<sentence>" [--repo <name>] [--base <branch>] [--here [--dirty-ok]] [--agent <id> [--model <m>]] [--linear <id>] [--allow-config <path,...>]
                 # hand a small change to a cheap agent chosen by quota, in a tab you talk to.
                 # Default: its own worktree off develop (or the remote's default branch, else main,
                 # when the repo has no develop; --base names one). Prefers origin/<base> after a fetch; if the fetch fails it
                 # uses the stored origin/<base>, or the local branch when there is none, and says so.
                 # --here uses the checkout you are in, on its current branch (--dirty-ok if it has
                 # uncommitted work). --allow-config: the gate config this task may change.
                 # Never pushes or merges. Exit: 0 started · 1 failed · 2 usage · 126 environment
rein quick ask "<question>" [--replaces q1]
                 # run by that agent when it needs the owner: the question goes to .rein/quick.json;
                 # it then waits. --replaces closes its own earlier open question (it must exist and
                 # be open here, else exit 2 and nothing is recorded)
rein quick answer [<path or quick-<slug>>] "<text>" [--id q1]
                 # record the owner's answer and type it into the agent's terminal.
                 # Exit: 0 sent · 1 no open question · 2 usage · 126 recorded, not sent
rein quick resolve [<path or quick-<slug>>] --id q1
                 # close a question WITHOUT telling the agent (answered in its own terminal)
rein quick watch [<path or quick-<slug>>] [--once]
                 # block until a quick task needs the owner: a QUESTION, a REPORT, or STALLED
                 # (no sign of work ship.stallMinutes after launch); prints it, exit 0. --once exits 1
                 # when nothing is pending and lists what was told and is still open as STILL OPEN
rein quick report "<text or file>"
                 # run by that agent when it finishes: its report, kept in .rein/quick.json
                 # (and .rein/quick-report.md); `rein quick watch` prints it (REPORT)
rein quick allow-config <path or quick-<slug>> <path,...>
                 # grant --allow-config to a quick already running and tell the agent. Exit: 0 · 1 no
                 # task · 2 usage · 126 recorded, agent not reached
rein quick resend [<path or quick-<slug>>] [--terminal <handle>]
                 # type the task's brief into the agent's terminal again (e.g. after a startup dialog
                 # swallowed it). Exit: 0 sent · 1 no quick task · 2 ambiguous · 126 Orca
rein quick done [--accept-outside] [--accept-path <abs path>]... [--accept-commit <sha>]... [--accept-hook <sha>]...
                 # run by that agent on "listo": lint/typecheck plus the tests the change reaches
                 # (gate.affected), stopping at the first failure, with gate.env and the gate cache;
                 # a risky path runs the full suite instead, then an audit. Never commits or pushes.
                 # --accept-outside: the owner has checked uncertain activity in another checkout.
                 # --accept-path / --accept-commit (repeatable): the owner's registered exit for a REAL
                 # change in a watched checkout that is another session's.
                 # --accept-hook <sha> (repeatable, 7+ chars): the commit's pre-commit hook could not be
                 # re-run by Rein (see "Guardrails") and you ran it by hand. Logged by commit.
                 # Exit: 0 green · 1 red or unresolved outside activity · 2 no quick task here ·
                 #       126 setup (not a verdict)
rein quick close [<workspace path or quick-<slug> name>] [--dry-run] [--yes]
                 # remove the worktrees of finished quick tasks (all when no argument) and their
                 # records, only if clean and integrated into origin/<base>; blocked ones are kept
                 # and listed. A --here task only loses its record. Run it from outside the worktree.
                 # Exit: 0 · 1 blockers · 126 Orca
rein migrate-config [root] [--write]
                 # v1 -> v2 flow.config.json: drop models/limits/worktree, add `ship`.
                 # Prints a diff; writes only with --write
rein doctor [root] # 🩺 start here: stack, resolved commands, and the source of each
                 # --json: {"version", "pluginRoot", "project", "plan", "verifyState", "ledger"}
rein detect      # resolved stack + commands as JSON (config > task runner > autodetect)
rein tasks       # the plan, parsed (--change <name> for openspec, --source, --path)
rein context     # detect + plan in ONE round-trip
rein verify      # actually RUN each resolved command and report the truth — an inference is not a fact
                 # exit 0 when every command could be INVOKED, even if a suite ran and failed
                 # rein verify [root] [--plan] [--change <name>] [--repo <name>] [--only slot,slot]
                 #             [--timeout <seconds>] [--json]
                 # --plan: run each TASK's own Verification before implementers are paid.
                 # Failing is fine (the work does not exist yet); proving NOTHING is not —
                 # a missing test module or a selector matching zero tests exits non-zero.
                 # --change <name> picks the change exactly as `rein tasks --change` does; an unknown
                 # change (or none named in an openspec tree) is an error, exit 2 — never an empty green
rein gate        # ✅ did they PASS — 0 green · 1 the code is wrong · 126 the environment could not run them
                 # runs only lint, typecheck, build, test (never testOne or custom slots), cheap
                 # first; the first slot that does not pass stops it, the rest report `skipped`.
                 # An optional `testIntegration` command (integration/e2e suite) runs last;
                 # --unit-only leaves it out and drops the browser-render demand -- unless
                 # --tier full is also given: then full wins and testIntegration runs.
                 # A slot that passed on the same tree state (HEAD, git status, diff, untracked
                 # files), command and gate.env is reused and printed `cached` (--no-cache: run
                 # anyway); failures, timeouts and setup problems are never stored. Cache:
                 # $REIN_CACHE_DIR or ~/.cache/rein. Each slot's timeout is 2x its slowest recent
                 # pass (2-30 min); before it ever passed, --timeout or ship.gateTimeoutSeconds.
                 # --tier task --base <ref>: lint, typecheck and only the tests the change since
                 # <ref> reaches (see gate.affected); --tier full: every gate slot plus
                 # testIntegration. Both are command gates (no browser-render demand).
                 # --require-review also demands a current APPROVED review episode (--change).
                 # rein gate [root] [--only slot,slot] [--timeout <seconds>] [--json] [--no-cache]
                 #           [--repo <name>] [--env-root <plan repo>] [--change <name>]
                 #           [--render <evidence.json>] [--browser-tools <t1,t2>] [--url <dev-server url>]
                 # (--only, --timeout, --json, --change, --render, --browser-tools, --url are not in --help)
rein gate-log <change> [--root <plan repo>] | --path <checkout>  [--json]
                 # every gate a ship run (task and repo gates) ran: tier, slots, time, cached, budget,
                 # gate.env names; kept next to its state, archived by `rein done`.
                 # `--path <checkout>`: the gates `rein gate` / `rein quick done` ran there
                 # (~/.claude/rein/verify/log, or $REIN_GATE_LOG_DIR). Exit 1 when there is no log
rein env [root]  # `export` lines of the worktree's gate.env with its placeholders filled:
                 # `eval "$(rein env)"` gives a shell the env Rein's gates use there
                 # [--repo <name>] [--env-root <plan repo>]
rein plan-check <plan-file> [--root <plan repo>] [--gate] [--tasks T001,T002]
                 # mechanical findings on a drafted plan's own text, as JSON. Always exits 0 (it runs
                 # inside the planning skill); `--gate` exits 1 on a BLOCKING finding (126 when the
                 # file cannot be read), which is how `rein ship` refuses a bad plan. `--tasks` scopes
                 # it: a finding pinned to a task nobody will execute cannot stop the run, one with no
                 # task id is plan-level and always counts. A task's Verification is looked up in its
                 # Repo: the plan root (--root, else the plan file's checkout), plan.repos, the plan
                 # root's sibling directory of that name, or else the repo registered in Orca
rein role <name> # ✅ one role's operating profile (planner|implementer|reviewer) as markdown
rein next        # ✅ the gate: is there a task to claim, and may it be
rein close T001  # tick a checkbox deterministically — no agent hand-edits the plan
rein review      # record / check a verdict bound to a code state (record|check)
rein token-report # what a run really cost, per agent and per model
rein ledger      # history across projects, with deltas vs a marked baseline (--json: {"runs": [...]},
                 # {"runs": []} when there is none yet; without --json an empty ledger is a text line, exit 0)
rein baseline    # mark the run everything is compared against (mark|show|clear)
rein benchmark startup [--profile default|lean|both] [--source <dir>] [--model <m>] [--max-tokens <n>] [--json]
                 # what a fresh Claude worker loads before its brief, per ship.launchProfile: one `claude -p`
                 # reply per profile (Haiku, on your subscription). Exit 0 · 1 over --max-tokens (30000, lean) · 126
```

`rein serve-probe` (start a dev server, wait for its URL, tear it down) is the internal helper the
an available helper for preparing a rendered verification (`--start --pidfile <p>` / `--stop --pidfile <p>`,
`--command <c> --url <u> [--timeout N] [--cwd <dir>]`); no gate or skill calls it automatically today (the
gate reads evidence from `--render`), and it is not listed in `rein --help`.

> **The rule that separates them:** if it's a *parse*, a script does it. If it's a *judgement*,
> an agent does it. Every fact an agent doesn't have to rediscover is turns you don't pay for.

---

## ⚖️ The three levers

**1️⃣ Fresh workers, one task each.** Every plan task gets a new Orca worker with an empty
context, so spend stops growing without a ceiling.

**2️⃣ Route by quota, not habit.** Claude's weekly usage ≥ `ship.claudeWeeklyThreshold` (60%) sends
implementation to Codex; otherwise Sonnet. A provider at `ship.brakePercent` (90% by default) of its session or weekly quota is
skipped for the next one, Antigravity (Gemini) last. Opus is reserved for the audit of risky or large
changes. A brake opens a gate ("retry or pause?") only when every candidate reaches that limit, or when an
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

Everything is optional. Drop a `flow.config.json` at your project root (or at the plan root; see "What the plan root passes on" below); what you omit is autodetected, and this
file always wins. [`plugins/rein/flow.config.example.json`](plugins/rein/flow.config.example.json) annotates most keys
(not `commands.testIntegration`). `rein migrate-config` rewrites a v1 file.

**Precedence:** `flow.config.json` › task runner (`justfile` / `Makefile` / `Taskfile` / `mise`) ›
autodetection. A project that already declares how it is built is never second-guessed.

| Key | Default | What it does |
|---|---|---|
| `stack`, `subtypes` | detected | Force the stack (`python`, `node`, …) and its subtypes (frontend frameworks) |
| `subproject` | `""` | Monorepo only: the sub-project every command resolves in. Set it for a monorepo with several candidates (Rein reports them and does not pick); with exactly one sub-project it is resolved for you |
| `commands.test` · `testOne` · `lint` · `typecheck` · `build` | detected | The gate slots. `testOne` takes `{target}` (a test file or id) |
| `commands.testIntegration` | unset | Integration/e2e suite: runs last in a full gate; left out by `--unit-only` (unless `--tier full` is also given) and by task gates |
| `commands.serve` | `""` | How to start the dev server for rendered verification (always wins over the `package.json` lookup) |
| `plan.source` · `plan.path` | detected · `""` | `openspec` (`openspec/changes/<change>/`) or `tasks-md` (a plain `tasks.md`) |
| `plan.repos` | `{}` | Cross-repo plans: where each task's `Repo:` is checked out (name → path, relative to the file). Used by `rein plan-check`; unset: the sibling directory, else the repo registered in Orca |
| `tracker.kind` | `none` | Reported by `rein doctor`; the plan's checkboxes are the state (Rein does not sync an external tracker) |
| `verify.mode` | `auto` | `auto` picks `rendered` for frontend subtypes, `plan-only` for infra without a test command, else `unit`. Asking for *less* than detected needs `verify.until: "YYYY-MM-DD"` and stops applying after it |
| `verify.url` | detected | Dev-server URL for rendered checks (e.g. an app under a subpath) |
| `ship.claudeWeeklyThreshold` | `60` | Claude weekly usage % above which implementation goes to Codex |
| `ship.brakePercent` | `90` | Session or weekly usage % at which a provider is braked in ship and quick; number >0 and <=100. At `100`, usage below 100% is allowed. Invalid values keep the default |
| `ship.maxConcurrentWorkers` | `3` | Live workers at once |
| `ship.maxRetries` | `2` | Fresh workers a red task gets before the owner is asked (`retry_exhausted`). A worker that fails exactly as the one before it (same failure once ids, shas, timings and temp paths are set aside) asks at once: another worker would meet it again, e.g. the guard's "wrote outside its worktree" about a commit in another checkout. The question says what a worker cannot do (`rein accept-outside` …); the text a retry is given has no colour codes |
| `ship.maxAuditRounds` | `2` | Audit + fix rounds. When it runs out with blocking findings the gate offers `fix` (one more round), `accept` or `reject` |
| `ship.taskTimeoutMinutes` | `45` | Wall time per task (its gating included) before a `timeout` gate opens |
| `ship.stallMinutes` | `10` | A worker or quick agent with no sign of work (no commit, edit or question) that long after launch asks the owner; `0` = never |
| `ship.trustProbeSeconds` | `30` | Startup dialogs, below. Must be positive: `0` or a bad value keeps the default |
| `ship.launchProfile` | `default` | `lean`: before a Claude **implementer** starts in an existing worktree, Rein puts a `.claude/settings.local.json` there that turns your user plugins off and denies the tools a worker never uses (Agent, Artifact, Workflow, Skill …; never ToolSearch). Measured: 59.0k → 21.2k tokens of startup context, re-read on every turn. Only where git ignores the file; the owner's own file is put back before the audit (auditors always run on the default) and when the run stops. The first worker of a repo starts on the default (Orca creates its worktree at launch). Check it with `rein benchmark startup` |
| `ship.reviewDir` | `""` | Where a `Human review` task's `review-<task>.md` goes, relative to the plan root. Default: `openspec/changes/<change>/`, else `.rein/review/<change>/` |
| `ship.gateTimeoutSeconds` | `900` | A gate slot's timeout until that slot has passed in the repo; after that, twice its slowest of the last five passes, between 2 and 30 minutes. Raise it for a slow suite |
| `ship.largeChangeFiles` | `25` | A ship change touching more files than this gets an Opus audit |
| `ship.riskyPaths` | `["**/auth/**", "**/payments/**", "**/migrations/**"]` | Globs that route the audit to Opus; in `rein quick done` they are what makes it run the full suite and an audit |
| `gate.env` | `{}` | Per-worktree environment (below) |
| `gate.affected` · `gate.affectedMax` | detected · `40` | Which tests a task's gate runs (below) |
| `gate.prepare` | unset | Command run in the worktree before the first check of a gate that is not answered from the cache (`rein gate`, ship's task and repo gates, `rein quick done`): e.g. generated API clients before typecheck. `{worktree}`, `{repo}`, `{path}`. Its failure is a setup problem (exit 126), not a red gate |
| `gate.cleanup` | unset | Command run once per worktree after `rein done` / `rein quick close` removed it, from the repo's main checkout, with the worktree's `gate.env`: drop what the gates created outside the tree (a test database). Same placeholders. A failure is reported, never undoes the close |
| `gate.regenerable` | `[]` | Gitignored files a worktree's setup regenerates (repo-relative globs, trailing `/` for a folder): `rein done` / `rein quick close` do not count them as lost and need no `--yes` for them |
| `gate.protected` | `[]` | Globs a task may not change unless its own text names them. `flow.config.json` and the config of every task runner a gate command uses are always protected (see "Guardrails"). A plan root's globs apply to every repo of the change |
| `gate.outsideIgnore` · `gate.outsideWatch` | `[]` · `[]` | Writes outside the worktree (below) |
| `retrieval.codegraph` | — | Present in the example file; no code reads it today (see the limitations) |

**What the plan root passes on.** In a ship run the `ship` block is read from the plan root. Of the `gate`
block, a repo being judged also takes from the plan root: `gate.env` (the repo's own value wins),
`gate.cleanup` (the repo's own, else the root's), `gate.regenerable`, `gate.outsideIgnore`,
`gate.outsideWatch` and `gate.protected` (the root's globs are *added* to the repo's own, since
protection is policy). It does **not** take `gate.prepare`, `gate.affected` or `gate.affectedMax`: those
are commands and settings of one repo's tree, so declare them in each repo's own `flow.config.json`.
(`commands.*` are always the repo's.)

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
the auditor's brief so their own test runs use the same database. `rein env` prints the same lines.
Without `gate.env` nothing changes. Creating the database on first use is the repo's job (its test
setup); dropping it is `gate.cleanup`'s.

### Tiered gates and the cache

A task's gate runs `lint`, `typecheck`, the tests its change reaches (`gate.affected`) and the
task's own Verification; `test` in full (and `build`, and the optional `testIntegration`) runs
**once per repo**, after all its tasks and before the audit and the re-audit. A red repo gate
runs once more before any worker is spent on it. The same failing tests twice become a
`GATE<n>-<repo>` fix task, whose worker first runs each failing test alone and never edits test
setup for a failure the change did not cause. The environment failing (a refused or dropped
connection, no Docker) or a different failure each run is a `repo_gate` question for you, with the
failure: no worker fixes a flaky suite. `rein gate --tier task --base <ref>` and `--tier full` are the
same two levels by hand. A slot that passed on the same tree state, command and `gate.env` is
reused (`cached`); a failure, a timeout or a setup problem is never cached.

**`gate.affected`** maps a change to tests:

```json
{ "gate": { "affected": { "python": "pytest", "node": "vitest" }, "affectedMax": 40 } }
```

- `pytest` — changed test files, `tests/**/test_<module>.py` for each changed module (the
  directory that mirrors the module's wins), and every test that imports the module (a grep of
  its dotted path), run through `commands.testOne` with all of them in `{target}` (a flag glued to
  `{target}` repeats per file, inside `sh -c '…'` too).
- `vitest` / `jest` — the runner's own graph: `vitest related --run <files>`,
  `jest --findRelatedTests <files>`.
- a command with `{tests}` (mapped test files, e.g. sibling `*.spec.ts`) or `{files}` (the changed
  files), e.g. `"npx ng test --watch=false --include {tests}"`.
- `testOne` (mapped test files through `commands.testOne`) or `full` (the `test` slot).

**Fallback when it is not configured:** detected from the `test` command (pytest, vitest, jest; a
package script such as `pnpm test` is read from `package.json`), else `testOne` when it takes
`{target}`, else `full`. `vitest related` / `jest --findRelatedTests` are only picked when the `test`
command is exactly ONE direct invocation of that runner (`vitest run`, `npx jest`,
`pnpm exec vitest run`, `pnpm vitest`, `yarn jest`, `bunx vitest`) with plain arguments on one line:
a line break, `;`, `&&`, `||`, `|`, `&`, backticks, `$(`, `<` or `>` anywhere disqualifies it, as do
subshells, env assignments or `cd` (a package invocation and its script's body must meet the same
rule). Anything else uses the repo's `testOne`, else the full suite: a repo tested with `ng test`
gets its `testOne` (detected as `ng test --watch=false --include={target}`). Nothing affected runs no
tests at task level (lint and typecheck still run); more than `affectedMax` files, a change to
dependencies or test configuration, or a strategy that cannot be applied runs the full `test` slot.

### Guardrails on every worker

Workers and auditors are **told** (in their brief): no writes outside the worktree (next section), no
bypassing the repo's hooks (`--no-verify`, `LEFTHOOK=0`, `SKIP`, `HUSKY=0` …), no change to the
configuration Rein judges with, no edit to global configuration (`~/.claude`, `~/.codex`, shell or git
config). Rein **checks** them too, when a worker's task ends and again when an auditor's verdict is read:

- **Gate config and test setup.** `flow.config.json`, a gate command's runner file, the test setup every
  test runs through (`conftest.py`, `pytest.ini`, `tox.ini`, a vitest, jest, playwright or karma config)
  and any `gate.protected` glob change only when the task declares them (`Gate config:`). A repo whose
  test setup lives elsewhere (a `tests/db_setup.py`) lists it in `gate.protected`.

- **Hooks.** Each commit of the change must pass the repo's own `commit-msg` hook and its `pre-commit`
  hook, both re-run by Rein with the bypass variables removed. `commit-msg` runs on the commit's message.
  `pre-commit` runs in a temporary detached checkout of the commit's parent with the commit's tree staged
  on top. The worker's gitignored folders (`node_modules`, `.venv` …) are given to that checkout as
  **copy-on-write clones** (APFS `cp -c`, or `--reflink` on Linux), so a hook such as lefthook's
  `pnpm exec eslint {staged_files}` finds the tools the worktree has. Nothing the hook writes can reach
  the worker: a write into a clone stays in the clone, and one anywhere else lands in the temporary
  checkout, where it counts as a change (below). After cloning, Rein walks the symlinks of the cloned
  folders (without following them): one whose target, absolute or relative, resolves inside the worker's
  worktree is moved to the same path of the temporary checkout (a clone preserves an absolute link, and a
  hook writing through it would write to the worker); if one cannot be moved safely the hook is
  unchecked. As a last defence Rein fingerprints the worker's worktree (`git status` plus a digest of
  every path it lists) before and after the re-run. If it changed meanwhile, Rein cannot know who changed
  it — the hook through a path it missed, the worker, you, another session — so that commit's hook result
  is **void** (red): "el worktree del worker cambió durante la comprobación del hook (paths…); Rein no
  puede saber quién lo cambió y no tocó nada; revisa esas rutas y vuelve a ejecutar done". It is blamed
  on nobody, and **Rein never restores, overwrites or deletes anything in the worker** — your edits,
  staged changes and new files, large ones included, stay byte for byte as they are. Costs, on APFS: cloning a medium `node_modules`
  (25 000 files, 195 MB) took about 2.6 s; a real pnpm one (17 257 files, 387 MB, 1 298 symlinks) cloned in
  3.0 s and its symlinks were walked in 0.14 s — once per re-run commit. If the filesystem cannot clone
  (another volume, no reflink) Rein does not copy the folders for real: the hook is **unchecked**, and the
  message says why. Three results, never mixed:
  - **red**: the hook ran on the commit's tree and rejected it (the message carries its error lines and
    the path of its full output) — a linter's rejection signal (`1:1 error …`, `file:line:col …`,
    `N problems`) always wins, whatever else the output says — **or** it left the tree changed (an
    autofix such as lint-staged, or any write into the checkout): the commit as made does not pass its
    hook, and the message shows the diff of what the hook would have fixed (amend the commit; Rein
    changes nothing). That includes `Cannot find module`, `ModuleNotFoundError` and `No such file or
    directory`: they are as often the code's own bad import as a missing dependency, and the worker's
    dependencies are already in the clone, so a module that is still missing there is, in almost every
    case, the code's error. The accepted cost: if a Python or Node module is genuinely absent even from
    the cloned dependencies, it shows as red, with its output and log;
  - **green**: it ran and left the tree as it found it;
  - **unchecked**: it could not be run reliably, and only because its tool is unambiguously missing —
    exit 126/127, or a line that is exactly `sh: x: command not found` / `sh: 1: x: not found`, or pnpm's
    `Command "x" not found` (pnpm's `ERR_PNPM_RECURSIVE_EXEC_FIRST_FAIL` alone is not a signal: it also
    prefixes `Command failed with exit code N`, a tool that ran and rejected, which is red) — or because the re-run itself could not
    be made: a timeout (120 s), a root commit, a commit past the newest 25, a git too old for `git hook`
    (< 2.36), no copy-on-write clone, or a symlink that cannot be relocated. Unchecked is never a pass and
    never a red: `rein quick done` exits 1 naming each commit, and the owner, having run the hook by hand,
    answers `--accept-hook <sha>` (logged by commit); ship holds the task in the `outside` gate
    (`accept` · `retry` · `skip`, no retry is spent on it) or `rein accept-outside --hook <sha>`
    remembers the answer for the run. Nothing retries it by itself, and an accept never turns a red green.

  Other hooks (`pre-push`, `post-commit` …) are not re-run.
- **After an auditor.** Before the auditor starts, Rein notes the protected config of the worktrees and
  the state of the watched checkouts; when the verdict is read it compares. If the auditor edited
  protected config or wrote outside its worktree (same rules, same `--accept-*` exits as for workers),
  the verdict is **not taken**, whatever it says. Writes outside cover the watched checkouts and, in
  ship, the ordinary files of the *other* worktrees of the run (only the auditor's own is left out), all
  with the `gate.outsideIgnore` each had **when its task was launched** — never a config read again
  later, which a worker may have edited, and never one repo's ignores applied to another. In
  `rein quick done` the run exits 1 (or 126 when the guard itself could not run) with the reasons and the
  exits below, and the rejection is kept in the quick's record with the baseline taken before that
  auditor: every later `done` re-checks it before it returns success — including the "nothing changed
  since the task started" return after the local work was undone — until the write is reverted or
  accepted; in `rein ship` an `audit_guard` gate opens (below). What an auditor does *inside* its
  worktree beyond protected config and the other worktrees is not checked.

The configuration Rein judges with, and protects from a worker, is `flow.config.json`, the config of a
task runner a gate command comes from or calls (`mise.toml`, `Makefile`, `justfile`,
`Taskfile.yml`, as resolved at the change's base and now), any `gate.protected` glob — unless the task
declares it in its `Gate config: <path>` field (quick: `--allow-config`). A runner no gate command
uses may change.

### Writes outside the worktree (`gate.outsideIgnore`, `gate.outsideWatch`)

**Scope.** When a worker (or quick agent) is launched Rein notes the git state of the other
checkouts **of its task** — for a quick, the checkout of its repo; for ship, those of the repos the
plan's tasks name and the plan root's (found by its real git path). The checkouts of every other repo
Orca knows belong to other sessions and are not watched. A sibling repo a task may really write into
(an update script that reaches into it) is declared with `gate.outsideWatch`, in the task repo's
`flow.config.json` or the plan root's:

```json
{ "gate": { "outsideWatch": ["sibling-repo"] } }
```

(Orca repo names, or absolute directories.)

**Certain versus uncertain.** By the end of its task, file content that differs in a watched checkout
fails it, with the files named (a file dirty at launch and committed unchanged by someone else is not a
difference). Other sessions work in those checkouts too, so a commit only counts when the worker could
have made it: created in that checkout (its reflog), under the checkout's own `user.email`, and not
already reachable from a ref seen at launch. A fetch, or a fast-forward or merge of work that existed
at launch, is not the worker's. A commit made in that checkout by another identity may have swept in a
file the worker left pending there, which no comparison can then see: it is **unresolved**, never
green. Odder but harmless activity (HEAD switched to unrelated history, work made after launch that
arrived by a merge) is a visible `guard warning`.

- **Unresolved** — `rein quick done` exits 1 ("not green") until the owner checks that checkout and runs
  `rein quick done --accept-outside`; ship holds the task in an `outside` gate (`accept` / `retry` /
  `skip`). `--accept-outside` accepts only this uncertain kind.
- **Certain** (the file really differs) — not the agent's to revert if it is another session's work (a
  legitimate integration, a regenerated file): the owner names it. `rein quick done --accept-path
  /abs/checkout/path --accept-commit <sha>` (repeatable) and, for ship, `rein accept-outside --root
  <plan repo> --path … --commit …` (stored in `<plan repo>/.rein/outside-accept.json`, applied when each
  task is judged) accept exactly those paths and commits, **only if they are real changes at that
  moment**; anything else named is itself a red verdict, and a commit prefix needs 7+ characters. A
  path's identity is its checkout plus its git entry, without resolving a final symlink: accepting link
  `A` does not accept link `B` to the same target. Every other change still counts.
- **Logged** — each acceptance goes to the gate log as `guardAccepted`, with the path's fingerprint or
  the commit and its subject; `rein gate-log` shows it.

`rein quick done` runs this check even when the worktree itself has no diff (restoring only the local
work settles nothing). In ship, the snapshot a task is judged against is kept through its retries while
a write outside is rejected or unresolved: a retry (the owner's, or the automatic one after a guard
rejection) never makes that write the new baseline; restore the other checkout or accept it.

**Hooks that write elsewhere.** A repo's own git hooks can write in another checkout without the worker
doing anything (a tool that rewrites a tracked folder in the MAIN checkout on every commit made in a
worktree). Leave such paths out with globs relative to the watched repo, in that repo's
`flow.config.json` or in the plan root's (it then applies to every watched repo):

```json
{ "gate": { "outsideIgnore": [".generated/"] } }
```

A pattern excludes exactly what it declares. `dir/` is everything inside that folder, nothing
else: not a file named `dir`, and not a `dir/` deeper in the tree (each of its components is
matched on its own, so `*` there stays inside one name). Any other glob must match the whole
repo-relative path (`*` crosses `/`), and never covers what is under a folder it happens to
match: `.generated/*.json` does not cover `.generated/x.json/CLAUDE.md`. The patterns are read when
the worker is launched, so a config edited later widens nothing, and they never hide a new
commit in the other checkout. Rein checks up to 5000 changed or untracked paths per checkout,
ignored paths not counted; more than that makes the guard fail closed ("guard could not run"),
never pass. Without `gate.outsideIgnore` every change counts.

### Questions and decision gates

A ship run opens an Orca **decision gate** whenever it needs you; answer with `rein answer` (or
`/answer`), and `rein watch` shows them. The kinds and their options:

| Gate | Opens when | Options |
|---|---|---|
| question | a worker asks | free text |
| `retry_exhausted` | a task is red after `maxRetries` | `retry` · `skip` |
| `outside` | the guard left something unresolved: activity outside the worktree, or a `pre-commit` hook it could not re-run | `accept` · `retry` · `skip` |
| `timeout` | a task passed `taskTimeoutMinutes` | `retry` · `skip` |
| `stalled` | no sign of work `stallMinutes` after launch | `wait` · `resend` · `retry` |
| `setup` | a worker failed or was aborted on a startup dialog | `retry` · `skip` |
| `human_review` | a `Human review` task (below) | `approve` · `reject` |
| `repo_gate` · `repo_gate_red` | a repo's full gate could not run / is red | `retry` · `skip` |
| `audit_exhausted` | blocking findings after `maxAuditRounds` | `fix` · `accept` · `reject` |
| `audit_timeout` · `audit_failed` | the audit timed out / could not run | `retry` · `skip` / `retry-audit` · `accept` · `reject` |
| `audit_guard` | the auditor edited protected config or wrote outside its worktree; its verdict is held | `retry-audit` · `accept` · `reject` |
| `brake` | every provider candidate is at 90% (or an `--agent` override is) | `retry` · `pause` |

**After a held audit verdict.** In `rein ship`, `audit_guard` keeps the verdict Rein did not take:
`accept` says the activity was not the auditor's and lets that verdict stand, `retry-audit` audits again
keeping the first look as the baseline (what was not reverted stays red; `rein accept-outside --path/--commit`
names a change that is another session's), `reject` ends the audit rejected. In `rein quick done` the way
out is the one a worker's write has: revert it and run `rein quick done` again (the standing approval
needs no new audit once the kept rejection is clear), or name it with `--accept-path` / `--accept-commit` (`--accept-outside` for the
uncertain kind). The attempt is in the gate log either way.

**Human review.** A task with `Human review: true` and no Verification is decided by you alone: no
worker runs. With a Verification, its gate runs first and, when green, you `approve` or `reject`. The
gate writes a review sheet (`review-<task>.md`: acceptance criteria, Verification, files and commits)
in `ship.reviewDir` and the question gives its path.

**Startup dialogs.** A worker with no sign of work `stallMinutes` after launch — at once when Orca
reports it parked on a prompt only a person can answer, or its terminal shows its agent's startup
dialog (Codex's "Hooks need review", a folder-trust prompt) — opens a `stalled` gate that names it.
A worker that fails, or a launch Orca aborts, on such a dialog opens one `setup` question instead of
spending retries against it. When Orca aborts the start (e.g. `agent-hooks-review-prompt`) the question
names the dialog, the repo and the worktree path, and Rein opens a terminal of yours with the task's
agent in that worktree (`orca terminal create --worktree path:<wt> --command <agent>`; Orca does not
close it) where you answer it. Meanwhile Rein probes with a fresh start after `trustProbeSeconds` (30,
doubling up to 16x); the first start that is no longer aborted carries on by itself, with no retry
spent and no second bell. Rein never answers the dialog nor edits the agent's trust settings: the
owner does. A quick agent gets the same `STALLED` notice from `rein quick watch`.

### The quick lane, end to end

`rein quick "<sentence>"` (`/quick`) is the no-plan path for one small change in one repo.

1. **Start.** Picks the implementer by quota (as ship does; `--agent` overrides), resolves the repo
   (`--repo`, else the one you are in), creates a worktree from `origin/<base>` (`develop`; the remote's
   default branch, else `main`, if the repo has no `develop`; `--base` names it). It tries to fetch first;
   if the fetch fails it uses the stored `origin/<base>` as it was, or the local branch when there is no
   remote one, and says so — or, with `--here`,
   uses your checkout on its current branch, even a local-only one (`--dirty-ok` if it has uncommitted
   work) — launches the agent in a tab and writes `.rein/quick.json`. It snapshots the watched
   checkouts for the guard. Two repos in one sentence: use `/rein:rein-plan` instead.
2. **Talk to it.** The agent asks through `rein quick ask`, never only in its terminal. `rein quick
   watch` (run it in the background) prints what needs you — a **QUESTION**, a **REPORT** (the agent's
   final text from `rein quick report`), **STALLED** (a startup dialog swallowed the brief) — and you
   reply with `rein quick answer`. `rein quick resend` types the brief again; `rein quick allow-config`
   lets a running quick change a gate config file.
3. **Questions.** A question is announced **once**. Until it is answered, resolved or replaced it is
   open, and `watch --once` lists it under **STILL OPEN** (with what happened since — another question,
   a report, a commit — as a hint, never as proof). Later activity never closes a question. It closes
   only by an explicit `rein quick answer` (also typed into the agent's terminal), `rein quick resolve
   [<quick>] --id qN` (you answered in the terminal; the agent is not told) or the agent's own
   `rein quick ask --replaces qN` (the id must be an open question of that quick, else exit 2 and
   nothing is recorded). `resolve` first settles *which quick* — inside a quick, that one; a name
   matching several quicks is ambiguous and needs the path; with none given outside a quick, it needs
   exactly one — and only then the id. `watch` never rewrites a record that is fine.
4. **Done.** On "listo" the agent runs `rein quick done`: lint, typecheck and the affected tests with
   `gate.env`, `gate.prepare` and the cache. Only a change that touches `riskyPaths` runs the full suite
   and then an audit (a size alone does not). The outside-writes guard runs too. It never commits or
   pushes. Exit 0 green · 1 red or unresolved outside activity · 126 setup.
5. **Close.** Once the work is integrated into `origin/<base>`, `rein quick close` removes the clean,
   integrated worktrees and their records (`gate.cleanup` runs per worktree; a `--here` task only loses
   its record; lost gitignored files need `--yes`).

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
agent and **per model**. A run is summarized into `~/.claude/rein/runs.jsonl` by `rein token-report`; `rein ship`
appends a row per repo it worked in when Claude transcripts for it exist (otherwise it prints that it has
none and goes on; a Codex-only run leaves no row) and prints the usage delta per provider when it finishes.

**On the word "savings":** this measures *consumption*. A saving needs a baseline. Mark one with
`rein baseline mark` and every later run in that project gets a signed delta.

---

## 🙃 Honest limitations

- **v2 is unproven on real work.** The v2 success criteria (Claude tokens ≤ 30% of baseline, Opus
  share ≤ 20%) are measured against a baseline recorded before the build; a pilot on a small
  multi-repo change comes first.
- **It needs Orca.** Rein v2 has no runtime of its own; the `rein ship` loop refuses to run outside an Orca terminal (`--detach` opens one; `--dry-run` runs nothing), and `quick`, `done` and `answer` call Orca; `detect`, `doctor`, `env`, `gate`, `verify` and the other local commands do not. `plan-check` needs no Orca terminal either, but as a last resort, to resolve a task's `Repo:`, it asks Orca for its repo list (`orca repo list`); without Orca that lookup just finds nothing.
- **The reviewer is calibrated hard.** In the v1 loop, 8 of 9 runs used all 3 review rounds and the
  last round still found a real blocking issue in 4 — which is why there is no `--fast` that
  reviews less.
- **The outside-writes guard is git-based.** It sees what git sees in the watched checkouts: ignored
  paths, files outside any repo and writes that were reverted before the end of the task are
  invisible, and a commit swept in by another identity is only flagged as unresolved. Declare the
  siblings a task really writes into (`gate.outsideWatch`); the rest is the owner's judgement.
- **Config keys that do nothing yet.** `retrieval.codegraph` is in the example file but no code reads
  it, and `tracker.kind` is only displayed by `rein doctor` (no Beads sync). The skills probe
  `codegraph` on their own.
- **Help is incomplete.** `rein --help` omits flags the parsers accept (`gate --only/--timeout/--json/--render/
  --browser-tools/--url/--change`, `verify --plan`) and the `serve-probe` command; the CLI block above has them.
  `rein <cmd> --help` prints the usage line for most commands and runs the command for a few (`gate`, `next`).
- **The hook and auditor checks have edges.** Only `commit-msg` and `pre-commit` are re-run. The
  `pre-commit` re-run has the worker's ignored folders (cloned) but not its whole environment, and each
  re-run costs a clone and a hook run (up to 25 commits): a hook that needs something else it has only in
  the worktree can fail in the clean checkout, and Rein calls that red unless it is exit 126/127 or an
  exact missing-tool line (shell or pnpm). The auditor is checked for protected config and writes outside its worktree
  (and, in ship, in the run's other worktrees), not for other edits inside its own.
- **Audience.** The owner. No compatibility promises.

See [docs/decisions.md](docs/decisions.md) for why, and the
[v2 design](docs/superpowers/specs/2026-10-17-rein-v2-orca-design.md) for how.

## 📄 License

MIT — see [LICENSE](LICENSE).

<div align="center">
<sub>Built with Claude Code, reviewed by an agent that wrote none of it.</sub>
</div>
