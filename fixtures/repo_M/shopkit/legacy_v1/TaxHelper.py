# TaxHelper - flat tax rates per state (v1). Rates as fractions.

STATE_TAX = {
    'CA': 0.0725, 'NY': 0.04, 'TX': 0.0625, 'WA': 0.065, 'FL': 0.06, 'IL': 0.0625,
}
DEFAULT_TAX = 0.0


def getTaxRate(state):
    if state in STATE_TAX:
        return STATE_TAX[state]
    return DEFAULT_TAX


def calcTax(amount, state):
    """amount in dollars (float); returns tax in dollars, rounded to cents"""
    return round(amount * getTaxRate(state), 2)


def addTax(amount, state):
    return amount + calcTax(amount, state)


def isTaxFree(state):
    return getTaxRate(state) == 0
