async def emit_invoice(user, payload, headers, service):
    if 'invoicing:manage' not in user['permissions']:
        raise PermissionError('forbidden')
    business_id = user['business_id']
    return 201, await service.emit(business_id, payload)
