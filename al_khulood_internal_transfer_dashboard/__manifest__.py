# -*- coding: utf-8 -*-
{
    'name': 'Al Khulood - Internal Transfers Dashboard',
    'version': '19.0.1.0.0',
    'category': 'Inventory/Reporting',
    'summary': 'Value and quantity of stock moved between warehouses (Main Store, Garden, shops) with filters and Excel export',
    'description': """
Read-only dashboard for warehouse-to-warehouse (internal) transfers.

* Excludes vendor receipts, customer deliveries, production and inventory adjustments.
* Warehouses are resolved from the location tree (not from stock.location.warehouse_id).
* Value = done quantity x the product's CURRENT cost (no historical valuation in Odoo 19 for internal moves).
* Filters: period, from / to warehouse, product category, text search.
* Excel export: summary matrix, product x route detail, transfer lines.
""",
    'author': 'Amwal W.L.L.',
    'website': 'https://amwal.solutions',
    'license': 'LGPL-3',
    'depends': ['stock', 'web'],
    'data': [
        'views/dashboard_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'al_khulood_internal_transfer_dashboard/static/src/scss/dashboard.scss',
            'al_khulood_internal_transfer_dashboard/static/src/js/dashboard.js',
            'al_khulood_internal_transfer_dashboard/static/src/xml/dashboard.xml',
        ],
    },
    'installable': True,
    'application': False,
}
