from decimal import Decimal

def invoice(amount):
    # Legacy public contract: no currency field, USD implicit.
    return {'amount': str(Decimal(amount))}
