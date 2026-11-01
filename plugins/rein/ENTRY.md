## REIN task entry

Inspect relevant code and tests before choosing how to execute. Distinguish existing
capability from missing behavior; trace the affected consumers and boundaries for
permissions, money, persistence, concurrency and external contracts. Keep concrete
evidence, risks, uncertainties and meaningful verification; a short explanation is
enough. Reuse current findings instead of repeating searches or reads.

Continue natively in this session when the evidence is sufficient. Neither wording,
file count nor multiple repositories alone justify a workflow. If an unresolved
question could change correctness or scope, investigate that question before the
affected implementation. Use the existing `rein-discover` guidance for deeper
investigation in this same session, carrying forward inspected paths and open
questions. Explicit investigation-only requests remain read-only. At the research
limit, report the missing evidence and next check rather than guessing.

Use `rein-plan` for requirements/dependencies that need a verifiable plan. Preserve
independent audit for changes to security/permissions, monetary invariants, durable
state, concurrency or service contracts; small implementation effort never waives
those controls. Never approve your own implementation. Quick is for requested
delegation; Ship is for coordination that actually needs workers or saved state.
Use bounded iteration only when requested, with attempt/time limits and a success
check; do not create a loop runtime.

Load only the selected skill and its provider guide when needed. `rein route` and
structured evidence are optional, not a required phase or six-label checklist.
Reconsider affected conclusions when code or assumptions change. Preserve evidence
freshness, worktree isolation, authorization and quota brakes; unknown/unusable
capacity cannot authorize new workers. Missing tools or capacity never imply approval.
