from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class AmwalKhuloodStockReportSetup(models.Model):
    """One line of configuration for the Daily Stock Report.

    * Branch: a warehouse that is reported on (one line per branch).
    * Giveaway location: stock moved to this location counts as Giveaway.
    * Giveaway customer: deliveries to this customer count as Giveaway
      instead of Delivered.
    """

    _name = "amwal.khulood.stock.report.setup"
    _description = "Daily Stock Report Setup"
    _order = "kind, sequence, id"

    kind = fields.Selection(
        [
            ("branch", "Branch"),
            ("giveaway_location", "Giveaway location"),
            ("giveaway_partner", "Giveaway customer"),
        ],
        required=True,
        default="branch",
    )
    sequence = fields.Integer(default=10)
    warehouse_id = fields.Many2one("stock.warehouse", string="Branch", ondelete="cascade")
    location_id = fields.Many2one("stock.location", string="Location", ondelete="cascade")
    partner_id = fields.Many2one("res.partner", string="Customer", ondelete="cascade")

    def _target(self):
        self.ensure_one()
        return {
            "branch": self.warehouse_id,
            "giveaway_location": self.location_id,
            "giveaway_partner": self.partner_id,
        }.get(self.kind)

    @api.depends("kind", "warehouse_id", "location_id", "partner_id")
    def _compute_display_name(self):
        for rec in self:
            target = rec._target()
            rec.display_name = target.display_name if target else _("New line")

    @api.constrains("kind", "warehouse_id", "location_id", "partner_id")
    def _check_target(self):
        for rec in self:
            if not rec._target():
                raise ValidationError(
                    _("Please select the branch, location or customer for this line.")
                )
