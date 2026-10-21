"""Category-wide exploration of observed assortment, independent of sales estimates.

Literal escaping keeps exported SQL reproducible. Identifiers are fixed or allowlisted.
All metrics retain snapshot provenance; unknown geography and stock remain unknown.
"""
from dataclasses import dataclass, field
from datetime import date

from queries import lit


@dataclass
class MarketFilters:
    platforms: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    brands: list[str] = field(default_factory=list)
    markets: list[str] = field(default_factory=list)
    date_from: date | None = None
    date_to: date | None = None
    search: str = ""


def source():
    return f"""SELECT s.*, l.platform_code, p.channel, raw.raw_name AS canonical_name, coalesce(nullif(trim(raw.raw_brand), ''), 'Unknown') AS brand_name,
       coalesce(nullif(trim(raw.raw_category), ''), 'Unknown') AS category,
       CASE WHEN nullif(trim(raw.raw_category), '') IS NOT NULL THEN 'Source category'
            ELSE 'Not supplied' END AS category_basis,
       raw.pack_size_raw,
       lo.market, lo.region, lo.city_name, r.source_file,
       CASE WHEN r.source_type = 'kaggle' AND l.platform_code IN ('zepto', 'bigbasket')
            THEN 'Archive timestamp (observation date unknown)' ELSE 'Observation timestamp' END AS date_basis,
       CASE WHEN s.selling_price > 0 AND s.mrp >= s.selling_price
            THEN 100.0 * (1 - s.selling_price / nullif(s.mrp, 0)) END AS discount_pct
FROM core.listing_snapshot s
JOIN mart.v_listing l USING (listing_id)
JOIN core.listing raw USING (listing_id)
JOIN ref.platform p ON p.platform_id = l.platform_id
JOIN mart.v_location lo USING (location_id)
JOIN core.scrape_run r USING (run_id)
WHERE r.source_type IN ('kaggle', 'own_scrape', 'vendor')"""


def filtered(f):
    clauses = ['TRUE']
    for col, vals in [('platform_code', f.platforms), ('category', f.categories),
                      ('brand_name', f.brands), ('market', f.markets)]:
        if vals:
            clauses.append(f"lower({col}) IN ({', '.join(lit(v.lower()) for v in vals)})")
    if f.date_from:
        clauses.append(f"CAST(scraped_at AS DATE) >= {lit(f.date_from)}")
    if f.date_to:
        clauses.append(f"CAST(scraped_at AS DATE) <= {lit(f.date_to)}")
    if f.search:
        clauses.append(f"contains(lower(canonical_name), lower({lit(f.search)}))")
    return f"WITH observed AS ({source()}), selected AS (SELECT * FROM observed WHERE " + ' AND '.join(clauses) + ')\n'


def options(f, column):
    if column not in {'platform_code', 'category', 'brand_name', 'market'}:
        raise ValueError('Unknown filter column')
    return filtered(f) + f"SELECT DISTINCT {column} FROM selected WHERE {column} IS NOT NULL ORDER BY 1"


def products(f):
    return filtered(f) + """SELECT listing_id, platform_code, channel, brand_name,
       canonical_name AS product, category, category_basis, pack_size_raw,
       count(DISTINCT location_id) AS locations, count(*) AS observations,
       count(DISTINCT scraped_at::DATE) AS observed_days,
       min(scraped_at)::DATE AS first_record, max(scraped_at)::DATE AS last_record,
       any_value(date_basis) AS date_basis,
       string_agg(DISTINCT source_file, '; ' ORDER BY source_file) AS source_files,
       avg(selling_price) FILTER (WHERE selling_price > 0) AS avg_pack_price,
       min(selling_price) FILTER (WHERE selling_price > 0) AS min_pack_price,
       max(selling_price) FILTER (WHERE selling_price > 0) AS max_pack_price,
       avg(discount_pct) AS avg_discount_pct,
       count(in_stock) AS stock_observations,
       count(*) FILTER (WHERE in_stock) AS in_stock_observations,
       100 * avg(in_stock::INT) AS in_stock_pct
FROM selected GROUP BY ALL ORDER BY platform_code, category, product, listing_id"""


def summary(f, dimension='category'):
    if dimension not in {'category', 'brand_name', 'platform_code'}:
        raise ValueError('Unknown summary dimension')
    return f"""WITH products AS ({products(f)})
SELECT platform_code, {dimension + ',' if dimension != 'platform_code' else ''}
       count(*) AS listings, count(DISTINCT brand_name) FILTER (WHERE brand_name <> 'Unknown') AS known_brands,
       count(avg_pack_price) AS priced_listings,
       median(avg_pack_price) AS median_pack_price,
       quantile_cont(avg_pack_price, 0.25) AS price_p25,
       quantile_cont(avg_pack_price, 0.75) AS price_p75,
       avg(avg_discount_pct) AS avg_discount_pct,
       sum(stock_observations) AS stock_observations,
       100.0 * sum(in_stock_observations) / nullif(sum(stock_observations), 0) AS in_stock_pct,
       min(first_record) AS first_record, max(last_record) AS last_record
FROM products GROUP BY ALL ORDER BY listings DESC"""


def coverage():
    return f"""WITH observed AS ({source()})
SELECT platform_code, channel, source_file, date_basis,
       count(DISTINCT listing_id) AS listings, count(*) AS observations,
       count(DISTINCT market) FILTER (WHERE market <> 'Unknown') AS known_markets,
       count(DISTINCT scraped_at::DATE) AS observed_days,
       min(scraped_at)::DATE AS first_record, max(scraped_at)::DATE AS last_record,
       round(100.0 * count(in_stock) / count(*), 1) AS stock_coverage_pct,
       count(*) FILTER (WHERE selling_price IS NULL OR selling_price <= 0) AS invalid_price_rows,
       count(DISTINCT listing_id) FILTER (WHERE category_basis = 'Not supplied') AS missing_category_listings
FROM observed GROUP BY ALL ORDER BY platform_code, source_file"""


def trend(f):
    return filtered(f) + """SELECT scraped_at::DATE AS record_date, platform_code, market,
       avg(selling_price) FILTER (WHERE selling_price > 0) AS avg_pack_price,
       100 * avg(in_stock::INT) AS in_stock_pct,
       count(in_stock) AS stock_observations,
       count(DISTINCT listing_id) AS listings
FROM selected WHERE date_basis = 'Observation timestamp'
GROUP BY ALL ORDER BY record_date, platform_code, market"""


def availability(f):
    return filtered(f) + """SELECT platform_code, market, category,
       count(DISTINCT listing_id) AS listings, count(in_stock) AS stock_observations,
       count(DISTINCT scraped_at::DATE) AS observed_days,
       100 * avg((NOT in_stock)::INT) AS out_of_stock_pct
FROM selected GROUP BY ALL ORDER BY out_of_stock_pct DESC NULLS LAST"""


def geography():
    return f"""WITH observed AS ({source()})
SELECT platform_code, market, date_basis, count(DISTINCT listing_id) AS listings,
       min(scraped_at)::DATE AS first_record, max(scraped_at)::DATE AS last_record
FROM observed GROUP BY ALL ORDER BY platform_code, market"""
