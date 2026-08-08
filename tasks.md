# Change: product-observability

## Why

Rein today knows about **one repo and one change at a time**. It plans,
executes, reviews and measures cost — all inside a single working directory.
That was enough while the unit of work was a change.

It is not enough now, and the numbers say so. Measured on `~/projects/me/proxima`:

```
24 repos, 14 with commits in the last 30 days
118 OpenSpec changes, 92 with unfinished tasks
2,649 tasks, 899 of them open
```

**A product is not a repo.** Answering "what is in flight across proxima?"
means opening twenty-four directories. A monorepo tool does not help: these are
separate repositories with separate remotes, not directories under one root.

**Status is reconstructed, and reconstruction lies.** The only record of what
got done is `tasks.md` checkboxes plus git log. A checkbox is written when
someone ticks it — not when the task was implemented, not when its verification
passed, not when it merged. The ledger records *runs*; nothing records *task
transitions*. So "when did T003 actually pass?" is answered by guessing at
timestamps.

**And 899 open tasks do not fit in a terminal.** `rein state` printing 899 rows
is not a tool, it is a wall. This is where a board earns its place — grouping,
filtering, and showing ninety-two in-flight changes on one screen.

The risk in adding a board is the obvious one: **it becomes the source of
truth.** Someone drags a card, repo and board disagree, and the board wins
because it is what people see. From there execution starts serving the board.

So the shape is fixed up front: **state is derived locally, Plane is a
projection of it.** One direction. Deleting the Plane config must leave
execution byte-identical — and that is a test, not an intention.

## What was measured

Plane 1.4.1 Community was brought up locally and driven through a full
projection of two real repos. Everything below is measured, not documented.

**The idempotent upsert works — for work items and modules only.**

| probe | result |
|---|---|
| `POST` with a used `external_id` on an issue or module | **409, and the body carries the existing `id`** |
| `GET ?external_id=X&external_source=Y` on issues | returns **the object**, 404 when absent |
| states of an API-created project | 5, one per group: `backlog` (default), `unstarted`, `started`, `completed`, `cancelled` |
| `POST /modules/` on an API-created project | **400 `"Modules are not enabled"`** until `PATCH {"module_view": true}` |

**Projects do not behave that way, and this is the finding that reshaped the
plan.** Three separate measurements:

- the **name**-uniqueness check runs *before* `external_id`, so re-syncing an
  existing project returns `409 {"name": "..."}` with **no `id`** — useless
- `GET ?external_id=` **does not filter** on projects; it returns everything
- there is **no `external_id` uniqueness** on projects: posting a repeated one
  with a different name returned **201 and created a duplicate**

So the project upsert must be **list-and-match client-side**. A plan written on
the uniform-upsert assumption would have failed on its first task.

**Four more that cost time to find:**

- `Project.FORBIDDEN_IDENTIFIER_CHARS_PATTERN` rejects `& + , : ; $ ^ } { * = ?
  @ # | ' < > . ( ) % ! -` — including the **hyphen**, so almost no repo name
  is a legal project name. Sanitising is mechanical and required
- `identifier` is `max_length=12`, unique per workspace. Truncating collided on
  **7 of 24** proxima repos (`proxima-website`, `-v2`, `-v3`, `2` all became
  `PROXIMAWEB`)
- Plane reports that collision as `{"name": "The project name is already
  taken"}` — the **wrong field**, because the handler maps any `IntegrityError`
  to that message
- `workspace_seed` creates a demo project **named after the workspace**, which
  then blocks the name rein needs. It must not be called
- work items are **not** attached to a module on creation; that is a separate
  `POST /modules/{id}/module-issues/`
- the instance enforces `API_KEY_RATE_LIMIT=60/minute`, returning
  `error_code 5900`. Backoff is not optional

**Volume decides the sync strategy.** At 60 req/min a full projection of
proxima costs **2,815 calls ≈ 47 minutes**, and would put **899 open cards** on
the board. Windowing by recency changes both:

| window | changes with tasks | work items | first sync | open cards |
|---|---|---|---|---|
| 7d | 6 | 78 | 4 min | 19 |
| **30d** | **21** | **396** | **9 min** | **130** |
| 60d | 61 | 1,230 | 23 min | 430 |
| all | 118 | 2,649 | 47 min | 899 |

**Workspaces are unlimited but rein cannot create one via the API.** The only
gate is `DISABLE_WORKSPACE_CREATION` (default `0`) — no count or seat limit —
but creation lives behind session auth: an API key gets `401` there and `404`
on `/api/v1/workspaces/`. Creating one through the Django ORM works and needs
no credential, which is how the two demo workspaces were made.

**Task dependencies have nowhere native to go.** API v1 exposes `links`,
`comments`, `activities`, `attachments` and the `parent` field — but no
relation endpoint, so `blocked-by` cannot be expressed. `parent` means
hierarchy and these tasks are siblings.

## Scope

- In: a workspace descriptor covering N sibling git repos, and the guard that a
  monorepo is not one
- In: task transitions **emitted during execution**, and a canonical product
  state folded from them plus git plus the plan
- In: a Plane client with a **per-entity** write strategy, mechanical name and
  identifier sanitising, and rate-limit backoff
- In: a recency window with a configurable default of 30 days; changes outside
  it project as a **closed Module with no work items**, preserving history at
  1/22nd of the cost
- In: incremental sync — only what changed since the last projection
- In: humanized Plane text from a small agent, language configurable, Spanish
  by default
- In: a test proving that removing the Plane config changes nothing
- Out: reading Plane **as a source of state**. No import, no webhook, no
  reconciliation, and nothing Plane returns may change a local file. Reads that
  a write needs — listing projects to match an `external_id` (D3), fetching the
  state ids to map a group, checking the workspace exists before failing with a
  useful message — are part of writing, not a second source of truth
- Out: rein creating Plane workspaces over the API — it cannot
- Out: rein deleting anything in Plane. Create and update only
- Out: `workspace_seed`. It actively blocks the names rein needs
- Out: sub-work-items. A rein task is atomic by construction — one verification,
  one review. Anything divisible would already be two tasks
- Out: a plugin framework for other trackers. One projection, concrete. YAGNI
- Out: an LLM in the sync path. The agent writes prose; it decides nothing

## Decisions

- **D1** State is derived locally; Plane is a projection. Deleting `plane.json`
  must leave `rein state`, `rein-apply` and `rein-step` identical
- **D2** Transitions are **emitted**, not inferred. Only the emitter knows when
  a task started, when its verification passed, and when it merged
- **D3** Idempotency is **per entity, not uniform**. Work items and modules ride
  the measured 409-with-id; projects are matched client-side from one listing
  call, because all three of their identity mechanisms were measured broken
- **D4** The API key lives only in `REIN_PLANE_API_KEY`. `plane.json` holds base
  URL, workspace slug, window and language — nothing secret, safe to commit
- **D5** A workspace is a **list of sibling repos**, each with its own `.git`
- **D6** rein creates Projects and Modules; it never deletes. A change that
  disappears locally leaves its Module untouched
- **D7** Humanization is decorative and must never block. Any failure falls back
  to raw text and the sync proceeds
- **D8** `Depends on` is rendered as body text, not a Plane relation — API v1
  has none, and `parent` means hierarchy, not dependency
- **D9** Sanitising a name or identifier is **mechanical**, not humanization.
  It runs with no agent and cannot fail: a repo whose name Plane rejects must
  still sync
- **D10** The default window is 30 days because it was measured: it puts 130
  open cards on the board instead of 899, and costs 9 minutes instead of 47.
  Outside it, a change becomes one closed Module — the history survives, the
  2,200 work items nobody would read do not

---

<!-- Tasks T001-T002 are the product state and stand alone: they deliver
     `rein state` across N repos with no Plane anywhere. If this change is split,
     that is the seam — and T005's removability proof is only meaningful once
     T002 has been in use. -->

- [x] T001 A workspace is N repos, and a monorepo is not one
  - Type: implementation
  - Depends on: none
  - Human review: false
  - Verification: `python3 -m unittest tests.test_workspace`
  - Acceptance:
    - `plugins/rein/lib/workspace.py` is a new module exposing `discover(start)`
      — walking up from `start` for `.rein/workspace.json` — and
      `members(doc, root)` returning each member's name, resolved path, current
      branch and short head; `tests/test_workspace.py` is a new module that
      builds a fixture of three real `git init` repos and asserts the exact
      tuple set, so the walk is proven against directories that exist
    - `members()` rejects a member whose path has no `.git` **of its own**,
      naming the offender — `tests/test_workspace.py` constructs a monorepo
      (one `.git`, three subdirectories), points a descriptor at the
      subdirectories, and asserts all three are rejected (D5)
    - a member path that does not exist, or that escapes the root via `..`, is
      reported and skipped rather than raising — `tests/test_workspace.py`
      asserts the surviving member set and the reported reasons, because a
      workspace with one broken entry must still answer for the others
    - `rein workspace` prints one line per member with its branch and head, and
      exits 0 with a single explanatory line when no descriptor is found —
      `tests/test_workspace.py` invokes the CLI entry point and asserts both
    - `plugins/rein/lib/workspace.py` reads no network and runs no command other
      than `git`; `tests/test_workspace.py` pins the resolved git invocations so
      a later refactor cannot widen what this module executes

- [x] T002 Transitions are emitted while they happen, not guessed afterwards
  - Type: implementation
  - Depends on: T001
  - Human review: false
  - Verification: `python3 -m unittest tests.test_product_state`
  - Acceptance:
    - `rein event task <task-id> <transition>` appends to the existing event log
      with the transition, task id, change name and resolved repo — accepting
      exactly `started`, `verified`, `blocked`, `merged` and rejecting anything
      else by name; `tests/test_product_state.py` is a new module asserting the
      accepted set and that an unknown transition exits non-zero without writing
    - **the loop emits the transitions, not the skill prose.** `loop.js` exposes
      a pure `transitionsFor(step)` returning the events a step must emit —
      `started` before the first attempt, `verified` only when the verification
      exited 0, `blocked` when the attempt cap is reached — and the step calls
      it; `tests/test_product_state.py` extracts that function from the shipped
      `loop.js` and **executes** it over fixture steps, asserting the emitted
      sequence for a passing step, a failing one and a capped one. Asserting
      that skill prose contains the words would be satisfied by a fixture
      without the path ever running, which is the defect class this plan exists
      to remove (D2)
    - `plugins/rein/lib/product_state.py` exposes `state(root)` folding the event
      log over the plan and git head into one record per task carrying its
      transition, when it happened, and the commit at that moment;
      `tests/test_product_state.py` feeds a log with out-of-order and duplicated
      events and asserts the fold is last-write-wins per task and stable across
      two calls
    - a task in `tasks.md` with no events appears as `planned` rather than being
      omitted — `tests/test_product_state.py` pins this, because a state whose
      unstarted tasks vanish reports the product as further along than it is
    - `state()` carries each change's **last-touched age in days**, taken from
      the newest commit touching the change directory —
      `tests/test_product_state.py` asserts it against a fixture with backdated
      commits, since T005's window is computed from this and nothing else
    - `rein state` prints the per-task record and, given a workspace, folds every
      member into one table — `tests/test_product_state.py` asserts the
      workspace-wide output names each member repo

- [x] T003 One write strategy per entity, because they were measured different
  - Type: implementation
  - Depends on: none
  - Human review: false
  - Verification: `python3 -m unittest tests.test_plane_client`
  - Acceptance:
    - `plugins/rein/lib/plane_client.py` is a new module whose work-item and
      module upsert is `POST` → **201 created** | **409 → read `id` from the
      body** → `PATCH`; `tests/test_plane_client.py` is a new module driving it
      through a fake transport replaying the recorded 201 and 409 bodies and
      asserting the exact request sequence
    - the **project** upsert instead lists projects once and matches on
      `external_id` + `external_source` client-side, patching on a hit and
      posting on a miss — `tests/test_plane_client.py` asserts no project flow
      ever reads an `id` out of a 409 body, and replays the measured
      `409 {"name": "The project name is already taken"}` to prove that path
      does not crash (D3)
    - `safe_name()` strips every character in the measured forbidden set and
      `safe_identifier()` emits at most 12 characters as an 8-character stem
      plus a 4-character hash of the **full** name; `tests/test_plane_client.py`
      carries the 24 colliding repo names **as a literal list in the test file**
      — `proxima-website`, `proxima-website-v2`, `proxima-website-v3`,
      `proxima-website2` and the rest — and asserts they produce 24 distinct
      identifiers, since truncation collided on 7 of them. The names are data,
      not a repository to fetch: nothing in this test reads a path outside this
      repo (D9)
    - a `409`/`400` whose body does not carry an `id` is raised as a named
      conflict error carrying the attempted name, never as a `KeyError` —
      `tests/test_plane_client.py` asserts the error type and that the message
      names the entity, because Plane reports identifier collisions under the
      `name` key and the message must not repeat that misdirection
    - every request retries with exponential backoff on `error_code 5900` and
      on HTTP 429, and gives up after a bounded number of attempts —
      `tests/test_plane_client.py` asserts the retry count and that the sleep
      is injected rather than real, so the suite does not sleep
    - creating a Project is followed by `PATCH {"module_view": true}` before any
      Module is attempted — `tests/test_plane_client.py` asserts the ordering,
      because the probe measured `400 "Modules are not enabled"` otherwise
    - attaching work items to a Module is a separate
      `POST /modules/{id}/module-issues/` — `tests/test_plane_client.py` asserts
      it is issued, since creation alone leaves every card unlinked
    - `external_id` is `rein:{workspace}:{repo}:{change}:{task}` with every
      component escaped so a name containing `:` cannot forge another entity's
      identity, and `external_source` is fixed to `rein`;
      `tests/test_plane_client.py` asserts the collision case explicitly
    - the API key is read only from `REIN_PLANE_API_KEY`; the client raises a
      named error when absent and never reads it from any file —
      `tests/test_plane_client.py` writes a `plane.json` containing a key-shaped
      field and asserts it is ignored (D4)
    - no code path issues `DELETE` — `tests/test_plane_client.py` asserts the
      emitted method set is exactly `{GET, POST, PATCH}` (D6)
    - `tests/test_plane_client.py` opens no socket: the transport is injected and
      a test asserts the default one is never constructed during the suite

- [x] T004 The board reads like a person wrote it, or it reads like the plan
  - Type: implementation
  - Depends on: T003
  - Human review: false
  - Verification: `python3 -m unittest tests.test_humanize`
  - Acceptance:
    - `plugins/rein/lib/humanize.py` is a new module exposing
      `humanize(text, kind, lang)` returning prose for a title and body, with
      `lang` read from `plane.json` and defaulting to `es`;
      `tests/test_humanize.py` is a new module asserting the default and that an
      explicit `en` reaches the request unchanged
    - every failure mode returns the **raw input**: no agent, non-zero exit,
      timeout, empty output, or output past the configured cap —
      `tests/test_humanize.py` drives all five and asserts verbatim return (D7)
    - humanization never runs on names or identifiers, which are sanitised
      mechanically by T003 — `tests/test_humanize.py` asserts a repo whose name
      Plane would reject still yields a legal project name with the humanizer
      replaced by a function that raises (D9)
    - the humanizer runs at most once per entity per sync, cached by content
      hash — `tests/test_humanize.py` syncs a fixture with a repeated title and
      asserts one invocation
    - `humanize()` is never on the path deciding *what* to sync:
      `tests/test_humanize.py` asserts the projection computes its full upsert
      set with the humanizer raising, identical to the humanized run

- [x] T005 Only what is live, and only what changed
  - Type: implementation
  - Depends on: T002, T003
  - Human review: false
  - Verification: `python3 -m unittest tests.test_plane_window`
  - Acceptance:
    - `plugins/rein/lib/plane_projection.py` is a new module exposing
      `select(state, window_days)` splitting changes into **live** (touched
      within the window, projected with all their work items) and **history**
      (one closed Module, **no work items**); `tests/test_plane_window.py` is a
      new module asserting the split against a fixture with backdated changes
    - the window defaults to **30** and is read from `plane.json` —
      `tests/test_plane_window.py` asserts the default, an override, and that a
      change whose age equals the window exactly is treated as live, since an
      off-by-one at the boundary silently drops a change nobody would look for.
      The fixture is synthetic, with ages the test sets: the proxima corpus that
      motivated 30 days lives in this plan's Why as evidence and is not
      reachable from a worktree, so pinning its counts would be unfalsifiable
      here and would drift on the next commit to that repo (D10)
    - a history Module carries its task counts in its description and is created
      in a completed state — `tests/test_plane_window.py` asserts no work item
      is emitted for a history change, since that is the entire saving
    - `select()` emits only entities whose content hash differs from the last
      recorded projection — `tests/test_plane_window.py` runs twice over an
      unchanged state and asserts the second emits nothing, then mutates one
      task and asserts exactly one entity is emitted. Applying the selected
      entities and deciding when to write the record back is T006's
      `plane_sync.run_sync()`, not this module's job — see T006 acceptance 5
      for that half
    - (moved to T006 acceptance 5 — round-2 review finding 2: an earlier
      `sync()` here stopped at the first failure and wrote nothing at all,
      the OPPOSITE of what `run_sync()` actually does; it was dead code,
      never called outside its own now-removed tests, and duplicating the
      guarantee here would just contradict production again)

- [x] T006 Deleting the Plane config changes nothing
  - Type: implementation
  - Depends on: T004, T005
  - Human review: false
  - Verification: `python3 -m unittest tests.test_plane_projection`
  - Acceptance:
    - `rein sync --plane` walks the selection from T005 and issues the upserts
      from T003 — repo to Project, change to Module, task to Work Item,
      transition to the state whose `group` matches (`planned`→`backlog`,
      `started`→`started`, `verified`/`merged`→`completed`,
      `blocked`→`unstarted`) — `tests/test_plane_projection.py` is a new module
      asserting the full mapping against the five groups measured on a fresh
      project
    - **with `plane.json` absent, `rein state`, `rein-apply` and `rein-step`
      produce byte-identical output to a run where it is present** —
      `tests/test_plane_projection.py`'s `ByteIdenticalWithoutPlaneJsonTests`
      compares the deterministic CLI commands `rein-apply`/`rein-step` actually
      shell out to (`state`, `next`, `tasks`, `context`; see SKILL.md) both ways
      over one fixture, since the two skills themselves are agent-driven and
      have no deterministic output to diff (D1).
      `NoPlaneReachableFromReinApplyOrReinStepTests` backs this mechanically
      for the two named commands themselves: `loop.js` (what both drive),
      `plan.py`, `verify.py`, `gate.py` and `plan_check.py` are asserted to
      contain no `plane.json` literal and no
      `plane_client`/`plane_sync`/`plane_projection`/`humanize` import
      (round-2 review finding 3)
    - `rein sync --plane` with no `plane.json` exits 0 with one line saying the
      projection is not configured; with `plane.json` present but
      `REIN_PLANE_API_KEY` unset it exits non-zero naming the variable; with
      both present but the workspace slug absent in Plane it exits non-zero
      saying the workspace must be created by a human and printing the URL —
      `tests/test_plane_projection.py` pins all three, since they mean different
      things and must not report the same way
    - a task's `Depends on` appears as a plain line in the work item body and
      **no relation call is attempted** — `tests/test_plane_projection.py`
      asserts the dependency text is present and that the emitted request set
      touches no `/relation` path, since a silent 404 would look like a working
      feature (D8)
    - a Plane failure — any non-2xx that is not the expected 409, or a transport
      error — leaves the local state untouched and is reported per entity rather
      than aborting the sync; `tests/test_plane_projection.py` injects a failure
      on the third of five upserts and asserts the other four were attempted and
      the local event log is unchanged (D1). The projection record
      (`plane_projection.save_record`) is written once, at the end of the whole
      sync, carrying only the entities that actually applied — a failed
      entity's key is simply absent, so `select()` re-emits it (and it alone)
      on the next run, with no separate "retry set" needed; the same test
      asserts the record holds exactly the four that applied and none of the
      failed one's key
