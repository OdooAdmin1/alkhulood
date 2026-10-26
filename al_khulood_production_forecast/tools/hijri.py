# -*- coding: utf-8 -*-
"""Gregorian <-> Hijri conversion, plus the labels the dashboard prints.

Which calendar is authoritative
-------------------------------
The arithmetic ("tabular") Islamic calendar implemented here is the Kuwaiti
algorithm. It is deterministic and needs no data table, but it can differ from
what Bahrain actually observed by a day, because the start of a month is
declared on a moon sighting, not computed.

So the rule in this module is: the arithmetic conversion is only a *display*
fallback. Whenever the day falls inside a confirmed al.demand.event window, the
Hijri day is counted from that confirmed start instead, so the date shown on
screen and the event driving the forecast can never disagree.
"""

MONTH_NAMES = (
    'Muharram', 'Safar', "Rabi' al-Awwal", "Rabi' al-Thani",
    'Jumada al-Ula', 'Jumada al-Akhirah', 'Rajab', "Sha'ban",
    'Ramadan', 'Shawwal', "Dhu al-Qa'dah", 'Dhu al-Hijjah',
)


def _gregorian_to_jdn(year, month, day):
    """Julian Day Number for a (proleptic) Gregorian date."""
    if month < 3:
        year -= 1
        month += 12
    a = year // 100
    b = 2 - a + a // 4
    return (int(365.25 * (year + 4716)) + int(30.6001 * (month + 1))
            + day + b - 1524)


def _jdn_to_gregorian(jdn):
    a = jdn + 32044
    b = (4 * a + 3) // 146097
    c = a - (146097 * b) // 4
    d = (4 * c + 3) // 1461
    e = c - (1461 * d) // 4
    m = (5 * e + 2) // 153
    day = e - (153 * m + 2) // 5 + 1
    month = m + 3 - 12 * (m // 10)
    year = 100 * b + d - 4800 + m // 10
    return year, month, day


def gregorian_to_hijri(d):
    """date -> (hijri_year, hijri_month, hijri_day) by the tabular calendar."""
    jd = _gregorian_to_jdn(d.year, d.month, d.day)
    jd = jd - 1948440 + 10632
    n = (jd - 1) // 10631
    jd = jd - 10631 * n + 354
    j = (((10985 - jd) // 5316) * ((50 * jd) // 17719)
         + (jd // 5670) * ((43 * jd) // 15238))
    jd = (jd - ((30 - j) // 15) * ((17719 * j) // 50)
          - (j // 16) * ((15238 * j) // 43) + 29)
    month = (24 * jd) // 709
    day = jd - (709 * month) // 24
    year = 30 * n + j - 30
    return int(year), int(month), int(day)


def hijri_to_gregorian(year, month, day):
    """(hijri_year, hijri_month, hijri_day) -> (y, m, d) by the tabular calendar."""
    jd = (day + (29.5 * (month - 1) + 0.5) // 1
          + (year - 1) * 354
          + (3 + 11 * year) // 30
          + 1948439.5 - 0.5)
    return _jdn_to_gregorian(int(jd))


def month_name(month):
    if 1 <= month <= 12:
        return MONTH_NAMES[month - 1]
    return ''


def format_hijri(year, month, day):
    return '%d %s %d AH' % (day, month_name(month), year)
