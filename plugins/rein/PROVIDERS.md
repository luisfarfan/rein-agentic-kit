# Shared provider contract

The current session may be Claude Code or Codex. The provider does not change
scope, gates, guard checks, review independence, or authorization.

- Claude plugin invocation: `/rein:<name>`. Codex plugin: `$rein:<name>`; standalone skill: `$<name>` (select the REIN
  skill if another plugin uses the same name). Cross-references using Claude
  slash notation mean the skill with that name on Codex, not a shell command.
- The plugin root is the first ancestor of the real SKILL.md path containing
  `bin/rein`. This also works when a wheel materializes resource symlinks. Use
  `python3 <plugin-root>/bin/rein` for every `rein` command below if the shell's
  `rein --version` or `rein doctor . --json` identifies a different installation.
  No global installation or Claude-specific environment variable is required.
- Read the target repository's AGENTS.md / CLAUDE.md under the host's normal
  precedence. This plugin does not install or replace project instructions. The
  packaged ENTRY.md is a small, optional project-adoption template; plugin
  installation alone does not activate it. Keep one body in AGENTS.md, imported
  by CLAUDE.md when needed; do not import all skills or IMPACT.md at startup.
- Ordinary implementation stays in the current session when sufficient.
  Quick and Ship explicitly delegate through Orca; either initiating provider
  can pass `--agent claude` or `--agent codex`, and an explicit `--model`.
- Close native work with `rein done <change> --root <plan repo> --native <manifest.json>`:
  preview with `--dry-run`, then execute the same explicit inputs. Read
  `<plugin-root>/NATIVE-CLOSURE.md` for the manifest and recovery.
  Identify each repo ID, worktree, branch and integration target explicitly; never
  fabricate Ship state. An Orca completed card does not mean cleanup completed.
- Background watchers use the host's asynchronous shell facilities. Retain the
  process handle, collect its output, and relay owner questions. If unavailable,
  explain the limitation and use the existing `rein answer` workflow; do not
  simulate a running watcher or launch a duplicate worker.
- Audit requires a session independent of the implementer. The fallback to
  reviewing the five axes yourself applies to that independent reviewer session.
  No missing Claude reviewer plugin permits self-approval.

Before implementation, reuse or inspect relevant code and impact in this session. For
routing that benefits from a reusable record, optionally use [impact routing](IMPACT.md) and `rein route` to explain the
choice. Keep ordinary work native; an explicit Quick/Ship request selects its
existing delegated contract. Reconsider when evidence changes.
