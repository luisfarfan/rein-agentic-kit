# Recorded Orca responses

Captured 2026-10-17 from Orca 1.4.227, from an Orca terminal in this repo's checkout
(the repo was registered with `orca repo add`). One throwaway Run, two tasks, one
Claude Haiku worker that changed nothing, one decision gate. The owner approved the capture.

Redaction: keys matching email/token/secret/password/providerAccountId/cookie/apiKey were
set to `"redacted"`; `/Users/<owner>` became `/HOME`; email-shaped strings became
`redacted@example.com` (this also rewrote a `git@github.com:` remote URL in
`repo_list.json`). `account_list.json` predates this capture (Task 4).

## Commands, in order

| File | Command (every one with `--json`) |
|---|---|
| `status.json` | `orca status` |
| `repo_list.json` | `orca repo list` |
| `run_create.json` | `orca orchestration run-create --objective "rein fixture capture"` |
| `task_create.json` | `orca orchestration task-create --task-title fixture --spec "…"` |
| `worker_start.json` | `orca orchestration worker-start --task <task> --worktree current --agent claude --model haiku` |
| `check_worker_done.json` | `orca orchestration check --wait --types worker_done,escalation,question --timeout-ms 300000` |
| `worker_show.json` | `orca orchestration worker-show --dispatch <dispatch>` |
| `worker_list.json` | `orca orchestration worker-list --run <run>` |
| `task_create_gate.json` | `orca orchestration task-create --task-title fixture-gate --spec "…"` |
| `gate_create.json` | `orca orchestration gate-create --task <task2> --question "fixture gate?" --options '["yes","no"]'` |
| `gate_list.json` | `orca orchestration gate-list` (gate still pending) |
| `gate_resolve.json` | `orca orchestration gate-resolve --id <gate> --resolution yes` |
| `worker_release.json` | `orca orchestration worker-release --dispatch <dispatch>` |
| `check_ack.json` | `orca orchestration check --ack <delivery>` |
| `check_all.json` | `orca orchestration check --all` |

## JSON paths (all under the top-level `result`)

Every response is `{"id", "ok", "result", "_meta"}`. On failure: `{"ok": false, "error": {"code", "message"}}`.

| Value | Path |
|---|---|
| capabilities | `status`: `runtime.capabilities` (list of strings; contains `orchestration.contract.v1`) |
| app version | `status`: `runtime.appVersion` |
| repos | `repo_list`: `repos[]` with `id`, `path`, `displayName`, `worktreeBaseRef` (may be absent) |
| run id | `run_create`: `run.id` |
| task id | `task_create`: `task.id`; task deps are a JSON **string** in `task.deps` |
| dispatch id | `worker_start`: `dispatchId` (also `taskId`, `runId`) |
| worker terminal | `worker_start`: `effects[]` item with `kind == "terminal"` → `id` |
| worktree id | `worker_start`: `effects[]` item with `kind == "worktree"` → `id`; `worker_show`: `worker.worktreeId` |
| worktree path | the part of the worktree id after `::` |
| launch actually used | `worker_start`: `launch.effective.{agent,model,effort}` |
| start failure | `worker_start`: `residualResources[]`; a failed start exits non-zero with `failedStage` |
| delivery id | `check`: `deliveryId` (`null` when no messages) |
| messages | `check`: `messages[]` with `id`, `type`, `subject`, `body`, `from_handle` |
| message payload | `messages[].payload` is a JSON **string**: `{"taskId", "dispatchId", "outcome"}` |
| wait outcome | `check`: `timedOut`, `cancelled`, `connectionLost` booleans |
| dispatch status | `worker_show`: `dispatch.status` (`completed`), `worker.state` (`succeeded`), `worker.stage` (`settled`) |
| fleet view | `worker_list`: `workers[]` with `dispatchId`, `taskId`, `terminalState` (`reclaimable`), `projection.liveness.verdict` (`live`), `projection.outcome`, `projection.nextAction` |
| gate id | `gate_create`: `gate.id`; `gate.options` is a JSON **string** |
| gate state | `gate_list`: `gates[]` with `id`, `task_id`, `status` (`pending` / `resolved`), `resolution` |
| release | `worker_release`: `state` (`released`), `processAction`, `archive.status` |
| ack | `check --ack`: `acknowledged` |

## Behaviour observed (load-bearing for `rein ship`)

- **Resolving a gate sends no inbox message.** After `gate-resolve`, `check --all` still shows
  only the `worker_done`. The driver must poll `gate-list` for `status == "resolved"`;
  `check --wait` will not wake for it, so waits must be bounded.
- Orca 1.4.229 refuses `gate-create` on a task whose supervised dispatch is active
  (`task_not_startable: ... cannot open a gate while supervised Dispatch ... is active`), which is
  why `rein ship` opens every owner gate on a worker-less holder task.
- `worker_done` settles the dispatch on its own (`dispatch.status == "completed"`), but the
  worker terminal stays `reclaimable` until `worker-release`.
- `check` without `--ack` replays the same delivery; `--ack <deliveryId>` acknowledges it.
- `worker-list` reports `liveness.verdict == "live"` for a settled worker whose terminal is
  still open. Liveness alone does not mean "still working"; read `stage.worker` / `outcome`.
