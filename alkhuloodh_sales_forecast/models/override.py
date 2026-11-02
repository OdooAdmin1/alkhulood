import numpy as np
import pandas as pd

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError

MANAGER_GROUPS = ('base.group_system', 'alkhuloodh_sales_forecast.group_sf_manager')
POS_COLUMN = {'eid_fitr': 'pos_eid_fitr', 'eid_adha': 'pos_eid_adha',
              'hijri_new_year': 'pos_hijri_new_year', 'ashura': 'pos_ashura'}
EVENT_KINDS = tuple(POS_COLUMN) + ('ramadan_start',)
FIND_WINDOW = 6          # the calendar's own date must be within this many days of the confirmed date


class AlkhuloodhSfOverride(models.Model):
    _name = 'alkhuloodh.sf.override'
    _description = 'Confirmed date, holiday or closure'
    _order = 'date desc, id desc'

    kind = fields.Selection([
        ('eid_fitr', 'Eid al-Fitr, first day'),
        ('eid_adha', 'Eid al-Adha, first day'),
        ('ramadan_start', 'Ramadan, first day'),
        ('hijri_new_year', 'Hijri New Year (day shops observe it)'),
        ('ashura', 'Ashura (day shops observe it)'),
        ('holiday', 'Public / school holiday (kept out of learning)'),
        ('closure', 'Full closure (forecast = 0)'),
    ], required=True, default='eid_fitr')
    date = fields.Date(string='Confirmed date', required=True)
    date_to = fields.Date(string='Until (holiday / closure)', help='Leave empty for a single day.')
    state = fields.Selection([('draft', 'Predicted, not confirmed'), ('confirmed', 'Confirmed')],
                             default='confirmed', required=True)
    source = fields.Char(string='Confirmed by / source', help='Who confirmed it, e.g. the client or the moon-sighting announcement.')
    note = fields.Char()
    active = fields.Boolean(default=True)
    base_date = fields.Date(string='Calendar says', compute='_compute_base_date')
    shift_days = fields.Integer(string='Shift (days)', compute='_compute_base_date')

    @api.depends('kind', 'date')
    def _compute_base_date(self):
        Cal = self.env['alkhuloodh.sf.calendar.day'].sudo()
        for r in self:
            r.base_date = False
            r.shift_days = 0
            if not r.date or r.kind not in EVENT_KINDS:
                continue
            dom = [('date', '>=', fields.Date.add(r.date, days=-FIND_WINDOW)),
                   ('date', '<=', fields.Date.add(r.date, days=FIND_WINDOW))]
            dom.append((POS_COLUMN[r.kind], '=', '0') if r.kind in POS_COLUMN else ('ramadan_day', '=', 1))
            days = Cal.search(dom)
            if days:
                best = min(days, key=lambda d: abs((d.date - r.date).days))
                r.base_date = best.date
                r.shift_days = (r.date - best.date).days

    @api.constrains('kind', 'date', 'date_to')
    def _check_dates(self):
        for r in self:
            if r.date_to and r.date_to < r.date:
                raise ValidationError(self.env._('"Until" cannot be before the confirmed date.'))
            if r.kind in EVENT_KINDS and not r.base_date:
                raise ValidationError(self.env._(
                    'The Hijri calendar has no such event within %s days of %s. Check the kind and the date.',
                    FIND_WINDOW, r.date))

    def _check_manager(self):
        if not any(self.env.user.has_group(g) for g in MANAGER_GROUPS):
            raise AccessError(self.env._('Only Sales Forecast managers can do this.'))

    # ------------------------------------------------------------------ used by the forecast
    @api.model
    def _active_confirmed(self):
        return self.sudo().search([('state', '=', 'confirmed'), ('active', '=', True)])

    @api.model
    def _apply_to_calendar(self, df):
        """Returns the calendar with confirmed dates applied. The base values in the database stay untouched."""
        ovs = self._active_confirmed()
        if not ovs:
            return df
        df = df.copy()
        idx = pd.DatetimeIndex(df['date'])
        for o in ovs:
            d = pd.Timestamp(o.date)
            if o.kind in POS_COLUMN:
                if not o.base_date:
                    continue
                b = pd.Timestamp(o.base_date)
                m = np.asarray((idx >= b - pd.Timedelta(days=25)) & (idx <= b + pd.Timedelta(days=25)))
                pos = np.asarray((idx - d).days, dtype=float)
                df.loc[m, POS_COLUMN[o.kind]] = np.where((pos >= -20) & (pos <= 20), pos, np.nan)[m]
            elif o.kind == 'ramadan_start':
                if not o.base_date:
                    continue
                b = pd.Timestamp(o.base_date)
                m = np.asarray((idx >= b - pd.Timedelta(days=5)) & (idx <= b + pd.Timedelta(days=40)))
                rd = np.asarray((idx - d).days, dtype=int) + 1
                df.loc[m, 'ramadan_day'] = np.where((rd >= 1) & (rd <= 30), rd, 0)[m]
            else:
                end = pd.Timestamp(o.date_to or o.date)
                m = np.asarray((idx >= d) & (idx <= end))
                df.loc[m, 'fixed_event'] = o.kind
        return df

    @api.model
    def _closed_dates(self):
        out = set()
        for o in self._active_confirmed().filtered(lambda x: x.kind == 'closure'):
            out.update(pd.date_range(o.date, o.date_to or o.date))
        return out

    # ------------------------------------------------------------------ buttons
    def action_suggest(self):
        """Adds the calendar's own predicted dates for the next 13 months as 'not confirmed' lines to confirm."""
        self._check_manager()
        today = fields.Date.context_today(self)
        Cal = self.env['alkhuloodh.sf.calendar.day'].sudo()
        specs = [('eid_fitr', ('pos_eid_fitr', '=', '0')), ('eid_adha', ('pos_eid_adha', '=', '0')),
                 ('ramadan_start', ('ramadan_day', '=', 1)), ('hijri_new_year', ('pos_hijri_new_year', '=', '0')),
                 ('ashura', ('pos_ashura', '=', '0'))]
        made = 0
        for kind, cond in specs:
            for day in Cal.search([cond, ('date', '>=', today), ('date', '<=', fields.Date.add(today, days=400))]):
                if not self.with_context(active_test=False).search_count([
                        ('kind', '=', kind), ('date', '>=', fields.Date.add(day.date, days=-FIND_WINDOW)),
                        ('date', '<=', fields.Date.add(day.date, days=FIND_WINDOW))]):
                    self.create({'kind': kind, 'date': day.date, 'state': 'draft',
                                 'note': self.env._('Predicted by the calendar. Confirm it or change the date.')})
                    made += 1
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': self.env._('Upcoming dates'), 'type': 'success',
                           'message': self.env._('%s dates added. Open each one and set it to Confirmed when the date is known.', made),
                           'next': {'type': 'ir.actions.client', 'tag': 'reload'}}}

    def action_confirm(self):
        self._check_manager()
        self.write({'state': 'confirmed'})
        return True

    def action_refresh_forecast(self):
        """Re-runs the forecast now so confirmed dates show on the dashboard immediately."""
        self._check_manager()
        run = self.env['alkhuloodh.sf.run']._run_forecast('manual')
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': self.env._('Forecast'), 'type': 'success' if run.state == 'done' else 'danger',
                           'message': self.env._('Forecast refreshed.') if run.state == 'done'
                           else self.env._('The run failed. Open Forecast runs for details.')}}
