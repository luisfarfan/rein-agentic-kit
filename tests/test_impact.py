import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/rein/lib'))
import impact


class ImpactTest(unittest.TestCase):
    def test_missing_or_unsupported_claim_never_becomes_low_risk(self):
        for report in ({}, {'dimensions': {'security': {'level': 'low'}}},
                       {'dimensions': {'money': {'level': 'not_applicable'}}}):
            result = impact.assess(report)
            self.assertEqual(result['confidence'], 'UNKNOWN')
            self.assertIn('security', result['unknowns'])
            self.assertIn('money', result['unknowns'])

    def test_supported_currency_and_hidden_settlement_impact_differ(self):
        dimensions = {key: {'level': 'low', 'evidence': ['config.py:4; tests/test_config.py:12']} for key in impact.DIMENSIONS}
        small = impact.assess({'dimensions': dimensions})
        self.assertEqual(small['discovery'], 'sufficient')
        dimensions['money'] = {'level': 'high', 'evidence': ['settlement.py:63 conversion persisted in ledger']}
        large = impact.assess({'dimensions': dimensions})
        self.assertEqual(large['risks'], ['money'])
        self.assertEqual(small['risks'], [])

    def test_rejects_malformed_claims(self):
        for item in ({'level': 'trivial'}, {'evidence': 'trust me'}, {'evidence': ['']}):
            with self.assertRaises(ValueError):
                impact.assess({'dimensions': {'security': item}})
