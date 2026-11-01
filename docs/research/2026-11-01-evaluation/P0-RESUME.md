# Resume only the blocked P0 cross-provider checks

Status remains PARTIALLY ACCEPTED. Do not repeat passed Codex discovery, Repo,
packaging or Codex-origin Ship checks. This procedure is not a scheduled task.

1. Resolve the current Orca CLI using the orca-cli skill, load its installed guide,
   then read `orca account list --json`. Retain only provider status, window usage,
   updatedAt and resets. Confirm Claude and Codex are not limited, have sufficiently
   recent supported observations, and remain below the project's existing brake.
   Missing windows are unknown. A reset date or plan name is not proof of capacity.
   If unavailable/stale/limited, record BLOCKED and stop. Do not change brakes,
   reset credits, accounts or provider settings to finish an acceptance check.
2. Create a NEW disposable local repository with no remote, a tiny behavior and
   runnable test, flow.config.json with one-worker limits, and explicit scope in
   AGENTS.md/CLAUDE.md. Register it in Orca. Install/link the shared skills only
   inside the fixture. Keep the code and plan independent of production repos.
3. Start Claude through `orca agent launch --agent claude --worktree path:<fixture>`.
   Use its installed plugin discovery route (local `--plugin-dir` setup if needed;
   never globally install). In that initiating session, select Discover on a small
   concrete request, then Plan; record selected skill, references and read-only
   behavior. These Claude-origin checks have NOT yet passed.
4. From the Claude initiating session explicitly request Quick using Codex. The
   installed CLI shape is:
   `python3 <plugin>/bin/rein quick "<fixture task>" --repo <registered-name> --agent codex --model gpt-6.1-sol`.
   Verify exactly one worker, requested provider/model, actual acceptance results,
   independent review and watcher/question delivery. Record live receipt IDs, not
   just accepted input or correct JSON. Do not self-approve a code review.
5. In a fresh fixture, reverse only the missing direction: Codex initiating session
   invokes Quick with `--agent claude --model sonnet` (or the user's named model).
   Query capacity again at this execution event. Do not replay the already-passed
   full Codex Ship flow. If a real gate occurs, inspect it and answer only its
   specific authorized fixture decision. A technical review remains independent.
6. Mark each direction PASS only after observed completion and checks. Preserve
   FAIL/BLOCKED outcomes and logs; stop on quota brake, external side effect,
   ambiguous launch or scope violation. No blind resend, merge, push or cleanup.

Keep separate results for native selection, initiating provider, delegated
provider/model, watcher/answer, independent review and state-bound approval.
A contract test or Claude manifest validation is not substitute live evidence.
