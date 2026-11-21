# -*- coding: utf-8 -*-
#
# StringUtils - common string functions for the storefront
# author: dev team, 2017
#
import re
import string

MAX_SLUG_LEN = 60
DEFAULT_CURRENCY_SYMBOL = '$'


def makeSlug(strTitle, maxLen=MAX_SLUG_LEN):
    """makes url friendly slug from a title"""
    if strTitle == None:
        return ''
    s = strTitle.lower().strip()
    s = re.sub('[^a-z0-9 -]', '', s)
    s = s.replace(' ', '-')
    while '--' in s:
        s = s.replace('--', '-')
    if len(s) > maxLen:
        s = s[0:maxLen]
    return s


def cleanSKU(sku):
    """cleans up product code"""
    if not sku:
        return ''
    result = ''
    for ch in sku:
        if ch in string.ascii_letters or ch in string.digits or ch == '-':
            result = result + ch.upper()
    return result


def formatPrice(amount, symbol=DEFAULT_CURRENCY_SYMBOL):
    """amount is a float in dollars!! e.g. 12.5 -> $12.50"""
    try:
        return symbol + '%.2f' % float(amount)
    except:
        return symbol + '0.00'


def isEmail(strEmail):
    if strEmail == None or strEmail == '':
        return False
    if strEmail.find('@') < 1:
        return False
    if strEmail.find('.') == -1:
        return False
    return True


def truncateText(text, length=100, suffix='...'):
    if len(text) <= length:
        return text
    return text[:length - len(suffix)] + suffix


def toTitleCase(s):
    words = s.split(' ')
    newWords = []
    for w in words:
        if len(w) > 0:
            newWords.append(w[0].upper() + w[1:].lower())
    return ' '.join(newWords)


def stripHtml(html):
    return re.sub('<[^<]+?>', '', html)


def padLeft(s, n, ch=' '):
    s = str(s)
    while len(s) < n:
        s = ch + s
    return s
