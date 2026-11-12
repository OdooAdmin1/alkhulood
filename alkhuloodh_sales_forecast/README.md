# Alkhuloodh Sales Forecast (Odoo 19)

Hijri-aware sales forecast (revenue and quantity) per branch, product category and top product, for the next 1-6 months.
Reads POS tables only. Writes only to its own tables. Needs python pandas and numpy.

## Menus (Sales Forecast)
- **Dashboard**: filters, a product's sales in every branch (when a product is picked), low / expected / high chart, predicted vs actual vs error per day, comparisons with last week,
  last month, last year and the same Hijri date last year, weekly / monthly / Hijri-month roll-ups, CSV download.
- **Dates & holidays**: confirmed Eid / Ramadan / Ashura / Hijri New Year dates, holidays and full closures.
- **Accuracy**: frozen forecasts compared with what really sold (list and graph), plus a drift warning.
- **Daily actuals**, **Hijri calendar**, **Forecast runs**, **Forecast lines**: the stored data.
- **Configuration**: load history, run forecast, build past forecasts, refresh accuracy, reconciliation.

## First-time setup (staging)
1. Configuration > Load full history. Then Reconciliation summary (compare with the extract figures).
2. Configuration > Run forecast now.
3. Configuration > Build past forecasts (backtest). One click: it builds in the background and the progress shows at the top of the
   page. Then Refresh accuracy.
4. Dates & holidays > Suggest upcoming dates. Confirm each date when it is known. Refresh forecast now.
5. Open the Dashboard.

## Product in a branch (19.0.1.3.0)
Pick a product and a branch together to see that product's forecast in that branch, for revenue and quantity.
Pick a product alone to see all branches combined, with a table and bars of how it sold in each branch.
- A product that a branch sold on at least 100 of the last 365 days is forecast from that branch's own history ("Own history").
- With 28 to 99 selling days, the product's all-branch forecast is split by the branch's recent share of its sales ("Estimated from share").
- With fewer than 28 selling days, or no sale in the last 8 weeks, there is no forecast ("Not enough sales").
- Branch forecasts are made separately, so they may not add up exactly to the all-branch product forecast.
- Ranges for these series are provisional (product ranges stretched by a fixed factor, plus the natural spread of small counts).
  Judge them by Accuracy > Product in branch, "Inside range %".
- Weekly snapshots keep product-in-branch lines only for their first 28 days (what the Accuracy screen compares).
- After upgrading from an older version: Run forecast now, then Build past forecasts (one click), then Refresh accuracy.

## Past forecasts (19.0.1.4.0)
- **One click, background:** the button queues a scheduled action that builds the 12 month-end forecasts, newest first, about
  40 seconds per run, and queues itself again until all are built. Progress ("Past forecasts: 8 of 12 built") shows on the
  Configuration page. "Build now (wait here)" is the fallback that works in the page for about 25 seconds per press.
- **Only what is missing:** each past forecast records which products it holds. Adding a product to "Extra products to forecast"
  queues a background job that adds only that product to the 12 months (seconds, not a full rebuild).
- **Automatic:** the nightly forecast queues the build if a month-end is missing or the products list changed.
- A full rebuild happens only when the forecasting method changes (a new release says so).
- Staging may start the background job a few minutes late because it sleeps when idle; production does not.

## Automatic jobs
- 00:00 UTC load yesterday's sales (re-reads the last 7 days) and refresh accuracy.
- 00:30 UTC refresh the forecast. Sunday's run is kept as the frozen weekly snapshot used for accuracy.

## Known limits
- Product forecasts: the 70 best sellers of the last 12 months that are still selling, plus the products listed in Configuration > Extra products. A product with no sale in the last 56 days gets no forecast.
- Quantity forecast: those products only. Revenue: every level.
- A confirmed Eid / Ramadan date moves the occasion pattern; the Hijri month shown in the calendar is not shifted.
- Closures apply to all branches. Stock-outs are not handled. Access groups are placeholders.
