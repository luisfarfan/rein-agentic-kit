# Change: doctor-knows-it-is-stale

## Why
A project that installs this kit has no way to learn that a newer version
exists. Verified on this machine, which is the author's own:

```
installed  (installed_plugins.json)                     0.4.0
available  (marketplaces/rein-agentic-kit/…json)        0.4.0
repository                                              0.6.2
```

Two versions behind on the installed plugin, and the local marketplace clone
is stale too — so even `claude plugin update rein` would have installed 0.4.0
again. The author of the kit did not notice for eight changes; nobody else
stands a better chance.

The mechanism to fix it already exists and needs no network. Both facts are
files on disk that Claude Code itself maintains:

- `~/.claude/plugins/installed_plugins.json` → what is installed, per scope
- `~/.claude/plugins/marketplaces/<name>/.claude-plugin/marketplace.json` →
  what that marketplace offers, from a git clone Claude Code refreshes

Comparing them is a string comparison over two JSON reads. `rein doctor` is
already the command that says "here is what I detected"; it is the honest
place to also say "and you are running an old me".

## Scope
- In: `rein doctor` reporting the installed version against the available
  one, and printing the exact commands when they differ
- In: the stale-marketplace case, because it is the one that actually
  happened: the clone said 0.4.0 while the repository was at 0.6.2, so
  updating the plugin alone would have changed nothing
- Out: any network call. `setup.py` states the kit adds none, and a version
  check that phones home would be the first — the data is already local
- Out: auto-updating anything. Reporting is the job; installing is the
  operator's decision, exactly as `--install` is opt-in
- Out: notifying from inside the loop or the skills. `doctor` is where a
  person looks; adding a nag to every agent prompt is prompt surface for
  something a human reads once

## Decisions
- D1 Local files only. Both facts are on disk; a version check is not a reason to make this kit's first outbound request
- D2 Report, never act. `doctor` prints the two commands and returns; nothing is installed, updated or written
- D3 Unknown is not stale. A missing file, an unreadable one, a plugin installed from a path rather than a marketplace — each reports "cannot tell" with the reason, never a false "up to date" and never a false alarm
- D4 Never a failure. `doctor`'s exit code keeps meaning what it means today; being out of date is information, not a broken environment

---

- [ ] T001 doctor says when it is out of date, and how to fix it
  - Type: implementation
  - Depends on: none
  - Human review: false
  - Verification: `python3 -m unittest tests.test_version_staleness`
  - Acceptance:
    - a pure function takes the two parsed JSON documents and returns one of `up-to-date`, `stale`, or `unknown` with a reason, comparing the running plugin's version against the version its marketplace offers; a test drives every branch from fixtures, including equal, newer-available, and newer-installed (which is `unknown`, not `stale` — that is a developer running from a checkout)
    - `rein doctor` prints the verdict, and when stale prints BOTH commands in order — refreshing the marketplace before updating the plugin — because the measured failure was a stale clone that made the update a no-op
    - the marketplace clone being older than the plugin's own repository is reported as its own case with its own fix, since that is the state this machine was actually in
    - every unreadable, missing or unexpected-shape input yields `unknown` with the reason named, and a test covers a missing file, malformed JSON, and a plugin absent from the installed list (D3)
    - the check makes no network call and writes nothing: a test asserts the function touches only paths under `~/.claude/plugins` and that `doctor`'s exit code is unchanged whether the verdict is stale or not (D1/D2/D4)
    - `rein doctor --json` gains the verdict alongside its existing keys, and a test pins the keys it had before so nothing that reads it breaks
