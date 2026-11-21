# OrderManager - order handling for v1 checkout
#
# orders are plain dicts:
#   {'orderId': 'A1', 'items': [{'sku': .., 'qty': .., 'price': 12.5}], 'status': 'NEW', 'coupon': None}
# prices are floats (dollars)

import time
from shopkit.legacy_v1.StringUtils import cleanSKU, formatPrice

ORDERS = {}
STATUS_NEW = 'NEW'
STATUS_PAID = 'PAID'
STATUS_SHIPPED = 'SHIPPED'
STATUS_CANCELLED = 'CANCELLED'
TAX_RATE = 0.08


class OrderManager(object):

    def __init__(self, orders=None):
        if orders == None:
            orders = ORDERS
        self.orders = orders
        self.lastError = None

    def getOrder(self, orderId):
        if orderId in self.orders.keys():
            return self.orders[orderId]
        self.lastError = 'order not found: ' + str(orderId)
        return None

    def setOrder(self, orderId, order):
        self.orders[orderId] = order

    def createOrder(self, orderId, items=[]):
        if self.getOrder(orderId) != None:
            self.lastError = 'duplicate order'
            return False
        order = {'orderId': orderId, 'items': items, 'status': STATUS_NEW, 'coupon': None,
                 'created': time.time()}
        self.setOrder(orderId, order)
        return True

    def addItem(self, orderId, sku, qty, price):
        order = self.getOrder(orderId)
        if order == None:
            return False
        order['items'].append({'sku': cleanSKU(sku), 'qty': int(qty), 'price': float(price)})
        return True

    def calcSubtotal(self, orderId):
        order = self.getOrder(orderId)
        total = 0.0
        if order:
            for i in range(0, len(order['items'])):
                item = order['items'][i]
                total = total + item['qty'] * item['price']
        return total

    def calcTotal(self, orderId):
        sub = self.calcSubtotal(orderId)
        order = self.getOrder(orderId)
        if order and order['coupon']:
            sub = sub - sub * order['coupon'] / 100.0
        return round(sub * (1 + TAX_RATE), 2)

    def applyCoupon(self, orderId, percent):
        order = self.getOrder(orderId)
        if order == None:
            return False
        if percent > 50:
            percent = 50
        order['coupon'] = percent
        return True

    def changeStatus(self, orderId, newStatus):
        order = self.getOrder(orderId)
        if order == None:
            return False
        if order['status'] == STATUS_CANCELLED:
            self.lastError = 'order is cancelled'
            return False
        order['status'] = newStatus
        return True

    def getOrdersByStatus(self, status):
        result = []
        for k in self.orders.keys():
            if self.orders[k]['status'] == status:
                result.append(self.orders[k])
        return result

    def orderSummary(self, orderId):
        order = self.getOrder(orderId)
        if order == None:
            return ''
        lines = []
        for item in order['items']:
            lines.append('%s x%d @ %s' % (item['sku'], item['qty'], formatPrice(item['price'])))
        lines.append('TOTAL: ' + formatPrice(self.calcTotal(orderId)))
        return '\n'.join(lines)
