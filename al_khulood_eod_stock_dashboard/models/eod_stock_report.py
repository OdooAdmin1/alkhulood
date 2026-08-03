from datetime import timedelta

from odoo import api, fields, models
from odoo.osv import expression

# The client wants exactly these 9 retail branches on the dashboard - never
# MAIN STORE, SHOP RETURN, or any other warehouse that might exist/get added.
# Matched by (case-insensitive) substring against the warehouse name, which
# tolerates the "... Branch" suffix Al Khulood uses (e.g. "Tubli Branch").
BRANCH_NAME_KEYWORDS = [
    "Muharraq",
    "Hamad Town",
    "Tubli",
    "Budaiya",
    "Manama",
    "Jid Ali",
    "Riffa",
    "Galali",
    "Al Baraha",
]


class AlKhuloodEodStockReport(models.Model):
    """Reporting model with no database table and no stored history.

    Every number this dashboard shows - whether "right now" or "as of
    31/07/2026 23:55" - is computed on demand using the same mechanism
    behind Odoo's native Inventory > Reporting > Stock > "Inventory at
    Date" feature: passing a `to_date` (and `location`) key in the context
    when reading a product's qty_available. Odoo then reconstructs the
    quantity by replaying done stock.move.line records up to that datetime,
    instead of reading the live stock.quant table.

    Deliberately no cron, no snapshot table, nothing to maintain: this
    model is just a thin, read-only convenience layer over that existing
    core computation, batched across branches/products for the dashboard.
    """

    _name = "al.khulood.eod.stock.report"
    _description = "Al Khulood EOD Stock Report (computed on demand)"
    _auto = False  # no DB table is created for this model

    @api.model
    def _get_tracked_products(self, product_ids=None, category_ids=None):
        domain = [("eod_dashboard_tracked", "=", True)]
        if product_ids:
            domain = expression.AND([domain, [("id", "in", product_ids)]])
        if category_ids:
            domain = expression.AND([domain, [("categ_id", "in", category_ids)]])
        return self.env["product.product"].search(domain, order="name")

    @api.model
    def _get_branch_warehouses(self, warehouse_ids=None):
        if warehouse_ids:
            # Explicit ids requested (e.g. store chips already scoped to the
            # allowed 9) - trust them, still gated by the flag.
            domain = [("id", "in", warehouse_ids), ("eod_dashboard_branch", "=", True)]
            return self.env["stock.warehouse"].search(domain, order="name")

        name_domain = expression.OR([[("name", "ilike", kw)] for kw in BRANCH_NAME_KEYWORDS])
        domain = expression.AND([[("eod_dashboard_branch", "=", True)], name_domain])
        return self.env["stock.warehouse"].search(domain, order="name")

    @api.model
    def _quantities_at(self, products, warehouse, to_date=None):
        """{product_id: qty} in warehouse's stock location. If to_date is
        falsy, this is the live/current quantity (normal quant read). If
        to_date is set (a datetime string, e.g. '2026-07-31 23:55:00'),
        this is reconstructed as of that moment - identical logic to the
        native Inventory at Date report."""
        location = warehouse.lot_stock_id
        if not location or not products:
            return {p.id: 0.0 for p in products}
        ctx = {"location": location.ids}
        if to_date:
            ctx["to_date"] = to_date
        scoped = products.with_context(**ctx)
        return {p.id: p.qty_available for p in scoped}

    @api.model
    def get_categories(self):
        """Distinct product categories among currently tracked products,
        for the category filter chips."""
        products = self._get_tracked_products()
        categories = products.mapped("categ_id")
        return [{"id": c.id, "name": c.display_name} for c in categories]

    @api.model
    def get_dashboard_data(self, warehouse_ids=None, product_ids=None,
                            category_ids=None, as_of=None):
        """as_of: optional datetime string. When provided, the 'closing'
        rows are reconstructed as of that moment. When omitted, 'closing'
        just mirrors 'live'."""
        products = self._get_tracked_products(product_ids, category_ids)
        warehouses = self._get_branch_warehouses(warehouse_ids)

        if not products or not warehouses:
            return {
                "stores": [],
                "products": [],
                "live": [],
                "closing": [],
                "as_of": as_of,
                "server_now": fields.Datetime.to_string(fields.Datetime.now()),
            }

        live_rows = []
        closing_rows = []
        for warehouse in warehouses:
            live_qty = self._quantities_at(products, warehouse, to_date=None)
            closing_qty = (
                self._quantities_at(products, warehouse, to_date=as_of) if as_of else live_qty
            )
            for product in products:
                threshold = product.eod_dashboard_low_stock_threshold
                live_rows.append(
                    {
                        "warehouse_id": warehouse.id,
                        "product_id": product.id,
                        "qty": live_qty.get(product.id, 0.0),
                        "threshold": threshold,
                    }
                )
                closing_rows.append(
                    {
                        "warehouse_id": warehouse.id,
                        "product_id": product.id,
                        "qty": closing_qty.get(product.id, 0.0),
                        "threshold": threshold,
                    }
                )

        return {
            "stores": [{"id": w.id, "name": w.name} for w in warehouses],
            "products": [
                {
                    "id": p.id,
                    "name": p.display_name,
                    "categ_id": p.categ_id.id,
                    "categ_name": p.categ_id.name,
                }
                for p in products
            ],
            "live": live_rows,
            "closing": closing_rows,
            "as_of": as_of,
            "server_now": fields.Datetime.to_string(fields.Datetime.now()),
        }

    @api.model
    def get_trend_data(self, warehouse_ids=None, product_ids=None, category_ids=None,
                        days=7, closing_time="23:55:00"):
        """Closing balance for each of the last `days` days, computed on
        demand (one to_date reconstruction per day per branch) - nothing
        is stored, so this recomputes from move history every time it's
        called. Kept to a short default range since each day/branch pair
        is its own query."""
        products = self._get_tracked_products(product_ids, category_ids)
        warehouses = self._get_branch_warehouses(warehouse_ids)
        if not products or not warehouses:
            return {"rows": []}

        rows = []
        today = fields.Date.context_today(self)
        for delta in range(days):
            day = today - timedelta(days=delta)
            to_date = f"{day.isoformat()} {closing_time}"
            for warehouse in warehouses:
                qty_by_product = self._quantities_at(products, warehouse, to_date=to_date)
                for product in products:
                    rows.append(
                        {
                            "date": day.isoformat(),
                            "warehouse_id": warehouse.id,
                            "product_id": product.id,
                            "qty": qty_by_product.get(product.id, 0.0),
                        }
                    )
        return {"rows": rows}
