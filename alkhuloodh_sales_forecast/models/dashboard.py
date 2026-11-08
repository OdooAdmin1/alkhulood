import calendar as pycal

import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta

from odoo import api, models
from odoo.exceptions import AccessError, UserError

from .. import akf_engine
from .calendar_day import HIJRI_MONTHS

EVENT_NAMES = {'fitr': 'Eid al-Fitr', 'adha': 'Eid al-Adha', 'ashura': 'Ashura', 'hny': 'Hijri New Year'}
DOW = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']


def _num(x, nd=2):
    if x is None:
        return None
    try:
        if np.isnan(x):
            return None
    except TypeError:
        return None
    return round(float(x), nd)


def _event_label(key):
    if key in ('plain', 'skip'):
        return ''
    name, _sep, off = key.partition(':')
    if name == 'ram':
        return 'Ramadan %s' % off
    base = EVENT_NAMES.get(name, name)
    o = int(off)
    return base if o == 0 else '%s %+d' % (base, o)


def _event_name(key):
    name = key.partition(':')[0]
    return 'Ramadan' if name == 'ram' else EVENT_NAMES.get(name, name)


class AlkhuloodhSfDashboard(models.AbstractModel):
    _name = 'alkhuloodh.sf.dashboard'
    _description = 'Forecast dashboard data'

    # ------------------------------------------------------------------ helpers
    @api.model
    def _check_access(self):
        user = self.env.user
        if not (user.has_group('base.group_system') or user.has_group('alkhuloodh_sales_forecast.group_sf_user')):
            raise AccessError(self.env._('You do not have access to the sales forecast.'))

    @api.model
    def _current_run(self):
        run = self.env['alkhuloodh.sf.run'].sudo().search([('is_current', '=', True), ('state', '=', 'done')], limit=1)
        if not run:
            raise UserError(self.env._('There is no forecast yet. Open Configuration and press "Run forecast now".'))
        return run

    # ------------------------------------------------------------------ options for the filter bar
    @api.model
    def get_options(self):
        self._check_access()
        cr = self.env.cr
        run = self._current_run()
        cr.execute("""SELECT DISTINCT a.warehouse_id FROM alkhuloodh_sf_actual_daily a""")
        wh = self.env['stock.warehouse'].sudo().browse([r[0] for r in cr.fetchall()]).exists()
        cr.execute("""SELECT DISTINCT categ_id FROM alkhuloodh_sf_actual_daily WHERE categ_id IS NOT NULL""")
        cats = self.env['product.category'].sudo().browse([r[0] for r in cr.fetchall()]).exists()
        cr.execute("""SELECT DISTINCT product_id FROM alkhuloodh_sf_forecast_line
                       WHERE run_id = %s AND level = 'product' AND metric = 'qty'""", (run.id,))
        prods = self.env['product.product'].sudo().with_context(display_default_code=False) \
            .browse([r[0] for r in cr.fetchall()]).exists()
        cr.execute('SELECT MAX(GREATEST(write_date, create_date)) FROM alkhuloodh_sf_override')
        changed = cr.fetchone()[0]
        stale = bool(changed and run.run_date and changed > run.run_date)
        return {
            'stale': stale,
            'drift': self.env['alkhuloodh.sf.accuracy']._drift_status(),
            'warehouses': sorted(({'id': w.id, 'name': w.name} for w in wh), key=lambda x: x['name']),
            'categories': sorted(({'id': c.id, 'name': c.complete_name} for c in cats), key=lambda x: x['name']),
            'products': sorted(({'id': p.id, 'name': p.display_name} for p in prods), key=lambda x: x['name']),
            'run': {'id': run.id, 'kind': run.kind, 'origin': str(run.origin), 'horizon_end': str(run.horizon_end),
                    'run_date': str(run.run_date)},
        }

    # ------------------------------------------------------------------ main data call
    @api.model
    def get_data(self, filters):
        self._check_access()
        cr = self.env.cr
        run = self._current_run()
        f = {
            'warehouse_id': int(filters.get('warehouse_id') or 0) or None,
            'categ_id': int(filters.get('categ_id') or 0) or None,
            'product_id': int(filters.get('product_id') or 0) or None,
            'metric': 'qty' if filters.get('metric') == 'qty' else 'revenue',
            'months': max(1, min(int(filters.get('months') or 3), 12)),
            'history_days': max(7, min(int(filters.get('history_days') or 28), 120)),
            'gran': filters.get('gran') if filters.get('gran') in ('day', 'week', 'month', 'hijri') else 'day',
        }
        wh, ca, pr, metric = f['warehouse_id'], f['categ_id'], f['product_id'], f['metric']
        if metric == 'qty' and not pr:
            return {'error': self.env._('Quantity forecasts are made per product. Pick a product, or switch to Revenue.')}

        # which stored series
        if pr:
            level, cond, args, plevel = 'product', 'l.product_id = %s', [pr], ('prod_qty' if metric == 'qty' else 'prod_rev')
        elif wh and ca:
            level, cond, args, plevel = 'cell', 'l.warehouse_id = %s AND l.categ_id = %s', [wh, ca], 'cell'
        elif wh:
            level, cond, args, plevel = 'branch', 'l.warehouse_id = %s', [wh], 'branch'
        elif ca:
            level, cond, args, plevel = 'category', 'l.categ_id = %s', [ca], 'category'
        else:
            level, cond, args, plevel = 'company', 'TRUE', [], 'company'
        sel_metric = 'qty' if (pr and metric == 'qty') else 'revenue'

        origin = pd.Timestamp(run.origin)
        horizon_end = pd.Timestamp(run.horizon_end)
        start = origin - pd.Timedelta(days=f['history_days'])
        end_m = origin + relativedelta(months=f['months'])
        end = min(horizon_end, pd.Timestamp(end_m.year, end_m.month, pycal.monthrange(end_m.year, end_m.month)[1]))
        idx = pd.date_range(start, end)

        # predictions: for every day the latest forecast that was made before that day
        cr.execute("""
            SELECT DISTINCT ON (l.date) l.date, l.expected, l.low, l.high, r.origin
              FROM alkhuloodh_sf_forecast_line l
              JOIN alkhuloodh_sf_run r ON r.id = l.run_id
             WHERE r.state = 'done' AND r.origin < l.date AND l.date BETWEEN %s AND %s
               AND l.level = %s AND l.metric = %s AND """ + cond + """
             ORDER BY l.date, r.origin DESC""", [start.date(), end.date(), level, sel_metric] + args)
        prow = cr.fetchall()
        pred = pd.DataFrame(prow, columns=['date', 'expected', 'low', 'high', 'made']).set_index('date') \
            if prow else pd.DataFrame(columns=['expected', 'low', 'high', 'made'])
        pred.index = pd.to_datetime(pred.index)
        pred = pred.reindex(idx)
        for c in ('expected', 'low', 'high'):
            pred[c] = pred[c].astype(float)

        # actuals (whole history, so the comparisons can look back)
        col = 'qty' if sel_metric == 'qty' else 'revenue'
        acond, aargs = [], []
        if pr:
            acond.append('product_id = %s'); aargs.append(pr)
        else:
            if wh:
                acond.append('warehouse_id = %s'); aargs.append(wh)
            if ca:
                acond.append('categ_id = %s'); aargs.append(ca)
        cr.execute('SELECT MIN(date) FROM alkhuloodh_sf_actual_daily')
        first = cr.fetchone()[0]
        cr.execute('SELECT date, SUM(%s) FROM alkhuloodh_sf_actual_daily WHERE %s GROUP BY date' % (
            col, ' AND '.join(acond) if acond else 'TRUE'), aargs)
        arows = cr.fetchall()
        A = pd.Series({pd.Timestamp(d): float(v) for d, v in arows}, dtype=float)
        A = A.reindex(pd.date_range(pd.Timestamp(first), origin)).fillna(0.0) if first else A

        # calendar and events
        cal = self.env['alkhuloodh.sf.calendar.day']._to_dataframe()
        keys = pd.Series([akf_engine.event_key(r) for r in cal.itertuples()], index=pd.DatetimeIndex(cal['date']))
        ci = cal.set_index('date')
        hmap = {(int(r.hijri_year), int(r.hijri_month), int(r.hijri_day)): r.Index for r in ci.itertuples()}
        hy = ci.loc[idx, 'hijri_year'].astype(int).values
        hm = ci.loc[idx, 'hijri_month'].astype(int).values
        hd = ci.loc[idx, 'hijri_day'].astype(int).values
        ev_keys = keys.reindex(idx).values
        is_event = np.array([k not in ('plain', 'skip') for k in ev_keys])

        def look(dates):
            return A.reindex(pd.DatetimeIndex(dates)).values

        c_lw = look(idx - pd.Timedelta(days=7))
        c_lm = look([d - relativedelta(months=1) for d in idx])
        c_ly = look([d - relativedelta(years=1) for d in idx])
        c_hly = look([hmap.get((y - 1, m, d), hmap.get((y - 1, m, d - 1))) for y, m, d in zip(hy, hm, hd)])
        act = A.reindex(idx).values
        exp = pred['expected'].values
        made = pred['made'].values

        params = akf_engine.RangeParams(self.env['alkhuloodh.sf.range.param'].sudo()._to_dataframe())

        def band(E, n, event, first_day):
            B = 1 if n == 1 else 7 if n <= 10 else 28
            bucket = 'near' if (first_day - origin).days < akf_engine.NEAR_DAYS else 'far'
            q10, _q50, q90, k = params.get(plevel, B, 'event' if event else 'plain', bucket)
            return max(E - (E - E * q10) * k, 0.0), E + (E * q90 - E) * k

        nd = 1 if sel_metric == 'qty' else 2
        rows = []
        gran = f['gran']
        if gran == 'day':
            for i, d in enumerate(idx):
                e = exp[i]
                a = act[i]
                err = (a - e) if not (np.isnan(a) or np.isnan(e)) else np.nan
                rows.append({
                    'label': d.strftime('%Y-%m-%d'), 'date': d.strftime('%Y-%m-%d'), 'dow': DOW[d.dayofweek],
                    'hijri': '%d %s %d' % (hd[i], HIJRI_MONTHS[hm[i] - 1], hy[i]),
                    'event': _event_label(ev_keys[i]), 'n': 1, 'future': bool(d > origin),
                    'low': _num(pred['low'].values[i], nd), 'expected': _num(e, nd), 'high': _num(pred['high'].values[i], nd),
                    'actual': _num(a, nd), 'error': _num(err, nd),
                    'error_pct': _num(err / a * 100, 1) if (not np.isnan(err) and a) else None,
                    'lw': _num(c_lw[i], nd), 'lm': _num(c_lm[i], nd), 'ly': _num(c_ly[i], nd), 'hly': _num(c_hly[i], nd),
                    'made': str(made[i])[:10] if made[i] is not None and not pd.isna(made[i]) else None,
                })
        else:
            if gran == 'week':
                gkey = [(d - pd.Timedelta(days=d.dayofweek)).strftime('%Y-%m-%d') for d in idx]
            elif gran == 'month':
                gkey = [d.strftime('%Y-%m') for d in idx]
            else:
                gkey = ['%d-%02d' % (y, m) for y, m in zip(hy, hm)]
            order = list(dict.fromkeys(gkey))
            gk = np.array(gkey)
            for key in order:
                sel = np.where(gk == key)[0]
                days = idx[sel]
                n = len(sel)
                # only whole periods inside the chosen window
                if gran == 'week' and n < 7 or gran == 'month' and n < days[0].days_in_month:
                    continue
                if gran == 'hijri':
                    ys, ms = key.split('-')
                    mlen = int(ci[(ci.hijri_year == int(ys)) & (ci.hijri_month == int(ms))].hijri_day.max())
                    if n < mlen:
                        continue
                    label = '%s %s' % (HIJRI_MONTHS[int(ms) - 1], ys)
                elif gran == 'month':
                    label = days[0].strftime('%b %Y')
                else:
                    label = '%s – %s' % (days[0].strftime('%d %b'), days[-1].strftime('%d %b'))
                e_part = exp[sel]
                covered = int((~np.isnan(e_part)).sum())
                E = float(np.nansum(e_part)) if covered else np.nan
                complete_actual = days[-1] <= origin
                a = float(act[sel].sum()) if complete_actual else np.nan
                lo = hi = np.nan
                if covered == n:
                    lo, hi = band(E, n, bool(is_event[sel].any()), days[0])
                err = (a - E) if (not np.isnan(a) and covered == n) else np.nan

                def s(arr):
                    v = arr[sel].sum()
                    return _num(v, nd)
                rows.append({
                    'label': label, 'date': days[0].strftime('%Y-%m-%d'), 'n': n, 'future': bool(days[0] > origin),
                    'dow': '', 'hijri': '', 'event': ', '.join(sorted({_event_name(k) for k in ev_keys[sel] if k not in ('plain', 'skip')}))[:60],
                    'low': _num(lo, nd), 'expected': _num(E if covered == n else np.nan, nd), 'high': _num(hi, nd),
                    'actual': _num(a, nd), 'error': _num(err, nd),
                    'error_pct': _num(err / a * 100, 1) if (not np.isnan(err) and a) else None,
                    'lw': s(c_lw), 'lm': s(c_lm), 'ly': s(c_ly), 'hly': s(c_hly), 'made': None,
                })

        # summary cards (future part of the window)
        fut = np.where(idx > origin)[0]
        summary = []
        for title, n_days in (('Next 7 days', 7), ('Next 28 days', 28), ('Whole horizon', len(fut))):
            sl = fut[:n_days]
            if len(sl) == 0 or np.isnan(exp[sl]).any():
                continue
            E = float(exp[sl].sum())
            lo, hi = band(E, len(sl), bool(is_event[sl].any()), idx[sl[0]])
            summary.append({'title': title, 'days': len(sl), 'expected': _num(E, nd), 'low': _num(lo, nd), 'high': _num(hi, nd),
                            'from': idx[sl[0]].strftime('%d %b'), 'to': idx[sl[-1]].strftime('%d %b %Y')})
        # accuracy over the past days that have both a prediction and an actual
        both = np.where((~np.isnan(exp)) & (~np.isnan(act)))[0]
        accuracy = None
        if len(both) >= 7 and act[both].sum() > 0:
            a_s, e_s = act[both].sum(), exp[both].sum()
            accuracy = {'days': int(len(both)), 'accuracy': _num((1 - np.abs(act[both] - exp[both]).sum() / a_s) * 100, 1),
                        'bias': _num((e_s / a_s - 1) * 100, 1),
                        'inside': _num(float(((act[both] >= pred['low'].values[both]) & (act[both] <= pred['high'].values[both])).mean() * 100), 0)}
        notes = []
        past = idx <= origin
        if past.any() and np.isnan(exp[past]).all():
            notes.append(self.env._('No past forecasts are stored for this period yet. Press "Build past forecasts" in Configuration to compare predicted and actual.'))
        if pr and (wh or ca):
            notes.append(self.env._('Product forecasts are for all branches together, so the branch and category filters are ignored.'))
        return {
            'rows': rows, 'summary': summary, 'accuracy': accuracy, 'notes': notes, 'metric': sel_metric,
            'origin': str(origin.date()), 'level': level, 'filters': f,
        }
