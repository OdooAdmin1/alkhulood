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
# Layout
# ------------------------------------------------------------------------------------------------------------
def round_half_up(x, nd=0):
    f = 10 ** nd
    return int(x * f + 0.5) / f if nd else int(x + 0.5)


def build_layout(lines, branches, cells, metric, nd=None):
    """lines: [{'label', 'product_id', 'name'}]; branches: [{'id', 'short', 'name'}];
    cells: {(product_id, branch_id): {'e', 'lo', 'hi', 'alloc'}}.
    Every figure shown is rounded, and totals add up the rounded figures, so the table always adds up."""
    nd = (0 if metric == 'qty' else 1) if nd is None else nd
    rows = []
    col_tot = {b['id']: 0.0 for b in branches}
    col_has = {b['id']: False for b in branches}
    grand = 0.0
    for ln in lines:
        out, tot, has = [], 0.0, False
        for b in branches:
            c = cells.get((ln.get('product_id'), b['id'])) if ln.get('product_id') else None
            if c is None:
                out.append({'v': None, 'alloc': False, 'lo': None, 'hi': None})
                continue
            v = round_half_up(c['e'], nd)
            out.append({'v': v, 'alloc': bool(c.get('alloc')),
                        'lo': round_half_up(c['lo'], nd) if c.get('lo') is not None else None,
                        'hi': round_half_up(c['hi'], nd) if c.get('hi') is not None else None})
            tot += v
            has = True
            col_tot[b['id']] += v
            col_has[b['id']] = True
        total = round(tot, nd) if has else None
        if has:
            grand += tot
        note = ''
        if not ln.get('product_id'):
            note = 'Product not matched'
        elif not has:
            note = 'Not enough sales to forecast'
        rows.append({'label': ln['label'], 'name': ln.get('name') or '', 'product_id': ln.get('product_id'),
                     'cells': out, 'total': total, 'note': note})
    totals = {'cells': [round(col_tot[b['id']], nd) if col_has[b['id']] else None for b in branches],
              'total': round(grand, nd)}
    return {'columns': [{'id': b['id'], 'short': b['short'], 'name': b['name']} for b in branches],
            'rows': rows, 'totals': totals, 'nd': nd}


def _fmt(v, nd):
    if v is None:
        return '-'
    return f'{v:,.{nd}f}'


# ------------------------------------------------------------------------------------------------------------
# Excel
# ------------------------------------------------------------------------------------------------------------
def to_xlsx(rep):
    import xlsxwriter
    nd = rep['nd']
    bio = io.BytesIO()
    wb = xlsxwriter.Workbook(bio, {'in_memory': True})
    num = '#,##0' if nd == 0 else '#,##0.0'
    f_title = wb.add_format({'bold': True, 'font_size': 14})
    f_sub = wb.add_format({'font_color': '#555555'})
    f_head = wb.add_format({'bold': True, 'bg_color': '#1F2D5C', 'font_color': '#FFFFFF', 'align': 'center', 'valign': 'vcenter', 'border': 1})
    f_headl = wb.add_format({'bold': True, 'bg_color': '#1F2D5C', 'font_color': '#FFFFFF', 'align': 'left', 'valign': 'vcenter', 'border': 1})
    f_lab = wb.add_format({'border': 1})
    f_num = wb.add_format({'num_format': num, 'align': 'center', 'border': 1})
    f_est = wb.add_format({'num_format': num, 'align': 'center', 'border': 1, 'italic': True, 'bg_color': '#EEF3FF'})
    f_none = wb.add_format({'align': 'center', 'border': 1, 'font_color': '#999999'})
    f_tot = wb.add_format({'num_format': num, 'align': 'center', 'border': 1, 'bold': True, 'bg_color': '#E8ECF5'})
    f_totl = wb.add_format({'bold': True, 'border': 1, 'bg_color': '#E8ECF5'})
    f_note = wb.add_format({'font_color': '#555555', 'italic': True})

    ws = wb.add_worksheet('Forecast')
    cols = rep['columns']
    ws.write(0, 0, 'Forecast report', f_title)
    ws.write(1, 0, rep['subtitle'], f_sub)
    ws.write(2, 0, rep['source'], f_sub)
    hr = 4
    ws.write(hr, 0, 'PRODUCT', f_headl)
    for j, c in enumerate(cols):
        ws.write(hr, 1 + j, c['short'], f_head)
    ws.write(hr, 1 + len(cols), 'TOTAL', f_head)
    r = hr + 1
    for row in rep['rows']:
        ws.write(r, 0, row['label'], f_lab)
        for j, c in enumerate(row['cells']):
            if c['v'] is None:
                ws.write(r, 1 + j, '-', f_none)
            else:
                ws.write_number(r, 1 + j, c['v'], f_est if c['alloc'] else f_num)
        if row['total'] is None:
            ws.write(r, 1 + len(cols), '-', f_none)
        else:
            ws.write_number(r, 1 + len(cols), row['total'], f_tot)
        r += 1
    ws.write(r, 0, 'ALL PRODUCTS', f_totl)
    for j, v in enumerate(rep['totals']['cells']):
        ws.write_number(r, 1 + j, v, f_tot) if v is not None else ws.write(r, 1 + j, '-', f_none)
    ws.write_number(r, 1 + len(cols), rep['totals']['total'], f_tot)
    r += 2
    for line in rep['footnotes']:
        ws.write(r, 0, line, f_note)
        r += 1
    ws.set_column(0, 0, 30)
    ws.set_column(1, len(cols), 10)
    ws.set_column(1 + len(cols), 1 + len(cols), 11)
    ws.freeze_panes(hr + 1, 1)
    ws.set_landscape()
    ws.set_paper(9)
    ws.fit_to_pages(1, 0)
    ws.repeat_rows(hr)

    wr = wb.add_worksheet('Likely range')
    wr.write(0, 0, 'Likely range (low and high) per product and branch', f_title)
    wr.write(1, 0, rep['subtitle'], f_sub)
    wr.write(3, 0, 'PRODUCT', f_headl)
    wr.write(4, 0, '', f_headl)
    for j, c in enumerate(cols):
        wr.merge_range(3, 1 + 2 * j, 3, 2 + 2 * j, c['short'], f_head)
        wr.write(4, 1 + 2 * j, 'Low', f_head)
        wr.write(4, 2 + 2 * j, 'High', f_head)
    r = 5
    for row in rep['rows']:
        wr.write(r, 0, row['label'], f_lab)
        for j, c in enumerate(row['cells']):
            for k, key in enumerate(('lo', 'hi')):
                if c[key] is None:
                    wr.write(r, 1 + 2 * j + k, '-', f_none)
                else:
                    wr.write_number(r, 1 + 2 * j + k, c[key], f_num)
        r += 1
    r += 1
    wr.write(r, 0, 'Ranges are not added up across products or branches: the low of a total is not the sum of the lows.', f_note)
    wr.set_column(0, 0, 30)
    wr.set_column(1, 2 * len(cols), 7)
    wr.freeze_panes(5, 1)
    wb.close()
    return bio.getvalue()


# ------------------------------------------------------------------------------------------------------------
# PDF (A4 landscape)
# ------------------------------------------------------------------------------------------------------------
def to_pdf(rep):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    nd = rep['nd']
    cols = rep['columns']
    bio = io.BytesIO()
    doc = SimpleDocTemplate(bio, pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=12 * mm, bottomMargin=14 * mm, title='Forecast report')
    ss = getSampleStyleSheet()
    title = ParagraphStyle('t', parent=ss['Title'], alignment=0, fontSize=16, spaceAfter=2)
    sub = ParagraphStyle('s', parent=ss['Normal'], fontSize=9, textColor=colors.HexColor('#555555'), leading=12)
    foot = ParagraphStyle('f', parent=ss['Normal'], fontSize=7.5, textColor=colors.HexColor('#555555'), leading=10)
    story = [Paragraph('Forecast report', title), Paragraph(rep['subtitle'], sub), Paragraph(rep['source'], sub), Spacer(1, 4 * mm)]

    head = ['PRODUCT'] + [c['short'] for c in cols] + ['TOTAL']
    data = [head]
    est = []
    for i, row in enumerate(rep['rows'], start=1):
        line = [row['label']]
        for j, c in enumerate(row['cells']):
            line.append(_fmt(c['v'], nd))
            if c['v'] is not None and c['alloc']:
                est.append((1 + j, i))
        line.append(_fmt(row['total'], nd))
        data.append(line)
    data.append(['ALL PRODUCTS'] + [_fmt(v, nd) for v in rep['totals']['cells']] + [_fmt(rep['totals']['total'], nd)])
    n = len(data)
    avail = landscape(A4)[0] - 24 * mm
    first = 58 * mm
    other = (avail - first) / (len(cols) + 1)
    t = Table(data, colWidths=[first] + [other] * (len(cols) + 1), repeatRows=1)
    st = [
        ('FONT', (0, 0), (-1, -1), 'Helvetica', 8),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1F2D5C')), ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONT', (0, 0), (-1, 0), 'Helvetica-Bold', 8),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#B8BFD3')),
        ('BACKGROUND', (0, n - 1), (-1, n - 1), colors.HexColor('#E8ECF5')), ('FONT', (0, n - 1), (-1, n - 1), 'Helvetica-Bold', 8),
        ('FONT', (-1, 1), (-1, n - 2), 'Helvetica-Bold', 8),
        ('ROWBACKGROUNDS', (0, 1), (-1, n - 2), [colors.white, colors.HexColor('#F6F8FC')]),
        ('TOPPADDING', (0, 0), (-1, -1), 2.2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2.2),
    ]
    for (cx, ry) in est:
        st.append(('FONT', (cx, ry), (cx, ry), 'Helvetica-Oblique', 8))
        st.append(('TEXTCOLOR', (cx, ry), (cx, ry), colors.HexColor('#2F5BD3')))
    t.setStyle(TableStyle(st))
    story.append(t)
    story.append(Spacer(1, 4 * mm))
    for line in rep['footnotes']:
        story.append(Paragraph(line, foot))

    def _page(canvas, d):
        canvas.saveState()
        canvas.setFont('Helvetica', 7.5)
        canvas.setFillColor(colors.HexColor('#777777'))
        canvas.drawRightString(landscape(A4)[0] - 12 * mm, 7 * mm, 'Page %d' % d.page)
        canvas.drawString(12 * mm, 7 * mm, 'Amwal W.L.L. - Sales forecast')
        canvas.restoreState()
    doc.build(story, onFirstPage=_page, onLaterPages=_page)
    return bio.getvalue()
