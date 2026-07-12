# -*- coding: utf-8 -*-
from odoo import models, api


class AlKhuloodStockDashboard(models.AbstractModel):
    _name = 'al.khulood.stock.dashboard'
    _description = 'Al Khulood Stock Dashboard (all-locations stock overview)'

    @api.model
    def _get_all_warehouses(self):
        """Return every active warehouse (branches, main store, production, etc),
        ordered by name."""
        return self.env['stock.warehouse'].search([('active', '=', True)], order='name')

    @api.model
    def get_default_branch(self):
        """Return the stock location id (lot_stock_id) of the current user's
        default warehouse (res.users.property_warehouse_id), if set."""
        user_wh = self.env.user.property_warehouse_id
        if user_wh and user_wh.lot_stock_id:
            return user_wh.lot_stock_id.id
        return False

    @api.model
    def get_categories(self):
        """Return product categories that have at least one stockable product,
        sorted by name."""
        products = self.env['product.template'].search(self._stockable_domain())
        categ_ids = products.mapped('categ_id').ids
        categories = self.env['product.category'].browse(categ_ids).sorted(key=lambda c: c.name)
        return [{'id': c.id, 'name': c.name} for c in categories]

    @api.model
    def _stockable_domain(self):
        if 'is_storable' in self.env['product.template']._fields:
            return [('type', '=', 'consu'), ('is_storable', '=', True)]
        return [('type', '=', 'product')]

    @api.model
    def _variant_to_template_map(self, variant_ids):
        mapping = {}
        if variant_ids:
            for v in self.env['product.product'].browse(variant_ids):
                mapping[v.id] = v.product_tmpl_id.id
        return mapping

    @api.model
    def _compute_live_qty_map(self, products, location_ids):
        """{template_id: {location_id: qty}} using live stock.quant data.
        This is the ground-truth on-hand quantity, verified directly against
        stock.quant records (do not switch this back to context-based
        product.qty_available - that field ignores the 'location' context on
        this instance and silently returns the company-wide total instead).
        """
        qty_map = {}
        if not products:
            return qty_map

        quants = self.env['stock.quant'].read_group(
            domain=[
                ('location_id', 'in', location_ids),
                ('product_id.product_tmpl_id', 'in', products.ids),
            ],
            fields=['quantity:sum'],
            groupby=['product_id', 'location_id'],
            lazy=False,
        )
        variant_ids = list({q['product_id'][0] for q in quants if q.get('product_id')})
        variant_to_tmpl = self._variant_to_template_map(variant_ids)

        for q in quants:
            if not q.get('product_id') or not q.get('location_id'):
                continue
            tmpl_id = variant_to_tmpl.get(q['product_id'][0])
            location_id = q['location_id'][0]
            if not tmpl_id:
                continue
            qty_map.setdefault(tmpl_id, {})
            qty_map[tmpl_id][location_id] = qty_map[tmpl_id].get(location_id, 0.0) + q['quantity']
        return qty_map

    @api.model
    def _move_line_group_map(self, variant_ids, location_field, location_ids, as_of_datetime):
        """Sum done stock.move.line quantity, grouped by (product_id, location
        field), for moves that happened AFTER as_of_datetime. Used to roll
        live quantities back in time."""
        result = {}
        if not variant_ids:
            return result
        domain = [
            ('product_id', 'in', variant_ids),
            (location_field, 'in', location_ids),
            ('state', '=', 'done'),
            ('date', '>', as_of_datetime),
        ]
        groups = self.env['stock.move.line'].read_group(
            domain=domain,
            fields=['quantity:sum'],
            groupby=['product_id', location_field],
            lazy=False,
        )
        variant_to_tmpl = self._variant_to_template_map(variant_ids)
        for g in groups:
            if not g.get('product_id') or not g.get(location_field):
                continue
            tmpl_id = variant_to_tmpl.get(g['product_id'][0])
            location_id = g[location_field][0]
            if not tmpl_id:
                continue
            result.setdefault(tmpl_id, {})
            result[tmpl_id][location_id] = result[tmpl_id].get(location_id, 0.0) + g['quantity']
        return result

    @api.model
    def _compute_asof_qty_map(self, products, location_ids, as_of_datetime):
        """Reconstruct historical on-hand qty as of a given UTC datetime
        string by rolling back done stock moves that happened after that
        moment: qty_then = qty_now - moves_in_after + moves_out_after.
        """
        live_map = self._compute_live_qty_map(products, location_ids)
        if not products:
            return live_map

        # All variants that could be involved (from products, plus anything
        # already in the live map - same set really, but keep it simple).
        variants = self.env['product.product'].search([('product_tmpl_id', 'in', products.ids)])
        variant_ids = variants.ids

        moves_in_after = self._move_line_group_map(variant_ids, 'location_dest_id', location_ids, as_of_datetime)
        moves_out_after = self._move_line_group_map(variant_ids, 'location_id', location_ids, as_of_datetime)

        qty_map = {}
        tmpl_ids = set(live_map) | set(moves_in_after) | set(moves_out_after)
        for tmpl_id in tmpl_ids:
            qty_map[tmpl_id] = {}
            locs = set(live_map.get(tmpl_id, {})) | set(moves_in_after.get(tmpl_id, {})) | set(moves_out_after.get(tmpl_id, {}))
            for loc in locs:
                now_qty = live_map.get(tmpl_id, {}).get(loc, 0.0)
                in_after = moves_in_after.get(tmpl_id, {}).get(loc, 0.0)
                out_after = moves_out_after.get(tmpl_id, {}).get(loc, 0.0)
                qty_map[tmpl_id][loc] = now_qty - in_after + out_after
        return qty_map

    @api.model
    def get_dashboard_data(self, search='', offset=0, limit=60, categ_ids=None,
                            group_by_category=False, negative_only=False,
                            branch_ids=None, as_of_datetime=None):
        """Return location columns + a page of products with per-location qty.

        :param search: text search on product name / internal reference
        :param offset: pagination offset
        :param limit: page size
        :param categ_ids: optional list of product.category ids to filter by
        :param group_by_category: if True, order results by category then name
        :param negative_only: if True, only return products with negative
            stock in at least one of branch_ids (or all locations if not set)
        :param branch_ids: optional list of location ids (lot_stock_id) to
            check for negative stock against; defaults to all locations
        :param as_of_datetime: optional UTC datetime string
            ('YYYY-MM-DD HH:MM:SS') to compute historical stock as of that
            moment instead of the live on-hand quantity
        """
        warehouses = self._get_all_warehouses()
        location_ids = warehouses.mapped('lot_stock_id').ids
        branches = [{'id': w.lot_stock_id.id, 'name': w.name} for w in warehouses]
        filter_location_ids = branch_ids or location_ids

        product_domain = self._stockable_domain()
        if categ_ids:
            product_domain = ['&'] + product_domain + [('categ_id', 'in', categ_ids)]
        if search:
            product_domain = ['&'] + product_domain + [
                '|', ('name', 'ilike', search), ('default_code', 'ilike', search)
            ]

        Product = self.env['product.template']
        order = 'categ_id, name' if group_by_category else 'name'

        def qty_map_for(products):
            if as_of_datetime:
                return self._compute_asof_qty_map(products, location_ids, as_of_datetime)
            return self._compute_live_qty_map(products, location_ids)

        if negative_only:
            all_products = Product.search(product_domain, order=order)
            qty_map = qty_map_for(all_products)
            candidates = [
                p for p in all_products
                if any(qty_map.get(p.id, {}).get(loc, 0.0) < 0 for loc in filter_location_ids)
            ]
            total_count = len(candidates)
            page_products = candidates[offset:offset + limit]
        else:
            total_count = Product.search_count(product_domain)
            page_products = Product.search(product_domain, order=order, offset=offset, limit=limit)
            qty_map = qty_map_for(page_products)

        result_products = []
        for p in page_products:
            branch_qty = qty_map.get(p.id, {})
            row = {
                'id': p.id,
                'name': p.name,
                'default_code': p.default_code or '',
                'uom': p.uom_id.name,
                'has_image': bool(p.image_128),
                'categ_id': p.categ_id.id,
                'categ_name': p.categ_id.name,
                'branches': {str(loc_id): round(branch_qty.get(loc_id, 0.0), 2) for loc_id in location_ids},
                'total': round(sum(branch_qty.values()), 2),
            }
            result_products.append(row)

        return {
            'branches': branches,
            'products': result_products,
            'total_count': total_count,
        }
