"""SQL builders for the dashboard.

Every function returns a plain SQL string with filter values inlined as literals, so
the text shown under "SQL for DuckDB UI" is exactly what the dashboard executes and
can be pasted into the DuckDB UI unchanged. No Streamlit imports here: testable alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

DATASETS = {
    "synthetic": "Synthetic sourdough panel (demo, Apr–Sep 2026)",
    "blinkit": "Real: Blinkit prices, 5 cities (Jan 2024)",
}
# The real Blinkit file has prices and stock flags but no stock counts or ratings, so no sales estimate.
SALES_METRICS = {"est_gmv": "Est. GMV (Rs)", "est_units": "Est. units sold",
                 "est_units_demand": "Est. demand (incl. lost sales)"}
PRICE_METRICS = {"avg_price": "Avg selling price (Rs)", "avg_discount": "Avg discount (%)",
                 "availability": "In-stock share (%)", "listings": "Products listed"}
METRICS = {**SALES_METRICS, **PRICE_METRICS}
METRIC_AGG = {"est_gmv": "sum(est_gmv)", "est_units": "sum(est_units)",
              "est_units_demand": "sum(est_units_demand)", "avg_price": "avg(avg_selling_price)",
              "avg_discount": "avg(avg_discount_pct)", "availability": "100 * avg(in_stock_share)",
              "listings": "count(DISTINCT listing_id)"}


def has_sales(dataset: str) -> bool:
    return dataset == "synthetic"


def metrics_for(dataset: str) -> dict:
    return METRICS if has_sales(dataset) else PRICE_METRICS


def dataset_sql(dataset: str, prefix: str = "") -> str:
    """Rows belonging to a dataset. Real catalogues (BigBasket, Zepto) sit at region 'Unknown', so they
    never appear here; they have their own tab."""
    return f"{prefix}is_synthetic" if dataset == "synthetic" else f"NOT {prefix}is_synthetic"
GRAINS = ["day", "week", "month"]
SCOPES = {"sourdough": "Sourdough only", "bakery": "Sourdough vs other bakery", "all": "All categories"}
COLOR_BY = {"platform_code": "Platform", "region": "Region", "market": "Market", "tier": "City tier",
            "brand_name": "Brand", "segment": "Sourdough vs other"}

# Column expressions per source, so one filter set can target different tables.
SALES = dict(date="sales_date", platform="platform_code", region="region", tier="tier",
             market="market", brand="brand_name", flour="flour", l3="l3", l1="l1",
             sourdough="is_sourdough", synthetic="is_synthetic")
SNAPSHOT = dict(date="CAST(s.scraped_at AS DATE)", platform="l.platform_code", region="lo.region",
                tier="lo.tier", market="lo.market", brand="l.brand_name", flour="l.flour", l3="l.l3",
                l1="l.l1", sourdough="l.is_sourdough", synthetic="s.is_synthetic")


@dataclass
class Filters:
    date_from: date
    date_to: date
    platforms: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    tiers: list[int] = field(default_factory=list)
    markets: list[str] = field(default_factory=list)
    scope: str = "sourdough"
    l3s: list[str] = field(default_factory=list)
    brands: list[str] = field(default_factory=list)
    flours: list[str] = field(default_factory=list)
    metric: str = "est_gmv"
    grain: str = "week"
    dataset: str = "synthetic"


def lit(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, date):
        return f"DATE '{v.isoformat()}'"
    return "'" + str(v).replace("'", "''") + "'"


def _in(col: str, values) -> str:
    vals = [v for v in values if v != "(unspecified)"]
    parts = [f"{col} IN ({', '.join(lit(v) for v in vals)})"] if vals else []
    if "(unspecified)" in values:
        parts.append(f"{col} IS NULL")
    return parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")"


def where(f: Filters, c: dict = SALES, skip: tuple = (), extra: tuple = ()) -> str:
    """WHERE clause for the sidebar filters. `skip` drops filters by name, e.g. 'scope'."""
    w = [f"{c['date']} BETWEEN {lit(f.date_from)} AND {lit(f.date_to)}",
         f"{c['region']} <> 'Unknown'"]  # location 0 = national catalogue rows with no sales signal
    for name, col, vals in [("platform", c["platform"], f.platforms), ("region", c["region"], f.regions),
                            ("tier", c["tier"], f.tiers), ("market", c["market"], f.markets),
                            ("l3", c["l3"], f.l3s), ("brand", c["brand"], f.brands),
                            ("flour", c["flour"], f.flours)]:
        if vals and name not in skip:
            w.append(_in(col, vals))
    if "scope" not in skip:
        w += {"sourdough": [c["sourdough"]], "bakery": [f"{c['l1']} = 'Bakery'"], "all": []}[f.scope]
    w.append(c["synthetic"] if f.dataset == "synthetic" else f"NOT {c['synthetic']}")
    return "WHERE " + "\n  AND ".join(w + list(extra))


def _bakery(f: Filters) -> str:
    """Filters for 'sourdough share of bakery' metrics: ignore scope/sub-category, keep the rest."""
    return where(f, skip=("scope", "l3", "flour", "brand"), extra=("l1 = 'Bakery'",))


SEGMENT = "CASE WHEN is_sourdough THEN 'Sourdough' ELSE 'Other bakery' END"


def synthetic_check(f):
    return f"SELECT count(*) AS rows, coalesce(bool_or(is_synthetic), FALSE) AS has_synthetic\nFROM mart.sales_daily\n{where(f)}"


def kpis(f):
    return f"""SELECT sum(est_units) AS units,
       sum(est_gmv) AS gmv,
       sum(est_units_demand) AS demand,
       sum(price_per_100g * est_units) / nullif(sum(est_units), 0) AS rs_per_100g,
       100 * avg(in_stock_share) AS availability_pct,
       avg(avg_selling_price) AS avg_price,
       avg(avg_discount_pct) AS avg_discount_pct,
       avg(price_per_100g) AS avg_rs_per_100g,
       count(DISTINCT listing_id) AS skus
FROM mart.sales_daily
{where(f)}"""


def sourdough_share(f):
    return f"""SELECT 100 * sum(est_gmv) FILTER (WHERE is_sourdough) / nullif(sum(est_gmv), 0) AS sd_share_of_bakery_gmv_pct
FROM mart.sales_daily
{_bakery(f)}"""


def trend(f, color: str):
    series = SEGMENT if color == "segment" else f"CAST({color} AS VARCHAR)"
    step = {"day": "1 DAY", "week": "7 DAY", "month": "1 MONTH"}[f.grain]
    return f"""-- {f.grain}ly {f.metric} by {color}; series beyond the top 7 fold into 'Other'.
-- 'Other' re-aggregates already-aggregated series: exact for sums, approximate for averages.
-- {f.grain.title()}s that run past the first or last date in view are dropped (they'd plot as a fake drop).
WITH rows AS (
    SELECT * FROM mart.sales_daily
    {where(f)}
), base AS (
    SELECT date_trunc('{f.grain}', sales_date) AS period, {series} AS series,
           {METRIC_AGG[f.metric]} AS value
    FROM rows
    GROUP BY 1, 2
), complete AS (
    SELECT DISTINCT period FROM base, (SELECT min(sales_date) AS lo, max(sales_date) AS hi FROM rows)
    WHERE period >= lo AND period + INTERVAL {step} - INTERVAL 1 DAY <= hi
), ranked AS (
    SELECT series, row_number() OVER (ORDER BY sum(value) DESC NULLS LAST) AS rk FROM base GROUP BY series
)
SELECT period, CASE WHEN rk <= 7 THEN series ELSE 'Other' END AS series,
       {"sum" if f.metric in SALES_METRICS or f.metric == "listings" else "avg"}(value) AS value
FROM base JOIN ranked USING (series) JOIN complete USING (period)
GROUP BY 1, 2 ORDER BY 1, 2"""


def market_platform(f):
    return f"""SELECT market, platform_code, {METRIC_AGG[f.metric]} AS value
FROM mart.sales_daily
{where(f)}
GROUP BY 1, 2 ORDER BY 1, 2"""


def tier_share(f):
    return f"""SELECT date_trunc('month', sales_date) AS month, 'Tier ' || tier AS tier,
       100 * sum(est_gmv) FILTER (WHERE is_sourdough) / nullif(sum(est_gmv), 0) AS sd_share_of_bakery_gmv_pct
FROM mart.sales_daily
{_bakery(f)}
  AND tier IS NOT NULL
GROUP BY 1, 2
HAVING count(DISTINCT sales_date) >= 28  -- full months only
ORDER BY 1, 2"""


def city_map(f):
    return f"""SELECT d.city_name, d.market, lo.lat, lo.lon, {METRIC_AGG[f.metric]} AS value
FROM (SELECT * FROM mart.sales_daily
      {where(f)}) d
JOIN mart.v_location lo USING (location_id)
WHERE lo.lat IS NOT NULL
GROUP BY ALL ORDER BY value DESC"""


def brand_share(f):
    return f"""SELECT market, brand_name,
       100 * sum(est_gmv) / sum(sum(est_gmv)) OVER (PARTITION BY market) AS gmv_share_pct
FROM mart.sales_daily
{where(f)}
GROUP BY market, brand_name
ORDER BY market, gmv_share_pct DESC"""


def price_ladder(f):
    return f"""SELECT canonical_name, brand_name, platform_code,
       round(avg(price_per_100g), 1) AS rs_per_100g,
       round(avg(avg_selling_price)) AS avg_selling_price,
       round(avg(avg_discount_pct), 1) AS avg_discount_pct
FROM mart.sales_daily
{where(f)}
  AND price_per_100g IS NOT NULL
GROUP BY ALL ORDER BY rs_per_100g DESC, canonical_name, platform_code
LIMIT 60  -- the 60 priciest per 100 g; narrow the filters to see others"""


def discount_response(f):
    return f"""SELECT CASE WHEN avg_discount_pct < 5 THEN '0-5%'
            WHEN avg_discount_pct < 12 THEN '5-12%' ELSE '12%+' END AS discount_band,
       {SEGMENT} AS segment,
       count(*) AS sku_location_days,
       avg(est_units) AS avg_units_per_sku_day
FROM mart.sales_daily
{where(f)}
GROUP BY 1, 2 ORDER BY 2, 1"""


def oos_heatmap(f):
    return f"""SELECT dayname(s.scraped_at) AS weekday, isodow(s.scraped_at) AS dow, hour(s.scraped_at) AS hour,
       100 * avg(CAST(NOT s.in_stock AS INT)) AS oos_pct, count(*) AS snapshots
FROM core.listing_snapshot s
JOIN mart.v_listing l USING (listing_id)
JOIN mart.v_location lo USING (location_id)
{where(f, SNAPSHOT)}
GROUP BY 1, 2, 3 ORDER BY 2, 3"""


def lost_demand(f):
    return f"""SELECT platform_code, market,
       round(100 * avg(in_stock_share), 1) AS availability_pct,
       round(sum(est_units)) AS est_units,
       round(sum(est_units_demand - est_units)) AS est_lost_units,
       round(100 * sum(est_units_demand - est_units) / nullif(sum(est_units_demand), 0), 1) AS lost_pct_of_demand
FROM mart.sales_daily
{where(f)}
GROUP BY 1, 2 ORDER BY est_lost_units DESC"""


def search_share(f, term: str):
    w = [f"search_term = {lit(term)}", f"week_start BETWEEN {lit(f.date_from)} AND {lit(f.date_to)}"]
    if f.platforms:
        w.append(_in("platform_code", f.platforms))
    if f.markets:
        w.append(_in("market", f.markets))
    return f"""-- mart.share_of_search has no region/tier/brand columns: only platform, market and dates apply.
SELECT platform_code, brand_name,
       100 * sum(top10_slots) / sum(sum(top10_slots)) OVER (PARTITION BY platform_code) AS share_of_top10_pct,
       sum(avg_rank * top10_slots) / sum(top10_slots) AS avg_rank,
       100 * sum(sponsored_share * top10_slots) / sum(top10_slots) AS sponsored_slot_pct
FROM mart.share_of_search
WHERE {chr(10).join(('  AND ' if i else '') + x for i, x in enumerate(w))}
GROUP BY platform_code, brand_name
ORDER BY platform_code, share_of_top10_pct DESC"""


def affinity(f):
    w = ["TRUE"]
    for col, vals in [("a.region", f.regions), ("a.tier", f.tiers), ("a.market", f.markets)]:
        if vals:
            w.append(_in(col, vals))
    return f"""-- REAL data (Swiggy/Zomato restaurant listings) joined to quick-commerce penetration.
WITH pen AS (
    SELECT market,
           100 * sum(est_gmv) FILTER (WHERE is_sourdough) / nullif(sum(est_gmv), 0) AS sd_share_of_bakery_gmv_pct,
           bool_or(is_synthetic) AS penetration_is_synthetic
    FROM mart.sales_daily
    {_bakery(f)}
    GROUP BY 1
)
SELECT a.market, a.region, a.tier, a.restaurants,
       round(100 * a.premium_share, 1)            AS premium_1000_pct,
       round(100 * a.western_midpremium_share, 1) AS western_midprem_pct,
       round(100 * a.cafe_share, 1)               AS cafe_pct,
       a.sourdough_restaurants, a.affinity_index,
       round(pen.sd_share_of_bakery_gmv_pct, 1)   AS sd_share_of_bakery_gmv_pct,
       pen.penetration_is_synthetic
FROM mart.market_affinity a LEFT JOIN pen USING (market)
WHERE {' AND '.join(w)}
ORDER BY a.affinity_index DESC"""


def oos_by_category(f):
    """Out-of-stock rate by market x category. Works on both datasets (no sales needed)."""
    return f"""SELECT market, l2 AS category,
       100 * (1 - avg(in_stock_share)) AS oos_pct,
       count(DISTINCT listing_id) AS products
FROM mart.sales_daily
{where(f, skip=("l3",))}
GROUP BY 1, 2
ORDER BY 1, 2"""


CATALOGUE_PLATFORMS = ("bigbasket", "zepto", "blinkit")


def catalogue_summary():
    return """-- REAL data: what each real source lists in bakery, and how much of it is sourdough.
SELECT l.platform_code,
       min(CAST(s.scraped_at AS DATE)) AS from_date, max(CAST(s.scraped_at AS DATE)) AS to_date,
       count(DISTINCT l.listing_id) AS products,
       count(DISTINCT l.listing_id) FILTER (WHERE l.l1 = 'Bakery') AS bakery_products,
       count(DISTINCT l.listing_id) FILTER (WHERE l.is_sourdough) AS sourdough_products
FROM mart.v_listing l
JOIN core.listing_snapshot s USING (listing_id)
WHERE NOT s.is_synthetic
GROUP BY 1 ORDER BY products DESC"""


def catalogue_bakery(sourdough_only: bool):
    cond = "l.is_sourdough" if sourdough_only else "l.l1 = 'Bakery'"
    return f"""-- REAL data: bakery listings across the real sources, latest price seen.
SELECT l.platform_code, l.brand_name, l.canonical_name AS product, l.l3 AS category, l.is_sourdough,
       arg_max(s.selling_price, s.scraped_at) AS selling_price,
       arg_max(s.mrp, s.scraped_at) AS mrp,
       l.pack_grams,
       round(100 * arg_max(s.selling_price, s.scraped_at) / nullif(l.pack_grams, 0), 1) AS rs_per_100g,
       max(CAST(s.scraped_at AS DATE)) AS last_seen
FROM mart.v_listing l
JOIN core.listing_snapshot s USING (listing_id)
WHERE NOT s.is_synthetic AND {cond}
GROUP BY l.listing_id, l.platform_code, l.brand_name, l.canonical_name, l.l3, l.is_sourdough, l.pack_grams
ORDER BY l.is_sourdough DESC, l.platform_code, selling_price DESC"""
