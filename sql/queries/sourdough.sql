-- Named queries for the sourdough deep-dive. `python -m qcom report` runs them in order;
-- `--only <name>` runs one. Each block starts with `-- name: <id>` and `-- title: <text>`.
-- Queries on mart.* include synthetic rows when loaded: check the `synthetic` column.

-- name: platform_overview
-- title: Sourdough by platform: size, share of bread, price, availability
SELECT platform_code,
       count(DISTINCT product_id) FILTER (WHERE is_sourdough)       AS sourdough_products,
       count(DISTINCT brand_name) FILTER (WHERE is_sourdough)       AS sourdough_brands,
       round(sum(est_units) FILTER (WHERE is_sourdough))            AS sourdough_units,
       round(sum(est_gmv) FILTER (WHERE is_sourdough) / 1e5, 1)     AS sourdough_gmv_lakh,
       round(100 * sum(est_gmv) FILTER (WHERE is_sourdough) / sum(est_gmv), 1) AS pct_of_bread_gmv,
       round(avg(price_per_100g) FILTER (WHERE is_sourdough), 1)    AS sd_rs_per_100g,
       round(avg(price_per_100g) FILTER (WHERE NOT is_sourdough), 1) AS other_bread_rs_per_100g,
       round(100 * avg(in_stock_share) FILTER (WHERE is_sourdough), 1) AS sd_availability_pct,
       any_value(est_method)                                        AS est_method,
       bool_or(is_synthetic)                                        AS synthetic
FROM mart.sales_daily
WHERE l1 = 'Bakery' AND region <> 'Unknown'
GROUP BY 1 ORDER BY sourdough_gmv_lakh DESC;

-- name: region_trend_monthly
-- title: Sourdough GMV by region and month (Rs lakh), with month-on-month growth
WITH m AS (
    SELECT month_start, region, sum(est_gmv) / 1e5 AS gmv_lakh, sum(est_units) AS units
    FROM mart.sales_daily
    WHERE is_sourdough AND region <> 'Unknown'
    GROUP BY ALL
    HAVING count(DISTINCT sales_date) >= 28
)
SELECT strftime(month_start, '%Y-%m') AS month, region, round(gmv_lakh, 2) AS gmv_lakh, round(units) AS units,
       round(100 * (gmv_lakh / lag(gmv_lakh) OVER (PARTITION BY region ORDER BY month_start) - 1), 1) AS mom_growth_pct
FROM m ORDER BY region, month;

-- name: market_growth
-- title: Markets ranked by sourdough growth (first 8 vs last 8 full weeks)
WITH w AS (
    SELECT market, region, min(tier) AS tier, week_start,
           sum(est_units) FILTER (WHERE is_sourdough)         AS sd_units,
           sum(est_gmv) FILTER (WHERE is_sourdough)           AS sd_gmv,
           sum(est_gmv) FILTER (WHERE l2 = 'Bread' OR l2 = 'Buns & Pav') AS bread_gmv
    FROM mart.sales_weekly_geo
    WHERE region <> 'Unknown' AND l1 = 'Bakery'
      AND week_start IN (SELECT week_start FROM mart.sales_daily GROUP BY 1 HAVING count(DISTINCT sales_date) = 7)
    GROUP BY ALL
), b AS (
    SELECT *, dense_rank() OVER (ORDER BY week_start) AS wk, count(DISTINCT week_start) OVER () AS n_wk FROM w
)
SELECT market, region, tier,
       round(sum(sd_units) FILTER (WHERE wk <= 8))         AS units_first_8w,
       round(sum(sd_units) FILTER (WHERE wk > n_wk - 8))   AS units_last_8w,
       round(100 * (sum(sd_units) FILTER (WHERE wk > n_wk - 8) / sum(sd_units) FILTER (WHERE wk <= 8) - 1), 1) AS growth_pct,
       round(100 * sum(sd_gmv) FILTER (WHERE wk > n_wk - 8) / sum(bread_gmv) FILTER (WHERE wk > n_wk - 8), 1) AS sd_share_of_bread_gmv_pct
FROM b GROUP BY ALL ORDER BY growth_pct DESC;

-- name: whitespace
-- title: Whitespace: premium food culture (REAL restaurant data) vs sourdough penetration (quick commerce)
WITH pen AS (
    SELECT market,
           100 * sum(est_gmv) FILTER (WHERE is_sourdough) / sum(est_gmv) AS sd_share_of_bread_gmv_pct,
           bool_or(is_synthetic) AS penetration_is_synthetic
    FROM mart.sales_daily
    WHERE l1 = 'Bakery' AND sales_date >= (SELECT max(sales_date) - INTERVAL 28 DAY FROM mart.sales_daily)
    GROUP BY 1
)
SELECT a.market, a.region, a.tier, a.restaurants,
       round(100 * a.premium_share, 1)            AS premium_1000_pct,
       round(100 * a.western_midpremium_share, 1) AS western_midprem_pct,
       a.sourdough_restaurants, a.affinity_index,
       round(pen.sd_share_of_bread_gmv_pct, 1)    AS sd_share_of_bread_gmv_pct,
       pen.penetration_is_synthetic
FROM mart.market_affinity a LEFT JOIN pen USING (market)
ORDER BY a.affinity_index DESC
LIMIT 25;

-- name: brand_share_by_region
-- title: Sourdough brand GMV share by region (%)
SELECT region, brand_name, brand_type,
       round(100 * sum(est_gmv) / sum(sum(est_gmv)) OVER (PARTITION BY region), 1) AS gmv_share_pct
FROM mart.sales_daily
WHERE is_sourdough AND region <> 'Unknown'
GROUP BY region, brand_name, brand_type ORDER BY region, gmv_share_pct DESC;

-- name: price_ladder
-- title: Sourdough price ladder across platforms (last 28 days)
SELECT canonical_name, brand_name, pack_grams, platform_code,
       round(avg(avg_mrp))           AS mrp,
       round(avg(avg_selling_price)) AS avg_selling_price,
       round(avg(avg_discount_pct), 1) AS avg_discount_pct,
       round(avg(price_per_100g), 1) AS rs_per_100g
FROM mart.sales_daily
WHERE is_sourdough AND sales_date >= (SELECT max(sales_date) - INTERVAL 28 DAY FROM mart.sales_daily)
GROUP BY ALL ORDER BY rs_per_100g DESC, canonical_name, platform_code;

-- name: availability_gaps
-- title: Where stock-outs cost the most sourdough sales (estimated lost units)
SELECT platform_code, market,
       round(100 * avg(in_stock_share), 1)        AS availability_pct,
       round(sum(est_units))                      AS est_units,
       round(sum(est_units_demand - est_units))   AS est_lost_units,
       round(100 * sum(est_units_demand - est_units) / sum(est_units_demand), 1) AS lost_pct_of_demand
FROM mart.sales_daily
WHERE is_sourdough AND region <> 'Unknown'
GROUP BY ALL ORDER BY est_lost_units DESC LIMIT 15;

-- name: weekday_profile
-- title: Weekday demand index (100 = average day): sourdough vs other bread
WITH d AS (
    SELECT is_sourdough, sales_date, any_value(weekday) AS weekday, isodow(sales_date) AS dow, sum(est_units) AS units
    FROM mart.sales_daily WHERE l1 = 'Bakery' AND region <> 'Unknown' GROUP BY ALL
)
SELECT weekday,
       round(100 * avg(units) FILTER (WHERE is_sourdough) / avg(avg(units) FILTER (WHERE is_sourdough)) OVER ()) AS sourdough_idx,
       round(100 * avg(units) FILTER (WHERE NOT is_sourdough) / avg(avg(units) FILTER (WHERE NOT is_sourdough)) OVER ()) AS other_bread_idx
FROM d GROUP BY weekday, dow ORDER BY dow;

-- name: flour_mix
-- title: Sourdough variant (flour) mix by region, % of units
SELECT region, coalesce(flour, 'unspecified') AS flour,
       round(100 * sum(est_units) / sum(sum(est_units)) OVER (PARTITION BY region), 1) AS units_pct
FROM mart.sales_daily
WHERE is_sourdough AND region <> 'Unknown'
GROUP BY region, flour ORDER BY region, units_pct DESC;

-- name: share_of_search
-- title: Who owns the "sourdough" search page (last 8 weeks, all markets)
SELECT platform_code, brand_name,
       round(100 * sum(top10_slots) / sum(sum(top10_slots)) OVER (PARTITION BY platform_code), 1) AS share_of_top10_pct,
       round(sum(avg_rank * top10_slots) / sum(top10_slots), 1) AS avg_rank,
       round(100 * sum(sponsored_share * top10_slots) / sum(top10_slots), 1) AS sponsored_slot_pct
FROM mart.share_of_search
WHERE search_term = 'sourdough'
  AND week_start > (SELECT max(week_start) - INTERVAL 8 WEEK FROM mart.share_of_search)
GROUP BY platform_code, brand_name ORDER BY platform_code, share_of_top10_pct DESC;

-- name: estimator_backtest
-- title: Estimator accuracy vs synthetic ground truth (bias = est / true - 1)
SELECT platform_code, est_method,
       sum(true_units_sold) AS true_units, round(sum(est_units)) AS est_units,
       round(100 * (sum(est_units) / sum(true_units_sold) - 1), 1) AS bias_units_pct,
       round(100 * (sum(est_units_demand) / sum(true_demand) - 1), 1) AS bias_demand_pct,
       round(100 * avg(abs(bias_units)), 1) AS weekly_mape_pct
FROM mart.estimator_backtest
GROUP BY ALL ORDER BY platform_code;

-- name: real_zepto_bakery
-- title: REAL DATA - Zepto Kaggle catalogue: bakery & dairy coverage (shows the sourdough gap)
SELECT li.l1, li.l2, count(*) AS skus, count(*) FILTER (WHERE li.is_sourdough) AS sourdough_skus,
       round(avg(s.selling_price)) AS avg_price
FROM mart.v_listing li JOIN core.listing_snapshot s USING (listing_id)
WHERE li.platform_code = 'zepto' AND NOT s.is_synthetic
GROUP BY ALL ORDER BY skus DESC;

-- name: real_sourdough_restaurants
-- title: REAL DATA - Swiggy/Zomato restaurants that mention sourdough
SELECT c.city_name, coalesce(c.metro_cluster, c.city_name) AS market, p.platform_code,
       r.restaurant_name, r.area, r.rating, r.price_for_two
FROM core.restaurant_listing r
JOIN ref.city c USING (city_id) JOIN ref.platform p USING (platform_id)
WHERE r.mentions_sourdough ORDER BY market, r.restaurant_name;

-- name: dq_unmapped_cities
-- title: DATA QUALITY - cities auto-created from raw data (add them to data/seed/cities.csv)
SELECT c.city_name, count(r.restaurant_row_id) AS restaurant_rows
FROM ref.city c LEFT JOIN core.restaurant_listing r USING (city_id)
WHERE NOT c.is_seeded GROUP BY 1 ORDER BY 2 DESC LIMIT 30;

-- name: dq_matching
-- title: DATA QUALITY - how listings were matched to products
SELECT platform_code, match_method, count(*) AS listings FROM mart.v_listing GROUP BY ALL ORDER BY ALL;

-- name: dq_orphans
-- title: DATA QUALITY - orphaned foreign keys (should all be 0)
SELECT 'snapshot -> listing' AS rel, count(*) AS orphans FROM core.listing_snapshot s
  WHERE NOT EXISTS (SELECT 1 FROM core.listing l WHERE l.listing_id = s.listing_id)
UNION ALL SELECT 'snapshot -> location', count(*) FROM core.listing_snapshot s
  WHERE NOT EXISTS (SELECT 1 FROM ref.location l WHERE l.location_id = s.location_id)
UNION ALL SELECT 'listing -> product', count(*) FROM core.listing l
  WHERE NOT EXISTS (SELECT 1 FROM core.product p WHERE p.product_id = l.product_id)
UNION ALL SELECT 'product -> category', count(*) FROM core.product p
  WHERE NOT EXISTS (SELECT 1 FROM ref.category c WHERE c.category_id = p.category_id);
