{
    'name': 'Alkhuloodh Sales Forecast',
    'version': '19.0.1.0.0',
    'summary': 'Hijri-aware sales forecast (quantity and revenue) by branch, category and product',
    'description': """
Step 1: settings, Hijri calendar, aggregated daily POS sales and a reconciliation summary.
Step 2: forecast runs (nightly refresh, weekly snapshot), forecast lines with low/expected/high ranges.
Step 3: forecast dashboard (filters, range chart, predicted vs actual, comparisons, weekly/monthly/Hijri roll-ups).
Step 4: confirmed dates (Eid, Ramadan, Ashura, Hijri New Year), holidays and closures.
Step 5: accuracy tracking (frozen forecasts vs actuals) with a drift warning.
Reads POS tables only; writes only to its own tables.
""",
    'category': 'Point of Sale',
    'author': 'Amwal W.L.L.',
    'license': 'LGPL-3',
    'depends': ['point_of_sale', 'stock', 'product'],
    'external_dependencies': {'python': ['pandas', 'numpy']},
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/ir_cron.xml',
        'views/run_views.xml',
        'views/actual_views.xml',
        'views/calendar_views.xml',
        'views/config_views.xml',
        'views/override_views.xml',
        'views/accuracy_views.xml',
        'views/dashboard_views.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'alkhuloodh_sales_forecast/static/src/dashboard/dashboard.js',
            'alkhuloodh_sales_forecast/static/src/dashboard/dashboard.xml',
            'alkhuloodh_sales_forecast/static/src/dashboard/dashboard.scss',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': True,
}
