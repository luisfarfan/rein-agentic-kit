# Valid comparison, native control and quota observability

2026-11-01. Protocol and evaluator frozen in `c1757f7` before any model call.
Exactly four fresh Sol Medium sessions ran: two corrected P1 cells and one P2 pair.
No extra reviewer, retry for quality, product edit, push, merge, release or global
change. No new daemon, mandatory LLM router, dependency, loop engine or scheduled lab.

## Recommendation

**Keep:** bounded inspection of actual code, existing capability vs missing behavior,
concrete evidence, explicit uncertainty, risks and meaningful verification. Retain
freshness validation, mandatory-control protection and independent review where
the change warrants it. Keep Orca as the existing execution/quota source.

**Simplify:** propose replacing the mandatory six-part prose classification in
Discover with a compact account of evidence, uncertainty, risks and verification;
state execution/plan/review implications only when they change the decision. Old
Discover already found the critical facts here. Preserve the optional machine
contract for callers needing a reusable record rather than breaking it to remove
headings. This is a proposal after measurement; no P1/P2 prompts or algorithms were
edited to improve scores during the experiment.

**Remove from the proposed default workflow:** compulsory JSON/checker/router/report
cycles for small native changes and any proposal to make P2 universal. They are
not globally enabled now, and this result does not justify enabling them. No
existing API is deleted in this iteration. P2 remains optional when its evidence
record/control checks are useful; it is not a proven better decision maker.

**Still unproven:** superiority across representative difficult tasks, global
quality/efficiency gains, correct host behavior on dangerous or UNKNOWN routes,
Claude/Opus integration under real capacity, and reliable recovery from an
unrefreshable quota source. No autonomy expansion is justified by these results.

## P3: conservative and recoverable, not guaranteed observable

Before any follow-up message change, [scenario evidence](p3-scenarios-before.json)
ran the existing quota functions and Ship state machine. These are deterministic
tests, not deliberately exhausted live subscriptions. Three additional regression
tests cover cached-source expiry, retry/pause/recovery and missing session windows.

| Requested condition | Observed behavior | Practical interpretation |
| --- | --- | --- |
| Fresh valid weekly snapshot, session absent | Available on reported weekly window; one requested dispatch allowed | Missing session stays unknown, never zero; no promise of whole-task capacity |
| More than 900 seconds old | Capacity unknown; zero new dispatches | Timestamp age is an observability failure, not proof of exhaustion |
| Orca answers but returns cached bytes | Fresh initially; same snapshot becomes unusable at 901 seconds | Transport success does not renew observation age |
| Actually available provider reported UNKNOWN | Zero new dispatches | Actual availability is a scenario assumption; REIN cannot infer it from missing numbers |
| Both Claude and Codex limited | No dispatch to either; override does not bypass brake | A separately eligible observed provider could still be selected under existing rules; this test supplies none |
| Plan/window/reset identity changes | Fresh eligible data can authorize work, but delta is unknown | Capacity eligibility and comparable consumption are different questions |
| Retry with same stale data | Still blocked | Retry is not an override or refresh |
| Retry after new usable data | Dispatch resumes | No permanent internal stale-data latch |
| Pause without active workers | Existing state machine finishes with task pending | No worker or busy retry is needed; active workers remain supervised under existing pause behavior |

The [installed CLI help](orca-account-help.txt) exposes `account list`, not a
force-refresh option. Two actual reads returned identical Claude/Codex `updatedAt`
values. A later read had advanced timestamps without any force-refresh action.
See [sanitized observations](quota-observations.json). This proves cached reads and
eventual update in this session, **not** an update interval or immediate recovery
guarantee. No supported immediate refresh mechanism was verified in the CLI; no
claim is made that every Orca UI/version lacks one.

There is a real tradeoff: if Orca never reports usable fresh usage, new workers can
remain blocked indefinitely. A guaranteed launch would contradict verified-capacity
requirements. REIN can avoid an internal permanent lock and useless automatic
retries; it cannot repair an unobservable provider by inventing capacity. Local
inspection/tests/planning in the existing session remain possible. Required review
does not become approval because a reviewer cannot start.

The previous diagnostic misleadingly said “refresh Orca before dispatch” without a
verified way to force it; Ship's audit text described every refusal as over quota.
Only diagnostics and documentation changed after the reproduction:

- Unknown/stale refusal now names unverified capacity, asks for an Orca read and
  timestamp comparison, explains that listing does not force refresh, and says to
  pause new workers if observations remain unchanged/unknown.
- Quick audit refusal prints provider reasons. Ship audit refusal distinguishes
  lack of verified capacity from a measured percentage limit.
- [Quota runbook](../../../plugins/rein/IMPACT.md) documents pause/retry, missing
  windows and the potentially indefinite external prerequisite. No hidden worker
  probe, timestamp rewrite, account switch or repeated refresh loop is suggested.

**Policy unchanged:** 900 seconds, 60-second clock skew, 90% default brake, explicit
provider choices, reserve semantics and no unknown-capacity worker fallback. The
900-second threshold is still conservative rather than empirically calibrated.
Plan/reset comparisons are deterministic tests; no live plan change was induced.
Unreported plan changes remain undetectable. Even small changes in a reported reset
timestamp can conservatively suppress a delta; equal-looking percentages do not
prove comparable or attributable task consumption. Dynamic audit/correction
reservation and full mid-task recovery remain incomplete.

## P1: corrected laboratory, valid but non-contemporaneous comparison

[Preflight](preflight.json) records real Orca shell cwd, Git root, no remotes,
resolved context roots, no inherited plan, prompt hashes and input hashes. Each case
was registered as its own no-remote Git root; no fixture source was changed. Actual
model session metadata confirms the correct cwd and Sol Medium in both cells.
Read traces stay in the allowed fixture/assigned resources. This is prompt-scoped
isolation, not an OS sandbox.

The exact original prompts, output paths, b2f4244 Discover/resources, fixtures,
oracle and budgets were retained. The canonical context command and its detect/
plan implementations were checked against b2f4244. Later quota/route changes are
not exercised by that context call. Only actual launch cwd/workspace was corrected.
Old temporary outputs were backed up; the prior committed invalid results remain
unchanged in [the earlier report](../2026-11-01-evaluation/RESULTS.md).

The old valid cells ran at about 07:09/07:10 UTC; corrected new cells at about 07:40
UTC. This comparison is **NON-CONTEMPORANEOUS**, one observation per cell, with no
cache equalization or causal latency estimate. No old cell was repeated.

| Frozen fact | Old valid | Corrected new | Assessment against code and report |
| --- | --- | --- | --- |
| C1 sequential duplication | Found | Found | Headers unused; every service call emits |
| C2 shared durable concurrency boundary | Found | Found | Local Store and number uniqueness cannot deduplicate intent |
| C3 external effect before save, unknown provider | Found | Found | Local transaction alone cannot guarantee exactly-once |
| C4 business and permission preservation | Found | Found | Authorized tenant-scoped replay |
| C5 stable client key and old-client compatibility | Found | Found | New intent vs retry; optional additive API contract |
| C6 migration/rollback, monetary invariants, plan/review | Partial | Partial | Both identify durable fiscal risks/review; concrete additive migration/rollback verification remains absent. New explicitly selects planning. |
| C7 batch already exists | Found | Found | Client request and API batch consumer already present |
| C8 localized menu mismatch | Found | Found | menu.py excludes inventory/pricing supported by policy.py |
| C9 visibility is not authority | Found | Found | Preserve per-action area permission |
| C10 business and grant key | Found | Found | All required for each proposal; no cross-tenant widening |
| C11 contract trace and allowed/denied verification | Found | Partial | New traces producer/consumer and reasons about denials, but its verification section emphasizes missing adapter evidence without the old report's explicit positive/negative regression checklist |

This is a manual fact-level assessment, not keyword or LLM grading. The C11
qualification concerns verification specificity, not an omitted authorization
boundary. Both versions cover **5/5 frozen dangerous-risk facts** (C2/C3/C4/C9/C10),
with **0/5 observed dangerous omissions** and no unsupported ready/exactly-once
guarantee. This is not a population false-negative rate. Both also identify the
payload override of trusted business/number fields, an exploratory finding already
observed in old Discover. The fixed cases do **not** reveal a critical impact missed
by the old version. Six classifications mostly add explicit organization here.

Actual reports: [issuance](reports/p1-case1.md), [approval](reports/p1-case2.md).
Both used three investigation calls plus one report-writing call. Case 1's combined
read command returned exit 1 because the subsequent local AGENTS search found no
file; all six requested source files were read. It is retained, not retried/hidden.
No fixture implementation, test execution, worker or outside research occurred.

| Case / version | Total tokens | Input | Cached input | Uncached input | Output | Seconds | Tools total / investigation |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Issuance old | 111095 | 108320 | 97664 | 10656 | 2775 | 78.346 | 4 / 3 |
| Issuance corrected new | 104359 | 101908 | 93312 | 8596 | 2451 | 71.186 | 4 / 3 |
| Approval old | 132333 | 129696 | 119552 | 10144 | 2637 | 72.321 | 6 / 5 |
| Approval corrected new | 103714 | 101166 | 93056 | 8110 | 2548 | 69.832 | 4 / 3 |

New cells used 19.3% and 20.1% fewer uncached input tokens in these observations;
duration was 9.1% and 3.4% lower. Approval needed fewer investigation calls. Thus
the corrected experiment does **not** support a blanket claim that new Discover
only adds consumption. Nor does it show its six dimensions caused savings or new
risk detection: the runs are not contemporaneous, caching/response construction
vary, and old Discover already covered the key facts. Keep bounded exploration;
simplify classification ceremony rather than claim overall superiority.

## P2: matched native vs REIN

The same natural request, identical initial [source/tests](p2-initial/), model and
effort were used in two fresh sessions launched less than one second apart.
Preflight pins source hashes; each session confirms its own actual cwd. Both got
the same local scope/budget/test constraints, twelve calls/180 seconds, and no
workers or network. Only [B's entry policy](p2-rein-AGENTS.md) added frozen Discovery
guidance, evidence and routing; [A's policy](p2-native-AGENTS.md) has no REIN steps.
Native's actual trace contains no REIN resource or command. Shared host/global
instructions and subscription cache were not reset; this is a local paired test,
not a randomized or fully isolated population study.

The narrow in-process menu task has a justified native solution. Neither arm was
asked to deploy HTTP adapters or alter mutation authority. The [independent evaluator](check_p2.py)
was frozen before launch, outside both workspaces; baseline failed only the two
missing menu permissions. It checks behavioral positives/negatives and unchanged
policy/request-producer bytes. Models never read that evaluator or the oracle.

| Outcome | A: native | B: Discovery + route |
| --- | --- | --- |
| Final functional code | Add inventory/pricing to existing tuple | Identical tuple change |
| Independent checks | **19/19 passed** | **19/19 passed** |
| Mutation authority/request contract | Unchanged | Unchanged, also hash-checked by agent |
| Risk coverage | Area, tenant, grant and visibility ≠ approval covered in added tests | Same boundaries; additionally explicit mixed unauthorized batch regression |
| Agent's own tests | 3 test methods, including nested permission/area cases | 4 test methods, including mixed batch |
| Strategy actually used | Native in current session | Native in current session |
| Backend rebuild / extra worker / formal plan | None | None |
| Additional process work | Inspect, edit, test | Load guidance/contracts, create report, checker, route, edit/test, update evidence and route again |
| Independent review | Neither requested nor claimed | Route did not require it for this scoped change; none claimed |

Method counts do not measure coverage: A checks all four viewer permissions against
both action areas; B adds a mixed-batch case but does not repeat all A combinations.
The external evaluator checks both implementations identically. Neither omitted a
dangerous authorization boundary in this task. B adds useful auditability and one
explicit regression scenario, but no superior final behavior or strategy was
observed. A's untested mixed-batch combination is a regression-coverage difference,
not a functional failure: its unchanged implementation passes that independent test.

**Router correctness vs host compliance:** B's pre-edit report/check/route are
preserved under [reports](reports/). The actual route was `ready`, `native`, no plan,
no audit, no blockers. Trace order shows code inspection and route before the patch;
the agent then executed natively and tested. The advice was reasonable for the
frozen narrow scope and the host **actually followed it**. This does not establish
added decision value: A independently chose the same adequate strategy. There was
no test of a host obeying a required audit or stopping on UNKNOWN in this pair.
Post-edit hashes/report were refreshed and a second route was recorded; the earlier
report remains historical evidence, not a current approval.

| Arm | Total tokens | Input | Cached input | Uncached input | Output | Seconds | Actual tool calls |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Native | 75384 | 74567 | 53376 | 21191 | 817 | 20.753 | 4 |
| REIN | 160035 | 156951 | 132608 | 24343 | 3084 | 57.370 | 6 |

B used **3152 more uncached input tokens (+14.9%)**, **36.617 more seconds (+176.4%)**
and **two more tool calls**. Total tokens increased 112.3%, including repeated cached
context. One shell call can contain several commands; B ran discover-check and
route together, so tool count alone understates procedure. Route validates evidence
itself; the separate checker and post-change artifacts are extra process work,
not proof of better decisions. No redundant backend implementation occurred.

All counters come from actual completed sessions in [metrics.json](metrics.json),
with read/write traces, output and patches retained. Cached tokens are a subset of
input; total is input + output. Reasoning is not added twice. No dollars, token
balance or per-task subscription-percentage consumption is inferred. Parent
evaluation/coding/testing work is excluded. All four model turns completed within
budget with no intervention or extra turn.

## Acceptance, validation and remaining boundary

P0 remains **PARTIALLY ACCEPTED**. Latest supported read, 07:44:01 UTC: Claude 91%
weekly, still over the unchanged brake; Codex 37% on its reported weekly window.
Session windows and plan labels absent. No Claude probe or passed P0 Codex rerun.
The [existing resume procedure](../2026-11-01-evaluation/P0-RESUME.md) remains valid.

P3 remains partial: improved actionable diagnostics and tested re-entry, no guaranteed
source refresh, dynamic reserve or full mid-task recovery. P1 is bounded guidance
with no demonstrated additional critical-risk detection here. P2 is optional advice
with demonstrated compliance in this one native case, not demonstrated better choice.

Focused quota/Quick/Ship-decision checks passed: **255 tests, 18.619 s**. The first
full sweep ran **2000 tests in 310.816 s**, with two failures in diagnostic wording
assertions and two skips. Both failures expected the quota-brake label in the audit
message; no behavior assertion failed. The message now retains that label while
explaining unverified capacity, and both targeted tests passed again (0.135 s).
Temporary-environment wheel checks passed: **7 tests, 4.160 s**. The final full
regression passed: **2000 tests, 383.214 s, OK (skipped=2)**. See
[validation record](validation.json). Diagnostic-only change: `3541489`. Existing round6
ResourceWarnings concern unclosed test files and were not introduced here.

No further model experiment is proposed as an automatic next action. If future work is requested,
the useful question is a task where independent review or an UNKNOWN stop changes
the outcome; another easy native case would add little. Freeze that oracle and
budget separately rather than expand this experiment.

`git diff --check`, artifact JSON parsing, local report links and frozen-byte/cwd
checks passed. Source changes and evaluation evidence are committed separately;
no push or release. Disposable Orca fixtures remain local and have no remote.
