from odoo import api, fields, models

from .. import akf_report

# Short column names the client uses on the paper report. MDN is not certain: it is the only one of the client's nine
# columns left over, so it is given to Tubli. Change it under Report setup > Report branches.
DEFAULT_BRANCHES = [
    ('muharraq', 'MRQ', 10), ('hamad', 'HT', 20), ('tubli', 'MDN', 30), ('budaiya', 'BDY', 40), ('manama', 'MANAMA', 50),
    ('riffa', 'RIFFA', 60), ('jid', 'JIDALI', 70), ('baraha', 'BARAHA', 80), ('galali', 'GALALI', 90),
]


class AlkhuloodhSfReportBranch(models.Model):
    _name = 'alkhuloodh.sf.report.branch'
    _description = 'Forecast report: branch column'
    _order = 'sequence, id'

    sequence = fields.Integer(default=100)
    warehouse_id = fields.Many2one('stock.warehouse', string='Branch', required=True, ondelete='cascade')
    short_name = fields.Char(string='Column name', required=True,
                             help='Name shown at the top of the column, as on the client\'s report (MRQ, HT, MDN ...). '
                                  'The full branch name is printed under the table as a key.')
    show = fields.Boolean(string='Show in report', default=True)
    active = fields.Boolean(default=True, help='Archive a branch that should never appear here (for example a store room). '
                                              'It will not be added again.')

    @api.model
    def _ensure(self):
        """One row per branch that has sales (created on first use). Branches without sales in the last year start hidden."""
        cr = self.env.cr
        cr.execute("""SELECT warehouse_id, MAX(date) >= (CURRENT_DATE - 365) FROM alkhuloodh_sf_actual_daily
                       GROUP BY warehouse_id""")
        found = cr.fetchall()
        have = set(self.sudo().with_context(active_test=False).search([]).mapped('warehouse_id').ids)
        wh = {w.id: w for w in self.env['stock.warehouse'].sudo().browse([r[0] for r in found]).exists()}
        vals = []
        for wid, recent in found:
            if wid in have or wid not in wh:
                continue
            name = (wh[wid].name or '').lower()
            for key, short, seq in DEFAULT_BRANCHES:
                if key in name:
                    vals.append({'warehouse_id': wid, 'short_name': short, 'sequence': seq, 'show': bool(recent)})
                    break
            else:
                vals.append({'warehouse_id': wid, 'short_name': (wh[wid].name or str(wid)).upper()[:8],
                             'sequence': 200, 'show': bool(recent)})
        if vals:
            self.sudo().create(vals)

    @api.model
    def action_open(self):
        self._ensure()
        search = self.env.ref('alkhuloodh_sales_forecast.view_sf_report_branch_search', raise_if_not_found=False)
        return {'type': 'ir.actions.act_window', 'name': self.env._('Report branches'), 'res_model': self._name,
                'view_mode': 'list', 'target': 'current', 'search_view_id': search.id if search else False}


class AlkhuloodhSfReportLine(models.Model):
    _name = 'alkhuloodh.sf.report.line'
    _description = 'Forecast report: product row'
    _order = 'sequence, id'

    sequence = fields.Integer(default=10)
    label = fields.Char(string='Name on the report', required=True)
    product_id = fields.Many2one('product.product', string='Odoo product', context={'active_test': False})
    match_state = fields.Selection([('matched', 'Matched'), ('check', 'Check'), ('none', 'No match'), ('manual', 'Chosen by hand')],
                                   string='Match')
    match_note = fields.Char(string='Note')
    active = fields.Boolean(default=True)

    def write(self, vals):
        if 'product_id' in vals and 'match_state' not in vals:
            vals = dict(vals, match_state='manual' if vals['product_id'] else 'none', match_note=False)
        return super().write(vals)

    def action_match(self):
        # a normal method on purpose: the list-header button sends the (possibly empty) selection as its first argument
        """Matches every row that has no Odoo product yet to one of the forecast products, by name."""
        run = self.env['alkhuloodh.sf.run'].sudo().search([('is_current', '=', True), ('state', '=', 'done')], limit=1)
        if not run:
            return self._notify(self.env._('There is no forecast yet. Run the forecast first.'), 'warning')
        cr = self.env.cr
        cr.execute("SELECT DISTINCT product_id FROM alkhuloodh_sf_forecast_line WHERE run_id = %s AND level = 'product'", (run.id,))
        prods = self.env['product.product'].sudo().with_context(display_default_code=False, active_test=False) \
            .browse([r[0] for r in cr.fetchall()]).exists()
        candidates = [(p.id, p.display_name) for p in prods]
        todo = self.sudo().search([('product_id', '=', False)])
        counts = {'matched': 0, 'check': 0, 'none': 0}
        for ln in todo:
            pid, state, note = akf_report.match_label(ln.label, candidates)
            ln.write({'product_id': pid or False, 'match_state': state, 'match_note': note or False})
            counts[state] += 1
        msg = self.env._('Matched %(m)s, to check %(c)s, no match %(n)s. Rows marked Check or No match can be set by hand.',
                         m=counts['matched'], c=counts['check'], n=counts['none'])
        return self._notify(msg, 'success', reload=True)

    @api.model
    def _notify(self, message, kind, reload=False):
        params = {'title': self.env._('Report products'), 'message': message, 'type': kind, 'sticky': False}
        if reload:
            params['next'] = {'type': 'ir.actions.client', 'tag': 'reload'}
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': params}
