# P1 diagnosis and minimum design

Baseline: `0ef5267`, clean `codex/native-evolution`. P0 stays **PARTIALLY ACCEPTED**.
This design precedes implementation; the user explicitly authorized diagnosis,
design, implementation and validation in this iteration. No separate plan gate
or Ship execution is necessary for this sequential work.

## Diagnosis

- Discover already traces an entry point and consumers in the current session,
  but its budget is qualitative and its output is prose without a repeatable
  existing/missing/relationship/question contract.
- `impact.assess` separates eleven dimensions, but conflates their high values
  into `risks`; it has no independent cross-repository or planning classification.
  Any nonempty evidence string is accepted, even after its file changes.
- `routing.recommend` already combines plan, native/Quick/Ship and audit. It is
  advisory and pure, but its explanations do not identify each policy rule's
  evidence, and iteration has no explicit bounded opt-in contract.
- P0 has real Codex-origin Ship evidence, independent audit and stale-review
  rejection. Fresh model generation of `Repo:` and installed skill-resource
  traversal still need focused verification, not another Ship run.

## Minimum implementation

Keep the existing skills, CLI and Orca ownership. No classifier model, index,
graph service, new orchestrator or autonomous loop.

1. Preserve the legacy report contract. Add an opt-in version 2 report whose six
   independent classifications are implementation complexity, modification risk,
   uncertainty, repository scope, planning and independent verification. Levels
   remain low/high/unknown/not_applicable, with no confidence percentage.
2. The investigating session supplies existing capabilities, missing behavior,
   dependencies/contracts, risks, and unresolved questions. Each conclusion cites
   evidence IDs and distinguishes observation from inference. Negative evidence
   describes the bounded path checked; a failed search alone proves no absence.
3. A small `discover-check` command checks cited local files, content hashes,
   line ranges and optional symbols inside explicitly supplied repo roots.
   Missing/stale/unavailable evidence becomes UNKNOWN. It never executes commands
   from a report or claims hashes prove the semantic conclusion. Reports remain
   optional for trivial work; no automatic artifact is created.
4. Discover uses at most eight investigation tool calls / three minutes by
   default. Batch focused reads, reuse valid evidence, and spend a further pass
   only on a named question that could change the decision. At the limit, report
   UNKNOWN and the next targeted check. No automatic workers or model escalation.
5. After P1 validation, let the existing advisory router consume checked v2
   reports. Explain plan/audit/execution separately with evidence; retain explicit
   overrides and readiness brakes. Native is the default, including multi-repo
   work when no delegation benefit is evidenced. A bounded Loop/Goal recommendation
   requires an explicit request, success check and finite budget; it starts nothing.

## Acceptance and validation

| Capability | Acceptance | Verification |
| --- | --- | --- |
| P0 Repo generation | Fresh Codex reads corrected skill and chooses registered name without manual correction or reading old plans | One bounded Orca Plan probe, no Ship |
| P0 adapters | All seven skills reach shared resources and CLI in source and actual installed wheel | Native/packaging contract tests |
| P1 separation | Same small implementation can be high risk; multi-repo scope alone does not imply difficulty | Four easy/complex/ambiguous/hidden-risk fixtures |
| P1 evidence | Absent, stale, invalid and unavailable references never grant readiness; out-of-root files are not read | Filesystem-backed tests and CLI checks |
| P1 progressive behavior | Current session investigates natural-language request; bounded report identifies existing, missing, relationships and questions | Focused real model comparison against previous skill, separate from contract tests |
| P2 conservative choice | Native default; selective plan/audit; only justified delegation; overrides preserve unresolved evidence and review | Routing policy tests, no dispatch |
| Consumption | No runtime model/subprocess/network calls in check/router; actual experiment tools/time recorded, unavailable token counts null | Instrumented tests, live receipts; no quota-as-savings claim |

## Compare before implementing P2

The October 30 closure found that planning helped the multi-repo task but also
encoded a bad permission decision; audits missed permission defects, and flaky
gates triggered costly unrelated fixes. Full orchestration was mixed and more
expensive in the widget case. One run/cell and an invalid cell prevent broad
conclusions. Thus retain selective plan/audit, require evidence of delegation
benefit, classify failed checks before iteration, and keep scope/guard enforcement
in existing drivers. See [the historical closure](../2026-10-28-post-sdd/CIERRE-PROVISIONAL-FASE2.md)
and [evaluation limitations](EVALUATION.md). No new quality or savings claim follows
from these historical observations.

P3 quotas and Harness² remain later phases. Existing quota brakes are unchanged.
