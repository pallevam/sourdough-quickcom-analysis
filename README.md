# Quick-commerce sourdough analysis

A DuckDB warehouse for product data scraped from Blinkit, Swiggy Instamart and Zepto
(BB Now and Flipkart Minutes are pre-registered too). You can slice it by **platform ×
product category × geography (pincode → city → metro cluster → state → region / tier)
× time**, and it estimates sales, which platforms do not publish. Sourdough bread is the
first category explored.

```bash
pip install -e '.[dev]'          # or: PYTHONPATH=src python3 -m qcom ...
python -m qcom build             # schema + Kaggle zips + synthetic panel  (~7 s)
python -m qcom report            # sourdough deep-dive queries
python -m qcom report --only market_growth whitespace --export outputs/
python -m pytest -q
```

Then explore ad hoc in the DuckDB UI (queries to start from: `sql/queries/ui_exploration.sql`):

```bash
duckdb -cmd "ATTACH 'data/qcom.duckdb' AS qcom (READ_ONLY); USE qcom;" -ui   # http://localhost:4213
```

This keeps DuckDB's own session writable (the UI needs an in-memory scratch database) while the
warehouse file stays read-only, so the dashboard can run alongside. Don't use `duckdb -readonly … -ui`:
every query then fails with `Catalog "localmemdb" does not exist`. In each notebook cell, set the
database dropdown (top right) to `qcom`, or write fully qualified names like `qcom.mart.sales_daily`.

## What's in the data you have (read this first)

| File | What it actually is | How it is used |
|---|---|---|
| `zepto.zip` | 765 Zepto SKUs from a single snapshot. No pincode, no timestamp, no brand column. **0 bread / sourdough SKUs.** | Loaded as a catalogue snapshot at location 0 ("national"). Useful for taxonomy and pricing, but not for trends. |
| `swiggy.zip` (= `swiggy_data_coffee.zip`, byte-identical) | 140k Swiggy **food-delivery restaurants**, not Instamart, across 581 cities | `core.restaurant_listing` → `mart.market_affinity` (how much premium / western food culture each city has) |
| `zomato.zip` | 45k Zomato restaurants, 98 cities | same as above |
| `blinkit.zip` ([Kaggle](https://www.kaggle.com/datasets/tomtillo/blinkit-grocery-list-price-city-date), CC0) | Blinkit home-page products: about 2,600 products × 5 cities × 11 days (17 Jan – 1 Feb 2024). Price, MRP, pack size, out-of-stock flag. No pincode, stock count, rating or category. **0 sourdough.** | Real price / discount / availability data, one city-wide location per city. Sales estimates are NULL (no signal). Dashboard: *Dataset → Real: Blinkit*. Kolkata never shows a stock-out, which looks like a gap in the source. |
| `bigbasket.zip` ([Kaggle](https://www.kaggle.com/datasets/surajjha101/bigbasket-entire-product-list-28k-datapoints), CC BY-NC-SA 4.0) | BigBasket catalogue, about 27,500 products, one date (Jun 2022), real brands and categories, pack sizes mostly missing. **3 real sourdough products** (The Baker's Dozen). | National catalogue snapshot (platform `bigbasket`); seeds the brand list used to guess Blinkit brands. Dashboard: *Real catalogues* tab. |
| `zomato_data_coffee.zip`, `coffee_data_kaggle.zip` | Restaurant reviews; a single cafe's POS log | Not loaded (not relevant to quick-commerce) |

Products are only matched across platforms when the pack size is known. Two listings on one
platform are never merged, since titles often omit the variant or size.

Data credits: Blinkit data by tomtillo on Kaggle (CC0 1.0). BigBasket data by surajjha101 on
Kaggle, used under [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/):
non-commercial use, with attribution, and derivatives shared under the same licence.

So **none of the real data can produce a sourdough sales trend yet**. That needs
repeated, location-pinned scrapes (see *Collecting data* below). Until then,
`python -m qcom synth` loads a **synthetic** panel so the schema, estimators and
queries can be built and validated end to end:

- the brands are fictional
- every row has `is_synthetic = TRUE`
- query outputs carry a `synthetic` column

Treat any number from it as a demo of the method, never as a market fact.

## Schema

```
ref.platform ─┐                 ref.city ◄── ref.city_alias
ref.category ─┤                    ▲
ref.brand ────┤                ref.location (pincode = dark-store proxy)
              │                    ▲
core.product ◄── core.listing ◄── core.listing_snapshot ──► core.scrape_run (lineage)
 (1 per real     (1 per platform    (listing × location × scraped_at)
  product)        SKU)            core.search_result (rank × term × location × time)
                                  core.restaurant_listing (food-delivery proxy)
                                  core.synthetic_truth_daily (backtest only)
```

| Layer | Table | Grain / purpose |
|---|---|---|
| ref | `platform` | Blinkit, Instamart, Zepto, BB Now, Flipkart Minutes, Swiggy/Zomato food |
| ref | `city`, `city_alias` | city → state, region, tier, metro cluster (Gurgaon + Noida + Delhi = *Delhi NCR*); aliases fix raw spellings (`Noida-1`, `NCR`, `Central-Goa` …) |
| ref | `location` | pincode + locality: the vantage point of a scrape. Prices, stock and assortment differ by dark store. |
| ref | `category`, `platform_category_map` | 3-level canonical taxonomy (`Bakery > Bread > Sourdough`) plus each platform's raw labels mapped onto it |
| ref | `brand`, `model_param` | brands; sales-estimation assumptions (editable) |
| core | `product` | one row per real product (brand + variant + pack). `is_sourdough`, `pack_grams`, JSON `attributes` (flour, sliced, format, inclusions) |
| core | `listing` | one row per platform SKU, linked to a product by exact key or cross-platform fuzzy match (`match_method`, `match_score`) |
| core | `listing_snapshot` | **main fact**: price, MRP, in-stock flag, inventory, ETA, rating, rating count, sponsored flag |
| core | `search_result` | search-page rank: share of shelf, sponsored slots |
| mart | `listing_day` | daily sales estimate per SKU × location (see below) |
| mart | `sales_daily` | wide, BI-ready, every dimension pre-joined. **Start here for ad-hoc work.** |
| mart | `sales_weekly_geo` | platform × region/market/city/tier × category × week |
| mart | `share_of_search`, `market_affinity`, `estimator_backtest` | discoverability; city food-culture proxy; estimator accuracy |

## Dashboard

```bash
pip install -e '.[dashboard]'
streamlit run dashboard/app.py          # http://localhost:8501
```

Sidebar: a **Dataset** switch (synthetic sourdough panel, or real Blinkit Jan 2024), then
date range, category scope, metric (sales estimates for synthetic; price, discount, in-stock
share and products listed for both), time grain, platform, region, tier, market, sub-category,
brand and flour. Charts that need sales estimates are hidden for the real Blinkit data.

There are seven tabs:
- Overview
- Geography (including a city map)
- Brands & price
- Availability
- Search
- Real catalogues (BigBasket, Blinkit, Zepto bakery and sourdough listings)
- Restaurant data
- SQL playground

Under every chart, **"SQL for DuckDB UI"** shows the exact query that ran, with the
filter values inlined. Copy it into `duckdb -ui` to keep exploring. The query builders
live in `dashboard/queries.py`, and `tests/test_dashboard_queries.py` runs each of them
across several filter combinations.

The dashboard opens `data/qcom.duckdb` **read-only**, so it can run alongside the DuckDB
UI. `python -m qcom build/load/synth` needs a write lock, so stop both before reloading data.

### Ask tab (chat)

The first tab answers plain-English questions. Claude writes DuckDB SQL, runs it through
`run_sql` in `dashboard/assistant.py`, and replies with the answer, a chart when the result has
an obvious shape, and every query it ran ("How I got this").

- **Key:** set `ANTHROPIC_API_KEY` (env var or `.streamlit/secrets.toml`; template in
  `.streamlit/secrets.toml.example`). Without it, the tab shows setup instructions.
- **Safety:** the dashboard connection is read-only, with external access disabled and the config
  locked, so SQL from the chat or the playground can't write, read local files or reach the network.
  The tool accepts one SELECT/PIVOT/SUMMARIZE at a time and stops queries after 15 s.
- **Spend:** the default model is Claude Haiku 5.5 (about 0.2¢ per question by estimate). Change it with
  `CHAT_MODEL`/`CHAT_EFFORT`; `CHAT_MAX_QUESTIONS` caps questions per browser session (default 25).
  The token count for the session is shown under the chat. Also set a monthly spend limit in the
  Anthropic Console.
- **Synthetic data:** the assistant is instructed to say when an answer rests on the synthetic panel.

## Deploying (Streamlit Community Cloud)

Free hosting from a GitHub repo. The repo stays small (about 4 MB): the database isn't committed,
and on first start the app runs `python -m qcom build` itself (about 10 s).

1. Push this folder to a **private** GitHub repo. `.gitignore` already excludes the database,
   secrets, the venv and the unused zips. Check the Kaggle datasets' licences before making it public.
2. On share.streamlit.io, sign in with GitHub. Then: **Create app** → pick the repo →
   main file `dashboard/app.py` → **Advanced settings** → Python 3.12.
3. Paste your secrets (same format as `.streamlit/secrets.toml.example`): `ANTHROPIC_API_KEY`,
   plus `APP_PASSWORD` to require a shared password.
4. Deploy. In the app's **Share** settings, keep it private and invite viewers by email, or share the
   URL together with the password.

Data refreshes: commit new landing files or change the build, then reboot the app from its menu;
the database is rebuilt on start. Free apps sleep after a period without visitors and wake on the
next visit.

## How sales are estimated

Platforms don't publish units, so `mart.listing_day` infers them from signals a scraper can see:

1. **Inventory depletion.** Sum the drops in `inventory_qty` between consecutive
   snapshots, and ignore rises (restocks). Some platform API responses expose stock
   or a max-cart quantity. This misses sales between a restock and the next scrape:
   the backtest shows it runs about **−8%** when there are 4 scrapes a day.
2. **Rating velocity**, used when there is no inventory signal:
   new ratings × `orders_per_rating` × `units_per_order`. It is only as good as those
   two parameters. The backtest shows **+11%**, because `units_per_order` = 1.1 while
   the simulator used 1.0. Calibrate it on a platform where both signals exist.
3. **Demand vs. sales.** `est_units_demand` divides by the in-stock share to estimate
   unconstrained demand, which gives lost sales during stock-outs (`availability_gaps`).

`python -m qcom report --only estimator_backtest` scores both methods against the
synthetic ground truth. Re-run it whenever you change scrape frequency or parameters.

## The sourdough queries (`sql/queries/sourdough.sql`)

| Query | Question it answers |
|---|---|
| `platform_overview` | Sourdough units, GMV, share of bread, ₹/100 g premium, and availability by platform |
| `region_trend_monthly` | Monthly GMV and MoM growth by region (full months only) |
| `market_growth` | Which markets are growing fastest (first 8 vs last 8 *full* weeks), and sourdough's share of bread |
| `whitespace` | **Real** restaurant-culture affinity vs. sourdough penetration: where to expand next |
| `brand_share_by_region` | Regional brand strength |
| `price_ladder` | ₹/100 g by product × platform, and discount depth |
| `availability_gaps` | Where stock-outs lose the most sales |
| `weekday_profile` | Weekend lift for sourdough vs. everyday bread |
| `flour_mix` | White / whole wheat / multigrain / rye mix by region |
| `share_of_search` | Who owns the "sourdough" results page, and how much of it is paid |
| `real_*`, `dq_*` | Real-data views and data-quality checks (orphans, unmapped cities, match methods) |

## Collecting data (the scrape contract)

Any scraper writes parquet/CSV/JSONL with these columns and runs
`python -m qcom load --snapshots f.parquet --search s.parquet`. The loader handles
city resolution, brand and product matching, idempotency (it skips files whose
SHA-256 it has already seen) and lineage. Full spec: `src/qcom/ingest/snapshots.py`.

- **snapshot:** `platform, platform_sku, scraped_at, product_name, selling_price` +
  optional `pincode, locality, city, brand, platform_category, pack_size, mrp, in_stock,
  inventory_qty, max_cart_qty, eta_minutes, rating, rating_count, is_sponsored, url`
- **search:** `platform, platform_sku, scraped_at, search_term, rank_position` + optional
  `pincode, locality, city, is_sponsored`

Recommended panel for sourdough:
- **Locations:** 2–3 pincodes in each of ~15 cities, across tiers.
- **Product scrapes:** about 4 per day, with the first just after stores open.
- **Search scrape:** daily, for `sourdough`, `bread`, `artisan bread` and `focaccia`.

Check each platform's Terms of Service and robots.txt before you scrape. Keep request
rates low, and prefer a licensed data vendor for anything commercial.

## Suggested additions

- **Collection:** Playwright (handles location pop-ups, and you can read the XHR/JSON
  responses rather than the HTML); scheduled with cron, Prefect or GitHub Actions; raw
  responses kept as parquet partitioned by `platform/date` before loading.
- **Matching at scale:** `rapidfuzz` (faster, token-set scoring) and a manual review
  queue for low scores. Sentence-embedding matching once there are thousands of SKUs.
- **Transformations:** `dbt-duckdb` once the marts grow, to get tests, docs and
  incremental `listing_day`.
- **Data quality:** `pandera` or Great Expectations checks on landing files.
- **Geography:** the DuckDB `spatial` extension, `geopandas` and India district/pincode
  shapefiles for maps. Census / household-income data per district gives a sounder
  affinity denominator than restaurant listings.
- **Demand context:** Google Trends (`pytrends`) for "sourdough" by state is a free,
  independent check on regional interest.
- **Analysis and sharing:** Polars for heavy notebook work; Streamlit or Evidence.dev
  for a dashboard over `mart.sales_daily`.
- **Calibration:** brand-disclosed numbers (D2C/Shark-Tank-style interviews, investor
  decks), and Redseer or other industry reports for total quick-commerce GMV by city.
  Use them to sanity-check the scale of the estimates.
