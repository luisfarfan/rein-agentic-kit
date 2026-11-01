SUPPORTED_CURRENCIES = ('USD', 'EUR')
ENABLED_CURRENCIES = ('USD',)

def enabled(currency):
    return currency in ENABLED_CURRENCIES
