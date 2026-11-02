# Close native work from Claude Code or Codex

Native execution needs no Quick or Ship run. Use the same CLI from either provider:

```sh
rein done my-change --root /absolute/plan-repo --native /absolute/closure.json --dry-run
rein done my-change --root /absolute/plan-repo --native /absolute/closure.json
```

Use `python3 <plugin-root>/bin/rein` if PATH points at an older installation. Run
from outside every target worktree. `--root` is the durable plan/evidence repository,
also outside the targets; it need not contain an OpenSpec plan. Do not manufacture
`.rein/ship` state, launch workers retrospectively, or mark an Orca card completed
as a substitute for closure.

## Explicit inputs

Copy the exact repo `id`, main checkout `path`, worktree `path` and branch from
`orca repo list --json` and `orca worktree list --json`. Names, issue prefixes and
similar branch names never establish membership. All paths below are absolute;
`repo_path` and `path` must be canonical (resolve symlinks with `realpath`).
`repo` is the **Orca repo ID**, not its display name. Integration is an explicit
`origin/<branch>` per worktree, with no default or fallback.
For cleanup, `{repo}` uses the same main-checkout directory name as an ordinary
native `rein gate`. If that passing gate used `--repo <name>`, add
`"gate_repo": "<same name>"` to its worktree entry. This resource name never selects
membership; `repo` remains the exact Orca ID.

```json
{
  "schema": 1,
  "change": "my-change",
  "execution": "finished",
  "worktrees": [
    {
      "repo": "exact-orca-repo-id",
      "repo_path": "/workspace/api",
      "path": "/worktrees/api-change",
      "branch": "codex/my-change",
      "integration": "origin/develop",
      "head": "<full validated commit SHA>",
      "review_root": "/workspace/plans",
      "validation": [
        {
          "path": "/workspace/plans/evidence/api-gate-and-qa.txt",
          "sha256": "<SHA-256 of the existing evidence file>",
          "head": "<same full validated commit SHA>",
          "result": "passed"
        }
      ]
    }
  ]
}
```

For multiple repos, append an entry for **each** worktree, with its own ID, main
checkout, branch, integration target, validated commit and evidence. Multiple
worktrees in one repo are supported too. Keep the manifest and validation artifacts
outside **all** removal targets so retries can still read them.

The manifest explicitly attests which existing passing gate/functional evidence
applies to each commit. Reference the actual reports, not an empty placeholder or
a newly invented pass. REIN checks artifact hashes, declared passing outcomes and
the exact HEAD; it does not interpret arbitrary report prose or independently
certify that assertion. Produce hashes with `shasum -a 256 <report>` and get HEAD
with `git -C <worktree> rev-parse HEAD`. Do not relabel pre-commit or pre-edit QA as
evidence for a later commit: recover the actual state-bound report or perform only
the verification missing for the final code. Closure never runs QA or test suites.

`review_root` defaults to the worktree. It selects the existing
`rein review check --root <root> --change my-change` episode, which must be APPROVED,
current, and cover files in that worktree. A shared multirepo review can live in
the plan repo with explicit file paths into all worktrees. Reuse the actual
independent review; recording a new approval is the reviewer's action, never the
implementer's. If a historical review was never registered, the independent
reviewer can register the files they actually reviewed with `rein review record`;
closure must not invent that episode. A changed reviewed file or a newer rejected
review blocks closure.

## Four separate facts

- **Validated:** the exact HEAD has declared passing evidence and a current
  independent registered approval. An Orca `workspaceStatus=completed` proves none
  of this. Ship's `phase=done` means execution ended; an explicit
  `audit_result=unapproved`, `rejected` or `skipped` blocks its closure. Older Ship
  states without an audit field retain compatibility and report approval unknown.
- **Integrated:** after fetching the explicit origin branch, existing
  `check_worktree` / `integration_gap` checks prove that neither HEAD nor the
  branch Orca removes contains work absent from that branch. Squash/rebase/content
  checks are shared with Ship/Quick. There is no merge or push.
- **Execution finished:** the manifest says `finished`, and Orca confirms no
  terminal or agent sessions, including structured chats, and no live or
  unverifiable PTYs. Finish and close those sessions yourself first; even idle
  tabs conservatively block. Incomplete/unsupported Orca session inventories fail
  closed. This path currently requires local paths and POSIX file locking.
- **Cleanup completed:** Orca removed the target and its configured `gate.cleanup`
  succeeded, or there was no cleanup command. Only then does the journal mark it
  cleaned. Configured cleanup commands must be safe to repeat after interruption,
  e.g. `dropdb --if-exists ...`; exactly-once shell effects cannot be guaranteed.

## Safety, preview and recovery

All targets are preflighted before any removal. Main clones, wrong ownership or
branches, unintegrated work, dirty files, missing targets without a recorded removal,
and active sessions block the attempt. Existing gitignored-file protection applies:
the preview lists losses, except existing regenerable/junk exceptions. Execution
needs `--yes` to accept those listed losses; it waives no other check. The dry-run
shows removals, branch names, cleanup commands and any local plan archive, without
removing, cleaning, committing or writing a journal. It does fetch remote refs to
prove integration, like existing closure commands.

The durable `.rein/done/<change>.json` journal belongs to closure, **not Ship**.
It captures the exact manifest, review hashes, cleanup command/environment and
per-target progress before removing anything. Preserve it and the original
manifest on failure. Fix the reported cause and repeat the **same command**:
already removed targets are not removed again, pending cleanup resumes without
their directory, and successful cleanup is skipped. Changed manifest inputs,
recreated removed paths and changed surviving evidence/review files block a retry.
Removal intent is atomically replaced and fsynced, along with its parent directories,
before calling Orca. Concurrent native closures sharing a root or repo are serialized/refused with
filesystem locks. Orca removal is never forced. Other sessions can still change
Git state, so checks run again immediately before each removal; a later failure
may leave earlier targets removed and is recovered through the journal.

If a tracked, clean OpenSpec change folder exists, the existing archive operation
moves it and creates a **local** commit after cleanup; other staged files stay out
of that commit. A failure keeps the journal pending. A crash after the exact staged
archive move or its exact local commit can be recovered from recorded parent/tree
identity; the preview reports that recovery without changing the index. Ambiguous
changes are preserved and require explicit plan/index recovery, as printed.
Native closure refuses
`--push`; review and publish that archive separately. No plan folder means no
archive commit. The completed journal remains available, and repeating the command
reports already closed. Do not delete the journal to bypass a blocker.

Cleanup settings can contain sensitive values: the atomic journal is owner-only
on POSIX. Keep `.rein/done/` as local operational state, out of commits. Never
rewrite a Ship or Quick record as a native manifest; existing `rein done` and
`rein quick close` keep their own entry points.

Exit codes: `0` closed/already closed or successful preview; `1` blocker;
`2` CLI usage; `126` Orca/filesystem failure or pending cleanup. Read the output:
on a partial failure it names the stage that still needs recovery.
