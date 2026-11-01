"""Shared provider wiring; live discovery has a separate opt-in smoke probe."""
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'plugins/rein'


class NativeSkillsTest(unittest.TestCase):
    def test_links_resolve_to_canonical_skills_and_cli(self):
        canonical = sorted((PLUGIN / 'skills').glob('*/SKILL.md'))
        self.assertEqual(len(canonical), 7)
        for skill in canonical:
            link = ROOT / '.agents/skills' / skill.parent.name
            self.assertTrue(link.is_symlink())
            resolved = (link / 'SKILL.md').resolve(strict=True)
            self.assertEqual(resolved, skill)
            self.assertEqual((link / 'IMPACT.md').read_text(), (PLUGIN / 'IMPACT.md').read_text())
            relative = re.search(r'\[provider setup\]\(([^)]+)\)', (link / 'SKILL.md').read_text()).group(1)
            # Hosts may normalize a Markdown reference before following symlinks.
            literal_target = Path(os.path.normpath(str(link / relative)))
            self.assertEqual(literal_target.read_text(), (PLUGIN / 'PROVIDERS.md').read_text())
            plugin = next(parent for parent in resolved.parents if (parent / 'bin/rein').is_file())
            proc = subprocess.run([sys.executable, str(plugin / 'bin/rein'),
                                   'role', 'reviewer'], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn('never review your own implementation', proc.stdout)

    def test_both_manifests_use_one_skill_tree(self):
        for provider in ('claude', 'codex'):
            manifest = json.loads((PLUGIN / ('.%s-plugin' % provider) / 'plugin.json').read_text())
            skills = manifest.get('skills', './skills/')
            self.assertEqual((PLUGIN / skills).resolve(), PLUGIN / 'skills')

    def test_claude_imports_shared_project_instructions(self):
        self.assertEqual((ROOT / 'CLAUDE.md').read_text().strip(), '@AGENTS.md')
        self.assertTrue((ROOT / 'AGENTS.md').is_file())
