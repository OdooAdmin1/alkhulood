import logging
import time
from datetime import date, datetime, time as dtime, timedelta

import pytz

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Gross sales: positive POS lines only (before returns), revenue excluding VAT, one row per day x branch x product.
# qty = units on priced lines; qty_free = units on zero-price (free) lines, kept apart as in the extract the model was built on.
# Branch = warehouse of the POS configuration's operation type.
LOAD_SQL = """
INSERT INTO alkhuloodh_sf_actual_daily
       (date, warehouse_id, product_id, categ_id, qty, qty_free, revenue, create_uid, create_date, write_uid, write_date)
SELECT (po.date_order AT TIME ZONE 'UTC' AT TIME ZONE %(tz)s)::date,
       spt.warehouse_id,
       pl.product_id,
       pt.categ_id,
       SUM(CASE WHEN pl.price_subtotal <> 0 THEN pl.qty ELSE 0 END),
       SUM(CASE WHEN pl.price_subtotal = 0 THEN pl.qty ELSE 0 END),
       SUM(pl.price_subtotal),
       %(uid)s, (now() AT TIME ZONE 'UTC'), %(uid)s, (now() AT TIME ZONE 'UTC')
  FROM pos_order_line pl
  JOIN pos_order po ON po.id = pl.order_id
  JOIN pos_session ps ON ps.id = po.session_id
  JOIN pos_config pc ON pc.id = ps.config_id
  JOIN stock_picking_type spt ON spt.id = pc.picking_type_id
  JOIN product_product pp ON pp.id = pl.product_id
  JOIN product_template pt ON pt.id = pp.product_tmpl_id
 WHERE po.state IN ('paid', 'done', 'invoiced')
   AND pl.qty > 0
   AND spt.warehouse_id IS NOT NULL
   AND po.date_order >= %(start_utc)s AND po.date_order < %(end_utc)s
   AND pl.product_id <> ALL(%(bad_products)s)
   AND COALESCE(pt.categ_id, -1) <> ALL(%(bad_categs)s)
 GROUP BY 1, 2, 3, 4
"""


def _local_day_start_utc(d, tz):
    return tz.localize(datetime.combine(d, dtime.min)).astimezone(pytz.utc).replace(tzinfo=None)


class AlkhuloodhSfActualDaily(models.Model):
    _name = 'alkhuloodh.sf.actual.daily'
    _description = 'Aggregated daily POS sales (gross, ex-VAT)'
    _order = 'date desc, warehouse_id, product_id'

    date = fields.Date(required=True, index=True)
    warehouse_id = fields.Many2one('stock.warehouse', string='Branch', required=True, index=True, ondelete='restrict')
    product_id = fields.Many2one('product.product', required=True, index=True, ondelete='restrict')
    categ_id = fields.Many2one('product.category', string='Category', index=True, ondelete='restrict')
    qty = fields.Float(string='Quantity sold', digits=(16, 3))
    qty_free = fields.Float(string='Free units', digits=(16, 3))
    revenue = fields.Float(string='Revenue ex-VAT', digits=(16, 3))

    def init(self):
        self.env.cr.execute(
            'CREATE UNIQUE INDEX IF NOT EXISTS alkhuloodh_sf_actual_daily_key_uniq '
            'ON alkhuloodh_sf_actual_daily (date, warehouse_id, product_id)')

    # ------------------------------------------------------------------ loading
    @api.model
    def _today_local(self, tz):
        return datetime.now(pytz.utc).astimezone(tz).date()

    @api.model
    def _load_range(self, d1, d2, cfg, tz):
        """Rebuild days d1..d2 (inclusive) from POS. Read-only on POS tables."""
        cr = self.env.cr
        cr.execute('DELETE FROM alkhuloodh_sf_actual_daily WHERE date >= %s AND date <= %s', (d1, d2))
        cr.execute(LOAD_SQL, {
            'tz': cfg.tz, 'uid': self.env.uid or 1,
            'start_utc': _local_day_start_utc(d1, tz), 'end_utc': _local_day_start_utc(d2 + timedelta(days=1), tz),
            'bad_products': cfg.excluded_product_ids.ids or [0], 'bad_categs': cfg.excluded_categ_ids.ids or [0],
        })
        return cr.rowcount

    @api.model
    def _refresh(self, full=False):
        """full=True rebuilds the whole history; otherwise only the last N days (first run = full)."""
        t0 = time.time()
        cfg = self.env['alkhuloodh.sf.config'].get_config()
        tz = pytz.timezone(cfg.tz)
        end = self._today_local(tz) - timedelta(days=1)          # last complete day
        cr = self.env.cr
        cr.execute('SELECT MAX(date) FROM alkhuloodh_sf_actual_daily')
        last = cr.fetchone()[0]
        if full or not last:
            start = cfg.history_start
        else:
            start = max(cfg.history_start, last - timedelta(days=cfg.overlap_days))
        total = 0
        cur = start
        while cur <= end:                                         # month by month keeps each statement small
            nxt = (cur.replace(day=1) + timedelta(days=32)).replace(day=1)
            d2 = min(end, nxt - timedelta(days=1))
            total += self._load_range(cur, d2, cfg, tz)
            cur = nxt
        self.env.invalidate_all()
        seconds = time.time() - t0
        cfg.sudo().write({'last_load_at': fields.Datetime.now(), 'last_load_range': f'{start} to {end}',
                          'last_load_rows': total, 'last_load_seconds': seconds})
        _logger.info('Sales forecast actuals loaded: %s to %s, %s rows, %.1fs', start, end, total, seconds)
        return total

    @api.model
    def _cron_load_actuals(self):
        self._refresh(full=False)
        self.env['alkhuloodh.sf.accuracy']._refresh()

    # ------------------------------------------------------------------ reconciliation
    @api.model
    def _reconcile(self):
        """Compact summary to compare against the extract the model was built and tested on."""
        cr = self.env.cr
        out = []
        cr.execute('SELECT COUNT(*), MIN(date), MAX(date), COUNT(DISTINCT product_id), COUNT(DISTINCT warehouse_id) '
                   'FROM alkhuloodh_sf_actual_daily')
        n, d1, d2, nprod, nwh = cr.fetchone()
        out.append(f'rows={n} days={d1}..{d2} products={nprod} branches={nwh}')
        cr.execute('SELECT COUNT(*) FROM (SELECT 1 FROM alkhuloodh_sf_actual_daily '
                   'GROUP BY warehouse_id, COALESCE(categ_id, -1)) x')
        out.append(f'branch x category cells={cr.fetchone()[0]}')
        cr.execute('SELECT ROUND(SUM(qty)::numeric, 0), ROUND(SUM(qty_free)::numeric, 0) FROM alkhuloodh_sf_actual_daily')
        out.append('units sold / free units: %s / %s' % cr.fetchone())
        cr.execute('SELECT EXTRACT(YEAR FROM date)::int, ROUND(SUM(revenue)::numeric, 2) FROM alkhuloodh_sf_actual_daily '
                   'GROUP BY 1 ORDER BY 1')
        out.append('revenue by year: ' + ', '.join(f'{y}: {v}' for y, v in cr.fetchall()))
        cr.execute('SELECT warehouse_id, ROUND(SUM(revenue)::numeric, 2) FROM alkhuloodh_sf_actual_daily '
                   'GROUP BY 1 ORDER BY 1')
        out.append('revenue by branch id: ' + ', '.join(f'{w}: {v}' for w, v in cr.fetchall()))
        cr.execute('SELECT ROUND(COALESCE(SUM(revenue), 0)::numeric, 2) FROM alkhuloodh_sf_actual_daily WHERE categ_id IS NULL')
        out.append(f'revenue with no category: {cr.fetchone()[0]}')
        cfg = self.env['alkhuloodh.sf.config'].get_config()
        cfg.sudo().write({'reconciliation_text': '\n'.join(out)})
        return True
