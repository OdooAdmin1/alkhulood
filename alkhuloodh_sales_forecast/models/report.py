from datetime import timedelta

import pandas as pd

from odoo import api, fields, models

from .. import akf_report
from .calendar_day import HIJRI_MONTHS

MAX_DAYS = 31
# Month of the Hijri calendar a confirmed date belongs to, in the order they win when a month has two (Muharram)
SHIFT_ORDER = ('hijri_new_year', 'ashura', 'ramadan_start', 'eid_fitr', 'eid_adha')


class AlkhuloodhSfReport(models.AbstractModel):
    _name = 'alkhuloodh.sf.report'
    _description = 'Forecast report (all products by branch, day by day)'

    @api.model
    def _check_access(self):
        """Raises unless the user may open the forecast. Returns {'limit': None | branch id, 'viewer_only': bool}."""
        return self.env['alkhuloodh.sf.access']._scope()

    @api.model
    def _branch_columns(self, scope, want=()):
        self.env['alkhuloodh.sf.report.branch']._ensure()
        bsel = self.env['alkhuloodh.sf.report.branch'].sudo().search([('show', '=', True)])
        out = []
        for b in bsel:
            wid = b.warehouse_id.id
            if scope['limit']:
                if wid != scope['limit']:
                    continue               # a limited user gets their own branch, whatever is asked
            elif want and wid not in want:
                continue
            out.append({'id': wid, 'short': b.short_name, 'name': b.warehouse_id.name})
        return out

    @api.model
    def get_options(self):
        scope = self._check_access()
        cr = self.env.cr
        run = self.env['alkhuloodh.sf.dashboard']._current_run()
        cr.execute('SELECT MIN(date) FROM alkhuloodh_sf_actual_daily')
        first_actual = cr.fetchone()[0]
        cr.execute('SELECT DISTINCT categ_id FROM alkhuloodh_sf_actual_daily WHERE categ_id IS NOT NULL')
        cats = self.env['product.category'].sudo().browse([r[0] for r in cr.fetchall()]).exists()
        origin = fields.Date.to_date(run.origin)
        return {
            'origin': str(origin), 'horizon_end': str(run.horizon_end), 'run_date': str(run.run_date),
            'default_date': str(origin + timedelta(days=1)),
            'min_date': str(first_actual or origin), 'max_days': MAX_DAYS,
            'locked': bool(scope['limit']),
            'branches': [{'id': b['id'], 'short': b['short'], 'name': b['name']} for b in self._branch_columns(scope)],
            'categories': sorted(({'id': c.id, 'name': c.complete_name} for c in cats), key=lambda x: x['name']),
        }

    # ------------------------------------------------------------------ Hijri dates
    @api.model
    def _hijri_label(self, cal):
        """Hijri date for any day: the calendar, with the months whose first day was confirmed (moon sighting) shifted."""
        by_date = cal.set_index('date')
        shifts = {}
        ovs = self.env['alkhuloodh.sf.override']._active_confirmed().filtered(lambda o: o.kind in SHIFT_ORDER and o.base_date)
        for kind in SHIFT_ORDER:
            for o in ovs.filtered(lambda x: x.kind == kind):
                base = pd.Timestamp(o.base_date)
                if o.shift_days and base in by_date.index:
                    key = (int(by_date.at[base, 'hijri_year']), int(by_date.at[base, 'hijri_month']))
                    shifts.setdefault(key, o.shift_days)
        return akf_report.hijri_labeler(cal, shifts, HIJRI_MONTHS)

    # ------------------------------------------------------------------ the report
    @api.model
    def get_report(self, filters):
        scope = self._check_access()
        cr = self.env.cr
        run = self.env['alkhuloodh.sf.dashboard']._current_run()
        origin = pd.Timestamp(run.origin)
        horizon_end = pd.Timestamp(run.horizon_end)
        metric = 'qty' if filters.get('metric') == 'qty' else 'revenue'
        categ = int(filters.get('categ_id') or 0) or None
        cr.execute('SELECT MIN(date) FROM alkhuloodh_sf_actual_daily')
        first_actual = pd.Timestamp(cr.fetchone()[0] or origin)
        try:
            d1 = pd.Timestamp(filters.get('date_from') or origin + pd.Timedelta(days=1))
            d2 = pd.Timestamp(filters.get('date_to') or d1)
        except ValueError:
            return {'error': self.env._('Please enter valid dates.')}
        if d2 < d1:
            d1, d2 = d2, d1
        notes = []
        if d1 < first_actual:
            d1 = first_actual
            notes.append(self.env._('The start date was moved to %s, the first day with sales.', d1.date()))
        if d2 > horizon_end:
            d2 = horizon_end
            notes.append(self.env._('The end date was moved to %s, the end of the forecast.', d2.date()))
        if d1 > d2:
            return {'error': self.env._('There are no sales or forecast for these dates. Pick dates between %(a)s and %(b)s.',
                                        a=first_actual.date(), b=horizon_end.date())}
        n = (d2 - d1).days + 1
        if n > MAX_DAYS:
            return {'error': self.env._('Please pick at most %(m)s days (you picked %(n)s). The report has one table per day.',
                                        m=MAX_DAYS, n=n)}

        branches = self._branch_columns(scope, {int(x) for x in (filters.get('branch_ids') or [])})
        bids = [b['id'] for b in branches]

        # rows: the client's product list, or every forecast product when no list is set up
        setup = self.env['alkhuloodh.sf.report.line'].sudo().search([])
        if setup:
            lines = [{'label': s.label, 'product_id': s.product_id.id or None, 'name': s.product_id.display_name or ''}
                     for s in setup]
        else:
            cr.execute("""SELECT DISTINCT product_id FROM alkhuloodh_sf_forecast_line
                           WHERE run_id = %s AND level = 'pair'""", (run.id,))
            prods = self.env['product.product'].sudo().with_context(display_default_code=False, active_test=False) \
                .browse([r[0] for r in cr.fetchall()]).exists()
            lines = [{'label': p.display_name, 'product_id': p.id, 'name': p.display_name}
                     for p in sorted(prods, key=lambda x: x.display_name.lower())]
            notes.append(self.env._('No product list is set up (Report setup > Report products), so all forecast products are shown.'))
        pids = sorted({ln['product_id'] for ln in lines if ln['product_id']})
        if categ and pids:
            cr.execute("""SELECT DISTINCT ON (product_id) product_id, categ_id FROM alkhuloodh_sf_actual_daily
                           WHERE product_id = ANY(%s) ORDER BY product_id, date DESC""", (pids,))
            pcat = dict(cr.fetchall())
            lines = [ln for ln in lines if ln['product_id'] and pcat.get(ln['product_id']) == categ]
            pids = sorted({ln['product_id'] for ln in lines})

        # data: forecast (future days: current run; past days: the latest forecast made before that day) and actual sales
        fcast, actual = {}, {}
        col = 'qty' if metric == 'qty' else 'revenue'
        if pids and bids:
            cr.execute("""SELECT DISTINCT ON (l.date, l.product_id, l.warehouse_id)
                                 l.date, l.product_id, l.warehouse_id, l.expected, l.low, l.high, l.method
                            FROM alkhuloodh_sf_forecast_line l
                            JOIN alkhuloodh_sf_run r ON r.id = l.run_id
                           WHERE r.state = 'done' AND r.origin < l.date AND l.level = 'pair' AND l.metric = %s
                             AND l.date BETWEEN %s AND %s AND (l.date <= %s OR r.id = %s)
                             AND l.product_id = ANY(%s) AND l.warehouse_id = ANY(%s)
                           ORDER BY l.date, l.product_id, l.warehouse_id, r.origin DESC, r.id DESC""",
                       (metric, d1.date(), d2.date(), origin.date(), run.id, pids, bids))
            for dt, pid, wid, e, lo, hi, method in cr.fetchall():
                fcast.setdefault(pd.Timestamp(dt), {})[(pid, wid)] = {
                    'e': float(e or 0), 'lo': float(lo or 0), 'hi': float(hi or 0), 'alloc': method == 'alloc'}
            if d1 <= origin:
                cr.execute("""SELECT date, product_id, warehouse_id, SUM(%s) FROM alkhuloodh_sf_actual_daily
                               WHERE date BETWEEN %%s AND %%s AND product_id = ANY(%%s) AND warehouse_id = ANY(%%s)
                               GROUP BY 1, 2, 3""" % col, (d1.date(), min(d2, origin).date(), pids, bids))
                for dt, pid, wid, v in cr.fetchall():
                    actual.setdefault(pd.Timestamp(dt), {})[(pid, wid)] = float(v or 0)

        cal = self.env['alkhuloodh.sf.calendar.day']._to_dataframe()
        hijri = self._hijri_label(cal)
        nd = 0 if metric == 'qty' else 1
        days = []
        for d in pd.date_range(d1, d2):
            kind = 'past' if d <= origin else 'future'
            day = akf_report.build_day(lines, branches, fcast.get(d, {}), actual.get(d, {}), kind, nd)
            day.update({'date': str(d.date()), 'title': akf_report.day_title(d), 'hijri': hijri(d)})
            days.append(day)

        has_past = any(x['kind'] == 'past' for x in days)
        has_future = any(x['kind'] == 'future' for x in days)
        unit = self.env._('units') if metric == 'qty' else self.env._('BHD ex-VAT')
        what = 'Quantity' if metric == 'qty' else 'Revenue'
        span = akf_report.day_title(d1) if n == 1 else '%s to %s (%s days, one table per day)' % (akf_report.day_title(d1), akf_report.day_title(d2), n)
        hijri_note = self.env._('Hijri dates are calculated and follow the dates confirmed in Dates & holidays; the date announced after moon sighting can differ by one day.')
        past_note = self.env._('Actual = POS sales of that day (units do not include free items; revenue is ex-VAT, before returns). '
                               'Forecast = the latest forecast made before that day (month-end or weekly forecast), kept for about 12 months.')
        est_note = self.env._('Slanted figures are estimates: the branch sells the product on too few days for its own model, so the product forecast is split by the branch\'s recent share of sales.')
        common = [self.env._('Branches: %s.') % '; '.join('%s = %s' % (c['short'], c['name']) for c in branches),
                  self.env._('Figures are rounded; totals add up the figures shown. "-" means there are not enough sales of the product in that branch to forecast it.'),
                  hijri_note]
        rep = {
            'metric': metric, 'unit': unit, 'nd': nd, 'columns': branches, 'days': days,
            'date_from': str(d1.date()), 'date_to': str(d2.date()), 'n_days': n,
            'origin': str(origin.date()), 'horizon_end': str(horizon_end.date()), 'notes': notes,
            'limited': bool(scope['limit']),
            'subtitle': '%s %s, %s. Figures in %s.' % (what, 'actual sales and forecast' if has_past and not has_future else
                                                        'forecast' if not has_past else 'actual sales and forecast', span, unit),
            'subtitle_day': '%s forecast. Figures in %s.' % (what, unit),
            'subtitle_past': '%s: actual sales and the forecast for that day. Figures in %s.' % (what, unit),
            'source': 'Forecast made on %s from sales up to %s.' % (str(run.run_date)[:10], origin.date()),
            'footnotes_day': common[:1] + [est_note] + common[1:],
            'footnotes_past': common[:1] + [past_note, est_note] + common[1:],
            'footnotes': common[:1] + ([past_note] if has_past else []) + [est_note] + common[1:],
        }
        return rep
