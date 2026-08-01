---
name: audit
description: "Independent review of a completed change — the third role, distinct from planner and implementer. Runs the mechanical checks, delegates the five-axis judgement to the code-reviewer, and records a state-bound verdict. Never implements. Use when the user asks to review a change or audit a branch before merge, or invokes /rein:audit."
license: MIT
---

# /rein:audit

Thin orchestration for the **reviewer** role: *no agent approves its own implementation.* It
does not reimplement the mechanical checks or the five-axis judgement — it **composes** both
and records a registered verdict.

Usage: `/rein:audit [change-name]`

```bash
R=$(command -v rein || ls -d ~/.claude/plugins/cache/*/rein/*/bin/rein 2>/dev/null | sort -V | tail -1)
```

## Steps

1. **Record this invocation** — never blocks, never fails the run. Shell state does NOT
   persist between tool calls, so `R` is resolved and used in the SAME block or it is
   empty and nothing is recorded:
   ```bash
   R=$(command -v rein || ls -d ~/.claude/plugins/cache/*/rein/*/bin/rein 2>/dev/null | sort -V | tail -1); "$R" event audit
   ```
2. Load the plan and the real diff of what was implemented: `"$R" tasks .` and
   `git diff <base>...<branch>`.
3. **Mechanical part.** Run the project's configured `test`, `lint` and `typecheck`
   (`"$R" detect .`). If any is red the verdict **cannot** be `APPROVED` however good the
   code reads — record `CHANGES_REQUESTED` with the failing commands as `BLOCKING` findings
   (a red gate is by definition a blocker; see the severity vocabulary in step 8). If a slot
   is not configured, say it is absent rather than substituting one. If `verifyPolicy.mode`
   is `rendered`, a green suite alone is **not** a complete gate — a dedicated Verify-phase
   agent has already served and rendered the page and reports one of two outcomes: **failed**
   (it looked and the render broke — treat this as a `BLOCKING` finding; `APPROVED` cannot
   stand on top of it) or **rendered-unverified** (no browser tool was reachable, so nobody
   looked at all — state this plainly but it does not by itself block `APPROVED`). These are
   different facts and must not be collapsed into each other in the verdict.
4. **Judgement — five axes.** Delegate to the `agent-skills:code-reviewer` agent over the
   diff: correctness, readability, architecture, security, performance. Do not reimplement
   this inline. Look at the change **as a whole** — coherence defects *between* tasks are the
   ones nobody else will see.
5. **Intent.** If the plan has a `Why`, judge the work against it — not only against
   the criteria. Criteria can all pass while the change does not solve the stated
   problem. If it has `Scope: Out`, work that strayed there is a finding **even if it
   is good work**: it was excluded on purpose.

6. **Coverage, against the guarantee and not the wording.** For each task, check its
   acceptance criteria are genuinely met. Then specifically hunt the failure this flow keeps
   producing: **a criterion satisfied in letter by a test whose fixture avoids the case it
   claims to cover.** Try to construct an input where the stated guarantee still fails. If
   you can, the criterion is not met, whatever the suite says.
7. **Declare what you reviewed.** List the files actually inspected for this verdict —
   implementation, tests, and relevant docs. This is a deliberate declaration, not an
   automatic inference: it is what the verdict is bound to.
8. **Record the verdict:**

   ```bash
   "$R" review record --change <name> --verdict APPROVED|CHANGES_REQUESTED \
     --files <comma-separated> --reviewer <who-you-are> \
     --findings "BLOCKING: ...|IMPORTANT: ...|SUGGESTION: ..."
   ```

   Every finding must be prefixed with one of the three severities — `BLOCKING` (must fix,
   costs a round), `IMPORTANT` (should fix, does not by itself block `APPROVED`), or
   `SUGGESTION` (never costs a round). `CHANGES_REQUESTED` requires at least one `BLOCKING`
   finding; `APPROVED` tolerates none (D2) — a verdict that violates this is refused, not
   recorded. It also refuses an empty file list, a missing reviewer, and `implementer` as the
   actor. It stores a content hash per file plus a state hash over the set.
9. Report the verdict and, if `CHANGES_REQUESTED`, the findings the implementer needs. Make
   them precise enough to act on without asking you anything: file, what is wrong, what is
   missing. **Stop.**

## Approval is bound to a state, not to a moment

`rein review check --change <name>` re-hashes the declared files and fails if any changed:
an `APPROVED` verdict is valid **only for the exact code reviewed**. If anything is edited
afterwards the review is stale and must be re-run. Run it before merging or archiving.

This is also why you must not modify product code during a review — you would make your own
verdict stale.

## What this skill does NOT do

- Does not fix its own findings. `CHANGES_REQUESTED` → the implementer runs `/rein:step` to
  address them → a **new** `/rein:audit` invocation re-reviews.
- Does not tick checkboxes, merge, or archive. It produces the evidence the merge gate needs.
- Does not reimplement the mechanical checks or the five-axis analysis inline.

## Bounded re-review loop

`CHANGES_REQUESTED` → findings → fix → new invocation, a new episode, a new verdict.
**Max 3 rounds** by default; whoever orchestrates tracks the count. At the limit without
`APPROVED`, **do not simulate approval** — escalate to a human decision, and say plainly what
is unresolved.

If the only thing blocking approval is a judgement solely the user can give, do not spend a
round on it: report it as needing their decision, naming which task and what they must judge.

## Limits (per invocation)

- Exactly one change reviewed; one verdict; one episode.
- Never record `APPROVED` because the mechanical part passed alone — step 3 is mandatory even
  when everything is green.
- Never report a gate you did not actually run. Be demanding: approving something broken is
  worse than asking for another round.
