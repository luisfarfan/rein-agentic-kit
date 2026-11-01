# Evidence-backed impact

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
