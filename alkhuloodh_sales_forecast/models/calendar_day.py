from odoo import api, fields, models

HIJRI_MONTHS = ['Muharram', 'Safar', 'Rabi I', 'Rabi II', 'Jumada I', 'Jumada II',
                'Rajab', "Sha'ban", 'Ramadan', 'Shawwal', "Dhu al-Qi'dah", 'Dhu al-Hijjah']


class AlkhuloodhSfCalendarDay(models.Model):
    _name = 'alkhuloodh.sf.calendar.day'
    _description = 'Hijri calendar day (base values, Umm al-Qura)'
    _order = 'date'

    date = fields.Date(required=True, index=True)
    hijri_year = fields.Integer()
    hijri_month = fields.Integer()
    hijri_day = fields.Integer()
    hijri_text = fields.Char(compute='_compute_hijri_text', string='Hijri date')
    ramadan_day = fields.Integer(help='1..30 during Ramadan, 0 otherwise.')
    # Signed days from the nearest event (negative = before). Kept as text so "no event nearby" stays empty.
    # The +1 day shift for Ashura and Hijri New Year (shops close one day later) is already included.
    pos_eid_fitr = fields.Char(string='Days from Eid al-Fitr')
    pos_eid_adha = fields.Char(string='Days from Eid al-Adha')
    pos_hijri_new_year = fields.Char(string='Days from Hijri New Year')
    pos_ashura = fields.Char(string='Days from Ashura')
    fixed_event = fields.Char(help='Gregorian fixed date. Not used by the Hijri-only model.')

    @api.depends('hijri_year', 'hijri_month', 'hijri_day')
    def _compute_hijri_text(self):
        for r in self:
            m = HIJRI_MONTHS[r.hijri_month - 1] if 1 <= r.hijri_month <= 12 else ''
            r.hijri_text = f'{r.hijri_day} {m} {r.hijri_year}' if r.hijri_year else ''

    def init(self):
        self.env.cr.execute(
            'CREATE UNIQUE INDEX IF NOT EXISTS alkhuloodh_sf_calendar_day_date_uniq ON alkhuloodh_sf_calendar_day (date)')

    @api.model
    def _to_dataframe(self):
        """Calendar rows for the engine, with confirmed dates, holidays and closures applied."""
        import pandas as pd
        cols = ['date', 'hijri_year', 'hijri_month', 'hijri_day', 'ramadan_day', 'pos_eid_fitr', 'pos_eid_adha',
                'pos_hijri_new_year', 'pos_ashura', 'fixed_event']
        self.env.cr.execute('SELECT %s FROM alkhuloodh_sf_calendar_day ORDER BY date' % ', '.join(cols))
        df = pd.DataFrame(self.env.cr.fetchall(), columns=cols)
        df['date'] = pd.to_datetime(df['date'])
        for c in ('pos_eid_fitr', 'pos_eid_adha', 'pos_hijri_new_year', 'pos_ashura'):
            df[c] = pd.to_numeric(df[c], errors='coerce')
        df['fixed_event'] = df['fixed_event'].fillna('')
        return self.env['alkhuloodh.sf.override']._apply_to_calendar(df)
