# -*- coding: utf-8 -*-
from datetime import datetime, time as dtime, timedelta

import pytz

from odoo import api, fields, models

NATIVE_TAG_XMLID = 'khulood_shop_dashboard.product_tag_native'
SHOP_WAREHOUSE_IDS_PARAM = 'khulood_shop_dashboard.shop_warehouse_ids'
POS_DONE_STATES = ['paid', 'done', 'invoiced']


class ShopDashboard(models.AbstractModel):
    """Backend data provider for the Owl Shop Dashboard client action.

    Abstract (no table): it only exposes read methods called over RPC from
    the frontend, it doesn't store anything itself.
    """
    _name = 'shop.dashboard'
    _description = 'Shop Dashboard Data Provider'

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------
    @api.model
    def _get_shop_warehouses(self):
        """Warehouses flagged as real shops via the
        'khulood_shop_dashboard.shop_warehouse_ids' system parameter
        (comma-separated ids). Falls back to ALL warehouses if the
        parameter hasn't been set yet, so the dashboard isn't empty on
        first install.
        """
        param = self.env['ir.config_parameter'].sudo().get_param(SHOP_WAREHOUSE_IDS_PARAM)
        if param:
            ids = [int(x) for x in param.split(',') if x.strip().isdigit()]
            warehouses = self.env['stock.warehouse'].browse(ids).exists()
        else:
            warehouses = self.env['stock.warehouse'].search([])
        return warehouses

    @api.model
    def _get_native_tag(self):
        return self.env.ref(NATIVE_TAG_XMLID, raise_if_not_found=False)

    # ------------------------------------------------------------------
    # Bootstrap: shop list + which one the current user should land on
    # ------------------------------------------------------------------
    @api.model
    def get_bootstrap_data(self):
        warehouses = self._get_shop_warehouses()
        user = self.env.user
        default_warehouse = user.property_warehouse_id
        if default_warehouse not in warehouses:
            default_warehouse = warehouses[:1]
        return {
            'warehouses': [{'id': wh.id, 'name': wh.name} for wh in warehouses],
            'default_warehouse_id': default_warehouse.id if default_warehouse else False,
        }

    # ------------------------------------------------------------------
    # Main dashboard payload for a chosen warehouse
    # ------------------------------------------------------------------
    @api.model
    def get_dashboard_data(self, warehouse_id):
        warehouse = self.env['stock.warehouse'].browse(warehouse_id)
        empty = {
            'warehouse_name': warehouse.name if warehouse.exists() else '',
            'stock_rows': [],
            'performance_rows': [],
            'generated_at': fields.Datetime.now(),
        }
        if not warehouse.exists():
            return empty

        native_tag = self._get_native_tag()
        if not native_tag:
            return empty

        products = self.env['product.product'].search([
            ('product_tmpl_id.product_tag_ids', 'in', native_tag.id),
        ])
        if not products:
            return empty

        stock_by_product = self._get_current_stock(products.ids, warehouse)
        sales_by_product = self._get_yesterday_sales(products.ids, warehouse)

        stock_rows = []
        performance_rows = []
        for product in products:
            qty_on_hand = stock_by_product.get(product.id, 0.0)
            sold = sales_by_product.get(product.id, {'qty': 0.0, 'revenue': 0.0})

            stock_rows.append({
                'product_id': product.id,
                'name': product.display_name,
                'category': product.categ_id.name,
                'uom': product.uom_id.name,
                'qty_on_hand': qty_on_hand,
            })
            # Only list a product in "previous day's performance" if it
            # actually had activity yesterday - an empty wall of zeros
            # isn't useful for reorder decisions.
            if sold['qty'] or sold['revenue']:
                performance_rows.append({
                    'product_id': product.id,
                    'name': product.display_name,
                    'uom': product.uom_id.name,
                    'qty_sold_yesterday': sold['qty'],
                    'revenue_yesterday': sold['revenue'],
                })

        stock_rows.sort(key=lambda r: r['name'])
        performance_rows.sort(key=lambda r: r['qty_sold_yesterday'], reverse=True)

        return {
            'warehouse_name': warehouse.name,
            'stock_rows': stock_rows,
            'performance_rows': performance_rows,
            'generated_at': fields.Datetime.now(),
        }

    # ------------------------------------------------------------------
    # Drill-down: one product's previous day sales, line by line
    # ------------------------------------------------------------------
    @api.model
    def get_product_detail(self, product_id, warehouse_id):
        warehouse = self.env['stock.warehouse'].browse(warehouse_id)
        product = self.env['product.product'].browse(product_id)
        if not warehouse.exists() or not product.exists():
            return {'product_name': '', 'lines': [], 'total_qty': 0.0, 'total_revenue': 0.0}

        start_utc, end_utc = self._yesterday_utc_bounds()
        pos_configs = self._get_pos_configs_for_warehouse(warehouse)

        order_lines = self.env['pos.order.line'].search([
            ('product_id', '=', product.id),
            ('order_id.config_id', 'in', pos_configs.ids),
            ('order_id.date_order', '>=', start_utc),
            ('order_id.date_order', '<=', end_utc),
            ('order_id.state', 'in', POS_DONE_STATES),
        ], order='id')

        lines = []
        total_qty = 0.0
        total_revenue = 0.0
        for line in order_lines:
            order = line.order_id
            lines.append({
                'order_reference': order.pos_reference or order.name,
                'time': fields.Datetime.to_string(order.date_order),
                'qty': line.qty,
                'amount': line.price_subtotal_incl,
            })
            total_qty += line.qty
            total_revenue += line.price_subtotal_incl

        return {
            'product_name': product.display_name,
            'lines': lines,
            'total_qty': total_qty,
            'total_revenue': total_revenue,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @api.model
    def _get_current_stock(self, product_ids, warehouse):
        if not warehouse.view_location_id:
            return {}
        quant_groups = self.env['stock.quant'].read_group(
            domain=[
                ('product_id', 'in', product_ids),
                ('location_id', 'child_of', warehouse.view_location_id.id),
                ('location_id.usage', '=', 'internal'),
            ],
            fields=['quantity:sum'],
            groupby=['product_id'],
        )
        return {g['product_id'][0]: g['quantity'] for g in quant_groups}

    @api.model
    def _get_pos_configs_for_warehouse(self, warehouse):
        return self.env['pos.config'].search([
            ('picking_type_id.warehouse_id', '=', warehouse.id),
        ])

    @api.model
    def _yesterday_utc_bounds(self):
        """Yesterday's local calendar-day boundaries (per current user's tz),
        converted to naive UTC datetimes for filtering date_order.
        """
        tz_name = self.env.user.tz or 'UTC'
        try:
            user_tz = pytz.timezone(tz_name)
        except Exception:
            user_tz = pytz.UTC

        now_local = datetime.now(user_tz)
        yesterday_date = (now_local - timedelta(days=1)).date()
        start_local = user_tz.localize(datetime.combine(yesterday_date, dtime.min))
        end_local = user_tz.localize(datetime.combine(yesterday_date, dtime.max))
        start_utc = start_local.astimezone(pytz.UTC).replace(tzinfo=None)
        end_utc = end_local.astimezone(pytz.UTC).replace(tzinfo=None)
        return start_utc, end_utc

    @api.model
    def _get_yesterday_sales(self, product_ids, warehouse):
        pos_configs = self._get_pos_configs_for_warehouse(warehouse)
        if not pos_configs:
            return {}

        start_utc, end_utc = self._yesterday_utc_bounds()

        groups = self.env['pos.order.line'].read_group(
            domain=[
                ('product_id', 'in', product_ids),
                ('order_id.config_id', 'in', pos_configs.ids),
                ('order_id.date_order', '>=', start_utc),
                ('order_id.date_order', '<=', end_utc),
                ('order_id.state', 'in', POS_DONE_STATES),
            ],
            fields=['qty:sum', 'price_subtotal_incl:sum'],
            groupby=['product_id'],
        )
        return {
            g['product_id'][0]: {'qty': g['qty'], 'revenue': g['price_subtotal_incl']}
            for g in groups
        }
