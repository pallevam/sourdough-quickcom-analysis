"""Metric semantics against a tiny warehouse; no downloaded database required."""
import sys
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from qcom import db
from qcom.ingest.snapshots import load_snapshot_frame

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'dashboard'))
import market_queries as q


@pytest.fixture
def con():
    c = duckdb.connect(':memory:')
    db.init(c)
    c.execute("INSERT INTO core.scrape_run (run_id,source_type,source_file,file_sha256) VALUES (1,'kaggle','fixture.zip','fixture')")
    rows = [
        dict(platform='bigbasket', platform_sku='soap', product_name='Milk Soap', brand="O'Brien",
             platform_category='beauty & hygiene', scraped_at='2022-06-22', selling_price=80, mrp=100),
        dict(platform='blinkit', platform_sku='a', product_name='Coffee', brand='A',
             scraped_at='2024-01-17', selling_price=100, mrp=100, in_stock=True),
        dict(platform='blinkit', platform_sku='a', product_name='Coffee', brand='A',
             scraped_at='2024-01-18', selling_price=200, mrp=200, in_stock=False),
        dict(platform='blinkit', platform_sku='b', product_name='Tea', brand='B',
             scraped_at='2024-01-17', selling_price=300, mrp=300),
    ]
    load_snapshot_frame(c, pd.DataFrame(rows), 1)
    yield c
    c.close()


def test_real_default_and_source_category_precedence(con):
    d = con.sql(q.products(q.MarketFilters())).df()
    assert len(d) == 3
    soap = d[d['product'] == 'Milk Soap'].iloc[0]
    assert soap.category == 'beauty & hygiene'
    assert soap.category_basis == 'Source category'
    assert pd.isna(soap.in_stock_pct)
    assert 'Archive timestamp' in soap.date_basis


def test_listing_weighted_prices_and_known_stock_denominator(con):
    d = con.sql(q.summary(q.MarketFilters(platforms=['blinkit']), 'platform_code')).df().iloc[0]
    assert d.listings == 2  # three observations, two listings
    assert d.median_pack_price == 225  # median of listing means 150 and 300
    assert d.stock_observations == 2 and d.in_stock_pct == 50


def test_filters_quotes_search_and_dates(con):
    f = q.MarketFilters(brands=["O'Brien"], search='SOAP')
    assert len(con.sql(q.products(f)).df()) == 1
    f.search = "' OR TRUE --"
    assert con.sql(q.products(f)).df().empty
    f = q.MarketFilters(date_from=date(2024,1,18), date_to=date(2024,1,18))
    assert con.sql(q.products(f)).df().iloc[0].avg_pack_price == 200


def test_trends_exclude_archive_dates_and_keep_stock_unknown(con):
    d = con.sql(q.trend(q.MarketFilters())).df()
    assert set(d.platform_code) == {'blinkit'} and len(d) == 2
    d = con.sql(q.availability(q.MarketFilters(platforms=['bigbasket']))).df()
    assert d.stock_observations.iloc[0] == 0 and pd.isna(d.out_of_stock_pct.iloc[0])


def test_empty_slice_and_allowlisted_identifiers(con):
    f = q.MarketFilters(search='no matching product')
    assert con.sql(q.summary(f)).df().empty
    assert con.sql(q.products(f)).df().empty
    with pytest.raises(ValueError):
        q.summary(f, 'invalid')
    with pytest.raises(ValueError):
        q.options(f, 'invalid')


def test_removed_input_fields_are_rejected_before_loading(con):
    rows = pd.DataFrame([dict(platform='blinkit', platform_sku='rejected',
        product_name='Rejected', scraped_at='2024-01-17', selling_price=1, is_synthetic=True)])
    before = con.sql('SELECT count(*) FROM core.listing_snapshot').fetchone()
    with pytest.raises(ValueError, match='Unsupported input columns'):
        load_snapshot_frame(con, rows, 1)
    assert con.sql('SELECT count(*) FROM core.listing_snapshot').fetchone() == before


def test_old_warehouse_is_rejected():
    with duckdb.connect(':memory:') as old:
        old.execute('CREATE SCHEMA core; CREATE TABLE core.listing_snapshot (is_synthetic BOOLEAN)')
        with pytest.raises(ValueError, match='Unsupported warehouse version'):
            db.validate(old)


def test_source_type_contract(con):
    with pytest.raises(duckdb.ConstraintException):
        con.execute("INSERT INTO core.scrape_run (source_type,source_file,file_sha256) VALUES ('generated','x','x')")
    db.validate(con)
