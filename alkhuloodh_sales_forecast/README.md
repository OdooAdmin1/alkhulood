# Alkhuloodh Sales Forecast (Odoo 19)

Hijri-aware sales forecast (revenue and quantity) per branch, product category and top product, for the next 1-6 months.
Reads POS tables only. Writes only to its own tables. Needs python pandas and numpy.

## Icon
static/description/icon.png (256 x 256) is the module icon in Apps and the app icon in the main menu (web_icon on the root menu).

## Menus (Sales Forecast)
- **Dashboard**: filters, a product's sales in every branch (when a product is picked), low / expected / high chart, predicted vs actual vs error per day, comparisons with last week,
  last month, last year and the same Hijri date last year, weekly / monthly / Hijri-month roll-ups, CSV download.
- **Forecast report**: all products by branch in one table (one row per product, one column per branch, totals), like the
  client's paper report. Excel, PDF and print. See below.
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

## Forecast report (19.0.1.6.0)
Sales Forecast > Forecast report: pick the dates, quantity or revenue, a category and the branches. **One table per day**
(up to 31 days), laid out like the client's demo: one row per product, one column per branch, a total column and an
"All products" row. Day buttons (and arrows) switch between the days; the heading of each day shows the date and its
Hijri date in English letters (for example "Wed 18 Nov 2026 | 8 Jumada II 1448").
- **Forecast days** (after the last day of actuals): the forecast, rounded; totals add up the figures shown.
- **Past days**: for every branch two columns, Actual (POS sales of that day) and Forecast (the latest forecast made before that
  day: the month-end or weekly forecast, kept for about 12 months). Quantity does not include free items; revenue is ex-VAT before
  returns. "-" means no sales / not enough sales to forecast.
- Slanted figures are estimates (the branch sells the product on too few days for its own model: the product forecast split by
  the branch's recent share).
- **Downloads**: Excel (one sheet per day; forecast days also carry the likely range, low and high, below the table), PDF (A4
  landscape, one page per day). "Print this day" prints the day on screen. No period total (ask if one is wanted).
- **Quick select**: Last month, Last week, Last 7 days, Yesterday, Today, Tomorrow, Next 7 days, This week, Next week, This
  month (weeks run Sunday to Saturday). Each is cut to the days that have sales or a forecast.
- **Hijri dates** come from the calendar and follow the dates confirmed in Dates & holidays: a confirmed first day of Ramadan,
  Eid al-Fitr, Eid al-Adha, Hijri New Year or Ashura moves the start of that Hijri month, and neighbouring months give way so
  every month stays 29 or 30 days. The dashboard's own Hijri column is not shifted.
- **Report setup > Report branches**: short column name (MRQ, HT ...), order and show/hide for each branch. The headings start
  as in the client's demo when a branch is first listed; they can be edited. A branch that should never be listed (e.g. MAIN
  STORE) can be archived (Actions > Archive). Under every table a key lists what each heading stands for.
- **Report setup > Report products**: the products and their order, as on the client's paper report (the demo list is
  pre-filled). "Match names to products" links each name to an Odoo product; rows marked Check or No match can be set by hand.
  With no rows at all, every forecast product is listed.
- PDF text uses a standard font: use English product names.

## Access levels (19.0.1.6.0)
Each level includes the one below.
- **Sales Forecast: Viewer** (for example POS users): only the Dashboard and the Forecast report with its downloads. No access
  to any stored data (Daily actuals, Forecast lines, runs, Accuracy, Hijri calendar, Dates & holidays, Report setup,
  Configuration), in the menus or in the code. A Viewer sees **one branch only**: assign it under Report setup > Viewer
  branches (one row per user). A Viewer with no branch assigned sees nothing. The dashboard and report are limited to that branch
  on the server, whatever the screen sends; the all-branch product panel and the accuracy warning are not shown to a Viewer.
- **Sales Forecast: User**: everything read-only, all branches.
- **Sales Forecast: Manager**: also Configuration, Report setup (including Viewer branches) and dates & holidays.
- **Administrator**: everything.
Give the level under Settings > Users (access rights). Check with Odoo whether a Light User licence may open this app.

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
