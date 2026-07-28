# Decisions

Short records of choices that are not obvious from the code, so nobody re-opens
them without knowing why they were closed.

---

## D1 — A `Dockerfile` does not make a repository infrastructure

**Decided 2026-07-28.**

`Dockerfile` was in `INFRA_FILES` from phase 0. It was inert while subtypes were
only informational. Phase 2 turned subtypes into a **policy** (`plan-only` forbids
`deploy`/`apply`/`destroy` as a means of verification) and made the marker scan
descend two directories — at which point every containerised application was
classified as infrastructure.

Measured on real repositories: three ordinary apps (`codeborn`, `firecrawl`,
`futbol-manager`) resolved to `plan-only` purely because they had a `Dockerfile`
in a subdirectory.

**A `Dockerfile` is a build artifact, not infrastructure-as-code.** It describes
how to package a service; it does not declare infrastructure. The remaining
markers — `serverless.yml`, `serverless.ts`, `sst.config.ts`, `template.yaml`,
`*.tf`, `*.tfvars` — all declare infrastructure itself.

After the change those three repos resolve to `unit`, and `proxima` still
resolves to `plan-only` because it genuinely contains terraform. That is the
discrimination the policy exists to make.

**Consequence:** a containerised app that *is* infrastructure-managed can still
opt in with `verify.mode: "plan-only"` or `subtypes: ["infra"]` in
`flow.config.json`. Explicit intent always wins over detection.
