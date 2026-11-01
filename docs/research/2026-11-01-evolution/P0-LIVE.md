# P0 live Orca acceptance — 2026-11-01

Result: the Codex initiating session completed Discover → Plan → Ship → Answer →
watcher completion, with an independent Antigravity audit. Claude initiation and
explicit Claude↔Codex Quick delegation remain pending quota. P0 is **not fully
accepted**. No push, merge, release, global configuration change or product-repo
edit was performed.

## Capacity evidence

Read `orca account list --json` before launching and after completion. Orca is the
supported quota source; these are cached usage percentages, not token counts.

| Provider | First read, 06:24 UTC | Final read | Final source update |
| --- | --- | --- | --- |
| Claude | weekly 91%, session absent | weekly 91%, session absent | 06:34:26 UTC |
| Codex | weekly 21%, session absent | weekly 25%, session absent | 06:34:26 UTC |
| Antigravity | available to existing audit routing | session 1%, weekly 4% | 06:34:31 UTC |

Claude exceeds the existing 90% brake. No Claude model turn was attempted and no
brake was changed. Its reported weekly reset is 2026-11-02 at approximately 13:00
America/Lima; that is provider metadata, not a promise of future availability.
The final read at 06:36:23 UTC returned the cached update above. The installed account CLI
offers no force-refresh command. Missing session windows remain unknown.

The driver printed zero percentage deltas for its narrower run interval and no
Claude transcripts. Neither establishes zero cost or savings; background usage,
cache age and percentage granularity prevent attribution to this experiment.

## Isolated fixture and receipts

- Source: `.rein/p0-live-_sl9us7c`, a separate Git repository with no remote.
- Orca repo: `p0-live-_sl9us7c`, ID `27b80f21-900e-4c6e-9c71-02fe3772bca4`.
- Initiator: Codex `gpt-6-astra`, high effort, terminal
  `term_b1a76281-e103-4cad-b435-955342467ac0`.
- Driver: terminal `term_cdc691f0-1e7b-409b-8d46-f68e496abbe4`.
- Run: `run_3f7c9c7c6a38`; state:
  `.rein/p0-live-_sl9us7c/.rein/ship/enable-eur.json`.
- Worker checkout: `/Users/luisfarfan/orca/workspaces/p0-live-_sl9us7c/enable-eur`.
- Fixture plan commit: `8e30ab34f015393eb15a71e973baddff754903ae`.
- Fixture implementation commit: `b17b683` (enable EUR while preserving USD).

The fixture supports USD and EUR, initially enabling only USD. T001 enables EUR
and verifies USD, supported currencies and the unsupported GBP case. T002 is an
explicitly artificial operator gate about the fixture's USD/EUR labels. It is not
a product decision or approval of the code. Concurrency was limited to one worker.
The source fixture and Orca checkout remain available for inspection; ephemeral
receipts are excluded from the REIN Git repository by `.rein/p0-live-*`.

## Observed behavior

| Check | Evidence and result |
| --- | --- |
| Discover selected from native Codex catalog | Loaded the shared skill; traced EUR support versus enablement; two baseline tests passed; fixture Git state unchanged. |
| Plan selected from native catalog | Drafted one task with acceptance and runnable verification; presented the draft and waited before writing it. After operator confirmation, wrote the task file and parsed T001. |
| Documented plan-check syntax | Initially failed with `--gate` before the path; after the fix the same syntax returned `decision: continue`. |
| Ship detached launch | Initiator invoked the shared Ship skill and launched the real driver through Orca; retained a background watcher. |
| Implementation | Dispatch `ctx_cb5f2de94cb2`, Codex `gpt-6-astra`; one dispatch, zero retries; task gate and verification exit 0. Diff only in `settings.py` and `test_settings.py`. |
| Owner question | Gate `gate_b82356bb339e`, `human_review`, T002. Watcher delivered the question and the initiating session waited. |
| Answer and resume | Test operator inspected the review sheet and diff, authorized only the artificial labels, then the initiator used `rein:answer` to send `approve` to that exact gate. |
| Full gate | Repository gate passed, exit 0. |
| Independent audit | Separate dispatch `ctx_09fe8afc1718`, Antigravity `gemini-4-argon-medium`, chosen by existing routing. Approved; no implementation retry or audit fix round. This is not a Claude or Codex audit-session test. |
| Completion delivery | Watcher emitted `FINISHED enable-eur: audit approved`; state has phase `done`, T001/T002 done, no pending gates. |
| State-bound verdict | Real recorded review returned exit 0; a temporary comment in reviewed `settings.py` caused exit 1 with `changed since approval: settings.py`; restoring exact bytes returned exit 0. |

The review episode is `.rein/reviews/20261101T063315Z-enable-eur/episode.json`
inside the worker checkout. It records APPROVED, reviewer actor `reviewer`, no
findings, and hashes for both changed files. Independent dispatch identity comes
from the driver's transcript; the generic episode actor alone would not prove it.
No parent/coordinator wrote a substitute approval. The stale-verdict probe restored
the reviewed file immediately and left the worker checkout clean.

OpenSpec's installed strict validator reported “Nothing to validate” in this tiny
fixture (no specification files). The initiator reported that limitation; it was
not counted as a passed spec-validation check.

## Defects exposed and corrected

1. **Resources through external skill symlinks.** `../../PROVIDERS.md` was
   normalized against the receiving project's `.agents/skills`, leading outside
   the plugin. Every skill now links to sibling resource symlinks pointing at the
   shared canonical guides. Wheel data includes the materialized resources. The
   plugin-root instruction finds the ancestor of the real `SKILL.md` containing
   `bin/rein`, which works for source and installed wheels. The live Discover rerun
   read both provider and impact guides successfully.
2. **Boolean argument consumption.** `plan-check --gate <draft>` treated the draft
   as a flag value and lost the positional path. The command now excludes its
   boolean flag before positional parsing. A regression compares both flag orders;
   the initiating session successfully reran the previously failing syntax.
3. **Repo naming guidance.** The model put an absolute path in `Repo:`. Real Ship
   preflight rejected it because the contract expects an Orca registered name.
   Plan now states that requirement explicitly. The test operator corrected the
   draft and added the artificial review gate before the successful live Ship run.
   A fresh model-generated plan after this wording change has not been retested.

## Validation and remaining acceptance

- Focused native-skill, budget and plan-check regression: 81 tests passed, 3.147 s.
- Real wheel build, scratch installation and CLI/package checks: 5 passed, 3.039 s,
  using the temporary developer venv, without global installation.
- Codex app-server discovery: seven skills, zero errors. Claude plugin validation:
  success, exit 0; only the existing non-blocking missing-icon advice.
- Full regression: `python3 -m unittest discover -s tests -q` — 1968 tests,
  196.388 s, OK, skipped=2. Existing round6 ResourceWarnings remain.
- `git diff --check` passed. All fixes and this report are local to
  `codex/native-evolution`; no push was performed.

Still required: a real Claude initiating session with adequate quota, explicit
cross-provider Quick in both directions, and its watcher/answer/review behavior.
This run proves a real Codex-origin Ship path and an independent fallback audit;
it does not establish every host, plugin-install UI, model, or quota-recovery path.
