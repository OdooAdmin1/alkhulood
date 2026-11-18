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

    extra_product_ids = fields.Many2many(
        'product.product', 'alkhuloodh_sf_config_extra_product_rel', 'config_id', 'product_id',
        string='Extra products to forecast', context={'active_test': False},
        help='Products outside the top 70 by sales that you also want on the dashboard. They are forecast in every run.')
    last_load_at = fields.Datetime(readonly=True)
    last_load_range = fields.Char(readonly=True)
    last_load_rows = fields.Integer(readonly=True)
    last_load_seconds = fields.Float(digits=(10, 1), readonly=True)
    reconciliation_text = fields.Text(readonly=True)
    drift_message = fields.Text(string='Accuracy check', compute='_compute_drift_message')
    bt_note = fields.Char(string='Past forecasts status', readonly=True)
    bt_extra_sig = fields.Char(readonly=True, help='Extra products list at the last complete build of the past forecasts.')
    bt_progress = fields.Text(string='Past forecasts', compute='_compute_bt_progress')

    def _compute_drift_message(self):
        status = self.env['alkhuloodh.sf.accuracy']._drift_status()
        for r in self:
            r.drift_message = status['message']

    def _compute_bt_progress(self):
        run = self.env['alkhuloodh.sf.run']
        built, total = run._backtest_counts()
        for r in self:
            lines = []
            if total:
                lines.append(self.env._('Past forecasts: %(built)s of %(total)s built.', built=built, total=total))
            else:
                lines.append(self.env._('Past forecasts: not enough history yet.'))
            if r.bt_note:
                lines.append(r.bt_note)
            if r.bt_extra_sig and r.bt_extra_sig != run._extra_sig(r):
                lines.append(self.env._('The extra products list changed: the past forecasts are being updated for the new products.'))
            r.bt_progress = '\n'.join(lines)

    def write(self, vals):
        res = super().write(vals)
        if 'extra_product_ids' in vals:
            # new products need past forecasts too: only the missing products are added, in the background
            self.env['alkhuloodh.sf.run']._queue_backtests(self.env._('Extra products changed. Queued: the new products are added to the past forecasts.'))
        return res

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
        """Queues the build in the background and returns at once; progress shows on this screen."""
        self._check_manager()
        queued = self.env['alkhuloodh.sf.run']._queue_backtests()
        msg = (self.env._('Past forecasts are being built in the background. Progress is shown at the top of this page. '
                          'On staging the server may start a few minutes late.') if queued else
               self.env._('The background job is missing. Update the module, or use "Build now".'))
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': self.env._('Past forecasts'), 'message': msg, 'type': 'success' if queued else 'warning',
                           'sticky': False, 'next': {'type': 'ir.actions.client', 'tag': 'reload'}}}

    def action_build_backtests_now(self):
        """Fallback: builds for about 25 seconds in this request. Press again to continue."""
        self._check_manager()
        res = self.env['alkhuloodh.sf.run']._build_backtests()
        msg = self.env._('Past forecasts built: %(made)s now, %(remaining)s still to do (of %(total)s). ', **res)
        if res['remaining']:
            msg += self.env._('Press the button again to continue.')
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': self.env._('Past forecasts'), 'message': msg, 'type': 'success', 'sticky': False,
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
