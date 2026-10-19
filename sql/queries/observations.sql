-- name: source_coverage
-- title: Source files and loaded rows
SELECT source_type, source_file, loaded_at, row_count, notes
FROM core.scrape_run ORDER BY run_id;

-- name: platform_listings
-- title: Observed platform listings
SELECT p.platform_code, count(DISTINCT s.listing_id) AS listings, count(*) AS observations
FROM core.listing_snapshot s JOIN core.listing l USING (listing_id)
JOIN ref.platform p USING (platform_id) GROUP BY 1 ORDER BY 1;
