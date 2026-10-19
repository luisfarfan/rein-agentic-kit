---
name: quick
description: "Hand a small change to a cheap agent in its own tab, no plan needed. Use when the user says: tarea rápida, arréglalo rápido, cambio rápido, quick task, /quick."
license: MIT
---

# /quick

Starts a small change with `rein quick`; a cheap agent does it, the owner talks to that agent.
Never edits code itself.

## Steps

1. Map the owner's words to flags: a repo name → `--repo <name>`; "en main" → `--base main`;
   "aquí mismo" / "en el checkout" → `--here`; "con codex" → `--agent codex` (`--model` only if they name one); a
   `PPR-…` id → `--linear <id>`.
2. A sentence naming two repos: stop. Suggest `/rein:rein-plan`, then `/ship`.
3. Run once: `rein quick "<sentence>" [flags]`.
4. Report the agent, the workspace and the tab. The owner now talks to that agent there; when
   they say "listo", the agent runs `rein quick done`.
5. Exit 2 (e.g. a dirty checkout with "aquí mismo"): show the message and ask the owner before
   re-running with `--dirty-ok`.
6. Exit 1 or 126: show the output and stop; never retry.
