# shipping cost calculation (v1)
# weights are in POUNDS, prices returned as float dollars

FREE_SHIPPING_LIMIT = 75.0


def getShippingCost(country, weightLbs, orderTotal=0):
    country = country.upper()
    if country == 'US' or country == 'USA':
        if orderTotal >= FREE_SHIPPING_LIMIT:
            return 0.0
        if weightLbs <= 1:
            return 4.95
        elif weightLbs <= 5:
            return 7.95
        elif weightLbs <= 20:
            return 14.95
        else:
            return 14.95 + (weightLbs - 20) * 0.5
    elif country == 'CA' or country == 'MX':
        if weightLbs <= 1:
            return 12.95
        elif weightLbs <= 5:
            return 21.95
        else:
            return 44.95
    elif country in ['GB', 'UK', 'DE', 'FR', 'NL', 'IE', 'ES', 'IT']:
        if weightLbs <= 1:
            return 14.95
        elif weightLbs <= 5:
            return 24.95
        else:
            return 54.95
    else:
        if weightLbs <= 1:
            return 19.95
        return 79.95


def getZone(country):
    country = country.upper()
    if country in ['US', 'USA']:
        return 1
    if country in ['CA', 'MX']:
        return 2
    if country in ['GB', 'UK', 'DE', 'FR', 'NL', 'IE', 'ES', 'IT']:
        return 3
    return 4


def kgToLbs(kg):
    return kg * 2.2


def estimateDays(country):
    zone = getZone(country)
    days = {1: 3, 2: 7, 3: 10, 4: 14}
    return days[zone]
