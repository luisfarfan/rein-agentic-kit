---
name: rein-discover
description: "Codex: $rein:rein-discover. Deepen investigation when unresolved architecture or impact could change the solution, or when explicitly asked to investigate before implementing. Reuse current findings. Explicit Discover is read-only: no implementation or plan."
---

Read [provider setup](PROVIDERS.md) once when using REIN in this session.

# Discover

Deepen the unresolved question in this session using existing findings, inspected
paths and checks. Do not restart reconnaissance. Use `rein context <actual-repo-root>`
only for missing commands/plan state; confirm the working directory first. Use
targeted `rg` searches, bounded reads and existing tests/config; graphs and caller tools are optional.

Budget: at most eight investigation tool calls or three minutes, whichever comes
first, unless the user sets another limit. Count earlier investigation toward this
budget; escalation does not reset it. Batch reads and use host timing. Do not repeat
inventory or reads without a new question or changed evidence. Do not create workers,
indexes or model calls, or switch models by default.

1. Locate the entry point and identify **existing capability** versus **missing
   behavior**. Trace the relevant consumer, storage, permissions and tests before
   declaring existing support sufficient. An empty search does not prove absence.
2. Follow only relationships that can change the decision: callers, data flow,
   service contracts, persistence/migrations, money, security/tenant permissions,
   concurrency/idempotency, side effects and rollback. Name producer and consumer;
   check negative cases as well as happy paths.
3. Explain the evidence, risks, unresolved questions and verification that can change
   the decision. Cite concrete files/symbols/tests, distinguish observations from
   inferences and checks read from checks executed. No six classifications or JSON
   are required for prose discovery. A small diff can need independent review;
   multiple repos do not force delegation.
4. Stop when the path, boundaries and verification are understood. Otherwise make
   one targeted follow-up within the remaining budget. At exhaustion retain UNKNOWN
   and the next check; do not silently expand research. Ask the user only for
   decisions code cannot answer.

Close briefly with the supported behavior, relevant evidence, risks, uncertainties
and verification. Explain plan/review/execution implications only when they matter.
Load [impact routing](IMPACT.md) only for optional reusable evidence or routing.
Its v2 contract and `rein discover-check` retain their required fields and freshness
checks; neither proves semantic truth. Reconsider only affected conclusions.

An explicit Discover invocation is read-only: do not implement, write a plan or start workers.
Suggest native work, selective planning (`/rein:rein-plan`) or independent audit as
justified. Initial inspection during authorized implementation needs no skill;
deeper investigation may reuse this guidance. Resume implementation only when the
missing evidence and required controls permit it.
