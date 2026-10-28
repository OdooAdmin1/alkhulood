import logging
import time
import traceback
from datetime import datetime

import pandas as pd
import pytz
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

from .. import akf_engine

_logger = logging.getLogger(__name__)

ENGINE_VERSION = 'akf-engine 1.0 (Hijri profile, hierarchical shapes)'
KEEP_WEEKLY_DAYS = 400

LINE_INSERT = """
INSERT INTO alkhuloodh_sf_forecast_line
       (run_id, date, level, warehouse_id, categ_id, product_id, metric, expected, low, high,
        create_uid, create_date, write_uid, write_date)
SELECT %(run)s, t.d, t.l, t.w, t.c, t.p, t.m, t.e, t.lo, t.hi,
       %(uid)s, (now() AT TIME ZONE 'UTC'), %(uid)s, (now() AT TIME ZONE 'UTC')
  FROM unnest(%(d)s::date[], %(l)s::text[], %(w)s::int[], %(c)s::int[], %(p)s::int[], %(m)s::text[],
              %(e)s::float8[], %(lo)s::float8[], %(hi)s::float8[]) AS t(d, l, w, c, p, m, e, lo, hi)
"""


def _ints(series):
    return [None if pd.isna(x) else int(x) for x in series]


class AlkhuloodhSfRun(models.Model):
    _name = 'alkhuloodh.sf.run'
    _description = 'Forecast run'
    _order = 'id desc'

    name = fields.Char(compute='_compute_name', store=True)
    kind = fields.Selection([('nightly', 'Nightly refresh'), ('weekly', 'Weekly snapshot'), ('manual', 'Manual'),
                             ('backtest', 'Backtest (past origin)')],
                            required=True, default='manual')
    state = fields.Selection([('running', 'Running'), ('done', 'Done'), ('failed', 'Failed')], default='running')
    run_date = fields.Datetime(default=fields.Datetime.now)
    origin = fields.Date(string='Last day of actuals')
    horizon_end = fields.Date(string='Forecast to')
    seconds = fields.Float(digits=(10, 1))
    line_count = fields.Integer()
    is_current = fields.Boolean(string='Shown on dashboard', default=False, index=True)
    engine_version = fields.Char()
    error_log = fields.Text()

    @api.depends('kind', 'origin')
    def _compute_name(self):
        for r in self:
            r.name = f'{dict(self._fields["kind"].selection).get(r.kind, "")} {r.origin or ""}'.strip()

    # ------------------------------------------------------------------ entry points
    @api.model
    def _cron_run_forecast(self):
        tz = pytz.timezone(self.env['alkhuloodh.sf.config'].get_config().tz)
        weekday = datetime.now(pytz.utc).astimezone(tz).weekday()
        # Sunday is the first working day in Bahrain: that night's run is kept as the frozen weekly snapshot.
        self._run_forecast('weekly' if weekday == 6 else 'nightly', commit=True)

    @api.model
    def _run_forecast(self, kind, commit=False):
        run = self.sudo().create({'kind': kind, 'state': 'running'})
        if commit:
            self.env.cr.commit()        # a killed job still leaves a visible "running" record
        t0 = time.time()
        try:
            with self.env.cr.savepoint():
                engine, cal, params, cfg = self._load_engine()
                run._execute(engine, cal, params, cfg)
            run.write({'state': 'done', 'seconds': time.time() - t0})
            run._finish_housekeeping()
        except Exception:
            _logger.exception('Forecast run failed')
            run.write({'state': 'failed', 'seconds': time.time() - t0, 'error_log': traceback.format_exc()})
        return run

    @api.model
    def _build_backtests(self, budget_seconds=60):
        """Past forecasts for the last 12 month-ends, made with data up to each date only. They let the dashboard
        and the accuracy screen compare predicted vs actual from day one. Works until the time budget is used,
        then stops: press the button again to continue."""
        t0 = time.time()
        engine, cal, params, cfg = self._load_engine()
        end = engine.END
        origins = []
        for i in range(12, 0, -1):
            o = (end.replace(day=1) - relativedelta(months=i - 1) - pd.Timedelta(days=1)).normalize()
            if o < end and o - pd.Timestamp(cfg.history_start) > pd.Timedelta(days=240):
                origins.append(o)
        done = set(self.sudo().search([('kind', '=', 'backtest'), ('state', '=', 'done')]).mapped('origin'))
        todo = [o for o in origins if o.date() not in done]
        made = 0
        for o in todo:
            if made and time.time() - t0 > budget_seconds:
                break
            run = self.sudo().create({'kind': 'backtest', 'state': 'running'})
            t1 = time.time()
            try:
                with self.env.cr.savepoint():
                    run._execute(engine, cal, params, cfg, origin=o, horizon_days=45, light=True)
                run.write({'state': 'done', 'seconds': time.time() - t1})
                made += 1
            except Exception:
                _logger.exception('Backtest run failed')
                run.write({'state': 'failed', 'seconds': time.time() - t1, 'error_log': traceback.format_exc()})
                break
        if made:
            self.env['alkhuloodh.sf.accuracy']._refresh()
        return {'made': made, 'remaining': len(todo) - made, 'total': len(origins)}

    # ------------------------------------------------------------------ the run itself
    @api.model
    def _load_engine(self):
        cr = self.env.cr
        cfg = self.env['alkhuloodh.sf.config'].get_config()
        cr.execute("SELECT date, warehouse_id, product_id, COALESCE(categ_id, -1), revenue, qty "
                   "FROM alkhuloodh_sf_actual_daily")
        rows = cr.fetchall()
        if not rows:
            raise UserError(self.env._('No daily actuals yet. Open Configuration and press "Load full history" first.'))
        sales = pd.DataFrame(rows, columns=['day', 'warehouse_id', 'product_id', 'categ_id', 'rev', 'qty'])
        sales['rev'] = sales['rev'].astype(float)
        sales['qty'] = sales['qty'].astype(float)
        del rows
        cal = self.env['alkhuloodh.sf.calendar.day']._to_dataframe()
        params = akf_engine.RangeParams(self.env['alkhuloodh.sf.range.param']._to_dataframe())
        engine = akf_engine.Engine(sales, cal)
        return engine, cal, params, cfg

    def _execute(self, engine, cal, params, cfg, origin=None, horizon_days=None, light=False):
        """Forecast from `origin` (default: last day of actuals). light = company, branch and category only."""
        self.ensure_one()
        cr = self.env.cr
        origin = pd.Timestamp(origin) if origin is not None else engine.END
        if horizon_days:
            horizon_end = origin + pd.Timedelta(days=horizon_days)
        else:
            horizon_end = (origin + relativedelta(months=cfg.horizon_months)).normalize()
        if horizon_end > cal['date'].max():
            raise UserError(self.env._('The Hijri calendar ends on %s, before the forecast horizon.', cal['date'].max().date()))
        dates = pd.date_range(origin + pd.Timedelta(days=1), horizon_end)
        F, _shared = engine.run(origin, dates, products=not light)
        if light:
            F = {k: v for k, v in F.items() if k in ('company', 'branch', 'category')}
        lines = engine.forecast_lines(F, origin, dates, params)
        closed = self.env['alkhuloodh.sf.override']._closed_dates()
        if closed:
            mask = lines['date'].isin(closed)
            lines.loc[mask, ['expected', 'low', 'high']] = 0.0

        cr.execute('DELETE FROM alkhuloodh_sf_forecast_line WHERE run_id = %s', (self.id,))
        cr.execute(LINE_INSERT, {
            'run': self.id, 'uid': self.env.uid or 1,
            'd': [d.date() for d in lines['date']], 'l': lines['level'].tolist(),
            'w': _ints(lines['warehouse_id']), 'c': _ints(lines['categ_id']), 'p': _ints(lines['product_id']),
            'm': lines['metric'].tolist(), 'e': lines['expected'].tolist(),
            'lo': lines['low'].tolist(), 'hi': lines['high'].tolist(),
        })
        self.write({'origin': origin.date(), 'horizon_end': horizon_end.date(), 'line_count': len(lines),
                    'engine_version': ENGINE_VERSION})

    def _finish_housekeeping(self):
        """Newest successful run becomes the current one. Old nightly/manual runs lose their lines, old weekly
        snapshots are kept for accuracy tracking."""
        self.ensure_one()
        cr = self.env.cr
        cr.execute('UPDATE alkhuloodh_sf_run SET is_current = (id = %s) WHERE is_current OR id = %s', (self.id, self.id))
        cr.execute("""DELETE FROM alkhuloodh_sf_forecast_line l USING alkhuloodh_sf_run r
                       WHERE l.run_id = r.id AND r.id <> %s AND r.kind IN ('nightly', 'manual')""", (self.id,))
        cr.execute("""DELETE FROM alkhuloodh_sf_forecast_line l USING alkhuloodh_sf_run r
                       WHERE l.run_id = r.id AND r.kind = 'weekly' AND r.id <> %s
                         AND r.run_date < (now() AT TIME ZONE 'UTC') - make_interval(days => %s)""", (self.id, KEEP_WEEKLY_DAYS))
        self.env.invalidate_all()
