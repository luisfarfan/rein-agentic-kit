AREA_PERMISSION = {'inventory': 'inventory:manage', 'pricing': 'pricing:manage'}

def allowed(user, proposal, grant_key):
    return (user['business_id'] == proposal['business_id']
            and AREA_PERMISSION[proposal['area']] in user['permissions']
            and grant_key == proposal['grant_key'])
