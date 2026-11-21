# date helpers v1 - uses time tuples, not datetime
import time

SECONDS_PER_DAY = 86400


def addWorkDays(timestamp, days):
    """adds working days (mon-fri) to a unix timestamp"""
    t = timestamp
    added = 0
    while added < days:
        t = t + SECONDS_PER_DAY
        wd = time.localtime(t).tm_wday
        if wd < 5:
            added = added + 1
    return t


def formatDate(timestamp, fmt='%m/%d/%Y'):
    return time.strftime(fmt, time.localtime(timestamp))


def parseDate(s, fmt='%m/%d/%Y'):
    try:
        return time.mktime(time.strptime(s, fmt))
    except:
        return None


def weekNumber(timestamp):
    return int(time.strftime('%U', time.localtime(timestamp)))


def daysBetween(ts1, ts2):
    return int(abs(ts2 - ts1) / SECONDS_PER_DAY)
