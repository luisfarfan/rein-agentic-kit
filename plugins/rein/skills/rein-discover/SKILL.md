---
name: rein-discover
description: "Codex: $rein:rein-discover. Investigate technical impact before choosing a solution. Read-only exploration, no implementation or plan. Use explicitly for Discover or when asked to think a problem through before planning."
---

Read [provider setup](PROVIDERS.md) once when using REIN in this session.

# Discover

Investigate the natural-language request in this session. Start with `rein context .`
for resolved commands and plan state; reuse it if already current. Use targeted `rg` searches,
bounded reads and existing tests/config. Symbol/caller tools are optional.
File count and the wording of a request do not establish difficulty.
Neither graphs nor a second model are prerequisites.

Budget: at most eight investigation tool calls or three minutes, whichever comes
first, unless the user sets another limit. Batch related reads; count actual calls
and elapsed time, not invented tokens. Reuse valid evidence instead of rereading it.
Use host timing when available. After reading the relevant files completely, do
not repeat inventory or searches without a named unresolved question.
Do not create workers, indexes or model calls; do not switch to Astra by default.

1. Locate the entry point and identify **existing capability** versus **missing
   behavior**. “Agregar soporte para euros” might only require enabling a supported
   currency. Trace the relevant consumer, storage, permissions and tests before
   declaring that sufficient. A single empty search does not prove absence.
2. Follow only relationships that can change the decision: callers, data flow,
   service contracts, persistence/migrations, money, security/tenant permissions,
   concurrency/idempotency, side effects and operational rollback. Name the producer
   and consumer. Check negative cases as well as happy paths; existing support in
   one layer does not establish end-to-end support.
3. Classify separately: **implementation complexity**, **modification risk**,
   **system uncertainty**, **repository scope**, **need for planning**, and
   **need for independent verification**. Use low/high/UNKNOWN/not applicable with
   a reason and concrete file/symbol/test evidence for each, including low and N/A.
   One-line code can be high risk; multiple repos do not force delegation. Do not
   invent confidence percentages. Mark observations versus inferences and identify
   tests merely read versus checks actually executed.
4. Stop when the affected path, boundaries and verification are understood. If a
   missing fact could change risk or scope, name that question and make one targeted
   follow-up within the remaining budget. At exhaustion retain UNKNOWN and state
   the next check; do not silently expand research. Ask the user only for decisions
   that code cannot answer.

Close with existing capabilities, missing behavior, dependencies/contracts, technical
and operational risks, the six classifications, verification and unresolved questions.
Load [impact routing](IMPACT.md) only when constructing a reusable JSON report or
router request; prose discovery does not need that resource. Use its v2 contract
and `rein discover-check`; it checks file freshness, not semantic truth. An artifact
is optional for small changes. Reconsider only affected conclusions when evidence changes.

This explicit Discover invocation is read-only: do not implement, write a plan,
or start workers. Suggest native work, selective planning (`/rein:rein-plan`), or
independent audit as justified. Discovery within an already-authorized implementation
can be brief and does not require invoking this read-only skill.
