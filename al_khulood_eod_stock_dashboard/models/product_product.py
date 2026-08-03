from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    eod_dashboard_tracked = fields.Boolean(
        string="Track in EOD Dashboard",
        default=False,
        help="If checked, this product's closing stock per branch is captured "
        "every day at 23:00 and shown on the Al Khulood EOD Stock Dashboard. "
        "Keep this limited to the top-selling products the dashboard should track.",
    )
    eod_dashboard_low_stock_threshold = fields.Float(
        string="EOD Dashboard Low-Stock Threshold",
        default=0.0,
        help="Per-branch quantity below which this product is highlighted as "
        "low stock on the EOD dashboard. 0 means no threshold is applied.",
    )
