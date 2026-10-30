# Brief: build one screen task package (hidden tests + intent)

You are preparing the **oracle** for one candidate of a pre-registered experiment that compares
coding-agent harnesses. Rigor matters more than speed: a weak hidden test makes the whole
experiment wrong. Read first:

- `docs/research/2026-10-28-post-sdd/CRIBA.md` §4 (test classes, admissibility, validation matrix)
  and §6 (what *Correcta* means). These rules are binding.
- `pilot/screen.json` → `candidates.<cid>`: change name, base commit per repo, prior, and the
  `evidence` (BLOCKING audit findings and later owner fixes, with commit hashes).
- `pilot/screen.py` (the `validate` command and the `hidden.json` format in its comment block).
- A finished example: `/Users/luisfarfan/Developer/sandbox/rein-pilot/screen/obs-cors/task/`.

## Hard limits

- Product repos under `/Users/luisfarfan/Developer/products/proxima/` are **read-only**: only
  `git -C <repo> log/show/diff/grep/cat-file`. Never checkout, commit, stash, or write there.
  `screen.py validate` makes its own clones under `/Users/luisfarfan/Developer/sandbox/rein-pilot/screen/<cid>/validate/`.
- Do not touch the REIN repo except reading `pilot/` and `CRIBA.md`. Do not edit `screen.py` or
  `screen.json`; if the tool is in your way, say so in your report.
- Never run `claude`, `codex`, `rein` or any agent. No network beyond what the stack setup does.
- Postgres for proxima-api is the container `rein-pilot-pg` (port 56543). If it is `Exited`,
  `docker start rein-pilot-pg`. Never remove or recreate it.
- Write only inside `/Users/luisfarfan/Developer/sandbox/rein-pilot/screen/<cid>/task/`.

## Steps

0. **Check the base first.** Two candidates had a base that already was the first implementation
   commit (or a commit of the run's own branch, not on `develop`). Confirm that the base in
   `screen.json` is the state the implementer started from: it must not contain any of the change's
   code. If it is wrong, stop and report the right base with evidence; do not build on a wrong one.
1. **Plan.** Find the change's `tasks.md`:
   `git -C <repo> log --all --diff-filter=A --name-only --format=%h -- 'openspec/changes/<change>/tasks.md' 'openspec/changes/archive/*<change>/tasks.md'`
   in the candidate's repo(s) and in `proxima-engineering`. Take the version as it was **when the
   implementation started** (the commit closest before the first implementation commit after the
   base), not a later edited one. Save it as `task/plan.md`, then run
   `python3 pilot/screen.py intent <cid>` (cwd: `docs/research/2026-10-28-post-sdd`) and read
   `task/intent.md`: it is everything an arm will know.
2. **Versions.** Identify:
   - `final`: the implementation's last commit before archive (per repo);
   - every defect in `evidence`: a `prefix` (code with the defect) and a `postfix` (defect fixed).
     For a fix inside the run, use the fix commit's parent and the fix commit. For a later owner fix,
     prefer `{"at": final, "patch": "fix-<k>.patch"}` built from the fix commit's **non-test**
     diff, so unrelated later changes do not leak in;
   - `stub`: `{"at": final, "patch": "stub.patch"}` — final with the change's core behaviour
     hollowed (no-op / empty returns) but still importable and compiling. It exists to catch
     vacuous tests. If base already is a true null implementation of everything the tests check,
     `stub` may be base (say why in NOTES).
3. **Classify** before writing code:
   - every «Done when» criterion gets ≥1 **F** test;
   - every defect is **D-spec** (violates a criterion: cite it), **D-impl** (violates a property any
     senior assumes: data loss, duplicated effects under concurrency, broken main flow), or
     **excluded** (an owner preference not deducible from the intent: no test, just a note).
4. **Write tests** in `task/hidden/<repo>/`, named `*zz_hidden*` / `*-zz-hidden-*`, with a `dest`
   where the repo's runner finds them. Rules (CRIBA §4.2): only interfaces from the base or named in
   the intent; no mocks of internals, no call-order or wording assertions the intent does not fix;
   deterministic (fake clocks only where the intent speaks of time, no real network). D tests must
   also assert the positive path, so «nothing happened» cannot pass them. If a test truly needs a
   public name the intent does not give, add the **smallest** line to `task/intent_extra.md`
   (every arm gets it) and rerun `intent`. Prefer rewriting the test.
5. **hidden.json** (format in `screen.py`). Test keys are substrings of the JUnit name and must be
   unambiguous. Mark F tests that already pass on base as `"guard": true` — they are reported, but
   be suspicious: a criterion whose every test is a guard has no signal; try to test it better.
6. **Validate**: `python3 pilot/screen.py validate <cid>` (each test 3× on every version). Iterate
   until it prints `OK`. Never weaken a test only to satisfy the matrix; if a test cannot be made
   valid, drop it and record why. You may validate one version with `validate <cid> <version>`.
7. **NOTES.md** in `task/`: criterion → tests map; each defect with class and reason (or why
   excluded); guards; `intent_extra` justification; anything ambiguous a human should rule on.

## Report back (short)

cid, plan source commit, versions used, test counts per class (F / guard / D-spec / D-impl),
excluded defects, validation `OK` or the remaining problems, and the ambiguities from NOTES.md.
