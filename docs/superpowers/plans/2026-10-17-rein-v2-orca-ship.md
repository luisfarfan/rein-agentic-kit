# Rein v2 (`rein ship` on Orca) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Rein's Claude-only `Workflow` loop with `rein ship`, a deterministic Python
coordinator that runs an openspec plan through Orca orchestration with Codex/Sonnet workers,
gating every task with `rein gate`.

**Architecture:** A pure decision core (`ship_decide.decide(state, event, cfg)`) returns
actions. A thin driver (`ship.run_ship`) turns those actions into `orca … --json` calls
through `orca_client` and turns Orca messages back into events. The kept modules (`detect`,
`verify`, `gate`, `plan`, `plan_check`, `roles`, `token_report`, `serve`) are reused
unchanged except where a task says otherwise.

**Tech Stack:** Python ≥ 3.9, stdlib only, `unittest`, Orca CLI 1.4.227 (`orca … --json`).

**Spec:** `docs/superpowers/specs/2026-10-17-rein-v2-orca-design.md`

## Global Constraints

- Zero runtime dependencies; stdlib only (`pyproject.toml` `dependencies = []`).
- Python 3.9 compatible: every module starts with `from __future__ import annotations`
  (`tests/test_python39_compat.py` enforces this).
- Tests use `unittest` with the `sys.path.insert(… "plugins", "rein", "lib")` header used in
  `tests/test_gate_decision.py`. Full suite: `python3 -m unittest discover -s tests -q`.
- CLI exit codes: 0 ok, 1 failure, 2 usage error, 126 environment could not run the check.
- Only `orca_client.py` may shell out to `orca`. The executable is `$ORCA_CLI_COMMAND` when
  set, otherwise `orca` on PATH.
- Defaults (spec §7): `claudeWeeklyThreshold` 60, `maxConcurrentWorkers` 3, `maxRetries` 2,
  `maxAuditRounds` 2, `taskTimeoutMinutes` 45, `largeChangeFiles` 25,
  `riskyPaths` `["**/auth/**", "**/payments/**", "**/migrations/**"]`.
- Implementers: Codex is `("codex", "gpt-6.1-sol")`, Sonnet is `("claude", "sonnet")`,
  Opus is `("claude", "opus")`.
- Brakes (spec §6): Claude session ≥ 90%, or the chosen provider's weekly usage ≥ 90%.
- Never push, merge, open a PR, deploy, or run a worker on a primary checkout.
- Code, comments and docs are in English, matching the repo.

**Spec deviation for owner approval:** spec §4.3 removes the flat `tasks.md` plan source.
`parse_tasks_md` is shared by both sources, so removing the source only saves the location
branch in `plan_path`/`read_plan` (~10 lines). Rein's own repo plans with flat `tasks.md`
(`flow.config.json` `"source": "tasks-md"`), so this plan **keeps** the flat source. PROXIMA
uses openspec only. Reverse this by deleting that branch in Task 1 if you prefer.

## Review Focus

1. Orca restarts mid-run, so terminal handles go stale. The driver must address workers only
   by dispatch id and, on resume, must not relaunch a task whose dispatch is still live
   (Task 8 test `test_resume_does_not_relaunch_live_dispatch`).
2. `orca account list` returns a provider with `status != "ok"` or no `rateLimits` entry. Model
   choice must stay deterministic: unknown Claude usage routes to Codex
   (Task 4 test `test_unknown_claude_usage_routes_to_codex`).
3. The plan changes between runs (a task added, or a checkbox ticked by hand). Resume must
   create Orca tasks only for new ids and skip ticked ones
   (Task 6 test `test_reconcile_adds_new_and_skips_checked`).
4. A `worker_done` arrives for a superseded dispatch, such as the attempt before a retry. It
   must be ignored, not close the task (Task 7 test `test_stale_dispatch_event_is_ignored`).
5. A `Repo:` value names a repo that is not registered in Orca. Preflight must fail and list
   the registered names (Task 8 test `test_preflight_unknown_repo_lists_registered`).

---

### Task 1: Tag v1 and remove what Orca replaces

**Files:**
- Delete: `plugins/rein/workflows/loop.js`, `plugins/rein/lib/{dashboard,events,product_state,backlog,workspace,setup,linear_client,linear_issue,linear_select,linear_intake,version_staleness,ansi}.py`, `plugins/rein/_entry.py`, `plugins/rein/bin/token-report`, `plugins/rein/skills/{rein-apply,rein-step,rein-steps,rein-linear}`, `plugins/rein/commands/{rein-setup,rein-ping}.md`, and every test whose only subject is a deleted module (find them with `grep -l` on the module name).
- Modify: `plugins/rein/bin/rein`: drop the imports at lines 82–101 for deleted modules, the
  `cmd_workspace`, `cmd_linear`, `cmd_intake`, `cmd_land`, `cmd_setup`, `cmd_event`,
  `cmd_state`, `cmd_dashboard` and `cmd_backlog` handlers, their `COMMANDS` entries and
  `USAGE` lines, and the `cmd_doctor` sections that call `_staleness`, `_setup` or
  `_workspace`. Replace `_ansi.paint(...)` calls with their plain text.
- Modify: `pyproject.toml`: remove the `_entry` console-script entry, keeping the `rein` script
  pointing at `bin/rein`. Keep `tests/test_packaging.py` green by updating its expectation.

**Interfaces:**
- Produces: a CLI whose `COMMANDS` keys are exactly `detect, verify, gate, role, tasks,
  plan-check, context, close, next, review, token-report, ledger, baseline, serve-probe,
  doctor`. Later tasks add `ship` and `migrate-config`.

- [ ] **Step 1: Tag the current main for rollback**

Run: `git tag rein--v0.15.0 main && git tag --list 'rein--v0.15*'`
Expected: `rein--v0.15.0`

- [ ] **Step 2: Write the failing test** in `tests/test_v2_surface.py`

```python
def test_cli_commands_are_the_v2_surface(self):
    self.assertEqual(sorted(rein_cli.COMMANDS), sorted([
        "detect", "verify", "gate", "role", "tasks", "plan-check", "context", "close",
        "next", "review", "token-report", "ledger", "baseline", "serve-probe", "doctor"]))

def test_removed_modules_are_gone(self):
    for name in ("dashboard", "events", "product_state", "backlog", "workspace", "setup",
                 "linear_client", "linear_issue", "linear_select", "linear_intake",
                 "version_staleness", "ansi"):
        self.assertFalse(os.path.exists(os.path.join(LIB, name + ".py")), name)
    self.assertFalse(os.path.exists(os.path.join(PLUGIN, "workflows", "loop.js")))
```

Load `bin/rein` as `rein_cli` with `importlib.machinery.SourceFileLoader`, as
`tests/test_cli_presentation.py` does.

- [ ] **Step 3: Run it and confirm it fails**

Run: `python3 -m unittest tests.test_v2_surface -v`
Expected: FAIL (extra commands, files present)

- [ ] **Step 4: Delete the files and edit `bin/rein` and `pyproject.toml` as listed above**

- [ ] **Step 5: Run the full suite**

Run: `python3 -m unittest discover -s tests -q`
Expected: `OK`. Any failure is a leftover reference to a deleted module; remove the reference,
never restore the module.

- [ ] **Step 6: Commit (removals only, per spec §10)**

```bash
git add -A && git commit -m "refactor!: remove the Workflow loop and modules Orca replaces"
```

### Task 2: `Repo:` task field

**Files:**
- Modify: `plugins/rein/lib/plan.py:69-75` (the `FIELDS` table) and `parse_tasks_md`'s task dict
- Test: `tests/test_plan.py`

**Interfaces:**
- Produces: every parsed task dict has `"repo": str` (`""` when absent; the field name is
  matched case-insensitively as `repo`).

- [ ] **Step 1: Write the failing tests**

```python
def test_repo_field_is_parsed(self):
    t = plan.parse_tasks_md("- [ ] T001 X\n  - Repo: proxima-api\n  - Verification: `x`\n")[0]
    self.assertEqual(t["repo"], "proxima-api")

def test_repo_defaults_to_empty(self):
    t = plan.parse_tasks_md("- [ ] T001 X\n  - Verification: `x`\n")[0]
    self.assertEqual(t["repo"], "")
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `python3 -m unittest tests.test_plan -v`
Expected: FAIL with `KeyError: 'repo'`

- [ ] **Step 3: Add `"repo": ("repo",)` to `FIELDS` and `"repo": ""` to the new-task dict**

- [ ] **Step 4: Run the suite**

Run: `python3 -m unittest discover -s tests -q`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(plan): a task may name the repo it belongs to"
```

### Task 3: `rein gate --unit-only`

Per-task gates in frontend repos must not demand a browser render; render evidence belongs to
`Type: verification` tasks.

**Files:**
- Modify: `plugins/rein/bin/rein` `cmd_gate` (around line 287, where `policy` is built)
- Test: `tests/test_gate_cli.py`

**Interfaces:**
- Produces: `rein gate <root> --unit-only [--json]`. With the flag, `policy["mode"]` is forced
  to `"unit"` before `decide_gate`, so a rendered-mode repo with green commands exits 0.

- [ ] **Step 1: Write the failing test.** Build a temporary repo whose `flow.config.json` has
  `"verify": {"mode": "rendered"}` and `"commands": {"test": "true"}`. Assert that `rein gate
  <tmp>` exits 126 and `rein gate <tmp> --unit-only` exits 0. Reuse the temp-repo helper
  already in `tests/test_gate_cli.py`.
- [ ] **Step 2: Run it and confirm it fails**

Run: `python3 -m unittest tests.test_gate_cli -v`
Expected: FAIL (the second call exits 126)

- [ ] **Step 3: In `cmd_gate`, after `policy = dict(...)`, add `if "--unit-only" in argv: policy["mode"] = "unit"`, and add the flag to `USAGE`**
- [ ] **Step 4: Run the suite**

Run: `python3 -m unittest discover -s tests -q`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(gate): --unit-only for per-task gates in rendered repos"
```

### Task 4: `quota.py`

**Files:**
- Create: `plugins/rein/lib/quota.py`
- Create: `tests/fixtures/orca/account_list.json`: copy the real `orca account list --json`
  output and replace `email` and `providerAccountId` with `"redacted"`. Include a provider with
  `"status": "error"`.
- Test: `tests/test_quota.py`

**Interfaces:**
- Produces:
  - `CODEX = ("codex", "gpt-6.1-sol")`, `SONNET = ("claude", "sonnet")`, `OPUS = ("claude", "opus")`
  - `parse_rate_limits(account_list: dict) -> dict[str, dict]`: maps `{"claude": {"session":
    float|None, "weekly": float|None}, "codex": {...}}` from
    `result.rateLimits.<p>.{session,weekly}.usedPercent`. A provider whose `status != "ok"`
    or whose block is missing gets `None` values.
  - `choose_implementer(limits: dict, threshold: float, override: tuple[str, str] | None = None) -> tuple[str, str]`
  - `brake_reason(limits: dict, agent: str, max_pct: float = 90.0) -> str`: `""` when fine,
    otherwise a sentence naming the provider and the window.
  - `usage_delta(before: dict, after: dict) -> dict[str, dict]`: per provider and window,
    `after - before`, or `None` when either side is `None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_parses_real_fixture(self):
    lim = quota.parse_rate_limits(load("account_list.json"))
    self.assertEqual(lim["claude"]["weekly"], 77)
    self.assertEqual(lim["codex"]["weekly"], 15)

def test_claude_over_threshold_routes_to_codex(self):
    self.assertEqual(quota.choose_implementer({"claude": {"weekly": 60.0}}, 60), quota.CODEX)

def test_claude_under_threshold_routes_to_sonnet(self):
    self.assertEqual(quota.choose_implementer({"claude": {"weekly": 59.9}}, 60), quota.SONNET)

def test_unknown_claude_usage_routes_to_codex(self):          # Review Focus 2
    self.assertEqual(quota.choose_implementer({"claude": {"weekly": None}}, 60), quota.CODEX)
    self.assertEqual(quota.choose_implementer({}, 60), quota.CODEX)

def test_override_wins(self):
    self.assertEqual(quota.choose_implementer({"claude": {"weekly": 99}}, 60, quota.OPUS), quota.OPUS)

def test_brake_on_session_or_weekly_at_90(self):
    self.assertIn("claude", quota.brake_reason({"claude": {"session": 90, "weekly": 10}}, "claude"))
    self.assertIn("codex", quota.brake_reason({"codex": {"session": 1, "weekly": 91}}, "codex"))
    self.assertEqual(quota.brake_reason({"codex": {"session": 1, "weekly": 10}}, "codex"), "")

def test_delta(self):
    d = quota.usage_delta({"claude": {"session": 10, "weekly": 50}},
                          {"claude": {"session": 25, "weekly": 53}})
    self.assertEqual(d["claude"], {"session": 15, "weekly": 3})
```

- [ ] **Step 2: Run them and confirm they fail** (`python3 -m unittest tests.test_quota -v`: ImportError)
- [ ] **Step 3: Implement `quota.py`.** Unknown usage never brakes. Brake only on a known value ≥ `max_pct`.
- [ ] **Step 4: Run the suite** (`python3 -m unittest discover -s tests -q`: `OK`)
- [ ] **Step 5: Commit** (`git add plugins/rein/lib/quota.py tests/test_quota.py tests/fixtures/orca && git commit -m "feat(quota): pick the implementer from live quota"`)

### Task 5: Record Orca write-command fixtures — **STOP: requires the owner's explicit approval**

This creates a throwaway Run, one task, and one short-lived worker in the current Orca
worktree. It does not create a new worktree.

**Files:**
- Create: `tests/fixtures/orca/{status,repo_list,run_create,task_create,worker_start,check_worker_done,worker_show,worker_list,worker_release,gate_create,gate_list}.json`
- Create: `tests/fixtures/orca/README.md`: the exact commands used and the date

- [ ] **Step 1: Ask the owner for approval in chat and wait for an explicit yes**
- [ ] **Step 2: From an Orca terminal in this repo's worktree, run the sequence and save each `--json` output** (substitute `ORCA` with the resolved executable, as the orca-cli skill describes):

```text
ORCA status --json
ORCA repo list --json
ORCA orchestration run-create --objective "rein fixture capture" --json
ORCA orchestration task-create --task-title "fixture" --spec "Reply worker_done with outcome succeeded immediately; change nothing." --json
ORCA orchestration worker-start --task <task_id> --worktree current --agent claude --model haiku --json
ORCA orchestration check --wait --types "worker_done,escalation,question" --timeout-ms 300000 --json
ORCA orchestration worker-show --dispatch <dispatch_id> --json
ORCA orchestration worker-list --json
ORCA orchestration gate-create --task <task_id> --question "fixture gate?" --options '["yes","no"]' --json
ORCA orchestration gate-list --json
ORCA orchestration worker-release --dispatch <dispatch_id> --json
ORCA orchestration check --ack <delivery_id> --json
```

- [ ] **Step 3: Redact every email, token, account id and absolute home path** (replace with `"redacted"`/`/HOME`), then confirm with `grep -rEi "@|token|/Users/" tests/fixtures/orca` that nothing is left
- [ ] **Step 4: Record in the README which JSON paths hold** run id, task id, dispatch id, delivery id, message type, outcome, worktree id and path, and gate id. Task 6 relies on exactly these paths.
- [ ] **Step 5: Commit** (`git add tests/fixtures/orca && git commit -m "test(fixtures): recorded Orca orchestration responses"`)

### Task 6: `orca_client.py` and `ship_state.py`

**Files:**
- Create: `plugins/rein/lib/orca_client.py`, `plugins/rein/lib/ship_state.py`
- Test: `tests/test_orca_client.py`, `tests/test_ship_state.py`

**Interfaces:**
- Consumes: the fixture JSON paths documented in Task 5's README.
- Produces, `orca_client`:
  - `class OrcaError(Exception)` with `.code: str`, `.message: str`, `.argv: list[str]`
  - `class OrcaClient(executable: str | None = None, runner: Callable[[list[str]], tuple[int, str]] | None = None)`
    The default runner is `subprocess.run(..., capture_output=True, text=True)`. Every method
    appends `--json`, parses stdout, raises `OrcaError` on a non-zero exit or `"ok": false`,
    and returns `result`.
  - Methods (argv exactly as `orca agent-context` lists them): `status()`,
    `account_list()`, `repo_list()`, `run_create(objective)`, `run_use(run_id)`,
    `task_create(spec, title, deps: list[str])`,
    `worker_start(task_id, *, worktree, agent, model, repo="", name="", setup="", comment="")`,
    `worker_show(dispatch_id)`, `worker_list(run_id="")`, `worker_release(dispatch_id)`,
    `check(*, wait=True, types="worker_done,escalation,question", timeout_ms=900000, ack="")`,
    `reply(message_id, body)`, `gate_create(task_id, question, options: list[str])`,
    `gate_list()`, `worktree_set(selector, *, status="", comment="")`
  - `accessor` functions holding every JSON path from Task 5 (`run_id_of`,
    `task_id_of`, `dispatch_id_of`, `worktree_of`, `messages_of`, `delivery_id_of`,
    `gate_id_of`), so the paths live in one place.
- Produces, `ship_state`:
  - `@dataclass TaskState: id: str, repo: str, deps: list[str], human_review: bool, verification: str, type: str, status: str = "pending", attempts: int = 0, orca_task_id: str = "", dispatch_id: str = "", agent: str = "", model: str = "", started_at: float = 0.0, last_failure: str = ""`
    `status` is one of `pending, running, gating, gated, done, failed`.
  - `@dataclass ShipState: change: str, plan_path: str, run_id: str = "", tasks: dict[str, TaskState], worktrees: dict[str, str], worktree_paths: dict[str, str], gates: dict[str, str], phase: str = "running", audit_rounds: int = 0, audit_dispatch: str = "", implementer_agents: list[str]`
    `phase` is one of `running, auditing, fixing, braked, done`.
  - `from_plan(plan: dict, default_repo: str) -> ShipState`. Checked tasks start `done`.
  - `reconcile(state: ShipState, plan: dict, default_repo: str) -> ShipState`
  - `load(path: str) -> ShipState | None` and `save(state: ShipState, path: str) -> None`
    Writes atomically (temp file plus `os.replace`) at `<plan repo>/.rein/ship-state.json`.

- [ ] **Step 1: Write the failing tests**
  - `test_methods_build_documented_argv`: a fake runner records argv. Assert
    `worker_start("t1", worktree="new-top-level", agent="codex", model="gpt-6.1-sol", repo="name:proxima-api", name="c", setup="run")`
    produces `["orchestration", "worker-start", "--task", "t1", "--worktree", "new-top-level", "--agent", "codex", "--model", "gpt-6.1-sol", "--repo", "name:proxima-api", "--name", "c", "--setup", "run", "--json"]`
    after the executable.
  - `test_error_envelope_raises`: `{"ok": false, "error": {"code": "x", "message": "m"}}` raises
    `OrcaError` with `.code == "x"`.
  - `test_accessors_read_fixtures`: each accessor returns a non-empty string on its Task 5 fixture.
  - `test_from_plan_marks_checked_done` and `test_default_repo_applies_when_blank`.
  - `test_reconcile_adds_new_and_skips_checked` (Review Focus 3): state has T001 running. The
    plan now has T001, a ticked T002, and a new T003. Result: T001 unchanged, T002 `done`, T003
    `pending`.
  - `test_save_load_round_trip`.
- [ ] **Step 2: Run them and confirm they fail** (`python3 -m unittest tests.test_orca_client tests.test_ship_state -v`)
- [ ] **Step 3: Implement both modules**
- [ ] **Step 4: Run the suite** (`OK`)
- [ ] **Step 5: Commit** (`git commit -m "feat(ship): Orca client and resumable ship state"`)

### Task 7: `ship_decide.py`, the pure decision core

**Files:**
- Create: `plugins/rein/lib/ship_decide.py`
- Test: `tests/test_ship_decide.py`

**Interfaces:**
- Consumes: `ShipState`/`TaskState` (Task 6), `quota.choose_implementer`, `brake_reason`, and `OPUS` (Task 4).
- Produces:
  - `@dataclass ShipConfig` with the Global Constraints defaults, plus
    `from_flow_config(cfg: dict) -> ShipConfig`, which reads the `ship` block and leaves
    absent keys at their defaults.
  - Events (dataclasses): `Tick(now: float, limits: dict)`,
    `WorkerDone(dispatch_id: str, outcome: str)`,
    `GateVerdict(task_id: str, exit_code: int, reason: str)`,
    `Question(dispatch_id: str, message_id: str, text: str)`,
    `GateResolved(gate_id: str, resolution: str)`,
    `AuditVerdict(verdict: str, blocking: int, changed_files: list[str])`
  - Actions (dataclasses): `Dispatch(task_id, repo, worktree, agent, model)` where `worktree`
    is `"new-top-level"` or `id:<worktree id>`, `RunGate(task_id, unit_only: bool)`,
    `CloseTask(task_id)`, `Release(dispatch_id)`, `OpenGate(task_id: str | None, question, options: list[str])`,
    `Relay(message_id, body)`, `StartAudit(agent, model)`, `Brake(reason)`, `Finish()`
  - `decide(state: ShipState, event, cfg: ShipConfig) -> tuple[ShipState, list]`
  - `audit_model(changed_files: list[str], implementer_agents: list[str], cfg: ShipConfig) -> tuple[str, str]`

Rules, each pinned by the test named in brackets:
- On `Tick`, dispatch ready tasks: deps all `done`, not human review, at most one `running`
  task per repo, and no more than `maxConcurrentWorkers` running in total. A repo's first
  dispatch uses `"new-top-level"`; later ones use `id:<state.worktrees[repo]>`
  [`test_dispatch_respects_deps_repo_serial_and_cap`].
- On `Tick`, a ready human-review task gets `OpenGate(..., ["approve", "reject"])` and status
  `gated` [`test_human_review_opens_gate`].
- On `Tick`, a non-empty `brake_reason` for the chosen implementer sets `phase="braked"` and
  emits a single `Brake` plus `OpenGate(None, …, ["switch-to-codex", "pause"])`
  [`test_quota_brake`].
- On `Tick`, a running task older than `taskTimeoutMinutes` gets an `OpenGate` and status
  `gated` [`test_task_timeout_opens_gate`].
- `WorkerDone` for a dispatch id that no task currently holds produces no actions and leaves
  state unchanged [`test_stale_dispatch_event_is_ignored`] (Review Focus 4).
- `WorkerDone` with `succeeded` emits `RunGate(task, unit_only = task.type != "verification")`
  and status `gating`; with `failed` it follows the red path [`test_failed_outcome_is_red`].
- `GateVerdict` 0 emits `CloseTask` and `Release`, status `done`. Verdict 1 with
  `attempts < maxRetries` emits `Release` and a fresh `Dispatch` with `attempts + 1` and
  `last_failure = reason[:2000]`; once retries are exhausted the task is `failed` and gets an
  `OpenGate`. Verdict 126 emits `OpenGate` immediately
  [`test_gate_green_red_retry_and_setup`].
- `Question` emits `OpenGate(task, text, [])` and records the gate→task mapping.
  `GateResolved` for that gate emits `Relay(message_id, resolution)`
  [`test_question_round_trip`].
- When every task is `done`: `phase="auditing"` and
  `StartAudit(*audit_model(...))`. `audit_model` returns `OPUS` if any changed file matches
  `riskyPaths` (`fnmatch`) or there are more than `largeChangeFiles` changed files.
  Otherwise it returns the provider not in `implementer_agents` (`SONNET` if only Codex
  implemented, `CODEX` if only Claude did, `SONNET` if both did) [`test_audit_model_selection`].
- `AuditVerdict` APPROVED emits `Finish`. CHANGES_REQUESTED with `blocking > 0` and
  `audit_rounds < maxAuditRounds` reopens one fix task per repo with the findings and sets
  `phase="fixing"`. When rounds run out it emits `OpenGate` and `Finish`
  [`test_audit_rounds_cap`].

- [ ] **Step 1: Write the ten tests named above**, each building a `ShipState` by hand and asserting the exact action list
- [ ] **Step 2: Run them and confirm they fail** (`python3 -m unittest tests.test_ship_decide -v`)
- [ ] **Step 3: Implement `ship_decide.py`.** No I/O, no clock reads (time comes from `Tick.now`), no randomness.
- [ ] **Step 4: Run the suite** (`OK`)
- [ ] **Step 5: Commit** (`git commit -m "feat(ship): pure decision core"`)

### Task 8: `rein ship`, the driver, preflight and dry run

**Files:**
- Create: `plugins/rein/lib/ship.py`
- Modify: `plugins/rein/bin/rein`: add `cmd_ship` and a `COMMANDS["ship"]` entry
- Test: `tests/test_ship.py`

**Interfaces:**
- Consumes: Tasks 2–7.
- Produces:
  - `preflight(root: str, change: str, client: OrcaClient, env: dict) -> tuple[dict, list[str]]`
    Returns `(context, errors)`. The checks are:
    - `ORCA_TERMINAL_HANDLE` is in `env`.
    - `orchestration.contract.v1` is in `status().runtime.capabilities`.
    - `plan_check` has no BLOCKING finding.
    - Every unchecked task has a runnable verification or human review.
    - Every task repo is in `repo_list()`.
    For an unknown repo the error lists the registered display names.
  - `run_ship(root: str, change: str, client: OrcaClient, gate_runner: Callable[[str, bool], tuple[int, str]], *, env: dict, clock: Callable[[], float], dry_run: bool = False, override: tuple[str, str] | None = None, state_path: str = "") -> int`
  - `build_task_spec(task: TaskState, plan: dict, implementer_profile: str) -> str`: the
    implementer profile (`roles.profile("implementer", PLUGIN)["text"]`), the task title,
    acceptance criteria and verification command, the plan's Why and Scope-Out, `last_failure`
    when retrying, and these boundary lines verbatim: `Edit only the <repo> repository.`,
    `Never push, merge, open a pull request or deploy.`,
    `Never read or print .env* files or credentials.`
  - `build_audit_spec(plan: dict, changed: dict[str, list[str]], reviewer_profile: str) -> str`:
    the reviewer profile plus `Run the rein-audit skill on <worktree path>` for each worktree,
    and `Record the verdict with rein review record`.
  - `changed_files(worktree_path: str, base_ref: str) -> list[str]`: runs
    `git -C <path> diff --name-only <base_ref>...HEAD`. `base_ref` is the repo's
    `worktreeBaseRef` from `repo_list()`.
  - When the audit worker sends `worker_done`, the driver reads `gate.check_review(root, change)`
    and `gate.latest_episode(root, change)` and emits
    `AuditVerdict(verdict, number of BLOCKING findings, changed files)`.
  - Worker message bodies are passed through as data only; they never select an action.
  - CLI: `rein ship <change> [--root <plan repo>] [--agent <id> --model <m>] [--dry-run]`.
    Exit 0 when finished approved, 1 when finished with failed tasks or a rejected audit,
    2 on usage, 126 on preflight errors.
- Driver loop: load or create state, then `run_create` or `run_use`, then `task_create` for
  each task (`--deps` mapped to Orca ids), saving state after every mutating call. Then loop:
  - Emit a `Tick` with `parse_rate_limits(account_list())`.
  - Execute the actions.
  - Call `check(wait=True)`, map each message to an event, and ack the delivery only after
    every message in it has been decided.
  - After three empty waits in a row, call `worker_list` and follow each row's
    `projection.nextAction`.
  - `RunGate` calls `gate_runner(worktree_path, unit_only)`, which runs
    `rein gate <path> --json [--unit-only]`.
  - At `Finish`, set each worktree to `in-review` with a one-line comment, print the
    `usage_delta` per provider and a `token_report.summarize` over the Claude transcripts of
    the run's worktrees, and append a ledger row (`append_to_ledger`).
  - With `--dry-run`, use a recording `OrcaClient` runner, print every argv, and execute
    nothing.

- [ ] **Step 1: Write the failing tests.** Use an in-memory `FakeOrca` runner that answers from the Task 5 fixtures and scripted message queues.
  - `test_preflight_outside_orca_terminal_fails`
  - `test_preflight_unknown_repo_lists_registered` (Review Focus 5)
  - `test_end_to_end_two_repos_one_retry`: plan with T001 (proxima-api), T002 (proxima-admin,
    depends on T001), and T003 (proxima-api). T001's first gate returns 1 and its second
    returns 0. Assert the argv sequence: `run-create`; three `task-create`; `worker-start`
    T001 with `new-top-level`; a retry `worker-start` T001 with `id:`; T002 and T003 dispatched
    only after T001 is done, T003 reusing the api worktree; `StartAudit`; `worktree set
    --workspace-status in-review` twice. Exit code 0.
  - `test_resume_does_not_relaunch_live_dispatch` (Review Focus 1): a saved state has T001
    `running` with dispatch `d1`, and `worker_list` reports it live. Assert no new
    `worker-start` for T001.
  - `test_dry_run_executes_nothing`: the runner records argv and no state file is written.
  - `test_task_spec_carries_boundaries`: the three boundary lines and the task's verification
    command appear verbatim, and `last_failure` appears only when `attempts > 0`.
- [ ] **Step 2: Run them and confirm they fail** (`python3 -m unittest tests.test_ship -v`)
- [ ] **Step 3: Implement `ship.py` and `cmd_ship`.** Update the `COMMANDS` assertion in `tests/test_v2_surface.py` to include `ship`.
- [ ] **Step 4: Run the suite** (`OK`)
- [ ] **Step 5: Commit** (`git commit -m "feat(ship): rein ship drives a plan through Orca"`)

### Task 9: `ship` config block and `rein migrate-config`

**Files:**
- Modify: `plugins/rein/lib/detect.py` `resolve()`: expose `resolved["ship"]` as the raw `ship` block (`{}` when absent)
- Create: `plugins/rein/lib/migrate_config.py`
- Modify: `plugins/rein/bin/rein`: add `cmd_migrate_config` and `COMMANDS["migrate-config"]`
- Modify: `plugins/rein/flow.config.example.json`: drop `models`, `limits` and `worktree`; add the `ship` block from spec §7
- Test: `tests/test_migrate_config.py`

**Interfaces:**
- Produces: `migrate(cfg: dict) -> dict` (pure: drops `models`, `limits` and `worktree`, and adds
  the default `ship` block when it is absent, preserving any existing `ship` values).
  `rein migrate-config [root] [--write]` prints a unified diff and writes only with `--write`.

- [ ] **Step 1: Write the failing tests**: `test_migrate_drops_and_adds`, `test_migrate_preserves_existing_ship`, `test_cli_without_write_changes_nothing`
- [ ] **Step 2: Run them and confirm they fail**
- [ ] **Step 3: Implement**, and have `ShipConfig.from_flow_config` read `resolved["ship"]`
- [ ] **Step 4: Run the suite** (`OK`)
- [ ] **Step 5: Commit** (`git commit -m "feat(config): ship block and migrate-config"`)

### Task 10: Skills

**Files:**
- Create: `plugins/rein/skills/ship/SKILL.md` (≤ 200 words). It explains when to use the skill, then
  runs `rein ship <change>` in the current Orca terminal, tells the user that worker questions
  arrive as Orca decision gates, and never edits code itself.
- Modify: `plugins/rein/skills/rein-plan/SKILL.md`, at ≤ 700 words. The skill must:
  - Decide `Repo:` per task.
  - Write single-repo plans to `<repo>/openspec/changes/<c>/` and multi-repo plans to
    `proxima-engineering/openspec/changes/<c>/`.
  - Require a runnable `Verification` per task, or `Human review: true`.
  - End the session after writing, stating that implementation runs with `/ship`.
- Modify: `plugins/rein/skills/rein-discover/SKILL.md`: investigate with `codegraph query` and
  offset reads, never whole-file reads by default; for a large scope, delegate reading to
  read-only workers; end with an understanding note, never a plan.
- Modify: every remaining `SKILL.md` (`rein-plan`, `rein-discover`, `rein-audit`, `rein-role`):
  remove the `$R` path-resolution block and the `rein event` call, and call `rein` directly.
- Test: `tests/test_skill_names.py` (update the expected set) and a new `tests/test_skill_budget.py`

- [ ] **Step 1: Write the failing tests**
  - `test_skill_set` == `{"rein-plan", "rein-discover", "rein-audit", "rein-role", "ship"}`
  - `test_word_budgets`: rein-plan ≤ 700 words, ship ≤ 200
  - `test_no_event_or_path_boilerplate`: no SKILL.md contains `rein event` or `plugins/cache`
  - `test_roles_still_parse`: `roles.profile(r, PLUGIN)["ok"]` for implementer, reviewer and planner
- [ ] **Step 2: Run them and confirm they fail**
- [ ] **Step 3: Edit the skills**
- [ ] **Step 4: Run the suite** (`OK`)
- [ ] **Step 5: Commit** (`git commit -m "feat(skills): v2 skill set — plan, discover, audit, role, ship"`)

### Task 11: Version, README, decisions

**Files:**
- Modify: the five version strings `tests/test_version_lock.py` checks, setting them to `2.0.0`
- Modify: `README.md`: replace the flow and quickstart sections with the lifecycle from spec §3 and the `rein ship` usage. Keep the measured-problem section.
- Modify: `docs/decisions.md`: add a "v2: Orca is the runtime" entry that cites the spec
- Modify: `tests/test_readme_is_current.py` if it pins removed command names

- [ ] **Step 1: Update the strings and docs**
- [ ] **Step 2: Run the suite** (`OK`)
- [ ] **Step 3: Commit** (`git commit -m "chore(release): 2.0.0"`)

### Task 12: Baseline and pilot — **STOP: requires the owner's explicit approval**

This installs software (`pipx`) and creates real worktrees and workers in PROXIMA.

- [ ] **Step 1: Ask for approval in chat and wait for a yes**
- [ ] **Step 2: Install the CLI on PATH:** `pipx install --force /Users/luisfarfan/Developer/labs/agents/rein`, then from an Orca terminal check that `rein --version` prints `rein 2.0.0`
- [ ] **Step 3: Baseline:** with the owner, pick two or three recent PROXIMA features done the old way. Run `rein token-report <their transcript folder or .jsonl> --change baseline-<name> --record`, then `rein baseline mark <wf_id>` on the most representative one.
- [ ] **Step 4: Migrate config:** in `proxima-api` and `proxima-admin`, run `rein migrate-config` and show the diff. Run `--write` only after the owner approves.
- [ ] **Step 5: Pilot:** plan a small api+admin change with `/rein-plan` from `proxima-engineering`, then run `rein ship <change> --dry-run`, then `rein ship <change>`
- [ ] **Step 6: Report against spec §2.** Show Claude tokens against the baseline (target ≤ 30%), the Opus share (target ≤ 20%), gate and audit results, and the gates the owner had to answer.
