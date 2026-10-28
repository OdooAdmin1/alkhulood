from odoo import api, fields, models

KEEP_DAYS = 380

REFRESH_SQL = """
INSERT INTO alkhuloodh_sf_accuracy
       (run_id, origin, kind, level, warehouse_id, categ_id, window_days, n_days, expected_total, actual_total,
        abs_error, in_range_days, accuracy_daily, accuracy_total, bias_pct, in_range_pct,
        create_uid, create_date, write_uid, write_date)
SELECT t.run_id, t.origin, t.kind, t.level, t.warehouse_id, t.categ_id, t.win, t.n, t.e, t.a, t.abs_err, t.inr,
       CASE WHEN t.a > 0 THEN 100.0 * (1 - t.abs_err / t.a) END,
       CASE WHEN t.a > 0 THEN 100.0 * (1 - ABS(t.a - t.e) / t.a) END,
       CASE WHEN t.a > 0 THEN 100.0 * (t.e / t.a - 1) END,
       100.0 * t.inr / t.n,
       1, (now() AT TIME ZONE 'UTC'), 1, (now() AT TIME ZONE 'UTC')
  FROM (
    SELECT r.id AS run_id, r.origin, r.kind, %(level)s AS level, l.warehouse_id, l.categ_id, w.win,
           COUNT(*) AS n, SUM(l.expected) AS e, SUM(COALESCE(a.rev, 0)) AS a,
           SUM(ABS(COALESCE(a.rev, 0) - l.expected)) AS abs_err,
           SUM(CASE WHEN COALESCE(a.rev, 0) BETWEEN l.low AND l.high THEN 1 ELSE 0 END) AS inr
      FROM alkhuloodh_sf_run r
      JOIN alkhuloodh_sf_forecast_line l ON l.run_id = r.id AND l.level = %(level)s AND l.metric = 'revenue'
     CROSS JOIN (VALUES (7), (28)) AS w(win)
     CROSS JOIN (SELECT MAX(date) AS d FROM alkhuloodh_sf_actual_daily) mx
      LEFT JOIN (SELECT date, @GCOLS@ SUM(revenue) AS rev FROM alkhuloodh_sf_actual_daily GROUP BY date@GBY@) a
             ON a.date = l.date @JCOND@
     WHERE r.state = 'done' AND r.kind IN ('weekly', 'backtest') AND r.origin >= (CURRENT_DATE - %(keep)s)
       AND l.date > r.origin AND l.date <= r.origin + w.win AND r.origin + w.win <= mx.d
     GROUP BY r.id, r.origin, r.kind, l.warehouse_id, l.categ_id, w.win
  ) t
"""


class AlkhuloodhSfAccuracy(models.Model):
    _name = 'alkhuloodh.sf.accuracy'
    _description = 'Forecast accuracy: frozen forecast vs what really sold'
    _order = 'origin desc, level, window_days'

    run_id = fields.Many2one('alkhuloodh.sf.run', string='Forecast run', ondelete='cascade', index=True)
    origin = fields.Date(string='Forecast made on', index=True)
    kind = fields.Selection([('weekly', 'Weekly snapshot'), ('backtest', 'Backtest')])
    level = fields.Selection([('company', 'Company'), ('branch', 'Branch'), ('category', 'Category')])
    warehouse_id = fields.Many2one('stock.warehouse', string='Branch', ondelete='cascade')
    categ_id = fields.Many2one('product.category', string='Category', ondelete='cascade')
    window_days = fields.Integer(string='Window (days)', help='Days after the forecast date that are compared.')
    n_days = fields.Integer(string='Days')
    expected_total = fields.Float(string='Expected total', digits=(16, 0))
    actual_total = fields.Float(string='Actual total', digits=(16, 0))
    abs_error = fields.Float(string='Sum of daily errors', digits=(16, 0))
    in_range_days = fields.Integer(string='Days inside range')
    accuracy_daily = fields.Float(string='Daily accuracy %', digits=(6, 1),
                                  help='100 minus the average daily error as % of actual sales.')
    accuracy_total = fields.Float(string='Window-total accuracy %', digits=(6, 1),
                                  help='How close the total for the whole window was.')
    bias_pct = fields.Float(string='Bias %', digits=(6, 1), help='Positive = forecast too high.')
    in_range_pct = fields.Float(string='Inside range %', digits=(6, 0))

    @api.model
    def _refresh(self):
        """Rebuilds the comparison rows from the stored forecasts and the daily actuals. Only complete windows count."""
        cr = self.env.cr
        cr.execute('DELETE FROM alkhuloodh_sf_accuracy WHERE origin >= CURRENT_DATE - %s', (KEEP_DAYS,))
        for level, gcols, gby, jcond in (
                ('company', '', '', ''),
                ('branch', 'warehouse_id,', ', warehouse_id', 'AND a.warehouse_id = l.warehouse_id'),
                ('category', 'categ_id,', ', categ_id', 'AND a.categ_id = l.categ_id')):
            # gcols / gby / jcond are fixed literals from this file, never user input
            sql = REFRESH_SQL.replace('@GCOLS@', gcols).replace('@GBY@', gby).replace('@JCOND@', jcond)
            cr.execute(sql, {'level': level, 'keep': KEEP_DAYS})
        self.env.flush_all()
        self.env.invalidate_all()
        cr.execute('SELECT COUNT(*) FROM alkhuloodh_sf_accuracy')
        return cr.fetchone()[0]

    @api.model
    def _drift_status(self):
        """Is the forecast getting worse? Compares the latest weekly snapshots with the backtest average."""
        cr = self.env.cr
        cr.execute("""SELECT kind, accuracy_daily, bias_pct, in_range_pct FROM alkhuloodh_sf_accuracy
                       WHERE level = 'company' AND window_days = 7 AND accuracy_daily IS NOT NULL
                       ORDER BY origin DESC""")
        rows = cr.fetchall()
        weekly = [r for r in rows if r[0] == 'weekly'][:4]
        base = [r for r in rows if r[0] == 'backtest']
        if not base:
            return {'level': 'info', 'message': self.env._('Accuracy tracking has no past forecasts yet. Press "Build past forecasts" and then "Refresh accuracy" in Configuration.')}
        if len(weekly) < 4:
            return {'level': 'info', 'message': self.env._(
                'Drift check starts after 4 weekly snapshots (now %s). Until then see the backtest rows in Accuracy.', len(weekly))}
        recent = sum(r[1] for r in weekly) / len(weekly)
        usual = sum(r[1] for r in base) / len(base)
        bias = sum(r[2] for r in weekly) / len(weekly)
        inside = sum(r[3] for r in weekly) / len(weekly)
        msgs = []
        if recent < usual - 8:
            msgs.append(self.env._('Daily accuracy of the last 4 weekly forecasts is %(r).0f%%, the backtest average is %(u).0f%%.', r=recent, u=usual))
        if abs(bias) > 15:
            msgs.append(self.env._('The last 4 weekly forecasts were on average %(b)+.0f%% off in one direction.', b=bias))
        if inside < 65:
            msgs.append(self.env._('Only %.0f%% of recent days fell inside the likely range (target about 80%%).', inside))
        if msgs:
            return {'level': 'warn', 'message': ' '.join(msgs) + ' ' + self.env._('Check for new events, closures or a change in the shops.')}
        return {'level': 'ok', 'message': self.env._('Forecast accuracy is steady: %(r).0f%% daily accuracy over the last 4 weekly forecasts.', r=recent)}

