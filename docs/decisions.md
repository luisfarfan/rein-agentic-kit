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

---

## D3 — Linear is the only source an intake reads

**Decided 2026-08-28.**

The 50 bug issues on the `pproxima` board each point at a long-form document in
a second repository (`proxima-qa/docs/bugs/<id>.md`), and the first design read
that document: it is cleaner markdown, and 26 of the 50 carry an
`## Arreglo sugerido` section the board body does not.

**Reading a second repository was rejected anyway**, and the reason is
operational rather than aesthetic: that repo has to be cloned and current for an
intake to run. The working clone was two commits stale when this was measured —
the documents existed on the remote and not on disk. A source that can silently
be out of date is how one question grows two answers.

What made it the sturdier choice rather than merely the safer one: **most of
what an intake needs is a typed API field, not markdown.** `title`, `priority`,
`state`, `labels`, `parent` and `branchName` all arrive structured, and no regex
can get them wrong. Only two facts live in the body — who found it, and which
flows it touches.

**Consequence:** when the board is missing something, the board is what gets
fixed. `## Arreglo sugerido` is absent from every Linear body today and belongs
upstream, in whatever writes the issues.

---

## D4 — The Linear↔Beads link lives in a record file, because Beads cannot hold it

**Decided 2026-08-28.**

`rein land` has to know which Beads issue closes which Linear issue. Beads looks
like it can carry that and, measured against `bd 1.1.0`, cannot:

| attempt | result |
|---|---|
| `bd create --external-ref` (its help names Linear) | stored, but absent from `bd show --json` **and** `bd export` |
| `bd search PPR-86` | 0 hits, on an issue whose description contains the string |
| `bd query 'description=PPR-86'` | 0 hits — while `description=PPR` returns it |
| `bd query 'description=Reportado'` | 0 hits, for a description containing the word |

The hyphen alone loses it. So the link is `<repo>/.rein/linear_record.json`, and
`--external-ref` is still written for whoever reads the Beads UI — never relied
on.

**Consequence:** a lost record is not fatal and must never be guessed around.
`land` refuses by name and takes `--bead <id>` instead. Closing the wrong issue
is worse than stopping.

---

## D5 — "Merged" is checked against the base branch log, not the commit graph

**Decided 2026-08-28.**

`rein land` refuses to mark an issue Done when the work did not ship. The obvious
check is `git branch --contains <sha>`, and it is wrong here: a squash merge
writes a **new** commit, so the branch's own sha is nowhere in the base.

Measured on PPR-90, which shipped as `08efd151`:

```
git branch --contains c10f983a  →  only the feature branch. develop absent.
```

The graph check would have refused to close work that was already on `develop`.
What survives a squash is the identifier in the commit subject, so that is what
is searched.

**Consequence:** the check depends on the commit convention naming the issue.
When it does not, `--force` exists and says so in the refusal message.

---

## D6 — Beads is opt-in through the repo's own `tracker.kind`

**Decided 2026-08-28.**

`flow.config.example.json` already carried this contract — *"none = tasks.md
checkboxes are the state. beads = also sync/close Beads issues (requires the bd
CLI)"* — and the first version of `rein intake` ignored it, shelling out to `bd`
on every run. That is wrong twice: it files a Beads issue in a repo that declared
it does not use one, and it fails outright where `bd` is not installed. All three
`proxima` repos declare `"none"` today and got Beads issues anyway.

Skipping Beads is not a degraded intake. The branch, the record and the board
move are the parts that always apply; the Beads issue is the part the repo opts
into.

**Related, and decided at the same time: there is no `rein linear state`.**
Moving an issue to Done belongs to `rein land`, behind D5's check. A free-form
state command would route around that guard, and the guard is the point. When a
write is genuinely missing, the answer is a subcommand — `rein linear comment`
exists because an agent otherwise reached for the raw GraphQL endpoint with the
personal API key, which spreads the credential across ad-hoc calls with no
bounded surface and nothing to audit.
