# rein-agentic-kit

**Rein — keep your Claude Code agents lean and in control.**

A Claude Code plugin + marketplace packaging a token-lean, stack-aware agentic dev
flow: `/rein:plan`, `/rein:loop`, `/rein:review`, and a `rein token-report` CLI that
measures what a run actually cost — **per model**.

> **Status: phase 1.** The loop, the plan parser and the measurement tool are
> real. Stack-specific toolsets and the dashboard are not yet. See
> [Roadmap](#roadmap).

## Why

Optimizing agent cost by "making the model write less" does nothing. Measured over
real Claude Code transcripts:

| Where the tokens go | Share |
|---|---|
| `cache_read` (context re-read every turn) | **~90%** |
| `cache_write` | ~10% |
| **output** | **~0.3%** |
| fresh input | ~0% |

So **cost ≈ turns × context size**. One agent that ran 241 turns, re-reading ~234k
of context on every turn, was most of a 112M-token run. And Claude Code has **no
native eviction of stale tool results** — only `/compact`, which is lossy and
breaks the prompt cache. A long agent's context only grows.

Three levers move the needle. Everything in this kit is one of them:

1. **Bounded loop of fresh agents.** Each task is implemented by at most *N* short,
   fresh agents handing off a **compact ledger** (`progress` / `remaining` /
   `filesTouched` / `verification`) — not one agent running 200+ turns. Context
   resets at every boundary, so spend stops growing without a ceiling.
2. **Per-agent model routing.** Mechanical work → Haiku, code → Sonnet, review →
   Opus. On a subscription this does not lower the bill (it is fixed) — it frees
   the **scarce Opus quota**, which is the limit you actually hit.
3. **Retrieval discipline + honest measurement.** Graph-first orientation, read
   symbols not whole files, small command output, few turns — and a token report
   broken down **per model**, because the total is not what limits you.

Measured effect of applying these: **241 turns → 26**, **Opus 100% → 0%**, **~7×
less context per turn**.

### Deliberately not included

Tried and discarded **with data**, not taste:

- **Output compressors** — attack the 0.3%.
- **Multi-provider API swarms** — the saving does not apply on a subscription, and
  more agents means more contexts re-read.
- **Cross-session memory tools** — orthogonal to the actual cost driver.
- **Cache-aware proxies** — no-op on already-cached traffic.

## Install

```bash
/plugin marketplace add luisfarfan/rein-agentic-kit
```

```bash
/plugin install rein@rein-agentic-kit --scope user
```

Use `--scope project` to enable it for one repository only.

## Configure

Everything is optional — what you omit is autodetected. Drop a `flow.config.json`
at your project root to override:

```json
{
  "commands": {
    "test": "uv run pytest -q",
    "testOne": "uv run pytest -q {target}",
    "lint": "uv run ruff check .",
    "typecheck": "uv run mypy ."
  },
  "models": { "aux": "haiku", "impl": "sonnet", "review": "opus" },
  "limits": { "maxTaskSteps": 8, "maxReviewRounds": 3 }
}
```

Resolution precedence: **`flow.config.json` > task runner (`justfile` /
`Makefile` / `Taskfile`) > autodetection.** A project that already declares how it
is built is not second-guessed.

Check what was resolved:

```bash
rein doctor
```

## Supported stacks

| Stack | Detected by | Verification |
|---|---|---|
| Python | `pyproject.toml`, uv / poetry | pytest, ruff, mypy |
| Node / JS / TS | `package.json`, pnpm / npm / yarn / bun | vitest or jest, eslint, tsc |
| Rust | `Cargo.toml` | cargo test / clippy / check |
| Go | `go.mod` | go test / vet |
| **Frontend** (subtype of Node: Next, Vite, Astro, SvelteKit, Nuxt, Remix) | dependencies | **real rendered verification** — unit tests alone do not catch "the tests pass but the UI is broken" |
| Serverless / infra | `serverless.yml`, `sst.config.ts`, `*.tf`, `Dockerfile` | `plan` / `validate` — **never deploy** |

Optional tools (`graphify`, `openspec`, `serena`, `bd`) are **probed, never
required**: if one is absent the flow degrades, it does not break.

## Measure

```bash
rein token-report
```

Reads Claude Code's own JSONL transcripts (`usage`, including `cache_read`) and
breaks the run down per agent and **per model**. The workflow runtime's
`budget.spent()` counts only output and cannot be used for this.

Each run is summarized into a ledger at `~/.claude/rein/runs.jsonl`, so history
survives transcript rotation:

```bash
rein ledger
```

**On the word "savings":** this tool measures *consumption*. A saving requires a
baseline to compare against. Without one, the honest numbers are the three that
predict cost — **turns/agent**, **ctx_max/turn**, **% of tokens on Opus**.

Mark a run as that baseline and `rein ledger` decorates every later run in the
*same project* with a signed delta against it (negative = better):

```bash
rein baseline mark [wf_id]   # defaults to the most recent run
rein baseline show
rein baseline clear
```

The baseline is scoped to the project it was marked in — the ledger is global
across all projects, so a delta is only ever shown against a run from the same
project it was recorded in.

## Roadmap

| Phase | Scope | State |
|---|---|---|
| 0 | Scaffold, plugin plumbing, `token-report` + ledger, stack detection | ✅ done — [findings](docs/phase-0-findings.md) |
| 1 | Config-driven core loop + `tasks.md` adapter — **measured** | in progress |
| 2 | Stack toolsets; frontend rendered verification | |
| 3 | Local dashboard: metrics per project/session, per-agent model config | |
| 4 | Docs, polish, public marketplace | |

## License

MIT
