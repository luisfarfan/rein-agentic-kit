from settings import ENABLED_CURRENCIES
from ledger import record

def checkout(amount, currency):
    if currency not in ENABLED_CURRENCIES:
        raise ValueError('disabled currency')
    return record(amount, currency)
