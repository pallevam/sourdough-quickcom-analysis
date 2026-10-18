# Repository review and category-wide dashboard pivot

Reviewed 18 October 2026. Evidence comes from the local source code, every ZIP's CSV schema and row counts, the existing DuckDB warehouse, seed tables, SQL reports, generated outputs, and tests. This is an audit of the supplied data, not external verification of dataset accuracy, collection rights, or current platform conditions.

## What the repository can support

A useful historical assortment and pricing research workbench. The schema, source-file hashes, separate snapshot/search facts, explicit synthetic flags, and read-only SQL assistant are good foundations. It cannot yet establish category sales, current market share, growth across platforms, or a defensible brand opportunity from real data.

### Observed inventory

| Source | Loaded listings / records | Observations | Time and geography | Suitable use |
|---|---:|---:|---|---|
| BigBasket | 27,554 product listings | 27,554 | One ZIP timestamp, 22 Jun 2022; location unknown | Historical e-grocery assortment, source categories, pack prices |
| Zepto | 765 product listings | 765 | One ZIP timestamp, 3 Aug 2025; location unknown | Small historical catalogue sample |
| Blinkit | 2,599 product listings | 115,004 | 11 observed dates between 17 Jan and 1 Feb 2024, five city-wide locations | Sampled price, discount and stock observations |
| Swiggy food delivery | 140,657 restaurant rows | — | 579 resolved city IDs | Restaurant context only; not Instamart |
| Zomato food delivery | 44,891 restaurant rows | — | 97 resolved city IDs | Restaurant context only |
| Synthetic bakery panel | 42 platform listings | 626,592 | Apr–Sep 2026, Blinkit/Instamart/Zepto | Pipeline and estimator demonstrations only |

The product explorer contains **30,918 real platform listings**. This is neither a count of unique cross-platform products nor a market-size estimate. BigBasket is registered as `e_grocery`, separate from BB Now. There are no real Instamart, BB Now or Flipkart Minutes product observations.

The raw BigBasket file has 27,555 rows; one has no product name and is dropped. Blinkit has 116,554 raw rows and 115,004 loaded rows after title/null and duplicate handling. Zepto has 765 raw and loaded rows. Load counts should become explicit reconciliation reports rather than only console messages.

Other archives: `swiggy_data_coffee.zip` duplicates the Swiggy input; `zomato_data_coffee.zip` contains 105 restaurant metadata rows and 10,000 reviews; `coffee_data_kaggle.zip` contains two cafe POS files with 3,636 and 262 rows. The latter files are not quick-commerce assortment and remain outside the product dashboard. Existing `outputs/` CSVs are legacy sourdough reports, not generic market results.

## Implemented in this pivot

- Real observations and all available categories are the default; synthetic bakery observations are an explicit separate mode.
- Generic platform, category, brand, city, text-search and optional record-date filters.
- Assortment, brands/prices, observed trends/availability, research shortlist, source coverage, chat and SQL views. Only the selected view executes.
- Source category labels take precedence in the explorer. Existing inferred classifications remain visible as a separate field and a clearly identified fallback. This avoids presenting known misclassifications as verified categories, without rewriting historical product assignments.
- Pack-price distributions replace universal Rs/100 g comparisons. Unknown stock stays null. Listing counts do not double-count repeated snapshots.
- Archive timestamp provenance is visible; those catalogue dates are excluded from trend charts.
- CSV and exact SQL downloads; bounded five-minute query caching and manual refresh.
- Category-wide assistant guidance defaults to real observations and distinguishes assortment from demand. Playground queries now use the assistant's existing statement checks, timeout and result cap.
- Historical sourdough SQL, query builders, generator and warehouse remain available. The new dashboard no longer presents synthetic sales and restaurant affinity as its principal analysis.

## Prioritised findings still to address

### P1 — Category classification is not reliable across categories

**Evidence:** `src/qcom/taxonomy.py` applies product-name regexes before platform category mapping in `src/qcom/ingest/snapshots.py::_Matcher.match`. `lassi` matches inside “Classic”. Milk soap and body lotion are classified as dairy; cake moulds as cakes. `sql/02_seed.sql` maps cleaning/household and kitchen/garden/pets into Stationery, and baby care into Skincare. BigBasket's more useful `sub_category` and `type` fields are discarded by its loader.

**Measured:** 90.3% of Blinkit's 2,599 real listings are Uncategorised. Zero uncategorised rows in BigBasket does not mean accurate classification.

**Next:** preserve the complete source category hierarchy; build a versioned canonical taxonomy covering food, beauty, home, baby, pets and other departments; apply source-category constraints before name rules; store method/confidence and allow reviewed overrides. Add adversarial examples (Classic shaving foam, milk soap, peanut butter, cake moulds) and coverage metrics per source. Reclassification must be an explicit migration: reloading an unchanged hash will not update existing products.

### P1 — Pack parsing mixes incompatible quantities and can corrupt matching

**Evidence:** `src/qcom/normalize.py::_UNIT_TO_GRAMS` treats ml as g; `product_key` does not encode unit dimension and omits piece count. `_Matcher` uses the resulting mass-like value for fuzzy candidate selection. This affects both comparisons and cross-platform product identity. Freebies, range quantities, count-only packs and variant numbers need explicit handling.

**Measured:** parsed pack weight is missing for 97.3% of BigBasket, 43.8% of Blinkit and 15.9% of Zepto listings.

**Next:** store quantity, unit dimension (mass/volume/count), base unit, multipack count, parser confidence and raw text separately. Compare Rs/100 g, Rs/100 ml or Rs/item only within compatible segments. Include dimension and variant attributes in matching keys; review fuzzy merges. The dashboard mitigation is to show pack prices, not to claim the parser is fixed.

### P1 — Ingestion is not atomic or safely retryable after failure

**Evidence:** `db.start_run` inserts a hash before parsing/loading; loaders have no enclosing transaction. If parsing, casting or matching fails later, a run can remain with null row_count and partial dimension writes. Retrying the same file is skipped because the hash already exists.

**Next:** wrap each complete load in a transaction; track started/completed/failed runs separately; deduplicate against successful loads only. Add a failure-and-retry integration test. Record raw, accepted, rejected and duplicate counts with rejection reasons instead of silent `on_bad_lines='skip'` and `INSERT OR IGNORE` losses.

### P1 — Provenance and observation time need a first-class model

**Evidence:** Zepto/BigBasket use ZIP entry timestamps as `scraped_at`. Restaurant records have no observation timestamp. Source URLs, licences, dataset versions, fetch dates, category/brand inference and observation-time confidence are not structured fields. The new UI marks the two known archive-dated sources, but this should not depend on platform-specific application logic.

**Next:** add a source registry and source-specific adapters, explicit observation time versus archive/import time, geographic granularity and collection method. Preserve original files and provider metadata. Document and verify dataset permissions before choosing sources for a commercial workflow; the existing README records a non-commercial licence for BigBasket. No legal determination was made in this audit.

### P1 — Do not equate observed stock or simulated sales with opportunity

**Evidence:** real price datasets lack inventory counts and rating-count histories needed by the estimators. Synthetic category demand, regional preferences and growth are chosen inputs in `src/qcom/synthetic.py`. The restaurant affinity index was designed for premium bread, not all consumer goods. A stock-out may be a supply or data problem. Kolkata's Blinkit sample has no out-of-stock flags.

**Next:** retain observable metrics separately from hypotheses and calibrated estimates. Build repeated comparable panels, validate demand with independent evidence, and add unit economics and customer repeat-purchase evidence before scoring a brand concept. Avoid a composite “opportunity score” until its inputs and predictive usefulness can be validated.

### P2 — Geography can collapse distinct cities

**Evidence:** `_location_ids` deduplicates and joins on `(pincode, locality)` only. City-only inputs with blank pincode/locality become location 0; identical locality names in different cities can collide. Blinkit's loader works around this using a city-wide locality string.

**Next:** use location keys including city/country and granularity; distinguish unknown location, city-wide observation and a genuinely national catalogue. Test two cities sharing a locality name and city-only input.

### P2 — Matching and provenance can become dependent on load order

**Evidence:** `guess_brand` guesses a first word if no known prefix exists. Zepto loads before BigBasket in `cmd_load_kaggle`, so it cannot use BigBasket's brand dictionary on a fresh build. Existing listings only update first/last seen timestamps, not corrected category/brand metadata. `brand_is_synthetic` is set when a brand is first inserted, so it is not a reliable replacement for snapshot-level provenance. The legacy assistant enumerations still include brand-based provenance shortcuts.

**Next:** introduce brand aliases and inference confidence; make loader ordering deterministic; store provenance at observation/source level; provide an explicit rematch/reclassification workflow and reviewed overrides. Add reversed-load-order and real/synthetic collision tests.

### P2 — Estimation marts need explicit comparability and missingness rules

**Evidence:** `mart.v_snapshot` partitions deltas by listing/location, without source/synthetic boundaries or a maximum observation gap. `mart.listing_day` declares a rating signal from non-null counts, even without an observed predecessor. `mart.share_of_search` drops `is_synthetic`; many legacy report queries do not explicitly separate real and demo rows. Inventory depletion can include shrinkage and miss sales during restocks; availability uplift is an assumption, not measured lost sales.

**Next:** require valid paired observations, retain source/provenance through all marts, define maximum intervals and signal confidence, and calibrate by platform/category. Keep missing estimates null. Add tests for first readings, counter resets, gaps, mixed provenance and replenishment. Separate real and demo report entry points.

### P2 — Quality tests and reproducibility need broader coverage

**Evidence:** previous dashboard/assistant tests skip when the local database is absent and mainly check non-empty output. There were no loader, transaction, geography-collision or estimator edge-case tests. `requirements.txt` is pinned while project extras were broader; the Streamlit lower bound has now been aligned to the API version used by the dashboard.

**Next:** keep the new small in-memory semantic tests and UI integration checks; add clean-build CI, schema/version migrations, incremental-load fixtures, and source reconciliation. Record a reproducible dataset manifest. Product/brand deduplication and source updates should have regression fixtures.

### P3 — Deployment and analyst workflow

The app still builds a full database and demo panel on first start. The CLI report remains sourdough-specific. The simple password gate and per-session chat cap are not per-user identity or a global spending budget. Cached read-only connections require restart after replacing the DB. Chat provider behaviour was tested with fakes, not live API calls.

Next: explicit data-build jobs, atomic published database versions, a generic CLI report, persistent review/shortlist notes with evidence, and deployment-level identity/budget controls if this becomes a shared tool. Materialise daily facts only when profiling shows the need; the generic explorer already queries snapshots without running sales-estimation windows.

## Suggested next implementation order

1. Source registry, full raw taxonomy preservation, and classification repair with a migration.
2. Unit-aware pack parsing, brand aliases and reviewed product identity.
3. Atomic ingestion, failed-load recovery and reconciliation tests.
4. Comparable observation panels with source/date/location coverage thresholds.
5. An evidence-backed research workflow: segment → competition → price/availability signals → customer validation → unit economics.

## Verification

The changes are tested with in-memory fixture assertions for real/demo separation, source-category precedence, listing counts versus repeated observations, listing-weighted price summaries, known-stock denominators, literal escaping, date filters, archive-date exclusion and empty results. Streamlit AppTest exercises the main views, platform filtering, no-match searches and switching to synthetic mode. A fresh disposable warehouse build checks compatibility with the existing ingestion pipeline. The running app is inspected separately for rendering. No external model request or production data migration is needed for this pivot.

Result: **55 tests passed** against a fresh database built at `/tmp/qcom-review-fresh.duckdb` (6.27 seconds for the suite). The existing warehouse was not mutated. Browser inspection confirmed the real-data landing page and its 30,918-listing total at the already-running localhost server.
