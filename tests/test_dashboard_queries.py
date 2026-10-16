"""Every dashboard query must run, and return rows, for representative filter sets."""

import os
import sys
from datetime import date
from pathlib import Path

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))
import queries as q  # noqa: E402

DB = Path(os.environ.get("QCOM_DB", ROOT / "data" / "qcom.duckdb"))  # override to test another build
pytestmark = pytest.mark.skipif(not DB.exists(), reason="run `python -m qcom build` first")

BASE = dict(date_from=date(2026, 4, 1), date_to=date(2026, 9, 30))
CASES = {
    "default": q.Filters(**BASE),
    "south_tier2": q.Filters(**BASE, regions=["South"], tiers=[2]),
    "blinkit_units_month": q.Filters(**BASE, platforms=["blinkit"], metric="est_units", grain="month"),
    "bakery_quoted_brand": q.Filters(**BASE, scope="bakery", brands=["Levain & Loaf", "O'Brien's"],
                                     flours=["rye", "(unspecified)"], markets=["Bangalore", "Delhi NCR"]),
    "all_days": q.Filters(**BASE, scope="all", grain="day", metric="est_units_demand"),
    "synthetic_avg_price": q.Filters(**BASE, metric="avg_price", grain="month"),
    "blinkit_real": q.Filters(date_from=date(2024, 1, 17), date_to=date(2024, 2, 1), dataset="blinkit",
                              scope="all", metric="avg_price", grain="day"),
    "blinkit_real_south": q.Filters(date_from=date(2024, 1, 17), date_to=date(2024, 2, 1), dataset="blinkit",
                                    scope="bakery", metric="availability", regions=["South"]),
}
# Queries allowed to be empty for a given case (e.g. no lat/lon, no rye in that slice).
MAY_BE_EMPTY = {("bakery_quoted_brand", "tier_share")}
# Sales-based views the dashboard hides for datasets without a sales signal.
SALES_ONLY = {"sourdough_share", "tier_share", "brand_share", "discount_response", "oos_heatmap",
              "lost_demand", "search"}


def all_queries(f):
    for name, sql in _all_queries(f):
        if q.has_sales(f.dataset) or name not in SALES_ONLY:
            yield name, sql


def _all_queries(f):
    yield "synthetic_check", q.synthetic_check(f)
    yield "kpis", q.kpis(f)
    yield "sourdough_share", q.sourdough_share(f)
    for color in q.COLOR_BY:
        yield f"trend_{color}", q.trend(f, color)
    for name in ["market_platform", "tier_share", "city_map", "brand_share", "price_ladder",
                 "discount_response", "oos_heatmap", "lost_demand", "affinity", "oos_by_category"]:
        yield name, getattr(q, name)(f)
    yield "search", q.search_share(f, "sourdough")


@pytest.fixture(scope="module")
def con():
    c = duckdb.connect(str(DB), read_only=True)
    yield c
    c.close()


@pytest.mark.parametrize("case", CASES)
def test_queries_run(con, case):
    for name, sql in all_queries(CASES[case]):
        df = con.sql(sql).df()
        if (case, name) not in MAY_BE_EMPTY:
            assert len(df), f"{case}/{name} returned no rows:\n{sql}"


def test_literals_are_escaped():
    assert q.lit("O'Brien's") == "'O''Brien''s'"
    assert q.lit(date(2026, 4, 1)) == "DATE '2026-04-01'"
    assert "IS NULL" in q._in("flour", ["(unspecified)"])


def test_catalogue_queries(con):
    summary = con.sql(q.catalogue_summary()).df()
    assert set(summary.platform_code) >= {"bigbasket", "blinkit", "zepto"}
    assert summary.set_index("platform_code").loc["bigbasket", "sourdough_products"] == 3
    assert len(con.sql(q.catalogue_bakery(True)).df()) == 3
    assert len(con.sql(q.catalogue_bakery(False)).df()) > 100


def test_real_blinkit_has_no_sales_but_has_prices(con):
    f = CASES["blinkit_real"]
    k = con.sql(q.kpis(f)).df().iloc[0]
    assert k.units != k.units or k.units is None  # NaN/None: unknown, not zero
    assert k.avg_price > 0 and 0 < k.availability_pct <= 100
