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
| Broad consumption/quality improvement | Statistical / held-out evaluation | NOT TESTED | One run/version on development examples; final elapsed time and uncached tokens exceeded baseline |
| Model-authored v2 reports → checker → router | Real Codex + local CLI | PASS | A ready/native; B investigate with UNKNOWN, plan/native/audit; no manual report repair |
| Complex and external-adapter ambiguous live fixtures | Real model investigation | NOT TESTED | Covered by automatic contracts only; B separately exercised an unresolved monetary contract |
| Native default, justified delegation, overrides | Policy contracts | PASS | Multi-repo alone stays native; risk review and unresolved evidence cannot be waived |
| Loop / Goal constraints | Policy contracts | PASS | Explicit request, finite attempts/time, success check, failure classification; nothing launched |
| Loop / Goal host execution | Real integration | NOT TESTED | Advice only; no new execution engine introduced |
| P3 recovery / Harness² | Later-phase integration | NOT TESTED | No new quota policy, watcher or lab in this iteration |

See [results, comparison and validation](P1-P2-RESULTS.md) and its retained raw
reports/metrics. P1 is implemented for this bounded scope; P2 is a first
conservative advisory implementation, not an automatic orchestration system.
