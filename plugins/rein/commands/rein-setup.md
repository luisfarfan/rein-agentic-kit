---
description: "Provision this project: probe the retrieval tools, and with --install add what is missing, activate them and index"
allowed-tools: Bash
---

# /rein:rein-setup

Leaves THIS repository ready to work in. Runs from inside Claude Code, so
nothing has to be on your shell's `$PATH` — the plugin's own `bin/` is
resolved here.

Run the probe first. It writes nothing:

```bash
R=$(command -v rein || ls -d ~/.claude/plugins/cache/*/rein/*/bin/rein 2>/dev/null | sort -V | tail -1); "$R" setup .
```

Report what it found: which tools are present, which are missing, and which
are present but **inert** — installed and unusable, which is a different
problem with a different fix.

Then ask the user whether to provision. If they say yes:

```bash
R=$(command -v rein || ls -d ~/.claude/plugins/cache/*/rein/*/bin/rein 2>/dev/null | sort -V | tail -1); "$R" setup . --install
```

That installs what is missing, activates serena for this repo, builds
codegraph's index, disables codegraph's telemetry, and adds the tools' local
state to `.gitignore`. Report each line of its output as it is.

Do not run it with `--install` without asking — it writes to the repository
and installs software on the machine. A bare `/rein:rein-setup` changes nothing,
which is the point.
