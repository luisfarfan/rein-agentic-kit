"""Shared provider wiring; live discovery has a separate opt-in smoke probe."""
import json
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
            guide = (resolved.parent / '../../PROVIDERS.md').resolve(strict=True)
            proc = subprocess.run([sys.executable, str(guide.parent / 'bin/rein'),
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
