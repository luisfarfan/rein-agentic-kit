import unittest
from decimal import Decimal
from unittest.mock import patch

import checkout
import ledger


class CurrencyBoundaryTests(unittest.TestCase):
    def setUp(self):
        # Keep each test's ledger separate from any pre-existing entries.
        ledger_patch = patch.object(ledger, 'ENTRIES', [])
        ledger_patch.start()
        self.addCleanup(ledger_patch.stop)

    def test_usd_checkout_preserves_amount_and_currency(self):
        amount = Decimal('12.50')
        entry = checkout.checkout(amount, 'USD')
        self.assertEqual(entry, {'amount': amount, 'currency': 'USD'})
        self.assertEqual(ledger.ENTRIES, [entry])

    def test_eur_is_disabled_without_writing(self):
        with self.assertRaisesRegex(ValueError, '^disabled currency$'):
            checkout.checkout(Decimal('12.50'), 'EUR')
        self.assertEqual(ledger.ENTRIES, [])

    def test_direct_non_usd_records_leave_existing_entries_unchanged(self):
        existing = ledger.record(Decimal('7.25'), 'USD')
        for currency in ('EUR', 'GBP', '', None):
            with self.subTest(currency=currency):
                with self.assertRaisesRegex(
                    ValueError, '^unsupported settlement currency$'
                ):
                    ledger.record(Decimal('12.50'), currency)
                self.assertEqual(ledger.ENTRIES, [existing])

    def test_enabling_eur_cannot_mislabel_euros_as_usd(self):
        with patch.object(checkout, 'ENABLED_CURRENCIES', ('USD', 'EUR')):
            with self.assertRaisesRegex(
                ValueError, '^unsupported settlement currency$'
            ):
                checkout.checkout(Decimal('12.50'), 'EUR')
        self.assertEqual(ledger.ENTRIES, [])


if __name__ == '__main__':
    unittest.main()
