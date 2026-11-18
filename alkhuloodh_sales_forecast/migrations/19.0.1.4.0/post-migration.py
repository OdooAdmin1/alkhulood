"""Past forecasts made by 19.0.1.3.x already hold product-in-branch lines: mark them as current-method runs and record
which products they cover, so only missing products (and the one month that is still old) are built."""


def migrate(cr, version):
    cr.execute("""
        UPDATE alkhuloodh_sf_run r
           SET rule_ver = 'pair1',
               covered_product_ids = COALESCE((SELECT string_agg(DISTINCT l.product_id::text, ',')
                                                 FROM alkhuloodh_sf_forecast_line l
                                                WHERE l.run_id = r.id AND l.level = 'product'), '')
         WHERE r.kind = 'backtest' AND r.state = 'done'
           AND EXISTS (SELECT 1 FROM alkhuloodh_sf_forecast_line l WHERE l.run_id = r.id AND l.level = 'pair')
    """)
