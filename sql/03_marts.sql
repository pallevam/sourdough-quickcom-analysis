-- =============================================================================
-- Analytical views. Platforms do not publish sales, so units are ESTIMATED from
-- what a scraper can observe, in this order of preference:
--
--   1. inventory depletion   sum of drops in inventory_qty between consecutive
--                            snapshots (rises = restocks, ignored). Undercounts
--                            sales that happen between a restock and the next
--                            snapshot, so scrape soon after stores open.
--   2. rating velocity       new ratings x orders_per_rating x units_per_order
--                            (ref.model_param). Noisy at low volume, so calibrate.
--
-- Observed sales are capped by stock. est_units_demand scales them up by the
-- in-stock share to approximate unconstrained demand (lost sales included).
--
-- Views are fine to tens of millions of snapshots. Beyond that, materialise
-- mart.listing_day with CREATE TABLE ... AS and refresh it incrementally.
-- =============================================================================

CREATE OR REPLACE VIEW mart.v_listing AS
SELECT l.listing_id, p.platform_id, p.platform_code, p.platform_name, l.platform_sku,
       pr.product_id, pr.canonical_name, b.brand_name, b.brand_type, b.is_synthetic AS brand_is_synthetic,
       c.category_id, c.l1, c.l2, c.l3, c.category_path, pr.is_sourdough,
       pr.pack_grams, pr.pack_units,
       pr.attributes ->> 'flour'  AS flour,
       pr.attributes ->> 'format' AS bread_format,
       l.match_method, l.first_seen_at, l.last_seen_at
FROM core.listing l
JOIN ref.platform p USING (platform_id)
LEFT JOIN core.product pr USING (product_id)
LEFT JOIN ref.brand b ON b.brand_id = pr.brand_id
LEFT JOIN ref.category c ON c.category_id = pr.category_id;

CREATE OR REPLACE VIEW mart.v_location AS
SELECT lo.location_id, lo.pincode, lo.locality, c.city_id,
       coalesce(c.city_name, 'Unknown') AS city_name, c.state,
       coalesce(c.region, 'Unknown') AS region, c.tier,
       coalesce(c.metro_cluster, c.city_name, 'Unknown') AS market,
       coalesce(lo.lat, c.lat) AS lat, coalesce(lo.lon, c.lon) AS lon
FROM ref.location lo
LEFT JOIN ref.city c USING (city_id);

-- Snapshot-level, with consecutive-snapshot deltas.
CREATE OR REPLACE VIEW mart.v_snapshot AS
WITH s AS (
    SELECT *,
           lag(inventory_qty) OVER w AS prev_inventory,
           lag(rating_count)  OVER w AS prev_rating_count,
           lag(scraped_at)    OVER w AS prev_scraped_at
    FROM core.listing_snapshot
    WINDOW w AS (PARTITION BY listing_id, location_id ORDER BY scraped_at)
)
SELECT s.*,
       CAST(scraped_at AS DATE) AS snap_date,
       round(100 * (1 - selling_price / nullif(mrp, 0)), 1) AS discount_pct,
       CASE WHEN inventory_qty < prev_inventory THEN prev_inventory - inventory_qty ELSE 0 END AS units_depleted,
       inventory_qty > prev_inventory AS restocked,
       greatest(rating_count - prev_rating_count, 0) AS new_ratings
FROM s;

-- Grain: listing x location x day. The core estimate everything else rolls up.
CREATE OR REPLACE VIEW mart.listing_day AS
WITH p AS (SELECT
        max(value) FILTER (WHERE param = 'orders_per_rating') AS orders_per_rating,
        max(value) FILTER (WHERE param = 'units_per_order') AS units_per_order,
        max(value) FILTER (WHERE param = 'min_availability_for_uplift') AS min_avail
    FROM ref.model_param),
d AS (
    SELECT listing_id, location_id, snap_date,
           count(*)                            AS snapshots,
           avg(CAST(in_stock AS INT))          AS in_stock_share,
           avg(selling_price)                  AS avg_selling_price,
           avg(mrp)                            AS avg_mrp,
           avg(discount_pct)                   AS avg_discount_pct,
           count(inventory_qty) > 0            AS has_inventory_signal,
           count(rating_count) > 0             AS has_rating_signal,
           sum(units_depleted)                 AS units_depleted,
           sum(new_ratings)                    AS new_ratings,
           bool_or(is_synthetic)               AS is_synthetic
    FROM mart.v_snapshot
    GROUP BY ALL
)
SELECT d.*,
       CASE WHEN has_inventory_signal THEN 'inventory_depletion'
            WHEN has_rating_signal THEN 'rating_velocity' ELSE 'none' END AS est_method,
       -- NULL, not 0, when the source has neither signal (e.g. Blinkit Kaggle prices): unknown, not zero
       CASE WHEN has_inventory_signal THEN units_depleted
            WHEN has_rating_signal THEN new_ratings * p.orders_per_rating * p.units_per_order END AS est_units,
       CASE WHEN has_inventory_signal THEN units_depleted
            WHEN has_rating_signal THEN new_ratings * p.orders_per_rating * p.units_per_order END
         / CASE WHEN in_stock_share >= p.min_avail THEN in_stock_share ELSE 1 END AS est_units_demand
FROM d, p;

-- Wide, BI-ready daily fact: every dimension you might slice by, pre-joined.
CREATE OR REPLACE VIEW mart.sales_daily AS
SELECT ld.snap_date AS sales_date,
       date_trunc('week', ld.snap_date)  AS week_start,
       date_trunc('month', ld.snap_date) AS month_start,
       dayname(ld.snap_date)             AS weekday,
       li.platform_code, li.platform_name,
       lo.region, lo.state, lo.market, lo.city_name, lo.tier, lo.pincode, lo.locality,
       li.l1, li.l2, li.l3, li.category_path, li.is_sourdough,
       li.brand_name, li.brand_type, li.product_id, li.canonical_name, li.flour, li.pack_grams,
       ld.listing_id, ld.location_id,
       ld.est_method, ld.est_units, ld.est_units_demand,
       ld.est_units * ld.avg_selling_price                    AS est_gmv,
       ld.avg_selling_price, ld.avg_mrp, ld.avg_discount_pct,
       100 * ld.avg_selling_price / nullif(li.pack_grams, 0)  AS price_per_100g,
       ld.in_stock_share, ld.snapshots, ld.is_synthetic
FROM mart.listing_day ld
JOIN mart.v_listing li USING (listing_id)
JOIN mart.v_location lo USING (location_id);

-- The headline trend table: platform x geography x category x week.
CREATE OR REPLACE VIEW mart.sales_weekly_geo AS
SELECT week_start, platform_code, region, market, city_name, tier,
       l1, l2, l3, is_sourdough,
       sum(est_units)                        AS est_units,
       sum(est_units_demand)                 AS est_units_demand,
       sum(est_gmv)                          AS est_gmv,
       sum(est_gmv) / nullif(sum(est_units), 0) AS avg_realised_price,
       sum(price_per_100g * est_units) / nullif(sum(est_units), 0) AS avg_price_per_100g,
       avg(avg_discount_pct)                 AS avg_discount_pct,
       avg(in_stock_share)                   AS availability,
       count(DISTINCT listing_id)            AS active_skus,
       count(DISTINCT brand_name)            AS active_brands,
       count(DISTINCT sales_date)            AS days_observed,  -- < 7 at panel edges: exclude from growth maths
       bool_or(is_synthetic)                 AS has_synthetic
FROM mart.sales_daily
GROUP BY ALL;

-- Discoverability: who owns the top of the search page.
CREATE OR REPLACE VIEW mart.share_of_search AS
WITH r AS (
    SELECT date_trunc('week', s.scraped_at) AS week_start, li.platform_code, lo.market,
           s.search_term, li.brand_name, s.rank_position, s.is_sponsored, li.is_sourdough
    FROM core.search_result s
    JOIN mart.v_listing li USING (listing_id)
    JOIN mart.v_location lo USING (location_id)
    WHERE s.rank_position <= 10
)
SELECT week_start, platform_code, market, search_term, brand_name,
       count(*)                                                    AS top10_slots,
       count(*) / sum(count(*)) OVER (PARTITION BY week_start, platform_code, market, search_term)
                                                                   AS share_of_top10,
       avg(rank_position)                                          AS avg_rank,
       avg(CAST(is_sponsored AS INT))                              AS sponsored_share,
       avg(CAST(is_sourdough AS INT))                              AS sourdough_share
FROM r
GROUP BY week_start, platform_code, market, search_term, brand_name;

-- Market-level premium / western food culture from food-delivery restaurant listings,
-- a proxy for the audience that buys Rs 50+/100g bread. Notes from profiling the data:
--   * the Kaggle scrapes cap rows per city (~2,000), so only SHARES are meaningful
--   * 'Bakery' in Indian listings is mostly cakes, puffs and mithai: weak signal, not indexed
--   * median price-for-two barely varies by city; the share at Rs 1000+ does
-- Shares are computed per platform, then averaged, so Swiggy/Zomato mix doesn't bias a city.
CREATE OR REPLACE VIEW mart.market_affinity AS
WITH per_platform AS (
    SELECT coalesce(c.metro_cluster, c.city_name)  AS market,
           any_value(coalesce(c.region, 'Unknown')) AS region,
           min(c.tier)                              AS tier,
           r.platform_id,
           count(*)                                 AS restaurants,
           avg(CAST(r.price_for_two >= 1000 AS INT)) AS premium_share,
           avg(CAST(list_has_any(r.cuisines, ['Continental', 'Italian', 'European', 'Salad',
                    'Healthy Food', 'Bakery', 'Cafe', 'Desserts']) AND r.price_for_two >= 400 AS INT))
                                                    AS western_midpremium_share,
           avg(CAST(r.is_bakery AS INT))            AS bakery_share,
           avg(CAST(r.is_cafe AS INT))              AS cafe_share,
           count(*) FILTER (WHERE r.mentions_sourdough) AS sourdough_restaurants,
           median(r.price_for_two)                  AS median_price_for_two
    FROM core.restaurant_listing r
    JOIN ref.city c USING (city_id)
    GROUP BY 1, r.platform_id
), m AS (
    SELECT market, any_value(region) AS region, min(tier) AS tier,
           sum(restaurants) AS restaurants,
           avg(premium_share) AS premium_share,
           avg(western_midpremium_share) AS western_midpremium_share,
           avg(bakery_share) AS bakery_share,
           avg(cafe_share) AS cafe_share,
           sum(sourdough_restaurants) AS sourdough_restaurants,
           avg(median_price_for_two) AS median_price_for_two
    FROM per_platform
    WHERE restaurants >= 200
    GROUP BY 1
    HAVING sum(restaurants) >= 500
)
SELECT *,
       round(( (premium_share - avg(premium_share) OVER ()) / stddev(premium_share) OVER ()
             + (western_midpremium_share - avg(western_midpremium_share) OVER ())
               / stddev(western_midpremium_share) OVER ()
             ) / 2, 2) AS affinity_index
FROM m;

-- Synthetic data only: how far are the estimators from the truth?
CREATE OR REPLACE VIEW mart.estimator_backtest AS
SELECT date_trunc('week', ld.snap_date) AS week_start, li.platform_code, ld.est_method,
       sum(t.units_sold)                       AS true_units_sold,
       sum(t.units_sold + t.units_lost)        AS true_demand,
       sum(ld.est_units)                       AS est_units,
       sum(ld.est_units_demand)                AS est_units_demand,
       sum(ld.est_units) / nullif(sum(t.units_sold), 0) - 1                       AS bias_units,
       sum(ld.est_units_demand) / nullif(sum(t.units_sold + t.units_lost), 0) - 1 AS bias_demand
FROM mart.listing_day ld
JOIN core.synthetic_truth_daily t
  ON t.sales_date = ld.snap_date AND t.listing_id = ld.listing_id AND t.location_id = ld.location_id
JOIN mart.v_listing li ON li.listing_id = ld.listing_id
GROUP BY ALL;
