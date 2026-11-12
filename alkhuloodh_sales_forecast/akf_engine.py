"""Alkhuloodh sales forecasting engine.

Standalone: needs only pandas and numpy. It imports nothing from Odoo, so the same file
runs inside an Odoo scheduled action or on any outside machine.

Inputs
  sales : DataFrame with columns day, warehouse_id, product_id, categ_id, rev, qty
          (gross POS sales before returns, revenue ex-VAT, bad products already removed;
           categ_id = -1 for uncategorised products)
  cal   : DataFrame, one row per day, with columns
          date, hijri_year, hijri_month, hijri_day, ramadan_day,
          pos_eid_fitr, pos_eid_adha, pos_hijri_new_year, pos_ashura, fixed_event
          (pos_* = signed days from the event; Muharram events already carry the
           +1 day shop-closure shift; client-confirmed dates are applied to this table)

Forecast = level x weekday factor x Hijri-month index x occasion multiplier,
learned hierarchically (company -> branch / category -> branch x category -> product).
"""
import numpy as np
import pandas as pd

K_N, V0 = 2.0, 300.0          # shrinkage constants
LEVEL_DAYS = 56               # plain days used for the current level
TOP_PRODUCTS = 70
PAIR_STRONG_DAYS = 100        # product in a branch: sold on at least this many of the last 365 days -> own model
PAIR_MIN_DAYS = 28            # ... at least this many -> share of the product's all-branch forecast; fewer -> no forecast
PAIR_SHARE_DAYS = 91          # window used to measure a branch's share of a product's sales
PAIR_WIDEN = {'model': 1.3, 'alloc': 1.5}   # provisional range stretch for product-in-branch series (see Accuracy screen)


def event_key(r):
    pa = r.pos_ashura
    if pd.notna(pa) and -2 <= pa <= 2:
        return f'ashura:{int(pa)}'
    ph = r.pos_hijri_new_year
    if pd.notna(ph) and -1 <= ph <= 1:
        return f'hny:{int(ph)}'
    pf = r.pos_eid_fitr
    if pd.notna(pf) and -12 <= pf <= 6:
        return f'fitr:{int(pf)}'
    pa2 = r.pos_eid_adha
    if pd.notna(pa2) and -12 <= pa2 <= 6:
        return f'adha:{int(pa2)}'
    if r.ramadan_day > 0:
        return f'ram:{int(r.ramadan_day)}'
    if isinstance(r.fixed_event, str) and r.fixed_event:
        return 'skip'          # Gregorian fixed events are not used (Hijri-only model)
    return 'plain'


class Engine:
    def __init__(self, sales, cal):
        cal = cal.copy()
        cal['date'] = pd.to_datetime(cal['date'])
        cal['fixed_event'] = cal['fixed_event'].fillna('')
        self.cal = cal
        self.ci = cal.set_index('date')
        self.ALL = pd.DatetimeIndex(self.ci.index)
        self.keys = pd.Series([event_key(r) for r in cal.itertuples()], index=self.ALL)

        sales = sales.copy()
        sales['day'] = pd.to_datetime(sales['day'])
        self.END = sales.day.max()
        self.DAYS = pd.date_range(sales.day.min(), self.END)
        D = self.DAYS

        self.tot = sales.groupby('day').rev.sum().reindex(D).fillna(0.0)
        self.br = {w: g.groupby('day').rev.sum().reindex(D).fillna(0.0) for w, g in sales.groupby('warehouse_id')}
        self.brfirst = sales.groupby('warehouse_id').day.min()
        self.ct = {c: g.groupby('day').rev.sum().reindex(D).fillna(0.0) for c, g in sales.groupby('categ_id')}
        self.bc = {k: g.groupby('day').rev.sum().reindex(D).fillna(0.0) for k, g in sales.groupby(['warehouse_id', 'categ_id'])}
        self.pcat = sales.sort_values('day').groupby('product_id').categ_id.first()
        self.prod_rev = {p: g.groupby('day').rev.sum().reindex(D).fillna(0.0) for p, g in sales.groupby('product_id')}
        self.prod_qty = {p: g.groupby('day').qty.sum().reindex(D).fillna(0.0) for p, g in sales.groupby('product_id')}
        # product x branch: kept as small frames, turned into daily series only for the products that are forecast
        self._pair_frames = {k: g[['day', 'rev', 'qty']] for k, g in sales.groupby(['warehouse_id', 'product_id'])}
        self._pair_by_product = {}
        for (b, p) in self._pair_frames:
            self._pair_by_product.setdefault(p, []).append(b)
        self._pair_cache = {}
        self._pshape = {}
        self.pair_method = {}
        self.pair_price = {}

    # ---------- stage 0: shared learning (weekday factors, Hijri-month index, occasion multipliers)
    def _weekday_factors(self, origin):
        s = self.tot[:origin]
        pl = s[self.keys.reindex(s.index) == 'plain']
        ma = pl.rolling('29D', center=True).mean()
        ratio = (pl / ma).dropna()
        w = ratio.groupby(ratio.index.dayofweek).median()
        return w / w.mean()

    def _hijri_season(self, origin, w):
        s = self.tot[:origin]
        pl = s[self.keys.reindex(s.index) == 'plain']
        adj = pl / pl.index.dayofweek.map(w).values
        ci = self.ci
        d = pd.DataFrame({'x': adj.values, 'hy': ci.loc[adj.index, 'hijri_year'].values,
                          'hm': ci.loc[adj.index, 'hijri_month'].values})
        g = d.groupby(['hy', 'hm']).x.agg(['mean', 'size'])
        g = g[g['size'] >= 8]
        lg = np.log(g['mean']).unstack('hm').reindex(columns=range(1, 13))
        a = pd.Series(0.0, index=lg.index)
        for _ in range(60):
            sm = lg.sub(a, axis=0).mean(axis=0)
            a = lg.sub(sm, axis=1).mean(axis=1)
        sm = sm.reindex(range(1, 13))
        xs = np.arange(1, 13)
        ok = sm.notna().values
        xe = np.concatenate([xs[ok] - 12, xs[ok], xs[ok] + 12])
        ye = np.tile(sm[ok].values, 3)
        full = np.interp(xs, xe, ye)
        full = full - full.mean()
        x3 = np.concatenate([xs - 12, xs, xs + 12])
        y3 = np.tile(full, 3)
        pos = (ci.loc[self.ALL, 'hijri_month'].values + (ci.loc[self.ALL, 'hijri_day'].values - 0.5) / 30 - 0.5)
        return pd.Series(np.exp(np.interp(pos, x3, y3)), index=self.ALL)

    def shared(self, origin):
        """w (weekday factors), S (Hijri-month index per day), m3 (company occasion multipliers)."""
        origin = pd.Timestamp(origin)
        w = self._weekday_factors(origin)
        S = self._hijri_season(origin, w)
        s = self.tot[:origin]
        pl = s[self.keys.reindex(s.index) == 'plain']
        x = pl / (pl.index.dayofweek.map(w).values * S[pl.index].values)
        ms = {}
        for d, k in self.keys[:origin].items():
            if k in ('plain', 'skip') or d not in s.index:
                continue
            win = x[(x.index >= d - pd.Timedelta(days=45)) & (x.index <= d + pd.Timedelta(days=45))]
            if len(win) < 10:
                continue
            ms.setdefault(k, []).append(s[d] / (w[d.dayofweek] * S[d] * win.mean()))
        m3 = {k: float(np.median(v)) for k, v in ms.items()}
        return {'origin': origin, 'w': w, 'S': S, 'm3': m3}

    # ---------- building blocks
    def raw_shape(self, series, sh):
        origin, w, S = sh['origin'], sh['w'], sh['S']
        s = series[:origin]
        kk = self.keys.reindex(s.index)
        x = s / (s.index.dayofweek.map(w).values * S[s.index].values)
        Lc = x.where(kk == 'plain').rolling('91D', center=True, min_periods=10).mean()
        ev = (kk != 'plain') & (kk != 'skip') & Lc.notna()
        exp = (s.index.dayofweek.map(w).values * S[s.index].values * Lc)[ev]
        d = pd.DataFrame({'k': kk[ev].values, 'num': s[ev].values, 'den': exp.values})
        g = d.groupby('k').agg(num=('num', 'sum'), den=('den', 'sum'), n=('num', 'size'))
        return {k: (r.num / r.den if r.den > 0 else np.nan, r.n, r.num) for k, r in g.iterrows()}

    @staticmethod
    def blend(raw, parent, K=K_N, V=V0):
        out = {}
        for k, mp in parent.items():
            if k in raw and not np.isnan(raw[k][0]):
                mr, n, v = raw[k]
                lam = n / (n + K) * v / (v + V)
                mr = min(max(mr, mp / 8.0), mp * 8.0) if mp > 0 else 0.0
                out[k] = 0.0 if mp == 0 else float(np.exp(lam * np.log(max(mr, 0.02)) + (1 - lam) * np.log(max(mp, 0.02))))
            else:
                out[k] = mp
        return out

    def level(self, series, sh, n=LEVEL_DAYS):
        origin, w, S = sh['origin'], sh['w'], sh['S']
        s = series[:origin]
        pl = s[self.keys.reindex(s.index) == 'plain'].iloc[-n:]
        if len(pl) < 28:
            return np.nan
        return max(float((pl / (pl.index.dayofweek.map(w).values * S[pl.index].values)).mean()), 0.0)

    def fcst(self, L, dates, sh, m):
        w, S = sh['w'], sh['S']
        return pd.Series([max(L * w[d.dayofweek] * S[d] * m.get(self.keys[d], 1.0), 0.0) for d in dates], index=dates)

    # ---------- stage 1: branch and category shapes
    def shapes(self, sh):
        origin, m3 = sh['origin'], sh['m3']
        mb = {b: self.blend(self.raw_shape(sr, sh), m3) for b, sr in self.br.items()
              if self.brfirst[b] <= origin - pd.Timedelta(days=70)}
        cats = [c for c in self.ct if c != -1 and self.ct[c][:origin].sum() > 0]
        mc = {c: self.blend(self.raw_shape(self.ct[c], sh), m3) for c in cats}
        return mb, mc

    # ---------- stage 2: branch x category cells for one branch
    def cells_for_branch(self, b, sh, mb, mc, dates):
        origin, m3 = sh['origin'], sh['m3']
        out = {}
        for (bb, c), sr in self.bc.items():
            if bb != b or bb not in mb or c not in mc or sr[:origin].sum() <= 0:
                continue
            L = self.level(sr, sh)
            if np.isnan(L):
                continue
            par = {k: (0.0 if m3[k] == 0 else mb[bb].get(k, 1) * mc[c].get(k, 1) / m3[k]) for k in m3}
            mbc = self.blend(self.raw_shape(sr, sh), par)
            out[(bb, c)] = self.fcst(L, dates, sh, mbc)
        return out

    # ---------- stage 3: top products
    def top_products(self, sh, mc, n=TOP_PRODUCTS):
        origin = sh['origin']
        year_ago = origin - pd.Timedelta(days=365)
        # biggest sellers of the LAST 12 MONTHS that are still selling (not all-time: old, replaced products drop out)
        ranked = sorted((p for p in self.prod_rev if self.is_active(p, origin)),
                        key=lambda p: self.prod_rev[p][year_ago:origin].sum(), reverse=True)[:n]
        return [p for p in ranked if self.pcat.get(p, -1) in mc]

    def is_active(self, p, origin):
        """Sold something in the last 56 days. A product with no recent sales gets no forecast (not a row of zeros)."""
        return self.prod_rev[p][origin - pd.Timedelta(days=LEVEL_DAYS - 1):origin].sum() > 0

    def product_forecast(self, p, sh, mc, dates):
        c = self.pcat[p]
        out = {}
        if not self.is_active(p, sh['origin']):
            return out
        for nm, PS in (('prod_rev', self.prod_rev), ('prod_qty', self.prod_qty)):
            sr = PS[p]
            L = self.level(sr, sh)
            if np.isnan(L):
                continue
            shape = self.blend(self.raw_shape(sr, sh), mc[c])
            self._pshape[(nm, p)] = shape
            out[nm] = self.fcst(L, dates, sh, shape)
        return out

    # ---------- stage 4: product in one branch
    def _pair_series(self, b, p):
        key = (b, p)
        if key not in self._pair_cache:
            f = self._pair_frames[key].set_index('day')
            self._pair_cache[key] = (f['rev'].reindex(self.DAYS).fillna(0.0), f['qty'].reindex(self.DAYS).fillna(0.0))
        return self._pair_cache[key]

    def pair_class(self, b, p, origin):
        """'model' (own history is long enough), 'alloc' (share of the product forecast) or None (not enough sales)."""
        f = self._pair_frames.get((b, p))
        if f is None or b not in self.br:
            return None
        sold = f[(f['rev'] > 0) | (f['qty'] > 0)]
        recent = sold[(sold['day'] > origin - pd.Timedelta(days=LEVEL_DAYS)) & (sold['day'] <= origin)]
        if recent.empty:
            return None
        n = int(((sold['day'] > origin - pd.Timedelta(days=365)) & (sold['day'] <= origin)).sum())
        return 'model' if n >= PAIR_STRONG_DAYS else 'alloc' if n >= PAIR_MIN_DAYS else None

    def pair_forecasts(self, p, sh, dates, prod_f):
        """Forecast of product p in each branch. prod_f = {'prod_rev': Series, 'prod_qty': Series} (all branches)."""
        origin = sh['origin']
        out = {'pair_rev': {}, 'pair_qty': {}}
        for b in self._pair_by_product.get(p, []):
            cls = self.pair_class(b, p, origin)
            if cls is None:
                continue
            rev_s, qty_s = self._pair_series(b, p)
            for nm, pnm, sr, PS in (('pair_rev', 'prod_rev', rev_s, self.prod_rev[p]), ('pair_qty', 'prod_qty', qty_s, self.prod_qty[p])):
                if pnm not in prod_f:
                    continue
                how = cls
                fc = None
                if cls == 'model':
                    L = self.level(sr, sh)
                    if not np.isnan(L):
                        shape = self.blend(self.raw_shape(sr, sh), self._pshape[(pnm, p)])
                        fc = self.fcst(L, dates, sh, shape)
                    else:
                        how = 'alloc'
                if fc is None:
                    tot = float(PS[origin - pd.Timedelta(days=PAIR_SHARE_DAYS - 1):origin].sum())
                    part = float(sr[origin - pd.Timedelta(days=PAIR_SHARE_DAYS - 1):origin].sum())
                    if tot <= 0 or part <= 0:
                        continue
                    fc = prod_f[pnm] * (part / tot)
                out[nm][(b, p)] = fc
                self.pair_method[(b, p)] = how
                if nm == 'pair_rev':      # average price: turns unit counting noise into revenue noise
                    win = slice(origin - pd.Timedelta(days=PAIR_SHARE_DAYS - 1), origin)
                    q = float(qty_s[win].sum())
                    self.pair_price[(b, p)] = float(rev_s[win].sum()) / q if q > 0 else 1.0
        return out

    # ---------- everything in one go (outside use / tests)
    def run(self, origin, dates, products=True, extra=()):
        sh = self.shared(origin)
        mb, mc = self.shapes(sh)
        F = {'cell': {}, 'branch': {}, 'category': {}, 'company': {}, 'prod_rev': {}, 'prod_qty': {},
             'pair_rev': {}, 'pair_qty': {}}
        self.pair_method = {}
        self.pair_price = {}
        for b in mb:
            F['cell'].update(self.cells_for_branch(b, sh, mb, mc, dates))
        self.rollup(F)
        if products:
            chosen = self.top_products(sh, mc)
            chosen += [p for p in extra if p not in chosen and p in self.prod_rev and self.pcat.get(p, -1) in mc]
            for p in chosen:
                pf = self.product_forecast(p, sh, mc, dates)
                for nm, f in pf.items():
                    F[nm][p] = f
                if pf:
                    for nm, d in self.pair_forecasts(p, sh, dates, pf).items():
                        F[nm].update(d)
        return F, sh

    @staticmethod
    def rollup(F):
        F['branch'], F['category'], F['company'] = {}, {}, {}
        for (b, c), f in F['cell'].items():
            F['branch'][b] = F['branch'].get(b, 0) + f
            F['category'][c] = F['category'].get(c, 0) + f
            F['company'][0] = F['company'].get(0, 0) + f


# ======================================================================================
# Ranges (low / expected / high)
# ======================================================================================
RANGE_FALLBACK = (0.5, 1.0, 1.5, 1.0)       # q10, q50, q90, widening
NEAR_DAYS = 91


class RangeParams:
    """Ratios actual/forecast measured in the rolling backtest, by level, block size (1, 7, 28 days),
    event or plain, and near (first 91 days) or far horizon. `widen` stretches the measured band
    so later periods reach about 80% coverage."""

    def __init__(self, df):
        self.t = {(r.level, int(r.B), r.kind, r.bucket): (float(r.q10), float(r.q50), float(r.q90), float(r.widen))
                  for r in df.itertuples()}

    def get(self, level, B, kind, bucket, method='model'):
        if level == 'pair':      # no measured parameters yet: use the product ones, stretched
            q10, q50, q90, k = self.get('product', B, kind, bucket)
            return q10, q50, q90, k * PAIR_WIDEN.get(method, PAIR_WIDEN['alloc'])
        other = 'near' if bucket == 'far' else 'far'
        for k in ((level, B, kind, bucket), (level, B, kind, other), (level, B, 'event', bucket)):
            if k in self.t:
                return self.t[k]
        return RANGE_FALLBACK


def _band(f_values, q10, q90, k, unit=None):
    low = np.clip(f_values - (f_values - f_values * q10) * k, 0, None)
    high = f_values + (f_values * q90 - f_values) * k
    if unit:        # small series of counted units: never narrower than the natural spread of counting
        spread = 1.28 * np.sqrt(f_values * unit)     # unit = 1 for quantity, average price for revenue
        low = np.clip(np.minimum(low, f_values - spread), 0, None)
        high = np.maximum(high, f_values + spread)
    return low, high


_LEVEL_OUT = {'cell': 'cell', 'branch': 'branch', 'category': 'category', 'company': 'company',
              'prod_rev': 'product', 'prod_qty': 'product', 'pair_rev': 'pair', 'pair_qty': 'pair'}
_METRIC = {'prod_qty': 'qty', 'pair_qty': 'qty'}


def _lines(engine, F, origin, dates, params):
    """One row per day x series: expected, low, high. Returns a DataFrame."""
    n = len(dates)
    ev = ~engine.keys.reindex(dates).isin(['plain', 'skip']).values
    h = (dates - pd.Timestamp(origin)).days.values - 1
    bucket = np.where(h < NEAR_DAYS, 'near', 'far')
    parts = []
    for lvl, series in F.items():
        if not series:
            continue
        is_pair = lvl.startswith('pair')
        qs = {}
        for sid, f in series.items():
            method = engine.pair_method.get(sid, 'alloc') if is_pair else 'model'
            if method not in qs:
                q = [params.get('pair' if is_pair else lvl, 1, 'event' if e else 'plain', b, method) if is_pair
                     else params.get(lvl, 1, 'event' if e else 'plain', b) for e, b in zip(ev, bucket)]
                qs[method] = (np.array([x[0] for x in q]), np.array([x[2] for x in q]), np.array([x[3] for x in q]))
            q10, q90, k = qs[method]
            fv = f.values.astype(float)
            unit = (1.0 if lvl == 'pair_qty' else engine.pair_price.get(sid, 1.0)) if is_pair else None
            low, high = _band(fv, q10, q90, k, unit=unit)
            if lvl == 'cell':
                wh, ca, pr = sid[0], sid[1], None
            elif lvl == 'branch':
                wh, ca, pr = sid, None, None
            elif lvl == 'category':
                wh, ca, pr = None, sid, None
            elif lvl == 'company':
                wh, ca, pr = None, None, None
            elif is_pair:
                wh, ca, pr = sid[0], engine.pcat.get(sid[1], -1), sid[1]
            else:
                wh, ca, pr = None, engine.pcat.get(sid, -1), sid
            if ca is not None and ca == -1:
                ca = None
            parts.append(pd.DataFrame({
                'date': dates, 'level': _LEVEL_OUT[lvl], 'warehouse_id': wh, 'categ_id': ca, 'product_id': pr,
                'metric': _METRIC.get(lvl, 'revenue'), 'expected': fv, 'low': low, 'high': high,
                'method': engine.pair_method.get(sid) if is_pair else None}))
    return pd.concat(parts, ignore_index=True)


Engine.forecast_lines = lambda self, F, origin, dates, params: _lines(self, F, origin, dates, params)
