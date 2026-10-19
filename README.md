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
   verification command and a `Repo:`. Nothing is written without your confirmation.
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
| 🚢 **`/ship`** (`/rein:ship`) | Open a detached Orca tab running `rein ship <change>`; you can close the session. Never edits code or opens a PR |
| 💬 **`/answer`** (`/rein:answer`) | "¿Qué me pregunta rein?" / "respóndele: …": show and answer the run's pending gates |
| ⚡ **`/quick`** (`/rein:quick`) | "Tarea rápida: …": hand a small change to a cheap agent in its own tab, no plan. You talk to it; "listo" runs the light gate. Never edits code itself |
| 🔍 **`/rein:rein-audit`** | The independent gate: mechanical checks first, then five-axis judgement |
| 🎭 **`/rein:rein-role`** | Assign this session's role: planner · implementer · reviewer |

### CLI — the deterministic half

```bash
rein ship <change> [--root <plan repo>] [--agent <id> [--model <m>]] [--dry-run | --detach]
                 # drive a plan through Orca workers: dispatch, gate each task, audit.
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
rein quick "<sentence>" [--repo <name>] [--base <branch>] [--here [--dirty-ok]] [--agent <id> [--model <m>]] [--linear <id>]
                 # hand a small change to a cheap agent chosen by quota, in a tab you talk to.
                 # Default: its own worktree off develop, or the remote's default branch when there is none; --here uses the main checkout
                 # (--dirty-ok if it has uncommitted work). Never pushes or merges.
                 # Exit: 0 started · 1 failed · 2 usage · 126 environment
rein quick done  # run by that agent on "listo": lint/typecheck plus the tests related to the
                 # changed files; an audit only if a risky path changed. Never commits.
                 # Exit: 0 green · 1 red · 2 no quick task here · 126 setup (not a verdict)
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
                 # --plan: run each TASK's own Verification before implementers are paid.
                 # Failing is fine (the work does not exist yet); proving NOTHING is not —
                 # a missing test module or a selector matching zero tests exits non-zero
rein plan-check  # mechanical findings on a drafted plan's own text. Always exits 0 (it runs
                 # inside the planning skill); `--gate` exits 1 on a BLOCKING finding, which
                 # is how `rein ship` refuses a bad plan. `--tasks T001,T002` scopes it
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
    "gateTimeoutSeconds": 900,
    "largeChangeFiles": 25,
    "riskyPaths": ["**/auth/**", "**/payments/**", "**/migrations/**"]
  }
}
```

The `ship` block shows its defaults. `maxRetries` is how many times a red task is retried with a
fresh worker, `maxAuditRounds` bounds audit and fix rounds, `taskTimeoutMinutes` opens a gate
instead of waiting forever, `gateTimeoutSeconds` is the per-command timeout of each task's gate
(raise it for a slow suite), and a change that touches `riskyPaths` or more than `largeChangeFiles`
files gets an Opus audit.

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
