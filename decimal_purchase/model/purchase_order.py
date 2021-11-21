# -*- coding: utf-8 -*-

from odoo import models, fields, api


class PurchaseOrder(models.Model):
	_inherit = 'purchase.order'
	
	amount_total = fields.Float(string='Total',
		store=True, readonly=True, compute='_amount_all', digits=(16,4))
	curr = fields.Char(string='', default="BD", readonly=True)

class PurchaseOrderLine(models.Model):
	_inherit = 'purchase.order.line'

	price_unit = fields.Float(string='Unit Price',
		required=True, digits=(16,4))
