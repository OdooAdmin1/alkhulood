{
    'name': 'ZB Custom Check Print (Stub - Deprecated)',
    'version': '19.0.1.0.0',
    'category': 'Uncategorized',
    'summary': 'Deprecated stub. Original functionality removed during 16->19 upgrade.',
    'description': """
    This is an intentionally empty stub module.

    The original 'zb_custom_check_print' module was removed from the
    Al Khulood Sweets database as part of the Odoo 16 -> 19 upgrade.
    This stub exists only so that Odoo.sh's upgrade process does not
    treat the module as a missing/ghost addon when it restores the
    upgraded backup (which still references the module as installed).

    Once the production upgrade completes successfully, this module
    can be safely uninstalled via Settings > Apps.
    """,
    'author': 'Amwal W.L.L.',
    'license': 'LGPL-3',
    'depends': ['base'],
    'data': [],
    'installable': True,
    'auto_install': False,
}
