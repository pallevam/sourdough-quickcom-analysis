# India quick-commerce explorer

Explore historical source listings, brands, categories, pack prices and explicit stock observations with DuckDB and Streamlit. Counts describe the loaded catalogue samples, not sales, demand or market share. Source files can contain errors.

## Run

```bash
pip install -e '.[dashboard,dev]'
python -m qcom build
streamlit run dashboard/app.py
python -m pytest -q
```

The build loads the bundled Zepto, BigBasket, Blinkit, Swiggy and Zomato archives into `data/qcom-observations-v2.duckdb`. The dashboard builds this database on first start if missing. `QCOM_DB` overrides the dashboard path; use `python -m qcom --db PATH build` for the matching CLI path. Older warehouse schemas are rejected; rebuild from source into a new file.

## Data and limitations

- Zepto and BigBasket are historical catalogues. Their record dates come from ZIP metadata, not verified observation timestamps. Location is unknown.
- Blinkit contains historical city-level price records. Only explicit stock labels count toward availability; missing stock and MRP remain unknown.
- Brands and categories in dashboard reports come from source fields. Missing labels remain Unknown. Labels differ between sources.
- Prices are per pack. Pack sizes vary; reports do not infer unit-price conversions.
- Swiggy and Zomato restaurant records appear separately in source coverage. They are not Instamart product records or evidence of product demand. BigBasket is e-grocery.
- Trend reports exclude archive-dated catalogues. Daily averages can change with assortment mix.

Source provenance is recorded in `core.scrape_run`. Product observations live in `core.listing_snapshot`, with original labels in `core.listing`. `mart.v_listing` and `mart.v_location` provide joins; internal product matching and taxonomy remain provisional and are not used as dashboard source labels.

## Dashboard

Assortment, Brands and prices, Trends and availability, and Research shortlist share sidebar filters. Data coverage describes the whole warehouse. Tables include CSV and SQL exports. SQL playground allows read-only queries with external access disabled, a time limit and a row cap.

Ask includes a source/location/date coverage table and a direct report browser that works without an API key. Use Product name contains to find terms such as bread; location Unknown does not establish city-level availability.

Consumption and sales wording is checked before model routing; those questions get an explicit missing-measure explanation. Empty reports show the requested scope and identify unavailable location/platform labels without silently broadening filters.

Ask uses a model only to select a reviewed report and filters. The app validates that selection, executes its own SQL, and displays database tables, selected scope, source coverage and fixed explanatory text. It never displays model-written factual answers or executes model-written SQL. Unsupported questions return an insufficient-evidence message. Report selection can still misinterpret a question: check the displayed scope and query. Each question is independent.

Set `ANTHROPIC_API_KEY` in environment or Streamlit secrets for Ask. Optional settings: `CHAT_MODEL`, `CHAT_MAX_QUESTIONS` (default 25), and `APP_PASSWORD`. No API request is made by tests.

## Load observations

```bash
python -m qcom load --snapshots observations.csv --search search.parquet
python -m qcom report
```

Snapshot inputs require `platform, platform_sku, scraped_at, product_name, selling_price`. Optional fields and search contracts are documented in `src/qcom/ingest/snapshots.py`. Input columns are checked; files carry source hashes and load provenance. Stop database readers before loading new files.

## Deployment

The configured Streamlit entry point is `dashboard/app.py` on `main` of `pallevam/sourdough-quickcom-analysis`. Community Cloud updates after a push or merge to that branch. The database and secrets are not committed. CI builds from bundled source archives and runs tests. Local changes do not update the hosted dashboard until published.
