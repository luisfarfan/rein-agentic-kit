"""Contract tests over real files; supplied labels are NOT model integration evidence."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/rein/lib'))
import impact
import evidence

FIXTURES = ROOT / 'tests/fixtures/discovery'


def case(name, root=None):
    root = Path(root or FIXTURES / name)
    refs = {}
    for path in sorted(root.glob('*')):
        if path.is_file():
            refs[path.name] = {'path': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                               'lines': [1, len(path.read_text().splitlines())]}
    ids = list(refs)
    def claim(text):
        return {'claim': text, 'kind': 'observed', 'evidence': ids}
    r = {'schema_version': 2, 'request': 'Agregar soporte para euros', 'evidence': refs,
         'classification': {k: {'level': 'low', 'reason': 'bounded path inspected', 'evidence': ids}
                            for k in impact.CLASSIFICATIONS},
         'findings': {'existing': [claim('Existing behavior inspected')],
                      'missing': [], 'dependencies': [claim('Entry point to local consumer inspected')], 'risks': []},
         'questions': [], 'investigation': {'steps': 3, 'seconds': 10}}
    if name == 'easy':
        r['findings']['existing'] = [claim('EUR supported; USD enabled')]
        r['findings']['missing'] = [claim('Enable EUR and add its negative/positive acceptance checks')]
    if name in ('hidden', 'complex'):
        for key in ('risk', 'independent_verification'):
            r['classification'][key]['level'] = 'high'
        facets = ['money'] if name == 'hidden' else ['contracts', 'persistence', 'money']
        r['risk_facets'] = {k: {'level': 'high', 'evidence': ids} for k in facets}
        r['findings']['risks'] = [claim('USD-only persisted monetary meaning must be preserved')]
    if name == 'complex':
        for key in ('implementation', 'repo_scope', 'planning'):
            r['classification'][key]['level'] = 'high'
        r['findings']['dependencies'] = [claim('API Invoice amount -> client Invoice type; database rows imply USD')]
    if name == 'ambiguous':
        r['classification']['uncertainty']['level'] = 'high'
        r['questions'] = [{'question': 'Which adapter is deployed?', 'next_check': 'Read approved deployment configuration and adapter contract'}]
    return r


class DiscoveryV2Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'repo'
        shutil.copytree(FIXTURES / 'easy', self.root)
        self.report = case('easy', self.root)
        self.roots = {'.': self.root}
        self.addCleanup(self.tmp.cleanup)

    def test_same_request_has_four_distinct_evidenced_outcomes(self):
        results = {name: impact.assess(case(name), {'.': FIXTURES / name})
                   for name in ('easy', 'hidden', 'complex', 'ambiguous')}
        self.assertEqual(results['easy']['discovery'], 'sufficient')
        self.assertEqual(results['hidden']['classification']['implementation']['level'], 'low')
        self.assertEqual(results['hidden']['classification']['risk']['level'], 'high')
        self.assertEqual(results['complex']['classification']['repo_scope']['level'], 'high')
        self.assertEqual(results['complex']['classification']['planning']['level'], 'high')
        self.assertEqual(results['ambiguous']['discovery'], 'deeper')
        self.assertEqual(results['ambiguous']['next_action'], 'targeted_check')
        for r in results.values():
            self.assertNotIn('confidence', r)
            self.assertEqual(len(r['classification']), 6)

    def test_no_roots_never_makes_v2_ready(self):
        self.assertEqual(impact.assess(self.report)['discovery'], 'deeper')

    def test_stale_evidence_invalidates_all_dependent_conclusions(self):
        self.assertEqual(impact.assess(self.report, self.roots)['discovery'], 'sufficient')
        p = self.root / 'settings.py'
        p.write_text(p.read_text() + '\n# changed\n')
        r = impact.assess(self.report, self.roots)
        self.assertEqual(r['evidence']['settings.py']['status'], 'stale')
        self.assertEqual(r['classification']['risk']['level'], 'unknown')
        self.assertEqual(r['discovery'], 'deeper')

    def test_missing_unknown_ids_and_bad_locations_never_authorize(self):
        for change in ('missing', 'hash', 'range', 'symbol', 'id'):
            with self.subTest(change=change):
                r = copy.deepcopy(self.report)
                ref = r['evidence']['settings.py']
                if change == 'missing': ref['path'] = 'absent.py'
                if change == 'hash': ref['sha256'] = 'invented'
                if change == 'range': ref['lines'] = [1, 999]
                if change == 'symbol': ref['symbol'] = 'imaginary_function'
                if change == 'id': r['classification']['risk']['evidence'] = ['unknown-id']
                self.assertEqual(impact.assess(r, self.roots)['discovery'], 'deeper')

    def test_explicit_second_repo_root_is_required(self):
        self.report['evidence']['settings.py']['repo'] = 'api'
        self.assertEqual(impact.assess(self.report, self.roots)['discovery'], 'deeper')
        self.assertEqual(impact.assess(self.report, dict(self.roots, api=self.root))['discovery'], 'sufficient')

    def test_outside_and_symlink_escape_are_not_read(self):
        outside = Path(self.tmp.name) / 'outside.py'
        outside.write_text('secret = 1\n')
        (self.root / 'escape.py').symlink_to(outside)
        for path in ('../outside.py', 'escape.py', str(outside)):
            ref = {'path': path, 'sha256': hashlib.sha256(outside.read_bytes()).hexdigest(), 'lines': [1, 1]}
            with patch.object(Path, 'open', side_effect=AssertionError('must not read outside')):
                self.assertEqual(evidence.check({'x': ref}, self.roots)['x']['status'], 'invalid')

    def test_duplicate_references_read_once_and_never_run_commands(self):
        refs = {'a': self.report['evidence']['settings.py'], 'b': self.report['evidence']['settings.py']}
        original, reads = Path.open, []
        def opened(path, *args, **kwargs):
            reads.append(str(path))
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', opened), patch('subprocess.run', side_effect=AssertionError('no execution')):
            result = evidence.check(refs, self.roots)
        self.assertEqual(len(reads), 1)
        self.assertTrue(all(v['status'] == 'current' for v in result.values()))

    def test_budget_exhaustion_reports_unknown_instead_of_research_loop(self):
        r = case('ambiguous')
        r['investigation']['steps'] = 8
        out = impact.assess(r, {'.': FIXTURES / 'ambiguous'})
        self.assertEqual(out['next_action'], 'report_unknowns')
        self.assertTrue(out['investigation']['exhausted'])

    def test_risk_contradiction_and_unsubstantiated_findings_block(self):
        r = case('hidden')
        r['classification']['risk']['level'] = 'low'
        self.assertIn('risk_contradiction', impact.assess(r, {'.': FIXTURES / 'hidden'})['unknowns'])
        self.report['findings']['missing'] = [{'claim': 'Unsupported guess', 'kind': 'inference', 'evidence': []}]
        self.assertIn('findings.missing', impact.assess(self.report, self.roots)['unknowns'])

    def test_empty_or_invalid_reports_do_not_pass(self):
        self.assertEqual(impact.assess({'schema_version': 2})['discovery'], 'deeper')
        for key, value in [('classification', {'typo': {}}), ('questions', ['vague']),
                           ('investigation', {'seconds': float('nan')}), ('evidence', []),
                           ('investigation', {'limit_steps': None}),
                           ('schema_version', 2.0),
                           ('schema_version', 99)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                impact.assess(dict(self.report, **{key: value}), self.roots)

    def test_cli_is_read_only_and_reports_stale_exit(self):
        report = Path(self.tmp.name) / 'report.json'
        report.write_text(json.dumps(self.report))
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        args = [sys.executable, str(ROOT / 'plugins/rein/bin/rein'), 'discover-check', str(report), '--root', str(self.root)]
        p = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})
        (self.root / 'settings.py').unlink()
        p = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(p.returncode, 1, p.stderr)
        self.assertEqual(json.loads(p.stdout)['discovery'], 'deeper')
