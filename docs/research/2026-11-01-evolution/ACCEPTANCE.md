# Acceptance matrix

P0: **PARTIALLY ACCEPTED**. PASS means the named check only. Automated contracts
are not live provider integrations. FAIL = observed defect; BLOCKED = prerequisite
unavailable; NOT TESTED = no executed check. Historical failures remain in the
[first live report](P0-LIVE.md), including their successful retests.

| Check | Kind | Result | Evidence / remaining work |
| --- | --- | --- | --- |
| Codex native skill discovery | Host protocol | PASS | Seven canonical skills, no errors |
| Claude manifest | Static validation | PASS | CLI validator; not a Claude model turn |
| Codex Discover / Plan | Real Orca session | PASS | First P0 run, read-only Discover and confirmed Plan |
| `plan-check --gate <file>` | Contract + real session | PASS | Both argument orders; real successful retry |
| Fresh model-generated `Repo:` | Real Orca session | PASS | `gpt-6.1-sol` medium, terminal `term_2b6907c2-175c-42de-87fb-05b547ef77ee`; 43 seconds; queried `orca worktree current` then `orca repo show`, generated `p0-live-_sl9us7c` without manual correction or old-plan reads; `/tmp/rein-p0-repo-probe.md`, gate exit 0 |
| Codex Ship, answer and watcher | Real Orca session | PASS | Prior run `run_3f7c9c7c6a38`; not repeated |
| Independent audit and stale verdict | Real fallback + CLI | PASS | Separate Antigravity/Gemini auditor; modifying reviewed bytes rejects approval |
| All skill resources from source | Filesystem contract | PASS | Markdown links traversed via `.agents/skills`; CLI resolved from real SKILL ancestor |
| All skill resources from installed wheel | Packaging contract | PASS | Seven actual installed skills followed to provider/impact guides and packaged CLI in isolated Python outside source checkout |
| Claude → REIN | Real integration | BLOCKED | Last supported quota snapshot: 91% weekly used, brake 90%; no launch or brake edit |
| Claude ↔ Codex Quick | Real integration | BLOCKED | Claude capacity; neither direction counted as passed |
| Claude-origin watcher / answer / audit | Real integration | BLOCKED | Requires eligible Claude initiating session |
| Desktop plugin installation UI | UI integration | NOT TESTED | No global install or UI setup change requested |
| Linux / Windows hosts | Platform integration | NOT TESTED | This iteration runs macOS |

The fresh Repo probe stopped at the draft confirmation boundary. No fixture
implementation, Ship repetition, product change or push followed it. OpenSpec
again reported no specification files; that was reported, not counted as PASS.

## P1 / P2

| Check | Kind | Result | Evidence / limit |
| --- | --- | --- | --- |
| Six independent classifications and evidence freshness | Filesystem + CLI contracts | PASS | Easy/complex/ambiguous/hidden-risk cases; stale, missing, invalid and cross-repo references |
| Progressive natural-language Discover | Real Codex, two synthetic repos | PASS | Existing EUR capability and USD-only ledger risk identified; six calls in final version, no workers |
| First revision consumption discipline | Real measured comparison | FAIL | Extra search and clock call: eight investigation calls; retained in evidence and corrected |
| Final revision tool-call non-regression | Real measured comparison | PASS | Six investigation calls, same as baseline; this is not a token/cost savings result |
| Broad consumption/quality improvement | Statistical / held-out evaluation | NOT TESTED | New paired comparison is ineligible: both new arms violated context-root isolation; superiority and savings remain unestablished |
| Model-authored v2 reports → checker → router | Real Codex + local CLI | PASS | A ready/native; B investigate with UNKNOWN, plan/native/audit; no manual report repair |
| Historical fiscal/concurrency and approval cases | Real model investigation | PASS for old arms only | Old Discover found five frozen dangerous-risk facts across two reductions; new arms invalid, not comparative success |
| Native default, justified delegation, overrides | Policy contracts | PASS | Multi-repo alone stays native; risk review and unresolved evidence cannot be waived |
| Loop / Goal constraints | Policy contracts | PASS | Explicit request, finite attempts/time, success check, failure classification; nothing launched |
| Loop / Goal host execution | Real integration | NOT TESTED | Advice only; no new execution engine introduced |
| P3 full mid-task recovery / Harness² | Later-phase integration | NOT TESTED | Separate recovery branch remains untouched; no recurring lab |

See [results, comparison and validation](P1-P2-RESULTS.md) and its retained raw
reports/metrics. P1 is implemented for this bounded scope; P2 is a first
conservative advisory implementation, not an automatic orchestration system.


## Latest held-out evaluation and P3 follow-up

See [full results and limitations](../2026-11-01-evaluation/RESULTS.md).
P0 is still PARTIALLY ACCEPTED; Claude weekly remained 91%, Codex was 35% at the
recorded observation. Session windows were absent. Passed Codex P0 tests were not
repeated. [Resume procedure](../2026-11-01-evaluation/P0-RESUME.md) covers only the
missing Claude-origin and cross-provider checks.

| Check | Kind | Result | Evidence / limit |
| --- | --- | --- | --- |
| New Discover held-out isolation | Actual model tool inputs | FAIL | Both new arms used coordinator cwd for context; INVALID_CONTEXT_ROOT; retained without retry |
| Independent historical counterexamples | Executable oracle | PASS | Frozen before model runs; duplicates, external-effect gap, existing batch and tenant/permission/grant denials |
| Natural request → inspect → route → native edit → tests | Actual Astra in Orca | PASS | One isolated opt-in local policy, five tests; not automatic installation behavior or savings evidence |
| Stale evidence / risk override / multi-repo / delegation capacity / loop limits | Controlled CLI replays | PASS | Label perturbations are policy checks, not independent model discoveries or host-stop observations |
| Claude/Opus natural routing behavior | Real integration | BLOCKED | Existing quota brake; not forced |
| P3 fresh/unknown/limited quota enforcement | Driver/state/CLI contracts | PASS | Explicit overrides cannot authorize unusable observations; both limited dispatches nothing |
| P3 changed plan/window usage comparison | Automated contracts | PASS | Incomparable or unusable observations produce unknown delta |
| P3 live source observation | Read-only Orca query | PASS | Actual timestamps and numeric windows; not a live exhaustion/recovery experiment |
| Dynamic audit/correction capacity reservation | Implementation / live integration | NOT TESTED | Not implemented; default ten percentage-point reserve is not a fit prediction |

P3 remains PARTIALLY IMPLEMENTED/VALIDATED. Main suite: 1997 tests, OK, skipped=2;
separate temporary-environment packaging suite: seven passed. No push, merge,
release, product edits, global changes or recurring experiments.


## Corrected laboratory and matched native control

The [follow-up](../2026-11-01-followup/RESULTS.md) supersedes the lack of a valid
new-arm comparison, without erasing the earlier failed isolation checks. Exactly
two corrected Sol Medium P1 cells and two matched Sol Medium P2 arms ran.

| Check | Kind | Result | Evidence / limit |
| --- | --- | --- | --- |
| Corrected P1 startup root | Actual Orca shell preflight + model traces | PASS | Correct cwd/context, same prompts/resources/fixtures; no outside-context read observed |
| P1 critical-risk coverage | Frozen independent rubric | PASS | Both old and corrected new cover 5/5 dangerous-risk facts; no additional critical fact found by new version |
| P1 superiority | Comparative inference | NOT TESTED at population level | Valid non-contemporaneous observations; lower measured tokens/time, no causal or broad superiority claim |
| P2 matched final behavior | Independent frozen evaluator | PASS | Both native and REIN pass 19/19; identical functional tuple change and unchanged mutation authority |
| P2 advice followed | Actual execution order | PASS | REIN route preceded edit; native advice followed. Native control independently chose the same strategy |
| P2 net improvement for this task | Paired local outcome | FAIL | No superior final behavior/strategy; 14.9% more uncached input, 57.370 vs 20.753 s, 6 vs 4 tool calls; richer trace/mixed-batch test acknowledged |
| P3 cached data / retry / recovery / pause | Driver and state-machine regression | PASS | Re-read can remain stale; retry does not bypass; usable new data permits dispatch; pause preserves pending work |
| P3 immediate supported forced refresh | Installed CLI / live query | NOT TESTED | CLI exposes no documented force-refresh; immediate mechanism not verified; eventual observed update is not a guarantee |
| P3 freshness and percentage brakes | Policy preservation | PASS | 900 s / clock skew / 90% defaults unchanged; only diagnostics/runbook changed |

P0 remains PARTIALLY ACCEPTED, Claude-origin and cross-provider work blocked at
91% weekly. No automatic routing, global skill activation or new runtime added.

Follow-up final regression: **2000 tests, OK (skipped=2)**; seven separate wheel
checks passed. Two initial message-contract failures were corrected and retained
in the [validation history](../2026-11-01-followup/validation.json).
