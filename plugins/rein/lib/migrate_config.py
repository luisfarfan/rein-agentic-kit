"""migrate-config: bring a v1 flow.config.json up to v2.

v2 drops the `models`, `limits` and `worktree` blocks (Orca owns them) and adds a
`ship` block. `migrate` is pure; the CLI shows a diff and writes only on request.
"""

from __future__ import annotations

import copy
import difflib
import json

REMOVED = ("models", "limits", "worktree")

DEFAULT_SHIP = {
    "claudeWeeklyThreshold": 60,
    "maxConcurrentWorkers": 3,
    "maxRetries": 2,
    "maxAuditRounds": 2,
    "taskTimeoutMinutes": 45,
    "largeChangeFiles": 25,
    "riskyPaths": ["**/auth/**", "**/payments/**", "**/migrations/**"],
}


def migrate(cfg: dict) -> dict:
    """Return a migrated copy. An existing `ship` block is kept exactly as written."""
    out = {k: copy.deepcopy(v) for k, v in cfg.items() if k not in REMOVED}
    if "ship" not in out:
        out["ship"] = copy.deepcopy(DEFAULT_SHIP)
    return out


def render(cfg: dict) -> str:
    return json.dumps(cfg, indent=2, ensure_ascii=False) + "\n"


def diff(before: str, after: str, name: str = "flow.config.json") -> str:
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=name, tofile=name + " (migrated)"))
