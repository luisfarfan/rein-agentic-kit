# Decision-quality evaluation and minimal P3 follow-up

2026-11-01. Baseline `b2f4244`; protocol `91a03ff`; independent oracle and
counterexamples `7407fea`; quota correction `eff8193`. No production edits, push,
merge, release, global installation or recurrent laboratory.

## Answers to the three questions

1. **Better discovery: not established.** The old Discover already identified the
   critical boundaries in both new historical reductions. Both new-version cells
   violated context isolation and are ineligible for a paired quality or efficiency
   conclusion. The new format makes six dimensions explicit, but this experiment
   does not demonstrate that those labels improve decisions.
2. **Better strategy selection: not established.** Observed recommendations agree
   with selective planning/review and native execution already supported by the
   October experiments. The deterministic freshness/UNKNOWN/override checks are
   useful safeguards. They do not prove the agent's supplied classifications are
   correct, or that advice is better than the existing rules.
3. **Natural integration: demonstrated narrowly, savings not established.** One
   actual Astra session in Orca inspected code, authored evidence, consulted the
   router, made a native change and passed five checks. It used an explicit local
   AGENTS.md policy; this is not automatic interception in an ordinary installation.
   There was no native-only control and no Claude/Opus run. Neither lower consumption
   nor higher quality can be attributed to REIN from this one observation.

Keep evidence and routing optional. Do not add mandatory JSON, a chain of skills,
or global activation. No Discover prompt or P1/P2 algorithm was tuned in this
iteration. If a valid paired experiment still shows no decision benefit, simplify
the six-class reporting ceremony to concise evidence, unresolved boundaries and
required checks; retain source freshness and mandatory-control validation. This
invalid comparison does not justify removing those independently tested guards.

## Protocol integrity and independent evidence

[Protocol](PROTOCOL.md) and [oracle](ORACLE.md) were committed before model output.
Two executable reductions derive from PROXIMA historical changes: duplicate fiscal
issuance across admin/API and an existing batch approval capability hidden by menu
permissions. They are not production snapshots. Product repositories were read
with Git only. Oracle tests demonstrated selected counterexamples before launch;
neither tests nor labels were copied into model directories.

Four fresh `gpt-6.1-sol` medium turns compared `0ef5267` and `b2f4244`, with at most
eight investigation calls/180 seconds per turn and at most two simultaneous turns.
All completed within budget, without workers. The additional Astra high turn had
twelve calls/180 seconds. No retry, additional model review or benchmark expansion
was performed. The scope boundary was prompt-based, not an OS sandbox.

**Harness failure:** benchmark sessions were launched from the registered P0
coordinator fixture, with the allowed case path supplied explicitly. The old arms
passed the case path or set the command's working directory. Both new arms called
`rein context .` from the coordinator directory. The returned context included
prior P0 plan/EUR context outside their allowed directory. They did inspect the
correct case code; no oracle or gold report access was observed. Nevertheless,
both are **INVALID_CONTEXT_ROOT**, not successful held-out evaluations. Their own
prose does not override the recorded tool inputs. The appropriate next correction
is the harness's actual starting directory, not prompt tuning or discarding bad
results. It was not retried to make this iteration green.

All four [reports and tool-input traces](reports/) are retained. The following
manual rubric is descriptive for the invalid new arms; only old-arm entries are
eligible evidence. References name the inspected fixture boundaries and the report
sections explaining them, rather than rewarding classification keywords.

| Frozen fact | Old | New (invalid) | Supporting boundary / qualification |
| --- | --- | --- | --- |
| C1 sequential replay | Found | Found | router headers ignored; service emits on every call |
| C2 durable concurrency | Found | Found | Store is local; schema uniqueness on number cannot deduplicate intent |
| C3 external uncertainty | Found | Found | issuer before save; unknown timeout/idempotency/reconciliation |
| C4 business/permission | Found | Found | router authorization and tenant-scoped replay must remain |
| C5 client/API compatibility | Found | Found | stable intent key across retries; existing no-header behavior |
| C6 migration, monetary invariants, plan/review | Partial | Partial | Both cover persistence and fiscal invariants/review; neither specifies a concrete additive migration/rollback verification. New explicitly recommends selective planning and discusses rollback; old does not explicitly select a planning lane. |
| C7 batch already exists | Found | Found | approval_request and approve_batch already support the request |
| C8 localized menu mismatch | Found | Found | menu.py vs policy.py permissions |
| C9 visibility is not mutation authority | Found | Found | per-proposal area permission remains required |
| C10 tenant and grant key | Found | Found | policy.py conjunction; entire batch rejects a failing item |
| C11 contract and negative checks | Found | Found | payload/header → approval → policy; denied cases; two repos do not require Ship |

The old valid reports cover **5/5 frozen dangerous-risk facts** (C2/C3/C4/C9/C10)
without unsafe readiness advice: zero observed dangerous omissions among those
five facts, not a population error rate. New reports descriptively discuss those
same facts, but there is **no valid new-arm denominator and no paired improvement
score**. Neither version implements these discovery cases or proves production
correctness. Both case-1 reports additionally noticed `**payload` overriding trusted
business/number fields; this is an exploratory finding outside the frozen score.

## Actual consumption

[metrics.json](metrics.json) retains task completion time, token counters and tool
counts from the five actual Codex sessions. Total = input + output; uncached is
input minus cached input. Reasoning counters are retained separately in JSON, not
added again. Counts include repeated prompt/context input. Parent evaluation,
coding and test work is excluded. These are tokens, not currency or quota capacity.

| Case / arm | Validity | Total tokens | Input | Cached input | Uncached input | Output | Seconds | Tools total / investigation |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Issuance / old | VALID | 111095 | 108320 | 97664 | 10656 | 2775 | 78.346 | 4 / 3 |
| Issuance / new | INVALID_CONTEXT_ROOT | 110361 | 107337 | 85888 | 21449 | 3024 | 87.685 | 6 / 5 |
| Approval / old | VALID | 132333 | 129696 | 119552 | 10144 | 2637 | 72.321 | 6 / 5 |
| Approval / new | INVALID_CONTEXT_ROOT | 108240 | 105541 | 96512 | 9029 | 2699 | 79.289 | 4 / 3 |
| Natural workflow / Astra | VALID, different task/model | 143668 | 141163 | 108544 | 32619 | 2505 | 49.528 | 7 / n/a |

The issuance candidate used roughly twice the uncached input while the approval
candidate used less; both took longer. This is descriptive, **not an eligible
efficiency comparison**. Different cache reuse, report length, resource/context
reads and the wrong context root confound attribution. Tool counts alone do not
measure cost: one command can read many files. Explicit classification adds output
and evidence-validation work, which is justified only if useful decisions improve.
No savings claim, latency benchmark, dollar estimate or quota-derived token count
is made. One run/cell would remain weak evidence even after fixing isolation.

## Actual natural-task behavior and routing limits

The separate no-remote fixture `.rein/p0-live-evaluation/workflow` was registered in
Orca as `139a8c17-f01e-43a8-a345-17d2e0a61870`. Astra terminal
`term_fb7045e0-59a2-40d5-bbe1-6d7390a2c9d7` received this ordinary request:

> Haz que el panel de aprobaciones sea visible también para quienes tienen
> inventory:manage o pricing:manage. Conserva los accesos existentes y las
> comprobaciones de autorización para aprobar. Trabaja sólo en este ejemplo local;
> no hagas push.

The fixture's [local entry policy](reports/workflow-AGENTS.md) required inspection
and an evidence-backed route for permissions/contracts/uncertainty. It explicitly
bounded the task to in-process menu visibility, not an HTTP deployment or mutation
policy change. This scope was supplied by the harness and is part of the result.

Observed order, from [tool inputs](reports/workflow-astra-tools.json): read code and
hash references; write v2 impact report; run `rein route`; receive ready/native;
edit only menu.py and test_panel.py; pass **5 tests** including existing visibility,
both newly visible permissions, rejected area/business/grant and mixed batch;
confirm policy.py, approvals.py and view.py hashes unchanged. No worker, reviewer,
self-approval, merge or push followed. The route did not require audit for this
explicitly narrow scope. That does not show review is optional for a production
permission or approval mutation. See [actual patch](reports/workflow.patch).

[Observed CLI output](e2e-route-observed.json) comes from the session's actual tool
response. The agent deleted its temporary report; [reconstructed input](e2e-report-reconstructed.json)
preserves the observed assessment/references without relabeling and is explicitly
a reconstruction. The code inspection, route call and implementation actually
happened; a correct JSON shape alone is not the integration evidence.

[Routing checks](routing-checks.json) then replayed that evidence against original
and changed bytes and applied controlled label/override perturbations. These are
policy tests, not additional independent model investigations:

| Condition | Observed advice / interpretation |
| --- | --- |
| Actual pre-edit evidence | ready/native; agent followed it |
| Same evidence after actual code change | investigate; stale hashes invalidate readiness |
| High repository scope alone | native; reading multiple repos does not justify Ship |
| High risk/review plus audit=false override | audit retained |
| Explicit Quick or Ship without capacity | capacity_required; Ship also carries plan |
| Explicit iteration without attempt/time/success bounds | iteration_constraints_required |

Earlier real model-authored monetary UNKNOWN reports remain relevant evidence for
Plan/Audit advice; the new issuance prose similarly recommends resolving external
unknowns before implementation. Current executable contracts cover UNKNOWN and
separate planning/review. No live Quick, Ship or Loop was repeated here, and no
agent was tested actually stopping under the controlled high-risk/UNKNOWN variants.
CLI success means a recommendation was produced, not authority to implement.
The router trusts semantic assessments: hashes verify referenced bytes, not the
truth of a low-risk claim. The difference between the broad approval request's
review advice and the narrow natural case's native route illustrates why scope
and host policy need testing before autonomy. There is no automatic Claude/Codex
hook, no proof every host follows advice, and no superiority over the October
selective-plan/audit rules.

## P0 and P3 status

**P0 remains PARTIALLY ACCEPTED.** Latest read-only Orca observation at
2026-11-01 07:23:29 UTC: Claude weekly 91% (limited by unchanged 90% brake), Codex
weekly 35% available on the observed window; session windows and plan labels absent.
The observation itself was about 142 seconds old. It is an observation at that
time, not enduring capacity. See [sanitized source](quota-observation.json) and
[reproducible resume procedure](P0-RESUME.md). No Claude launch or passed Codex P0
rerun was attempted. Claude-origin and both cross-provider Quick directions remain
blocked; a forecast reset is not proof capacity has returned.

**P3 is PARTIALLY IMPLEMENTED/VALIDATED.** Existing code already supplied event
queries via Orca, provider selection, a 90% dispatch brake, explicit overrides,
audit selection, state persistence and before/after percentage recording. Two
gaps were reproduced before editing: a 24-hour-old low numeric observation still
authorized capacity, and changed window/plan identity could yield a fabricated
comparable delta. Minimal correction `eff8193`:

- Quick startup/audit, Ship execution events and `route --limits` use checked Orca
  observations. Missing, invalid, unknown, stale or implausibly future observations
  cannot authorize dispatch, including explicit provider/model overrides. A known
  limited signal still brakes; all-limited state dispatches nothing.
- Freshness is a documented conservative policy: at most 900 seconds old, allowing
  60 seconds of future clock skew. Orca `updatedAt` is milliseconds. This threshold
  is not an experimentally established capacity guarantee. One available window
  does not fill in another missing window.
- Recorded plan/window/reset identity changes, unusable observations, decreasing
  usage or unmatched older state produce unknown delta. No dollars, task token
  balance or per-task attribution is inferred. Missing/unreported plan changes
  cannot be detected; concurrent subscription activity remains a confounder.
- Public drivers intentionally no longer use the old unknown-capacity one-worker
  fallback. Pure legacy parsing remains compatible. No default model change,
  daemon, background refresh, new provider endpoint or dependency was added.

The unchanged 90% brake leaves ten percentage points as a dispatch reserve; it
does **not** predict whether an audit/correction will fit or reserve capacity from
other sessions. Dynamic reservation and full mid-task recovery are not complete.
The unpublished recovery branch remains separate and untouched. P3 tests exercise
transport fakes and real CLI/state transitions; the live query validates source
shape/freshness, not a newly executed cross-provider run or quota-exhaustion event.
No claim of complete P3 acceptance or independent code-audit approval is made.

## Validation

- Frozen executable oracle: 3 tests passed before model launch.
- Focused quota/Quick/Ship/route checks after corrections: 414 passed, 18.627 s.
- Full suite: `python3 -m unittest discover -s tests -q`: **1997 tests, 197.602 s,
  OK, skipped=2**. The system interpreter omits wheel checks without `build`.
- Follow-up test-only correction `40be596` anchors the CLI stale timestamp to actual
  test time (the earlier fixed clock could exercise future rather than old input);
  all 7 freshness tests passed again. Production code is unchanged since the full suite.
- Temporary developer environment wheel build/install/execution: **7 passed**,
  3.833 s. No global package installation.
- Natural fixture: **5 passed** in the actual agent turn; mutation-authority files
  unchanged. This is separate from the main suite.
- `git diff --check` passed. Existing fake current quota observations now carry
  current timestamps; dedicated stale tests retain old timestamps. Dry-run capacity
  is synthetic only inside the existing non-executing runner.

## Proposed next experiments, not launched

Quality before efficiency, efficiency before autonomy. No schedule, daemon, new
loop runtime, Harness² integration or API-backed lab was created.

| Priority | Hypothesis / experiment | Bounded resource envelope, not a price |
| --- | --- | --- |
| 1 | Correct actual case cwd and repeat only the two invalid new arms at the frozen version/fixtures; test context isolation before launching. Can it match the independent critical-risk facts? Retain old valid observations, disclose non-contemporaneous pairing. | 2 Sol medium turns, 8 investigation calls and 180 s each; at most 6 agent-minutes. Stop on scope violation; no tuning/retry loop. |
| 2 | Fresh matched native-only vs local-policy natural task. Does routing change a bad decision or add useful coverage, and at what measured uncached-token cost? | 2 matched economical-model turns, 12 calls/180 s each; at most 6 agent-minutes. Same task/model/context budget; freeze oracle first. |
| 3 | A tiny edit actually changes permission or concurrency semantics. Does a host retain required review and stop on UNKNOWN, rather than merely emit advice? | One new fixture, 2 arms, 180 s each; at most 6 agent-minutes. Add one separately budgeted reviewer only if needed; do not auto-approve. |
| 4 / P4 | Existing bounded triage distinguishes a useful correction from repeated environmental failure. Does explicit stop/success evidence prevent unproductive repeats? | One disposable fixture, max 2 attempts/120 s total and a concrete success check; no recurrent loop or external services. |
| Deferred / P5 | Candidate instruction optimization helps a genuinely held-out set rather than fitting known examples. | First design a stable offline train/holdout split and scoring procedure; zero model calls now. Any optimizer run needs a separate finite budget and evaluation isolation. |

These envelopes do not authorize recurring runs. The immediate value is repairing
evaluation validity and checking host behavior, not expanding REIN's feature set.
