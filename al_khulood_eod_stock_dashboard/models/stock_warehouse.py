from odoo import fields, models


class StockWarehouse(models.Model):
    _inherit = "stock.warehouse"

    eod_dashboard_branch = fields.Boolean(
        string="Show on EOD Stock Dashboard",
        default=True,
        help="Uncheck for warehouses that are not a retail branch/shop-floor "
        "location (e.g. a central store or transit warehouse), so it is "
        "excluded from the per-branch dashboard view.",
    )
