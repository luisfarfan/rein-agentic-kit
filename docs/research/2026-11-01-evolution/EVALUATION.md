# Evaluation ledger and next controlled experiment

## Executed here

The tests for impact and routing supply evidence declarations directly. They check
policy behavior: native defaults, unknown evidence, hidden-risk labels, multi-repo
delegation, capacity, overrides and reconsideration. They do **not** measure whether
an agent discovers those facts. Quota tests exercise unavailable sources, malformed
numbers, limited status, changing plan labels and reset deltas. These are unit and
CLI integration tests, not agent benchmarks.

P0 additionally exercises actual Codex discovery and a real installed wheel. No
model turn, Quick worker, Ship run or agent A/B benchmark was launched here.
The live Claude weekly usage was 91%, above the default brake. No token savings,
quality advantage or statistical significance is claimed.

## Existing agent evidence

The October 30 closure reports three tasks across N1 (native + gates), N2 (+ audit),
P2 (+ plan) and A (full REIN), one run per cell, with N0 from earlier screening.
Widget passed with simpler arms too. The permissions task retained a dangerous
false negative; its A cell was invalid. The concurrency task exposed both value
from planning and problems in audit correction and flaky shared test infrastructure.
See [the original closure](../2026-10-28-post-sdd/CIERRE-PROVISIONAL-FASE2.md).
This favors testing selective additions, not assuming full orchestration wins.

## Proposed A/B protocol — not executed

Arms: A native; B native + selective plan; C native + selective independent audit;
D existing full REIN; E new evidence-based discovery/routing. Use the same initial
commit, provider/model/effort, task prompt, tools, environment and frozen evaluator
within each comparison. Randomize arm order. Report actual provider differences
instead of calling a cross-model comparison an effect of the harness.

| Fixture | Key held-out criterion |
|---|---|
| Localized label change | Exact behavior, no unrelated edits or workers |
| “Enable EUR” with single-currency ledger | Money invariants and settlement dependencies found |
| Multi-currency support already present | Existing implementation reused, bounded investigation |
| Tenant permission change | Cross-tenant negative tests remain denied |
| API plus client contract | Both sides compatible and independently verified |
| Database migration | Existing data preserved and rollback/compatibility demonstrated |
| Flaky test | No product or test-setup mutation to hide environment failure |
| Unknown/exhausted quota | Correct uncertainty/brake handling, no parallel burst |

Before spending quota, choose at most two fixtures and all five arms, with a
predeclared per-cell timeout, token/observable quota cap, and at most one retry.
The budget is not authorized by this document. Prefer the low-cost preliminary
comparison A versus E when quota is scarce. Expand repetitions only after the
fixture and evaluator are shown stable. Keep a separate unseen set for adoption.

Record per cell: functional correctness; escaped defects; regressions; acceptance
criteria met/total; tokens (including cache categories where observable); quota
snapshots and resets; elapsed time; workers; retries; coordination time; human
interventions; and impact false negatives/positives. Missing metrics stay null.
Do not substitute API list-price equivalents for subscription spending.

Stop a cell on isolation failure, secret/data exposure, unavailable verification,
quota brake or repeated failure with no progress. An invalid cell stays invalid.
Adopt E only if it improves correctness or resource use without worsening dangerous
false negatives. Small samples are descriptive; default to the simpler strategy
when the comparison is inconclusive.
