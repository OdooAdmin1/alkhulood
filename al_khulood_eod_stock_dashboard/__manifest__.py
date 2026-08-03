{
    "name": "Al Khulood Sweets - EOD Stock Dashboard",
    "version": "19.0.1.0.0",
    "summary": "Live and end-of-day (11 PM) closing stock dashboard per branch for top-selling products",
    "description": """
Al Khulood Sweets - End of Day Stock Dashboard
================================================

Purpose: certain fresh/daily-produced products must sell out each day. If a
branch is closing with too much balance on hand, that branch is over-supplied
and Amwal/the client can act (reduce next-day supply, etc). This dashboard
surfaces that per-branch closing balance for the ~20 tracked products.

Design note: this module stores NOTHING and runs no cron. Every figure -
live or "as of a past date/time" - is computed on demand by reusing the exact
mechanism behind Odoo's own Inventory > Reporting > Stock > "Inventory at
Date" feature (the `to_date` / `location` context keys on a product's
qty_available, which replay done stock.move.line history). This keeps a
single source of truth with core Odoo instead of maintaining a parallel
snapshot table.

Provides:
- Live tab: current on-hand quantity per tracked product, per branch.
- Closing Balance tab: pick any date/time (defaults to the most recently
  completed business day at 23:55) and see the reconstructed balance per
  product per branch at that moment - the same number the native
  "Inventory at Date" report would show, pre-filtered to the tracked
  products and laid out as a branch x product grid.
- Optional short trend view (last few days), computed on demand.
- A checkbox on the product form ("Track in EOD Dashboard") to control which
  products appear (used to scope this to the ~20 top sellers), and a flag
  on the warehouse form to mark which warehouses are a retail branch.

This module is fully read-only with respect to stock: it never writes to
stock.quant, stock.move, or valuation, and does not create any new
persisted table of its own.
""",
    "author": "Amwal W.L.L.",
    "company": "Amwal W.L.L.",
    "website": "https://amwal.solutions",
    "category": "Inventory/Reporting",
    "license": "OPL-1",
    "depends": ["stock", "product", "web"],
    "data": [
        "security/ir.model.access.csv",
        "views/eod_stock_dashboard_views.xml",
        "views/product_view_inherit.xml",
        "views/eod_stock_dashboard_menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "al_khulood_eod_stock_dashboard/static/src/scss/eod_stock_dashboard.scss",
            "al_khulood_eod_stock_dashboard/static/src/js/eod_stock_dashboard.js",
            "al_khulood_eod_stock_dashboard/static/src/xml/eod_stock_dashboard.xml",
        ],
    },
    "installable": True,
    "application": False,
}
