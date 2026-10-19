"""Loaders for the public Kaggle exports shipped in the project root.

zepto.zip   Zepto_data.csv: one national catalogue snapshot (no pincode, no timestamp,
            no brand column). Loaded through the snapshot contract at location 0.
swiggy.zip  swiggy_file.csv  } Food-delivery *restaurant* listings, not Instamart.
zomato.zip  zomato_dataset.csv } Loaded into core.restaurant_listing as a city-level
                                 bakery / cafe / sourdough affinity signal.
blinkit.zip    blinkit_retail.csv: Blinkit home-page prices, 5 cities x 11 days (Jan 2024), CC0.
               No pincode, stock count or ratings: one city-wide location per city, no sales estimate.
bigbasket.zip  BigBasket Products.csv: ~27.5k-product catalogue (2022), CC BY-NC-SA 4.0. National, one date.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from datetime import datetime
from pathlib import Path

import pandas as pd

from qcom import db
from qcom.ingest.snapshots import load_snapshot_frame


def _read_member(zip_path: Path, suffix: str) -> tuple[pd.DataFrame, datetime]:
    with zipfile.ZipFile(zip_path) as z:
        info = next(i for i in z.infolist() if i.filename.lower().endswith(suffix.lower()))
        with z.open(info) as f:
            return pd.read_csv(f, on_bad_lines="skip"), datetime(*info.date_time)


def load_zepto(con, zip_path: Path) -> int:
    run_id = db.start_run(con, "kaggle", zip_path, "Zepto catalogue; date = file date in archive")
    if run_id is None:
        print(f"skip {zip_path.name}: already loaded")
        return 0
    raw, file_date = _read_member(zip_path, "Zepto_data.csv")
    df = pd.DataFrame({
        "platform": "zepto",
        "platform_sku": [hashlib.sha1(f"{n}|{p}".encode()).hexdigest()[:12]
                         for n, p in zip(raw.Product_name, raw.Packsize)],
        "scraped_at": file_date,
        "product_name": raw.Product_name,
        "brand": None,
        "platform_category": raw.Category.str.lower(),
        "pack_size": raw.Packsize,
        "selling_price": raw.Price,
        "mrp": raw.Original_price,
        "rating": raw.Rating,
    }).drop_duplicates(["platform_sku"])
    n = load_snapshot_frame(con, df, run_id)
    print(f"loaded {n:,} Zepto catalogue rows")
    return n


def _rupees(s) -> float | None:
    """'₹ 1,299' -> 1299.0"""
    if not isinstance(s, str):
        return None
    m = re.search(r"[\d,]+(?:\.\d+)?", s)
    return float(m.group(0).replace(",", "")) if m else None


def _sku(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


def load_bigbasket(con, zip_path: Path) -> int:
    """Load source-reported brands and categories."""
    run_id = db.start_run(con, "kaggle", zip_path, "BigBasket catalogue (Kaggle, CC BY-NC-SA 4.0); national, one date")
    if run_id is None:
        print(f"skip {zip_path.name}: already loaded")
        return 0
    raw, file_date = _read_member(zip_path, "BigBasket Products.csv")
    raw = raw.dropna(subset=["product"])
    df = pd.DataFrame({
        "platform": "bigbasket",
        "platform_sku": raw["index"].astype(str),
        "scraped_at": file_date,
        "product_name": raw["product"].str.strip(),
        "brand": raw["brand"].fillna("Unknown").str.strip(),
        "platform_category": raw["category"].str.lower(),
        "selling_price": raw["sale_price"],
        "mrp": raw["market_price"],
        "rating": raw["rating"],
    })
    n = load_snapshot_frame(con, df, run_id)
    print(f"loaded {n:,} BigBasket catalogue rows")
    return n


def load_blinkit(con, zip_path: Path) -> int:
    run_id = db.start_run(con, "kaggle", zip_path, "Blinkit home-page prices (Kaggle, CC0); 5 cities, city-level")
    if run_id is None:
        print(f"skip {zip_path.name}: already loaded")
        return 0
    raw, _ = _read_member(zip_path, "blinkit_retail.csv")
    raw = raw.dropna(subset=["Title"]).drop_duplicates(["Title", "Details", "Place", "Date"])
    sp = raw["Price_discount"].map(_rupees)
    df = pd.DataFrame({
        "platform": "blinkit",
        "platform_sku": [_sku(t, d) for t, d in zip(raw["Title"], raw["Details"].fillna(""))],
        "scraped_at": pd.to_datetime(raw["Date"].astype(str), format="%Y%m%d"),
        "pincode": "",
        "locality": raw["Place"] + " (city-wide)",   # no pincode in the source: one location per city
        "city": raw["Place"],
        "product_name": raw["Title"].str.strip(),
        "brand": None,
        "pack_size": raw["Details"],
        "selling_price": sp,
        "mrp": raw["Price_original"].map(_rupees),
        "in_stock": raw["Tag"].map({"Out of Stock": False, "In Stock": True}),
    })
    n = load_snapshot_frame(con, df, run_id)
    print(f"loaded {n:,} Blinkit price rows")
    return n


def _rating_count_floor(s: str | float) -> int | None:
    m = re.search(r"([\d.]+)\s*(K)?\+?", str(s), re.I) if isinstance(s, str) else None
    return int(float(m.group(1)) * (1000 if m.group(2) else 1)) if m else None


def _price(s) -> tuple[int | None, str | None]:
    m = re.search(r"(\d+)\s*for\s*(one|two)", str(s), re.I)
    return (int(m.group(1)), f"for {m.group(2).lower()}") if m else (None, None)


def load_restaurants(con, zip_path: Path, platform_code: str, member: str) -> int:
    run_id = db.start_run(con, "kaggle", zip_path, f"{platform_code} restaurant listings")
    if run_id is None:
        print(f"skip {zip_path.name}: already loaded")
        return 0
    raw, _ = _read_member(zip_path, member)
    city_ids = db.resolve_cities(con, raw["Location"].dropna())
    cuisine = raw["Cuisine"].fillna("")
    name = raw["Restaurant Name"].fillna("")
    prices = raw["Average Price"].map(_price)
    df = pd.DataFrame({
        "run_id": run_id,
        "platform_id": db.platform_id(con, platform_code),
        "city_id": raw["Location"].map(city_ids),
        "area": raw.get("Area"),
        "restaurant_name": name,
        "cuisines": cuisine.map(lambda c: [x.strip() for x in c.split(",") if x.strip()]),
        "rating": pd.to_numeric(raw["Rating"], errors="coerce"),
        "rating_count_floor": raw.get("Number of Ratings", pd.Series(dtype=object)).map(_rating_count_floor),
        "price_amount": prices.map(lambda p: p[0]),
        "price_basis": prices.map(lambda p: p[1]),
        "offer_count": raw.get("Number of Offers"),
        "is_pure_veg": raw.get("Pure Veg"),
        "is_bakery": cuisine.str.contains(r"bakery", case=False),
        "is_cafe": cuisine.str.contains(r"caf[eé]", case=False) | name.str.contains(r"caf[eé]", case=False),
        "mentions_sourdough": (name + " " + cuisine).str.contains(r"sour\s*dough", case=False),
    })
    df["price_for_two"] = df.price_amount.where(df.price_basis != "for one", df.price_amount * 2)
    con.register("_rest", df)
    n = con.execute("""
        INSERT INTO core.restaurant_listing (run_id, platform_id, city_id, area, restaurant_name,
            cuisines, rating, rating_count_floor, price_amount, price_basis, price_for_two,
            offer_count, is_pure_veg, is_bakery, is_cafe, mentions_sourdough)
        SELECT run_id, platform_id, city_id, area, restaurant_name, cuisines, rating,
               rating_count_floor, price_amount, price_basis, price_for_two, offer_count,
               is_pure_veg, is_bakery, is_cafe, mentions_sourdough
        FROM _rest
    """).fetchone()[0]
    con.unregister("_rest")
    db.finish_run(con, run_id, n)
    print(f"loaded {n:,} {platform_code} restaurant rows")
    return n
