---
name: rein-discover
description: "Codex: $rein:rein-discover. Investigate technical impact before choosing a solution. Read-only exploration, no implementation or plan. Use explicitly for Discover or when asked to think a problem through before planning."
---

Read [provider setup](PROVIDERS.md) once when using REIN in this session.

# Discover

Investigate in this session. Start with `rein context .` for resolved commands and
plan state. Use targeted `rg` searches, bounded reads, and existing tests/config;
use symbol/caller tools when available and useful. File count alone is no reason
to create workers. Neither graphs nor a second model are prerequisites.

Follow the capability from its entry point to the relevant consumer, storage,
permissions and tests. Read only enough to settle the decision. A request such
as “enable EUR” could be an existing configuration switch or a change to money,
orders, payments, refunds and reporting: inspect the code before deciding.

Assess separately: functional scope, implementation complexity, operational
risk, uncertainty, persistence/migrations, security/tenant permissions,
concurrency/idempotency, service contracts, test coverage, side effects and money.
For each material conclusion cite a file/symbol, contract or executed check.
Distinguish observed support from an inference; “no match in one search” does not
prove absence. A low or not-applicable finding also needs a reason and reference.

Use progressive depth: stop a light investigation once the affected path and
verification are understood. Trace another boundary when missing evidence could
change scope, risk or acceptance. Escalate research or model capability only for
a named unresolved question; say UNKNOWN if evidence remains insufficient.
No fixed minimum file count, mandatory exhaustive scan, or model call.

Close with what exists, likely changes, impact evidence, verification available,
and unresolved questions. Ask only for decisions the code cannot answer. If a
machine-readable recommendation is useful, use the assessment contract in
[impact routing](IMPACT.md); an artifact is optional for small changes.

This explicit Discover invocation is read-only: do not implement, write a plan,
or start workers. Suggest the appropriate next step, which may be native work,
selective planning (`/rein:rein-plan`) or independent audit. Discovery within an already-authorized
implementation can be brief and does not require invoking this read-only skill.
