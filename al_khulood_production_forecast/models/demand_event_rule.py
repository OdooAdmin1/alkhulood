# -*- coding: utf-8 -*-
"""Recurrence rules: the calendar generates the occasions, nobody types them.

Every occasion the forecast cares about sits on a *fixed* date in its own
calendar - Eid al-Fitr is always 1 Shawwal, Ashura always 10 Muharram,
National Day always 16 December. So one rule per occasion replaces one record
per occasion per year, and the calendar never runs out.

What the arithmetic cannot do
-----------------------------
A Hijri month begins on a sighting, not on a calculation. Checked against the
dates Bahrain actually observed between 1444 and 1450 AH, the tabular
conversion in tools/hijri.py lands:

    Ramadan start   6/6 exact        Eid al-Fitr   3/6 exact
    Muharram start  5/6 exact        Eid al-Adha   1/5 exact
    Ashura          5/6 exact        Gregorian     always exact

20 of 29 exact, 9 wrong by exactly one day, never by more.

One day is survivable when *planning* a future Eid - the rush is cooked across
the window anyway. It is not survivable when *learning* from a past one, because
the factor is read off ``date_start + offset`` and Eid eve is a ~40x day: put the
anchor one day out and the spike is learned against an ordinary Tuesday.

Hence the precedence rule enforced here and in al.demand.event: a generated date
is only ever a placeholder. A confirmed date, or a hand-made record, always wins
and is never overwritten.
"""
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..tools import hijri

# How wide a window the rolling top-up keeps filled, in years either side of
# today. Five ahead because the dashboard lets you page the calendar forward
# and an empty month reads as a broken screen, not as "not generated yet";
# one back keeps the learning window populated if an old backup is restored.
# At ~10 rules this is well under a hundred rows in total.
YEARS_BACK = 1
YEARS_AHEAD = 5


class DemandEventRule(models.Model):
    _name = 'al.demand.event.rule'
    _description = 'Demand Event Recurrence Rule'
    _order = 'calendar, anchor_month, anchor_day'

    name = fields.Char(
        required=True,
        help="Used to name the occasions this rule generates, with the year "
             "appended - 'Ramadan' becomes 'Ramadan 1447'.")
    event_type = fields.Selection(
        selection=lambda self: self.env['al.demand.event']._fields['event_type'].selection,
        required=True, index=True,
        help="Occasions sharing a type are learned together, so the rule must "
             "use the same type as any hand-made records for it.")
    calendar = fields.Selection([
        ('hijri', 'Islamic (Hijri)'),
        ('gregorian', 'Gregorian'),
    ], required=True, default='hijri',
        help="Which calendar the anchor month and day are counted in.")
    anchor_month = fields.Integer(
        required=True,
        help="Month in this rule's own calendar. Ramadan is month 9, "
             "Dhu al-Hijjah is month 12, December is month 12.")
    anchor_day = fields.Integer(
        required=True, help="Day of that month the occasion starts on.")
    duration_days = fields.Integer(
        default=1, required=True,
        help="How many days the occasion runs, counting the first.")
    lead_days = fields.Integer(
        default=0,
        help="Days before the start that already carry the rush.")
    closed = fields.Boolean(
        string='Branches closed',
        help="The shops do not trade. Generated occasions inherit this.")
    needs_sighting = fields.Boolean(
        default=False,
        help="This occasion's real date is announced after a moon sighting, so "
             "a generated date is an estimate until somebody confirms it. Set "
             "on Ramadan, the two Eids and Muharram; the occasions counted "
             "from inside a month, such as Ashura or Mawlid, inherit whatever "
             "their month start turned out to be.")
    active = fields.Boolean(default=True)
    event_ids = fields.One2many('al.demand.event', 'rule_id', string='Occasions')
    event_count = fields.Integer(compute='_compute_event_count')

    _anchor_range = models.Constraint(
        'CHECK (anchor_month BETWEEN 1 AND 12 AND anchor_day BETWEEN 1 AND 30)',
        "An anchor must be a real month (1-12) and day (1-30).",
    )
    _duration_positive = models.Constraint(
        'CHECK (duration_days >= 1)',
        "An occasion lasts at least one day.",
    )

    @api.depends('event_ids')
    def _compute_event_count(self):
        counts = dict(self.env['al.demand.event']._read_group(
            [('rule_id', 'in', self.ids)], ['rule_id'], ['__count']))
        for rule in self:
            rule.event_count = counts.get(rule, 0)

    # ------------------------------------------------------------------
    # generation
    # ------------------------------------------------------------------
    def _start_date(self, year):
        """First Gregorian day of this rule's occurrence in `year`.

        `year` is read in the rule's own calendar, so a Hijri rule is asked for
        a Hijri year. Returns None when the conversion cannot place the date,
        which happens for a Hijri day 30 of a month that only ran 29 days.
        """
        self.ensure_one()
        if self.calendar == 'gregorian':
            try:
                return fields.Date.to_date(
                    '%04d-%02d-%02d' % (year, self.anchor_month, self.anchor_day))
            except ValueError:
                return None
        y, m, d = hijri.hijri_to_gregorian(year, self.anchor_month, self.anchor_day)
        try:
            return fields.Date.to_date('%04d-%02d-%02d' % (y, m, d))
        except ValueError:
            return None

    @api.model
    def _current_year(self, calendar):
        today = fields.Date.context_today(self)
        if calendar == 'gregorian':
            return today.year
        return hijri.gregorian_to_hijri(today)[0]

    def _generate(self, first_year=None, last_year=None):
        """Materialise occurrences for every active rule, and return them.

        Idempotent by (rule, year): a second run creates nothing. An occurrence
        is skipped entirely when the year is already covered by a confirmed or
        hand-made record, and is refreshed in place only when the existing row
        is itself generated and still unconfirmed.
        """
        Event = self.env['al.demand.event']
        rules = self or self.search([])
        created = Event.browse()
        for rule in rules.filtered('active'):
            lo = first_year or (rule._current_year(rule.calendar) - YEARS_BACK)
            hi = last_year or (rule._current_year(rule.calendar) + YEARS_AHEAD)
            for year in range(lo, hi + 1):
                created |= rule._generate_year(year)
        return created

    def _generate_year(self, year):
        self.ensure_one()
        Event = self.env['al.demand.event']
        start = self._start_date(year)
        if not start:
            return Event.browse()

        existing = Event.search([('rule_id', '=', self.id),
                                 ('source_year', '=', year)], limit=1)
        if existing:
            # Anything a person has vouched for outranks the arithmetic.
            if existing.confirmed or not existing.generated:
                return Event.browse()
            existing.write(self._event_values(year, start))
            return Event.browse()

        # A hand-made record for the same occasion and year - the shipped
        # observed calendar - also wins, and adopts the rule so the pair is
        # never generated twice.
        observed = self._observed_for(year, start)
        if observed:
            observed.write({'rule_id': self.id, 'source_year': year})
            return Event.browse()

        return Event.create(self._event_values(year, start))

    def _observed_for(self, year, start):
        """A hand-entered record of this same occasion, within a few days."""
        self.ensure_one()
        return self.env['al.demand.event'].search([
            ('rule_id', '=', False),
            ('event_type', '=', self.event_type),
            ('date_start', '>=', start - timedelta(days=4)),
            ('date_start', '<=', start + timedelta(days=4)),
        ], limit=1)

    def _event_values(self, year, start):
        self.ensure_one()
        return {
            'name': '%s %d' % (self.name, year),
            'event_type': self.event_type,
            'calendar': self.calendar,
            'date_start': start,
            'date_end': start + timedelta(days=max(self.duration_days, 1) - 1),
            'lead_days': self.lead_days,
            'closed': self.closed,
            # Only a sighting-dependent occasion is left unconfirmed; a fixed
            # Gregorian date is as certain as it will ever be.
            'confirmed': not self.needs_sighting,
            'rule_id': self.id,
            'source_year': year,
            'generated': True,
        }

    # ------------------------------------------------------------------
    @api.model
    def _cron_top_up(self):
        """Keep the rolling window filled so the calendar never runs out."""
        self.search([])._generate()

    def action_generate(self):
        created = self._generate()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'message': _('%(n)s occasion(s) added to the calendar.',
                             n=len(created)),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def action_view_events(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.name,
            'res_model': 'al.demand.event',
            'view_mode': 'list',
            'domain': [('rule_id', '=', self.id)],
            'context': {'default_rule_id': self.id},
        }

    @api.model
    def _seed_rules_and_generate(self):
        """Install/upgrade entry point: ship the rules, then fill the window."""
        if not self.search_count([]):
            raise UserError(_("The occasion rules are missing; reinstall the "
                              "module so its data files are loaded."))
        # Rows that predate the column hold SQL NULL. The ORM reads that as
        # False, so the precedence logic was never at risk, but raw reporting
        # SQL over the table would silently drop them from either side of a
        # `generated` test.
        self.env.cr.execute("""
            UPDATE al_demand_event SET generated = FALSE WHERE generated IS NULL
        """)
        self.env.invalidate_all()
        self._cron_top_up()
