---
name: ship
description: "Codex: $rein:ship. Implement a planned change: opens a detached Orca tab that runs `rein ship CHANGE` (workers, task gates, an audit). Use only when orchestration pays (parallel tasks, several repos, dependencies to recover, a long run needing saved state), not for a sequential plan one session can do; when the user says ship, lánzalo, implementa el plan, or /ship."
license: MIT
---

Read [provider setup](PROVIDERS.md) once when using REIN in this session.

# /ship

Starts `rein ship` in its own Orca tab (`/rein:ship` when namespaced); keeps supervision here.
**Never edits code, pushes, merges or opens a PR.**

Usage: `/ship <change> [--root <plan repo>]`. "con codex" → `--agent codex`;
"con antigravity" → `--agent antigravity`; "con sonnet/claude" → `--agent claude`.
Pass `--model` only if named.

## Steps

1. Check `rein tasks <root> --change <change>` (default root `.`).
   No plan: stop, suggest `/rein:rein-plan`.
2. Run `rein ship <change> --root <root> --detach` with the mapped flags. On refusal,
   show the output; never retry, never run the loop in this session. Only the owner may
   accept a temporary plan root: then add `--allow-temp-root`.
3. Immediately run `rein watch <change> --root <root> --timeout 60`. **Keep this agent turn active.**
   A BACKGROUND process cannot resume an ended turn. Retain and collect its handle until exit:
   Codex `session_id` → `write_stdin`; yielded exec cell → `functions.wait`.
   Never duplicate a running watcher. Details: `<plugin-root>/SHIP-WATCH.md`.
4. Handle the complete output:
   - **WAITING**: repeat step 3 immediately. Never finish with “monitor active”. Only a verified
     host wake mechanism permits yielding supervision; a process id or UI event does not.
   - **QUESTION** / **PENDING** (handle ALL listed gates, one by one): read each question in
     full; inspect relevant evidence read-only. Explain the decision gate, options and recommendation.
     Wait for the owner; never invent approval. Run
     `rein answer <change> "<text>" --gate <id> --root <root>` (listed options only).
     On exit 2, re-show options; on stale/refused answers, re-read state. Restart the watcher
     after answering; use `--skip <ids>` for questions awaiting/deferred by the owner.
     They can return with "¿qué me pregunta rein?". Keep collecting other events.
   - **FINISHED**: summarise tasks, audit result and where the work is. An audit other than
     `approved` comes first. Offer pushing to develop only with the owner's yes (API before
     admin, respecting hooks), then `rein done <change> --root <root>` (`--dry-run` previews).
     Ask separately about publishing the archive: `rein done <change> --root <root> --push`.
     It never pushes main/master/qa; show the printed command. Show blockers and stop.
     Listed gitignored losses need explicit acceptance before `--yes`. Do not restart the watcher.
   - **STOPPED**: tell the owner and offer to resume it in the Orca tab "rein ship <change>";
     restart the watcher after.
