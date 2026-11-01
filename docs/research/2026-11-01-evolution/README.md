# Evolution — evidence and acceptance (2026-11-01)

## Phase 0: observed architecture

Baseline checkout: `c3a03cc`, clean `main`, equal to `origin/main` after fetch.
Work branch: `codex/native-evolution`. No push, deployment or global installation.

- `plugins/rein/bin/rein` is the CLI; `__init__.py` loads that same script from a wheel.
  `pyproject.toml` declares zero runtime dependencies. Seven shared skills live in
  `plugins/rein/skills`; the existing marketplace and manifest target Claude.
- `ship_decide.py` is a pure event/action state machine. `ship.py` owns side effects,
  persistent state, task gates, full gates and audit. `orca_client.py` owns transport.
  `quick_driver.py` implements the existing single-worker lane. These are reusable.
- `quota.py` already parses Orca accounts, selects implementers, brakes and computes
  percentage deltas. Unknown values are currently allowed through the brake; Quick's
  reviewer can select Opus on completely unknown limits. This is a real policy gap.
- `failure_triage.py`, gate cache, guard and state-bound review already cover retry
  signatures, environmental errors and verification. Do not replace these with a loop.
- Discover is currently read-only guidance. It requires no plan artifact, but recommends
  workers based on file count and assumes CodeGraph. Neither is necessary for impact work.
- Native execution is already recommended in README and October simplification docs;
  there is no general deterministic impact/routing contract yet.

## Local work and previous experiments

`feat/lean-launch-profile` and `p0-simplify` are ancestors of main. The separate
`luisfarfan/fix-e2e-round4` worktree has three unpublished recovery commits:
`8f11147`, `3197ffe`, `a07c4b4`. They add mid-task quota recovery, reassignment and
uncertain launch handling, but diverge from main's later guard/triage work. Preserve
that branch; do not bulk cherry-pick or replace main with it. Reconcile separately.

Read the October 26 architecture study and October 28/30 pilot closure. The latter
has three tasks, four arms, one run/cell, an invalid orchestration cell, flaky full
gates and contaminated evaluations. It supports selective planning and caution
about orchestration; it does not establish statistical superiority or token savings.
The October 26 study also does not justify mandatory graph retrieval or shared memory.

## Minimum design and acceptance, before implementation

1. P0: expose the same seven skill bodies through repository-local Codex discovery
   and a Codex manifest; keep Claude's names and CLI. Add a small shared provider
   guide, package it, and test actual Codex discovery, Claude validation and wheel
   execution. No copied skill trees, global configuration or new runtime.
2. P1: bounded current-session reconnaissance and evidence for each impact dimension;
   UNKNOWN remains explicit. Deep Discover stays read-only, explicitly invoked.
3. P2: deterministic recommendation with separate planning, execution, verification
   and iteration fields. Evidence is supplied by the current agent, never inferred
   from the task's wording. Missing evidence cannot classify a task as low risk.
4. P3: fix demonstrated unknown-quota routing conservatively, preserve brakes and
   expose supported metadata without claiming percentages equal tokens or dollars.
5. P4/P5: reuse existing bounded triage. Study external optimizers before installing
   anything; no recurrent experiment or API-backed benchmark without a budget.

## Compatibility sources inspected

Installed: Codex CLI 0.166.0, Claude Code 2.1.316, Orca 1.4.245. Orca's installed
`skills get orca-cli --json` is the version-matched interface guide. Codex app-server
JSON schemas were generated locally; CLI help was checked, not assumed.

- [Codex skills](https://learn.chatgpt.com/docs/build-skills): repository discovery
  under `.agents/skills`, including symlinked directories; explicit `$name` use.
- [Plugin packaging](https://developers.openai.com/plugins/build/plugins): shared
  skills directory; `.codex-plugin/plugin.json` remains a compatibility format.
- [Claude plugins](https://code.claude.com/docs/en/plugins): skills packaged in a
  plugin directory. Local validation is available in the installed CLI.
- [Codex App Server](https://learn.chatgpt.com/docs/app-server): inspect installed
  schemas before relying on protocol methods. Orca remains the execution owner.

## Not building

No daemon, graph database, shared memory service, mandatory router LLM, separate
mini/deep agent pair, fresh orchestrator, or new loop runtime. No replacement of
Orca terminal management. No model-name migration based on guessed aliases.

Baseline executed: `python3 -m unittest discover -s tests -q`: **1944 tests,
186.345 seconds, OK, skipped=2**. Existing ResourceWarnings occur in round6 tests.
Pre-change Codex `skills/list` returned no REIN skill from this checkout.

## P0 validation

20 focused tests passed. Wheel built/installed in a temporary venv: 4 packaging
checks passed (the system interpreter lacks `build`; no global install was made).
Codex app-server `skills/list` actually returned all seven canonical paths, with
`rein:` namespacing and no errors. Claude plugin validation succeeded; its only
catalog advice was a missing icon. Skill Creator validation succeeded for Discover.
See `tests/probe_native_skills.py` for the repeatable no-model discovery probe.

Live initiating-session/worker acceptance remains pending. Read-only Orca query
reported Claude weekly 91%, Codex weekly 13%, session windows absent. Claude is
above the existing 90% brake. No worker or model benchmark was started. This is
packaging/discovery validation, not a claim that both live workflows completed.

## P1 implemented

Discover now uses bounded reconnaissance in the current session, traces relevant
boundaries and treats absence of evidence as UNKNOWN. Explicit Discover remains
read-only. `impact.py` normalizes an optional report with independent dimensions;
it does not claim to inspect code or prove a citation. Three assessment regression
cases plus skill/provider checks passed (21 tests total). These are policy tests,
not evidence that a real agent correctly discovers every hidden dependency.

## P2 implemented

`rein route` accepts the investigating agent's evidence and emits deterministic
advice. Planning, execution, audit, unknowns, capacity and reconsideration triggers
are separate. It never dispatches, queries accounts or starts a loop. Existing
Quick/Ship keep their explicit contracts; the shared provider guide links routing.
Seven routing tests (including real CLI invocation) plus impact/surface checks
passed. Unknown impact is not ready; missing measured capacity blocks a delegated
recommendation. This is advisory integration, not automatic interception of every
natural-language task or a proof of model behavior.

## P3 implemented and bounded

Reproduced before changes: `quick.reviewer(parse_rate_limits({}))` chose Opus;
unknown Codex outranked known available Antigravity. Both now have regression
coverage. Provider-limited status without percentages also brakes; invalid numbers
are unknown; reset deltas are not negative usage. `rein route` exposes sanitized
plan/window/reset evidence and explicit plan labels without deriving capacity.
Unknown Ship capacity is limited to one active worker. 210 focused quota, Quick,
routing and Ship decision tests passed before full regression validation.

Compatibility boundary: explicit legacy implementer flows may still make one
attempt on an unknown provider; this is an unknown-capacity fallback, not a claim
of available quota. The advisory route refuses delegated readiness without known
capacity. Full mid-task recovery remains the separate unpublished branch's work.
No daemon, private endpoint, credential extraction, global setting, API spending
or scheduled lab was added. Orca remains the quota and execution integration.

## P4/P5 decisions and actual architecture

[Loop and laboratory study](LOOPS-LAB.md): retain native Goals / existing bounded
Ship triage; no extra loop runtime. Harness² adds candidate optimization but does
not yet justify an integration or PoC on REIN's available evidence.
[Evaluation ledger](EVALUATION.md): prior A/B limitations, executed policy checks,
and a proposed held-out protocol. No new agent benchmark ran.

Implemented flow: shared SKILL.md → current-session reconnaissance → optional
`impact.assess` / `rein route` advice → native work or existing Quick/Ship → existing
gates/review. Orca still owns workers and terminals. Provider manifests and repo
symlinks are adapters; there is one Python core and no new runtime dependency.

## Review corrections

The first full regression run executed 1964 tests and found five failures: the
README command index missed `route`, and four prose-contract tests expected the
previous Discover wording. The command index was fixed. Discover's tests now
assert the same read-only/no-plan guarantees and the intended bounded `rg` search
policy instead of mandatory CodeGraph/worker references.

Review also added regressions for evidenced high uncertainty (still investigate)
and a limited current provider (capacity required without automatic delegation).
Quick's description now distinguishes an explicit delegated workflow from an
ordinary request for a small native change. `_number` remains a general numeric
helper so the unpublished recovery branch's reset timestamps remain compatible;
percentage validation is separate.
