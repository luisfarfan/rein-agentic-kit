"""One blocked task used to silence the review of every other task.

In a real run: nine tasks, eight landed, and the ninth -- a supervised
verification depending on the whole pipeline -- blocked. The loop skipped
review entirely, so 95 minutes of implementation produced ZERO review signal
and three BLOCKING findings surfaced two hours later.

The reasoning it was built on ("reviewing an incomplete change burns a round
on a foregone CHANGES_REQUESTED") holds for two tasks and fails badly at
nine. But partial review has a sharp edge: approving what landed must never
become MERGING a half-finished change. That guard is what these tests are
mostly about.
"""

from __future__ import annotations

import os
import re
import unittest

LOOP_JS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "plugins", "rein", "workflows", "loop.js",
)


def _src() -> str:
    with open(LOOP_JS, encoding="utf-8") as fh:
        return fh.read()


class TestReviewIsNoLongerSuppressed(unittest.TestCase):
    def test_incomplete_tasks_do_not_skip_the_review(self):
        src = _src()
        self.assertNotIn("review NOT run: tasks still incomplete", src,
                         "one blocked task silences the review of every other one again")

    def test_the_reviewer_is_told_what_did_not_land(self):
        """A reviewer auditing eight of nine tasks without knowing the ninth is
        missing would judge a change it cannot see the shape of."""
        src = _src()
        self.assertIn("PARTIAL REVIEW", src)
        self.assertIn("are NOT yours to judge", src)
        # And told not to punish the landed work for the absence.
        self.assertIn("do not withhold approval for their absence", src)

    def test_the_partial_note_reaches_the_reviewer_prompt(self):
        """Building the string is worthless if the prompt does not carry it."""
        src = _src()
        self.assertRegex(src, r"stale\.`\s*\+\s*\n\s*partialNote,")


class TestApprovingPartialWorkNeverMergesIt(unittest.TestCase):
    """The sharp edge of the change, and the reason it is not simply 'review
    anyway': the review's approval used to flow straight into `git merge`."""

    def test_merge_is_gated_on_nothing_being_incomplete(self):
        src = _src()
        self.assertIn("if (approved && !mergeBlockedBy) {", src,
                      "approval of a partial review would merge a half-finished change")

    def test_the_block_reason_names_the_tasks(self):
        src = _src()
        block = src[src.index("const mergeBlockedBy"):]
        block = block[: block.index("\n\n")]
        self.assertIn("incomplete.length", block)
        self.assertIn("did not land", block)

    def test_the_result_says_the_review_was_partial(self):
        """`approved: true` on a partial review must not read as 'the whole
        change was approved' to anything downstream."""
        src = _src()
        self.assertIn("reviewScope: incomplete.length ? 'partial' : 'full'", src)
        self.assertIn("notMergedBecause: mergeBlockedBy", src)

    def test_the_operator_is_told_why_it_did_not_merge(self):
        src = _src()
        self.assertIn("not merging —", src)
