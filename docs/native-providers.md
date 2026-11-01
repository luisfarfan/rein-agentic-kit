# Use REIN from Claude Code or Codex

The seven canonical skills are in `plugins/rein/skills`. Both manifests use that
same directory; `.agents/skills` contains relative directory symlinks for Codex
in this checkout. No installation into your home directory is needed.

## Starting an ordinary task in Orca

Open the project's actual checkout/worktree in Orca, start your usual Claude or
Codex session, and describe the task. The current agent inspects the relevant path
and normally implements natively. It deepens unresolved questions with Discover,
reusing what it already inspected; planning, independent audit and delegation are
selected for their actual need. `rein route` and JSON are optional. There is no
`rein-impact` skill or mandatory sequence of commands.

This behavior needs **one-time project adoption**, not a special prompt on every
task. Installing the plugin alone exposes capabilities; it does not install a
project entry policy. Adopt the packaged [ENTRY.md](../plugins/rein/ENTRY.md) as a
block in the project's existing **AGENTS.md**, preserving its own instructions.
Keep just one copy there. For Claude, add `@AGENTS.md` on its own line in a
neighboring **CLAUDE.md**, preserving any Claude-specific content. This checkout
uses that exact arrangement; the shared block is checked against the template.

Use the template shipped with the installed plugin, or retrieve it from a local
wheel environment without touching global configuration:

```sh
.venv/bin/python -c 'from importlib.resources import files; print(files("rein_kit").joinpath("ENTRY.md").read_text())'
```

Merge the printed block once; do not overwrite an existing AGENTS.md or repeatedly
append it. On update, review the template diff as a project instruction change.
No installer, hook or daemon writes these files. Teams can commit the adopted
instructions so normal checkouts/worktrees inherit them. Do not put only an
`@ENTRY.md` reference in AGENTS.md and assume Codex expands it.

The policy presumes the existing skills are discoverable through a supported local
plugin installation or the repository links described below. Load only a selected
skill's body/provider guide; never import every SKILL.md or IMPACT.md into startup
instructions. If a required capability is unavailable, report that limitation;
do not invent an execution, waive review or bypass quota controls.

## What each provider loads

| Provider | Startup body in this setup | Later, only if useful |
| --- | --- | --- |
| Codex | Project AGENTS.md, including the adopted 257-word block, under native precedence/size limits | Selected skill and its resources; optional structured checker/router |
| Claude Code | CLAUDE.md importing that same AGENTS.md; no duplicated policy body | Selected skill and its resources; optional structured checker/router |

Codex documents project-root-to-cwd discovery, overrides and a combined instruction
size cap; start a fresh session after adoption and verify the intended cwd.
[Official Codex instructions documentation](https://learn.chatgpt.com/docs/agent-configuration/agents-md).

Claude documents local imports and direct AGENTS.md support with version/settings
conditions. Keeping the import works when that direct support is unavailable.
Check `/memory` or `/context` in a new Claude session to inspect loaded files.
[Official Claude memory documentation](https://code.claude.com/docs/en/memory).

A CLAUDE.md placed inside the plugin is not startup context, so REIN does not ship
one there. [Claude plugin reference](https://code.claude.com/docs/en/plugins-reference).
Native instruction discovery is not guaranteed compliance: overrides, exclusions,
limits, project trust and model behavior can affect it. No hook intercepts every
request. Plugin-only installations without project adoption retain ordinary native
behavior and explicit skills; automatic REIN entry is **not** claimed for them.

## Local checkout

Launch Codex in the REIN checkout and describe the task. Explicit commands remain
available when wanted: `$rein:rein-discover`,
`$rein:rein-plan`, `$rein:quick`, `$rein:ship`, `$rein:rein-audit`, `$rein:answer`,
or `$rein:rein-role`. Codex 0.166.0 reports the plugin-qualified names even through
repository symlinks. Standalone installation without plugin metadata can expose
unqualified names; use the skill picker to resolve ambiguity.

For Claude, launch `claude --plugin-dir /absolute/path/to/rein/plugins/rein` and
use `/rein:rein-discover`, `/rein:rein-plan`, `/rein:quick`, `/rein:ship`, etc.
The existing Claude marketplace installation remains supported.

Both providers can execute the exact source CLI without installation:

```sh
python3 /absolute/path/to/rein/plugins/rein/bin/rein context .
```

For another project, install the plugin through your client's local plugin UI,
or explicitly link its skill folders into that project's `.agents/skills`.
Keep the source checkout available: symlinks break if it moves or is removed.
Do not install both discovery routes at once if your client displays duplicates.
The plugin does not edit the target's AGENTS.md or CLAUDE.md. Adopt the small entry
block once as described above; this checkout's CLAUDE.md imports AGENTS.md.

For an isolated CLI installation, create a virtual environment and install the
local checkout there (`python3 -m venv .venv`, `.venv/bin/pip install .`). The
wheel includes both manifests, the canonical skills and the provider guide.

## Validation

```sh
python3 -m unittest tests.test_native_skills tests.test_skill_budget tests.test_version_lock -q
python3 tests/probe_native_skills.py
claude plugin validate plugins/rein --json
python3 -m unittest tests.test_packaging -q
```

The packaging tests require the optional developer `build` package and create a
scratch venv; they are skipped without it. Discovery smoke requires Codex on PATH
and uses a temporary Codex configuration directory. It performs no model turn.

Manual acceptance still required for both real initiating sessions in Orca:

1. Select Discover; verify it investigates a scratch repo and does not implement.
2. Select Plan; verify bounded task criteria and the existing plan-check contract.
3. In a scratch repo with a valid gate and sufficient quotas, explicitly request
   Quick with the other provider. Confirm one worker and the intended model.
4. Exercise Ship's detached watcher and answer a real decision gate; resume once.
5. Audit from an independent session and verify the verdict becomes stale after
   editing a reviewed file. Never self-approve to complete the smoke test.

Static validation and discovery do not establish behavioral correctness of a
model session or live Orca delegation. Record each separately. Do not override
quota brakes just to finish this checklist.

On 2026-11-01, the real Codex initiating session completed Discover, Plan, Ship,
Answer and watcher delivery in an isolated Orca fixture. A separate Antigravity
auditor approved the result, and editing a reviewed file invalidated that verdict.
Claude remained above the quota brake, so Claude initiation and explicit
Claude↔Codex Quick are still pending. See the
[live evidence and fixes](research/2026-11-01-evolution/P0-LIVE.md).

The follow-up [acceptance matrix](research/2026-11-01-evolution/ACCEPTANCE.md)
records fresh Codex-generated `Repo:` and installed resource checks as PASS, while
keeping P0 **PARTIALLY ACCEPTED**. Discover now supports bounded natural-language
investigation and optional v2 evidence reports; `discover-check` checks cited file
freshness, and `route` produces combined advice without starting execution. See
the [P1/P2 results](research/2026-11-01-evolution/P1-P2-RESULTS.md) for live examples,
automated contracts and consumption limitations.

The [entry-policy validation](research/2026-11-01-entry/RESULTS.md) uses the actual
installed wheel and unchanged adoption template. It demonstrates native execution
and selective deepening in bounded Codex cases; it does not establish universal
compliance, token savings or Claude behavioral acceptance.
