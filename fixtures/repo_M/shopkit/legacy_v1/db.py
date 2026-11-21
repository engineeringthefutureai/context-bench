# tiny "database" layer used by v1 before the move to services.
# tables are dicts of dicts kept in module globals.

TABLES = {'products': {}, 'customers': {}, 'orders': {}}
_nextId = {'products': 1, 'customers': 1, 'orders': 1}


def insert(table, record):
    global _nextId
    rid = _nextId[table]
    _nextId[table] = rid + 1
    record['id'] = rid
    TABLES[table][rid] = record
    return rid


def select(table, where=None):
    rows = []
    for rid in TABLES[table]:
        rec = TABLES[table][rid]
        ok = True
        if where:
            for k in where:
                if rec.get(k) != where[k]:
                    ok = False
        if ok:
            rows.append(rec)
    return rows


def update(table, rid, values):
    if rid not in TABLES[table]:
        return False
    for k in values:
        TABLES[table][rid][k] = values[k]
    return True


def delete(table, rid):
    try:
        del TABLES[table][rid]
        return True
    except:
        return False


def getCustomerByEmail(email):
    res = select('customers', {'email': email.lower()})
    if len(res) > 0:
        return res[0]
    return None


def getProductBySku(sku):
    res = select('products', {'sku': sku})
    if len(res) == 0:
        return None
    return res[0]
