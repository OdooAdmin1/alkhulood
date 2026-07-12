{
    'name': 'Al Khulood Stock Dashboard',
    'version': '19.0.1.0.0',
    'summary': 'Minimal branch-wise stock dashboard with product images',
    'description': """
        Modern, minimal dashboard showing on-hand stock quantity for every
        product across all Al Khulood Sweets retail branches, with product
        image thumbnails for quick visual scanning.
    """,
    'category': 'Inventory',
    'author': 'Amwal W.L.L.',
    'depends': ['stock', 'product'],
    'data': [
        'security/ir.model.access.csv',
        'views/stock_dashboard_menu.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'al_khulood_stock_dashboard/static/src/js/stock_dashboard.js',
            'al_khulood_stock_dashboard/static/src/xml/stock_dashboard.xml',
            'al_khulood_stock_dashboard/static/src/css/stock_dashboard.css',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
