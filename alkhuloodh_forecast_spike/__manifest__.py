{
    'name': 'Alkhuloodh Forecast Spike (TEST ONLY)',
    'version': '19.0.0.1.0',
    'summary': 'Throwaway test: can the forecast engine run inside Odoo.sh? Staging only.',
    'description': 'Reads aggregated POS sales (read-only), runs the forecast engine, and logs '
                   'time and memory. Writes only to its own log table. Uninstall after the test.',
    'category': 'Hidden',
    'author': 'Amwal W.L.L.',
    'license': 'LGPL-3',
    'depends': ['point_of_sale', 'stock'],
    'external_dependencies': {'python': ['pandas', 'numpy']},
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron.xml',
        'views/spike_views.xml',
    ],
    'installable': True,
    'application': False,
}
