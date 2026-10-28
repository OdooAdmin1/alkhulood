import logging

from odoo import api, fields, models
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)

MANAGER_GROUPS = ('base.group_system', 'alkhuloodh_sales_forecast.group_sf_manager')


class AlkhuloodhSfConfig(models.Model):
    _name = 'alkhuloodh.sf.config'
    _description = 'Sales forecast settings (single record)'

    name = fields.Char(default='Sales forecast settings', required=True)
    tz = fields.Char(string='Business time zone', default='Asia/Bahrain', required=True,
                     help='Sales are grouped into days in this time zone.')
    history_start = fields.Date(string='History starts', default='2023-01-01', required=True)
    horizon_months = fields.Integer(string='Forecast horizon (months)', default=6, required=True,
                                    help='How far ahead each forecast run goes. 3 months is the minimum asked for; 6 covers Ramadan and both Eids.')
    overlap_days = fields.Integer(string='Re-read last N days each night', default=7,
                                  help='Orders can still change after the day ends, so the most recent days are rebuilt every night.')
    excluded_product_ids = fields.Many2many(
        'product.product', 'alkhuloodh_sf_config_excl_product_rel', 'config_id', 'product_id',
        string='Excluded products', context={'active_test': False},
        help='Rounding, adjustment, gift card and similar lines that are not real sales.')
    excluded_categ_ids = fields.Many2many(
        'product.category', 'alkhuloodh_sf_config_excl_categ_rel', 'config_id', 'categ_id',
        string='Excluded categories')

    last_load_at = fields.Datetime(readonly=True)
    last_load_range = fields.Char(readonly=True)
    last_load_rows = fields.Integer(readonly=True)
    last_load_seconds = fields.Float(digits=(10, 1), readonly=True)
    reconciliation_text = fields.Text(readonly=True)
    drift_message = fields.Text(string='Accuracy check', compute='_compute_drift_message')

    def _compute_drift_message(self):
        status = self.env['alkhuloodh.sf.accuracy']._drift_status()
        for r in self:
            r.drift_message = status['message']

    @api.model
    def get_config(self):
        cfg = self.sudo().search([], limit=1)
        if not cfg:
            cfg = self.sudo().create({})
        return cfg

    def _check_manager(self):
        if not any(self.env.user.has_group(g) for g in MANAGER_GROUPS):
            raise AccessError(self.env._('Only Sales Forecast managers can do this.'))

    @api.model
    def action_open_config(self):
        cfg = self.get_config()
        return {'type': 'ir.actions.act_window', 'name': self.env._('Configuration'),
                'res_model': self._name, 'res_id': cfg.id, 'view_mode': 'form', 'target': 'current'}

    def action_load_full(self):
        self._check_manager()
        self.env['alkhuloodh.sf.actual.daily']._refresh(full=True)
        return True

    def action_load_recent(self):
        self._check_manager()
        self.env['alkhuloodh.sf.actual.daily']._refresh(full=False)
        return True

    def action_run_forecast(self):
        self._check_manager()
        self.env['alkhuloodh.sf.run']._run_forecast('manual')
        return {'type': 'ir.actions.act_window', 'res_model': 'alkhuloodh.sf.run', 'view_mode': 'list,form', 'name': self.env._('Forecast runs')}

    def action_build_backtests(self):
        self._check_manager()
        res = self.env['alkhuloodh.sf.run']._build_backtests()
        msg = self.env._('Past forecasts built: %(made)s now, %(remaining)s still to do (of %(total)s). ', **res)
        if res['remaining']:
            msg += self.env._('Press the button again to continue.')
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': self.env._('Backtest'), 'message': msg, 'type': 'success', 'sticky': False,
                           'next': {'type': 'ir.actions.client', 'tag': 'reload'}}}

    def action_refresh_accuracy(self):
        self._check_manager()
        n = self.env['alkhuloodh.sf.accuracy']._refresh()
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': self.env._('Accuracy'), 'type': 'success', 'sticky': False,
                           'message': self.env._('%s comparison rows rebuilt.', n),
                           'next': {'type': 'ir.actions.client', 'tag': 'reload'}}}

    def action_reconcile(self):
        self._check_manager()
        self.env['alkhuloodh.sf.actual.daily']._reconcile()
        return True
