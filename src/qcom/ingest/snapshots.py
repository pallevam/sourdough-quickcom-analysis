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
FUZZY_THRESHOLD = 0.90


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


def _text(v) -> str | None:
    """pandas gives NaN (a float) for missing strings; treat it as None."""
    return v.strip() or None if isinstance(v, str) else None


def _next_ids(con, seq: str, n: int) -> list[int]:
    return [r[0] for r in con.execute(f"SELECT nextval('{seq}') FROM range(?)", [n]).fetchall()] if n else []


class _Matcher:
    """Resolves new listings to products in memory, then writes brands/products in bulk.

    Matching rules: only when the pack size is known; exact product_key first; else fuzzy on
    (brand, pack size) but only to a product with no listing on the same platform (two SKUs on one platform are different
    products, and character similarity can't tell variants apart: '5 Blue Pens' vs
    '5 Black Pens' = 0.91); else a new product.
    """

    def __init__(self, con):
        self.con = con
        self.by_key = dict(con.execute("SELECT product_key, product_id FROM core.product").fetchall())
        self.brands = dict(con.execute("SELECT brand_key, brand_id FROM ref.brand").fetchall())
        self.catmap = {(pf, cat): cid for pf, cat, cid in con.execute(
            "SELECT platform_id, platform_category, category_id FROM ref.platform_category_map").fetchall()}
        self.platforms: dict[int, set] = {}
        for pid, pf in con.execute("SELECT product_id, platform_id FROM core.listing").fetchall():
            self.platforms.setdefault(pid, set()).add(pf)
        self.cands: dict[tuple, list] = {}
        for key, pid in self.by_key.items():
            b, toks, g = key.split("|")[:3]
            self.cands.setdefault((b, g), []).append((pid, toks))
        self.new_brands, self.new_products = [], []

    def _brand_id(self, name: str, brand_type, synthetic: bool) -> int:
        key = brand_key(name)
        if key not in self.brands:
            self.brands[key] = _next_ids(self.con, "ref.seq_brand", 1)[0]
            self.new_brands.append((self.brands[key], name, key, brand_type or "unknown", synthetic))
        return self.brands[key]

    def match(self, platform_id: int, row) -> tuple[int, str, float]:
        name, brand = row.product_name, _text(row.brand) or "Unknown"
        pack = _text(row.pack_size)
        grams, pieces = parse_pack_size(pack) if pack else parse_pack_size(name)
        key = product_key(brand, name, grams)
        bkey, tokens, gkey = key.split("|", 2)
        if grams is None or (key in self.by_key and platform_id in self.platforms.get(self.by_key[key], ())):
            # Unknown pack size, or the same key twice on one platform: the name alone can't tell
            # variants apart ('Tata Tea Gold' 250 g vs 1 kg), so never merge. Platform-scoped key.
            key = f"{key}|{platform_id}:{row.platform_sku}"
        if key in self.by_key:
            pid, method, score = self.by_key[key], "exact_key", 1.0
        else:
            best = max(((pid, similarity(tokens, t)) for pid, t in self.cands.get((bkey, gkey), [])
                        if gkey and platform_id not in self.platforms.get(pid, ())),
                       key=lambda x: x[1], default=None)
            if best and best[1] >= FUZZY_THRESHOLD:
                pid, method, score = best[0], "fuzzy", best[1]
            else:
                category_id = classify(name)
                cat = _text(row.platform_category)
                if category_id is None and cat:
                    category_id = self.catmap.get((platform_id, cat.lower()))
                sourdough = is_sourdough(name)
                attrs = bread_attributes(name) if sourdough or (category_id or 0) // 100 == 1 else {}
                pid = _next_ids(self.con, "core.seq_product", 1)[0]
                self.new_products.append((pid, key, name, self._brand_id(brand, _text(row.brand_type),
                                          bool(row.is_synthetic)), category_id or 999, grams, pieces,
                                          sourdough, json.dumps(attrs)))
                self.by_key[key] = pid
                self.cands.setdefault((bkey, gkey), []).append((pid, tokens))
                method, score = "new", 0.0
        self.platforms.setdefault(pid, set()).add(platform_id)
        return pid, method, score

    def flush(self) -> None:
        if self.new_brands:
            self.con.executemany("INSERT INTO ref.brand (brand_id, brand_name, brand_key, brand_type, is_synthetic) "
                                 "VALUES (?, ?, ?, ?, ?)", self.new_brands)
        if self.new_products:
            prod = pd.DataFrame(self.new_products, columns=["product_id", "product_key", "canonical_name",
                                "brand_id", "category_id", "pack_grams", "pack_units", "is_sourdough", "attributes"])
            self.con.register("_prod", prod)
            self.con.execute("INSERT INTO core.product SELECT product_id, product_key, canonical_name, brand_id, "
                             "category_id, pack_grams, pack_units, is_sourdough, attributes::JSON FROM _prod")
            self.con.unregister("_prod")
        self.new_brands, self.new_products = [], []


def _listing_ids(con, df: pd.DataFrame) -> pd.Series:
    """Upsert core.listing for every (platform, sku) in df; return listing_id per row."""
    latest = (df.sort_values("scraped_at")
                .groupby(["platform", "platform_sku"], as_index=False)
                .agg(first_seen=("scraped_at", "min"), last_seen=("scraped_at", "max"),
                     **{c: (c, "last") for c in ["product_name", "brand", "brand_type",
                        "platform_category", "pack_size", "url", "is_synthetic"]}))
    latest["platform_id"] = latest["platform"].map({p: db.platform_id(con, p) for p in latest["platform"].unique()})
    con.register("_latest", latest)
    existing = con.execute("""
        SELECT l.platform_id, l.platform_sku, l.listing_id FROM core.listing l
        JOIN _latest x ON x.platform_id = l.platform_id AND x.platform_sku = l.platform_sku
    """).fetchall()
    con.execute("""
        UPDATE core.listing l SET first_seen_at = least(l.first_seen_at, x.first_seen),
                                  last_seen_at = greatest(l.last_seen_at, x.last_seen)
        FROM _latest x WHERE x.platform_id = l.platform_id AND x.platform_sku = l.platform_sku
    """)
    con.unregister("_latest")
    ids = {(pf, sku): lid for pf, sku, lid in existing}

    new = latest[[(pf, sku) not in ids for pf, sku in zip(latest.platform_id, latest.platform_sku)]]
    if len(new):
        matcher = _Matcher(con)
        listing_ids = _next_ids(con, "core.seq_listing", len(new))
        rows = []
        for lid, row in zip(listing_ids, new.itertuples(index=False)):
            pid, method, score = matcher.match(row.platform_id, row)
            ids[(row.platform_id, row.platform_sku)] = lid
            rows.append((lid, row.platform_id, row.platform_sku, pid, row.product_name, _text(row.brand),
                         _text(row.platform_category), _text(row.pack_size), _text(row.url), method, score,
                         row.first_seen, row.last_seen))
        matcher.flush()
        con.register("_lst", pd.DataFrame(rows, columns=[
            "listing_id", "platform_id", "platform_sku", "product_id", "raw_name", "raw_brand", "raw_category",
            "pack_size_raw", "url", "match_method", "match_score", "first_seen_at", "last_seen_at"]))
        con.execute("INSERT INTO core.listing SELECT * FROM _lst")
        con.unregister("_lst")
    pf_of = {p: db.platform_id(con, p) for p in df["platform"].unique()}
    return pd.Series([ids[(pf_of[p], sku)] for p, sku in zip(df["platform"], df["platform_sku"])], index=df.index)


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
