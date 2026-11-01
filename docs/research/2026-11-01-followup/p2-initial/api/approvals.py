from policy import allowed

def approve_batch(user, proposals, grant_key):
    if not all(allowed(user, proposal, grant_key) for proposal in proposals):
        raise PermissionError('forbidden')
    return [{'id': p['id'], 'status': 'approved'} for p in proposals]
