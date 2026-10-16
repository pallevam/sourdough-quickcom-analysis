"""SQL builders for the dashboard.

Every function returns a plain SQL string with filter values inlined as literals, so
the text shown under "SQL for DuckDB UI" is exactly what the dashboard executes and
can be pasted into the DuckDB UI unchanged. No Streamlit imports here: testable alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

METRICS = {"est_gmv": "Est. GMV (Rs)", "est_units": "Est. units sold",
           "est_units_demand": "Est. demand (incl. lost sales)"}
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
    include_synthetic: bool = True


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
    if not f.include_synthetic:
        w.append(f"NOT {c['synthetic']}")
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
       count(DISTINCT listing_id) AS skus
FROM mart.sales_daily
{where(f)}"""


def sourdough_share(f):
    return f"""SELECT 100 * sum(est_gmv) FILTER (WHERE is_sourdough) / nullif(sum(est_gmv), 0) AS sd_share_of_bakery_gmv_pct
FROM mart.sales_daily
{_bakery(f)}"""


def trend(f, color: str):
    series = SEGMENT if color == "segment" else f"CAST({color} AS VARCHAR)"
    full_len = {"day": "1", "week": "7", "month": "day(last_day(period))"}[f.grain]
    return f"""-- {f.grain}ly {f.metric} by {color}; series beyond the top 7 fold into 'Other'.
-- Partial {f.grain}s at the edges of the date range are dropped (they'd plot as a fake drop).
WITH base AS (
    SELECT date_trunc('{f.grain}', sales_date) AS period, {series} AS series,
           sum({f.metric}) AS value, count(DISTINCT sales_date) AS days
    FROM mart.sales_daily
    {where(f)}
    GROUP BY 1, 2
), complete AS (
    SELECT period FROM base GROUP BY period HAVING max(days) = {full_len}
), ranked AS (
    SELECT series, row_number() OVER (ORDER BY sum(value) DESC) AS rk FROM base GROUP BY series
)
SELECT period, CASE WHEN rk <= 7 THEN series ELSE 'Other' END AS series, sum(value) AS value
FROM base JOIN ranked USING (series) JOIN complete USING (period)
GROUP BY 1, 2 ORDER BY 1, 2"""


def market_platform(f):
    return f"""SELECT market, platform_code, sum({f.metric}) AS value
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
    return f"""SELECT d.city_name, d.market, lo.lat, lo.lon, sum(d.{f.metric}) AS value
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
GROUP BY ALL ORDER BY rs_per_100g DESC, canonical_name, platform_code"""


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
