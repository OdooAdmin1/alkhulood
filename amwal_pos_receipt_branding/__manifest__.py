{
    'name': 'POS Receipt Branding - Amwal',
    'version': '19.0.1.0.0',
    'summary': 'Replace the "Powered by Odoo" receipt footer with Amwal W.L.L and its logo.',
    'description': """
Replaces the footer of the Point of Sale customer receipt.

Odoo prints "Powered by Odoo" at the bottom of every receipt. This module
overrides that single block with "Powered by Amwal W.L.L", preceded by the
Amwal mark sized just above the cap height of the surrounding text.

The logo ships as a PNG with a real alpha channel rather than the white-matted
source artwork, so it composites onto a receipt of any colour without showing
a white box behind it.
    """,
    'author': 'Amwal W.L.L.',
    'website': 'https://amwal.solutions',
    'category': 'Sales/Point of Sale',
    'depends': ['point_of_sale'],
    'assets': {
        'point_of_sale._assets_pos': [
            'amwal_pos_receipt_branding/static/src/scss/receipt_branding.scss',
            'amwal_pos_receipt_branding/static/src/xml/order_receipt.xml',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
