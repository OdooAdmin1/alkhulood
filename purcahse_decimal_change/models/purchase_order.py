# -*- coding: utf-8 -*-

from odoo import models, fields, api


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    amount_total = fields.Float(string='Total',
                                store=True, readonly=True, compute='_amount_all', digits=(16, 4))
    curr = fields.Char(string='', default="BD", readonly=True)


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    price_unit = fields.Float(string='Unit Price',
                              required=True, digits=(16, 4))


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    @api.model
    def action_reset_account(self):
        result = self.search([('account_id', 'in', [5, 6]), ('matching_number', '!=', False)], limit=50000)
        for l in result:
            l.remove_move_reconcile()
        return True
