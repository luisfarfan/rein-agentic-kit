from store import Store

class InvoiceService:
    def __init__(self, store, issuer):
        self.store, self.issuer = store, issuer

    async def emit(self, business_id, payload):
        number = self.store.next_number(business_id)
        issued = await self.issuer.issue(business_id, number, payload)
        row = dict(business_id=business_id, number=number,
                   amount=payload['amount'], currency=payload['currency'],
                   provider_id=issued['id'])
        self.store.save(row)
        return row
