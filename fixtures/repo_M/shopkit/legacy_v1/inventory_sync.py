# nightly inventory sync with the old warehouse system (flat file, pipe-separated)
# line format: SKU|QTY|LOCATION

from shopkit.legacy_v1.StringUtils import cleanSKU

LOW_STOCK = 3
stockLevels = {}
syncErrors = []


def parseLine(line):
    parts = line.strip().split('|')
    if len(parts) != 3:
        return None
    try:
        return (cleanSKU(parts[0]), int(parts[1]), parts[2])
    except:
        return None


def syncFile(contents):
    global stockLevels, syncErrors
    syncErrors = []
    count = 0
    lineNo = 0
    for line in contents.split('\n'):
        lineNo = lineNo + 1
        if line.strip() == '' or line.startswith('#'):
            continue
        rec = parseLine(line)
        if rec == None:
            syncErrors.append('bad line %d: %s' % (lineNo, line))
            continue
        sku, qty, loc = rec
        stockLevels[sku] = stockLevels.get(sku, 0) + qty
        count = count + 1
    return count


def getLowStock():
    result = []
    for sku in sorted(stockLevels.keys()):
        if stockLevels[sku] <= LOW_STOCK:
            result.append(sku)
    return result


def splitIntoBatches(lst, n):
    batches = []
    i = 0
    while i < len(lst):
        batches.append(lst[i:i + n])
        i = i + n
    return batches
