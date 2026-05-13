from odoo import fields, models

class PosOrdersReport(models.Model):
    # This inherits the existing POS Orders Report model
    _inherit = 'report.pos.order'
    
    # Define the new related field
    product_standard_price = fields.Float(
        string='Product Cost',
        # The path to the cost: product_id (M2O) -> standard_price (Float)
        related='product_id.standard_price',
        readonly=True,
        # Setting store=True ensures the value is physically saved in the database, 
        # which is required for efficient filtering, grouping, and searching in reports.
        store=False,
        digits='Product Price',
        help='The standard cost price of the product at the time of the order.',
    )