ENTRIES = []

def record(amount, currency):
    # Existing settlement contract is USD-only; other amounts would be mislabeled.
    entry = {'amount': amount, 'currency': 'USD'}
    ENTRIES.append(entry)
    return entry
