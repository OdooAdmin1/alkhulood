import logging
import hashlib
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
PAIR_KEEP_DAYS = 28        # weekly snapshots keep product-in-branch lines only for the first 28 days (what accuracy compares)
BACKTEST_RULE = 'pair1'    # changes only when forecasts are made differently: then every past forecast is rebuilt
BACKTEST_CRON = 'alkhuloodh_sales_forecast.cron_build_backtests'
BACKTEST_BUDGET = 40       # seconds of work per background run; it then queues itself again

# One text block (built by pandas in C) parsed by Postgres: much less work for the Odoo process than adapting
# nine long Python lists. Values never contain commas or line breaks.
LINE_INSERT = """
INSERT INTO alkhuloodh_sf_forecast_line
       (run_id, date, level, warehouse_id, categ_id, product_id, metric, expected, low, high, method,
        create_uid, create_date, write_uid, write_date)
SELECT %(run)s, f[1]::date, f[2], NULLIF(f[3], '')::int, NULLIF(f[4], '')::int, NULLIF(f[5], '')::int, f[6],
       f[7]::float8, f[8]::float8, f[9]::float8, NULLIF(f[10], ''),
       %(uid)s, (now() AT TIME ZONE 'UTC'), %(uid)s, (now() AT TIME ZONE 'UTC')
  FROM (SELECT string_to_array(x, ',') AS f
          FROM unnest(string_to_array(%(blob)s, E'\\n')) AS x WHERE x <> '') s
"""


def _int_col(series):
    return series.astype('Int64').astype(str).replace('<NA>', '')


def _lines_blob(lines):
    df = pd.DataFrame({
        'd': lines['date'].dt.strftime('%Y-%m-%d'), 'l': lines['level'],
        'w': _int_col(lines['warehouse_id']), 'c': _int_col(lines['categ_id']), 'p': _int_col(lines['product_id']),
        'm': lines['metric'],
        'e': lines['expected'].fillna(0.0).round(3), 'lo': lines['low'].fillna(0.0).round(3),
        'hi': lines['high'].fillna(0.0).round(3), 'me': lines['method'].fillna('')})
    return df.to_csv(index=False, header=False)



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
    has_products = fields.Boolean(string='Includes products', default=False)
    product_sig = fields.Char(help='Not used any more (kept so old data stays valid).')
    rule_ver = fields.Char(string='Method version', help='Version of the forecasting method this run was made with.')
    covered_product_ids = fields.Text(help='Products this run holds forecasts for (ids, comma separated). Used to add only '
                                           'the missing products when the extra products list changes.')

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
        # a new month-end, or a changed products list, may have left past forecasts to build: queue them if so
        try:
            self._queue_backtests_if_needed()
        except Exception:
            _logger.exception('Could not queue the past forecasts')

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

    # ------------------------------------------------------------------ past forecasts (backtests)
    @api.model
    def _backtest_origins(self, end, history_start):
        """The last 12 month-ends before `end` that have enough history behind them (oldest first)."""
        end = pd.Timestamp(end)
        origins = []
        for i in range(12, 0, -1):
            o = (end.replace(day=1) - relativedelta(months=i - 1) - pd.Timedelta(days=1)).normalize()
            if o < end and o - pd.Timestamp(history_start) > pd.Timedelta(days=240):
                origins.append(o)
        return origins

    @api.model
    def _extra_sig(self, cfg):
        key = ','.join(str(i) for i in sorted(cfg.extra_product_ids.ids))
        return hashlib.md5(key.encode()).hexdigest()[:12]

    @api.model
    def _backtest_counts(self):
        """(built, total) past forecasts, read cheaply from the database (no forecasting engine needed)."""
        cfg = self.env['alkhuloodh.sf.config'].get_config()
        cr = self.env.cr
        cr.execute('SELECT MAX(date) FROM alkhuloodh_sf_actual_daily')
        end = cr.fetchone()[0]
        if not end:
            return 0, 0
        origins = [o.date() for o in self._backtest_origins(end, cfg.history_start)]
        if not origins:
            return 0, 0
        cr.execute("""SELECT COUNT(DISTINCT origin) FROM alkhuloodh_sf_run
                       WHERE kind = 'backtest' AND state = 'done' AND rule_ver = %s AND origin = ANY(%s)""",
                   (BACKTEST_RULE, origins))
        return cr.fetchone()[0], len(origins)

    @api.model
    def _queue_backtests(self, note=None):
        """Ask the scheduler to build the past forecasts in the background (returns at once)."""
        cron = self.env.ref(BACKTEST_CRON, raise_if_not_found=False)
        cfg = self.env['alkhuloodh.sf.config'].get_config()
        if not cron:
            return False
        cfg.write({'bt_note': note or self.env._('Queued. The server builds them in the background.')})
        cron.sudo()._trigger()
        return True

    @api.model
    def _queue_backtests_if_needed(self):
        cfg = self.env['alkhuloodh.sf.config'].get_config()
        built, total = self._backtest_counts()
        if built < total or (cfg.bt_extra_sig and cfg.bt_extra_sig != self._extra_sig(cfg)):
            self._queue_backtests()

    @api.model
    def _cron_build_backtests(self):
        """Scheduled action: builds past forecasts for about BACKTEST_BUDGET seconds, saves progress, and queues
        itself again until nothing is left."""
        cfg = self.env['alkhuloodh.sf.config'].get_config()
        cr = self.env.cr
        # a job killed halfway leaves a "running" record behind
        cr.execute("""DELETE FROM alkhuloodh_sf_run WHERE kind = 'backtest' AND state = 'running'
                         AND create_date < (now() AT TIME ZONE 'UTC') - interval '1 hour'""")
        cfg.write({'bt_note': self.env._('Working in the background.')})
        cr.commit()
        try:
            res = self._build_backtests(budget_seconds=BACKTEST_BUDGET, commit=True)
        except Exception as e:
            _logger.exception('Past forecast build failed')
            cr.rollback()
            self.env.invalidate_all()
            cfg.write({'bt_note': self.env._('Stopped: %s', str(e)[:300])})
            return
        if res['failed']:
            cfg.write({'bt_note': self.env._('Stopped after an error. See Forecast runs (state Failed). It is retried tonight.')})
        elif res['remaining']:
            cfg.write({'bt_note': self.env._('Working in the background.')})
            cr.commit()
            self.env.ref(BACKTEST_CRON).sudo()._trigger()
        else:
            cfg.write({'bt_note': self.env._('Up to date.'), 'bt_extra_sig': self._extra_sig(cfg)})

    @api.model
    def _build_backtests(self, budget_seconds=25, commit=False):
        """Past forecasts for the last 12 month-ends, made with data up to each date only, newest first. They let the
        dashboard and the accuracy screen compare predicted vs actual. A month that exists but lacks some of the products
        now wanted (extra products added) gets only those products added. Works until the time budget is used, then
        stops and says how many are left."""
        t0 = time.time()
        engine, cal, params, cfg = self._load_engine()
        origins = self._backtest_origins(engine.END, cfg.history_start)
        extra = cfg.extra_product_ids.ids
        runs = {}
        for r in self.sudo().search([('kind', '=', 'backtest'), ('state', '=', 'done')], order='id'):
            runs[r.origin] = r              # the newest run of each month wins
        todo = []
        for o in reversed(origins):
            run = runs.get(o.date())
            if not run or run.rule_ver != BACKTEST_RULE:
                todo.append((o, None, None))
                continue
            covered = {int(x) for x in (run.covered_product_ids or '').split(',') if x}
            missing = [p for p in engine.chosen_products(o, extra) if p not in covered]
            if missing:
                todo.append((o, run, missing))
        made, failed = 0, False
        for o, run, missing in todo:
            if made and time.time() - t0 > budget_seconds:
                break
            t1 = time.time()
            if run is not None:
                try:
                    with self.env.cr.savepoint():
                        run._execute_append(engine, cal, params, cfg, o, 45, missing)
                    made += 1
                except Exception:
                    _logger.exception('Adding products to a past forecast failed')
                    failed = True
                    break
            else:
                self.sudo().search([('kind', '=', 'backtest'), ('origin', '=', o.date())]).unlink()   # old or incomplete
                run = self.sudo().create({'kind': 'backtest', 'state': 'running'})
                try:
                    with self.env.cr.savepoint():
                        run._execute(engine, cal, params, cfg, origin=o, horizon_days=45, light=False)
                    run.write({'state': 'done', 'seconds': time.time() - t1})
                    made += 1
                except Exception:
                    _logger.exception('Backtest run failed')
                    run.write({'state': 'failed', 'seconds': time.time() - t1, 'error_log': traceback.format_exc()})
                    failed = True
                    break
            if commit:
                self.env.cr.commit()        # finished months are kept even if the job is stopped later
        if made:
            self.env['alkhuloodh.sf.accuracy']._refresh()
        return {'made': made, 'remaining': len(todo) - made, 'total': len(origins), 'failed': failed}

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

    def _make_lines(self, engine, cal, params, cfg, origin, horizon_days, light=False, only_products=None):
        """Forecast lines for one origin. only_products: just those products (product level and product in branch)."""
        origin = pd.Timestamp(origin) if origin is not None else engine.END
        if horizon_days:
            horizon_end = origin + pd.Timedelta(days=horizon_days)
        else:
            horizon_end = (origin + relativedelta(months=cfg.horizon_months)).normalize()
        if horizon_end > cal['date'].max():
            raise UserError(self.env._('The Hijri calendar ends on %s, before the forecast horizon.', cal['date'].max().date()))
        dates = pd.date_range(origin + pd.Timedelta(days=1), horizon_end)
        if only_products is None:
            F, _shared = engine.run(origin, dates, products=not light, extra=cfg.extra_product_ids.ids)
        else:
            F, _shared = engine.run_products(origin, dates, only_products)
        lines = engine.forecast_lines(F, origin, dates, params) if any(F.values()) else None
        if lines is not None:
            closed = self.env['alkhuloodh.sf.override']._closed_dates()
            if closed:
                mask = lines['date'].isin(closed)
                lines.loc[mask, ['expected', 'low', 'high']] = 0.0
        return lines, origin, horizon_end

    def _insert_lines(self, lines):
        if lines is not None and len(lines):
            self.env.cr.execute(LINE_INSERT, {'run': self.id, 'uid': self.env.uid or 1, 'blob': _lines_blob(lines)})

    def _execute(self, engine, cal, params, cfg, origin=None, horizon_days=None, light=False):
        """Forecast from `origin` (default: last day of actuals). light = company, branch and category only."""
        self.ensure_one()
        lines, origin, horizon_end = self._make_lines(engine, cal, params, cfg, origin, horizon_days, light=light)
        self.env.cr.execute('DELETE FROM alkhuloodh_sf_forecast_line WHERE run_id = %s', (self.id,))
        self._insert_lines(lines)
        covered = [] if light else engine.chosen_products(origin, cfg.extra_product_ids.ids)
        self.write({'origin': origin.date(), 'horizon_end': horizon_end.date(), 'line_count': len(lines) if lines is not None else 0,
                    'engine_version': ENGINE_VERSION, 'has_products': not light,
                    'rule_ver': BACKTEST_RULE, 'covered_product_ids': ','.join(str(p) for p in covered)})

    def _execute_append(self, engine, cal, params, cfg, origin, horizon_days, products):
        """Adds the forecasts of `products` to this run, leaving everything else in it untouched."""
        self.ensure_one()
        lines, _origin, _horizon_end = self._make_lines(engine, cal, params, cfg, origin, horizon_days, only_products=products)
        self._insert_lines(lines)
        covered = {int(x) for x in (self.covered_product_ids or '').split(',') if x} | set(products)
        self.write({'line_count': (self.line_count or 0) + (len(lines) if lines is not None else 0),
                    'covered_product_ids': ','.join(str(p) for p in sorted(covered))})

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
        # product-in-branch lines are many: weekly snapshots keep only the first days that the accuracy screen compares
        cr.execute("""DELETE FROM alkhuloodh_sf_forecast_line l USING alkhuloodh_sf_run r
                       WHERE l.run_id = r.id AND r.kind = 'weekly' AND l.level = 'pair' AND l.date > r.origin + %s""",
                   (PAIR_KEEP_DAYS,))
        self.env.invalidate_all()
