{
    'name': 'Amwal Renewal Tracker',
    'version': '19.0.1.0.0',
    'category': 'Operations',
    'summary': 'Track CR, certificate, insurance and other renewals with automatic countdown and email reminders',
    'description': """
Amwal Renewal Tracker
======================
Track any recurring compliance document (Commercial Registration, certificates,
insurance policies, licenses, etc.) with:

- Automatic days-remaining countdown
- Color-coded status (Valid / Expiring Soon / Expired)
- Automated email reminders at configurable intervals before expiry
- Kanban, List and Calendar views
- Attach the renewal document itself to each record
- Per-client and per-responsible filtering

Built by Amwal W.L.L.
    """,
    'author': 'Amwal W.L.L.',
    'website': 'https://amwal.com',
    'license': 'LGPL-3',
    'depends': ['base', 'mail'],
    'data': [
        'security/renewal_security.xml',
        'security/ir.model.access.csv',
        'data/renewal_type_defaults.xml',
        'data/mail_template_data.xml',
        'data/ir_cron_data.xml',
        'views/renewal_tracker_views.xml',
        'views/renewal_type_views.xml',
        'views/renewal_menus.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
}
