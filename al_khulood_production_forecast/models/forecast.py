# -*- coding: utf-8 -*-
"""Day forecast: how much each product will sell on a chosen day.

Method (deliberately plain arithmetic, not machine learning)
------------------------------------------------------------
For one product on one day::

    predicted = baseline x trend x (event factor 1) x (event factor 2) x ...

* **baseline**  - median of the same weekday over the last 8 *ordinary* (non-event)
  occurrences. Median, so a single catering order cannot move tomorrow's plan.
* **trend**     - recent ordinary days against the ordinary days before them, clamped.
* **event**     - learned by lining history up on the event's own offset: Ramadan
  day 12 is compared with Ramadan day 12 of previous years, the eve of Eid with
  the eve of Eid. Both calendars go through the identical mechanism, so New Year
  is learned exactly the way Eid is.

Every factor is returned separately, so the number on screen can always be
explained. Nothing here is a black box, and nothing is written to the database.

Honest limits
-------------
* Only days strictly BEFORE the selected date are ever used, so picking a past
  date gives a real backtest rather than a flattering fit.
* A sold-out day looks like low demand. Until leftovers are recorded, festival
  forecasts lean low.
* With ~1 observation per event per product, raw multipliers are shrunk toward
  1.0; the result is safe rather than sharp, and improves every year.
"""
import math
from datetime import timedelta
from statistics import median

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError

from ..tools import hijri

BAHRAIN_OFFSET = timedelta(hours=3)

BASELINE_WEEKS = 8          # how many same-weekday ordinary days make a baseline
TREND_WINDOW = 28           # ordinary days each side of the trend comparison
TREND_CLAMP = (0.85, 1.20)
SHRINK_K = 1.0              # pulls thin event evidence toward "no effect"
MAX_UPLIFT = 25.0           # a sanity cap: never predict more than 25x baseline
TRACKED_LIMIT = 60
HISTORY_DAYS = 45           # how far the chart under the day looks back

WEEKDAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday',
            'Friday', 'Saturday', 'Sunday')

EVENT_LABELS = {
    'ramadan': 'Ramadan',
    'eid_fitr': 'Eid al-Fitr',
    'eid_adha': 'Eid al-Adha',
    'muharram': 'Muharram',
    'ashura': 'Ashura',
    'hijri_new_year': 'Hijri New Year',
    'national_day': 'Bahrain National Day',
    'new_year': 'New Year',
    'valentine': "Valentine's Day",
    'mother_day': "Mother's Day",
    'other': 'Occasion',
}


class ProductionForecast(models.AbstractModel):
    _name = 'al.production.forecast'
    _description = 'Daily Sales Forecast'

    # ------------------------------------------------------------------
    # access / dates
    # ------------------------------------------------------------------
    def _check_access(self):
        if not self.env.user.has_group('stock.group_stock_user'):
            raise AccessError(_("You are not allowed to open the Production Forecast."))

    @api.model
    def _today(self):
        return (fields.Datetime.now() + BAHRAIN_OFFSET).date()

    # ------------------------------------------------------------------
    # raw data
    # ------------------------------------------------------------------
    @api.model
    def _tracked_products(self):
        """Products worth planning: what sells, plus anything we manufacture."""
        self.env.flush_all()
        today = self._today()
        self.env.cr.execute("""
            WITH sold AS (
                SELECT pol.product_id, SUM(pol.qty) AS units
                  FROM pos_order_line pol
                  JOIN pos_order po ON po.id = pol.order_id
                 WHERE po.state IN ('paid', 'done')
                   AND po.date_order >= %(since)s
                 GROUP BY 1
                 ORDER BY 2 DESC
                 LIMIT %(limit)s
            ), produced AS (
                SELECT DISTINCT product_id
                  FROM mrp_production
                 WHERE state = 'done' AND date_start >= %(since)s
            )
            SELECT pp.id,
                   COALESCE(pt.name->>'en_US', pt.name->>'ar_001', '?') AS name,
                   COALESCE(u.name->>'en_US', '')                      AS uom,
                   COALESCE(pc.complete_name, '')                      AS categ,
                   COALESCE(pt.list_price, 0)                          AS price
              FROM product_product pp
              JOIN product_template pt ON pt.id = pp.product_tmpl_id
              LEFT JOIN uom_uom u ON u.id = pt.uom_id
              LEFT JOIN product_category pc ON pc.id = pt.categ_id
              LEFT JOIN sold ON sold.product_id = pp.id
              LEFT JOIN produced ON produced.product_id = pp.id
             WHERE sold.product_id IS NOT NULL OR produced.product_id IS NOT NULL
             ORDER BY COALESCE(sold.units, 0) DESC
        """, {'since': today - timedelta(days=365), 'limit': TRACKED_LIMIT})
        return {r['id']: r for r in self.env.cr.dictfetchall()}

    @api.model
    def _daily_sales(self, product_ids):
        """{product_id: {date: (qty, revenue)}} in Bahrain days."""
        if not product_ids:
            return {}
        self.env.cr.execute("""
            SELECT pol.product_id AS pid,
                   (po.date_order + interval '3 hours')::date AS day,
                   SUM(pol.qty) AS qty,
                   SUM(pol.price_subtotal) AS revenue
              FROM pos_order_line pol
              JOIN pos_order po ON po.id = pol.order_id
             WHERE po.state IN ('paid', 'done')
               AND pol.product_id = ANY(%(pids)s)
             GROUP BY 1, 2
        """, {'pids': list(product_ids)})
        out = {}
        for r in self.env.cr.dictfetchall():
            out.setdefault(r['pid'], {})[r['day']] = (float(r['qty'] or 0.0),
                                                      float(r['revenue'] or 0.0))
        return out

    @api.model
    def _events(self):
        return self.env['al.demand.event'].search_read(
            [], ['name', 'event_type', 'calendar', 'date_start', 'date_end',
                 'lead_days', 'confirmed', 'closed'])

    # ------------------------------------------------------------------
    # calendar reasoning
    # ------------------------------------------------------------------
    @api.model
    def _events_on(self, day, events):
        """Which occasions touch `day`, and where in each one the day sits."""
        hits = []
        for ev in events:
            start = ev['date_start']
            end = ev['date_end']
            first = start - timedelta(days=ev['lead_days'] or 0)
            if first <= day <= end:
                hits.append({
                    'event': ev,
                    'offset': (day - start).days,     # negative = run-up
                })
        return hits

    @api.model
    def _event_days(self, events):
        """Every date touched by any event - these are excluded from baselines."""
        out = set()
        for ev in events:
            first = ev['date_start'] - timedelta(days=ev['lead_days'] or 0)
            d = first
            while d <= ev['date_end']:
                out.add(d)
                d += timedelta(days=1)
        return out

    @api.model
    def _hijri_label(self, day, hits):
        """Hijri date, counted from a confirmed Ramadan when we are inside one."""
        for hit in hits:
            ev = hit['event']
            if ev['event_type'] == 'ramadan' and ev['confirmed'] \
                    and ev['date_start'] <= day <= ev['date_end']:
                hy = hijri.gregorian_to_hijri(day)[0]
                return {
                    'year': hy, 'month': 9, 'day': hit['offset'] + 1,
                    'label': '%d %s %d AH' % (hit['offset'] + 1,
                                              hijri.month_name(9), hy),
                    'source': 'confirmed',
                }
        hy, hm, hd = hijri.gregorian_to_hijri(day)
        return {'year': hy, 'month': hm, 'day': hd,
                'label': hijri.format_hijri(hy, hm, hd), 'source': 'calculated'}

    # ------------------------------------------------------------------
    # the maths
    # ------------------------------------------------------------------
    @api.model
    def _baseline(self, series, day, event_days):
        """Median of the same weekday on ordinary days strictly before `day`."""
        picked = []
        probe = day - timedelta(days=7)
        guard = 0
        while len(picked) < BASELINE_WEEKS and guard < BASELINE_WEEKS * 6:
            guard += 1
            if probe not in event_days and probe in series:
                picked.append(series[probe][0])
            probe -= timedelta(days=7)
        if not picked:
            # no same-weekday history: fall back to any ordinary recent day
            recent = [q for d, (q, _r) in series.items()
                      if d < day and d not in event_days
                      and d >= day - timedelta(days=60)]
            return median(recent) if recent else 0.0
        return median(picked)

    @api.model
    def _trend(self, series, day, event_days):
        def mean_window(end, length):
            vals = [q for d, (q, _r) in series.items()
                    if end - timedelta(days=length) <= d < end
                    and d not in event_days]
            return sum(vals) / len(vals) if vals else None

        recent = mean_window(day, TREND_WINDOW)
        before = mean_window(day - timedelta(days=TREND_WINDOW), TREND_WINDOW)
        if not recent or not before:
            return 1.0
        return max(TREND_CLAMP[0], min(TREND_CLAMP[1], recent / before))

    @api.model
    def _event_factor(self, series, hit, day, events, event_days):
        """How much this occasion moved this product, learned from past years.

        Compares the matching offset of every earlier occurrence of the same
        event type against what an ordinary day looked like back then, then
        shrinks the result toward 1.0 because we rarely have many observations.
        """
        ev = hit['event']
        ratios = []
        for other in events:
            if other['event_type'] != ev['event_type'] or other['id'] == ev['id']:
                continue
            if other['date_start'] >= ev['date_start']:
                continue                      # never learn from the future
            past_day = other['date_start'] + timedelta(days=hit['offset'])
            if past_day >= day or past_day not in series:
                continue
            base = self._baseline(series, past_day, event_days)
            actual = series[past_day][0]
            if base > 0 and actual > 0:
                ratios.append(actual / base)
        if not ratios:
            return None, 0
        raw = math.exp(sum(math.log(r) for r in ratios) / len(ratios))
        n = len(ratios)
        shrunk = raw ** (n / float(n + SHRINK_K))
        return shrunk, n

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    @api.model
    def get_day_forecast(self, date_str=None):
        self._check_access()
        today = self._today()
        try:
            day = fields.Date.to_date(date_str) if date_str else today
        except (ValueError, TypeError):
            raise UserError(_("Invalid date."))
        if not day:
            day = today

        products = self._tracked_products()
        pids = list(products)
        sales = self._daily_sales(pids)
        events = self._events()
        event_days = self._event_days(events)
        hits = self._events_on(day, events)

        # ---- the calendar context ------------------------------------
        closed_by = next((h['event'] for h in hits if h['event'].get('closed')),
                        None)
        hijri_info = self._hijri_label(day, hits)
        weekday = WEEKDAYS[day.weekday()]
        is_weekend = day.weekday() in (4, 5)        # Bahrain: Fri / Sat

        # One shared date axis, so every product's chart lines up with the
        # all-products one and with the occasion markers on it.
        hist_dates = self._history_dates(sales, day)

        # ---- per-product forecast ------------------------------------
        rows = []
        tot_pred = tot_rev = tot_base = 0.0
        for pid, info in products.items():
            series = sales.get(pid, {})
            base = self._baseline(series, day, event_days)
            trend = self._trend(series, day, event_days)

            factors = [{'kind': 'base', 'label': '%s median (last %d)'
                        % (weekday, BASELINE_WEEKS), 'value': round(base, 2)}]
            if abs(trend - 1.0) > 0.005:
                factors.append({'kind': 'trend', 'label': 'Recent trend',
                                'value': round(trend, 3)})

            predicted = base * trend
            if closed_by:
                # The branches do not open. Not a dip to be estimated: zero.
                factors.append({'kind': 'event',
                                'label': '%s - branches closed' % closed_by['name'],
                                'value': 0.0, 'n': 0,
                                'calendar': closed_by['calendar']})
                predicted = 0.0
            for hit in hits:
                if closed_by:
                    break
                factor, n = self._event_factor(series, hit, day, events, event_days)
                ev = hit['event']
                label = self._hit_label(hit)
                if factor is None:
                    factors.append({'kind': 'event', 'label': label,
                                    'value': None, 'n': 0,
                                    'calendar': ev['calendar'],
                                    'note': 'no history yet'})
                    continue
                predicted *= factor
                factors.append({'kind': 'event', 'label': label,
                                'value': round(factor, 3), 'n': n,
                                'calendar': ev['calendar']})
            if base > 0:
                predicted = min(predicted, base * MAX_UPLIFT)

            actual = series.get(day, (None, None))[0] if day in series else None
            rows.append({
                'product_id': pid,
                'name': info['name'],
                'uom': info['uom'],
                'category': info['categ'],
                'baseline_qty': round(base, 1),
                'predicted_qty': round(predicted, 1),
                'predicted_revenue': round(predicted * float(info['price']), 2),
                'actual_qty': round(actual, 1) if actual is not None else None,
                'factors': factors,
                'confidence': self._confidence(series, hits, base),
                'history': [round(series.get(d, (0.0, 0.0))[0], 1)
                            for d in hist_dates],
            })
            tot_pred += predicted
            tot_rev += predicted * float(info['price'])
            tot_base += base

        rows.sort(key=lambda r: -r['predicted_qty'])

        uplift = ((tot_pred / tot_base - 1.0) * 100.0) if tot_base else 0.0
        return {
            'date': fields.Date.to_string(day),
            'is_past': day < today,
            'is_today': day == today,
            'weekday': weekday,
            'is_weekend': is_weekend,
            'hijri': hijri_info,
            'events': [self._event_payload(h) for h in hits],
            'kpis': {
                'predicted_units': round(tot_pred, 1),
                'predicted_revenue': round(tot_rev, 2),
                'baseline_units': round(tot_base, 1),
                'uplift_pct': round(uplift, 1),
                'tracked_items': len(rows),
                'actual_units': round(sum(r['actual_qty'] or 0.0 for r in rows), 1)
                                if day < today else None,
            },
            'products': rows,
            'history': self._history(sales, hist_dates, event_days),
        }

    # ------------------------------------------------------------------
    @api.model
    def _hit_label(self, hit):
        ev = hit['event']
        name = EVENT_LABELS.get(ev['event_type'], ev['event_type'])
        off = hit['offset']
        if ev['event_type'] in ('ramadan', 'muharram'):
            return '%s day %d' % (name, off + 1) if off >= 0 \
                else '%d day(s) before %s' % (-off, name)
        if off < 0:
            return '%d day%s before %s' % (-off, '' if off == -1 else 's', name)
        if off == 0:
            # a one-day occasion is just its name; only a span needs a day number
            if ev['date_start'] == ev['date_end']:
                return name
            return '%s (day 1)' % name
        return '%s day %d' % (name, off + 1)

    @api.model
    def _event_payload(self, hit):
        ev = hit['event']
        return {
            'id': ev['id'],
            'name': ev['name'],
            'type': ev['event_type'],
            'calendar': ev['calendar'],
            'label': self._hit_label(hit),
            'offset': hit['offset'],
            'confirmed': ev['confirmed'],
            'closed': bool(ev.get('closed')),
            'date_start': fields.Date.to_string(ev['date_start']),
            'date_end': fields.Date.to_string(ev['date_end']),
        }

    @api.model
    def _confidence(self, series, hits, base):
        if base <= 0:
            return 'none'
        if not hits:
            return 'high' if len(series) >= 60 else 'medium'
        return 'medium' if len(series) >= 180 else 'low'

    @api.model
    def _history_dates(self, sales, day):
        """The chart's x-axis: every trading day in the window, once.

        Built once and shared, so a single product's line sits on exactly the
        same dates as the all-products line and the occasion markers.
        """
        first = day - timedelta(days=HISTORY_DAYS)
        seen = set()
        for series in sales.values():
            for d in series:
                if first <= d <= day:
                    seen.add(d)
        return sorted(seen)

    @api.model
    def _history(self, sales, hist_dates, event_days):
        """Total units per day across every tracked product."""
        wanted = set(hist_dates)
        totals = {}
        for series in sales.values():
            for d, (q, _r) in series.items():
                if d in wanted:
                    totals[d] = totals.get(d, 0.0) + q
        return [{'date': fields.Date.to_string(d),
                 'qty': round(totals.get(d, 0.0), 1),
                 'is_event': d in event_days}
                for d in hist_dates]

    # ------------------------------------------------------------------
    @api.model
    def get_month_calendar(self, year, month):
        """One month of days, each carrying both calendars and its occasions.

        Cheap on purpose: no sales maths here, so the picker stays instant.
        """
        self._check_access()
        try:
            first = fields.Date.to_date('%04d-%02d-01' % (int(year), int(month)))
        except (ValueError, TypeError):
            raise UserError(_("Invalid month."))
        nxt = (first + timedelta(days=32)).replace(day=1)
        last = nxt - timedelta(days=1)
        events = self._events()
        days = []
        d = first
        while d < nxt:
            hits = self._events_on(d, events)
            days.append({
                'date': fields.Date.to_string(d),
                'day': d.day,
                'weekday': d.weekday(),
                'is_weekend': d.weekday() in (4, 5),
                'hijri': self._hijri_label(d, hits),
                'events': [self._event_payload(h) for h in hits],
            })
            d += timedelta(days=1)
        return {
            'year': first.year,
            'month': first.month,
            'hijri_label': self._hijri_month_span(first, last),
            'first_weekday': first.weekday(),
            'today': fields.Date.to_string(self._today()),
            'days': days,
        }

    @api.model
    def _hijri_month_span(self, first, last):
        """The Hijri month(s) a Gregorian month covers, for the picker title.

        A Gregorian month almost always straddles two Hijri months, so the
        title has to name both - otherwise the Hijri day numbers in the cells
        belong to a month nobody has been told the name of.
        """
        fy, fm, _fd = hijri.gregorian_to_hijri(first)
        ly, lm, _ld = hijri.gregorian_to_hijri(last)
        if (fy, fm) == (ly, lm):
            return '%s %d AH' % (hijri.month_name(fm), fy)
        if fy == ly:
            return '%s - %s %d AH' % (hijri.month_name(fm),
                                      hijri.month_name(lm), fy)
        return '%s %d - %s %d AH' % (hijri.month_name(fm), fy,
                                     hijri.month_name(lm), ly)
