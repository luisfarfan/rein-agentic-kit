---
name: rein-audit
description: "Independent review of a completed change — the third role, distinct from planner and implementer. Runs the mechanical checks, delegates the five-axis judgement to the code-reviewer, and records a state-bound verdict. Never implements. Use when the change touches permissions, security, concurrency or durable state, or contracts between services, or when the user asks to review a change or audit a branch before merge, or invokes /rein:rein-audit."
license: MIT
---

# /rein:rein-audit

Thin orchestration for the **reviewer** role: *no agent approves its own implementation.* It
does not reimplement the mechanical checks or the five-axis judgement — it **composes** both
and records a registered verdict.

Usage: `/rein:rein-audit [change-name]`

## Steps

1. Load the plan (`rein tasks <root> --change <name>`) and the real diff:
   `git diff <base>...<branch>`.
2. **Mechanical part.** Run `rein gate <worktree>` — under `rein ship`, the exact command your
   brief gives for each worktree — never the raw test, lint or typecheck commands, and never
   with a shorter `--timeout` (without one it uses the project's). A check that already passed
   on this exact state is reused and printed `cached`, so this costs nothing after Rein's own
   gate. A red slot is not a finding by itself. Run each failing test alone: one your change
   causes is a `BLOCKING` finding with that test as its reproduction (and the verdict cannot be
   `APPROVED`); one that passes alone, or fails the same way on the base, is the environment or
   a flaky suite — say so in your report, never as a finding. A `SETUP` result is not a
   verdict on the code: say so rather than calling it red. If a slot is not configured, say it
   is absent rather than substituting one.
3. **Judgement — five axes.** Delegate to the `agent-skills:code-reviewer` agent over the
   diff: correctness, readability, architecture, security, performance. If that agent is not
   available (Codex, Antigravity, or a Claude without its plugin), review the
   five axes yourself and never ask anyone for a reviewer. Look at the change **as a whole** — coherence defects *between* tasks are the
   ones nobody else will see. **Always check, and say for each "checked" or "not applicable":**
   permissions and scopes (who can see or do what, tenant boundaries), security, concurrency and
   idempotency, contracts between services or repos. A plan *Decision* is not exempt: one that
   grants more access than the criteria need is a finding.
4. **Intent.** If the plan has a `Why`, judge the work against it — not only against
   the criteria. Criteria can all pass while the change does not solve the stated
   problem. If it has `Scope: Out`, work that strayed there is a finding **even if it
   is good work**: it was excluded on purpose.

5. **Coverage, against the guarantee and not the wording.** For each task, check its
   acceptance criteria are genuinely met. Then specifically hunt the failure this flow keeps
   producing: **a criterion satisfied in letter by a test whose fixture avoids the case it
   claims to cover.** Try to construct an input where the stated guarantee still fails. If
   you can, the criterion is not met, whatever the suite says.
6. **Declare what you reviewed.** List the files actually inspected for this verdict —
   implementation, tests, and relevant docs. This is a deliberate declaration, not an
   automatic inference: it is what the verdict is bound to.
7. **Record the verdict:**

   ```bash
   rein review record --change <name> --verdict APPROVED|CHANGES_REQUESTED \
     --files <comma-separated> --reviewer <who-you-are> \
     --findings "BLOCKING: ...|IMPORTANT: ...|SUGGESTION: ..."
   ```

   Every finding must be prefixed with one of the three severities. A `BLOCKING` is a
   **confirmed** defect, written `BLOCKING: <file:line> expected: ... observed: ... evidence: ...
   breaks: ...`: the code (in a file you reviewed), the behaviour expected and observed, the
   evidence (a test or command you ran, or for a race the interleaving that breaks it -- not every
   defect needs a new test) and the criterion, contract or invariant it breaks. `rein review
   record` refuses a BLOCKING missing any of them. A suspicion you could not confirm is
   `IMPORTANT`: it costs no fix round. `BLOCKING` (must fix,
   costs a round), `IMPORTANT` (should fix, does not by itself block `APPROVED`), or
   `SUGGESTION` (never costs a round). `CHANGES_REQUESTED` requires at least one `BLOCKING`
   finding; `APPROVED` tolerates none (D2) — a verdict that violates this is refused, not
   recorded. It also refuses an empty file list, a missing reviewer, and `implementer` as the
   actor. It stores a content hash per file plus a state hash over the set.
8. Report the verdict and, if `CHANGES_REQUESTED`, the findings the implementer needs. Make
   them precise enough to act on without asking you anything: file, what is wrong, what is
   missing. **Stop.**

## Approval is bound to a state, not to a moment

`rein review check --change <name>` re-hashes the declared files and fails if any changed:
an `APPROVED` verdict is valid **only for the exact code reviewed**. If anything is edited
afterwards the review is stale and must be re-run. Run it before merging or archiving.

This is also why you must not modify product code during a review — you would make your own
verdict stale.

## What this skill does NOT do

- Does not fix its own findings. `CHANGES_REQUESTED` → the implementer addresses them →
  a **new** `/rein:rein-audit` invocation re-reviews.
- Does not tick checkboxes, merge, or archive. It produces the evidence the merge gate needs.
- Does not reimplement the mechanical checks or the five-axis analysis inline.

## Bounded re-review loop

`CHANGES_REQUESTED` → findings → fix → new invocation, a new episode, a new verdict.
**Max 2 rounds** (one fix round, then one final audit); `rein ship` owns the count, or the
user when auditing by hand. At the limit without `APPROVED`, **do not simulate approval** — escalate to a human decision, and say plainly what
is unresolved.

If the only thing blocking approval is a judgement solely the user can give, do not spend a
round on it: report it as needing their decision, naming which task and what they must judge.

## Limits (per invocation)

- Exactly one change reviewed; one verdict; one episode.
- Never record `APPROVED` because the mechanical part passed alone — step 2 is mandatory even
  when everything is green.
- Never report a gate you did not actually run. Be demanding: approving something broken is
  worse than asking for another round.
