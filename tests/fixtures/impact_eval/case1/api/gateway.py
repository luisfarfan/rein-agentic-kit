class FiscalIssuer:
    def __init__(self, transport, endpoint):
        self.transport, self.endpoint = transport, endpoint

    async def issue(self, business_id, number, payload):
        return await self.transport.post(self.endpoint, {
            'business': business_id, 'number': number, **payload})
# Transport timeout and retry policy are supplied by deployment configuration.
