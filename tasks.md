# Change: fits-a-real-ecosystem

## Why
Installed into two real repositories, the kit reported the wrong problem
three times and was hard to read once.

**`proxima-engineering`** governs nine repositories (`proxima-api`,
`proxima-admin`, `proxima-builder`, `proxima-storefront-v2`,
`proxima-intelligence-v2`, `proxima-pos`, `proxima-app`, `proxima-infra`,
`proxima-runtime`) and provisions each one. Its own CI runs `mise run ci`,
and its README documents `mise run setup` / `mise run doctor`. **`mise` is
that ecosystem's task runner**, and `detect` knows only justfile, Makefile
and Taskfile — so it found no runner, found no root manifest, and fell
through to the monorepo branch:

```
stack      : monorepo
subproject : (none chosen)
commands   : (none)
MISSING    : test, testOne, lint, typecheck
```

That repository is not a monorepo. It has governance content at the root and
**exactly one** directory carrying a manifest (`harness/pyproject.toml`).
Reporting "choose a sub-project" over a list of one sends the operator to a
problem that does not exist — the mirror of the mistake D1 was written to
prevent.

**`make-montages`**, with everything resolved correctly, still printed:

```
plan : NOT FOUND at /…/openspec/changes/tasks.md
```

That path can never exist: openspec plans live at
`openspec/changes/<change>/tasks.md`, and with no change named the join
produces a file nobody could create. Six real changes sit in that directory,
unmentioned.

And the output itself: **not one colour code in the entire CLI**, columns
that break alignment on a longer command name, and `setup --install` printing
`present but inert: codegraph` and `add to .gitignore` in the same run that
indexed it and wrote the file — a summary of the state before its own work.

## Scope
- In: `mise` as a task runner, in the tier where task runners already live
- In: the two "reports a problem that does not exist" defects — a single
  sub-project, and an openspec plan with no change named
- In: making the CLI readable — colour, alignment, and a summary that
  describes the end state
- Out: changing anything in proxima-engineering or make-montages. rein is the
  guest; a kit that needs nine repositories to change their task runner has
  the adaptation backwards
- Out: colour in machine-facing output. Agents parse this; see D4
- Out: the monorepo behaviour itself for a genuine monorepo — reporting
  sub-projects and refusing to pick one stays exactly as it is

## Decisions
- D1 rein adapts to the ecosystem, never the reverse. `mise` is the standard across nine repositories and it works; teaching rein means zero `flow.config.json` per repo, and refusing to means nine hand-maintained files that will drift
- D2 One is not many. A root with a single manifest-bearing directory is a project in a subdirectory, not a monorepo — resolve its commands with the path rather than demanding a choice between one option
- D3 Never report a problem at a location that cannot exist. When a plan source needs a name nobody gave, say which names are available; a NOT FOUND at an impossible path is worse than silence
- D4 Colour is for humans only, and never changes a byte anyone parses. `--json`, a pipe, a redirect and `NO_COLOR` all produce exactly what they produce today — the loop's own agents read this output
- D5 A summary describes the state after the work, not before it

---

- [ ] T001 mise is a task runner like the others
  - Type: implementation
  - Depends on: none
  - Human review: false
  - Verification: `python3 -m unittest tests.test_mise_runner`
  - Acceptance:
    - a `mise.toml`, `.mise.toml` or `.config/mise/config.toml` at the root is detected as the task runner, its `[tasks]` are parsed, and a task named `test` / `lint` / `typecheck` / `check` resolves the matching command as `mise run <task>` with `commandSources` attributing it to `mise`
    - precedence is unchanged and asserted: `flow.config.json` still wins over mise, and mise still wins over autodetection — a fixture with both a config and a mise file resolves to the config
    - a repo with a mise file that declares none of the slots resolves what it can and leaves the rest to autodetection, rather than reporting the runner and no commands
    - a malformed or unreadable mise file degrades to autodetection with the reason recorded, never a traceback — the same rule every other runner already follows
    - `tests/test_detect.py` passes unchanged, so no existing stack changes its resolution

- [ ] T002 Never report a problem that does not exist
  - Type: implementation
  - Depends on: T001
  - Human review: false
  - Verification: `python3 -m unittest tests.test_single_subproject`
  - Acceptance:
    - a root with no manifest and exactly ONE manifest-bearing directory resolves that directory's commands, carrying its path so they run from the root, with `commandSources` naming it — and `stack` reports that project's own stack, not `monorepo` (D2)
    - two or more manifest-bearing directories keep today's behaviour exactly: `stack: monorepo`, no root commands, and the "choose a sub-project" entry — covered by a test using the `proxima-engineering` shape (one) and a two-subproject shape side by side
    - with `source: openspec` and no change named, `plan.path` is NOT a join that produces `openspec/changes/tasks.md`; the report names the changes that exist and says a change must be chosen, and a test asserts the impossible path never appears (D3)
    - an openspec directory with no changes at all reports that, distinctly from "you did not name one"
    - `rein detect` on `proxima-engineering`'s real shape resolves `harness`'s commands, and a fixture reproducing that shape is checked in

- [ ] T003 The CLI is readable, and still machine-safe
  - Type: implementation
  - Depends on: T002
  - Human review: false
  - Verification: `python3 -m unittest tests.test_cli_presentation`
  - Acceptance:
    - `doctor` and `setup` colour their output — status markers, headings and the source attribution — through ONE helper, and a test asserts every colour in the output comes from it rather than from an inline escape
    - colour is emitted only when stdout is a TTY, and never when `NO_COLOR` is set, `--json` is passed, or the output is piped; a test captures piped output and asserts it contains no escape byte at all (D4)
    - a test asserts the piped text output is byte-identical to what the previous version produced for the same fixture, so nothing that greps this breaks
    - the resolved-commands table aligns on the longest slot name actually present, so a long name like `harnessTest` cannot break the columns — asserted with a fixture containing one
    - `setup --install` prints its summary AFTER its work: a repo whose index it just built is not listed as inert, and a `.gitignore` it just wrote is not still being suggested (D5)
    - `rein doctor --json` keeps every key unchanged, asserted against the pinned set
