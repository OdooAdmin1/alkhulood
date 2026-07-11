{
    'name': 'Khulood Shop Dashboard',
    'version': '19.0.1.0.0',
    'category': 'Inventory',
    'summary': 'Per-shop dashboard: native product stock + previous day POS performance',
    'description': """
Khulood Shop Dashboard
=======================
Single-page Owl dashboard for shop staff:

1. Native products and their current stock in the shop (defaults to the
   user's own warehouse via the standard "Default Warehouse" user field).
2. Below that, previous day's sales performance for those same products.
3. Click a product in the performance list to drill into its previous
   day's individual sales (time, qty, amount).
4. A branch switcher at the top to check stock/performance at any other shop.

Does not touch existing product or user form views. "Native" is just a
Product Tag; which warehouses count as shops is a system parameter.
""",
    'author': 'Al Khulood Sweets',
    'depends': ['base', 'stock', 'point_of_sale', 'product'],
    'data': [
        'security/ir.model.access.csv',
        'data/product_tag_data.xml',
        'views/dashboard_menu.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'khulood_shop_dashboard/static/src/js/**/*',
            'khulood_shop_dashboard/static/src/xml/**/*',
            'khulood_shop_dashboard/static/src/scss/**/*',
        ],
    },
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
