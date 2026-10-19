"""`quick`: the pure start-side decisions of the quick lane."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "plugins", "rein", "lib",
))

import quick  # noqa: E402
import quota  # noqa: E402
import ship_decide  # noqa: E402

REPRODUCE = "Primero escribe un test que lo reproduzca."
DONE = "Cuando diga *listo*, ejecuta `rein quick done` y muéstrame el resultado."
LINEAR = "Incluye PPR-123 en el mensaje de commit."
NO_ID = "Commit convencional sin ID de issue: no inventes uno ni lo preguntes."
BIN = "/opt/rein/plugins/rein/bin/rein"
FALLBACK = "Si `rein` no está en el PATH, usa `python3 %s quick done`." % BIN
LONG_DONE = (
    "`rein quick done` puede tardar hasta 30 minutos si hay auditoría: ejecútalo con un "
    "timeout de 35 minutos o en segundo plano y espera su resultado; no hagas commit hasta "
    "que termine en verde."
)
NO_REIN_COMMIT = "No incluyas `.rein/` en ningún commit."


class SlugTests(unittest.TestCase):
    def test_slug_folds_and_cuts(self):
        self.assertEqual(
            quick.slugify("Arreglar la tarjeta de Cupones usados en KPIs!!", set()),
            "arreglar-la-tarjeta-de-cupones",
        )
        self.assertEqual(quick.slugify("Ñandú acción", set()), "nandu-accion")

    def test_slug_cut_never_ends_in_dash(self):
        # the 30th character falls on the separator: it must be stripped
        slug = quick.slugify("a" * 29 + " bbbb", set())
        self.assertEqual(slug, "a" * 29)
        self.assertLessEqual(len(quick.slugify("x" * 80, set())), 30)

    def test_slug_falls_back_to_task(self):
        self.assertEqual(quick.slugify("¡¿…?! 🎉", set()), "task")
        self.assertEqual(quick.slugify("", set()), "task")

    def test_slug_suffix_on_clash(self):
        self.assertEqual(quick.slugify("x", {"quick-x", "quick-x-2"}), "x-3")
        self.assertEqual(quick.slugify("x", {"quick-y"}), "x")

    def test_clash_suffix_is_added_after_the_cut(self):
        base = quick.slugify("a" * 80, set())
        self.assertEqual(len(base), 30)
        self.assertEqual(quick.slugify("a" * 80, {"quick-" + base}), base + "-2")


class BugTests(unittest.TestCase):
    def test_bug_detection(self):
        self.assertTrue(quick.looks_like_bug("El botón de guardar no funciona"))
        self.assertTrue(quick.looks_like_bug("Sale un ERROR 500 al abrir el reporte"))
        self.assertFalse(quick.looks_like_bug("Agregar columna de cupones a la tarjeta"))
        self.assertFalse(quick.looks_like_bug("Cambiar el color del encabezado"))


class BriefTests(unittest.TestCase):
    def test_brief_bug_and_linear_variants(self):
        plain = quick.build_brief("Cambiar el color")
        self.assertNotIn(REPRODUCE, plain)
        self.assertIn(NO_ID, plain)
        self.assertNotIn("PPR-", plain)

        bug = quick.build_brief("El botón no funciona", linear="PPR-123", bug=True)
        self.assertIn(REPRODUCE, bug)
        self.assertIn(LINEAR, bug)
        self.assertNotIn(NO_ID, bug)

    def test_brief_lines_in_spec_order(self):
        sentence = "El botón no funciona"
        brief = quick.build_brief(sentence, linear="PPR-123", bug=True, bin_path=BIN)
        marks = [
            sentence,
            "Iteramos juntos: propón, aplica, y espera mi visto bueno antes de seguir.",
            REPRODUCE,
            DONE,
            FALLBACK,
            LONG_DONE,
            "No hagas push, merge ni PR salvo que yo lo pida; "
            "si la rama destino es main, pide confirmación antes.",
            LINEAR,
            NO_REIN_COMMIT,
            "No leas `.env*` ni credenciales.",
        ]
        positions = [brief.index(m) for m in marks]
        self.assertEqual(positions, sorted(positions))
        self.assertTrue(brief.startswith(sentence))

    def test_brief_word_budget(self):
        sentence = " ".join(["palabra"] * 40)
        for bug in (False, True):
            for linear in ("", "PPR-123"):
                brief = quick.build_brief(sentence, linear=linear, bug=bug, bin_path=BIN)
                self.assertLessEqual(len(brief.split()), 170)
                self.assertIn(sentence, brief)


    def test_brief_names_the_fallback_only_when_given_a_path(self):
        self.assertNotIn("python3", quick.build_brief("x"))
        self.assertIn(FALLBACK, quick.build_brief("x", bin_path=BIN))

    def test_brief_warns_that_done_is_slow_and_keeps_rein_out_of_commits(self):
        brief = quick.build_brief("x")
        self.assertIn(LONG_DONE, brief)
        self.assertIn(NO_REIN_COMMIT, brief)


class RefusalTests(unittest.TestCase):
    def test_start_refusals(self):
        self.assertEqual(quick.start_refusal(False, False, False, False), "")
        self.assertEqual(quick.start_refusal(False, True, True, False), "")
        self.assertEqual(quick.start_refusal(True, False, True, True), "")
        self.assertIn("--base", quick.start_refusal(True, True, False, False))
        dirty = quick.start_refusal(True, False, True, False)
        self.assertIn("--dirty-ok", dirty)
        self.assertNotEqual(dirty, "")

    def test_dirty_only_matters_with_here(self):
        # a new worktree starts clean whatever the main checkout looks like
        self.assertEqual(quick.start_refusal(False, False, True, False), "")


class RecordTests(unittest.TestCase):
    def test_new_record_schema(self):
        rec = quick.new_record(
            "/r", "/w", "develop", "abc123", "codex", "gpt-6.1-sol",
            "arreglar algo", "PPR-1", 1700000000.0,
        )
        self.assertEqual(
            set(rec),
            {"version", "repo", "workspace", "base", "base_sha", "agent",
             "model", "sentence", "linear", "started_at", "audit_rounds"},
        )
        self.assertEqual(rec["version"], 1)
        self.assertEqual(rec["audit_rounds"], 0)
        self.assertEqual(rec["started_at"], 1700000000.0)
        self.assertEqual(rec["base_sha"], "abc123")
        self.assertEqual(quick.RECORD, ".rein/quick.json")


class FakeResult:
    def __init__(self, slot, outcome="ok", exit_code=0, elapsed_ms=10, error=""):
        self.slot, self.outcome = slot, outcome
        self.exit_code, self.elapsed_ms = exit_code, elapsed_ms
        self.error = error


def exists_in(*paths):
    return lambda p: p in paths


class RelatedTestsTests(unittest.TestCase):
    def test_related_tests_typescript(self):
        found = quick.related_tests(["src/a/card.ts"], exists_in("src/a/card.spec.ts"))
        self.assertEqual(found, ["src/a/card.spec.ts"])

    def test_related_tests_typescript_test_sibling_and_changed_tests(self):
        found = quick.related_tests(
            ["src/a/card.tsx", "src/b/list.test.ts"],
            exists_in("src/a/card.test.tsx", "src/b/list.test.ts"),
        )
        self.assertEqual(found, ["src/a/card.test.tsx", "src/b/list.test.ts"])

    def test_related_tests_python(self):
        found = quick.related_tests(["app/x.py"], exists_in("tests/app/test_x.py"))
        self.assertEqual(found, ["tests/app/test_x.py"])
        beside = quick.related_tests(["app/x.py"], exists_in("app/test_x.py"))
        self.assertEqual(beside, ["app/test_x.py"])

    def test_related_tests_python_top_level_module(self):
        found = quick.related_tests(["x.py"], exists_in("tests/test_x.py", "test_x.py"))
        self.assertEqual(found, ["test_x.py", "tests/test_x.py"])

    def test_related_tests_skip_deleted(self):
        found = quick.related_tests(
            ["src/a/gone.spec.ts", "src/a/gone.ts", "app/old.py", "tests/test_old.py"],
            exists_in(),
        )
        self.assertEqual(found, [])

    def test_related_tests_sorted_unique(self):
        found = quick.related_tests(
            ["b/x.py", "tests/b/test_x.py", "a/y.py"],
            exists_in("tests/b/test_x.py", "a/test_y.py"),
        )
        self.assertEqual(found, ["a/test_y.py", "tests/b/test_x.py"])

    def test_a_changed_test_is_not_its_own_source(self):
        found = quick.related_tests(["tests/test_x.py"], exists_in("tests/test_x.py"))
        self.assertEqual(found, ["tests/test_x.py"])


class GateStepsTests(unittest.TestCase):
    CMDS = {"lint": "npm run lint", "typecheck": "tsc --noEmit",
            "test": "npm test", "testOne": "npx vitest run {target}"}

    def test_lint_typecheck_then_each_related_test(self):
        steps = quick.gate_steps(self.CMDS, ["a.spec.ts", "b.spec.ts"])
        self.assertEqual(steps, [
            ("lint", "npm run lint", "npm run lint"),
            ("typecheck", "tsc --noEmit", "tsc --noEmit"),
            ("testOne:a.spec.ts", "npx vitest run a.spec.ts", "npx vitest run {target}"),
            ("testOne:b.spec.ts", "npx vitest run b.spec.ts", "npx vitest run {target}"),
        ])

    def test_gate_steps_cap_and_fallback(self):
        eleven = ["t%d.spec.ts" % i for i in range(11)]
        steps = quick.gate_steps(self.CMDS, eleven)
        self.assertEqual([s[0] for s in steps], ["lint", "typecheck", "test"])
        ten = quick.gate_steps(self.CMDS, eleven[:10])
        self.assertEqual(len(ten), 12)
        no_one = {k: v for k, v in self.CMDS.items() if k != "testOne"}
        self.assertEqual(
            [s[0] for s in quick.gate_steps(no_one, ["a.spec.ts"])],
            ["lint", "typecheck", "test"],
        )
        self.assertEqual(
            [s[0] for s in quick.gate_steps(self.CMDS, [])], ["lint", "typecheck"]
        )

    def test_unconfigured_commands_are_left_out(self):
        self.assertEqual(quick.gate_steps({}, ["a.spec.ts"]), [])
        self.assertEqual(
            quick.gate_steps({"lint": "", "testOne": None}, ["a.spec.ts"]), []
        )

    def test_target_with_spaces_is_quoted(self):
        steps = quick.gate_steps({"testOne": "pytest {target}"}, ["tests/a b/test_x.py"])
        self.assertEqual(steps[0][1], "pytest 'tests/a b/test_x.py'")


class VerdictTests(unittest.TestCase):
    def test_verdict_green_red(self):
        self.assertEqual(quick.verdict([]), "green")
        self.assertEqual(quick.verdict([FakeResult("lint")]), "green")
        red = [FakeResult("lint"), FakeResult("test", "failed", 1)]
        self.assertEqual(quick.verdict(red), "red")

    def test_verdict_setup_beats_red(self):
        for outcome in ("not_invocable", "timeout", "inconclusive", "skipped", "proves_nothing"):
            mixed = [FakeResult("test", "failed", 1), FakeResult("lint", outcome, None)]
            self.assertEqual(quick.verdict(mixed), "setup", outcome)


class ErrorLinesTests(unittest.TestCase):
    def test_error_lines_context_and_cap(self):
        text = "\n".join("line %d" % i for i in range(5)) + "\nTypeError: boom\n" \
            + "\n".join("after %d" % i for i in range(5))
        got = quick.error_lines(text)
        self.assertEqual(got, ["line 3", "line 4", "TypeError: boom", "after 0", "after 1"])
        many = "\n".join("error number %d" % i for i in range(60))
        self.assertEqual(len(quick.error_lines(many)), 40)
        self.assertEqual(len(quick.error_lines(many, cap=5)), 5)

    def test_error_lines_markers(self):
        for marker in ("FAIL x", "failed y", "\u2717 z", "\u00d7 z", "AssertionError: q", "ERROR"):
            self.assertTrue(quick.error_lines("ok\n" + marker), marker)
        self.assertEqual(quick.error_lines("all good\nstill fine"), [])
        self.assertEqual(quick.error_lines(""), [])

    def test_error_lines_context_does_not_duplicate(self):
        got = quick.error_lines("error a\nerror b\nx")
        self.assertEqual(got, ["error a", "error b", "x"])

    def test_error_lines_tolerates_garbage(self):
        junk = ("\ufffd" * 80 + "\n") * 60000  # about 5 MB of replacement characters
        self.assertEqual(quick.error_lines(junk), [])
        noisy = (("\ufffd" * 40 + " error\n") * 60000)
        self.assertEqual(len(quick.error_lines(noisy)), 40)
        self.assertEqual(quick.error_lines(None), [])
        self.assertLessEqual(len(quick.error_lines("error " + "x" * 5_000_000)[0]), 400)


class SummaryTests(unittest.TestCase):
    def test_summary_shows_errors_only_for_red(self):
        results = [FakeResult("lint", "ok", 0, 120),
                   FakeResult("testOne:a.spec.ts", "failed", 1, 3400)]
        outputs = {"lint": "error: this lint line must not show",
                   "testOne:a.spec.ts": "ok\nAssertionError: expected 1\nat x"}
        text = quick.summary(results, outputs, ["sin tests relacionados"])
        lines = text.splitlines()
        self.assertEqual(lines[0], "verdict: red")
        self.assertIn("lint exit=0 120ms", lines)
        self.assertIn("testOne:a.spec.ts exit=1 3400ms", lines)
        self.assertIn("AssertionError: expected 1", text)
        self.assertNotIn("must not show", text)
        self.assertEqual(lines[-1], "sin tests relacionados")

    def test_summary_green_and_missing_output(self):
        self.assertEqual(quick.summary([], {}, []), "verdict: green")
        text = quick.summary([FakeResult("test", "failed", 1, 5)], {}, [])
        self.assertIn("test exit=1 5ms", text)
        none_exit = quick.summary([FakeResult("lint", "timeout", None, 900000)], {}, [])
        self.assertIn("verdict: setup", none_exit)
        self.assertIn("lint exit=- 900000ms", none_exit)


class SetupReasonTests(unittest.TestCase):
    def test_a_setup_slot_says_why(self):
        for outcome, why in (("timeout", "timed out after 900s -- the whole process group was killed"),
                             ("not_invocable", "shell exit 127: command not found or not executable"),
                             ("inconclusive", "exit 2, output matched a known wrapper-runner message")):
            text = quick.summary([FakeResult("lint", outcome, None, 5, error=why)], {}, [])
            self.assertIn("verdict: setup", text)
            self.assertIn("%s: %s" % (outcome, why), text)

    def test_ok_and_failed_slots_print_no_reason(self):
        text = quick.summary([FakeResult("lint", "ok", 0, 5, error="stale"),
                              FakeResult("test", "failed", 1, 5, error="exit 1")], {}, [])
        self.assertNotIn("stale", text)
        self.assertNotIn("exit 1\n", text + "\n")


class PendingAuditSummaryTests(unittest.TestCase):
    def test_a_green_gate_with_an_audit_to_come_is_not_a_verdict_yet(self):
        text = quick.summary([FakeResult("lint", "ok", 0, 5)], {}, [], audit_pending=True)
        self.assertEqual(text.splitlines()[0], "gate: green \u2014 auditoría pendiente")
        self.assertNotIn("verdict:", text)

    def test_a_red_or_setup_gate_keeps_its_verdict(self):
        red = quick.summary([FakeResult("lint", "failed", 1, 5)], {}, [], audit_pending=True)
        self.assertEqual(red.splitlines()[0], "verdict: red")
        setup = quick.summary([FakeResult("lint", "timeout", None, 5)], {}, [], audit_pending=True)
        self.assertEqual(setup.splitlines()[0], "verdict: setup")

    def test_default_is_the_plain_verdict(self):
        self.assertEqual(quick.summary([], {}, []), "verdict: green")


class AuditDecisionTests(unittest.TestCase):
    CFG = ship_decide.ShipConfig()

    def test_needs_audit_risky_only(self):
        self.assertTrue(quick.needs_audit(["src/auth/login.py"], self.CFG))
        big_safe = ["src/f%d.py" % i for i in range(self.CFG.large_change_files + 5)]
        self.assertFalse(quick.needs_audit(big_safe, self.CFG))
        self.assertFalse(quick.needs_audit([], self.CFG))

    def test_reviewer_choices(self):
        cool = {"session": 1.0, "weekly": 1.0}
        hot = {"session": 95.0, "weekly": 1.0}
        self.assertEqual(quick.reviewer({"claude": cool, "codex": cool}), quota.OPUS)
        self.assertEqual(quick.reviewer({}), quota.OPUS)
        self.assertEqual(quick.reviewer({"claude": hot, "codex": cool}), quota.CODEX)
        self.assertIsNone(quick.reviewer({"claude": hot, "codex": hot}))


if __name__ == "__main__":
    unittest.main()
