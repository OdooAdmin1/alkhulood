import csv

from odoo import api, fields, models
from odoo.tools import file_open


class AlkhuloodhSfRangeParam(models.Model):
    _name = 'alkhuloodh.sf.range.param'
    _description = 'Forecast range calibration (actual / forecast ratios from the backtest)'
    _order = 'level, block_days, kind, bucket'

    level = fields.Char(required=True, index=True)
    block_days = fields.Integer(string='Block days', required=True, help='1 = day, 7 = week, 28 = month')
    kind = fields.Selection([('plain', 'Normal days'), ('event', 'Event days')], required=True)
    bucket = fields.Selection([('near', 'First 91 days'), ('far', 'Later')], required=True)
    q10 = fields.Float(string='10th percentile ratio', digits=(10, 4))
    q50 = fields.Float(string='Median ratio', digits=(10, 4))
    q90 = fields.Float(string='90th percentile ratio', digits=(10, 4))
    n = fields.Integer(string='Blocks measured')
    widen = fields.Float(string='Widening factor', digits=(6, 2))

    @api.model
    def _ensure_defaults(self):
        """Load the shipped calibration if the table is empty (new installs and upgrades)."""
        if self.sudo().search_count([]):
            return
        rows = []
        with file_open('alkhuloodh_sales_forecast/data/range_params.csv', 'r') as f:
            for r in csv.DictReader(f):
                rows.append({'level': r['level'], 'block_days': int(r['B']), 'kind': r['kind'], 'bucket': r['bucket'],
                             'q10': float(r['q10']), 'q50': float(r['q50']), 'q90': float(r['q90']),
                             'n': int(float(r['n'])), 'widen': float(r['widen'])})
        self.sudo().create(rows)

    @api.model
    def _to_dataframe(self):
        import pandas as pd
        self._ensure_defaults()
        self.env.cr.execute('SELECT level, block_days, kind, bucket, q10, q50, q90, n, widen FROM alkhuloodh_sf_range_param')
        return pd.DataFrame(self.env.cr.fetchall(),
                            columns=['level', 'B', 'kind', 'bucket', 'q10', 'q50', 'q90', 'n', 'widen'])
