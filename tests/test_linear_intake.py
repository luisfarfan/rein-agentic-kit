#!/usr/bin/env python3
"""Tests for taking an issue off the board and putting it back.

Both commands write to two systems that cannot be rolled back together --
a Beads issue and a Linear state — so most of what matters here is what
they REFUSE to do. The refusals are the feature; the happy path is one
test.

The two that would have shipped a wrong board:

  * `land` proving the merge with `git branch --contains` says "not
    merged" about squash-merged work, because a squash writes a NEW
    commit. It reported exactly that for the first issue this ran on.
  * `intake` run twice filing a second Beads issue for one bug — the one
    step in either command that re-running cannot undo.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(REPO_ROOT, "plugins", "rein", "lib")

sys.path.insert(0, LIB_DIR)
import linear_intake as li  # noqa: E402


def _issue(identifier="PPR-86", repo="proxima-api", state="Backlog", state_type="backlog",
           labels=None, parent="PPR-127", title="un bug"):
    names = labels if labels is not None else ["Bug", repo]
    return {
        "identifier": identifier, "title": title, "priority": 3,
        "branchName": f"luchofarfan9/{identifier.lower()}-un-bug",
        "url": f"https://linear.app/proximaa/issue/{identifier}",
        "state": {"name": state, "type": state_type},
        "labels": {"nodes": [{"name": n} for n in names]},
        "parent": {"identifier": parent} if parent else None,
        "description": (
            "**Impacto:** se rompe\n\n|  |  |\n| -- | -- |\n"
            f"| Repo dueño | `{repo}` |\n| Encontrado por | D12-01 |\n"
            "| Flujos afectados | D12 |\n\n---\n\nla prosa del bug\n"
        ),
    }


class FakeClient:
    def __init__(self, issue=None):
        self._issue = issue or _issue()
        self.states, self.comments = [], []

    def issue(self, identifier):
        return self._issue

    def set_state(self, identifier, state):
        self.states.append((identifier, state))
        return {"identifier": identifier, "state": {"name": state}}

    def comment(self, identifier, body):
        self.comments.append((identifier, body))
        return {"id": "c1", "url": "u"}


class FakeRun:
    """Records commands; replays canned answers keyed by a substring."""

    def __init__(self, answers=None):
        self.calls = []
        self.answers = answers or {}

    def __call__(self, cmd, cwd, timeout):
        self.calls.append(cmd)
        for key, value in self.answers.items():
            if key in " ".join(cmd):
                return value
        if cmd[:2] == ["bd", "create"]:
            return ("✓ Created issue: proxima-api-abc — un bug", "")
        return ("", "")

    def ran(self, *fragment):
        joined = " ".join(fragment)
        return any(joined in " ".join(c) for c in self.calls)


def _workspace(repo="proxima-api", base="develop", tracker="beads"):
    """A container directory holding one repo that looks like a real one.

    `tracker` defaults to `"beads"` because that is the configuration most
    of these tests are about. The repos that declare `"none"` get their own
    tests below -- they are a different contract, not a degraded one.
    """
    root = tempfile.mkdtemp()
    repo_root = os.path.join(root, repo)
    os.makedirs(os.path.join(repo_root, ".git"))
    with open(os.path.join(repo_root, "flow.config.json"), "w", encoding="utf-8") as fh:
        json.dump({"worktree": {"baseBranch": base}, "tracker": {"kind": tracker}}, fh)
    return root, repo_root


class IntakeRefusesWhatItCannotImplementTests(unittest.TestCase):
    def test_a_flow_index_is_refused_by_name(self):
        # 14 of 64 issues are groupers. Taking one produces nothing.
        root, _ = _workspace()
        client = FakeClient(_issue(labels=["Flujo"], parent=None))
        with self.assertRaises(li.IntakeError) as caught:
            li.intake("PPR-137", root=root, client=client, run=FakeRun())
        self.assertIn("index", str(caught.exception))
        self.assertEqual(client.states, [], "refused, but the board was touched")

    def test_a_malformed_issue_names_the_missing_fields(self):
        root, _ = _workspace()
        broken = _issue()
        broken["description"] = "no frame at all"
        broken["labels"] = {"nodes": [{"name": "Bug"}]}
        with self.assertRaises(li.IntakeError) as caught:
            li.intake("PPR-1", root=root, client=FakeClient(broken), run=FakeRun())
        self.assertIn("missing", str(caught.exception))

    def test_a_repo_disagreement_stops_the_intake(self):
        # Label says one repo, body says another. Choosing silently lands
        # the fix in the wrong repository.
        root, _ = _workspace()
        drifted = _issue(repo="proxima-api")
        drifted["description"] = drifted["description"].replace(
            "`proxima-api`", "`proxima-hub`")
        with self.assertRaises(li.IntakeError) as caught:
            li.intake("PPR-86", root=root, client=FakeClient(drifted), run=FakeRun())
        self.assertIn("wrong repository", str(caught.exception))

    def test_an_already_done_issue_is_refused(self):
        root, _ = _workspace()
        with self.assertRaises(li.IntakeError):
            li.intake("PPR-86", root=root,
                      client=FakeClient(_issue(state="Done", state_type="completed")),
                      run=FakeRun())

    def test_an_unknown_repo_directory_says_where_to_point_root(self):
        root = tempfile.mkdtemp()  # empty: no repo inside
        with self.assertRaises(li.IntakeError) as caught:
            li.intake("PPR-86", root=root, client=FakeClient(), run=FakeRun())
        self.assertIn("--root", str(caught.exception))

    def test_a_directory_without_git_is_not_a_repo(self):
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "proxima-api"))
        with self.assertRaises(li.IntakeError) as caught:
            li.intake("PPR-86", root=root, client=FakeClient(), run=FakeRun())
        self.assertIn("not a git repository", str(caught.exception))


class IntakeDoesTheFourThingsTests(unittest.TestCase):
    def test_bead_branch_record_and_board(self):
        root, repo_root = _workspace()
        client, run = FakeClient(), FakeRun()

        report = li.intake("PPR-86", root=root, client=client, run=run)

        self.assertTrue(run.ran("bd", "create"))
        self.assertTrue(run.ran("git", "checkout", "develop"))
        self.assertTrue(run.ran("git", "checkout", "-b", "luchofarfan9/ppr-86-un-bug"))
        self.assertEqual(client.states, [("PPR-86", "In Progress")])
        self.assertEqual(len(client.comments), 1)
        self.assertEqual(report["bead"], "proxima-api-abc")

        record = li.load_record(repo_root)
        self.assertEqual(record["PPR-86"]["bead"], "proxima-api-abc")
        self.assertEqual(record["PPR-86"]["base"], "develop")

    def test_the_base_branch_comes_from_the_repos_own_config(self):
        # This workspace builds off `develop`. Branching a fix off `main`
        # would put it on top of code that is not what ships.
        root, _ = _workspace(base="develop")
        run = FakeRun()
        li.intake("PPR-86", root=root, client=FakeClient(), run=run)
        self.assertTrue(run.ran("git", "checkout", "develop"))

    def test_the_branch_is_the_one_linear_already_named(self):
        # Linear auto-links a branch whose name it generated. Inventing one
        # throws that away.
        root, _ = _workspace()
        report = li.intake("PPR-86", root=root, client=FakeClient(), run=FakeRun())
        self.assertEqual(report["branch"], "luchofarfan9/ppr-86-un-bug")

    def test_a_dry_run_writes_nothing_anywhere(self):
        root, repo_root = _workspace()
        client, run = FakeClient(), FakeRun()
        report = li.intake("PPR-86", root=root, client=client, run=run, dry_run=True)
        self.assertTrue(report["dryRun"])
        self.assertEqual(run.calls, [])
        self.assertEqual(client.states, [])
        self.assertEqual(client.comments, [])
        self.assertFalse(os.path.exists(li.record_path(repo_root)))


class ARepeatedIntakeDoesNotFileASecondBeadTests(unittest.TestCase):
    """The one step re-running cannot undo. Two Beads issues for one bug is
    a board nobody can trust to say how much work is open."""

    def test_the_second_run_reuses_the_recorded_bead(self):
        root, repo_root = _workspace()
        li.intake("PPR-86", root=root, client=FakeClient(), run=FakeRun())

        second = FakeRun()
        report = li.intake("PPR-86", root=root, client=FakeClient(), run=second)

        self.assertFalse(second.ran("bd", "create"))
        self.assertTrue(report["resumed"])
        self.assertEqual(report["bead"], "proxima-api-abc")

    def test_an_existing_branch_is_switched_to_not_treated_as_failure(self):
        root, _ = _workspace()
        run = FakeRun({"checkout -b": ("", "fatal: a branch named ... already exists")})
        report = li.intake("PPR-86", root=root, client=FakeClient(), run=run)
        self.assertTrue(any("already existed" in s for s in report["steps"]))

    def test_an_issue_already_in_progress_is_not_moved_again(self):
        root, _ = _workspace()
        client = FakeClient(_issue(state="In Progress", state_type="started"))
        li.intake("PPR-86", root=root, client=client, run=FakeRun())
        self.assertEqual(client.states, [])
        self.assertEqual(len(client.comments), 1)


class BeadsIsOptInThroughTheReposOwnConfigTests(unittest.TestCase):
    """`flow.config.json`'s `tracker.kind` already meant this, and the first
    version of the module ignored it: it filed a Beads issue in every repo,
    including the ones that declared `"none"`, and would fail outright where
    `bd` is not installed."""

    def test_no_bead_is_filed_when_the_repo_says_none(self):
        root, repo_root = _workspace(tracker="none")
        run = FakeRun()
        report = li.intake("PPR-86", root=root, client=FakeClient(), run=run)

        self.assertFalse(run.ran("bd", "create"))
        self.assertEqual(report["bead"], "")
        self.assertTrue(any("not \"beads\"" in s for s in report["steps"]))

    def test_the_rest_of_the_intake_still_happens(self):
        # Skipping Beads is not a degraded intake: the branch, the record and
        # the board move are the parts that always apply.
        root, repo_root = _workspace(tracker="none")
        client, run = FakeClient(), FakeRun()
        li.intake("PPR-86", root=root, client=client, run=run)

        self.assertTrue(run.ran("git", "checkout", "-b", "luchofarfan9/ppr-86-un-bug"))
        self.assertEqual(client.states, [("PPR-86", "In Progress")])
        self.assertIn("PPR-86", li.load_record(repo_root))

    def test_land_does_not_demand_a_bead_the_repo_never_files(self):
        # Without this, `land` refused for ever on a `none` repo: it asked
        # for a --bead that intake was right not to create.
        root, _ = _workspace(tracker="none")
        li.intake("PPR-86", root=root, client=FakeClient(), run=FakeRun())

        client = FakeClient(_issue(state="In Progress", state_type="started"))
        run = FakeRun({"--grep": ("4e64f2b1 fix: PPR-86", "")})
        report = li.land("PPR-86", root=root, client=client, run=run)

        self.assertFalse(run.ran("bd", "close"))
        self.assertEqual(client.states, [("PPR-86", "Done")])
        self.assertEqual(report["bead"], "")

    def test_a_beads_repo_still_gets_both(self):
        root, _ = _workspace(tracker="beads")
        run = FakeRun()
        li.intake("PPR-86", root=root, client=FakeClient(), run=run)
        self.assertTrue(run.ran("bd", "create"))

    def test_an_unreadable_config_reads_as_none_not_as_beads(self):
        # Guessing "beads" from a missing config would shell out to a CLI
        # the repo never claimed to have.
        root, repo_root = _workspace()
        with open(os.path.join(repo_root, "flow.config.json"), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(li.tracker_kind(repo_root), "none")
        self.assertFalse(li.uses_beads(repo_root))


class MergedIsCheckedByTheLogNotTheGraphTests(unittest.TestCase):
    """A squash merge writes a NEW commit, so the branch's own sha is
    nowhere in the base. What survives is the identifier in the subject."""

    def test_it_greps_the_base_log_and_never_uses_branch_contains(self):
        root, repo_root = _workspace()
        run = FakeRun({"--grep": ("4e64f2b1 fix: PPR-86 — algo", "")})
        self.assertTrue(li.is_merged(repo_root, "PPR-86", "develop", run=run))
        self.assertFalse(any("--contains" in " ".join(c) for c in run.calls))

    def test_no_mention_means_not_merged(self):
        root, repo_root = _workspace()
        self.assertFalse(li.is_merged(repo_root, "PPR-86", "develop", run=FakeRun()))


class LandRefusesToClaimWhatDidNotShipTests(unittest.TestCase):
    def _taken(self, base="develop"):
        root, repo_root = _workspace(base=base)
        li.intake("PPR-86", root=root, client=FakeClient(), run=FakeRun())
        return root, repo_root

    def test_unmerged_work_is_refused(self):
        root, _ = self._taken()
        client = FakeClient(_issue(state="In Progress", state_type="started"))
        with self.assertRaises(li.IntakeError) as caught:
            li.land("PPR-86", root=root, client=client, run=FakeRun())
        self.assertIn("Merge it first", str(caught.exception))
        self.assertEqual(client.states, [], "refused, but the board says Done")

    def test_force_lands_it_anyway(self):
        root, _ = self._taken()
        client = FakeClient(_issue(state="In Progress", state_type="started"))
        li.land("PPR-86", root=root, client=client, run=FakeRun(), force=True)
        self.assertEqual(client.states, [("PPR-86", "Done")])

    def test_a_missing_record_refuses_instead_of_guessing(self):
        root, _ = _workspace()  # never taken, so no record
        client = FakeClient(_issue(state="In Progress", state_type="started"))
        with self.assertRaises(li.IntakeError) as caught:
            li.land("PPR-86", root=root, client=client, run=FakeRun())
        self.assertIn("--bead", str(caught.exception))

    def test_an_explicit_bead_replaces_the_missing_record(self):
        root, _ = _workspace()
        client = FakeClient(_issue(state="In Progress", state_type="started"))
        run = FakeRun({"--grep": ("4e64f2b1 fix: PPR-86", "")})
        report = li.land("PPR-86", root=root, client=client, run=run, bead="proxima-api-xyz")
        self.assertTrue(run.ran("bd", "close", "proxima-api-xyz"))
        self.assertEqual(report["bead"], "proxima-api-xyz")

    def test_an_already_done_issue_is_refused(self):
        root, _ = self._taken()
        client = FakeClient(_issue(state="Done", state_type="completed"))
        with self.assertRaises(li.IntakeError):
            li.land("PPR-86", root=root, client=client, run=FakeRun())


class LandClosesBothSidesTests(unittest.TestCase):
    def test_bead_closed_then_linear_done_with_the_commit(self):
        root, _ = _workspace()
        li.intake("PPR-86", root=root, client=FakeClient(), run=FakeRun())

        client = FakeClient(_issue(state="In Progress", state_type="started"))
        run = FakeRun({"--grep": ("4e64f2b1 fix(commerce): PPR-86 — algo", "")})
        report = li.land("PPR-86", root=root, client=client, run=run)

        self.assertTrue(run.ran("bd", "close", "proxima-api-abc"))
        self.assertEqual(client.states, [("PPR-86", "Done")])
        self.assertIn("4e64f2b1", client.comments[0][1])
        self.assertEqual(report["mergeCommit"], "4e64f2b1")

    def test_a_dry_run_closes_nothing(self):
        root, _ = _workspace()
        li.intake("PPR-86", root=root, client=FakeClient(), run=FakeRun())
        client = FakeClient(_issue(state="In Progress", state_type="started"))
        run = FakeRun({"--grep": ("4e64f2b1 fix: PPR-86", "")})
        li.land("PPR-86", root=root, client=client, run=run, dry_run=True)
        self.assertFalse(run.ran("bd", "close"))
        self.assertEqual(client.states, [])


class TheRecordSurvivesAMalformedFileTests(unittest.TestCase):
    def test_unreadable_json_reads_as_empty_not_as_a_crash(self):
        root, repo_root = _workspace()
        os.makedirs(os.path.dirname(li.record_path(repo_root)), exist_ok=True)
        with open(li.record_path(repo_root), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(li.load_record(repo_root), {})


if __name__ == "__main__":
    unittest.main()
