---
description: "Rein self-check: verify the plugin loaded, its CLI is reachable, and this project's stack resolves"
allowed-tools: Bash
---

# /rein:ping

Phase-0 plumbing probe. Confirms the plugin is wired correctly in **this** project
before anything is asked of it.

Run the environment probe. Which spelling succeeds is itself a diagnostic:

```bash
rein doctor 2>/dev/null || "$(ls -d ~/.claude/plugins/cache/*/rein/*/bin/rein 2>/dev/null | sort -V | tail -1)" doctor
```

The bare `rein` only works once Claude Code has put the plugin's `bin/` on
`$PATH`, which it does **at session start** — so it is expected to fail in the
session where the plugin was installed, and to work in the next one.

Then report, briefly:

1. **Plugin loaded** — you are reading this file, so yes. State the resolved
   `$CLAUDE_PLUGIN_ROOT`.
2. **CLI reachable** — which of the two invocations worked. If the bare `rein`
   failed, say so plainly: it means commands must go through
   `$CLAUDE_PLUGIN_ROOT/bin/rein`, and the user may want a shell alias.
3. **Stack resolution** — stack, subtypes, task runner, and the resolved
   `test` / `testOne` / `lint` / `typecheck` commands with the source of each
   (`flow.config.json` > task runner > autodetect).
4. **Gaps** — any missing command slot, and the one-line fix (add it to
   `flow.config.json`).

Do not explore the repository, do not run the resolved build/test commands, and
do not modify anything. This is a probe.
