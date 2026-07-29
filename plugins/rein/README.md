# rein (plugin)

Plugin internals. For what this is and why, see the [repository README](../../README.md).

## Layout

```
plugins/rein/
├── .claude-plugin/plugin.json    name: "rein" -> the /rein:* namespace
├── commands/ping.md              /rein:ping — plumbing self-check for fresh installs
├── skills/plan|loop|review/      /rein:plan, /rein:loop, /rein:review
├── workflows/loop.js             the bounded loop (phase 0: resolution stub)
├── lib/detect.py                 stack + command resolution
├── lib/token_report.py           per-model token accounting + ledger
├── bin/rein                      CLI: doctor · detect · token-report · ledger
├── bin/token-report              alias, in case plugin bin/ lands on PATH
├── settings.json                 empty by default (see note)
└── flow.config.example.json      every option, annotated
```

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
| A1 | The plugin loads in a second project | `/rein:ping` responds; and: does the marketplace **copy** the repo or read it **live**? |
| A2 | A plugin-shipped workflow resolves | `Workflow({name:'rein-loop'})`; if it fails, `{scriptPath:'${CLAUDE_PLUGIN_ROOT}/workflows/loop.js'}` |
| A3 | The plugin's `bin/` reaches `$PATH` | bare `rein doctor` works from another project |
| A4 | Config is read from the **consuming** project | the stub reports that project's stack and commands |

A2 and A3 are genuinely open — neither is documented behaviour, and both have a
working fallback. Which one holds decides how the commands are written.
