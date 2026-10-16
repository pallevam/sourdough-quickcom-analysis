"""Loader for the quick-commerce scrape contract.

Any scraper (yours, a vendor's, the synthetic generator) writes files with these
columns and this loader takes care of geography, brand, product matching and
lineage. Accepted formats: .parquet, .csv, .json/.jsonl.

SNAPSHOT file (one row per SKU seen at a location at a time)
  required: platform, platform_sku, scraped_at, product_name, selling_price
  optional: pincode, locality, city, brand, brand_type, platform_category, pack_size,
            mrp, in_stock, inventory_qty, max_cart_qty, eta_minutes, rating,
            rating_count, is_sponsored, url, is_synthetic

SEARCH file (one row per result slot)
  required: platform, platform_sku, scraped_at, search_term, rank_position
  optional: pincode, locality, city, is_sponsored, is_synthetic
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from qcom import db
from qcom.normalize import brand_key, name_tokens, parse_pack_size, product_key, similarity
from qcom.taxonomy import bread_attributes, classify, is_sourdough

SNAPSHOT_REQUIRED = ["platform", "platform_sku", "scraped_at", "product_name", "selling_price"]
SNAPSHOT_OPTIONAL = ["pincode", "locality", "city", "brand", "brand_type", "platform_category",
                     "pack_size", "mrp", "in_stock", "inventory_qty", "max_cart_qty", "eta_minutes",
                     "rating", "rating_count", "is_sponsored", "url", "is_synthetic"]
SEARCH_REQUIRED = ["platform", "platform_sku", "scraped_at", "search_term", "rank_position"]
SEARCH_OPTIONAL = ["pincode", "locality", "city", "is_sponsored", "is_synthetic"]
FUZZY_THRESHOLD = 0.88


def read_file(con, path: Path) -> pd.DataFrame:
    reader = {".parquet": "read_parquet", ".csv": "read_csv_auto",
              ".json": "read_json_auto", ".jsonl": "read_json_auto"}[path.suffix.lower()]
    return con.sql(f"SELECT * FROM {reader}(?)", params=[str(path)]).df()


def _conform(df: pd.DataFrame, required: list[str], optional: list[str]) -> pd.DataFrame:
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"file is missing required columns: {missing}")
    df = df.copy()
    for c in optional:
        if c not in df.columns:
            df[c] = None
    df["scraped_at"] = pd.to_datetime(df["scraped_at"])
    df["platform"] = df["platform"].str.lower().str.strip()
    # '' instead of NULL so (pincode, locality) is a usable unique key
    df["pincode"] = df["pincode"].fillna("").astype(str).str.strip()
    df["locality"] = df["locality"].fillna("").astype(str).str.strip()
    df["is_synthetic"] = df["is_synthetic"].map(lambda v: bool(v) if pd.notna(v) else False)
    return df


def _location_ids(con, df: pd.DataFrame) -> pd.DataFrame:
    locs = df[["pincode", "locality", "city"]].drop_duplicates(["pincode", "locality"])
    city_ids = db.resolve_cities(con, locs["city"].dropna())
    locs = locs.assign(city_id=locs["city"].map(city_ids))
    known = locs[(locs.pincode != "") | (locs.locality != "")]
    con.register("_locs", known)
    con.execute("""
        INSERT INTO ref.location (pincode, locality, city_id)
        SELECT pincode, locality, city_id FROM _locs
        ON CONFLICT (pincode, locality) DO NOTHING
    """)
    ids = con.execute("""
        SELECT l.pincode, l.locality, r.location_id
        FROM _locs l JOIN ref.location r USING (pincode, locality)
    """).df()
    con.unregister("_locs")
    out = df[["pincode", "locality"]].merge(ids, on=["pincode", "locality"], how="left")
    return out["location_id"].fillna(0).astype(int).values


def _brand_id(con, name: str, brand_type: str | None, synthetic: bool) -> int:
    key = brand_key(name)
    con.execute("""
        INSERT INTO ref.brand (brand_name, brand_key, brand_type, is_synthetic)
        VALUES (?, ?, coalesce(?, 'unknown'), ?) ON CONFLICT (brand_key) DO NOTHING
    """, [name, key, brand_type, synthetic])
    return con.execute("SELECT brand_id FROM ref.brand WHERE brand_key = ?", [key]).fetchone()[0]


def _match_or_create_product(con, platform_id: int, row) -> tuple[int, str, float]:
    brand = row.brand or "Unknown"
    grams, pieces = parse_pack_size(row.pack_size) if row.pack_size else parse_pack_size(row.product_name)
    key = product_key(brand, row.product_name, grams)

    hit = con.execute("SELECT product_id FROM core.product WHERE product_key = ?", [key]).fetchone()
    if hit:
        return hit[0], "exact_key", 1.0

    # Fuzzy: same brand + same pack size, closest token string. Only across platforms:
    # two SKUs on one platform are different products by definition, and character
    # similarity can't tell variants apart ('5 Blue Pens' vs '5 Black Pens' = 0.91).
    bkey, tokens = brand_key(brand), " ".join(name_tokens(row.product_name, brand))
    gkey = "" if grams is None else str(int(round(grams)))
    cands = con.execute("""
        SELECT product_id, split_part(product_key, '|', 2) FROM core.product p
        WHERE split_part(product_key, '|', 1) = ? AND split_part(product_key, '|', 3) = ?
          AND NOT EXISTS (SELECT 1 FROM core.listing l
                          WHERE l.product_id = p.product_id AND l.platform_id = ?)
    """, [bkey, gkey, platform_id]).fetchall()
    best = max(((pid, similarity(tokens, t)) for pid, t in cands), key=lambda x: x[1], default=None)
    if best and best[1] >= FUZZY_THRESHOLD:
        return best[0], "fuzzy", best[1]

    category_id = classify(row.product_name)
    if category_id is None and row.platform_category:
        r = con.execute("""SELECT category_id FROM ref.platform_category_map
                           WHERE platform_id = ? AND platform_category = lower(?)""",
                        [platform_id, row.platform_category]).fetchone()
        category_id = r[0] if r else None
    sourdough = is_sourdough(row.product_name)
    attrs = bread_attributes(row.product_name) if sourdough or (category_id or 0) // 100 == 1 else {}
    pid = con.execute("""
        INSERT INTO core.product (product_key, canonical_name, brand_id, category_id,
                                  pack_grams, pack_units, is_sourdough, attributes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING product_id
    """, [key, row.product_name, _brand_id(con, brand, row.brand_type, bool(row.is_synthetic)),
          category_id or 999, grams, pieces, sourdough, json.dumps(attrs)]).fetchone()[0]
    return pid, "new", 0.0


def _listing_ids(con, df: pd.DataFrame) -> pd.Series:
    """Upsert core.listing for every (platform, sku) in df; return listing_id per row."""
    latest = (df.sort_values("scraped_at")
                .groupby(["platform", "platform_sku"], as_index=False)
                .agg(first_seen=("scraped_at", "min"), last_seen=("scraped_at", "max"),
                     **{c: (c, "last") for c in ["product_name", "brand", "brand_type",
                        "platform_category", "pack_size", "url", "is_synthetic"]}))
    ids = {}
    for row in latest.itertuples(index=False):
        pfid = db.platform_id(con, row.platform)
        existing = con.execute("SELECT listing_id FROM core.listing WHERE platform_id = ? AND platform_sku = ?",
                               [pfid, str(row.platform_sku)]).fetchone()
        if existing:
            con.execute("""UPDATE core.listing
                           SET first_seen_at = least(first_seen_at, ?), last_seen_at = greatest(last_seen_at, ?)
                           WHERE listing_id = ?""", [row.first_seen, row.last_seen, existing[0]])
            ids[(row.platform, row.platform_sku)] = existing[0]
            continue
        product_id, method, score = _match_or_create_product(con, pfid, row)
        ids[(row.platform, row.platform_sku)] = con.execute("""
            INSERT INTO core.listing (platform_id, platform_sku, product_id, raw_name, raw_brand,
                raw_category, pack_size_raw, url, match_method, match_score, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING listing_id
        """, [pfid, str(row.platform_sku), product_id, row.product_name, row.brand,
              row.platform_category, row.pack_size, row.url, method, score,
              row.first_seen, row.last_seen]).fetchone()[0]
    return pd.Series([ids[k] for k in zip(df["platform"], df["platform_sku"])], index=df.index)


def load_snapshots(con, path: Path, source_type: str = "own_scrape") -> int:
    run_id = db.start_run(con, source_type, path)
    if run_id is None:
        print(f"skip {path.name}: already loaded")
        return 0
    n = load_snapshot_frame(con, read_file(con, path), run_id)
    print(f"loaded {n:,} snapshot rows from {path.name}")
    return n


def load_snapshot_frame(con, df: pd.DataFrame, run_id: int) -> int:
    """Load an in-memory frame that follows the snapshot contract under an existing run."""
    df = _conform(df, SNAPSHOT_REQUIRED, SNAPSHOT_OPTIONAL)
    df["platform_sku"] = df["platform_sku"].astype(str)
    df["location_id"] = _location_ids(con, df)
    df["listing_id"] = _listing_ids(con, df)
    df["run_id"] = run_id
    con.register("_snap", df)
    n = con.execute("""
        INSERT OR IGNORE INTO core.listing_snapshot
        SELECT run_id, listing_id, location_id, scraped_at, selling_price, mrp, in_stock,
               inventory_qty, max_cart_qty, eta_minutes, rating, rating_count,
               is_sponsored, is_synthetic
        FROM _snap
    """).fetchone()[0]
    con.unregister("_snap")
    db.finish_run(con, run_id, n)
    return n


def load_search(con, path: Path, source_type: str = "own_scrape") -> int:
    run_id = db.start_run(con, source_type, path)
    if run_id is None:
        print(f"skip {path.name}: already loaded")
        return 0
    df = _conform(read_file(con, path), SEARCH_REQUIRED, SEARCH_OPTIONAL)
    df["platform_sku"] = df["platform_sku"].astype(str)
    df["location_id"] = _location_ids(con, df)
    con.register("_srch", df)
    n = con.execute("""
        INSERT OR IGNORE INTO core.search_result
        SELECT ?, p.platform_id, s.location_id, s.scraped_at, s.search_term, s.rank_position,
               l.listing_id, s.is_sponsored, s.is_synthetic
        FROM _srch s
        JOIN ref.platform p ON p.platform_code = s.platform
        JOIN core.listing l ON l.platform_id = p.platform_id AND l.platform_sku = s.platform_sku
    """, [run_id]).fetchone()[0]
    con.unregister("_srch")
    dropped = len(df) - n
    db.finish_run(con, run_id, n)
    print(f"loaded {n:,} search rows from {path.name}"
          + (f" ({dropped:,} dropped: unknown SKU or duplicate slot)" if dropped else ""))
    return n
