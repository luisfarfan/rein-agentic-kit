# Change: one-owner-for-retrieval

## Why
Three tools now answer "who calls X": serena `find_referencing_symbols`,
graphify `explain`, codegraph `callers`. Measured on this repo, same questions,
same index cost (graphify 1.4s/1179 nodes; codegraph 1.5s/1227 nodes; neither
needs an LLM or an API key for code):

| the question an implementer actually asks | graphify | codegraph |
|---|---:|---:|
| "where is it decided that a command is not invocable?" | 546 tok of `flow.config.example.json` — the JSON key `verify` matched | 258 tok: `NOT_INVOCABLE_EXIT_CODES = (126,127)` at `verify.py:71` |
| who calls / is called by `run_one` | 158 tok, both directions, 1 turn | 22 + 75 tok, 2 turns |
| chain between two named symbols | 19 tok | no direct equivalent |
| which tests cover this symbol | no such concept | `⚠️ no covering tests found`, per symbol |

The prompt-surface argument for cutting tools was checked and does NOT hold:
the whole RETRIEVAL block costs 314 tokens with everything on against 149 with
nothing, so ~16.5k over a 100-turn agent — 2% of an 808k run. The cost of a
menu is not bytes, it is turns spent choosing. The only signal we have on that
is D2's control (the agent that used serena most oriented in 29 turns, the one
that used none in 12; n=3, weak, and pointing at choice cost).

So: one owner per question, and a tool with no exclusive question does not
appear in an agent-facing prompt at all. That is the same installed-vs-usable
discipline `setup.py` already enforces, applied to recommendations.

## Scope
- In: codegraph as the single owner of code retrieval, in the worktree where
  agents work; serena's block narrowed to what only it does; graphify removed
  from every agent-facing prompt
- In: the provisioner learns codegraph, its gitignore entry and its telemetry
- Out: any run-level savings claim — this is a per-call result, and D2 is the
  standing record of how that inference failed before
- Out: wiring or measuring serena's editing half; its retrieval overlap is
  removed here, its own value is a separate measurement
- Out: uninstalling graphify — it stays as the `/graphify` skill for non-code
  corpora (docs, papers, images), which is the half only it has
- Out: codegraph's MCP server — 9 tools registered in every session is real
  surface, where the CLI costs nothing

## Decisions
- D1 CLI, never MCP: `codegraph install` registers 9 tools into every session whether or not a run uses them; the CLI is invoked only when an agent chooses to
- D2 One owner per question. codegraph answers "what is this / who touches it / what breaks if I change it"; serena answers "edit this symbol" and "what are the type errors without running a build"; graphify answers nothing inside the loop
- D3 A tool is removed from the prompt for having no EXCLUSIVE question, not for being bad — graphify's `path` is genuinely cheaper, and implementers still ask the concept question far more often than the two-named-symbols question
- D4 `explore` is not taught: at 3,701 tokens it is a whole-file Read in disguise, and the block's entire point is bounded orientation
- D5 Defaults are the operator's, not the vendor's: telemetry is disabled and the index directory is excluded before anything is indexed

---

- [x] T001 codegraph owns retrieval, where the agents work
  - Type: implementation
  - Depends on: none
  - Human review: false
  - Verification: `python3 -m unittest tests.test_graph_index tests.test_graph_retrieval`
  - Acceptance:
    - the Isolate step builds codegraph's index inside the worktree (`codegraph init`, ~1.5s, no LLM) and excludes `.codegraph/` the same shared-`info/exclude` way `graphify-out/` is excluded today, reporting the outcome into `ISOLATE_SCHEMA` as a literal fact; the graphify index build is removed from that prompt
    - the availability decision keeps its current shape and tests — derived from what the Isolate step REPORTED about the worktree, never from `ctx.capabilities` of the base repo (the standing D2 of `graph-reaches-the-agents`) — and now answers for codegraph
    - with the graph on, the RETRIEVAL block teaches `codegraph query "<concept>"` (symbols matching a concept, with file:line), `codegraph callers`/`callees <symbol>`, `codegraph node <symbol>` (source plus callers, instead of reading the file) and `codegraph impact <symbol>` before an edit — each with a one-line statement of what it returns, and NOT `codegraph explore` (D4)
    - serena's part of the block no longer teaches retrieval that codegraph now owns: it keeps `get_diagnostics_for_file` (type errors without a build) and the symbol-level EDIT operations, and a test asserts `find_referencing_symbols` is no longer taught as the way to find callers (D2)
    - no agent-facing prompt in `loop.js` mentions `graphify` in any form — the Map scout included — and a test scanning the shipped source fails if one reappears, the same guard that today catches `graphify query`
    - the pure functions stay extracted from the SHIPPED `loop.js` and EXECUTED (never asserted by source substring), all four serena/graph combinations remain covered, and the no-tool branch is proven byte-identical to today

- [ ] T002 The provisioner recommends what the loop actually uses
  - Type: implementation
  - Depends on: T001
  - Human review: false
  - Verification: `python3 -m unittest tests.test_setup`
  - Acceptance:
    - `setup.py` gains a `codegraph` entry: probed by binary, installable with `npm i -g @colbymchenry/codegraph` (needing `npm`), index marker `.codegraph/`, gitignore entry `.codegraph/`, and a `why` that states its exclusive question rather than a generic benefit — the existing test that every tool states one must pass unchanged
    - installed-but-no-index is reported `inert` exactly as graphify is today, with its own one-command fix named; a test covers indexed and not-indexed
    - `--install` disables telemetry as part of activation (`codegraph telemetry off`) and reports that it did — it is on by default, and a provisioner that silently accepts a vendor default is not provisioning (D5)
    - graphify's entry is re-scoped in `why` and `manual` to non-code corpora, and no longer claims a role in the loop's retrieval; nothing about it is uninstalled or removed from `TOOLS`
    - `rein doctor` and `rein setup` render codegraph alongside the others with no change to their output contract, and `python3 -m unittest discover -s tests -q` stays green
