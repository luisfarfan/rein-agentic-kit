#!/usr/bin/env python3
"""T006 -- wires T005's selection to T003's writes and T004's prose behind
`rein sync --plane`, and is the module that PROVES deleting `plane.json`
changes nothing (D1): nothing else in this codebase imports this module or
`plane_client`/`plane_projection`/`humanize`, so a repo with no `plane.json`
never runs a line of it.

Three failure modes are distinguished on purpose, because they mean
different things (AC3):

  * no `plane.json` at all -- the projection is simply not configured. Not
    an error: exits 0 with one explanatory line.
  * `plane.json` present but `REIN_PLANE_API_KEY` unset -- an operator
    forgot a step. `PlaneClient.__init__` already raises `PlaneAuthError`
    naming the variable (D4); this module lets it propagate.
  * both present but the workspace slug does not exist in Plane -- rein
    cannot create a workspace over the API (Out-of-scope), so this is a
    human's job. `WorkspaceMissing` names the slug and prints the URL to
    create it at.

The sync itself never aborts on a single entity's failure (AC5): every
selected entity is attempted, in order, and a failure is recorded against
that entity alone. The on-disk projection record is written once, at the
end, carrying only the entities that actually applied -- so a failed
entity (and anything not yet reached) is retried on the next run, and the
local task-event log (`events.jsonl`, T002) is never touched by any of
this: this module only reads state, it never writes a transition.
"""

from __future__ import annotations

import json
import os
import time

import humanize as _humanize
import plan as _plan
import plane_client as _pc
import plane_projection as _pp
import product_state as _pstate
import workspace as _workspace

PLANE_CONFIG_FILENAME = "plane.json"
RECORD_RELATIVE_PATH = os.path.join(".rein", "plane_record.json")

# finding 5 (round-1 review): a 30-day window means ~396 work items, each a
# distinct title -- humanizing all of them is ~400 sequential agent calls
# against D10's 9-minute measured budget. Humanization is therefore
# opt-in, and even then scoped by default to the ~22 project/module titles
# a sync produces, never the work items that dominate the count.
HUMANIZE_CONFIG_KEY = "humanize"
HUMANIZE_WORK_ITEMS_CONFIG_KEY = "humanize_work_items"
HUMANIZE_BUDGET_CONFIG_KEY = "humanize_budget_seconds"
DEFAULT_HUMANIZE_BUDGET_SECONDS = 60.0

# AC1: transition -> the Plane State GROUP it maps onto. `merged` and
# `verified` share `completed` -- both mean the work is done, one via review
# gate, the other via a direct merge.
TRANSITION_GROUP = {
    "planned": "backlog",
    "started": "started",
    "verified": "completed",
    "merged": "completed",
    "blocked": "unstarted",
}


class PlaneSyncError(Exception):
    """Base class for the config-shaped errors `run_sync` raises before any
    entity is attempted (AC3) -- distinct from a per-entity failure, which
    is reported, never raised."""


class NotConfigured(PlaneSyncError):
    """No `plane.json` at the sync root. Not a failure -- the caller maps
    this to exit 0 with one explanatory line."""


class WorkspaceMissing(PlaneSyncError):
    """`base_url`/`workspace_slug` resolve, but the workspace does not
    exist in Plane. rein cannot create one over the API (Out-of-scope) --
    a human must, at `{base_url}/create-workspace/`."""

    def __init__(self, slug: str, base_url: str):
        self.slug = slug
        self.base_url = base_url
        self.create_url = f"{base_url.rstrip('/')}/create-workspace/"
        super().__init__(
            f"workspace {slug!r} does not exist in Plane -- a human must create it "
            f"first, at {self.create_url}"
        )


def load_config(root: str) -> dict | None:
    """The parsed `plane.json` at `root`, or `None` when it is absent,
    unreadable, malformed, or not an object -- every one of those means
    "not configured" to this module (AC3's first branch)."""
    path = os.path.join(root, PLANE_CONFIG_FILENAME)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def repos_for(root: str) -> list[tuple[str, str]]:
    """`[(name, path), ...]` -- every member of the `.rein/workspace.json`
    found above `root` (D5), or, when none is found, the single repo at
    `root` under its own directory name (AC1: repo -> Project)."""
    found = _workspace.discover(root)
    if found["found"] and found.get("doc") is not None:
        ok, _problems = _workspace.members(found["doc"], found["root"])
        if ok:
            return [(name, path) for name, path, _branch, _head in ok]
    return [(os.path.basename(os.path.abspath(root)) or "repo", root)]


def changes_for(repo_root: str) -> list[str]:
    """Every change name to project for one repo: every `openspec/changes/*`
    entry when the repo uses that layout, else the single implicit change a
    plain `tasks.md` plan resolves under (`product_state.state()` reads its
    name from the plan header, not from this argument)."""
    changes_dir = os.path.join(repo_root, "openspec", "changes")
    if os.path.isdir(changes_dir):
        return _plan._openspec_changes(repo_root)
    return [""]


def build_state(repo_root: str) -> list[dict]:
    """One `product_state.state()` record per change in `repo_root` -- the
    shape `plane_projection.select()` expects (T005)."""
    return [_pstate.state(repo_root, change=c) for c in changes_for(repo_root)]


def _depends_on_by_task(repo_root: str, change: str) -> dict[str, list[str]]:
    plan_doc = _plan.read_plan(repo_root, change=change)
    return {t["id"]: (t.get("dependsOn") or []) for t in (plan_doc.get("tasks") or [])}


def _state_id_for_transition(states: list[dict], transition: str) -> str | None:
    """AC1: the id of the State whose `group` matches the Plane group a
    transition maps onto (`TRANSITION_GROUP`) -- `None` when the project
    carries no state in that group, so the caller can skip setting it
    rather than send a bogus value."""
    group = TRANSITION_GROUP.get(transition, "backlog")
    for state in states:
        if state.get("group") == group:
            return state.get("id")
    return None


def _work_item_body(repo_root: str, change: str, task_id: str, depends_index: dict) -> str:
    """AC4: `Depends on` as a **plain line**, appended after humanization so
    the agent can never paraphrase it away -- no relation call is ever
    attempted, so this line is the only place a dependency is visible."""
    if change not in depends_index:
        depends_index[change] = _depends_on_by_task(repo_root, change)
    deps = depends_index[change].get(task_id) or []
    return f"Depends on: {', '.join(deps)}" if deps else ""


def _humanize_enabled(config: dict) -> bool:
    """Opt-in only (finding 5): with no `humanize` key in `plane.json`, or
    a falsy one, every title reaches Plane raw and no agent subprocess is
    ever spawned."""
    return bool(config.get(HUMANIZE_CONFIG_KEY, False))


def _humanize_work_items_enabled(config: dict) -> bool:
    """Work item titles -- by far the largest group (396 of the measured
    30-day window's ~420 entities) -- stay raw even when humanization is
    on, unless explicitly requested; project and module titles (~22) are
    the ones it actually pays for."""
    return bool(config.get(HUMANIZE_WORK_ITEMS_CONFIG_KEY, False))


def _humanize_budget_seconds(config: dict) -> float:
    value = config.get(HUMANIZE_BUDGET_CONFIG_KEY, DEFAULT_HUMANIZE_BUDGET_SECONDS)
    try:
        return float(value)
    except (TypeError, ValueError):
        return DEFAULT_HUMANIZE_BUDGET_SECONDS


def _safe_humanize(hcache, text: str, kind: str) -> str:
    """Guards a `.humanize()` call that itself raises. The shipped
    `humanize.HumanizeCache` never does -- its underlying `humanize()`
    already falls back to raw text internally (D7) -- but this is the
    outer belt for any other object passed as `humanize_cache` (tests, a
    future cache implementation), so a broken one degrades exactly like
    the well-behaved one instead of aborting the entity being applied."""
    try:
        return hcache.humanize(text, kind)
    except Exception:
        return text


class _BudgetedHumanizer:
    """Wraps a humanize cache with an overall wall-clock budget for one
    whole sync (finding 5). Once the cumulative time this run has spent
    inside `.humanize()` reaches `budget_seconds`, every later call
    returns the raw text immediately, with no further agent invocation --
    nothing bounded total spend before this, only the per-call 30s
    timeout, so a large window could still dominate wall-clock even with
    humanization scoped to project/module titles alone."""

    def __init__(self, inner, budget_seconds: float):
        self._inner = inner
        self._budget = budget_seconds
        self._spent = 0.0
        self._exhausted = False

    def humanize(self, text: str, kind: str, lang: str | None = None) -> str:
        if self._exhausted:
            return text
        start = time.monotonic()
        try:
            return self._inner.humanize(text, kind, lang)
        finally:
            self._spent += time.monotonic() - start
            if self._spent >= self._budget:
                self._exhausted = True


def _module_payload_for_change(change_name: str, state_by_change: dict, window_days: int) -> dict:
    """The module payload T005's `_module_entity` would compute for
    `change_name` **right now**, independent of whether the module entity
    itself was part of the current `select()` batch.

    Needed because a live change's module payload is `{"name": ...}` alone
    (T005) -- it never varies with the change's tasks, so its content hash
    never changes after the first sync. A later run that only added or
    moved a task therefore never re-emits the module entity, and
    `module_ids` (populated solely from module entities processed in the
    *current* batch) would stay empty for that change -- silently
    orphaning every new work item from its Module forever (finding 1,
    round-1 review). Resolving the payload independently of `select()`'s
    output lets a work item's module id be looked up (or, on a project
    with no cached id yet, upserted) regardless.
    """
    change_state = state_by_change.get(change_name) or {}
    tasks = change_state.get("tasks") or []
    age = change_state.get("lastTouchedDays")
    live = age is None or age <= window_days
    return dict(_pp._module_entity(change_name, tasks, live)["payload"])


def _resolve_module_id(
    client,
    hcache,
    humanize_enabled: bool,
    workspace_slug: str,
    repo_name: str,
    project_id: str,
    change_name: str,
    module_ids: dict,
    state_by_change: dict,
    window_days: int,
) -> str:
    """The Plane id of `change_name`'s Module -- from this batch's cache if
    a module entity was already applied in it, else upserted here (the
    409-with-id path returns the existing id harmlessly when nothing
    changed). Called before every work item is applied, never only when
    the module entity itself was selected (finding 1)."""
    if change_name in module_ids:
        return module_ids[change_name]
    payload = _module_payload_for_change(change_name, state_by_change, window_days)
    raw_name = payload.pop("name")
    name = _safe_humanize(hcache, raw_name, _humanize.KIND_TITLE) if humanize_enabled else raw_name
    module_ext_id = _pc.make_external_id(workspace_slug, repo_name, change_name, "")
    module = client.upsert_module(project_id, name, module_ext_id, **payload)
    module_ids[change_name] = module["id"]
    return module_ids[change_name]


def run_sync(root: str, *, client_factory=None, humanize_cache=None, env=None) -> dict:
    """The whole sync: config checks (AC3) then, entity by entity, every
    Project/Module/Work-Item upsert (AC1), never aborting on one entity's
    failure (AC5).

    Returns `{"applied": [key, ...], "failed": [{"key", "error"}, ...]}`.
    Raises `NotConfigured` / `WorkspaceMissing` / `plane_client.PlaneAuthError`
    before any entity is attempted -- those three are config problems, not
    per-entity ones, and mean different things to the caller (AC3).
    """
    config = load_config(root)
    if config is None:
        raise NotConfigured()

    base_url = config.get("base_url") or config.get("baseUrl") or ""
    workspace_slug = config.get("workspace_slug") or config.get("workspace") or ""
    window_days = _pp.load_window_days(root)

    factory = client_factory or _pc.PlaneClient
    client = factory(base_url, workspace_slug, env=env)

    workspace_doc = client.get_workspace()
    if workspace_doc is None:
        raise WorkspaceMissing(workspace_slug, base_url)

    hcache = _BudgetedHumanizer(
        humanize_cache or _humanize.HumanizeCache(root=root),
        _humanize_budget_seconds(config),
    )
    humanize_enabled = _humanize_enabled(config)
    humanize_work_items = humanize_enabled and _humanize_work_items_enabled(config)

    record_path = os.path.join(root, RECORD_RELATIVE_PATH)
    record = _pp.load_record(record_path)
    new_record = dict(record)

    report: dict = {"applied": [], "failed": []}

    for repo_name, repo_root in repos_for(root):
        # `plane_projection`'s entity keys (`module:<change>`, `item:<change>:
        # <task>`) carry no repo -- it was built for a single repo (T005). A
        # workspace of several repos can legitimately have two changes named
        # the same, so the persisted record is namespaced per repo here,
        # never inside plane_projection itself.
        repo_prefix = f"{repo_name}::"
        repo_record = {
            k[len(repo_prefix):]: v for k, v in record.items() if k.startswith(repo_prefix)
        }

        state_list = build_state(repo_root)
        state_by_change = {cs.get("change") or "": cs for cs in state_list}
        entities = _pp.select(state_list, window_days, record=repo_record)
        if not entities:
            continue

        project_ext_id = _pc.make_external_id(workspace_slug, repo_name, "", "")
        try:
            project_title = (
                _safe_humanize(hcache, repo_name, _humanize.KIND_TITLE) if humanize_enabled else repo_name
            )
            # D9: `name` is the RAW repo name, mechanically sanitised by
            # `safe_name()`/`safe_identifier()` inside `ensure_project()` --
            # never the humanized string. Humanizing `name` directly made
            # both the display name AND the workspace-unique `identifier`
            # depend on non-deterministic agent prose: two repos could
            # collide on the identifier where their raw names never would,
            # and the identifier churned across syncs whenever a different
            # reply (or a fallback to raw text) changed the input string
            # (finding 2, round-1 review). A humanized title, when wanted,
            # rides `description` instead -- a field with no identity role.
            project = client.ensure_project(repo_name, project_ext_id, description=project_title)
        except Exception as exc:  # noqa: BLE001 -- reported per repo, never aborts the sync
            report["failed"].append({"key": f"project:{repo_name}", "error": str(exc)})
            continue
        project_id = project["id"]

        module_ids: dict[str, str] = {}
        depends_index: dict[str, dict] = {}
        states_by_project = None

        for entity in entities:
            key = entity["key"]
            change_name = entity["change"]
            try:
                if entity["type"] == "module":
                    payload = dict(entity["payload"])
                    raw_name = payload.pop("name")
                    name = (
                        _safe_humanize(hcache, raw_name, _humanize.KIND_TITLE)
                        if humanize_enabled else raw_name
                    )
                    module_ext_id = _pc.make_external_id(workspace_slug, repo_name, change_name, "")
                    module = client.upsert_module(project_id, name, module_ext_id, **payload)
                    module_ids[change_name] = module["id"]
                else:
                    task_id = entity["taskId"]
                    payload = entity["payload"]
                    transition = payload.get("transition", "planned")
                    if states_by_project is None:
                        states_by_project = client.list_states(project_id)
                    state_id = _state_id_for_transition(states_by_project, transition)
                    fields = {}
                    if state_id:
                        fields["state"] = state_id
                    body = _work_item_body(repo_root, change_name, task_id, depends_index)
                    if body:
                        fields["description"] = body
                    raw_name = payload.get("name") or task_id
                    name = (
                        _safe_humanize(hcache, raw_name, _humanize.KIND_TITLE)
                        if humanize_work_items else raw_name
                    )
                    item_ext_id = _pc.make_external_id(workspace_slug, repo_name, change_name, task_id)
                    work_item = client.upsert_work_item(project_id, name, item_ext_id, **fields)
                    # Resolved regardless of whether a module entity was
                    # itself part of this batch (finding 1) -- a live
                    # change's module payload never varies with its tasks,
                    # so its hash never changes after the first sync, and
                    # `module_ids.get(change_name)` alone would silently
                    # and permanently orphan every work item added or
                    # transitioned on any later run.
                    module_id = _resolve_module_id(
                        client, hcache, humanize_enabled, workspace_slug, repo_name,
                        project_id, change_name, module_ids, state_by_change, window_days,
                    )
                    if module_id:
                        client.attach_work_item_to_module(project_id, module_id, work_item["id"])
                new_record[f"{repo_prefix}{key}"] = entity["hash"]
                report["applied"].append(key)
            except Exception as exc:  # noqa: BLE001 -- per-entity, never aborts the sync (AC5)
                report["failed"].append({"key": key, "error": str(exc)})
                continue

    if report["applied"]:
        _pp.save_record(record_path, new_record)

    return report
