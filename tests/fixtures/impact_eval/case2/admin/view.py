from menu import VISIBLE_WITH

def show_approvals(user):
    return bool(set(user['permissions']) & set(VISIBLE_WITH))

def approval_request(proposals, grant_key):
    return {'method': 'POST', 'path': '/approvals/batch',
            'headers': {'X-Grant-Key': grant_key},
            'body': {'proposal_ids': [p['id'] for p in proposals]}}
