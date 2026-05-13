{
    'name': 'POS Report Cost Extension',
    'version': '16.0.1.0.0',
    'category': 'Point of Sale',
    'summary': 'Adds the Standard Price (Cost) to the POS Orders Report.',
    'depends': [
        'point_of_sale',
        'product',
    ],
    'data': [
        # XML views are omitted as requested, the field will be added via Studio/UI
    ],
    'installable': True,
    'auto_install': False,
}