# rein (plugin)

Plugin internals. For what this is and why, see the [repository README](../../README.md).

## Layout

```
plugins/rein/
├── .claude-plugin/plugin.json    name: "rein" -> the /rein:* namespace
├── commands/rein-ping.md              /rein:rein-ping — plumbing self-check for fresh installs
├── skills/                       /rein:rein-plan · /rein:rein-apply · /rein:rein-step
│                                 /rein:rein-steps · /rein:rein-audit · /rein:rein-role
│                                 /rein:rein-discover · /rein:rein-linear
├── workflows/loop.js             the bounded loop (phase 0: resolution stub)
├── lib/detect.py                 stack + command resolution
├── lib/token_report.py           per-model token accounting + ledger
├── lib/linear_client.py          Linear GraphQL: read, move state, comment
├── lib/linear_issue.py           one issue -> its fields (pure)
├── lib/linear_select.py          which issues, in what order (pure)
├── lib/linear_intake.py          take an issue, and put it back when it lands
├── bin/rein                      CLI: doctor · detect · token-report · ledger
│                                      linear · intake · land
├── bin/token-report              alias, in case plugin bin/ lands on PATH
├── settings.json                 empty by default (see note)
└── flow.config.example.json      every option, annotated
```

## The Linear side

`linear_*` is four modules with one rule between them: **Linear is the only
source** (D3). Nothing reads a second repository, so no clone has to be current
for an intake to run.

```
rein linear list|show|comment      read the board, and comment on it
rein intake <ID>                   Beads issue (opt-in, D6), branch, In Progress
  … rein-plan / rein-apply / rein-audit — the ordinary flow, unchanged …
rein land <ID>                     Beads closed, Done. Refuses unmerged work (D5)
```

All of them need `REIN_LINEAR_API_KEY` in the environment; it is read from
nowhere else, and no code path deletes anything in either system.

`intake` files a Beads issue only where `flow.config.json` says
`tracker.kind: "beads"` — see D6. Everything else about the intake is the same
either way.

## Constraints that shaped this

**Workflow scripts have no filesystem and no Node APIs.** Everything the workflow
needs to know about the project has to come back through an agent. That is why
`rein detect` exists as a CLI: one bash round-trip returns the resolved stack and
commands, instead of an agent spending turns rediscovering deterministic facts.
Fewer turns is the entire thesis.

**A plugin's `settings.json` only supports `agent` and `subagentStatusLine`** — no
`env`, `permissions` or `model`. Model routing therefore stays per-agent inside the
workflow (`opts.model`), which is the correct mechanism anyway: a per-invocation
model overrides the global `CLAUDE_CODE_SUBAGENT_MODEL`. The file ships empty until
its exact schema is verified in phase 1 — an unverified settings file would
confound the phase-0 load test.

**No runtime dependencies.** Python 3 stdlib only. A plugin that drags a toolchain
along at install time is a plugin nobody installs.

## Local development

Add the marketplace by local path so iterating does not require a push:

```bash
/plugin marketplace add /absolute/path/to/rein-agentic-kit
```

```bash
/plugin install rein@rein-agentic-kit --scope project
```

Check the wiring from inside a consuming project:

```bash
"$CLAUDE_PLUGIN_ROOT"/bin/rein doctor
```

## Phase 0 acceptance criteria

| # | Claim | How it is falsified |
|---|---|---|
| A1 | The plugin loads in a second project | `/rein:rein-ping` responds; and: does the marketplace **copy** the repo or read it **live**? |
| A2 | A plugin-shipped workflow resolves | `Workflow({name:'rein-loop'})`; if it fails, `{scriptPath:'${CLAUDE_PLUGIN_ROOT}/workflows/loop.js'}` |
| A3 | The plugin's `bin/` reaches `$PATH` | bare `rein doctor` works from another project |
| A4 | Config is read from the **consuming** project | the stub reports that project's stack and commands |

A2 and A3 are genuinely open — neither is documented behaviour, and both have a
working fallback. Which one holds decides how the commands are written.
