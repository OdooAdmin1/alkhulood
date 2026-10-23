# -*- coding: utf-8 -*-
"""The dashboard must report each transfer at sales price as well as at cost.

Sales value = quantity moved x the product's current sales price. Everything the
dashboard returns is built in raw SQL, so a wrong join or a missed GROUP BY
column shows up as a plausible-looking number rather than an error. These tests
pin the sales figures to hand-computed values.
"""
from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged

MODEL = 'al.internal.transfer.dashboard'


@tagged('post_install', '-at_install')
class TestInternalTransferSalesValue(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= cls.env.ref('stock.group_stock_user')
        cls.dashboard = cls.env[MODEL]
        cls.company = cls.env.company
        # the base query inner-joins product_category, so every test product needs one
        cls.category = cls.env.ref('product.product_category_goods')

        Warehouse = cls.env['stock.warehouse']
        cls.wh_src = Warehouse.create({'name': 'Test Source WH', 'code': 'ZTSRC'})
        cls.wh_dst = Warehouse.create({'name': 'Test Dest WH', 'code': 'ZTDST'})
        # a second internal location inside the source warehouse, for the
        # "same warehouse is not a transfer" case
        cls.src_shelf = cls.env['stock.location'].create({
            'name': 'ZT Shelf',
            'usage': 'internal',
            'location_id': cls.wh_src.lot_stock_id.id,
        })

        # cost 2.00, sells for 5.00 -> 3.00 of margin per unit
        cls.product = cls._make_product('ZT Baklava', cost=2.0, price=5.0)
        # priced at 0 on purpose: it must land in the zero_price health counter
        cls.freebie = cls._make_product('ZT Sample Box', cost=1.5, price=0.0)

        cls.today = cls.dashboard._today()

    @classmethod
    def _make_product(cls, name, cost, price):
        return cls.env['product.product'].create({
            'name': name,
            'is_storable': True,
            'type': 'consu',
            'categ_id': cls.category.id,
            'standard_price': cost,
            'list_price': price,
            'uom_id': cls.env.ref('uom.product_uom_unit').id,
        })

    def _transfer(self, product, qty, src=None, dst=None):
        """Move `qty` of `product` between two locations and validate it."""
        src = src or self.wh_src.lot_stock_id
        dst = dst or self.wh_dst.lot_stock_id
        self.env['stock.quant']._update_available_quantity(product, src, qty)
        move = self.env['stock.move'].create({
            'product_id': product.id,
            'product_uom_qty': qty,
            'product_uom': product.uom_id.id,
            'location_id': src.id,
            'location_dest_id': dst.id,
            'company_id': self.company.id,
        })
        move._action_confirm()
        move._action_assign()
        move.quantity = qty
        move.picked = True
        move._action_done()
        return move

    def _filters(self):
        """A window wide enough that the Bahrain-time shift cannot drop today's moves."""
        return {
            'date_from': fields.Date.to_string(self.today - timedelta(days=1)),
            'date_to': fields.Date.to_string(self.today + timedelta(days=1)),
            'src_ids': [self.wh_src.id],
            'dst_ids': [self.wh_dst.id],
        }

    def _row_for(self, data, product):
        rows = [r for r in data['detail']['rows'] if r['product_id'] == product.id]
        self.assertEqual(len(rows), 1,
                         "expected exactly one detail row for %s" % product.display_name)
        return rows[0]

    # ------------------------------------------------------------------
    def test_detail_row_carries_the_sales_value(self):
        """Sales value = quantity x the product's current sales price."""
        self._transfer(self.product, 10)

        data = self.dashboard.get_dashboard_data(self._filters())
        row = self._row_for(data, self.product)

        self.assertEqual(row['qty'], 10.0)
        self.assertEqual(row['sale_value'], 50.0, "10 units x 5.00 sales price")
        self.assertEqual(row['unit_cost'], 2.0)
        self.assertEqual(row['value'], 20.0, "cost value must stay quantity x cost")

    def test_detail_row_has_no_unit_price_column(self):
        """The per-unit sales price is not part of the table; only the value is."""
        self._transfer(self.product, 10)

        row = self._row_for(self.dashboard.get_dashboard_data(self._filters()), self.product)

        self.assertNotIn('unit_price', row)
        self.assertIn('sale_value', row)

    def test_kpis_total_sales_value_and_margin(self):
        """The KPI block totals the sales value and the margin over cost."""
        self._transfer(self.product, 10)   # cost 20, sales 50
        self._transfer(self.freebie, 4)    # cost  6, sales  0

        kpis = self.dashboard.get_dashboard_data(self._filters())['kpis']

        self.assertEqual(kpis['total_value'], 26.0)
        self.assertEqual(kpis['total_sale_value'], 50.0)
        self.assertEqual(kpis['margin'], 24.0)
        self.assertEqual(kpis['zero_price'], 1,
                         "the product with no sales price must be flagged")
        self.assertEqual(kpis['zero_cost'], 0)

    def test_sales_value_follows_the_variant_attribute_extra(self):
        """A variant surcharge is part of the sales price, so it must be in the value.

        The price extra lives on product_template_attribute_value, which the base
        query reaches through a LEFT JOIN LATERAL. If that join breaks, the value
        silently falls back to the bare template price (6 x 7.00 = 42.00).
        """
        attribute = self.env['product.attribute'].create({
            'name': 'ZT Size',
            'value_ids': [(0, 0, {'name': 'Large'})],
        })
        template = self.env['product.template'].create({
            'name': 'ZT Gift Box',
            'is_storable': True,
            'type': 'consu',
            'categ_id': self.category.id,
            'list_price': 7.0,
            'standard_price': 3.0,
            'attribute_line_ids': [(0, 0, {
                'attribute_id': attribute.id,
                'value_ids': [(6, 0, attribute.value_ids.ids)],
            })],
        })
        template.attribute_line_ids.product_template_value_ids.price_extra = 2.5
        variant = template.product_variant_ids[0]

        self._transfer(variant, 6)

        row = self._row_for(self.dashboard.get_dashboard_data(self._filters()), variant)
        self.assertEqual(row['sale_value'], 57.0, "6 x (7.00 template price + 2.50 extra)")

    def test_sales_value_is_sortable(self):
        """'sale_value' is an accepted sort column and really orders the rows."""
        self._transfer(self.product, 10)   # sales 50
        self._transfer(self.freebie, 4)    # sales  0

        filters = self._filters()
        desc = self.dashboard.get_dashboard_data(filters, sort='sale_value', order='desc')
        asc = self.dashboard.get_dashboard_data(filters, sort='sale_value', order='asc')

        self.assertEqual([r['sale_value'] for r in desc['detail']['rows']], [50.0, 0.0])
        self.assertEqual([r['sale_value'] for r in asc['detail']['rows']], [0.0, 50.0])

    def test_routes_trend_and_top_products_expose_sales_value(self):
        """Every aggregate the dashboard returns carries the sales figure."""
        self._transfer(self.product, 10)

        data = self.dashboard.get_dashboard_data(self._filters())

        route = next(r for r in data['routes']
                     if r['src_id'] == self.wh_src.id and r['dst_id'] == self.wh_dst.id)
        self.assertEqual(route['sale_value'], 50.0)
        self.assertEqual(sum(t['sale_value'] for t in data['trend']), 50.0)

        top = next(p for p in data['top_products'] if p['product_id'] == self.product.id)
        self.assertEqual(top['sale_value'], 50.0)

    def test_drawer_lines_carry_sales_value(self):
        """The drill-down drawer shows the same sales figures as the table."""
        self._transfer(self.product, 10)
        filters = self._filters()

        drawer = self.dashboard.get_transfers_for_period(
            filters, date_from=filters['date_from'], date_to=filters['date_to'])

        self.assertEqual(drawer['totals']['sale_value'], 50.0)
        self.assertEqual(sum(tr['sale_value'] for tr in drawer['transfers']), 50.0)
        lines = [line for tr in drawer['transfers'] for line in tr['lines']]
        self.assertEqual([line['sale_value'] for line in lines], [50.0])
        self.assertNotIn('unit_price', lines[0])

    def test_moves_inside_one_warehouse_are_still_excluded(self):
        """Adding the sales value must not widen what the dashboard counts."""
        self._transfer(self.product, 3, dst=self.src_shelf)

        filters = dict(self._filters(), dst_ids=[])
        kpis = self.dashboard.get_dashboard_data(filters)['kpis']
        self.assertEqual(kpis['total_value'], 0.0)
        self.assertEqual(kpis['total_sale_value'], 0.0)

    def test_export_builds_with_the_sales_column(self):
        """The Excel export must still build once the sales column is in it."""
        self._transfer(self.product, 10)

        content, filename = self.dashboard._build_xlsx(self._filters())

        self.assertTrue(filename.endswith('.xlsx'))
        self.assertTrue(content, "the export produced no bytes")
