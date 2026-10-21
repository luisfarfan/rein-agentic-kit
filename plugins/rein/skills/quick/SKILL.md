---
name: quick
description: "Hand a small change to a cheap agent in its own tab, no plan needed. Use when the user says: tarea rápida, arréglalo rápido, cambio rápido, quick task, /quick."
license: MIT
---

# /quick

Starts a small change with `rein quick`; a cheap agent does it and the owner talks to it. Never edits code.

## Steps

1. Map the owner's words to flags: a repo name → `--repo <name>`; "en main" → `--base main`;
   "aquí mismo" / "en el checkout" → `--here`; "con codex" → `--agent codex`, "con antigravity" → `--agent antigravity`, "con sonnet" / "con claude" → `--agent claude` (`--model` only if they name one); a
   `PPR-…` id → `--linear <id>`.
2. A sentence naming two repos: stop. Suggest `/rein:rein-plan`, then `/ship`.
3. Run once: `rein quick "<sentence>" [flags]`.
4. Report the agent, workspace and tab. When the owner says "listo", that agent runs `rein quick done`.
5. Exit 2 (e.g. a dirty checkout with "aquí mismo"): ask the owner before re-running with `--dirty-ok`.
6. Exit 1 or 126: show the output; never retry.
7. After a passing `rein quick done`, once it is on develop ("está done"): offer `rein quick close <workspace>`, run it from outside that worktree on their yes ("ciérrala" is one); show blockers and stop. Add `--yes` for a lost-files WARNING only after the owner's yes.
