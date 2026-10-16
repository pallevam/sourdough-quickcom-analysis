"""Loaders for the public Kaggle exports shipped in the project root.

zepto.zip   Zepto_data.csv: one national catalogue snapshot (no pincode, no timestamp,
            no brand column). Loaded through the snapshot contract at location 0.
swiggy.zip  swiggy_file.csv  } Food-delivery *restaurant* listings, not Instamart.
zomato.zip  zomato_dataset.csv } Loaded into core.restaurant_listing as a city-level
                                 bakery / cafe / sourdough affinity signal.
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
from qcom.normalize import guess_brand


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
    known = set(con.execute("SELECT brand_key FROM ref.brand").df()["brand_key"])
    df = pd.DataFrame({
        "platform": "zepto",
        "platform_sku": [hashlib.sha1(f"{n}|{p}".encode()).hexdigest()[:12]
                         for n, p in zip(raw.Product_name, raw.Packsize)],
        "scraped_at": file_date,
        "product_name": raw.Product_name,
        "brand": [guess_brand(n, known) for n in raw.Product_name],
        "platform_category": raw.Category.str.lower(),
        "pack_size": raw.Packsize,
        "selling_price": raw.Price,
        "mrp": raw.Original_price,
        "rating": raw.Rating,
    }).drop_duplicates(["platform_sku"])
    n = load_snapshot_frame(con, df, run_id)
    print(f"loaded {n:,} Zepto catalogue rows")
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
