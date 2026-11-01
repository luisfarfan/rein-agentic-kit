# REIN entry architecture: implemented and bounded validation

Implementation `fde29c3`, protocol `71df0cd`, baseline `9ec47be`. No new skill,
runtime component, LLM router, hook, daemon, dependency or recurring experiment.
No product repository, global configuration, push, merge or release changed.

## Architecture decision and delivered changes

The current session owns initial inspection and execution choice. Native work is
the default when evidence is sufficient. A question that could change correctness
or scope triggers targeted investigation; deeper questions use the existing
Discover guidance, carrying forward inspected paths and findings. Escalation does
not reset its eight-call/three-minute research budget. Unknowns remain explicit.

Discover's prose no longer requires six classifications or JSON. Its body is
**418 words / 3119 bytes**, down from **496 / 3643**. A context command is conditional
on missing commands/plan information and uses the actual repository root. Explicit
Discover still does not implement, write a plan or start workers. During an already
authorized implementation, the same guidance can deepen a question, followed by
implementation only when evidence and controls permit it. No second agent or
second full reconnaissance phase is introduced.

The optional v2 evidence contract, `discover-check`, `rein route` and all existing
execution/verification core code are unchanged. Their required structured fields
remain required when a caller elects to use that format. They do not become an
automatic prelude to a small edit. Source freshness, risk controls, independent
review, quota brakes, unknown-capacity dispatch refusal and worktree safeguards
retain their existing implementation and regression coverage.

The shared [entry template](../../../plugins/rein/ENTRY.md) has **257 words / 1921
bytes**. It names when to use planning, independent audit, requested Quick,
coordination through Ship and explicitly bounded iteration. Complexity, file count
and repository count do not waive review or justify workers. README now presents
the old planned-work lifecycle as optional, rather than every task's entry path.
There is no `rein-impact` skill and the canonical skill count remains seven.

## What starts in Orca, and what installation actually means

The [public setup recipe](../../native-providers.md) has one project-adoption step:
merge the installed ENTRY.md block into the project's AGENTS.md, preserving local
instructions; have CLAUDE.md import `@AGENTS.md` when needed. This checkout has that
exact block, checked against its canonical template, with the existing one-line
CLAUDE.md import. It is not copied into each SKILL.md. A project adopts and reviews
template updates explicitly; no installer silently rewrites instruction files.

After adoption, start Claude/Codex in the intended Orca project/worktree and write
the ordinary task. The user need not choose Discover, Plan or route on every turn.
Only the entry body and the host's usual metadata/instructions load initially;
selected skill bodies and resources load when needed. Do not preload IMPACT.md or
all skill bodies. Project-specific instructions still need room under host limits.

Codex's documented startup discovery reads AGENTS.md through the project hierarchy,
subject to overrides and size limits. [Official Codex documentation](https://learn.chatgpt.com/docs/agent-configuration/agents-md).
Claude documents local imports and conditionally reading AGENTS.md directly; the
existing import keeps a single body across those modes and older configurations.
[Official Claude documentation](https://code.claude.com/docs/en/memory).
A plugin-root CLAUDE.md is not automatically loaded, so the policy cannot be made
universal merely by adding that file to the plugin. [Plugin reference](https://code.claude.com/docs/en/plugins-reference).

**Plugin-only installation is not automatic entry-policy activation.** A repository
without adoption retains ordinary native behavior and available explicit skills.
Supported startup instruction loading is not enforcement or guaranteed model
compliance. Trust, exclusions, overrides, limits, cwd and provider behavior matter.
No complex hooks were added to conceal that limit. No production project was
silently opted in. This limitation is part of the architecture, not a pending fake
automation step.

## How the validation differs from the earlier special fixture

Exactly four fresh model turns ran under the [frozen protocol](PROTOCOL.md), at
most twelve tools/180 seconds each. A is a matched Sol Medium pair; B/C use Astra
High, the user's normal Codex initiating model. No extra reviewer or retry followed.
These are **reused development fixtures**, not new blind cases or a statistical
comparison. B/C have no new matched native controls; prior studies are context only.

A real wheel was built and installed in a temporary venv. Each consuming project
linked its seven packaged skills locally. Entry arms copied the wheel's ENTRY.md
unchanged into AGENTS.md and used the documented CLAUDE.md import. The native arm
had the same available skills but no policy. There were no task-specific routing
instructions, low-risk labels or mandatory JSON in AGENTS.md. Common prompts asked
for EUR support and imposed only evaluation scope/resource limits.

[Preflight](preflight.json) verified actual cwd/context from Orca shells, independent
Git roots, no inherited plans/remotes, initial hashes and actual Codex app-server
discovery of the seven wheel skills. The checker initially compared `/tmp` with
macOS's canonical `/private/tmp`; normalizing both fixed the checker before any
model launch. This was neither a package defect nor an agent rerun.

Actual session metadata confirms requested models/efforts and correct cwd. The
exact 257-word policy occurs **once in startup user-instruction messages** for each
entry arm and zero times in the control. This is actual host injection evidence,
not merely a file existing on disk. Models sometimes read AGENTS.md again with
other files; we do not claim zero redundant reads or a token-free policy. Isolation
is prompt-scoped, not an OS sandbox; no cross-case/oracle access was observed.

## Observed behavior

| Scenario | Actual result | Acceptance and limits |
| --- | --- | --- |
| A native: already-supported EUR disabled | Native tuple edit, existing tests and inline EUR/USD/unsupported checks | Independent checks pass; no persistent EUR regression added |
| A entry: identical task/code/model | Native tuple edit plus persistent EUR test; three local tests and negative/invariant checks | Independent checks pass; **no skill body, context CLI, route or JSON** used |
| B: flag change hides USD-only ledger | Traced checkout → ledger, reproduced EUR being labeled USD, loaded audit/Discover guidance, retained EUR disabled | Risk/control behavior passes; added a fail-closed ledger guard and four tests, but EUR support and independent review remain pending |
| C: provider/adapter architecture absent | Read forwarding code, then selected Discover without a manual user invocation; reused that finding and tested the boundary with mocks | Four local probes pass; **no code changes**, real EUR support remains UNKNOWN pending actual provider/currency/amount contract |

[A's independent checks](A-entry-check.json) verify EUR enabled, USD preserved and
unsupported currency rejected; [control results](A-native-check.json) also pass.
Both implemented the same functional configuration change. A entry added a useful
persistent test, but this single observation does not prove higher general quality.
There was no routing overhead imposed on A beyond the entry instructions themselves.

B did not claim it had implemented EUR settlement. Its final answer and README
explicitly preserve the missing contract and independent-review requirement. The
baseline [counterexample](baseline-B.json) shows silent mislabeling; repeating that
probe after the guard now raises `ValueError` ([raw result](B-after-counterexample.json)).
That probe's nonzero exit is retained: it demonstrates refusal, **not** successful
EUR support. The agent's four regression tests cover USD, disabled EUR, direct
non-USD writes and an accidental configuration flip. Its guard patch is unreviewed
local fixture work, not a state-bound approval or completed monetary feature.

C's mock checks establish argument forwarding, USD preservation and propagation of
provider rejection. They cannot establish that an absent deployed provider accepts
EUR, nor determine amount units. After reading Discover, the agent did not repeat
its payments.py read or create a separate investigation session; it performed a
targeted boundary probe and named the missing contract. Initial file inventories
still overlap, so complete elimination of redundant research is not demonstrated.

All answers, actual tool inputs/outputs, patches and B's newly created test are in
[reports](reports/). [Scope checks](scope-checks.json) confirm unchanged instructions,
no remotes, no EUR-enable edit in B, and a clean source tree in C. No independent
auditor was launched or approval invented. No Quick/Ship/Loop was executed to make
this entry test larger.

## Observable overhead

[Metrics](metrics.json) are completed-session counters, excluding parent coding,
tests and evaluation work. Total = input + output; cached input is a subset of
input. Reasoning is not added again. No dollars or per-task quota usage is inferred.

| Arm | Model / effort | Input | Cached input | Uncached input | Output | Total | Seconds | Tools |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A native | Sol / medium | 115482 | 108160 | 7322 | 751 | 116233 | 26.327 | 6 |
| A entry | Sol / medium | 99735 | 83968 | 15767 | 838 | 100573 | 25.251 | 5 |
| B entry | Astra / high | 178527 | 167040 | 11487 | 2679 | 181206 | 82.106 | 10 |
| C entry | Astra / high | 120910 | 111744 | 9166 | 1246 | 122156 | 42.298 | 5 |

A entry used one fewer tool, 4.1% less elapsed time and 13.5% fewer total tokens,
but **8445 more uncached input tokens (+115.3%)**. Cache reuse differed substantially;
the policy's 1921 bytes cannot alone explain that entire delta. The observed
uncached overhead increased, so **there is no demonstrated net consumption saving**.
No cache warmup, prompt optimization or repeat run was used to improve the result.
B/C use another model and different tasks; their totals are not comparable to A
as treatment effects or to the earlier mandatory-JSON experiment.

Static size did improve where intended: Discover's selected body shrank 15.7% by
word count and 14.4% by bytes. Project adoption adds 257 always-loaded policy words
(plus any project instructions); this repo's full AGENTS.md grew from 393 to 2365
bytes, while CLAUDE.md stays 11 bytes. Keeping a policy small reduces its footprint
relative to a large workflow prelude, but cannot make it cost-free.

The provider guide grew from 286 to 329 words to explain adoption. Counting both
the selected Discover body and that guide, their combined size fell from 782 to
747 words (5679 to 5439 bytes), a smaller reduction than Discover alone. These
[static sizes](instruction-size.json) are not model-token measurements and exclude
the new always-loaded entry block.

## Validation and remaining work

- Full suite: **2001 tests, 214.152 s, OK (skipped=2)**.
- Focused provider/skill/discovery/routing checks: **57 passed**, 1.495 s.
- Wheel build/install/execution: **7 passed**, 4.264 s; ENTRY.md is packaged.
- Actual installed Codex discovery: seven skills in all four project roots.
- Claude 2.1.316 manifest validation passed, with existing nonblocking icon advice.
  Codex CLI was 0.166.0. Source/installed bodies, JSON artifacts, links and diff
  whitespace were checked. See [validation record](validation.json).

P0 remains **PARTIALLY ACCEPTED**. At the last launch observation (08:15:56 UTC),
Claude was 91% weekly against the unchanged 90% brake, and Codex 43% on the reported
weekly window. No Claude model turn or passed live P0 Codex workflow was repeated.
Claude's documented import and manifest validation do not substitute actual Opus
behavior. Its native entry, cross-provider Quick and pending P0 cases remain blocked.

The architecture is implemented and the bounded Codex behavior is observed; broad
reliability and efficiency are not established. Production installation/UI,
cross-platform behavior, arbitrary instruction overrides, every strategy transition,
mandatory audit execution and Claude behavior were not validated here. Keep routing
optional and project adoption explicit. Further autonomy needs evidence, not another
component.
