import os

def provider_name():
    return os.environ.get('PAYMENT_PROVIDER')

# The deployed provider and its currency contract are outside this checkout.
def charge(adapter, amount, currency):
    return adapter.charge(amount=amount, currency=currency)
