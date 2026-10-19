# Rein Quick Lane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `rein quick "<sentence>"` opens a cheap agent picked by quota in a chosen workspace for a
conversational small change, and `rein quick done` closes it with a light gate and an audit only
for risky paths.

**Architecture:** Pure decisions in `lib/quick.py` (no subprocess, no Orca); I/O in
`lib/quick_driver.py` (Orca CLI, git, gate runners, `.rein/quick.json`); a `quick` subcommand in
`bin/rein` and a `quick` skill. Orca's own `worktree create --agent` and `agent launch` do the
launching; there is no coordinator loop. `/ship` is untouched apart from one extracted helper.

**Tech Stack:** Python ≥3.9, stdlib only, `unittest`. Orca 1.4.229 CLI.

**Spec:** `docs/superpowers/specs/2026-10-19-rein-quick-lane-design.md`

## Global Constraints

- Python ≥3.9, stdlib only; `from __future__ import annotations` in new modules (see
  `tests/test_python39_compat.py`). Run tests with `python3 -m unittest discover -s tests`.
- Exit codes: 0 green/ok, 1 red (or nothing launched because every provider is braked), 126 setup
  (Orca, git, timeout, missing terminal), 2 usage.
- The implementer is never Opus unless the owner passes `--agent claude --model opus`.
- Default base branch `develop`. Worktree name `quick-<slug>`, slug ≤ 30 characters.
- Per-command gate timeout: `ship.gateTimeoutSeconds` (`ShipConfig.gate_timeout_seconds`, default
  900). At most 10 related tests before falling back to the `test` command.
- Audit: only when a changed file matches `ship.riskyPaths`; reviewer Opus, or the first unbraked
  of Codex then Sonnet when Claude is braked; none braked-free → 126. Wait at most 20 minutes.
  At most 2 audit rounds per task.
- Summary error lines: lines matching `error|FAIL|✗|×|AssertionError` (case-insensitive for
  `error`/`fail`), two lines of context each, capped at 40 lines.
- Record file: `<workspace>/.rein/quick.json`, schema `version 1` with keys `repo, workspace,
  base, base_sha, agent, model, sentence, linear, started_at, audit_rounds`.
- Rein never commits, pushes, merges or deletes the record in this lane.
- Commits in this repo end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- A sentence that is only punctuation, accents or emoji → the slug is ASCII-folded and falls back
  to `task`; the brief is passed to Orca as one argv element, never through a shell (Task 2).
- `--here` in a checkout whose only change is untracked files → still dirty and refused, but
  Rein's own `.rein/` entries do not count (Task 4).
- `rein quick done` run from a subdirectory of the workspace → finds the record at the git root
  (Task 5).
- A changed file that was deleted → it is never passed to `testOne` and does not crash the
  related-test lookup (Task 3).
- A gate command that prints megabytes or invalid UTF-8 → the summary stays within 40 error
  lines and the run does not crash (Task 3).

---

### Task 1: Orca launch fixtures and client methods

Needs the owner's approval before Step 1: it creates and removes one throwaway worktree in the
rein repo and launches one Haiku agent that does nothing.

**Files:**
- Create: `tests/fixtures/orca/worktree_create.json`, `tests/fixtures/orca/agent_launch.json`
- Modify: `tests/fixtures/orca/README.md` (two command rows, the JSON paths you find)
- Modify: `plugins/rein/lib/orca_client.py`
- Test: `tests/test_orca_client.py`

**Interfaces:**
- Produces: `OrcaClient.worktree_create(repo: str, name: str, base: str, agent: str, model: str,
  prompt: str, linear: str = "") -> dict` running `worktree create --repo <repo> --name <name>
  --base-branch <base> --agent <agent> --model <model> --prompt <prompt> --setup run --activate`
  (+ `--linear-issue <linear>` when non-empty).
- Produces: `OrcaClient.agent_launch(worktree: str, agent: str, model: str, prompt: str) -> dict`
  running `agent launch --worktree <worktree> --agent <agent> --model <model> --prompt <prompt>
  --focus`.
- Produces: `created_worktree_path_of(worktree_create_result: dict) -> str` and
  `launch_handle_of(result: dict) -> str` (the agent terminal handle, `""` when the launch opened a
  chat), using the JSON paths from the captured fixtures.

- [ ] **Step 1: Capture.** From an Orca terminal in the rein checkout, run
  `orca worktree create --repo path:$PWD --name rein-fixture-quick --base-branch main --agent claude --model haiku --prompt "Reply OK and stop." --setup skip --json`,
  then `orca agent launch --worktree path:<that path> --agent claude --model haiku --prompt "Reply OK and stop." --json`.
  Save each envelope's `result` (redacted like the existing fixtures) to the two files, then
  remove the worktree with `orca worktree rm` (or the closest verb `orca worktree --help` shows)
  and confirm it is gone with `orca worktree list --json`.
- [ ] **Step 2: Write failing tests** in `tests/test_orca_client.py`:
  `test_worktree_create_argv` (the exact argv above, with and without `linear`),
  `test_agent_launch_argv`, `test_created_worktree_path_of_reads_the_fixture` (equals the path in
  the fixture), `test_launch_handle_of_reads_the_fixture`, and both accessors returning `""` on `{}`.
- [ ] **Step 3: Run** `python3 -m unittest tests.test_orca_client` — expect the new tests to FAIL.
- [ ] **Step 4: Implement** the two methods and two accessors; record the JSON paths in the
  fixtures README table.
- [ ] **Step 5: Run** the full suite — expect OK.
- [ ] **Step 6: Commit** `feat(orca): worktree create and agent launch for the quick lane`.

### Task 2: Start decisions (pure)

**Files:**
- Create: `plugins/rein/lib/quick.py`
- Test: `tests/test_quick.py`

**Interfaces:**
- Produces: `slugify(sentence: str, taken: set) -> str` — NFKD ASCII fold, lowercase,
  non-alphanumerics → `-`, strip `-`, cut to 30, `"task"` when empty; append `-2`, `-3`, … while
  `"quick-" + slug` is in `taken`.
- Produces: `looks_like_bug(sentence: str) -> bool` — true when the lowercased sentence contains
  any of: `bug`, `error`, `falla`, `fallo`, `no funciona`, `roto`, `rompe`, `crash`, `excepción`,
  `exception`, `500`, `no carga`, `no guarda`, `sale vacío`, `sale vacia`.
- Produces: `build_brief(sentence: str, linear: str = "", bug: bool = False) -> str` — Spanish,
  ≤ 150 words, containing the spec §4 lines in this order: task verbatim; iterate together; (bug
  only) "Primero escribe un test que lo reproduzca."; "Cuando diga *listo*, ejecuta
  `rein quick done` y muéstrame el resultado."; the no-push/main-confirmation line; the commit
  rule (with `linear`: "Incluye <linear> en el mensaje de commit."; without: "Commit
  convencional sin ID de issue: no inventes uno ni lo preguntes."); "No leas `.env*` ni
  credenciales."
- Produces: `start_refusal(here: bool, base_given: bool, dirty: bool, dirty_ok: bool) -> str` —
  `""` or the sentence explaining the refusal (`--here` with `--base`; dirty without `dirty_ok`).
- Produces: `new_record(repo: str, workspace: str, base: str, base_sha: str, agent: str,
  model: str, sentence: str, linear: str, now: float) -> dict` — the Global Constraints schema with
  `version: 1`, `audit_rounds: 0`.
- Produces: `RECORD = ".rein/quick.json"`.

- [ ] **Step 1: Write failing tests:** `test_slug_folds_and_cuts` (`"Arreglar la tarjeta de
  Cupones usados en KPIs!!"` → `"arreglar-la-tarjeta-de-cupones"`), `test_slug_falls_back_to_task`
  (`"¡¿…?! 🎉"` → `"task"`), `test_slug_suffix_on_clash` (taken `{"quick-x", "quick-x-2"}` →
  `"x-3"`), `test_bug_detection` (two positive, two negative sentences),
  `test_brief_bug_and_linear_variants` (the reproduce line only when `bug`; the Linear line vs. the
  no-id line), `test_brief_word_budget` (≤ 150 words with a 40-word sentence),
  `test_start_refusals` (four combinations), `test_new_record_schema` (exact key set).
- [ ] **Step 2: Run** `python3 -m unittest tests.test_quick` — expect FAIL (module missing).
- [ ] **Step 3: Implement** the functions above in `quick.py`.
- [ ] **Step 4: Run** the full suite — expect OK.
- [ ] **Step 5: Commit** `feat(quick): start decisions and the agent brief`.

### Task 3: Close decisions (pure)

**Files:**
- Modify: `plugins/rein/lib/quick.py`
- Modify: `plugins/rein/lib/quota.py` only if `first_unbraked` needs nothing new (it should not)
- Modify: `plugins/rein/lib/ship_decide.py` — extract `touches_risky(files: list, patterns: list)
  -> bool` from `audit_model` (behaviour unchanged; `audit_model` calls it)
- Test: `tests/test_quick.py`, `tests/test_ship_decide.py` (one test for `touches_risky`)

**Interfaces:**
- Consumes: `quota.first_unbraked`, `quota.brake_reason`, `quota.OPUS/CODEX/SONNET`.
- Produces: `related_tests(changed: list, exists: Callable[[str], bool]) -> list` — repo-relative
  paths: changed files that are tests (`*.spec.*`, `*.test.*`, `test_*.py`, `*_test.py`) and still
  exist, plus existing siblings of changed source files: same dir `<stem>.spec.<ext>` and
  `<stem>.test.<ext>`; for `.py`, `<dir>/test_<stem>.py` and `tests/<dir>/test_<stem>.py`. Sorted,
  unique, deleted files skipped.
- Produces: `gate_steps(commands: dict, related: list) -> list` of `(slot, command, configured)`:
  `lint` and `typecheck` when configured; then each related test through `testOne` with
  `{target}` replaced (`slot` = `"testOne:<path>"`), or one `test` step when there are more than
  10 related tests or no `testOne`. `[]` tests when there are none.
- Produces: `verdict(results: list) -> str` — `"green"` / `"red"` / `"setup"` from
  `CommandResult.outcome` (`ok` → green; `not-invocable`/`timeout` → setup wins over red;
  `failed` → red).
- Produces: `error_lines(text: str, cap: int = 40) -> list` per Global Constraints.
- Produces: `summary(results: list, outputs: dict, notes: list) -> str` — verdict line, one line per
  slot (`slot exit=<code> <elapsed>ms`), error lines for red slots only, then `notes` (e.g.
  "sin tests relacionados", the large-change warning, the fallback notice).
- Produces: `needs_audit(changed: list, cfg: ShipConfig) -> bool` via `touches_risky`.
- Produces: `reviewer(limits: dict) -> tuple | None` — `OPUS` unless Claude is braked; then
  `first_unbraked([CODEX, SONNET])` when that one is unbraked; else `None`.

- [ ] **Step 1: Write failing tests:** `test_related_tests_typescript` (`src/a/card.ts` with
  existing `src/a/card.spec.ts`), `test_related_tests_python` (`app/x.py` → `tests/app/test_x.py`),
  `test_related_tests_skip_deleted`, `test_gate_steps_cap_and_fallback` (11 related → one `test`
  step; no `testOne` → `test`; none → only lint/typecheck), `test_verdict_setup_beats_red`,
  `test_error_lines_context_and_cap` (60 error lines → 40), `test_error_lines_tolerates_garbage`
  (a 5 MB string with `�`), `test_summary_shows_errors_only_for_red`,
  `test_needs_audit_risky_only` (large but safe → False), `test_reviewer_choices` (Claude fine →
  OPUS; Claude braked, Codex fine → CODEX; Claude and Codex braked → `None`), and in `test_ship_decide.py` `test_touches_risky`.
- [ ] **Step 2: Run** — expect FAIL.
- [ ] **Step 3: Implement** in `quick.py`; extract `touches_risky` in `ship_decide.py`.
- [ ] **Step 4: Run** the full suite — expect OK.
- [ ] **Step 5: Commit** `feat(quick): close decisions: related tests, verdict, audit choice`.

Note on `reviewer`: Sonnet is a Claude model, so when Claude is braked Sonnet is braked too; the
rule reduces to "Opus, else Codex, else None". Keep `first_unbraked([CODEX, SONNET])` anyway so the
order reads like the spec.

### Task 4: `rein quick` start driver

**Files:**
- Create: `plugins/rein/lib/quick_driver.py`
- Test: `tests/test_quick_driver.py`

**Interfaces:**
- Consumes: Task 1 client methods and accessors; Task 2 functions; `quota.parse_rate_limits`,
  `quota.choose_implementer`, `quota.brake_reason`; `ShipConfig.from_flow_config`;
  `detect.resolve`.
- Produces: `start_quick(sentence: str, *, repo: str, base: str = "", here: bool = False,
  dirty_ok: bool = False, agent: str = "", model: str = "", linear: str = "", cwd: str = ".",
  client: OrcaClient, git: Callable[..., tuple] = run_git, now: Callable[[], float] = time.time,
  say: Callable[[str], None] = print) -> int`.
- Produces: `run_git(path: str, *args: str) -> tuple` returning `(code, stdout)`.

Behaviour (spec §4): resolve `repo` against `client.repo_list()` by `displayName` (empty `repo` →
the repo whose `path` contains `cwd`; unknown → 2 listing names); refusal from `start_refusal`
→ 2; implementer from `--agent/--model` or `choose_implementer(limits, cfg.claude_weekly_threshold)`
and, without an override, `brake_reason` non-empty → print every braked provider, return 1;
`--here`: dirty = `git status --porcelain` lines not under `.rein/`, `base_sha` = `HEAD`, launch with
`agent_launch(path:<repo path>, …)`; default: `taken` = `git branch --list 'quick-*'` names in the
repo, `base_sha` = `git merge-base <base> HEAD` run in the new worktree after creation, launch with
`worktree_create(...)`; refuse with 2 when `<workspace>/.rein/quick.json` exists; write the record
only after Orca succeeded; when `git check-ignore -q .rein/quick.json` fails, print
`add ".rein/" to <workspace>/.git/info/exclude` (do not write it); `OrcaError` → print its message
and `residual_resources_of` when present, return 126; print agent, model, workspace and return 0.

- [ ] **Step 1: Write failing tests** with a fake `OrcaClient` runner returning the Task 1 fixtures
  and a fake `git`: `test_default_creates_a_worktree_from_develop` (exact argv tail, record
  written with `base_sha`), `test_here_launches_in_the_main_checkout`,
  `test_here_refuses_dirty_but_ignores_rein_state`, `test_dirty_ok_allows_it`,
  `test_here_with_base_is_usage`, `test_unknown_repo_lists_names`,
  `test_every_provider_braked_launches_nothing` (returns 1, no orca launch call),
  `test_override_skips_quota`, `test_name_clash_gets_a_suffix`,
  `test_existing_record_refuses`, `test_orca_failure_is_126_and_writes_no_record`,
  `test_unignored_rein_prints_the_exclude_line`, `test_brief_is_one_argv_element` (sentence with
  quotes and `$(...)`).
- [ ] **Step 2: Run** — expect FAIL.
- [ ] **Step 3: Implement** `start_quick` and `run_git`.
- [ ] **Step 4: Run** the full suite — expect OK.
- [ ] **Step 5: Commit** `feat(quick): rein quick opens a cheap agent where the owner chose`.

### Task 5: `rein quick done` close driver

**Files:**
- Modify: `plugins/rein/lib/verify.py` — `CommandResult` gains an `output: str` attribute holding
  the full decoded capture (default `""`; not added to `to_dict`, so reports are unchanged);
  `run_one` sets it.
- Modify: `plugins/rein/lib/quick_driver.py`
- Test: `tests/test_quick_driver.py`, `tests/test_verify_commands.py` (one test for `output`)

**Interfaces:**
- Consumes: Task 3 functions; `ship.changed_files`, `ship.build_audit_spec`,
  `ship._episode_stamp`; `gate.latest_episode`, `gate.check_review`; `roles.profile("reviewer",
  PLUGIN)`; `verify.run_one`; client `run_create`, `task_create`, `worker_start`, `check`,
  `worker_show`, `worker_release`.
- Produces: `done_quick(*, cwd: str = ".", client: OrcaClient, run=verify.run_one,
  clock: Callable[[], float] = time.time, env: dict = os.environ,
  say: Callable[[str], None] = print) -> int`.

Behaviour (spec §5): git root of `cwd` → record (missing → 2 with the "start one with
`rein quick`" sentence); `changed_files(workspace, base_sha)` (`RuntimeError` → 126); none → 0
"nothing changed"; `gate_steps` over `detect.resolve(workspace)["commands"]` run with
`cfg.gate_timeout_seconds`; notes for no related tests, fallback, and more than
`cfg.large_change_files` files; print `summary`; red → 1, setup → 126; green and `needs_audit`:
no `ORCA_TERMINAL_HANDLE` in `env` → 126 with the "needs an Orca terminal" sentence;
`reviewer(limits)` `None` → 126; `audit_rounds` already 2 → 1 with "dos rondas de auditoría:
decide tú" and the last findings; else `run_create("rein quick <workspace name>")`, `task_create`
with `build_audit_spec({"why": sentence}, {workspace: changed}, profile, workspace,
"quick-<name>")`, `worker_start(task, worktree="path:<workspace>", agent=…, model=…)`, increment
and save `audit_rounds`, wait with `check(wait=True, timeout_ms=60000)` until a `worker_done` for
that dispatch or 20 minutes (then `worker_show` once; still unsettled → 126), release the worker,
read the verdict like `ShipDriver.read_verdict` (new episode stamp + `check_review`): APPROVED →
0; CHANGES_REQUESTED → print BLOCKING findings, 1; no new episode → 126.

- [ ] **Step 1: Write failing tests** against a temporary git repo (init, commit a base, branch,
  edit) with a fake `run` returning `CommandResult`s: `test_no_record_is_usage`,
  `test_finds_the_record_from_a_subdirectory`, `test_nothing_changed_is_green`,
  `test_green_light_gate` (lint, typecheck, one testOne), `test_red_shows_error_lines_only`,
  `test_setup_is_126`, `test_large_change_warns_without_audit`,
  `test_risky_without_terminal_is_126`, `test_risky_runs_an_audit_and_approves` (fake client emits
  `worker_done`; the fake reviewer writes an APPROVED episode via `gate.record_review`),
  `test_changes_requested_is_red_and_counts_a_round`, `test_third_round_leaves_it_to_the_owner`,
  `test_audit_without_a_new_episode_is_126`; in `test_verify_commands.py`
  `test_run_one_keeps_the_full_output`.
- [ ] **Step 2: Run** — expect FAIL.
- [ ] **Step 3: Implement** the `CommandResult.output` attribute and `done_quick`.
- [ ] **Step 4: Run** the full suite — expect OK.
- [ ] **Step 5: Commit** `feat(quick): rein quick done gates lightly and audits risky changes`.

### Task 6: CLI, skill and docs

**Files:**
- Modify: `plugins/rein/bin/rein` — `cmd_quick` and `"quick": cmd_quick` in `COMMANDS`
- Create: `plugins/rein/skills/quick/SKILL.md`
- Modify: `tests/test_v2_surface.py` (add `"quick"`), `tests/test_skill_budget.py` (`"quick": 200`
  in `BUDGETS`, `"quick"` in the skill set), `tests/test_skill_names.py` (`"quick"` in
  `UNTOUCHED_SKILLS`), `tests/test_cli_presentation.py` or a new `tests/test_quick_cli.py`
- Modify: `README.md` (the command block and the skills table), spec status line → implemented

**Interfaces:**
- Consumes: `quick_driver.start_quick`, `quick_driver.done_quick`.
- Produces: `rein quick "<sentence>" [--repo <name>] [--base <branch>] [--here [--dirty-ok]]
  [--agent <id> --model <m>] [--linear <id>]` and `rein quick done`. Usage errors (no sentence,
  `--agent` without `--model` or the reverse) → print the usage line, return 2.

Skill (≤ 200 words, frontmatter like `answer`): description triggers "tarea rápida", "arréglalo
rápido", "cambio rápido", "quick task", `/quick`; steps: map the owner's words to flags (spec §4
list); a sentence naming two repos → stop and suggest `/rein:rein-plan` then `/ship`; run `rein
quick …` once; report the tab and that the owner now talks to that agent there; exit 1 or 126 →
show the output, never retry; never edit code itself.

- [ ] **Step 1: Write failing tests:** `test_quick_usage_errors` (three bad argv → 2),
  `test_quick_routes_start_and_done` (patch `quick_driver` functions, assert the parsed kwargs),
  plus the surface/skill-set updates above.
- [ ] **Step 2: Run** — expect FAIL.
- [ ] **Step 3: Implement** `cmd_quick`, the skill and the README lines.
- [ ] **Step 4: Run** the full suite — expect OK (this includes `test_readme_is_current`).
- [ ] **Step 5: Commit** `feat(quick): rein quick CLI and the /quick skill`.

### Task 7: Live check (owner-approved)

**Files:** none in this repo unless a bug is found (then a failing test first, as usual).

- [ ] **Step 1:** Reinstall: `uv tool install --force --reinstall .` and `rein quick` prints usage.
- [ ] **Step 2:** With the owner's go-ahead, from a Claude session say "tarea rápida en admin:
  <a small real change the owner picks>". Confirm: the model matches `choose_implementer` on live
  quota, a `quick-<slug>` worktree from `develop` opens with the brief, the record exists and is
  ignored by git.
- [ ] **Step 3:** The owner iterates, says "listo"; `rein quick done` prints the light gate summary
  and exits 0; the agent commits with the commit rule; nothing is pushed.
- [ ] **Step 4:** Record the result (model, tokens or quota delta, wall time, any finding) in the
  SDD ledger and commit fixes, if any, through the same TDD cycle.
