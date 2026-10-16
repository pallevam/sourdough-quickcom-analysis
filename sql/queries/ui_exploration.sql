-- Exploration queries for the DuckDB UI. Start it from the project root with:
--   duckdb -cmd "ATTACH 'data/qcom.duckdb' AS qcom (READ_ONLY); USE qcom;" -ui
-- (NOT `duckdb -readonly ... -ui`: that makes the UI's own scratch database read-only and every
-- query fails with 'Catalog "localmemdb" does not exist'.) In each cell set the database dropdown
-- (top right) to `qcom`, or prefix names with `qcom.`. Synthetic rows carry is_synthetic = TRUE.

-- 1. Orientation: what's in the warehouse
SELECT schema_name, table_name, estimated_size AS approx_rows
FROM duckdb_tables() WHERE database_name = 'qcom' ORDER BY schema_name, table_name;

-- 2. Profile the main analysis table (min/max/nulls/distinct for every column)
SUMMARIZE mart.sales_daily;

-- 3. Weekly sourdough GMV by region (Rs lakh), wide format for eyeballing the trend
PIVOT (
    SELECT week_start, region, est_gmv FROM mart.sales_weekly_geo
    WHERE is_sourdough AND region <> 'Unknown'
      AND week_start IN (SELECT week_start FROM mart.sales_daily GROUP BY 1 HAVING count(DISTINCT sales_date) = 7)
) ON region USING round(sum(est_gmv) / 1e5, 2)
GROUP BY week_start ORDER BY week_start;

-- 4. Market x platform sourdough GMV, last 28 days (Rs lakh)
PIVOT (
    SELECT market, platform_code, est_gmv FROM mart.sales_daily
    WHERE is_sourdough AND region <> 'Unknown'
      AND sales_date > (SELECT max(sales_date) - INTERVAL 28 DAY FROM mart.sales_daily)
) ON platform_code USING round(sum(est_gmv) / 1e5, 2)
GROUP BY market ORDER BY market;

-- 5. Sourdough share of bread GMV by city tier and month: is it spreading beyond metros?
SELECT strftime(month_start, '%Y-%m') AS month, tier,
       round(100 * sum(est_gmv) FILTER (WHERE is_sourdough) / sum(est_gmv), 1) AS sd_share_of_bread_pct,
       round(sum(est_units) FILTER (WHERE is_sourdough)) AS sd_units
FROM mart.sales_daily
WHERE l1 = 'Bakery' AND tier IS NOT NULL
GROUP BY ALL ORDER BY tier, month;

-- 6. Sourdough price premium over everyday bread by market (Rs per 100 g)
SELECT market,
       round(avg(price_per_100g) FILTER (WHERE is_sourdough), 1)     AS sourdough,
       round(avg(price_per_100g) FILTER (WHERE NOT is_sourdough), 1) AS other_bread,
       round(avg(price_per_100g) FILTER (WHERE is_sourdough)
             / avg(price_per_100g) FILTER (WHERE NOT is_sourdough), 1) AS premium_x
FROM mart.sales_daily WHERE l1 = 'Bakery' AND region <> 'Unknown'
GROUP BY market ORDER BY premium_x DESC;

-- 7. Promo response: units/day by discount band, per sourdough product
SELECT canonical_name,
       CASE WHEN avg_discount_pct < 5 THEN '0-5%' WHEN avg_discount_pct < 12 THEN '5-12%' ELSE '12%+' END AS discount_band,
       count(*) AS sku_location_days,
       round(avg(est_units), 2) AS avg_units_per_day,
       round(avg(in_stock_share), 2) AS avg_availability
FROM mart.sales_daily WHERE is_sourdough AND region <> 'Unknown'
GROUP BY ALL ORDER BY canonical_name, discount_band;

-- 8. When does sourdough go out of stock? Out-of-stock rate by scrape hour and day of week
SELECT dayname(s.scraped_at) AS weekday, isodow(s.scraped_at) AS dow, hour(s.scraped_at) AS hour,
       round(100 * avg(CAST(NOT s.in_stock AS INT)), 1) AS oos_pct
FROM core.listing_snapshot s JOIN mart.v_listing l USING (listing_id)
WHERE l.is_sourdough
GROUP BY ALL ORDER BY dow, hour;

-- 9. Brand strength heatmap: brand share of sourdough GMV in each market (%)
PIVOT (
    SELECT market, brand_name,
           100 * sum(est_gmv) / sum(sum(est_gmv)) OVER (PARTITION BY market) AS gmv_share_pct
    FROM mart.sales_daily
    WHERE is_sourdough AND region <> 'Unknown'
    GROUP BY market, brand_name
) ON brand_name USING round(first(gmv_share_pct), 1)
GROUP BY market ORDER BY market;

-- 10. Does search rank drive sales? Avg rank on the "sourdough" page vs units, per SKU-pincode-week
WITH r AS (
    SELECT listing_id, location_id, date_trunc('week', scraped_at) AS week_start,
           avg(rank_position) AS avg_rank
    FROM core.search_result WHERE search_term = 'sourdough' GROUP BY ALL
), u AS (
    SELECT listing_id, location_id, week_start, sum(est_units) AS units
    FROM mart.sales_daily WHERE is_sourdough GROUP BY ALL
), j AS (
    SELECT *, CASE WHEN avg_rank <= 3 THEN 'rank 1-3' WHEN avg_rank <= 6 THEN 'rank 4-6' ELSE 'rank 7-10' END AS band
    FROM r JOIN u USING (listing_id, location_id, week_start)
)
SELECT coalesce(band, 'all') AS rank_band, count(*) AS sku_weeks, round(avg(units), 1) AS avg_weekly_units,
       round(corr(avg_rank, units), 2) AS corr_rank_units
FROM j GROUP BY ROLLUP (band) ORDER BY rank_band;

-- 11. One SKU at one pincode, scrape by scrape: the restock / sell-down sawtooth behind the estimates
SELECT s.scraped_at, l.platform_code, lo.locality, s.selling_price, s.in_stock,
       s.inventory_qty, s.units_depleted, s.restocked, s.rating_count, s.new_ratings
FROM mart.v_snapshot s
JOIN mart.v_listing l USING (listing_id) JOIN mart.v_location lo USING (location_id)
WHERE l.platform_code = 'blinkit' AND l.canonical_name ILIKE '%Classic White Sourdough%'
  AND lo.locality = 'Koramangala'
ORDER BY s.scraped_at LIMIT 60;

-- 12. Estimator accuracy by week (synthetic ground truth): watch for drift
SELECT week_start, platform_code, est_method,
       round(100 * bias_units, 1) AS bias_units_pct, round(100 * bias_demand, 1) AS bias_demand_pct
FROM mart.estimator_backtest ORDER BY platform_code, week_start;

-- 13. REAL DATA: premium food culture by market (Swiggy + Zomato restaurant listings)
SELECT market, region, tier, restaurants,
       round(100 * premium_share, 1) AS premium_1000_pct,
       round(100 * western_midpremium_share, 1) AS western_midprem_pct,
       round(100 * cafe_share, 1) AS cafe_pct,
       sourdough_restaurants, affinity_index
FROM mart.market_affinity ORDER BY affinity_index DESC;

-- 14. REAL DATA: what do restaurants in the top-affinity markets serve? (top cuisines)
SELECT coalesce(c.metro_cluster, c.city_name) AS market, cuisine, count(*) AS restaurants
FROM core.restaurant_listing r
JOIN ref.city c ON c.city_id = r.city_id
CROSS JOIN unnest(r.cuisines) AS t(cuisine)
WHERE coalesce(c.metro_cluster, c.city_name) IN ('Goa', 'Mumbai MMR', 'Delhi NCR', 'Bangalore', 'Hyderabad')
GROUP BY market, cuisine
QUALIFY row_number() OVER (PARTITION BY market ORDER BY count(*) DESC) <= 8
ORDER BY market, restaurants DESC;

-- 15. The sales-estimation assumptions (edit with the CLI, not here: the UI connection is read-only)
FROM ref.model_param;
