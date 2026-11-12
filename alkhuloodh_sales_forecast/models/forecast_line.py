from odoo import fields, models


class AlkhuloodhSfForecastLine(models.Model):
    _name = 'alkhuloodh.sf.forecast.line'
    _description = 'Forecast line (one series, one day)'
    _order = 'date, level, warehouse_id, categ_id, product_id'

    run_id = fields.Many2one('alkhuloodh.sf.run', required=True, index=True, ondelete='cascade')
    date = fields.Date(required=True, index=True)
    level = fields.Selection([('company', 'Company'), ('branch', 'Branch'), ('category', 'Category'),
                              ('cell', 'Branch x category'), ('product', 'Product'),
                              ('pair', 'Product in branch')], required=True, index=True)
    warehouse_id = fields.Many2one('stock.warehouse', string='Branch', ondelete='cascade')
    categ_id = fields.Many2one('product.category', string='Category', ondelete='cascade')
    product_id = fields.Many2one('product.product', string='Product', ondelete='cascade')
    metric = fields.Selection([('revenue', 'Revenue ex-VAT'), ('qty', 'Quantity')], required=True, index=True)
    expected = fields.Float(digits=(16, 3))
    low = fields.Float(digits=(16, 3))
    high = fields.Float(digits=(16, 3))
    method = fields.Selection([('model', 'Own model'), ('alloc', "Branch share of product forecast")],
                              help="Product-in-branch lines only: 'model' = forecast from that branch's own sales history, "
                                   "'alloc' = the product's all-branch forecast split by the branch's recent share of sales.")
    is_current = fields.Boolean(related='run_id.is_current', store=False)

    def init(self):
        self.env.cr.execute('CREATE INDEX IF NOT EXISTS alkhuloodh_sf_forecast_line_run_level_date_idx '
                            'ON alkhuloodh_sf_forecast_line (run_id, level, metric, date)')
        self.env.cr.execute('CREATE INDEX IF NOT EXISTS alkhuloodh_sf_forecast_line_level_date_idx '
                            'ON alkhuloodh_sf_forecast_line (level, metric, date)')
