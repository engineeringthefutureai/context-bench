# CustomerUtils - customer helpers from the v1 account pages

from shopkit.legacy_v1 import db
from shopkit.legacy_v1.StringUtils import isEmail, toTitleCase

VIP_THRESHOLD = 1000.0      # lifetime spend, dollars
_cache = {}


def registerCustomer(email, name, newsletter=0):
    if not isEmail(email):
        return -1
    if db.getCustomerByEmail(email) != None:
        return -2
    rec = {'email': email.lower(), 'name': toTitleCase(name), 'newsletter': newsletter,
           'spend': 0.0, 'addresses': []}
    return db.insert('customers', rec)


def getCustomer(cid):
    if cid in _cache:
        return _cache[cid]
    res = db.select('customers', {'id': cid})
    if len(res) == 0:
        return None
    _cache[cid] = res[0]
    return res[0]


def formatAddressLines(addr):
    """addr is a dict with keys name, street, city, state, zip, country"""
    out = addr['name'] + '\n' + addr['street'] + '\n'
    out = out + addr['city'] + ', ' + addr.get('state', '') + ' ' + addr['zip'] + '\n'
    out = out + addr.get('country', 'USA')
    return out


def isVip(cid):
    c = getCustomer(cid)
    if c == None:
        return False
    return c['spend'] >= VIP_THRESHOLD


def maskCard(cardNo):
    return 'XXXX-XXXX-XXXX-' + cardNo[-4:]


def addSpend(cid, amount):
    c = getCustomer(cid)
    if c:
        c['spend'] = c['spend'] + amount
        db.update('customers', cid, {'spend': c['spend']})
