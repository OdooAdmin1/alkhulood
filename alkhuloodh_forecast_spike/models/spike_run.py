import logging
import resource
import time
import traceback
from datetime import date

import numpy as np
import pandas as pd

from odoo import fields, models
from odoo.tools import file_open

from .. import akf_engine

_logger = logging.getLogger(__name__)

# Same exclusions as the extract used for the offline model (staging copy has the same ids).
BAD_PRODUCTS = (2471, 6596, 6302, 6631, 6673, 6147, 7060)
BAD_CATEGS = (26, 42, 35)
DEFAULT_END = '2026-10-26'      # last full day in the staging copy; lets us compare with the offline run
HORIZON_END = '2027-03-31'

SQL = """
SELECT (po.date_order AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Bahrain')::date AS day,
       spt.warehouse_id AS warehouse_id,
       pl.product_id AS product_id,
       COALESCE(pt.categ_id, -1) AS categ_id,
       SUM(pl.price_subtotal) AS rev,
       SUM(pl.qty) AS qty
  FROM pos_order_line pl
  JOIN pos_order po ON po.id = pl.order_id
  JOIN pos_session ps ON ps.id = po.session_id
  JOIN pos_config pc ON pc.id = ps.config_id
  JOIN stock_picking_type spt ON spt.id = pc.picking_type_id
  JOIN product_product pp ON pp.id = pl.product_id
  JOIN product_template pt ON pt.id = pp.product_tmpl_id
 WHERE po.state IN ('paid', 'done', 'invoiced')
   AND pl.qty > 0
   AND pl.product_id NOT IN %s
   AND COALESCE(pt.categ_id, -1) NOT IN %s
   AND (po.date_order AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Bahrain')::date <= %s
 GROUP BY 1, 2, 3, 4
"""


def _rss_mb():
    try:
        with open('/proc/self/status') as f:
            for line in f:
                if line.startswith('VmRSS:'):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    return 0.0


class AlkhuloodhSpikeRun(models.Model):
    _name = 'alkhuloodh.spike.run'
    _description = 'Forecast engine spike run (test only)'
    _order = 'id desc'

    name = fields.Char(default='Spike run')
    state = fields.Selection([('draft', 'Draft'), ('done', 'Done'), ('failed', 'Failed')], default='draft')
    via = fields.Selection([('button', 'Button (web request)'), ('cron', 'Scheduler')], default='button')
    end_date = fields.Date(string='Last day of data used')
    rows_loaded = fields.Integer()
    seconds_load = fields.Float(digits=(10, 2), string='Seconds: load from DB')
    seconds_prepare = fields.Float(digits=(10, 2), string='Seconds: prepare')
    seconds_run = fields.Float(digits=(10, 2), string='Seconds: forecast')
    seconds_total = fields.Float(digits=(10, 2), string='Seconds: total')
    cpu_seconds = fields.Float(digits=(10, 2), string='CPU seconds used')
    peak_mb = fields.Float(digits=(10, 0), string='Process memory after run (MB)')
    company_forecast_total = fields.Float(digits=(16, 2), string='Company forecast, sum to 31 Mar 2027')
    yearly_totals = fields.Text()
    log = fields.Text()

    # ---- buttons
    def action_run_now(self):
        self.ensure_one()
        self._do_run('button')
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id,
                'view_mode': 'form', 'target': 'current'}

    def action_queue_cron(self):
        cron = self.env.ref('alkhuloodh_forecast_spike.cron_spike')
        cron._trigger()
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': 'Queued', 'type': 'success', 'sticky': False,
                           'message': 'Queued. Refresh the Runs list in a minute to see the result.'}}

    def _cron_run_spike(self):
        self.create({'via': 'cron'})._do_run('cron')

    # ---- the test itself (read-only on POS tables; writes only to this log record)
    def _do_run(self, via):
        self.ensure_one()
        lines = []
        t0 = time.time()
        cpu0 = time.process_time()
        end = self.env['ir.config_parameter'].sudo().get_param('alkhuloodh_spike.end_date', DEFAULT_END)
        try:
            lines.append(f'start via={via} rss={_rss_mb():.0f}MB end_date={end}')
            with self.env.cr.savepoint():
                self.env.cr.execute("SET LOCAL statement_timeout = '100s'")
                t = time.time()
                self.env.cr.execute(SQL, (BAD_PRODUCTS, BAD_CATEGS, end))
                rows = self.env.cr.fetchall()
                sales = pd.DataFrame(rows, columns=['day', 'warehouse_id', 'product_id', 'categ_id', 'rev', 'qty'])
                for c in ('rev', 'qty'):
                    sales[c] = sales[c].astype(float)
                t_load = time.time() - t
            lines.append(f'loaded {len(sales)} rows in {t_load:.1f}s rss={_rss_mb():.0f}MB')
            sales['day'] = pd.to_datetime(sales['day'])
            yearly = sales.groupby(sales.day.dt.year).rev.sum().round(2).to_dict()

            with file_open('alkhuloodh_forecast_spike/data/calendar.csv', 'rb') as f:
                cal = pd.read_csv(f, parse_dates=['date'])
            t = time.time()
            engine = akf_engine.Engine(sales, cal)
            t_prep = time.time() - t
            lines.append(f'prepared in {t_prep:.1f}s rss={_rss_mb():.0f}MB')

            t = time.time()
            origin = engine.END
            dates = pd.date_range(origin + pd.Timedelta(days=1), HORIZON_END)
            F, sh = engine.run(origin, dates)
            t_run = time.time() - t
            total = float(F['company'][0].sum())
            lines.append(f'forecast origin={origin.date()} days={len(dates)} cells={len(F["cell"])} '
                         f'products={len(F["prod_rev"])} in {t_run:.1f}s rss={_rss_mb():.0f}MB')
            nxt = F['company'][0].head(7).round(0).tolist()
            lines.append(f'company next 7 days: {nxt}')
            self.write({
                'state': 'done', 'via': via, 'end_date': origin.date(), 'rows_loaded': len(sales),
                'seconds_load': t_load, 'seconds_prepare': t_prep, 'seconds_run': t_run,
                'seconds_total': time.time() - t0, 'cpu_seconds': time.process_time() - cpu0,
                'peak_mb': max(_rss_mb(), resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0),
                'company_forecast_total': total, 'yearly_totals': str(yearly), 'log': '\n'.join(lines),
            })
        except Exception:
            _logger.exception('Forecast spike failed')
            lines.append(traceback.format_exc())
            self.write({'state': 'failed', 'via': via, 'seconds_total': time.time() - t0,
                        'cpu_seconds': time.process_time() - cpu0, 'log': '\n'.join(lines)})
