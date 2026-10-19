-- Source listing and location views. No demand or sales modelling.

CREATE OR REPLACE VIEW mart.v_listing AS
SELECT l.listing_id, p.platform_id, p.platform_code, p.platform_name, l.platform_sku,
       pr.product_id, pr.canonical_name, b.brand_name, b.brand_type,
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
