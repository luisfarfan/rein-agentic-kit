#!/usr/bin/env python3
"""Tests for `rein linear comment`.

The subcommand exists so that leaving a comment does not require going
around rein. `linear_client.comment()` was always there and `rein intake` /
`rein land` always used it; only the CLI door was missing, so anything that
wanted to comment on its own reached for the raw GraphQL endpoint with the
personal API key instead — the credential spread across ad-hoc scripts,
with no bounded surface and nothing to audit.

Everything here runs the SHIPPED script through a subprocess, because the
property being tested is what the command-line does, and importing the
module would skip the layer that has the bug.

**Exit codes carry the distinction**: `2` is a usage mistake, `1` is
everything else. The tests below assert `2` with no API key in the
environment, which pins the real property — a malformed invocation answers
with the malformation, instead of demanding a credential it was never
going to use.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REIN_BIN = os.path.join(REPO_ROOT, "plugins", "rein", "bin", "rein")


def _run(*args, stdin: str = "", key: str = ""):
    env = dict(os.environ)
    env.pop("REIN_LINEAR_API_KEY", None)
    if key:
        env["REIN_LINEAR_API_KEY"] = key
    proc = subprocess.run(
        [sys.executable, REIN_BIN, "linear", *args],
        input=stdin, capture_output=True, text=True, env=env, timeout=60,
    )
    return proc.returncode, proc.stdout + proc.stderr


class AUsageMistakeDoesNotAskForACredentialTests(unittest.TestCase):
    """Refusing before the key is looked up is the point: an operator who
    typed the command wrong should be told that, not sent hunting for an
    environment variable the run will never reach."""

    def test_no_identifier_prints_the_usage(self):
        code, out = _run("comment")
        self.assertEqual(code, 2)
        self.assertIn("usage: rein linear comment", out)
        self.assertNotIn("REIN_LINEAR_API_KEY", out)

    def test_no_body_is_refused(self):
        code, out = _run("comment", "PPR-1")
        self.assertEqual(code, 2)
        self.assertIn("empty comment", out)
        self.assertNotIn("REIN_LINEAR_API_KEY", out)

    def test_body_and_body_file_together_are_refused(self):
        code, out = _run("comment", "PPR-1", "--body", "x", "--body-file", "y.md")
        self.assertEqual(code, 2)
        self.assertIn("not both", out)

    def test_whitespace_is_not_a_comment(self):
        code, out = _run("comment", "PPR-1", "--body", "   \n\t ")
        self.assertEqual(code, 2)
        self.assertIn("empty comment", out)

    def test_an_unreadable_body_file_names_the_path(self):
        code, out = _run("comment", "PPR-1", "--body-file", "/nope/missing.md")
        self.assertEqual(code, 2)
        self.assertIn("/nope/missing.md", out)


class AWellFormedCallGetsPastValidationTests(unittest.TestCase):
    """Exit 1 with no key means the arguments were accepted and the run
    reached the credential. Anything still returning 2 would mean the body
    never resolved -- which is what a broken `--body-file` looks like."""

    def _reached_the_api(self, *args, stdin=""):
        code, out = _run(*args, stdin=stdin)
        self.assertEqual(code, 1, f"expected to reach the key check, got {code}: {out}")
        self.assertIn("REIN_LINEAR_API_KEY", out)

    def test_an_inline_body(self):
        self._reached_the_api("comment", "PPR-1", "--body", "listo")

    def test_a_body_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                         encoding="utf-8") as fh:
            fh.write("## Hecho\n\nCon `backticks` y \"comillas\".\n")
            path = fh.name
        try:
            self._reached_the_api("comment", "PPR-1", "--body-file", path)
        finally:
            os.unlink(path)

    def test_stdin(self):
        # The option that matters. A comment is multi-line markdown, and
        # pushing that through shell quoting is how backticks and quotes get
        # mangled on the way to the board.
        self._reached_the_api("comment", "PPR-1", "--body-file", "-",
                              stdin="## Hecho\n\n`x` y \"y\"\n")


class StdinIsConsumedExactlyOnceTests(unittest.TestCase):
    """`--body-file -` reads a stream that can only be read once.

    This is a source-level test on purpose, and it exists because the
    behavioural tests above CANNOT catch the bug it pins. An early version
    resolved the body twice — once to refuse a usage error before the key
    was looked up, once inside the action — so the second read got an
    exhausted stream and reported "empty comment" for a comment that was
    right there. Every test above still passed: with no API key in the
    environment the run stops at the credential check, which is BEFORE the
    second read. It only failed against a real pipe with a real key.

    So the property is pinned where it lives: the resolver is called once.
    """

    def _source(self) -> str:
        with open(REIN_BIN, encoding="utf-8") as fh:
            return fh.read()

    def test_the_resolver_is_defined_once_and_called_once(self):
        source = self._source()
        self.assertEqual(source.count("def _comment_request("), 1)
        self.assertEqual(
            source.count("_comment_request(rest)"), 1,
            "the comment body is resolved more than once -- `--body-file -` "
            "reads stdin, and the second read gets an exhausted stream",
        )

    def test_stdin_is_read_from_exactly_one_place(self):
        self.assertEqual(self._source().count("sys.stdin.read()"), 1)


class TheWriteSurfaceStaysBoundedTests(unittest.TestCase):
    def test_comment_is_the_only_write_the_linear_subcommand_exposes(self):
        """No bare `rein linear state`, on purpose.

        Moving an issue to Done belongs to `rein land`, which refuses when
        the base branch carries no commit naming it. A free-form state
        command would route around that guard, and the guard is the point.
        """
        code, out = _run("state", "PPR-1", "Done")
        self.assertEqual(code, 2)
        self.assertIn("usage: rein linear", out)

    def test_the_usage_lists_exactly_list_show_and_comment(self):
        code, out = _run()
        self.assertEqual(code, 2)
        for action in ("rein linear list", "rein linear show", "rein linear comment"):
            self.assertIn(action, out)


if __name__ == "__main__":
    unittest.main()
