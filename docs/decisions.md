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

---

## D2 — Serena stays wired, but its run-level effect is UNMEASURED

**Decided 2026-07-29.**

Serena was installed, registered as an MCP server, and wired into the loop's
retrieval discipline (gated on `serena-project`, so an unactivated repo is never
told about it). Two things are true and must not be conflated.

### What is measured, per call

On this repo, `plugins/rein/lib/detect.py` (697 lines):

| | tokens |
|---|---:|
| `Read` (whole file) | 7,097 |
| `get_symbols_overview` | 178 |

**40×.** And `find_symbol --include-body` returned one function with its line
range in ~350 tokens and a single turn, where grep-then-read is two. Those
numbers are real and they are why the wiring stays.

### What is NOT measured, per run

The hypothesis was that this reduces the **41 median turns an agent spends before
its first edit** — the metric that matters, because cost is quadratic in turns.

The experiment did not answer it, and the one control available points the other
way:

| Agent | serena calls | turns to first edit |
|---|---:|---:|
| implementer | 6 | 29 |
| fix | 2 | 14 |
| implementer | **0** | **12** |

The agent that used Serena most oriented **slowest**; the one that never touched
it oriented **fastest**. Run-level orientation did drop (41 → 14 median), but the
change was deliberately two small tasks (its own D1 says so), so task size is the
simpler explanation. `n=3` is far too small to conclude Serena *hurts* either.

**Therefore: no win is claimed.** The kit's README must not advertise a retrieval
speedup on this evidence.

### What would settle it

A run on a change of the same shape as the baseline — three tasks, five to seven
acceptance criteria each — comparing turns-to-first-edit with the wiring on and
off. Until that exists, this is an installed capability with a measured per-call
advantage and an unknown effect on what actually costs money.

### Two claims in this project's memory that did not survive checking

- **"Serena needs a Python language server (pyright/pylsp) that is not installed."**
  False as stated. It ships support for 40+ languages and manages the servers
  itself; it worked on this repo with neither installed.
- **"Serena indexes per worktree, which is overhead for ephemeral agents."**
  That was *graphify* — confirmed at 37 MB across five worktree indexes in the
  origin project. Serena initialises once per environment.

Both were repeated in earlier analysis without verification.
