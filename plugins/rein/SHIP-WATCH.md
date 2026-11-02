# Ship supervision from Claude Code or Codex

The driver owns workers, gates and retries. `rein watch` only observes that run;
it never consumes worker inbox deliveries, answers decisions or starts workers.
Its existing deterministic loop checks every 15 seconds. No additional scheduler,
LLM polling job or daemon is needed to keep the supervising **turn** attentive.

## Active collection

After `rein ship <change> --root <root> --detach`, run:

```sh
rein watch <change> --root <root> --timeout 60
```

`--timeout` is a polling window (positive seconds, maximum 300). At its end the
command prints `WAITING` with the observed phase and exits 0. A question or final
state seen in that poll takes precedence. If the driver cannot be found, finish
the existing liveness grace before returning a checkpoint: renewing must not reset
that grace indefinitely. This can extend the window until the driver returns or
`STOPPED` is confirmed (startup: up to eight polls). Existing in-flight Orca/process
probes keep their subprocess timeouts; this is not a hard deadline for a hung RPC.
`--once` remains a single poll with its existing exit codes and cannot be combined
with `--timeout`. Without either option, the legacy indefinite wait is unchanged.

The supervising agent must collect every result and renew WAITING windows in the
same turn. Do not send a final “monitor active” message while a process result is
still uncollected or the run still needs supervision. A queued background command
and its eventual UI completion event are not proof that the host will resume the
agent after its turn ends. This was the observed failure: QUESTION was present in
the finished process, but only a later user message caused the principal to read it.

For **Codex**, `exec_command` can yield a `session_id`. Retain that exact ID and
call `write_stdin` with empty input until the process exits. If a `functions.exec`
wrapper itself yields a cell ID, first resume that cell using `functions.wait`;
do not issue concurrent reads against the same process or launch another watcher.
Use bounded host waits (up to 60 seconds) and continue after a yield. Then handle
the complete output, including multiple gate IDs, before starting the next window.

For **Claude Code**, use the available Bash execution and task-output/wait tools
to collect the same command through completion. A background task notification can
be useful while the turn remains active; do not assume it provides post-turn wake.
No particular Claude tool name is guaranteed by REIN: use the tools actually present.

An independently verified host-native wake facility may replace active collection
only if it demonstrably resumes this exact supervising conversation on completion
and delivers the output. Establish it before yielding. If the host cannot sustain
collection or verified wake, report that supervision is unavailable; preserve the
run and its questions, provide `rein answer <change> --root <root>`, and do not claim
unattended monitoring. REIN does not install an automation or inject terminal input.

## Decisions and interruptions

- `QUESTION` / `PENDING`: inspect every listed gate, explain the decision and relay
  the owner's actual answer through `rein answer ... --gate <id>`. Never infer an
  approval from silence, timeout, a worker's final prose or a missing gate.
- While awaiting an owner answer, use the host's user-input facility. Keep other
  questions observable with `--skip <ids>` for the gates already presented; those
  IDs remain unanswered. Do not keep asking the same question on every window.
- An answer refusal or vanished gate requires re-reading current state. The worker
  may have continued or its attempt may have changed. Never blindly forward an old
  answer to whichever worker happens to be active next.
- `WAITING`: renew immediately; do not ask the user whether the workers responded.
  Unknown phase after an Orca error is uncertainty, not evidence that nothing is pending.
- `FINISHED`: report tasks and audit honestly, then end supervision. It does not
  authorize pushing or cleanup. `STOPPED`: explain the stopped driver and obtain
  the owner's resume decision; never start a duplicate driver automatically.

After interruption or session resumption, collect the retained watcher if that
host process still exists. If its handle is no longer usable, run a fresh watch
for the **same root and change**, without remembered skip IDs unless the owner
still defers them. Initial polls replay all current, unqueued pending gates.
This recovery is read-only; do not restart Ship, resolve a gate or consume its
coordinator inbox to recover attention.
