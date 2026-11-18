"""Adds the client's main product list (KHULOOD_PRODUCTS.pdf) to 'Extra products to forecast'.
Products already in the list stay. Ids that do not exist in this database are skipped."""
PRODUCT_IDS = [
    2544, 2543, 6921, 2545, 2546, 6266, 5807, 6206, 2552, 6079, 6988,      # halwa, mini, cup halwa
    3871, 3825, 6033,                                                      # jilebi (small, big, 1 kg)
    3259, 3312, 3709, 6366, 3710, 6620, 3711, 3712,                        # mathai
    2667, 2662, 2661, 3265, 5601,                                          # coffee 250 g, saffron
    2551, 6640, 6562,                                                      # cup rahash, banoosh, kamfaroosh (current product)
    6478, 6611, 6467,                                                      # aqili
    6277, 6329, 5726, 6012, 6090, 6462, 7003,                              # biscuits
    3211, 3212, 3214,                                                      # samoosa
]


def migrate(cr, version):
    from odoo import SUPERUSER_ID, api
    env = api.Environment(cr, SUPERUSER_ID, {})
    cfg = env['alkhuloodh.sf.config'].get_config()
    found = env['product.product'].with_context(active_test=False).browse(PRODUCT_IDS).exists()
    cfg.write({'extra_product_ids': [(4, pid) for pid in found.ids]})
