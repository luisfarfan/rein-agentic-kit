# P1 / conservative P2 — implementation and evidence

P0 remains **PARTIALLY ACCEPTED**. P1 is implemented and functionally validated in
the bounded cases below. P2 has a first conservative advisory implementation;
automatic interception/execution and broad model validation are not claimed.
The [design and acceptance criteria](P1-DESIGN.md) preceded implementation.

## P0 closure for this iteration

A fresh Orca Codex `gpt-6.1-sol` medium session queried the current worktree and
registered repo, then generated `Repo: p0-live-_sl9us7c` without being given that
name, reading previous plans, or receiving manual correction. Its draft passed
`plan-check --gate`. No Ship repetition followed. The existing source and actual
installed wheel now have tests traversing every skill's Markdown resource links
and invoking its resolved CLI outside the source checkout.

See the [PASS / FAIL / BLOCKED / NOT TESTED matrix](ACCEPTANCE.md). Claude-origin
work and Claude↔Codex Quick remain BLOCKED by the last supported 91% weekly usage
snapshot, above the unchanged 90% brake. There were no Claude turns or quota-policy
changes. Previous fixes remain recorded with their original failures and retests.

## P1 behavior

The current session receives the natural-language request and investigates real
code. Discover now separates existing capability, missing behavior, relationships,
risks and open questions, with six independent classifications: complexity, risk,
uncertainty, repo scope, planning and independent verification. It distinguishes
observations from inferences, read tests from executed tests, and local evidence
from assumptions about external systems. UNKNOWN is an acceptable outcome.

The normal path stays in the current terminal. Default investigation budget is
eight tool calls / three minutes, enforced by the session instructions, not a
daemon. Reuse inspected evidence, batch focused reads, and pursue only questions
that can change the decision. After a first measured run did redundant retrieval,
the final skill explicitly avoids repeated inventory without an unresolved
question and loads the JSON contract only when producing reusable evidence.
The skill has a 500-word regression budget (current body including metadata: 496).

Optional v2 reports are checked by `rein discover-check`. It checks only explicitly
cited files, within caller-supplied repo roots: SHA-256, line ranges and optional
symbol text. It reads duplicate file references once, refuses outside-root and
symlink escapes, and bounds references/file sizes. Changed or unavailable evidence
invalidates dependent conclusions. It never executes commands from the report.
Legacy v1 reports remain compatible and are still labeled as declarations.

These checks prove location/freshness, **not** semantic correctness, completeness,
execution of a cited test, or absence of an uncited dependency. Evidence capture is
the investigating agent's responsibility; blindly replacing a hash is not renewed
investigation. Explicit negative evidence and relevant boundary changes still
require judgment. No invented confidence percentages are used in v2.

## Executed cases

| Case | Automatic file/CLI/policy checks | Real model behavior |
| --- | --- | --- |
| Easy: EUR supported but disabled | PASS: six low classifications, current file evidence, native route | PASS: existing support versus missing enablement, USD and unknown-currency verification, no plan/audit |
| Complex: API/client contract and existing SQL rows | PASS: high complexity/scope, selective plan/audit, native default; distinct API/web roots checked | NOT TESTED with a model in this iteration |
| Ambiguous: deployed payment adapter absent | PASS: open question and high uncertainty produce deeper investigation; budget exhaustion reports unknowns | NOT TESTED as a separate live fixture; live money case also retained its unresolved contract |
| Hidden risk: configuration feeds USD-only ledger | PASS: small implementation can coexist with high monetary risk; stale/contradictory evidence blocks readiness | PASS: both old and new Discover traced checkout → ledger and identified mislabeling; new version also discussed direct calls and rollback limits |

Automatic labels are supplied by the tests, not discovered by Python. Those tests
validate contracts/policy, not agent competence. Live tests used fresh sessions,
the same natural-language request, model/effort, fixture contents and allowed tools.
They used tiny synthetic development fixtures, not held-out production examples.

## Comparison with the previous Discover

Each row covers both independent example repos. Source skill baseline: `0ef5267`.
All runs used `gpt-6.1-sol`, medium; no Astra or extra worker inside Discover.
Counts come from actual nested tool calls in the session JSONL, not self-report.
One output-file write is excluded from investigation calls and shown separately.

| Version | Investigation calls | Output writes | Agent duration | Input / cached input tokens | Output tokens |
| --- | ---: | ---: | ---: | ---: | ---: |
| Previous | 6 | 1 | 72.363 s | 176,172 / 165,248 | 2,769 |
| First P1 revision | 8 | 1 | 95.547 s | 208,916 / 184,704 | 3,564 |
| Final bounded revision | 6 | 1 | 84.547 s | 170,744 / 151,296 | 2,993 |

The first revision added a repeat inventory/search and a clock query. Both are
retained in the evidence, not hidden as an invalid sample. The bounded revision
restored the original investigation-call count and preserved the risk finding.
It emitted all six classifications explicitly and kept the monetary contract
unresolved instead of choosing a conversion policy. The previous Discover also
found the main defect; this is improved structure/traceability, not a demonstrated
general accuracy gain.

The final revision took 12.184 seconds longer than baseline and had more uncached
input (19,448 versus 10,924) and output tokens. Lower aggregate input includes cache
effects and does **not** establish savings. One run per version, a sequential
refinement and toy examples cannot establish statistical quality or cost effects.
The supported efficiency result is narrower: equal investigation tool count,
zero discovery workers, optional report resources, bounded file checks, and no new
model/network/subprocess calls in the normalizer/router. Broad non-regression in
consumption remains unproven. No quota delta is used as an efficiency metric.

Full reports: [previous](discovery-evidence/before.md),
[first revision](discovery-evidence/after.md),
[bounded revision](discovery-evidence/after-bounded.md).
[Machine-readable metrics](discovery-evidence/metrics.json) retain all attempts,
model/effort, fixture hashes, duration and provider token/cache categories. They
also include the separate 43.929-second Repo probe and the 51.570-second v2
integration turn. They exclude this parent coding session's implementation/test
consumption and must not be presented as the total cost of the iteration.

## P2: first conservative implementation

The existing `rein route` now checks v2 evidence before advising. It combines
Native, Plan, Audit, Quick/Ship and optionally bounded Loop/Goal, with reasons,
evidence-bearing classifications and readiness blockers. Multiple repositories
alone do not launch or even select Ship. A recognized delegation benefit with
current references or explicit user override is necessary; existing capacity
checks still apply. Risk-required review cannot be waived. UNKNOWN/stale evidence
cannot be bypassed by an override. New evidence is evaluated on the next command;
there is no hidden persistent decision or watcher.

Goal/Loop is advice only after an explicit request with a success check and finite
attempt/time limits. Environment/flaky/unknown failures or repeated failure without
progress prevent repetition advice. It starts no goal, worker or loop. Host
availability, user authorization and enforcement of limits remain with the caller.

The comparison with existing REIN and the October laboratory is in the design:
retain native work, selective planning/review and existing drivers. The historical
permissions false negative and flaky-gate scope drift do not justify more universal
orchestration. P3 recovery/quotas and Harness² are unchanged later phases.

## Real natural-language → report → route check

Reused the final session's context in a separate bounded four-call turn; no fresh
inventory or additional agent. Codex authored both JSON reports without manual
repair, captured actual file hashes and executed the checker/router:

| Report | discover-check | route | Interpretation |
| --- | --- | --- | --- |
| [A](discovery-evidence/report-A.json) | exit 0, sufficient | exit 0, ready; native | Existing localized capability |
| [B](discovery-evidence/report-B.json) | exit 1, deeper | exit 0, investigate; plan + native + audit | Implementation UNKNOWN and open monetary-contract question |

`route` exit 0 means valid JSON advice, not implementation clearance. B correctly
remains unready. The checker and router outputs were independently rechecked.
The probe printed verbose JSON; this diagnostic overhead is included separately
in metrics, not in the prose-only comparison. Normal small tasks need no JSON.

Replay the retained A report with:

```sh
python3 plugins/rein/bin/rein discover-check docs/research/2026-11-01-evolution/discovery-evidence/report-A.json --root tests/fixtures/discovery/easy
python3 plugins/rein/bin/rein route docs/research/2026-11-01-evolution/discovery-evidence/report-A.json --root tests/fixtures/discovery/easy --agent codex
```

For B substitute `report-B.json` and `hidden`; checker exit 1 is expected. No test
or command mentioned inside a report is run by these commands.

## Validation

- Final targeted regression: 73 tests, 1.131 s, OK; includes legacy compatibility,
  distinct repo roots, stale evidence, traversal/symlinks, no implicit execution,
  overrides, budget limits, UNKNOWN and iteration preconditions.
- Real wheel build/install and execution: 7 tests, 3.805 s, OK, temporary venv.
- First full suite: 1986 tests, one failure because the command-catalog contract
  had not yet listed `discover-check`. Updated that expected surface and verified
  the affected tests; no product behavior was changed to hide the failure.
- Final full suite: `python3 -m unittest discover -s tests -q` — **1987 tests,
  201.820 s, OK, skipped=2**. Existing round6 ResourceWarnings remain.
- `git diff --check`: passed. Commits by capability: `03001b4` design, `ec90cd5`
  P0 focused verification, `f03974e` P1, `38a222c` conservative P2, `799e684`
  installed-report and separate-repo regression coverage. Evidence follows in a
  documentation-only commit.

No push, merge, release, global install/config changes, permanent watcher or
automatic laboratory. Fixture files and live receipts remain available locally.
