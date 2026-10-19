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

REPRODUCE = "Primero escribe un test que lo reproduzca."
DONE = "Cuando diga *listo*, ejecuta `rein quick done` y muéstrame el resultado."
LINEAR = "Incluye PPR-123 en el mensaje de commit."
NO_ID = "Commit convencional sin ID de issue: no inventes uno ni lo preguntes."


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
        brief = quick.build_brief(sentence, linear="PPR-123", bug=True)
        marks = [
            sentence,
            "Iteramos juntos: propón, aplica, y espera mi visto bueno antes de seguir.",
            REPRODUCE,
            DONE,
            "No hagas push, merge ni PR salvo que yo lo pida; "
            "si la rama destino es main, pide confirmación antes.",
            LINEAR,
            "No leas `.env*` ni credenciales.",
        ]
        positions = [brief.index(m) for m in marks]
        self.assertEqual(positions, sorted(positions))
        self.assertTrue(brief.startswith(sentence))

    def test_brief_word_budget(self):
        sentence = " ".join(["palabra"] * 40)
        for bug in (False, True):
            for linear in ("", "PPR-123"):
                brief = quick.build_brief(sentence, linear=linear, bug=bug)
                self.assertLessEqual(len(brief.split()), 150)
                self.assertIn(sentence, brief)


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


if __name__ == "__main__":
    unittest.main()
