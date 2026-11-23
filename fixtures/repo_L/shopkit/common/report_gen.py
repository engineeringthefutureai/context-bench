# report generation - builds HTML tables for the admin panel

from shopkit.common.StringUtils import formatPrice, padLeft


class ReportGenerator(object):

    def __init__(self, title):
        self.title = title
        self.rows = []
        self.headers = []

    def setHeaders(self, headers):
        self.headers = headers

    def addRow(self, row):
        self.rows.append(row)

    def getRowCount(self):
        return len(self.rows)

    def toHtml(self):
        html = '<h1>' + self.title + '</h1>\n<table border="1">\n<tr>'
        for h in self.headers:
            html = html + '<th>' + h + '</th>'
        html = html + '</tr>\n'
        for row in self.rows:
            html = html + '<tr>'
            for cell in row:
                html = html + '<td>' + str(cell) + '</td>'
            html = html + '</tr>\n'
        html = html + '</table>'
        return html

    def toText(self):
        out = self.title + '\n'
        for row in self.rows:
            out = out + ' | '.join([padLeft(c, 10) for c in row]) + '\n'
        return out


def salesReport(orders):
    """orders: list of order dicts (see OrderManager)"""
    rg = ReportGenerator('Sales Report')
    rg.setHeaders(['Order', 'Items', 'Total'])
    grandTotal = 0.0
    for o in orders:
        total = 0.0
        for item in o['items']:
            total += item['qty'] * item['price']
        grandTotal += total
        rg.addRow([o['orderId'], len(o['items']), formatPrice(total)])
    rg.addRow(['TOTAL', '', formatPrice(grandTotal)])
    return rg


def paginateList(lst, pageNum, pageSize=20):
    """pageNum starts at 0!"""
    start = pageNum * pageSize
    return lst[start:start + pageSize]
