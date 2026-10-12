# -*- coding: utf-8 -*-
"""Read-only aggregation for the Internal Transfers dashboard.

Design notes
------------
* Nothing here writes to the database; every statement is a SELECT.
* Warehouses are resolved from the location tree (stock_location.parent_path vs the
  warehouse view location) instead of stock_location.warehouse_id, because on this
  database the stored warehouse_id of the HB and TB sub-locations is swapped.
* Everything is aggregated in SQL (the move table has ~250k internal rows); no record loops.
* Value = done quantity x product CURRENT cost. product_product.standard_price is a
  company-dependent jsonb ({"<company_id>": cost}).
* Dates are filtered/grouped in Bahrain time (UTC+3, no DST); stock_move.date is stored in UTC.
"""
import io
from datetime import timedelta, datetime, time

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError

BAHRAIN_OFFSET = timedelta(hours=3)
PRIMARY_CODES = ('MS', 'GW')          # Main Store, Garden: default "From" filter
GRAINS = ('day', 'week', 'month')
EXPORT_LINE_CAP = 100000
DRAWER_LINE_CAP = 3000
DETAIL_SORT = {
    'product': 'd.product_name',
    'category': 'd.categ_name',
    'from': 'ws.code',
    'to': 'wd.code',
    'qty': 'd.qty',
    'unit_cost': 'd.unit_cost',
    'transfers': 'd.transfers',
    'value': 'd.value',
}


class InternalTransferDashboard(models.AbstractModel):
    _name = 'al.internal.transfer.dashboard'
    _description = 'Internal Transfers Dashboard'

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _check_access(self):
        if not self.env.user.has_group('stock.group_stock_user'):
            raise AccessError(_("You are not allowed to open the Internal Transfers dashboard."))

    @api.model
    def _today(self):
        """Today's date in Bahrain time."""
        return (fields.Datetime.now() + BAHRAIN_OFFSET).date()

    @api.model
    def _normalize_filters(self, filters):
        filters = filters or {}
        today = self._today()
        try:
            d_from = fields.Date.to_date(filters.get('date_from')) or today.replace(day=1)
            d_to = fields.Date.to_date(filters.get('date_to')) or today
        except (ValueError, TypeError):
            raise UserError(_("Invalid date filter."))
        if d_from > d_to:
            d_from, d_to = d_to, d_from

        def ids(key):
            try:
                return [int(i) for i in (filters.get(key) or [])]
            except (ValueError, TypeError):
                return []

        return {
            'date_from': d_from,
            'date_to': d_to,
            'src_ids': ids('src_ids'),
            'dst_ids': ids('dst_ids'),
            'categ_ids': ids('categ_ids'),
            'search': (filters.get('search') or '').strip()[:100],
        }

    @api.model
    def _base_query(self, f):
        """Return (cte_sql, params). The CTE `base` has one row per done internal move
        that crosses warehouses, already filtered."""
        params = {
            'lang': self.env.lang or 'en_US',
            'dt_from': datetime.combine(f['date_from'], time.min) - BAHRAIN_OFFSET,
            'dt_to': datetime.combine(f['date_to'] + timedelta(days=1), time.min) - BAHRAIN_OFFSET,
            'company_ids': self.env.companies.ids,
        }
        where = [
            "sm.state = 'done'",
            "sm.quantity > 0",
            "sm.date >= %(dt_from)s",
            "sm.date < %(dt_to)s",
            "sm.company_id = ANY(%(company_ids)s)",
            "ls.warehouse_id <> ld.warehouse_id",
        ]
        if f['src_ids']:
            where.append("ls.warehouse_id = ANY(%(src_ids)s)")
            params['src_ids'] = f['src_ids']
        if f['dst_ids']:
            where.append("ld.warehouse_id = ANY(%(dst_ids)s)")
            params['dst_ids'] = f['dst_ids']
        if f['categ_ids']:
            paths = self.env['product.category'].browse(f['categ_ids']).exists().mapped('parent_path')
            where.append("pc.parent_path LIKE ANY(%(categ_paths)s)")
            params['categ_paths'] = [p + '%' for p in paths if p]
        if f['search']:
            term = f['search'].replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            where.append(
                "(COALESCE(pt.name ->> %(lang)s, pt.name ->> 'en_US') ILIKE %(search)s"
                " OR pp.default_code ILIKE %(search)s OR sm.reference ILIKE %(search)s)")
            params['search'] = '%' + term + '%'

        cte = """
        WITH loc_wh AS (
            -- internal location -> owning warehouse, from the location tree
            SELECT DISTINCT ON (l.id) l.id AS location_id, w.id AS warehouse_id
              FROM stock_warehouse w
              JOIN stock_location v ON v.id = w.view_location_id
              JOIN stock_location l
                ON left(l.parent_path, length(v.parent_path)) = v.parent_path
             WHERE l.usage = 'internal'
             ORDER BY l.id, length(v.parent_path) DESC
        ),
        base AS (
            SELECT sm.id AS move_id,
                   sm.date AS move_date,
                   sm.reference AS reference,
                   sm.picking_id AS picking_id,
                   COALESCE(sm.picking_id::text, 'm' || sm.id::text) AS transfer_key,
                   sm.product_id AS product_id,
                   COALESCE(pt.name ->> %(lang)s, pt.name ->> 'en_US') AS product_name,
                   pp.default_code AS default_code,
                   pc.complete_name AS categ_name,
                   COALESCE(uu.name ->> %(lang)s, uu.name ->> 'en_US') AS uom_name,
                   (sm.product_uom <> pt.uom_id) AS uom_mismatch,
                   ls.warehouse_id AS src_wh,
                   ld.warehouse_id AS dst_wh,
                   sm.quantity AS qty,
                   COALESCE((pp.standard_price ->> sm.company_id::text)::numeric, 0) AS unit_cost,
                   sm.quantity * COALESCE((pp.standard_price ->> sm.company_id::text)::numeric, 0) AS value
              FROM stock_move sm
              JOIN loc_wh ls ON ls.location_id = sm.location_id
              JOIN loc_wh ld ON ld.location_id = sm.location_dest_id
              JOIN product_product pp ON pp.id = sm.product_id
              JOIN product_template pt ON pt.id = pp.product_tmpl_id
              JOIN product_category pc ON pc.id = pt.categ_id
              JOIN uom_uom uu ON uu.id = pt.uom_id
             WHERE __WHERE__
        )
        """.replace('__WHERE__', " AND ".join(where))
        return cte, params

    @api.model
    def _fetch_detail(self, cte, params, sort='value', order='desc', limit=None, offset=0):
        order_col = DETAIL_SORT.get(sort, DETAIL_SORT['value'])
        direction = 'ASC' if (order or '').lower() == 'asc' else 'DESC'
        query = cte + """
        SELECT d.product_id, d.product_name, d.default_code, d.categ_name, d.uom_name,
               d.src_wh, d.dst_wh, ws.code AS src_code, wd.code AS dst_code,
               d.qty, d.unit_cost, d.transfers, d.value,
               COUNT(*) OVER () AS total_rows
          FROM (
                SELECT product_id, product_name, default_code, categ_name, uom_name,
                       src_wh, dst_wh,
                       SUM(qty) AS qty,
                       MAX(unit_cost) AS unit_cost,
                       COUNT(DISTINCT transfer_key) AS transfers,
                       SUM(value) AS value
                  FROM base
                 GROUP BY product_id, product_name, default_code, categ_name, uom_name, src_wh, dst_wh
               ) d
          JOIN stock_warehouse ws ON ws.id = d.src_wh
          JOIN stock_warehouse wd ON wd.id = d.dst_wh
         ORDER BY %s %s NULLS LAST, d.product_name, ws.code, wd.code
        """ % (order_col, direction)
        qparams = dict(params)
        if limit is not None:
            query += " LIMIT %(limit)s OFFSET %(offset)s"
            qparams['limit'] = int(limit)
            qparams['offset'] = int(offset)
        self.env.cr.execute(query, qparams)
        return self.env.cr.dictfetchall()

    # ------------------------------------------------------------------
    # RPC: filter options
    # ------------------------------------------------------------------
    @api.model
    def get_options(self):
        self._check_access()
        warehouses = self.env['stock.warehouse'].search([('company_id', 'in', self.env.companies.ids)])
        # MS first, then GW, then the rest alphabetically by code
        wh_list = sorted(
            ({'id': w.id, 'code': w.code, 'name': w.name} for w in warehouses),
            key=lambda w: (PRIMARY_CODES.index(w['code']) if w['code'] in PRIMARY_CODES else 99, w['code']),
        )
        categories = self.env['product.category'].search([], order='complete_name')
        currency = self.env.company.currency_id
        today = self._today()
        return {
            'warehouses': wh_list,
            'default_src_ids': [w['id'] for w in wh_list if w['code'] in PRIMARY_CODES],
            'categories': [{'id': c.id, 'name': c.complete_name} for c in categories],
            'currency': {
                'symbol': currency.symbol or currency.name,
                'name': currency.name,
                'decimal_places': currency.decimal_places,
                'position': currency.position,
            },
            'today': fields.Date.to_string(today),
        }

    # ------------------------------------------------------------------
    # RPC: dashboard data
    # ------------------------------------------------------------------
    @api.model
    def get_dashboard_data(self, filters=None, grain='auto', sort='value', order='desc', page=1, limit=25):
        self._check_access()
        f = self._normalize_filters(filters)
        cte, params = self._base_query(f)
        cr = self.env.cr

        days = (f['date_to'] - f['date_from']).days + 1
        if grain not in GRAINS:
            grain = 'day' if days <= 62 else ('week' if days <= 270 else 'month')
        page = max(int(page or 1), 1)
        limit = min(max(int(limit or 25), 5), 200)

        # KPIs + data health in one pass
        cr.execute(cte + """
            SELECT COALESCE(SUM(value), 0) AS total_value,
                   COUNT(DISTINCT transfer_key) AS transfers,
                   COUNT(*) AS lines,
                   COUNT(DISTINCT product_id) AS products,
                   COUNT(*) FILTER (WHERE uom_mismatch) AS uom_mismatch,
                   COUNT(*) FILTER (WHERE unit_cost = 0) AS zero_cost
              FROM base
        """, params)
        k = cr.dictfetchone()
        kpis = {
            'total_value': float(k['total_value']),
            'transfers': k['transfers'],
            'lines': k['lines'],
            'products': k['products'],
            'uom_mismatch': k['uom_mismatch'],
            'zero_cost': k['zero_cost'],
        }

        # route matrix source
        cr.execute(cte + """
            SELECT src_wh, dst_wh, SUM(value) AS value,
                   COUNT(DISTINCT transfer_key) AS transfers, COUNT(*) AS lines
              FROM base GROUP BY src_wh, dst_wh ORDER BY SUM(value) DESC
        """, params)
        routes = [{
            'src_id': r['src_wh'], 'dst_id': r['dst_wh'], 'value': float(r['value']),
            'transfers': r['transfers'], 'lines': r['lines'],
        } for r in cr.dictfetchall()]

        # trend (Bahrain local time buckets)
        tparams = dict(params, grain=grain)
        cr.execute(cte + """
            SELECT date_trunc(%(grain)s, move_date + interval '3 hours')::date AS period,
                   SUM(value) AS value, COUNT(DISTINCT transfer_key) AS transfers
              FROM base GROUP BY 1 ORDER BY 1
        """, tparams)
        trend = [{
            'period': fields.Date.to_string(r['period']), 'value': float(r['value']),
            'transfers': r['transfers'],
        } for r in cr.dictfetchall()]

        # top products
        cr.execute(cte + """
            SELECT product_id, product_name, default_code, uom_name,
                   SUM(value) AS value, SUM(qty) AS qty
              FROM base
             GROUP BY product_id, product_name, default_code, uom_name
             ORDER BY SUM(value) DESC, product_name
             LIMIT 10
        """, params)
        top_products = [{
            'product_id': r['product_id'], 'name': r['product_name'], 'code': r['default_code'] or '',
            'uom': r['uom_name'], 'value': float(r['value']), 'qty': float(r['qty']),
        } for r in cr.dictfetchall()]

        # detail table (product x route), paginated in SQL
        rows = self._fetch_detail(cte, params, sort, order, limit=limit, offset=(page - 1) * limit)
        detail = {
            'rows': [{
                'product_id': r['product_id'], 'product': r['product_name'], 'code': r['default_code'] or '',
                'category': r['categ_name'], 'uom': r['uom_name'],
                'src_id': r['src_wh'], 'dst_id': r['dst_wh'],
                'src_code': r['src_code'], 'dst_code': r['dst_code'],
                'qty': float(r['qty']), 'unit_cost': float(r['unit_cost']),
                'transfers': r['transfers'], 'value': float(r['value']),
            } for r in rows],
            'total': rows[0]['total_rows'] if rows else 0,
            'page': page,
            'limit': limit,
        }

        return {
            'filters': {
                'date_from': fields.Date.to_string(f['date_from']),
                'date_to': fields.Date.to_string(f['date_to']),
            },
            'grain': grain,
            'kpis': kpis,
            'routes': routes,
            'trend': trend,
            'top_products': top_products,
            'detail': detail,
        }

    # ------------------------------------------------------------------
    # RPC: drill-down - the transfers (with product lines) of a day / week / month
    # ------------------------------------------------------------------
    @api.model
    def get_transfers_for_period(self, filters=None, date_from=None, date_to=None):
        self._check_access()
        f = self._normalize_filters(filters)
        try:
            b_from = fields.Date.to_date(date_from)
            b_to = fields.Date.to_date(date_to)
        except (ValueError, TypeError):
            raise UserError(_("Invalid date."))
        if not b_from or not b_to:
            raise UserError(_("Please pick a period."))
        # the bucket can never reach outside the dashboard's own date range
        f['date_from'] = max(f['date_from'], b_from)
        f['date_to'] = min(f['date_to'], b_to)
        result = {
            'date_from': fields.Date.to_string(f['date_from']),
            'date_to': fields.Date.to_string(f['date_to']),
            'totals': {'value': 0.0, 'transfers': 0, 'lines': 0},
            'transfers': [],
            'truncated': False,
            'shown_lines': 0,
        }
        if f['date_from'] > f['date_to']:
            return result

        cte, params = self._base_query(f)
        cr = self.env.cr
        cr.execute(cte + """
            SELECT COALESCE(SUM(value), 0) AS total_value,
                   COUNT(DISTINCT transfer_key) AS transfers,
                   COUNT(*) AS lines
              FROM base
        """, params)
        t = cr.dictfetchone()
        result['totals'] = {'value': float(t['total_value']), 'transfers': t['transfers'], 'lines': t['lines']}

        cr.execute(cte + """
            SELECT b.transfer_key, b.picking_id, b.reference, b.move_date, b.move_id,
                   b.product_name, b.default_code, b.uom_name, b.uom_mismatch,
                   b.qty, b.unit_cost, b.value,
                   b.src_wh, b.dst_wh, ws.code AS src_code, wd.code AS dst_code
              FROM base b
              JOIN stock_warehouse ws ON ws.id = b.src_wh
              JOIN stock_warehouse wd ON wd.id = b.dst_wh
             ORDER BY b.move_date DESC, b.move_id DESC
             LIMIT %(cap)s
        """, dict(params, cap=DRAWER_LINE_CAP + 1))
        rows = cr.dictfetchall()
        result['truncated'] = len(rows) > DRAWER_LINE_CAP
        rows = rows[:DRAWER_LINE_CAP]
        result['shown_lines'] = len(rows)

        transfers = {}   # insertion order = latest transfer first
        for r in rows:
            key = '%s:%s:%s' % (r['transfer_key'], r['src_wh'], r['dst_wh'])
            tr = transfers.get(key)
            if tr is None:
                tr = transfers[key] = {
                    'key': key,
                    'picking_id': r['picking_id'],
                    'reference': r['reference'] or '',
                    'date': (r['move_date'] + BAHRAIN_OFFSET).strftime('%Y-%m-%d %H:%M'),
                    'src_id': r['src_wh'], 'dst_id': r['dst_wh'],
                    'src_code': r['src_code'], 'dst_code': r['dst_code'],
                    'value': 0.0,
                    'line_count': 0,
                    'lines': [],
                }
            value = float(r['value'])
            tr['value'] += value
            tr['line_count'] += 1
            tr['lines'].append({
                'product': r['product_name'], 'code': r['default_code'] or '',
                'uom': r['uom_name'], 'uom_mismatch': bool(r['uom_mismatch']),
                'qty': float(r['qty']), 'unit_cost': float(r['unit_cost']), 'value': value,
            })
        for tr in transfers.values():
            tr['lines'].sort(key=lambda line: (-line['value'], line['product']))
        result['transfers'] = list(transfers.values())
        return result

    # ------------------------------------------------------------------
    # Excel export
    # ------------------------------------------------------------------
    @api.model
    def _build_xlsx(self, filters):
        self._check_access()
        import xlsxwriter

        f = self._normalize_filters(filters)
        cte, params = self._base_query(f)
        cr = self.env.cr
        currency = self.env.company.currency_id
        dec = currency.decimal_places or 3

        warehouses = self.env['stock.warehouse'].search([('company_id', 'in', self.env.companies.ids)])
        wh_code = {w.id: w.code for w in warehouses}
        wh_name = {w.id: w.name for w in warehouses}

        cr.execute(cte + """
            SELECT src_wh, dst_wh, SUM(value) AS value, COUNT(DISTINCT transfer_key) AS transfers
              FROM base GROUP BY src_wh, dst_wh
        """, params)
        routes = cr.dictfetchall()
        cr.execute(cte + """
            SELECT COALESCE(SUM(value), 0) AS total_value,
                   COUNT(DISTINCT transfer_key) AS transfers,
                   COUNT(DISTINCT product_id) AS products,
                   COUNT(*) AS lines
              FROM base
        """, params)
        k = cr.dictfetchone()
        detail = self._fetch_detail(cte, params, 'value', 'desc')

        cr.execute(cte + """
            SELECT b.move_date, b.reference, b.product_name, b.default_code, b.categ_name,
                   ws.code AS src_code, wd.code AS dst_code,
                   b.qty, b.uom_name, b.unit_cost, b.value
              FROM base b
              JOIN stock_warehouse ws ON ws.id = b.src_wh
              JOIN stock_warehouse wd ON wd.id = b.dst_wh
             ORDER BY b.move_date DESC, b.move_id DESC
             LIMIT %(cap)s
        """, dict(params, cap=EXPORT_LINE_CAP + 1))
        lines = cr.dictfetchall()
        truncated = len(lines) > EXPORT_LINE_CAP
        lines = lines[:EXPORT_LINE_CAP]

        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {'in_memory': True, 'remove_timezone': True})
        money_fmt = '#,##0.' + '0' * dec if dec else '#,##0'
        fmt = {
            'title': wb.add_format({'bold': True, 'font_size': 16, 'font_color': '#1F2A44'}),
            'bold': wb.add_format({'bold': True}),
            'note': wb.add_format({'italic': True, 'font_color': '#6B7280'}),
            'warn': wb.add_format({'italic': True, 'font_color': '#B45309'}),
            'head': wb.add_format({'bold': True, 'font_color': '#FFFFFF', 'bg_color': '#1F2A44',
                                   'border': 1, 'align': 'center', 'valign': 'vcenter', 'text_wrap': True}),
            'text': wb.add_format({'border': 1}),
            'int': wb.add_format({'border': 1, 'num_format': '#,##0'}),
            'qty': wb.add_format({'border': 1, 'num_format': '#,##0.###'}),
            'money': wb.add_format({'border': 1, 'num_format': money_fmt}),
            'money_bold': wb.add_format({'border': 1, 'num_format': money_fmt, 'bold': True, 'bg_color': '#EEF2FF'}),
            'label_bold': wb.add_format({'border': 1, 'bold': True, 'bg_color': '#EEF2FF'}),
            'date': wb.add_format({'border': 1, 'num_format': 'yyyy-mm-dd hh:mm'}),
        }

        def names(ids):
            return ', '.join(wh_name.get(i, str(i)) for i in ids) if ids else 'All'

        # ---- Summary
        ws = wb.add_worksheet('Summary')
        ws.set_column(0, 0, 24)
        ws.set_column(1, 40, 14)
        ws.write(0, 0, 'Internal Transfers between Warehouses', fmt['title'])
        ws.write(1, 0, 'Period (Bahrain time)')
        ws.write(1, 1, '%s  to  %s' % (f['date_from'], f['date_to']))
        ws.write(2, 0, 'From warehouses')
        ws.write(2, 1, names(f['src_ids']))
        ws.write(3, 0, 'To warehouses')
        ws.write(3, 1, names(f['dst_ids']))
        ws.write(4, 0, 'Category / search')
        cat_names = self.env['product.category'].browse(f['categ_ids']).exists().mapped('complete_name')
        ws.write(4, 1, '%s / %s' % (', '.join(cat_names) or 'All', f['search'] or '-'))
        ws.write(5, 0, 'Valuation', fmt['warn'])
        ws.write(5, 1, "Quantity x the product's CURRENT cost (%s), not the cost at transfer date." % currency.name, fmt['warn'])
        ws.write(6, 0, 'Generated')
        ws.write(6, 1, (fields.Datetime.now() + BAHRAIN_OFFSET).strftime('%Y-%m-%d %H:%M') + ' (Bahrain)')

        ws.write(8, 0, 'Total value (%s)' % currency.name, fmt['bold'])
        ws.write_number(8, 1, float(k['total_value']), fmt['money_bold'])
        ws.write(9, 0, 'Transfers', fmt['bold'])
        ws.write_number(9, 1, k['transfers'], fmt['int'])
        ws.write(10, 0, 'Products moved', fmt['bold'])
        ws.write_number(10, 1, k['products'], fmt['int'])
        ws.write(11, 0, 'Move lines', fmt['bold'])
        ws.write_number(11, 1, k['lines'], fmt['int'])

        senders = sorted({r['src_wh'] for r in routes},
                         key=lambda i: -sum(float(r['value']) for r in routes if r['src_wh'] == i))
        receivers = sorted({r['dst_wh'] for r in routes},
                           key=lambda i: -sum(float(r['value']) for r in routes if r['dst_wh'] == i))
        cell = {(r['src_wh'], r['dst_wh']): float(r['value']) for r in routes}
        top = 14
        ws.write(top - 1, 0, 'Value matrix (%s): rows = From, columns = To' % currency.name, fmt['bold'])
        ws.write(top, 0, 'From \\ To', fmt['head'])
        for j, d in enumerate(receivers, start=1):
            ws.write(top, j, wh_code.get(d, d), fmt['head'])
        ws.write(top, len(receivers) + 1, 'Total sent', fmt['head'])
        for i, s in enumerate(senders, start=top + 1):
            ws.write(i, 0, '%s - %s' % (wh_code.get(s, s), wh_name.get(s, '')), fmt['label_bold'])
            row_total = 0.0
            for j, d in enumerate(receivers, start=1):
                v = cell.get((s, d), 0.0)
                row_total += v
                if v:
                    ws.write_number(i, j, v, fmt['money'])
                else:
                    ws.write_blank(i, j, None, fmt['text'])
            ws.write_number(i, len(receivers) + 1, row_total, fmt['money_bold'])
        tot_row = top + 1 + len(senders)
        ws.write(tot_row, 0, 'Total received', fmt['label_bold'])
        grand = 0.0
        for j, d in enumerate(receivers, start=1):
            col_total = sum(cell.get((s, d), 0.0) for s in senders)
            grand += col_total
            ws.write_number(tot_row, j, col_total, fmt['money_bold'])
        ws.write_number(tot_row, len(receivers) + 1, grand, fmt['money_bold'])
        ws.freeze_panes(top + 1, 1)

        # ---- By product & route
        wd_ = wb.add_worksheet('By Product & Route')
        heads = ['Product', 'Internal Ref', 'Category', 'From', 'To', 'UoM', 'Quantity',
                 'Unit cost (current)', 'Transfers', 'Value (%s)' % currency.name]
        widths = [42, 16, 26, 8, 8, 9, 12, 14, 10, 16]
        for c, (h, w) in enumerate(zip(heads, widths)):
            wd_.write(0, c, h, fmt['head'])
            wd_.set_column(c, c, w)
        for r, row in enumerate(detail, start=1):
            wd_.write(r, 0, row['product_name'], fmt['text'])
            wd_.write(r, 1, row['default_code'] or '', fmt['text'])
            wd_.write(r, 2, row['categ_name'] or '', fmt['text'])
            wd_.write(r, 3, row['src_code'], fmt['text'])
            wd_.write(r, 4, row['dst_code'], fmt['text'])
            wd_.write(r, 5, row['uom_name'] or '', fmt['text'])
            wd_.write_number(r, 6, float(row['qty']), fmt['qty'])
            wd_.write_number(r, 7, float(row['unit_cost']), fmt['money'])
            wd_.write_number(r, 8, row['transfers'], fmt['int'])
            wd_.write_number(r, 9, float(row['value']), fmt['money'])
        wd_.autofilter(0, 0, max(len(detail), 1), len(heads) - 1)
        wd_.freeze_panes(1, 1)

        # ---- Transfer lines
        wl = wb.add_worksheet('Transfer Lines')
        heads = ['Date (Bahrain)', 'Reference', 'Product', 'Internal Ref', 'Category', 'From', 'To',
                 'Quantity', 'UoM', 'Unit cost (current)', 'Value (%s)' % currency.name]
        widths = [18, 18, 42, 16, 26, 8, 8, 12, 9, 14, 16]
        for c, (h, w) in enumerate(zip(heads, widths)):
            wl.write(0, c, h, fmt['head'])
            wl.set_column(c, c, w)
        for r, row in enumerate(lines, start=1):
            wl.write_datetime(r, 0, row['move_date'] + BAHRAIN_OFFSET, fmt['date'])
            wl.write(r, 1, row['reference'] or '', fmt['text'])
            wl.write(r, 2, row['product_name'], fmt['text'])
            wl.write(r, 3, row['default_code'] or '', fmt['text'])
            wl.write(r, 4, row['categ_name'] or '', fmt['text'])
            wl.write(r, 5, row['src_code'], fmt['text'])
            wl.write(r, 6, row['dst_code'], fmt['text'])
            wl.write_number(r, 7, float(row['qty']), fmt['qty'])
            wl.write(r, 8, row['uom_name'] or '', fmt['text'])
            wl.write_number(r, 9, float(row['unit_cost']), fmt['money'])
            wl.write_number(r, 10, float(row['value']), fmt['money'])
        if truncated:
            wl.write(len(lines) + 2, 0,
                     'Only the latest %d lines are listed. Narrow the filters to see the rest; '
                     'the other sheets include everything.' % EXPORT_LINE_CAP, fmt['warn'])
        wl.autofilter(0, 0, max(len(lines), 1), len(heads) - 1)
        wl.freeze_panes(1, 0)

        wb.close()
        filename = 'internal_transfers_%s_%s.xlsx' % (f['date_from'], f['date_to'])
        return output.getvalue(), filename
