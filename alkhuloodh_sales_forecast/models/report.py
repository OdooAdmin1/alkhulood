from datetime import timedelta

import pandas as pd

from odoo import api, fields, models
from odoo.exceptions import AccessError

from .. import akf_engine, akf_report

NEAR = akf_engine.NEAR_DAYS


class AlkhuloodhSfReport(models.AbstractModel):
    _name = 'alkhuloodh.sf.report'
    _description = 'Forecast report (all products by branch)'

    @api.model
    def _check_access(self):
        user = self.env.user
        if not (user.has_group('base.group_system') or user.has_group('alkhuloodh_sales_forecast.group_sf_user')):
            raise AccessError(self.env._('You do not have access to the sales forecast.'))

    @api.model
    def get_options(self):
        self._check_access()
        cr = self.env.cr
        run = self.env['alkhuloodh.sf.dashboard']._current_run()
        self.env['alkhuloodh.sf.report.branch']._ensure()
        branches = self.env['alkhuloodh.sf.report.branch'].sudo().search([('show', '=', True)])
        cr.execute('SELECT DISTINCT categ_id FROM alkhuloodh_sf_actual_daily WHERE categ_id IS NOT NULL')
        cats = self.env['product.category'].sudo().browse([r[0] for r in cr.fetchall()]).exists()
        origin = fields.Date.to_date(run.origin)
        return {
            'origin': str(origin), 'horizon_end': str(run.horizon_end), 'run_date': str(run.run_date),
            'default_date': str(origin + timedelta(days=1)),
            'branches': [{'id': b.warehouse_id.id, 'short': b.short_name, 'name': b.warehouse_id.name} for b in branches],
            'categories': sorted(({'id': c.id, 'name': c.complete_name} for c in cats), key=lambda x: x['name']),
        }

    @api.model
    def get_report(self, filters):
        self._check_access()
        cr = self.env.cr
        run = self.env['alkhuloodh.sf.dashboard']._current_run()
        origin = pd.Timestamp(run.origin)
        horizon_end = pd.Timestamp(run.horizon_end)
        metric = 'qty' if filters.get('metric') == 'qty' else 'revenue'
        categ = int(filters.get('categ_id') or 0) or None
        try:
            d1 = pd.Timestamp(filters.get('date_from') or origin + pd.Timedelta(days=1))
            d2 = pd.Timestamp(filters.get('date_to') or d1)
        except ValueError:
            return {'error': self.env._('Please enter valid dates.')}
        if d2 < d1:
            d1, d2 = d2, d1
        notes = []
        if d2 <= origin:
            return {'error': self.env._('The report shows forecasts, so pick dates after %s (the last day of actuals).', origin.date())}
        if d1 <= origin:
            d1 = origin + pd.Timedelta(days=1)
            notes.append(self.env._('The start date was moved to %s, the first forecast day.', d1.date()))
        if d1 > horizon_end:
            return {'error': self.env._('The forecast ends on %s. Pick earlier dates.', horizon_end.date())}
        if d2 > horizon_end:
            d2 = horizon_end
            notes.append(self.env._('The end date was moved to %s, the end of the forecast.', d2.date()))
        n = (d2 - d1).days + 1

        # columns
        self.env['alkhuloodh.sf.report.branch']._ensure()
        bsel = self.env['alkhuloodh.sf.report.branch'].sudo().search([('show', '=', True)])
        want = {int(x) for x in (filters.get('branch_ids') or [])}
        branches = [{'id': b.warehouse_id.id, 'short': b.short_name, 'name': b.warehouse_id.name}
                    for b in bsel if not want or b.warehouse_id.id in want]

        # rows
        cr.execute("""SELECT DISTINCT product_id, categ_id FROM alkhuloodh_sf_forecast_line
                       WHERE run_id = %s AND level = 'pair'""", (run.id,))
        pcat = dict(cr.fetchall())
        prods = self.env['product.product'].sudo().with_context(display_default_code=False, active_test=False) \
            .browse(list(pcat)).exists()
        names = {p.id: p.display_name for p in prods}
        setup = self.env['alkhuloodh.sf.report.line'].sudo().search([])
        lines = [{'label': s.label, 'product_id': s.product_id.id or None, 'name': names.get(s.product_id.id) or s.product_id.display_name}
                 for s in setup]
        if not lines:
            lines = [{'label': names[p], 'product_id': p, 'name': names[p]} for p in sorted(names, key=lambda x: names[x].lower())]
            notes.append(self.env._('No product list is set up (Report setup > Report products), so all forecast products are shown.'))
        if categ:
            lines = [ln for ln in lines if ln['product_id'] and pcat.get(ln['product_id']) == categ]

        # cells
        cr.execute("""SELECT product_id, warehouse_id, SUM(expected), SUM(low), SUM(high), BOOL_OR(method = 'alloc')
                        FROM alkhuloodh_sf_forecast_line
                       WHERE run_id = %s AND level = 'pair' AND metric = %s AND date BETWEEN %s AND %s
                       GROUP BY product_id, warehouse_id""", (run.id, metric, d1.date(), d2.date()))
        raw = cr.fetchall()
        event = False
        if n > 1:
            cal = self.env['alkhuloodh.sf.calendar.day']._to_dataframe()
            cal = cal[(cal['date'] >= d1) & (cal['date'] <= d2)]
            event = any(akf_engine.event_key(r) not in ('plain', 'skip') for r in cal.itertuples())
        params = akf_engine.RangeParams(self.env['alkhuloodh.sf.range.param'].sudo()._to_dataframe())
        cells = {}
        for pid, wid, e, lo, hi, alloc in raw:
            e = float(e or 0)
            if n > 1:           # ranges for periods are measured on past forecasts, not added from the daily ones
                B = 7 if n <= 10 else 28
                bucket = 'near' if (d1 - origin).days < NEAR else 'far'
                q10, _q50, q90, k = params.get('pair', B, 'event' if event else 'plain', bucket, 'alloc' if alloc else 'model')
                lo, hi = max(e - (e - e * q10) * k, 0.0), e + (e * q90 - e) * k
            cells[(pid, wid)] = {'e': e, 'lo': float(lo or 0), 'hi': float(hi or 0), 'alloc': bool(alloc)}

        rep = akf_report.build_layout(lines, branches, cells, metric)
        unit = self.env._('units') if metric == 'qty' else self.env._('BHD ex-VAT')
        span = str(d1.date()) if n == 1 else '%s to %s (%s days, total for the period)' % (d1.date(), d2.date(), n)
        rep.update({
            'metric': metric, 'unit': unit, 'date_from': str(d1.date()), 'date_to': str(d2.date()), 'days': n,
            'origin': str(origin.date()), 'horizon_end': str(horizon_end.date()), 'notes': notes,
            'subtitle': '%s forecast, %s. Figures in %s.' % ('Quantity' if metric == 'qty' else 'Revenue', span, unit),
            'source': 'Forecast made on %s from sales up to %s.' % (str(run.run_date)[:10], origin.date()),
            'footnotes': [
                self.env._('Branches: %s.') % '; '.join('%s = %s' % (c['short'], c['name']) for c in rep['columns']),
                'Figures are rounded; totals add up the figures shown. Slanted figures are estimates: the branch sells the product '
                'on too few days for its own model, so the product forecast is split by the branch\'s recent share of sales.',
                '"-" means there are not enough sales of the product in that branch to forecast it.',
            ],
        })
        return rep
