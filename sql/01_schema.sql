-- =============================================================================
-- Quick-commerce product analytics schema (DuckDB)
--
--   ref.*   reference / dimension tables (platform, geography, taxonomy, brand)
--   core.*  facts at the grain they were observed (one row per scrape observation)
--   mart.*  analytical views (observed prices and stock) -- see 03_marts.sql
--
-- Foreign keys are documented in comments rather than declared: DuckDB's FK
-- enforcement blocks updates on referenced rows (e.g. listing.last_seen_at).
-- Orphans are caught by the `dq_orphans` query in queries/observations.sql instead.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS ref;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS mart;

CREATE SEQUENCE IF NOT EXISTS ref.seq_city     START 1000;  -- seeded cities use < 1000
CREATE SEQUENCE IF NOT EXISTS ref.seq_location START 1;     -- 0 = unknown / national
CREATE SEQUENCE IF NOT EXISTS ref.seq_brand    START 1;
CREATE SEQUENCE IF NOT EXISTS core.seq_product START 1;
CREATE SEQUENCE IF NOT EXISTS core.seq_listing START 1;
CREATE SEQUENCE IF NOT EXISTS core.seq_run     START 1;
CREATE SEQUENCE IF NOT EXISTS core.seq_restaurant START 1;

-- ---------------------------------------------------------------- reference --

CREATE TABLE IF NOT EXISTS ref.platform (
    platform_id     SMALLINT PRIMARY KEY,
    platform_code   VARCHAR NOT NULL UNIQUE,     -- 'blinkit', 'instamart', 'zepto', ...
    platform_name   VARCHAR NOT NULL,
    channel         VARCHAR NOT NULL CHECK (channel IN ('quick_commerce', 'e_grocery', 'food_delivery')),
    parent_company  VARCHAR
);

-- Geography hierarchy: location (pincode) -> city -> metro_cluster -> state -> region
CREATE TABLE IF NOT EXISTS ref.city (
    city_id         INTEGER PRIMARY KEY DEFAULT nextval('ref.seq_city'),
    city_name       VARCHAR NOT NULL UNIQUE,
    state           VARCHAR,
    region          VARCHAR CHECK (region IN ('North', 'South', 'East', 'West', 'Central', 'Northeast')),
    tier            SMALLINT CHECK (tier IN (1, 2, 3)),
    metro_cluster   VARCHAR,                     -- e.g. Gurgaon + Noida + Delhi -> 'Delhi NCR'
    lat             DOUBLE,
    lon             DOUBLE,
    is_seeded       BOOLEAN NOT NULL DEFAULT FALSE  -- FALSE = auto-created from unseen raw value
);

CREATE TABLE IF NOT EXISTS ref.city_alias (
    alias           VARCHAR PRIMARY KEY,         -- lower-cased raw spelling
    city_id         INTEGER NOT NULL,            -- -> ref.city
    note            VARCHAR
);

-- The vantage point a scrape was taken from. Quick-commerce assortment, price and
-- stock are per dark store; the pincode the app is pointed at is our proxy for it.
CREATE TABLE IF NOT EXISTS ref.location (
    location_id     INTEGER PRIMARY KEY DEFAULT nextval('ref.seq_location'),
    pincode         VARCHAR,
    locality        VARCHAR,
    city_id         INTEGER,                     -- -> ref.city (NULL for location 0)
    lat             DOUBLE,
    lon             DOUBLE,
    UNIQUE (pincode, locality)
);

-- Canonical, platform-independent taxonomy (3 levels).
CREATE TABLE IF NOT EXISTS ref.category (
    category_id     INTEGER PRIMARY KEY,
    l1              VARCHAR NOT NULL,
    l2              VARCHAR NOT NULL,
    l3              VARCHAR NOT NULL,
    category_path   VARCHAR NOT NULL UNIQUE      -- 'Bakery > Bread > Sourdough'
);

-- Each platform's own (messy) category label -> canonical category. Used as a
-- fallback when the product-name classifier in taxonomy.py has no opinion.
CREATE TABLE IF NOT EXISTS ref.platform_category_map (
    platform_id       SMALLINT NOT NULL,         -- -> ref.platform
    platform_category VARCHAR NOT NULL,
    category_id       INTEGER NOT NULL,          -- -> ref.category
    PRIMARY KEY (platform_id, platform_category)
);

CREATE TABLE IF NOT EXISTS ref.brand (
    brand_id        INTEGER PRIMARY KEY DEFAULT nextval('ref.seq_brand'),
    brand_name      VARCHAR NOT NULL,
    brand_key       VARCHAR NOT NULL UNIQUE,     -- normalised for matching
    brand_type      VARCHAR CHECK (brand_type IN ('national', 'regional', 'artisanal', 'private_label', 'unknown'))
);

-- --------------------------------------------------------------------- core --

-- One row per real-world product (brand + variant + pack size), across platforms.
CREATE TABLE IF NOT EXISTS core.product (
    product_id      INTEGER PRIMARY KEY DEFAULT nextval('core.seq_product'),
    product_key     VARCHAR NOT NULL UNIQUE,     -- brand_key|sorted name tokens|grams
    canonical_name  VARCHAR NOT NULL,
    brand_id        INTEGER,                     -- -> ref.brand
    category_id     INTEGER,                     -- -> ref.category
    pack_grams      DOUBLE,                      -- net weight (ml treated as g)
    pack_units      INTEGER,                     -- pieces, when stated
    is_sourdough    BOOLEAN NOT NULL DEFAULT FALSE,
    attributes      JSON                         -- {"flour": "whole wheat", "sliced": true, ...}
);

-- One row per platform SKU. Several listings (one per platform) map to one product.
CREATE TABLE IF NOT EXISTS core.listing (
    listing_id      INTEGER PRIMARY KEY DEFAULT nextval('core.seq_listing'),
    platform_id     SMALLINT NOT NULL,           -- -> ref.platform
    platform_sku    VARCHAR NOT NULL,            -- platform's product/variant id
    product_id      INTEGER,                     -- -> core.product
    raw_name        VARCHAR NOT NULL,
    raw_brand       VARCHAR,
    raw_category    VARCHAR,
    pack_size_raw   VARCHAR,
    url             VARCHAR,
    match_method    VARCHAR,                     -- 'exact_key' | 'fuzzy' | 'new'
    match_score     DOUBLE,
    first_seen_at   TIMESTAMP,
    last_seen_at    TIMESTAMP,
    UNIQUE (platform_id, platform_sku)
);

-- Lineage: every loaded file is a run. Re-loading the same file is a no-op.
CREATE TABLE IF NOT EXISTS core.scrape_run (
    run_id          INTEGER PRIMARY KEY DEFAULT nextval('core.seq_run'),
    source_type     VARCHAR NOT NULL CHECK (source_type IN ('kaggle', 'own_scrape', 'vendor')),
    source_file     VARCHAR NOT NULL,
    file_sha256     VARCHAR NOT NULL UNIQUE,
    loaded_at       TIMESTAMP NOT NULL DEFAULT current_timestamp,
    row_count       BIGINT,
    notes           VARCHAR
);

-- THE central fact: what a SKU looked like at a location at a moment.
-- Grain: listing x location x scraped_at.
CREATE TABLE IF NOT EXISTS core.listing_snapshot (
    run_id          INTEGER NOT NULL,            -- -> core.scrape_run
    listing_id      INTEGER NOT NULL,            -- -> core.listing
    location_id     INTEGER NOT NULL,            -- -> ref.location (0 = unknown)
    scraped_at      TIMESTAMP NOT NULL,
    selling_price   DECIMAL(10, 2),
    mrp             DECIMAL(10, 2),
    in_stock        BOOLEAN,
    inventory_qty   INTEGER,                     -- only when the platform payload exposes it
    max_cart_qty    INTEGER,
    eta_minutes     SMALLINT,
    rating          DECIMAL(3, 2),
    rating_count    INTEGER,                     -- cumulative source count
    is_sponsored    BOOLEAN,
    PRIMARY KEY (listing_id, location_id, scraped_at)
);

-- Search results page: drives share-of-shelf / discoverability metrics.
-- Grain: platform x location x scraped_at x search_term x rank.
CREATE TABLE IF NOT EXISTS core.search_result (
    run_id          INTEGER NOT NULL,
    platform_id     SMALLINT NOT NULL,
    location_id     INTEGER NOT NULL,
    scraped_at      TIMESTAMP NOT NULL,
    search_term     VARCHAR NOT NULL,
    rank_position   SMALLINT NOT NULL,           -- 1 = top
    listing_id      INTEGER NOT NULL,
    is_sponsored    BOOLEAN,
    PRIMARY KEY (platform_id, location_id, scraped_at, search_term, rank_position)
);

-- Food-delivery restaurant listings (Swiggy / Zomato). Not quick-commerce, but a
-- separate source context, never evidence of product demand.
CREATE TABLE IF NOT EXISTS core.restaurant_listing (
    restaurant_row_id  INTEGER PRIMARY KEY DEFAULT nextval('core.seq_restaurant'),
    run_id             INTEGER NOT NULL,
    platform_id        SMALLINT NOT NULL,
    city_id            INTEGER,
    area               VARCHAR,
    restaurant_name    VARCHAR,
    cuisines           VARCHAR[],
    rating             DECIMAL(3, 2),
    rating_count_floor INTEGER,                  -- '1K+ ratings' -> 1000
    price_amount       INTEGER,
    price_basis        VARCHAR,                  -- 'for one' | 'for two'
    price_for_two      INTEGER,                  -- normalised
    offer_count        INTEGER,
    is_pure_veg        BOOLEAN,
    is_bakery          BOOLEAN,
    is_cafe            BOOLEAN,
    mentions_sourdough BOOLEAN
);
