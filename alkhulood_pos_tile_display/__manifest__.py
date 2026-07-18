{
    'name': 'Al Khulood POS Tile Display (Price + Full Name)',
    'version': '19.0.1.0.0',
    'category': 'Point of Sale',
    'summary': 'Shows price badge on POS product tiles and displays full product names without truncation',
    'description': """
Al Khulood POS Tile Display
============================
- Adds a price badge to every POS product tile (image + list style)
- Removes the 2-line name truncation so full product names are always shown
- Slightly reduces product name font size so longer names fit cleanly

Built for Al Khulood Sweets (test DB: al-khulood-sweets-dashboardmaki).
""",
    'author': 'Amwal W.L.L.',
    'depends': ['point_of_sale'],
    'assets': {
        'point_of_sale.assets_prod': [
            'alkhulood_pos_tile_display/static/src/js/product_card_patch.js',
            'alkhulood_pos_tile_display/static/src/xml/product_card.xml',
            'alkhulood_pos_tile_display/static/src/scss/product_card.scss',
        ],
    },
    'installable': True,
    'auto_install': False,
    'license': 'LGPL-3',
}
