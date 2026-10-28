import csv
import logging

from odoo.tools import file_open

_logger = logging.getLogger(__name__)

# Client-specific defaults (Al Khulood Sweets): products and categories that are not real sales.
# Same ids as the extract used to build and test the model. Editable afterwards in Configuration.
BAD_PRODUCT_IDS = [2471, 6596, 6302, 6631, 6673, 6147, 7060]   # Rnd off, RND, ADJ x3, Gift Card, Free Product GIFT PACKAGE 2026
BAD_CATEG_IDS = [26, 42, 35]                                     # test, PACKING ITEMS, Non Active Codes


def post_init_hook(env):
    # 1. settings record with default exclusions
    Config = env['alkhuloodh.sf.config']
    cfg = Config.search([], limit=1)
    if not cfg:
        cfg = Config.create({})
    products = env['product.product'].with_context(active_test=False).browse(BAD_PRODUCT_IDS).exists()
    categs = env['product.category'].browse(BAD_CATEG_IDS).exists()
    cfg.write({'excluded_product_ids': [(6, 0, products.ids)], 'excluded_categ_ids': [(6, 0, categs.ids)]})
    _logger.info('Sales forecast: %s excluded products, %s excluded categories set by default', len(products), len(categs))

    # 2. base Hijri calendar (Umm al-Qura, 2022-12-01 .. 2032-12-31). Only rows that do not exist yet are added.
    Day = env['alkhuloodh.sf.calendar.day']
    have = set(Day.search([]).mapped('date'))
    rows = []
    with file_open('alkhuloodh_sales_forecast/data/calendar_base.csv', 'r') as f:
        for r in csv.DictReader(f):
            d = r['date']
            if d in have:
                continue
            rows.append({
                'date': d,
                'hijri_year': int(r['hijri_year']), 'hijri_month': int(r['hijri_month']), 'hijri_day': int(r['hijri_day']),
                'ramadan_day': int(r['ramadan_day']),
                'pos_eid_fitr': r['pos_eid_fitr'] or False, 'pos_eid_adha': r['pos_eid_adha'] or False,
                'pos_hijri_new_year': r['pos_hijri_new_year'] or False, 'pos_ashura': r['pos_ashura'] or False,
                'fixed_event': r['fixed_event'] or False,
            })
    for i in range(0, len(rows), 1000):
        Day.create(rows[i:i + 1000])
    _logger.info('Sales forecast: %s calendar days loaded', len(rows))
