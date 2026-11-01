# Use REIN from Claude Code or Codex

The seven canonical skills are in `plugins/rein/skills`. Both manifests use that
same directory; `.agents/skills` contains relative directory symlinks for Codex
in this checkout. No installation into your home directory is needed.

## Local checkout

Launch Codex in the REIN checkout. In a new session, select `$rein:rein-discover`,
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
The plugin does not edit the target's AGENTS.md or CLAUDE.md. This checkout's
CLAUDE.md imports AGENTS.md so development instructions have one source.

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
