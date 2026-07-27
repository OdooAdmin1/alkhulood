# -*- coding: utf-8 -*-
from datetime import date, timedelta

from odoo import api, fields, models


class RenewalTracker(models.Model):
    _name = 'renewal.tracker'
    _description = 'Renewal Tracker'
    _order = 'expiry_date'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = 'name'

    name = fields.Char(string='Reference', required=True, tracking=True,
                        help='Short name for this renewal, e.g. "CR Renewal - Al Khulood Sweets"')
    renewal_type_id = fields.Many2one('renewal.type', string='Renewal Type', required=True, tracking=True)
    partner_id = fields.Many2one('res.partner', string='Client', tracking=True)
    responsible_id = fields.Many2one('res.users', string='Responsible', default=lambda self: self.env.user,
                                      tracking=True)

    issue_date = fields.Date(string='Issue Date')
    expiry_date = fields.Date(string='Expiry Date', required=True, tracking=True)

    days_remaining = fields.Integer(string='Days Remaining', compute='_compute_days_remaining',
                                     store=True, help='Negative means already expired')

    state = fields.Selection([
        ('valid', 'Valid'),
        ('expiring', 'Expiring Soon'),
        ('expired', 'Expired'),
    ], string='Status', compute='_compute_state', store=True, tracking=True)

    expiring_soon_threshold = fields.Integer(
        string='"Expiring Soon" Threshold (days)', default=30,
        help='Record is marked Expiring Soon when days remaining falls below this number')

    reminder_sent_days = fields.Char(string='Reminders Sent', readonly=True, copy=False,
                                      help='Internal: comma separated list of reminder day marks already sent')

    document = fields.Binary(string='Renewal Document', attachment=True)
    document_name = fields.Char(string='Document Filename')

    notes = fields.Text(string='Notes')
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company)

    color = fields.Integer(string='Color', compute='_compute_color', store=False)

    @api.depends('expiry_date')
    def _compute_days_remaining(self):
        today = date.today()
        for rec in self:
            if rec.expiry_date:
                rec.days_remaining = (rec.expiry_date - today).days
            else:
                rec.days_remaining = 0

    @api.depends('days_remaining', 'expiring_soon_threshold')
    def _compute_state(self):
        for rec in self:
            if rec.days_remaining < 0:
                rec.state = 'expired'
            elif rec.days_remaining <= rec.expiring_soon_threshold:
                rec.state = 'expiring'
            else:
                rec.state = 'valid'

    @api.depends('state')
    def _compute_color(self):
        color_map = {'valid': 10, 'expiring': 3, 'expired': 1}  # green, orange, red
        for rec in self:
            rec.color = color_map.get(rec.state, 0)

    def _get_reminder_day_list(self):
        self.ensure_one()
        days_str = self.renewal_type_id.reminder_days or ''
        result = []
        for chunk in days_str.split(','):
            chunk = chunk.strip()
            if chunk.isdigit():
                result.append(int(chunk))
        return sorted(result, reverse=True)

    def _cron_update_states_and_send_reminders(self):
        """Called daily by scheduled action: refreshes computed fields and
        sends reminder emails for any threshold crossed today."""
        records = self.search([('active', '=', True)])
        template = self.env.ref('amwal_renewal_tracker.mail_template_renewal_reminder', raise_if_not_found=False)

        for rec in records:
            already_sent = set()
            if rec.reminder_sent_days:
                already_sent = {d.strip() for d in rec.reminder_sent_days.split(',') if d.strip()}

            for threshold in rec._get_reminder_day_list():
                marker = str(threshold)
                if rec.days_remaining <= threshold and marker not in already_sent and rec.days_remaining >= 0:
                    if template:
                        template.send_mail(rec.id, force_send=True)
                    # log on chatter regardless of email template availability
                    rec.message_post(
                        body=(f"Renewal reminder: '{rec.name}' expires in {rec.days_remaining} day(s) "
                              f"(on {rec.expiry_date}).")
                    )
                    already_sent.add(marker)

            if rec.days_remaining < 0 and 'expired' not in already_sent:
                rec.message_post(body=f"'{rec.name}' has EXPIRED on {rec.expiry_date}.")
                already_sent.add('expired')

            rec.reminder_sent_days = ','.join(sorted(already_sent))

    @api.model
    def get_dashboard_counts(self):
        recs = self.search([('active', '=', True)])
        return {
            'valid': len(recs.filtered(lambda r: r.state == 'valid')),
            'expiring': len(recs.filtered(lambda r: r.state == 'expiring')),
            'expired': len(recs.filtered(lambda r: r.state == 'expired')),
            'total': len(recs),
        }
