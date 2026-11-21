from odoo import api, fields, models
from odoo.exceptions import ValidationError


class AlkhuloodhSfViewerBranch(models.Model):
    _name = 'alkhuloodh.sf.viewer.branch'
    _description = 'Branch a Viewer may see'
    _order = 'user_id'

    user_id = fields.Many2one('res.users', string='User', required=True, ondelete='cascade')
    warehouse_id = fields.Many2one('stock.warehouse', string='Branch', required=True, ondelete='cascade')

    @api.constrains('user_id')
    def _check_one_branch(self):
        for r in self:
            if self.sudo().search_count([('user_id', '=', r.user_id.id), ('id', '!=', r.id)]):
                raise ValidationError(self.env._('%s already has a branch. A Viewer sees one branch only.', r.user_id.name))
