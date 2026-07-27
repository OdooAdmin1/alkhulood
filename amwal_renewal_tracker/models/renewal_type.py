# -*- coding: utf-8 -*-
from odoo import fields, models


class RenewalType(models.Model):
    _name = 'renewal.type'
    _description = 'Renewal Document Type'
    _order = 'name'

    name = fields.Char(string='Type', required=True, help='e.g. CR Renewal, Insurance, Certificate, License')
    reminder_days = fields.Char(
        string='Reminder Days',
        default='30,15,7,1',
        help='Comma-separated list of days before expiry to send a reminder email, e.g. 30,15,7,1'
    )
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company)
