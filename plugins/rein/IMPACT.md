# Evidence-backed impact

## Progressive Discover (v2)

The current session investigates the natural-language request. There is no second
classifier or scanner. Default limit: eight investigation tool calls / 180 seconds;
reuse current context and batch focused reads. Unknowns at the limit remain UNKNOWN
with a specific next check. This is a host instruction, not a hidden timer or hook.

For repeatable evidence, supply `schema_version: 2`. A report separates:

- `classification`: `implementation`, `risk`, `uncertainty`, `repo_scope`,
  `planning`, `independent_verification`. Each has `level`, `reason`, `evidence` IDs.
  Levels are `low`, `high`, `unknown`, `not_applicable`. Repo scope low = one repo,
  high = multiple repos; planning/review high = needed, low = not currently needed.
- `findings`: lists named `existing`, `missing`, `dependencies`, `risks`. Each item
  has `claim`, `kind` (`observed` or `inference`), and evidence IDs. A relationship
  names its producer and consumer. For local-only work, explain the checked local
  boundary instead of inventing a cross-repo dependency. Empty missing/risks lists
  are allowed; capability and dependency evidence are required for sufficiency.
- `risk_facets`: optional specific high/low/unknown assessments of `operations`,
  `persistence`, `security`, `concurrency`, `contracts`, `tests`, `side_effects`,
  `money`. High facets cannot be cancelled by a low aggregate risk.
- `questions`: unresolved `{ "question": "...", "next_check": "..." }` entries.
  Open questions prevent implementation readiness; do not conceal them to get green.
- `evidence`: IDs mapped to `{ "repo": ".", "path": "settings.py",
  "sha256": "<hash of the exact file inspected>", "lines": [1, 12],
  "symbol": "SUPPORTED_CURRENCIES" }`. `repo` defaults to `.`, symbol is optional.
  Use the host's existing file tools to capture the hash when reading the evidence,
  never refresh hashes blindly to make stale conclusions pass.
- `investigation`: optional observed `steps`, `seconds`, `limit_steps` (default 8),
  `limit_seconds` (default 180). Missing metrics stay null. No confidence percentage.

`rein discover-check report.json --root /repo` checks only cited files (at most 64,
2 MiB each). For other repositories explicitly pass `--repo registered-name=/path`;
roots inside the report are never trusted. Relative paths and symlinks must stay
inside their assigned root. Duplicate file citations are read once. File hashes,
inclusive line ranges and optional symbol text are checked. Missing or stale
evidence makes dependent conclusions unknown; arbitrary commands are never run.
Exit 0 = sufficient supplied evidence, 1 = deeper investigation, 2 = invalid report.

The checker does **not** prove that a claim follows from the code, that a mentioned
test ran, or that an uncited dependency does not exist. Record actual test commands
and outcomes in findings; checking a test file's hash only proves freshness. Reuse
evidence while its inspected files and relevant boundaries remain unchanged. New
dependencies or changed assumptions require targeted reinvestigation even when
all cited hashes still match. Reports are optional and nothing is written by the CLI.

## Legacy declarations (v1, retained)

The current agent investigates the code; Python normalizes its report. This is
not static analysis and does not verify whether cited evidence is true. Keep
references reviewable and include negative evidence before marking a dimension
low or not applicable. A short change may use one reference for several dimensions.

Optional report format (all omitted dimensions become UNKNOWN):

```json
{
  "dimensions": {
    "scope": {"level": "low", "evidence": ["settings/currency.py:42 enables currencies; test_currency.py:18 covers EUR"]},
    "money": {"level": "high", "evidence": ["billing/convert.py:63 rounds settlement amounts"]}
  }
}
```

Levels: `low`, `high`, `unknown`, `not_applicable`. Dimensions: `scope`,
`implementation`, `operations`, `uncertainty`, `persistence`, `security`,
`concurrency`, `contracts`, `tests` (high = inadequate coverage), `side_effects`,
`money`. Missing evidence overrides a claimed low/high/not-applicable value to
UNKNOWN. Inspect uncertain boundaries before simplifying the strategy.

Reassess on a new dependency, changed contract, migration, permission scope,
verification failure or quota change. Reuse already established evidence; do not
repeat full reconnaissance simply because implementation started.

## Explain and reconsider a route

For v2, `rein route report.json --root /repo --agent codex` calls the same freshness
checker; additional roots use `--repo name=/path`. No saved normalized output is
trusted as a substitute for rechecking the original cited hashes. Output includes
six evidence-bearing `decisions`, combined `strategies`, and all readiness
`blockers`. High complexity or explicit planning need adds Plan; high modification
risk or independent-verification need adds Audit. Native remains the default even
for multiple repositories. Delegation evidence uses current reference IDs, not
free-form strings. The old v1 declaration contract remains compatible and is not
upgraded into verified evidence by this command.

`--no-plan` and `--no-audit` express explicit overrides alongside the positive
flags. A risk-required audit cannot be waived, and an override cannot clear
UNKNOWN/stale evidence or capacity blockers. Re-run the original report with new
investigated evidence to reconsider; no background watcher or persistent routing
state is installed.

Optional iteration advice requires an explicit user request represented by:
`"iteration": {"requested": true, "mode": "goal", "max_attempts": 2,
"max_seconds": 120, "success_check": "python3 -m unittest test_currency"}`.
Mode can be `goal` or `loop`. Missing finite limits/success criteria block readiness;
`failure_kind` other than `code`, or `repeated_without_progress: true`, requires
classification/intervention before repeating. It starts no loop, goal, command or
worker. Use the host mechanism only when authorized and available; declared
limits are advice, not enforcement over arbitrary host sessions. A checked
success command still must be appropriate to the actual acceptance criteria.

Run `rein route assessment.json --agent codex --model <current-model>` (or
`--agent claude`). It prints JSON advice and never launches anything. No quota
file means unknown capacity; `--limits saved-orca-account-list.json` accepts the
existing supported envelope. Refresh it before any actual dispatch. The router
uses REIN's default 90% brake; execution still applies the project's configured
brake, which can be stricter.

Output separates `plan`, `execution` (native/quick/ship), `audit`, verification,
unknowns and reconsideration triggers. `status=investigate` is not clearance to
implement; `capacity_required` is not clearance to dispatch. Native remains the
default. No additional LLM is called. Re-run with updated evidence when a trigger
appears, and retain the previous JSON if comparing decisions is useful.

Only evidenced delegation changes the default, e.g.:

```json
{"delegation": {"reason": "multi_repo", "evidence": ["api/schema.py:8 and client/types.ts:21 must change together"]}}
```

Supported reasons: `independent_work`, `multi_repo`, `capability`, `recovery`.
Multi-repo coordination, independent work and recovery recommend Ship; a specific
capability need recommends Quick. These are recommendations, not mandatory tiers.
`--execution native|quick|ship`, `--plan`, `--audit` preserve explicit user choices.
Overrides do not waive gates or independent review for evidenced high-risk work.

Proceed through the existing workflow skill only when the user authorized that
execution and capacity is sufficient. Planning and auditing can also accompany
native execution. An iteration recommendation creates no loop: use the host's
bounded native mechanism only when necessary, with explicit time/attempt/budget
limits. A red test first needs code/environment/flaky classification.

## Quota semantics

`rein route ... --limits accounts.json --plans plans.json` includes a sanitized
`quota_snapshot`. `plans.json` maps provider names to explicit plan labels, e.g.
`{"claude":"Max $200", "codex":"Plus"}`. Labels are metadata and never imply
capacity; provider-reported planType takes precedence. No plan is cached.
Window percentages, duration and reset timestamps are preserved when present.

An Orca limited status brakes even without numeric windows. Unknown capacity is
reported as unknown, never zero. The selector prefers measured available
providers; Quick refuses an audit when no reviewer has measured capacity.
Quick/Ship execution events and `route --limits` now require a usable Orca
observation: numeric usage, valid millisecond `updatedAt`, and age at most fifteen
minutes (up to sixty seconds of clock skew). Unknown, missing, invalid or stale
observations block dispatch even with an explicit provider/model override. Refresh
through Orca; no private endpoint or background refresh is added. Raw legacy
`parse_rate_limits` callers remain compatible, but drivers use `checked_limits`.
Existing percentage brakes still apply. A single observed window does not prove
that an absent second window has spare capacity.

The existing driver reads Orca at execution events and records before/after
percentages. A decreasing percentage or changed reported plan/window/reset identity
produces unknown delta, not negative consumption or a comparison of unlike windows.
Unusable snapshots and older state without matching window metadata also produce
unknown deltas. Shared subscription activity means even a
positive delta cannot be attributed solely to this task. No dollars or token
balances are derived from percentages. The default brake leaves 10 percentage
points; it is a configurable dispatch reserve, not a guarantee an audit will fit.

An explicit Quick `--agent` selects a provider/model but now also queries quotas
and respects the configured brake. Unlike earlier versions, it does not bypass
that check. On a limited provider it returns exit 1 before creating a worktree or
worker. Unknown-source fallback is no longer authorized by the public drivers.
The unchanged default brake's ten percentage-point reserve is not an estimate that
an audit or correction will fit; there is no per-task token-capacity prediction.
