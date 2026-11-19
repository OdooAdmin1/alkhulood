# -*- coding: utf-8 -*-
"""Moves whose header destination is the warehouse VIEW location must be counted.

On Al Khulood the Jid Ali (JB) 'Internal Transfers' operation type defaults to
'Physical Locations/JB' (a view location) while the move lines land on JB/Stock.
The dashboard used to drop those moves, so JB looked empty.
"""
from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged

MODEL = 'al.internal.transfer.dashboard'


@tagged('post_install', '-at_install')
class TestInternalTransferViewDestination(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= cls.env.ref('stock.group_stock_user')
        cls.dashboard = cls.env[MODEL]
        Warehouse = cls.env['stock.warehouse']
        cls.wh_src = Warehouse.create({'name': 'Test View Source', 'code': 'ZVSRC'})
        cls.wh_dst = Warehouse.create({'name': 'Test View Dest', 'code': 'ZVDST'})
        cls.product = cls.env['product.product'].create({
            'name': 'ZV Halwa',
            'is_storable': True,
            'type': 'consu',
            'categ_id': cls.env.ref('product.product_category_goods').id,
            'standard_price': 2.0,
            'list_price': 5.0,
            'uom_id': cls.env.ref('uom.product_uom_unit').id,
        })
        cls.today = cls.dashboard._today()

    def _filters(self):
        return {
            'date_from': fields.Date.to_string(self.today - timedelta(days=1)),
            'date_to': fields.Date.to_string(self.today + timedelta(days=1)),
            'src_ids': [self.wh_src.id],
            'dst_ids': [self.wh_dst.id],
        }

    def _transfer_with_view_header(self, qty):
        """Validate a normal internal transfer, then point the move HEADER at the
        destination warehouse's view location (the move lines stay on its Stock),
        which is exactly the shape of the JB data."""
        src, dst = self.wh_src.lot_stock_id, self.wh_dst.lot_stock_id
        self.env['stock.quant']._update_available_quantity(self.product, src, qty)
        move = self.env['stock.move'].create({
            'product_id': self.product.id,
            'product_uom_qty': qty,
            'product_uom': self.product.uom_id.id,
            'location_id': src.id,
            'location_dest_id': dst.id,
            'company_id': self.env.company.id,
        })
        move._action_confirm()
        move._action_assign()
        move.quantity = qty
        move.picked = True
        move._action_done()
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE stock_move SET location_dest_id = %s WHERE id = %s",
            (self.wh_dst.view_location_id.id, move.id))
        self.env.invalidate_all()
        return move

    def test_view_destination_move_is_counted(self):
        self._transfer_with_view_header(10)

        data = self.dashboard.get_dashboard_data(self._filters())

        self.assertEqual(data['kpis']['transfers'], 1)
        self.assertEqual(data['kpis']['total_value'], 20.0, "10 units x 2.00 cost")
        rows = [r for r in data['detail']['rows'] if r['product_id'] == self.product.id]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['qty'], 10.0)

    def test_view_destination_move_with_non_internal_lines_is_ignored(self):
        """A view header whose lines did not land internal is not a stock transfer."""
        move = self._transfer_with_view_header(10)
        customer = self.env.ref('stock.stock_location_customers')
        self.env.cr.execute(
            "UPDATE stock_move_line SET location_dest_id = %s WHERE move_id = %s",
            (customer.id, move.id))
        self.env.invalidate_all()

        data = self.dashboard.get_dashboard_data(self._filters())

        self.assertEqual(data['kpis']['transfers'], 0)
