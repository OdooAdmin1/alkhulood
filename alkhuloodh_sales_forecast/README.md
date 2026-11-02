# Alkhuloodh Sales Forecast (Odoo 19)

Hijri-aware sales forecast (revenue and quantity) per branch, product category and top product, for the next 1-6 months.
Reads POS tables only. Writes only to its own tables. Needs python pandas and numpy.

## Menus (Sales Forecast)
- **Dashboard**: filters, low / expected / high chart, predicted vs actual vs error per day, comparisons with last week,
  last month, last year and the same Hijri date last year, weekly / monthly / Hijri-month roll-ups, CSV download.
- **Dates & holidays**: confirmed Eid / Ramadan / Ashura / Hijri New Year dates, holidays and full closures.
- **Accuracy**: frozen forecasts compared with what really sold (list and graph), plus a drift warning.
- **Daily actuals**, **Hijri calendar**, **Forecast runs**, **Forecast lines**: the stored data.
- **Configuration**: load history, run forecast, build past forecasts, refresh accuracy, reconciliation.

## First-time setup (staging)
1. Configuration > Load full history. Then Reconciliation summary (compare with the extract figures).
2. Configuration > Run forecast now.
3. Configuration > Build past forecasts (backtest). Press again until nothing remains. Then Refresh accuracy.
4. Dates & holidays > Suggest upcoming dates. Confirm each date when it is known. Refresh forecast now.
5. Open the Dashboard.

## Automatic jobs
- 00:00 UTC load yesterday's sales (re-reads the last 7 days) and refresh accuracy.
- 00:30 UTC refresh the forecast. Sunday's run is kept as the frozen weekly snapshot used for accuracy.

## Known limits
- Quantity forecast: top 70 products only. Revenue: every level.
- A confirmed Eid / Ramadan date moves the occasion pattern; the Hijri month shown in the calendar is not shifted.
- Closures apply to all branches. Stock-outs are not handled. Access groups are placeholders.
