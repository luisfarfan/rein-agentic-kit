# Change: a-backlog-that-feeds-the-plan

## Why

`/rein:rein-plan` has no input. You start from nothing, or from what you
happen to remember. Everything the kit does downstream — discover, plan,
apply, review, project — begins after someone has already decided what to
work on, and that decision has no home.

A bug found in passing is ten words. It is not half a task and it never will
be: it is a **pointer to a problem**. The real work of understanding it
happens in `/rein:rein-discover` and `/rein:rein-plan`, which produce a change
with criteria and verification. So the backlog item's whole life is: get
captured, get picked up, disappear.

That last part is what makes this cheap. Once the change exists, keeping the
backlog item open would count one piece of work twice — the board inflates and
a human has to remember that two cards are one thing.

**But the repo's own numbers say closing it is dangerous.** Measured on
`~/projects/me/proxima` while planning the projection:

```
118 OpenSpec changes · 92 with unfinished tasks
71 untouched for 1–4 months · 743 tasks open inside them
```

Most changes are abandoned. So: note a bug, plan it, the item closes — and the
plan joins the 71. The bug is now neither in the backlog nor fixed, and it
vanished precisely *because* the first step was done properly.

The fix is not another state. It is the discipline this kit already runs on:
**derive, never store.** A backlog item's state is recomputed on every sync
from whether its change is still alive. If the change goes stale, the item
comes back on its own, with nobody having to remember.

## What was measured

Probed against the live Plane 1.4.1 instance and the shipped CLI, not assumed:

| probe | result |
|---|---|
| a work item attached to module A, then to module B | **both attachments stay active** — attaching is additive, there is no "move", and detaching would need `DELETE`, which D6 forbids |
| an `openspec/changes/backlog/tasks.md` | `rein next` **offers its first line as claimable**, with no verification and no criteria — the loop would try to implement a ten-word note |
| `.rein/backlog.md` and `backlog.md` at the repo root | neither is found by `read_plan`; `rein next` reports nothing to claim |

The first is why the state, not the module, is what moves an item out of the
backlog. The second is why the backlog must not live under
`openspec/changes/`. The third is why `.rein/` is the right home — and
`.gitignore` already narrows to `.rein/plane_record.json`, so a backlog file
beside `workspace.json` is committed.

## Scope

- In: `.rein/backlog.md`, one line per item, with a stable id
- In: `rein backlog add "<text>"` and `rein backlog list`
- In: an `Absorbs:` declaration in a change's header, naming the items it took
- In: the backlog projected as one Module per repo, with each item's state
  **derived** — `backlog` while unclaimed, `completed` while its change is
  live, and `backlog` again if that change ages out with work unfinished
- In: a guard that a backlog file can never be executed as a plan
- Out: priorities, estimates, assignees, sprints, labels, due dates. Every one
  of them turns a list into a process that wants grooming, and grooming wants
  a UI, and a UI wants to read from Plane — which is the one thing that breaks
  D1. A line of text and an id
- Out: promoting an item automatically into a change. You delete the line and
  run `/rein:rein-plan`. Until that hurts, it is machinery for its own sake
- Out: reading anything from Plane, permanently. An item captured while
  looking at the board is typed into the repo, not dragged on the card

## Decisions

- **D1** A backlog item's Plane state is **derived on every sync**, never
  stored. That is what makes the return automatic when a change is abandoned,
  and it is the same rule the rest of the projection already follows
- **D2** The backlog lives at `.rein/backlog.md`, which `read_plan` does not
  look at. Measured: the same list under `openspec/changes/` is offered by
  `rein next` as claimable work
- **D3** An absorbed item goes to `completed` in one step, not through
  `unstarted`. It is closed with respect to itself — it was captured, then
  processed — and the change is where the state of the *work* lives. Two open
  cards for one piece of work is the defect this avoids
- **D4** Ids are assigned once and never reused, so an `external_id` cannot be
  recycled onto a different idea. `B007` means one thing forever, including
  after its line is deleted
- **D5** The item stays attached to the Backlog module after it is absorbed,
  and is additionally attached to its change's module. Measured: attachment is
  additive and removal would need `DELETE` (D6 of the projection). The board
  shows where an idea came from and where it went

---

- [x] T001 A backlog the plan can eat, and that comes back if the plan dies
  - Type: implementation
  - Depends on: none
  - Human review: false
  - Verification: `python3 -m unittest tests.test_backlog`
  - Acceptance:
    - `plugins/rein/lib/backlog.py` is a new module reading and writing
      `.rein/backlog.md`, one item per `- [Bnnn] text` line, exposing
      `items(root)`, `add(root, text)` and `next_id(existing)`;
      `tests/test_backlog.py` is a new module asserting a round trip through a
      real file, that `add` on a missing file creates it, and that a line the
      parser does not recognise is preserved verbatim rather than dropped —
      the file is hand-edited by design, so it must survive being written by a
      human
    - ids are assigned once and never reused: `next_id` returns one past the
      highest id **ever seen in the file**, including ids on lines that were
      deleted, which it learns from a trailing `<!-- rein:last-id Bnnn -->`
      marker the writer maintains — `tests/test_backlog.py` adds three items,
      deletes the middle line, adds a fourth, and asserts the fourth is `B004`
      and not `B002`, because a recycled id would silently point an existing
      Plane card at a different idea (D4)
    - `rein backlog add "<text>"` prints the assigned id and `rein backlog
      list` prints one line per item with its id and its derived state;
      `tests/test_backlog.py` invokes the CLI entry point for both and asserts
      the id appears in `add`'s output and in the subsequent `list`
    - a change declares what it took with `Absorbs: B001, B002` in its header,
      read by a **separate** `plan.absorbs_from_text(text)` — not by adding a
      key to `parse_header`, whose exact key set is pinned by
      `test_a_plan_with_no_header_still_parses_exactly_as_before` and which
      `CHANGE_HEADER_RE` already sidesteps for the same reason. It tolerates
      the spellings `Absorbs` / `Closes backlog` / `absorbs` and a `none`
      value; `tests/test_backlog.py` asserts the parsed list for each spelling,
      that a plan without the field yields `[]`, and that `parse_header`'s
      returned keys are unchanged, so every existing plan in this repo keeps
      parsing exactly as it does today
    - `plugins/rein/lib/backlog.py` exposes `derive_states(root, changes)`
      returning one state per item — `completed` when some change absorbing it
      has `lastTouchedDays` within the window, and `backlog` both when no
      change absorbs it **and** when every change that does has aged out of
      the window with at least one unfinished task; `tests/test_backlog.py`
      drives all four cases from a synthetic fixture whose ages the test sets,
      and asserts the third case explicitly, since an item that never returns
      is the failure this change exists to prevent (D1)
    - the derived state is recomputed from scratch on every call and depends on
      no stored value — `tests/test_backlog.py` runs `derive_states` twice over
      the same input and once more after ageing the change past the window,
      asserting the item returns to `backlog` with nothing deleted or reset in
      between
    - `product_state.state_all(root)` includes one synthetic change record
      named `backlog` carrying the items as tasks, so
      `plane_projection.select()` and the whole sync project it with no new
      code — `tests/test_backlog.py` asserts the record's shape matches what
      `select()` consumes by passing it straight into `select()` and checking
      the emitted entity keys, rather than asserting the dict's fields and
      hoping they line up
    - a backlog file is **never** executable as a plan: `tests/test_backlog.py`
      writes `.rein/backlog.md` AND a `backlog.md` at the root of a real git
      repo with no `tasks.md`, then asserts `rein next` reports nothing to
      claim and `rein tasks` returns zero tasks — measured today, the same list
      under `openspec/changes/backlog/` is offered as claimable with no
      verification and no criteria (D2)
    - an absorbed item is attached to its change's Module **in addition to**
      the Backlog module, never moved — `tests/test_backlog.py` asserts the
      emitted attach calls include both module ids and that no `DELETE` is
      issued, since attachment was measured to be additive and removal is
      forbidden (D5)
