# Change: graph-reaches-the-agents

## Why
Across seven runs of this repo, `graphify` was invoked ZERO times while the
loop's prompts advertised it. Three mechanical reasons, all measured:

1. Nothing ever built an index. `graphify update . --no-cluster` costs **1.4s**,
   no LLM and no API key, and produces 1179 nodes / 2231 edges on this repo.
2. `graphify-out/` is gitignored — correctly, it is machine-local and
   regenerable. But implementers work in the `rein-wt-*` WORKTREE, which is
   therefore always born without one. `hasGraph` is computed from `ctx`
   (the base repo) while every graph command runs in the worktree, so even a
   fully indexed base repo hands agents commands that answer
   `error: graph file not found`.
3. The command the prompts teach is the wrong one. `graphify query` does not
   do semantic search; it runs BFS from a literal token match. Asked
   "how does verify decide a command is not invocable" it returned 16 nodes of
   `flow.config.example.json` — because the JSON key `verify` matched. That is
   546 tokens of plausible-looking noise, which is worse for an agent than no
   answer at all.

Measured on `plugins/rein/lib/verify.py`:

| | tokens |
|---|---:|
| `Read` the whole file | 4,652 |
| `graphify query "<a natural-language question>"` | 546 (noise) |
| `graphify explain "run_one"` | **158** (the real call graph, in and out) |

**29×** — on the one thing that costs money, orientation before the first edit.

## Scope
- In: making the index exist where the agents actually work, and teaching the
  commands that answer
- Out: CodeGraph — a separate evaluation, and only worth a run if it beats
  `graphify explain` per call first
- Out: any claim about run-level savings; this change makes a wired capability
  REACHABLE, and whether that shortens runs is the measurement, not the premise
- Out: committing the index — it stays gitignored, machine-local, regenerated

## Decisions
- D1 The index is built in the WORKTREE, not shared from the base repo: 1.4s is cheaper than reasoning about whether a stale base-repo graph misleads an implementer working on new code
- D2 A capability is only claimed where its tools will actually RUN — `hasGraph` derived from the base repo while commands execute in the worktree is the same installed-vs-usable conflation `setup.py` already refuses
- D3 A retrieval command that returns confident noise is worse than none: an agent cannot tell a bad subgraph from a good one, and it pays for it on every later turn
- D4 Indexing failure never stops a run — the graph is a HINT; a repo where extraction fails degrades to bounded search exactly as today

---

- [x] T001 The graph exists where the agents work
  - Type: implementation
  - Depends on: none
  - Human review: false
  - Verification: `python3 -m unittest tests.test_graph_index`
  - Acceptance:
    - the Isolate step builds the index inside the worktree it just created (`graphify update <wd> --no-cluster`, the no-LLM path) and reports the outcome into `ISOLATE_SCHEMA` as a literal fact, in the same "report it, do not re-derive it" style as the existing fields
    - graph availability is decided by a pure function extracted from the SHIPPED `loop.js` and EXECUTED by the test — the same way `tests/test_render_policy.py` executes `decideRound`; a source-substring assertion does not count
    - that function returns unavailable when the worktree has no index, even if `ctx.capabilities` claims `graphify-index` from the base repo — the mismatch that made every graph command in every past run answer "graph file not found" (D2)
    - indexing that fails, times out, or finds no `graphify` binary is reported and the run CONTINUES with the graph off (D4); a test covers the failure path returning unavailable rather than raising
    - a test asserts the worktree's `graphify-out/` is never added to git — the index stays machine-local, and a run that committed 2.6MB of regenerable JSON would be a regression

- [x] T002 Teach the commands that answer
  - Type: implementation
  - Depends on: T001
  - Human review: false
  - Verification: `python3 -m unittest tests.test_graph_retrieval`
  - Acceptance:
    - the RETRIEVAL block is extracted into a pure function of its inputs (serena on/off, graph on/off) in the SHIPPED `loop.js`, executed by the test with each combination, and the existing behaviour is unchanged for the no-tool case (bounded search) and the serena case
    - with the graph on, the block teaches `graphify explain "<symbol>"` and `graphify path "<A>" "<B>"` with a one-line statement of what each returns, and does NOT teach `graphify query` with a natural-language question (D3)
    - the Map scout's prompt is fixed the same way and covered by the same test — it is the single heaviest graph consumer in the loop and it currently asks `query` three ways
    - a test fails if `graphify query` reappears anywhere in an agent-facing prompt in `loop.js`, so this cannot silently regress
    - `node --check plugins/rein/workflows/loop.js` passes, and `python3 -m unittest discover -s tests -q` stays green
