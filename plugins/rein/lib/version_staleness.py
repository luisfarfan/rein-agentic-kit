"""version_staleness.py -- does `rein doctor`'s own plugin need an update?

Two facts already live on disk, maintained by Claude Code itself, no network
required (D1):

  - ``~/.claude/plugins/installed_plugins.json``
    what is installed, per scope, keyed by ``"<plugin>@<marketplace>"``
  - ``~/.claude/plugins/marketplaces/<marketplace>/.claude-plugin/marketplace.json``
    what that marketplace's git clone currently offers

Comparing them is a string/tuple comparison over two JSON reads. The
repository's own VERSION constant is a THIRD fact and is deliberately never
consulted here -- nothing on disk can prove the marketplace clone itself is
current (D3), so this module only ever compares installed-vs-marketplace,
never installed-vs-repo.

Split in two, on purpose (so the branchy part is a plain function over data,
with no I/O to fake in a test):

  - ``load_staleness_inputs`` -- the READING half. Resolves the two known
    paths, parses what it finds, never raises on a missing file, an
    unreadable one, or a plugin root that was not installed from a
    marketplace cache at all (a dev checkout or a symlinked install, per
    docs/phase-0-findings.md's "Live symlink" dev loop) -- that last case
    also reports ``unknown``, per D3.
  - ``decide_staleness`` -- the DECIDING half. Pure: only ever touches the
    already-parsed dict-or-None documents handed to it. Every SHAPE it
    cannot read (plugin absent from the installed list, no ``version`` key,
    a ``version`` that is not a string) is its own named ``unknown`` reason
    (D3). Missing files and malformed JSON are the loader's problem, not
    this function's -- it never sees a path.

No network capability is imported anywhere in this module (D1) -- checked
mechanically by tests/test_version_staleness.py via ``ast``, not by review.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

UP_TO_DATE = "up-to-date"
STALE = "stale"
UNKNOWN = "unknown"

CLAUDE_PLUGINS_DIR = os.path.join(os.path.expanduser("~"), ".claude", "plugins")


@dataclass(frozen=True)
class StalenessResult:
    verdict: str
    reason: str
    installed_version: str | None = None
    available_version: str | None = None

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "reason": self.reason,
            "installedVersion": self.installed_version,
            "availableVersion": self.available_version,
        }


@dataclass(frozen=True)
class LoaderResult:
    installed_doc: dict | None
    marketplace_doc: dict | None
    plugin_key: str | None
    plugin_name: str | None
    marketplace_name: str | None
    load_reason: str | None = None


def _parse_version(value) -> tuple[int, ...] | None:
    """'0.4.0' -> (0, 4, 0). None for anything not a dotted-int string."""
    if not isinstance(value, str) or not value:
        return None
    parts = value.split(".")
    out = []
    for p in parts:
        if not p.isdigit():
            return None
        out.append(int(p))
    return tuple(out)


def _select_installed_entry(entry_list: list, plugin_root: str | None) -> tuple[dict | None, str | None]:
    """installed_plugins.json keys each plugin as a LIST because scopes
    (user / project / local) coexist. A single-entry list is unambiguous.
    A multi-entry list must be disambiguated against the plugin root the
    CALLER is actually running from (bin/rein's PLUGIN_ROOT, already
    realpath'd) by matching it to one entry's "installPath" -- guessing
    (e.g. always taking the last entry) can produce a false 'stale' or a
    false 'up-to-date' for the entry that is not actually running, which
    D3 forbids. Returns (entry, None) on success, or (None, reason) when
    no entry -- or more than one -- matches.
    """
    if len(entry_list) == 1:
        entry = entry_list[0]
        return (entry, None) if isinstance(entry, dict) else (None, "installed entry is not an object")
    if not plugin_root:
        return None, f"is installed at {len(entry_list)} scopes and no running plugin root was given to identify it"
    target = os.path.realpath(plugin_root)
    matches = [
        e
        for e in entry_list
        if isinstance(e, dict)
        and isinstance(e.get("installPath"), str)
        and os.path.realpath(e["installPath"]) == target
    ]
    if len(matches) != 1:
        return (
            None,
            f"is installed at {len(entry_list)} scopes and the running install "
            f"({plugin_root!r}) could not be matched to exactly one of them",
        )
    return matches[0], None


def decide_staleness(
    installed_doc: dict | None,
    marketplace_doc: dict | None,
    plugin_key: str | None,
    plugin_name: str | None,
    plugin_root: str | None = None,
) -> StalenessResult:
    """Pure: no filesystem access. installed_doc is the parsed contents of
    installed_plugins.json (or None); marketplace_doc is the parsed contents
    of a marketplace.json (or None). plugin_key looks up installed_doc's
    "plugins" dict (e.g. "rein@rein-agentic-kit"); plugin_name looks up an
    entry by "name" inside marketplace_doc's "plugins" list (e.g. "rein").
    plugin_root, when the installed list has more than one scope entry, is
    used to identify which entry is the one actually running (see
    _select_installed_entry) -- it is never used to read the filesystem here.
    """
    if not isinstance(installed_doc, dict):
        return StalenessResult(UNKNOWN, "installed_plugins.json could not be read")
    if not isinstance(marketplace_doc, dict):
        return StalenessResult(UNKNOWN, "marketplace.json could not be read")
    if not plugin_key or not plugin_name:
        return StalenessResult(UNKNOWN, "no plugin/marketplace identity to look up")

    installed_entries = installed_doc.get("plugins")
    if not isinstance(installed_entries, dict) or plugin_key not in installed_entries:
        return StalenessResult(UNKNOWN, f"{plugin_key!r} is not in installed_plugins.json")
    entry_list = installed_entries[plugin_key]
    if not isinstance(entry_list, list) or not entry_list:
        return StalenessResult(UNKNOWN, f"{plugin_key!r} has no installed entries")
    installed_entry, select_reason = _select_installed_entry(entry_list, plugin_root)
    if installed_entry is None:
        return StalenessResult(UNKNOWN, f"{plugin_key!r} {select_reason}")
    installed_version_raw = installed_entry.get("version")
    if not isinstance(installed_version_raw, str):
        return StalenessResult(UNKNOWN, f"{plugin_key!r} has no string 'version'")

    marketplace_entries = marketplace_doc.get("plugins")
    if not isinstance(marketplace_entries, list):
        return StalenessResult(UNKNOWN, "marketplace.json has no 'plugins' list", installed_version_raw)
    market_entry = next(
        (p for p in marketplace_entries if isinstance(p, dict) and p.get("name") == plugin_name),
        None,
    )
    if market_entry is None:
        return StalenessResult(UNKNOWN, f"{plugin_name!r} is not offered by this marketplace", installed_version_raw)
    available_version_raw = market_entry.get("version")
    if not isinstance(available_version_raw, str):
        return StalenessResult(
            UNKNOWN, f"{plugin_name!r}'s marketplace entry has no string 'version'", installed_version_raw
        )

    installed_v = _parse_version(installed_version_raw)
    available_v = _parse_version(available_version_raw)
    if installed_v is None or available_v is None:
        return StalenessResult(
            UNKNOWN,
            f"cannot compare versions {installed_version_raw!r} / {available_version_raw!r}",
            installed_version_raw,
            available_version_raw,
        )

    if installed_v == available_v:
        return StalenessResult(
            UP_TO_DATE,
            "installed matches what the marketplace clone offers -- but the clone itself can be "
            "stale and nothing local can prove otherwise",
            installed_version_raw,
            available_version_raw,
        )
    if installed_v < available_v:
        return StalenessResult(
            STALE,
            f"installed {installed_version_raw} is older than the {available_version_raw} the "
            "marketplace clone offers",
            installed_version_raw,
            available_version_raw,
        )
    return StalenessResult(
        UNKNOWN,
        f"installed {installed_version_raw} is newer than the marketplace clone's "
        f"{available_version_raw} -- likely a developer running from a checkout, not a stale install",
        installed_version_raw,
        available_version_raw,
    )


def _read_json(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _derive_marketplace_and_name(plugin_root: str) -> tuple[str, str] | None:
    """A plugin installed FROM a marketplace lives at
    ``.../cache/<marketplace>/<name>/<version>`` (docs/phase-0-findings.md).
    None when plugin_root does not look like that -- a dev checkout or a
    symlinked install, per D3's "installed from a path" case.
    """
    version_dir = os.path.abspath(plugin_root)
    name_dir = os.path.dirname(version_dir)
    marketplace_dir = os.path.dirname(name_dir)
    cache_dir = os.path.dirname(marketplace_dir)
    if os.path.basename(cache_dir) != "cache":
        return None
    name = os.path.basename(name_dir)
    marketplace = os.path.basename(marketplace_dir)
    if not name or not marketplace:
        return None
    return marketplace, name


def load_staleness_inputs(plugin_root: str) -> LoaderResult:
    """The READING half. Never raises: a missing/unreadable file becomes a
    None doc, and a plugin_root that isn't a marketplace-cache path becomes
    a LoaderResult with load_reason set and both docs None.
    """
    derived = _derive_marketplace_and_name(plugin_root)
    if derived is None:
        return LoaderResult(
            installed_doc=None,
            marketplace_doc=None,
            plugin_key=None,
            plugin_name=None,
            marketplace_name=None,
            load_reason=(
                "plugin root is not a marketplace cache path "
                "(expected .../cache/<marketplace>/<name>/<version>) -- likely a dev checkout "
                "or a symlinked install"
            ),
        )
    marketplace_name, plugin_name = derived
    installed_doc = _read_json(os.path.join(CLAUDE_PLUGINS_DIR, "installed_plugins.json"))
    marketplace_doc = _read_json(
        os.path.join(CLAUDE_PLUGINS_DIR, "marketplaces", marketplace_name, ".claude-plugin", "marketplace.json")
    )
    return LoaderResult(
        installed_doc=installed_doc,
        marketplace_doc=marketplace_doc,
        plugin_key=f"{plugin_name}@{marketplace_name}",
        plugin_name=plugin_name,
        marketplace_name=marketplace_name,
        load_reason=None,
    )


def resolve_verdict(plugin_root: str) -> tuple[StalenessResult, LoaderResult]:
    """Load + decide, in one call -- what `rein doctor` uses."""
    loaded = load_staleness_inputs(plugin_root)
    if loaded.load_reason is not None:
        return StalenessResult(UNKNOWN, loaded.load_reason), loaded
    result = decide_staleness(
        loaded.installed_doc, loaded.marketplace_doc, loaded.plugin_key, loaded.plugin_name, plugin_root
    )
    return result, loaded


def fix_commands(marketplace_name: str, plugin_name: str) -> list[str]:
    """Refresh the marketplace clone BEFORE updating the plugin -- updating
    first against a stale clone is a no-op (the measured failure this task
    exists for).
    """
    return [
        f"claude plugin marketplace update {marketplace_name}",
        f"claude plugin update {plugin_name}",
    ]
