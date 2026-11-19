from odoo import api, models
from odoo.exceptions import AccessError

USER_GROUPS = ('base.group_system', 'alkhuloodh_sales_forecast.group_sf_user')
VIEWER_GROUP = 'alkhuloodh_sales_forecast.group_sf_viewer'


class AlkhuloodhSfAccess(models.AbstractModel):
    _name = 'alkhuloodh.sf.access'
    _description = 'Who may open the forecast, and which branch'

    @api.model
    def _scope(self):
        """Checks that the current user may open the dashboard and the report, and says what they may see.

        Returns {'limit': None | branch id, 'viewer_only': bool}.
        - Administrator, Manager, User: every branch (limit None).
        - Viewer: only the one branch a manager assigned (Report setup > Viewer branches). A Viewer with no branch
          assigned sees nothing, not everything.
        - Anyone else: no access."""
        user = self.env.user
        if any(user.has_group(g) for g in USER_GROUPS):
            return {'limit': None, 'viewer_only': False}
        if user.has_group(VIEWER_GROUP):
            rec = self.env['alkhuloodh.sf.viewer.branch'].sudo().search([('user_id', '=', user.id)], limit=1)
            if not rec:
                raise AccessError(self.env._('No branch is assigned to you yet. Ask a Sales Forecast manager to assign one.'))
            return {'limit': rec.warehouse_id.id, 'viewer_only': True}
        raise AccessError(self.env._('You do not have access to the sales forecast.'))
