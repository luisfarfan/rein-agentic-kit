# Phase 0 — plumbing findings

Measured against Claude Code **2.1.220** on macOS, installing this repo as a local
marketplace. Everything below is observed behaviour, not documentation.

## A1 — the plugin loads ✅ (with a caveat that matters)

```bash
claude plugin marketplace add /path/to/rein-agentic-kit
claude plugin install rein@rein-agentic-kit --scope user
```

Both work non-interactively, so plugin setup is scriptable — `/plugin` is not the
only path.

**The marketplace is read live from the source directory**, but the **plugin itself
is copied** to:

```
~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/
```

The copy is verbatim: `workflows/`, `bin/` and `lib/` are all carried over even
though only some directories are recognised component types.

### The caveat: the cache is pinned by version

Editing the source and running `claude plugin marketplace update` **or**
re-running `claude plugin install` does **not** refresh the cached copy while
`version` stays the same. Verified with a marker file: still absent after both.

Two working dev loops, with different trade-offs:

| Loop | How | Use when |
|---|---|---|
| **Immutable copy** (default) | `rm -rf ~/.claude/plugins/cache/rein-agentic-kit/rein/<v> && claude plugin install rein@rein-agentic-kit --scope user` | Always, and **mandatory** when the loop edits this repo — the running plugin must not be the code being edited |
| **Live symlink** | `ln -s <repo>/plugins/rein ~/.claude/plugins/cache/rein-agentic-kit/rein/<v>` | Fast iteration on prompts/skills. The plugin still reports as enabled. **Do not** use while self-hosting the loop |

This settles the self-hosting question: with the default copy, the loop can safely
implement changes in this repo while the plugin runs from an immutable snapshot
elsewhere. There is no circularity.

## A2 — workflows are NOT a plugin component type ❌ → fallback confirmed ✅

`claude plugin details rein@rein-agentic-kit` reports:

```
Component inventory
  Skills (4)  loop, ping, plan, review
  Agents (0)
  Hooks (0)
  MCP servers (0)
  LSP servers (0)
```

There is no *Workflows* category at all. Confirmed empirically:

```
Workflow({name: 'rein-loop'})
  -> Workflow "rein-loop" not found. Available: deep-research, code-review
```

Name resolution only sees built-ins and `.claude/workflows/` of the current
project. **A plugin cannot register a workflow by name.**

**The fallback works.** `Workflow({scriptPath: '<plugin root>/workflows/loop.js'})`
runs fine — the file is present in the cached copy.

### Consequence for the design

`/rein:apply` must be a **skill that invokes the workflow by path**, resolving the
plugin root at call time. It cannot be a bare workflow name. Two further notes:

- `${CLAUDE_PLUGIN_ROOT}` is not interpolated by the Workflow tool — the skill has
  to resolve the absolute path (e.g. via `rein doctor`, which prints it) and pass
  a literal.
- Skills are the only viable carrier, which is fine: `commands/ping.md` was itself
  inventoried as a **Skill**, so commands and skills land in the same registry.

## A3 — a plugin's `bin/` DOES reach `$PATH`, but only from the next session ✅

The first test was misleading: `zsh -lic 'command -v rein'` fails, but a login
shell never sees Claude Code's injected `PATH`. Inspecting the actual session
`PATH` shows the real behaviour:

```
…:/Users/lucho/.claude/plugins/cache/addy-agent-skills/agent-skills/<ver>/bin
```

That plugin **does not even have a `bin/` directory** — so Claude Code appends
`<plugin>/bin` to `PATH` for every installed plugin, existent or not, and it
builds that `PATH` **at session start**. `rein` is absent here only because it was
installed mid-session.

So the bare `rein` works from the next session onward. Until then — and for any
process that does not inherit that `PATH` — a deterministic fallback is needed.
`CLAUDE_PLUGIN_ROOT` is **not** it (see A5); the install path is versioned but its
shape is fixed, so a glob resolves it:

```bash
R=$(command -v rein || ls -d ~/.claude/plugins/cache/*/rein/*/bin/rein 2>/dev/null | tail -1)
```

Every skill and command in the plugin uses exactly this line.

## A5 — `CLAUDE_PLUGIN_ROOT` is not exported to processes ❌

Unset in the main session's Bash **and** in workflow subagents — the first probe
run failed all three of its detection paths for this reason alone:

```
(a) rein detect                      -> 127, not on PATH (this session)
(b) $CLAUDE_PLUGIN_ROOT/bin/rein     -> 127, variable unset
(c) python3 $CLAUDE_PLUGIN_ROOT/lib/ -> 2,   resolved to /lib/detect.py
```

`${CLAUDE_PLUGIN_ROOT}` is interpolated in **skill and command markdown**, but it
is not an environment variable a shell or subagent can read. Anything executed —
by a hook, a workflow, or a subagent — must resolve the path itself.

This is why the probe's resolution chain is ordered `PATH → glob →
CLAUDE_PLUGIN_ROOT`, not the other way round.

## A4 — config resolves from the consuming project ✅

`rein detect` was verified against five real projects with different stacks:

| Project | Stack | Runner | Notable |
|---|---|---|---|
| make-montages | python + subtypes `frontend, node, vite` | `just` | `just lint/test/typecheck` correctly beat autodetect; plan source `openspec` |
| llmindex | python / poetry | — | `lint`, `typecheck` honestly reported missing |
| cv | node / npm | — | no scripts: all four slots reported missing, nothing invented |
| fuzo-serverless-platform | node / pnpm | — | jest picked for `testOne` |
| rein-agentic-kit | python (from `flow.config.json`) | — | config overrode autodetect |

Precedence holds: **`flow.config.json` > task runner > autodetect**, with the
source of every command reported so nothing is a black box.

## Measurement — verified on real transcripts

`rein token-report` over an actual 11-agent run reproduces the economics this kit
is built on:

```
cache_read   42,924,483  (93.4%)
cache_write   2,740,911  ( 6.0%)
output          188,048  ( 0.4%)
input           121,410  ( 0.3%)
```

Confirming that output-side optimisation is pointless and cost ≈ turns × context.

The ledger already surfaces a runaway across recorded runs (124 turns/agent,
ctx_max 277,934) — exactly the signal the bounded loop exists to remove, and the
first real dataset for the phase-3 dashboard.

## What phase 1 must carry forward

1. `/rein:apply` invokes the workflow **by absolute path**, never by name.
2. All CLI calls go through `"$CLAUDE_PLUGIN_ROOT"/bin/rein`, with the bare name as
   an optimistic first try.
3. Purge-and-reinstall before any self-hosted run; symlink only for prompt iteration.
4. `settings.json` stays empty until its schema is verified — it is not needed for
   model routing, which lives in `opts.model` per agent.
