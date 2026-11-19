"""Forecast report helpers: product name matching, table layout, Excel and PDF files.

Standalone (no Odoo imports) so it can be tested outside Odoo. The Odoo model in models/report.py supplies the data.
"""
import io
import re
from difflib import SequenceMatcher

# ------------------------------------------------------------------------------------------------------------
# Matching the client's product names (e.g. "MATHAI SPICY 400") to Odoo products
# ------------------------------------------------------------------------------------------------------------
ALIAS = {'SP': 'SPECIAL', 'GRN': 'GREEN', 'BISCUITS': 'BISCUIT', 'KUBOS': 'KUBOOS', 'KAMFAROSH': 'KAMFAROOSH',
         'ROSHAN': 'ROSHANE', 'ADHARY': 'ADHARI', 'ADHRI': 'ADHARI'}
# Words that make a product a different product: a candidate that has one of them while the label does not is rejected
VARIANT = {'SPICY', 'PREMIUM', 'SPECIAL', 'CUP', 'MINI', 'BIG', 'SMALL', 'WITH', 'HALWA', 'HONEY', 'FRY', 'RAHASH',
           'SAUCE', 'CAKE', 'RED', 'GREEN', 'ADHARI', 'DANA', 'BUSTAN', 'COFFEE', 'BISCUIT', 'KUBOOS', 'NAEEM',
           'METEEIN', 'AQILI', 'SAMOOSA', 'MATHAI', 'JILEBI', 'CUT', 'BANOOSH', 'KAMFAROOSH', 'DARWAZA', 'ROSHANE',
           'DEHRIZ'}


def _tokens(text):
    s = (text or '').upper()
    s = re.sub(r'(\d+)\s*(GMS|GM|G|KG)\b', r'\1', s)
    s = re.sub(r'[^A-Z0-9]+', ' ', s)
    out = []
    for t in s.split():
        t = ALIAS.get(t, t)
        if t.isdigit() and len(t) <= 2:      # "01 Premium Biscuit": the leading code is not part of the name
            continue
        out.append(t)
    return out


def _same(a, b):
    return a == b or (len(a) > 3 and len(b) > 3 and SequenceMatcher(None, a, b).ratio() >= 0.82)


def match_label(label, products):
    """products: [(product_id, name)]. Returns (product_id or None, state, note).
    state: 'matched' (one clear product), 'check' (several fit equally, none chosen), 'none' (nothing fits)."""
    lt = _tokens(label)
    if not lt:
        return None, 'none', ''
    fits, rejected = [], []
    for pid, name in products:
        pt = _tokens(name)
        if not all(any(_same(l, p) for p in pt) for l in lt):
            continue
        unmatched = [p for p in pt if not any(_same(l, p) for l in lt)]
        if any(p in VARIANT for p in unmatched):
            rejected.append(name)       # e.g. label JILEBI, product "Jilebi Small": a size the label does not say
            continue
        fits.append((len(unmatched), name, pid))
    if not fits:
        if rejected:
            return None, 'check', 'Could be: ' + '; '.join(sorted(rejected)[:4])
        return None, 'none', ''
    fits.sort()
    best = [f for f in fits if f[0] == fits[0][0]]
    if len(best) == 1:
        return best[0][2], 'matched', ''
    return None, 'check', 'Could be: ' + '; '.join(f[1] for f in best[:4])


# ------------------------------------------------------------------------------------------------------------
# Dates
# ------------------------------------------------------------------------------------------------------------
WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']


def day_title(d):
    return '%s %d %s %d' % (WEEKDAYS[d.weekday()], d.day, MONTHS[d.month - 1], d.year)


def hijri_labeler(cal, shifts, month_names):
    """Returns label(date) -> '28 Jumada I 1448' (English letters, Western digits).

    cal: DataFrame with date, hijri_year, hijri_month, hijri_day (the calculated calendar).
    shifts: {(hijri_year, hijri_month): days} for months whose first day was confirmed as different from the calendar
    (the moon-sighting dates entered in Dates & holidays). A month with a shift starts that many days later (or earlier);
    the month before it becomes longer (or shorter). Months without a confirmed date keep the calendar's boundaries."""
    import bisect
    import pandas as pd
    g = cal.sort_values('date').groupby(['hijri_year', 'hijri_month'], sort=False).agg(
        date=('date', 'min'), day=('hijri_day', 'first')).reset_index()
    ym, start, fixed = [], [], []
    for r in g.itertuples():
        y, m = int(r.hijri_year), int(r.hijri_month)
        calc_start = pd.Timestamp(r.date) - pd.Timedelta(days=int(r.day) - 1)
        ym.append((y, m))
        start.append(calc_start + pd.Timedelta(days=int(shifts.get((y, m), 0))))
        fixed.append((y, m) in shifts)
    order = sorted(range(len(start)), key=lambda i: start[i])
    ym, start, fixed = [ym[i] for i in order], [start[i] for i in order], [fixed[i] for i in order]
    # A Hijri month has 29 or 30 days. Each confirmed first day is kept; from it the neighbouring months that were not
    # confirmed give way, walking outwards until every month is 29 or 30 days again.
    def valid(n):
        return 29 <= n <= 30
    anchors = [i for i in range(len(start)) if fixed[i]]
    for a in anchors:
        for i in range(a - 1, -1, -1):                       # earlier months
            length = (start[i + 1] - start[i]).days
            if valid(length) or fixed[i]:
                break
            start[i] = start[i + 1] - pd.Timedelta(days=29 if length < 29 else 30)
        for i in range(a, len(start) - 1):                   # later months
            length = (start[i + 1] - start[i]).days
            if valid(length) or fixed[i + 1]:
                break
            start[i + 1] = start[i] + pd.Timedelta(days=29 if length < 29 else 30)
    starts = [(start[i], ym[i][0], ym[i][1]) for i in range(len(start))]
    keys = [x[0] for x in starts]

    def label(day):
        day = pd.Timestamp(day)
        i = bisect.bisect_right(keys, day) - 1
        if i < 0:
            return ''
        start, y, m = starts[i]
        return '%d %s %d' % (min((day - start).days + 1, 30), month_names[m - 1], y)
    return label


# ------------------------------------------------------------------------------------------------------------
# Layout
# ------------------------------------------------------------------------------------------------------------
def round_half_up(x, nd=0):
    f = 10 ** nd
    return int(x * f + 0.5) / f if nd else int(x + 0.5)


def build_day(lines, branches, fc, act, kind, nd):
    """One day as a table. kind: 'future' (forecast only) or 'past' (actual sales and the forecast made before the day).
    lines: [{'label', 'product_id', 'name'}]; branches: [{'id', 'short', 'name'}];
    fc: {(product_id, branch_id): {'e', 'lo', 'hi', 'alloc'}}; act: {(product_id, branch_id): actual}.
    Every figure shown is rounded and totals add up the figures shown, so a row and a column always add up."""
    past = kind == 'past'
    rows = []
    col_v = {b['id']: 0.0 for b in branches}
    col_a = {b['id']: 0.0 for b in branches}
    has_v = {b['id']: False for b in branches}
    has_a = {b['id']: False for b in branches}
    grand_v = grand_a = 0.0
    for ln in lines:
        pid = ln.get('product_id')
        cells, tv, ta, hv, ha = [], 0.0, 0.0, False, False
        for b in branches:
            f = fc.get((pid, b['id'])) if pid else None
            a = act.get((pid, b['id'])) if (pid and past) else None
            cell = {'v': None, 'lo': None, 'hi': None, 'alloc': False, 'a': None}
            if f is not None:
                cell['v'] = round_half_up(f['e'], nd)
                cell['alloc'] = bool(f.get('alloc'))
                if f.get('lo') is not None:
                    cell['lo'] = round_half_up(f['lo'], nd)
                    cell['hi'] = round_half_up(f['hi'], nd)
                tv += cell['v']
                hv = True
                col_v[b['id']] += cell['v']
                has_v[b['id']] = True
            if past:
                if a is not None:
                    cell['a'] = round_half_up(a, nd)
                elif f is not None:
                    cell['a'] = 0 if nd == 0 else 0.0     # forecast exists, nothing sold that day
                if cell['a'] is not None:
                    ta += cell['a']
                    ha = True
                    col_a[b['id']] += cell['a']
                    has_a[b['id']] = True
            cells.append(cell)
        note = ''
        if not pid:
            note = 'Product not matched'
        elif not past and not hv:
            note = 'Not enough sales to forecast'
        rows.append({'label': ln['label'], 'name': ln.get('name') or '', 'product_id': pid, 'cells': cells,
                     'total_v': round(tv, nd) if hv else None, 'total_a': round(ta, nd) if ha else None, 'note': note})
        grand_v += tv if hv else 0
        grand_a += ta if ha else 0
    totals = {'v': [round(col_v[b['id']], nd) if has_v[b['id']] else None for b in branches],
              'a': [round(col_a[b['id']], nd) if has_a[b['id']] else None for b in branches],
              'total_v': round(grand_v, nd), 'total_a': round(grand_a, nd) if past else None}
    return {'kind': kind, 'rows': rows, 'totals': totals}


def _fmt(v, nd):
    if v is None:
        return '-'
    return f'{v:,.{nd}f}'


def _hdr(day):
    return '%s  |  %s' % (day['title'], day['hijri']) if day.get('hijri') else day['title']


# ------------------------------------------------------------------------------------------------------------
# Excel: one sheet per day
# ------------------------------------------------------------------------------------------------------------
def to_xlsx(rep):
    import xlsxwriter
    nd = rep['nd']
    cols = rep['columns']
    bio = io.BytesIO()
    wb = xlsxwriter.Workbook(bio, {'in_memory': True})
    num = '#,##0' if nd == 0 else '#,##0.0'
    f_title = wb.add_format({'bold': True, 'font_size': 14})
    f_day = wb.add_format({'bold': True, 'font_size': 12, 'font_color': '#1F2D5C'})
    f_sub = wb.add_format({'font_color': '#555555'})
    f_head = wb.add_format({'bold': True, 'bg_color': '#1F2D5C', 'font_color': '#FFFFFF', 'align': 'center', 'valign': 'vcenter', 'border': 1})
    f_headl = wb.add_format({'bold': True, 'bg_color': '#1F2D5C', 'font_color': '#FFFFFF', 'align': 'left', 'valign': 'vcenter', 'border': 1})
    f_head2 = wb.add_format({'bold': True, 'bg_color': '#34457F', 'font_color': '#FFFFFF', 'align': 'center', 'border': 1, 'font_size': 9})
    f_lab = wb.add_format({'border': 1})
    f_num = wb.add_format({'num_format': num, 'align': 'center', 'border': 1})
    f_fc = wb.add_format({'num_format': num, 'align': 'center', 'border': 1, 'font_color': '#555555'})
    f_est = wb.add_format({'num_format': num, 'align': 'center', 'border': 1, 'italic': True, 'bg_color': '#EEF3FF'})
    f_none = wb.add_format({'align': 'center', 'border': 1, 'font_color': '#999999'})
    f_tot = wb.add_format({'num_format': num, 'align': 'center', 'border': 1, 'bold': True, 'bg_color': '#E8ECF5'})
    f_totl = wb.add_format({'bold': True, 'border': 1, 'bg_color': '#E8ECF5'})
    f_note = wb.add_format({'font_color': '#555555', 'italic': True})

    def put(ws, r, c, v, fmt, none_fmt=f_none):
        if v is None:
            ws.write(r, c, '-', none_fmt)
        else:
            ws.write_number(r, c, v, fmt)

    for day in rep['days']:
        ws = wb.add_worksheet(day['title'][:31])
        past = day['kind'] == 'past'
        ws.write(0, 0, 'Forecast report' if not past else 'Actual sales and forecast', f_title)
        ws.write(1, 0, _hdr(day), f_day)
        ws.write(2, 0, rep['subtitle_day'] if not past else rep['subtitle_past'], f_sub)
        ws.write(3, 0, rep['source'], f_sub)
        hr = 5
        if past:
            ws.merge_range(hr, 0, hr + 1, 0, 'PRODUCT', f_headl)
            for j, c in enumerate(cols):
                ws.merge_range(hr, 1 + 2 * j, hr, 2 + 2 * j, c['short'], f_head)
                ws.write(hr + 1, 1 + 2 * j, 'Actual', f_head2)
                ws.write(hr + 1, 2 + 2 * j, 'Forecast', f_head2)
            tc = 1 + 2 * len(cols)
            ws.merge_range(hr, tc, hr, tc + 1, 'TOTAL', f_head)
            ws.write(hr + 1, tc, 'Actual', f_head2)
            ws.write(hr + 1, tc + 1, 'Forecast', f_head2)
            r = hr + 2
        else:
            ws.write(hr, 0, 'PRODUCT', f_headl)
            for j, c in enumerate(cols):
                ws.write(hr, 1 + j, c['short'], f_head)
            tc = 1 + len(cols)
            ws.write(hr, tc, 'TOTAL', f_head)
            r = hr + 1
        first = r
        for row in day['rows']:
            ws.write(r, 0, row['label'], f_lab)
            for j, c in enumerate(row['cells']):
                if past:
                    put(ws, r, 1 + 2 * j, c['a'], f_num)
                    put(ws, r, 2 + 2 * j, c['v'], f_est if c['alloc'] else f_fc)
                else:
                    put(ws, r, 1 + j, c['v'], f_est if c['alloc'] else f_num)
            if past:
                put(ws, r, tc, row['total_a'], f_tot)
                put(ws, r, tc + 1, row['total_v'], f_tot)
            else:
                put(ws, r, tc, row['total_v'], f_tot)
            r += 1
        ws.write(r, 0, 'ALL PRODUCTS', f_totl)
        t = day['totals']
        for j in range(len(cols)):
            if past:
                put(ws, r, 1 + 2 * j, t['a'][j], f_tot)
                put(ws, r, 2 + 2 * j, t['v'][j], f_tot)
            else:
                put(ws, r, 1 + j, t['v'][j], f_tot)
        if past:
            put(ws, r, tc, t['total_a'], f_tot)
            put(ws, r, tc + 1, t['total_v'], f_tot)
        else:
            put(ws, r, tc, t['total_v'], f_tot)
        r += 2
        if not past:      # likely range, low and high, per product and branch
            ws.write(r, 0, 'Likely range (low and high). Ranges are not added up across products or branches.', f_note)
            r += 1
            ws.merge_range(r, 0, r + 1, 0, 'PRODUCT', f_headl)
            for j, c in enumerate(cols):
                ws.merge_range(r, 1 + 2 * j, r, 2 + 2 * j, c['short'], f_head)
                ws.write(r + 1, 1 + 2 * j, 'Low', f_head2)
                ws.write(r + 1, 2 + 2 * j, 'High', f_head2)
            r += 2
            for row in day['rows']:
                ws.write(r, 0, row['label'], f_lab)
                for j, c in enumerate(row['cells']):
                    put(ws, r, 1 + 2 * j, c['lo'], f_num)
                    put(ws, r, 2 + 2 * j, c['hi'], f_num)
                r += 1
            r += 1
        for line in rep['footnotes_past'] if past else rep['footnotes_day']:
            ws.write(r, 0, line, f_note)
            r += 1
        ws.set_column(0, 0, 30)
        ws.set_column(1, max(tc + 1, 2 * len(cols)), 9)
        ws.freeze_panes(first, 1)
        ws.set_landscape()
        ws.set_paper(9)
        ws.fit_to_pages(1, 0)
        ws.repeat_rows(hr, first - 1)
    wb.close()
    return bio.getvalue()


# ------------------------------------------------------------------------------------------------------------
# PDF (A4 landscape): one page per day
# ------------------------------------------------------------------------------------------------------------
def to_pdf(rep):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    nd = rep['nd']
    cols = rep['columns']
    bio = io.BytesIO()
    doc = SimpleDocTemplate(bio, pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=10 * mm, bottomMargin=12 * mm, title='Forecast report')
    ss = getSampleStyleSheet()
    title = ParagraphStyle('t', parent=ss['Title'], alignment=0, fontSize=12.5, leading=15, spaceAfter=2)
    dayst = ParagraphStyle('d', parent=ss['Heading2'], alignment=0, fontSize=12, textColor=colors.HexColor('#1F2D5C'), spaceAfter=1, spaceBefore=0)
    sub = ParagraphStyle('s', parent=ss['Normal'], fontSize=8.5, textColor=colors.HexColor('#555555'), leading=11)
    foot = ParagraphStyle('f', parent=ss['Normal'], fontSize=6.3, textColor=colors.HexColor('#555555'), leading=7.6)
    navy, navy2 = colors.HexColor('#1F2D5C'), colors.HexColor('#34457F')
    avail = landscape(A4)[0] - 24 * mm
    story = []
    for k, day in enumerate(rep['days']):
        past = day['kind'] == 'past'
        story += [Paragraph('%s &nbsp;|&nbsp; %s' % ('Actual sales and forecast' if past else 'Forecast report', _hdr(day).replace('  |  ', ' &nbsp;|&nbsp; ')), title),
                  Paragraph('%s &nbsp; %s' % (rep['subtitle_past'] if past else rep['subtitle_day'], rep['source']), sub), Spacer(1, 2 * mm)]
        fs = 6.4 if past else 8
        if past:
            h1 = ['PRODUCT'] + [x for c in cols for x in (c['short'], '')] + ['TOTAL', '']
            h2 = [''] + ['Actual', 'Forecast'] * len(cols) + ['Actual', 'Forecast']
            data = [h1, h2]
            nh = 2
        else:
            data = [['PRODUCT'] + [c['short'] for c in cols] + ['TOTAL']]
            nh = 1
        est = []
        for i, row in enumerate(day['rows']):
            if past:
                line = [row['label']]
                for j, c in enumerate(row['cells']):
                    line += [_fmt(c['a'], nd), _fmt(c['v'], nd)]
                    if c['v'] is not None and c['alloc']:
                        est.append((2 + 2 * j, nh + i))
                line += [_fmt(row['total_a'], nd), _fmt(row['total_v'], nd)]
            else:
                line = [row['label']]
                for j, c in enumerate(row['cells']):
                    line.append(_fmt(c['v'], nd))
                    if c['v'] is not None and c['alloc']:
                        est.append((1 + j, nh + i))
                line.append(_fmt(row['total_v'], nd))
            data.append(line)
        t = day['totals']
        if past:
            last = ['ALL PRODUCTS']
            for j in range(len(cols)):
                last += [_fmt(t['a'][j], nd), _fmt(t['v'][j], nd)]
            last += [_fmt(t['total_a'], nd), _fmt(t['total_v'], nd)]
            ncol = 1 + 2 * len(cols) + 2
            first_w = 40 * mm
            widths = [first_w] + [(avail - first_w) / (ncol - 1)] * (ncol - 1)
        else:
            last = ['ALL PRODUCTS'] + [_fmt(v, nd) for v in t['v']] + [_fmt(t['total_v'], nd)]
            ncol = 1 + len(cols) + 1
            first_w = 58 * mm
            widths = [first_w] + [(avail - first_w) / (ncol - 1)] * (ncol - 1)
        data.append(last)
        n = len(data)
        foot_lines = rep['footnotes_past'] if past else rep['footnotes_day']
        budget = (210 - 10 - 12 - 12 - 3 - 3) * mm - 7.6 * 1.45 * len(foot_lines) * 0.3528 * mm      # page - header - footnotes
        row_h = max(min(5.2 * mm, budget / len(data)), 3.3 * mm)
        tbl = Table(data, colWidths=widths, rowHeights=[row_h] * len(data), repeatRows=nh)
        st = [
            ('FONT', (0, 0), (-1, -1), 'Helvetica', fs), ('FONT', (0, 0), (-1, nh - 1), 'Helvetica-Bold', fs),
            ('BACKGROUND', (0, 0), (-1, nh - 1), navy), ('TEXTCOLOR', (0, 0), (-1, nh - 1), colors.white),
            ('ALIGN', (1, 0), (-1, -1), 'CENTER'), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#B8BFD3')),
            ('BACKGROUND', (0, n - 1), (-1, n - 1), colors.HexColor('#E8ECF5')), ('FONT', (0, n - 1), (-1, n - 1), 'Helvetica-Bold', fs),
            ('ROWBACKGROUNDS', (0, nh), (-1, n - 2), [colors.white, colors.HexColor('#F6F8FC')]),
            ('TOPPADDING', (0, 0), (-1, -1), 0.6), ('BOTTOMPADDING', (0, 0), (-1, -1), 0.6),
            ('LEFTPADDING', (0, 0), (-1, -1), 2), ('RIGHTPADDING', (0, 0), (-1, -1), 2),
        ]
        if past:
            for j in range(len(cols) + 1):
                st.append(('SPAN', (1 + 2 * j, 0), (2 + 2 * j, 0)))
                st.append(('BACKGROUND', (1 + 2 * j, 1), (2 + 2 * j, 1), navy2))
            st.append(('SPAN', (0, 0), (0, 1)))
            st.append(('FONT', (1, 1), (-1, 1), 'Helvetica-Bold', 5.6))
            for j in range(len(cols) + 1):      # forecast columns in grey
                st.append(('TEXTCOLOR', (2 + 2 * j, nh), (2 + 2 * j, n - 2), colors.HexColor('#555555')))
        else:
            st.append(('FONT', (-1, nh), (-1, n - 2), 'Helvetica-Bold', fs))
        for (cx, ry) in est:
            st.append(('FONT', (cx, ry), (cx, ry), 'Helvetica-Oblique', fs))
            st.append(('TEXTCOLOR', (cx, ry), (cx, ry), colors.HexColor('#2F5BD3')))
        tbl.setStyle(TableStyle(st))
        story.append(tbl)
        story.append(Spacer(1, 2 * mm))
        for line in foot_lines:
            story.append(Paragraph(line, foot))
        if k < len(rep['days']) - 1:
            story.append(PageBreak())

    def _page(canvas, d):
        canvas.saveState()
        canvas.setFont('Helvetica', 7.5)
        canvas.setFillColor(colors.HexColor('#777777'))
        canvas.drawRightString(landscape(A4)[0] - 12 * mm, 7 * mm, 'Page %d' % d.page)
        canvas.drawString(12 * mm, 7 * mm, 'Amwal W.L.L. - Sales forecast')
        canvas.restoreState()
    doc.build(story, onFirstPage=_page, onLaterPages=_page)
    return bio.getvalue()
