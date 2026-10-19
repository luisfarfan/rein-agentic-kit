# rein (plugin)

Plugin internals. For what this is and why, see the [repository README](../../README.md).

## Layout

```
plugins/rein/
├── .claude-plugin/plugin.json    name: "rein" -> the /rein:* namespace
├── skills/                       /rein:ship · /rein:rein-discover · /rein:rein-plan
│                                 /rein:rein-audit · /rein:rein-role
├── lib/detect.py                 stack + command resolution (flow.config > task runner > autodetect)
├── lib/verify.py                 runs resolved commands: ok / failed / not invocable / timeout
├── lib/gate.py                   0 / 1 / 126 gate decision; review episodes bound to file hashes
├── lib/plan.py                   openspec and flat tasks.md plans; the `Repo:` field
├── lib/plan_check.py             blocks plans with unbacked criteria or bad dependencies
├── lib/roles.py                  implementer / reviewer profiles sent to workers
├── lib/ship.py                   the `rein ship` driver (thin loop around the decision core)
├── lib/ship_decide.py            pure decision core: (state, event) -> action
├── lib/ship_state.py             .rein/ship/<change>.json: plan tasks -> Orca task / dispatch ids
├── lib/orca_client.py            the only module that shells out to `orca`
├── lib/quota.py                  reads `orca account list`: implementer choice, brakes, usage delta
├── lib/migrate_config.py         v1 -> v2 flow.config.json rewrite
├── lib/token_report.py           per-model token accounting, ledger, baseline
├── lib/serve.py                  dev-server lifecycle for rendered verification
├── bin/rein                      the CLI (see the repository README for every subcommand)
├── settings.json                 empty by default (see note)
└── flow.config.example.json      every option, annotated
```

## Constraints that shaped this

**Orca is the runtime.** Rein decides (what is ready, what passed, who reviews); Orca runs
(worktrees, worker terminals, gates, quota). `orca_client.py` is the only place that spawns
`orca`, so everything else is testable against recorded JSON. Design:
[`docs/superpowers/specs/2026-10-17-rein-v2-orca-design.md`](../../docs/superpowers/specs/2026-10-17-rein-v2-orca-design.md).

**A plugin's `settings.json` only supports `agent` and `subagentStatusLine`** — no `env`,
`permissions` or `model` — so it ships empty. Model choice belongs to `rein ship`, per worker.

**No runtime dependencies.** Python 3 stdlib only. A plugin that drags a toolchain along at
install time is a plugin nobody installs.

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

Run the tests with `python3 -m unittest discover -s tests -q`.
