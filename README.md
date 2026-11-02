# REIN

**English** | [Español](README.es.md)

**Evidence, verification, and control for development with coding agents.**

REIN is a set of skills and a CLI for **Claude Code and Codex**: understand the
impact of a change, turn requirements into verifiable tasks, implement with the
right level of coordination, and get an independent review when the risk calls
for one.

Your current session is the starting point. REIN provides shared guidance for
investigating, deciding, implementing, and checking the result. When delegation
helps, it uses **Orca** to manage workers, worktrees, and owner decisions.

[![CI](https://github.com/luisfarfan/rein-agentic-kit/actions/workflows/ci.yml/badge.svg)](https://github.com/luisfarfan/rein-agentic-kit/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](pyproject.toml)
[![Runtime dependencies](https://img.shields.io/badge/runtime%20deps-none-success.svg)](pyproject.toml)

## What it helps you do

### Understand what actually needs to change

Before implementation, REIN guides the agent to inspect code and tests,
distinguish existing capabilities from missing behavior, and trace the consumers
of a change. A UI request may depend on something the backend already supports;
a small edit may affect permissions, money, or persistence.

When an unanswered question could change the solution, Discover investigates that
question, reuses existing findings, and makes uncertainty explicit. An explicit
investigation-only request stays read-only. If you need a structured report,
`discover-check` checks whether its cited file evidence is still current before
you reuse it.

**You get a clearer scope and a concrete account of the questions to resolve
before changing code.**

### Turn requirements into work you can verify

Plan breaks a change into tasks with acceptance criteria, a repository,
dependencies, and an executable verification. It supports a plain `tasks.md` or
an OpenSpec structure. You review the draft before the plan is written.

The CLI checks mechanical aspects of the plan, such as invalid dependencies and
problems with its verifications. The agent judges whether the criteria actually
cover the request.

**You get a concrete definition of done and a way to check each part.**

### Choose a process that fits the work

A localized fix can stay in the current session. A change with requirements and
dependencies may need a plan. A change to authorization or persistent state needs
independent review. Work that benefits from parallel execution or recovery may
justify Ship.

Once adopted by a project, the entry policy guides those decisions from an
ordinary request. When you need a structured decision record, `rein route` offers
recommendations based on a report. It is optional and starts no workers.

**You can use the capabilities separately and add coordination when it has a
concrete purpose.**

### Verify results with observable evidence

`rein gate` runs the project's checks and distinguishes a pass, a failed check,
and an environment that could not run the check. A missing tool or a suite that
never ran gets its own diagnosis.

Commands come from project configuration, task runners, or stack detection.
Delegated execution uses checks per task and full checks per repository. Passing
results can be reused while the code state, command, and environment match.
Frontend verification policy accounts for evidence of a rendered UI, which must
be obtained and supplied separately.

**You can see what was checked, what failed, and what remains unproven.**

### Keep implementation and review independent

Auditing combines mechanical checks with the judgment of a session independent
of the implementer. It is part of the method for changes involving security,
permissions, money, persistence, concurrency, or contracts between services.

Blocking findings require a confirmed defect and concrete evidence. Approval is
bound to the reviewed state through hashes: changing reviewed files makes that
approval stale.

**A review has a verifiable scope and must be refreshed when the code it approved
changes.**

### Coordinate delegated work without losing decisions

Quick lets you explicitly delegate a small change to another agent. Ship
coordinates a plan through Orca: it respects dependencies, launches workers with
fresh context per task, runs gates, manages retries, and requests an audit.

Ship saves its state so it can resume. Watchers and Orca decision gates surface
questions, blockers, and pending decisions. You can inspect and answer them from
the initiating session. Human-review tasks can also produce a review sheet with
criteria and evidence.

**You can follow a long run, intervene where needed, and recover its state without
reconstructing the entire conversation.**

### Keep delegation within explicit limits

REIN checks for changes to protected configuration, reruns selected hooks, and
detects activity outside the worktree in watched checkouts. Per-worktree
environment variables can separate test resources, such as databases, and gate
logs preserve a record of the checks.

Worker selection uses Orca's quota observations and configured limits. Unknown or
stale capacity cannot authorize new workers. Explicitly selecting a provider
preserves those controls.

**Delegation has inspectable limits on scope, capacity, and escalation.** These
checks detect certain violations after the work; REIN does not provide a sandbox.

## Choose the capability for your situation

| Situation | Useful capability | Expected result |
| --- | --- | --- |
| The scope is clear and the change is localized | Current session with the entry guidance | Implementation and relevant verification |
| You need to determine whether a capability is missing or where a problem lies | Discover | Evidence, uncertainty, and the next check |
| Requirements or dependencies need organizing | Plan | Tasks with criteria and verifications |
| The change crosses sensitive boundaries | Audit, from another session | Findings and a verdict on the reviewed state |
| You want another agent to handle a small change | Quick | An Orca worker, normally in its own worktree |
| Parallel work or a recoverable run justifies coordination | Ship | Plan coordination, gates, and audit |
| Delegated work needs your decision | Answer and Watch | Visible questions and recorded answers |

For example:

- **“The menu doesn't show an action the user is allowed to perform.”** Inspect
  the UI, permissions, and existing contract; resolve it in the current session
  when the evidence supports a localized fix.
- **“Prevent duplicate charges when a payment is retried.”** Investigate
  concurrency, persistence, and external effects; prepare a verifiable plan and
  get an independent audit.
- **“Implement these independent tasks and tell me if something gets blocked.”**
  Use Ship when coordinating workers offers a concrete advantage.

## Getting started

### 1. Make the skills available to your agent

For Claude Code, install from the marketplace:

```sh
claude plugin marketplace add luisfarfan/rein-agentic-kit
claude plugin install rein@rein-agentic-kit --scope project
```

To update that installation:

```sh
claude plugin marketplace update rein-agentic-kit
claude plugin update rein@rein-agentic-kit
```

You can also load the plugin directly from a local checkout:

```sh
claude --plugin-dir /absolute/path/to/rein/plugins/rein
```

For Codex, this repository exposes the same seven skills through
[`.agents/skills`](.agents/skills) and the plugin manifest. To use them in another
project, follow the [local installation and provider guide](docs/native-providers.md).
Both providers share the same core in `plugins/rein`, including the same scope,
verification, and independent-review rules.

### 2. Adopt the project entry guidance

Merge one copy of [`ENTRY.md`](plugins/rein/ENTRY.md) into the project's
`AGENTS.md`, preserving its existing instructions. For Claude, add `@AGENTS.md` to
the neighboring `CLAUDE.md` when needed. Start a fresh session in the correct
checkout or worktree and describe the task normally.

**Installing the plugin exposes the skills; adopting the guidance enables the
shared approach to ordinary tasks.** REIN does not edit those files automatically.
The guidance is an instruction to the agent. Compliance also depends on the
provider and the project's other instructions.

You can invoke a capability directly:

| Skill | Claude Code | Codex |
| --- | --- | --- |
| Investigate | `/rein:rein-discover` | `$rein:rein-discover` |
| Plan | `/rein:rein-plan` | `$rein:rein-plan` |
| Delegate a small change | `/rein:quick` | `$rein:quick` |
| Coordinate a plan | `/rein:ship` | `$rein:ship` |
| Audit | `/rein:rein-audit` | `$rein:rein-audit` |
| Answer questions | `/rein:answer` | `$rein:answer` |
| Assign a session's role | `/rein:rein-role` | `$rein:rein-role` |

Standalone installation may expose names without the prefix. Use the skill picker
to resolve ambiguity.

### 3. Use the CLI and, for delegation, Orca

The CLI requires **Python 3.9 or newer** and has no runtime dependencies.
To install from this checkout in an isolated environment:

```sh
python3 -m venv .venv
.venv/bin/pip install .
.venv/bin/rein doctor .
```

You can also run the source directly:

```sh
python3 plugins/rein/bin/rein doctor .
```

For ordinary terminals, install the CLI with `uv tool install rein-agentic-kit`
or `pipx install rein-agentic-kit`. Your project's test and build tools are
installed separately.

Quick and Ship require **Orca**, its CLI, and repositories registered there.
Ship runs inside an Orca terminal; `rein ship <change> --detach` opens that
terminal. Local checks such as `doctor`, `detect`, and `gate` work without Orca.

## Configuration and checks

REIN respects this precedence: **`flow.config.json` → project task runner → stack
detection**. It recognizes Python, Node/TypeScript, Rust, and Go, along with
frontend and infrastructure subtypes. Monorepos with multiple candidates require
an explicit subproject selection.

The [example configuration](plugins/rein/flow.config.example.json) and
[operational reference](docs/reference.md#configuration) cover commands,
per-worktree environments, execution limits, and protections.

```sh
rein doctor .                            # Inspect stack, commands, and configuration
rein gate .                              # Run checks and decide whether they pass
rein plan-check tasks.md --root . --gate  # Check a plan mechanically
rein review check                        # Check whether the review is still current
```

`rein gate` returns `0` for a pass, `1` for a failure, and `126` when it cannot
verify the result. `rein verify` diagnoses whether commands can run: its exit code
does not certify that tests passed. See the [command reference](docs/reference.md#cli)
for flags and complete workflows.

## Evidence and current limits

The repository includes regression tests, Orca fixtures, and evaluations with
reports and traces. The [entry-policy validation](docs/research/2026-11-01-entry/RESULTS.md)
records bounded cases of native execution and selective investigation in Codex.
The [acceptance matrix](docs/research/2026-11-01-evolution/ACCEPTANCE.md) distinguishes
automated checks from real integrations and retains pending Claude and
cross-provider Quick checks.

A [comparison with native execution](docs/research/2026-11-01-followup/RESULTS.md)
produced the same functional behavior on a small task, with extra process work
when reports and routing were added. The
[planning, audit, and orchestration evaluations](docs/research/2026-10-28-post-sdd/CIERRE-PROVISIONAL-FASE2.md)
inform selective use of those layers. Available evidence does not establish a
general improvement in quality, speed, or savings.

Keep these limits in mind:

- Audits can miss defects. Approval applies to the reviewed state and scope.
- Worktree protections observe watched checkouts through Git. Ignored files and
  changes outside those repositories are outside that detection.
- Ship ends in review. Publishing, opening a PR, merging, and deploying remain
  outside its execution. `rein done --push` allows only an optional push of the
  plan-archive commit, with its own restrictions.
- Quota checks depend on usable Orca observations. REIN can resume dispatch when
  valid data arrives, but cannot guarantee that the source refreshes or that
  enough capacity remains to finish a task.

Consumption measurement remains available through `token-report`, `ledger`, and
`baseline`. Token reports read Claude Code transcripts; they are not complete
accounting for all Codex sessions. Historical studies are linked from the research
documentation.

## Documentation and development

- [Installation, providers, and project adoption](docs/native-providers.md).
- [CLI, configuration, and delegated execution reference](docs/reference.md).
- [Shared core structure](plugins/rein/README.md).
- [Project decisions](docs/decisions.md).
- [Evolution and evidence record](docs/research/2026-11-01-evolution/README.md).

Run the regression suite from the repository root:

```sh
python3 -m unittest discover -s tests -q
```

[MIT license](LICENSE).
