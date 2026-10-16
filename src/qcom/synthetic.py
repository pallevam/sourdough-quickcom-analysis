"""Synthetic quick-commerce bread panel, for building and testing the pipeline
before real multi-city, multi-day scrapes exist.

EVERYTHING HERE IS MADE UP. Brands are fictional; demand levels, city factors and
growth rates are illustrative assumptions, not market facts. All rows carry
is_synthetic = TRUE and should never be mixed into a real readout.

What it simulates per platform x pincode x SKU:
  * daily demand = base popularity x city affinity x platform share x weekday lift
                   x monthly growth x price elasticity (promos)
  * a dark store restocked once a day at 06:00 to a planned capacity, so weekends
    and promos cause stock-outs (lost sales)
  * 4 scrapes a day (07, 12, 17, 22h). Blinkit and Zepto payloads expose inventory;
    Instamart does not, so it must be estimated from rating velocity
  * a daily search-results scrape for "sourdough" and "bread"

Output follows the contract in qcom.ingest.snapshots, plus a ground-truth file.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

START, DAYS = date(2026, 4, 1), 183  # Apr - Sep 2026
SNAP_HOURS = [7, 12, 17, 22]
# Share of a day's demand in each interval: 06-07, 07-12, 12-17, 17-22, 22-06
INTERVAL_SHARE = np.array([0.03, 0.32, 0.25, 0.33, 0.07])
UNITS_SCALE = 8.0           # units/day for popularity 1.0 at affinity 1.0 and 100% share
PRICE_ELASTICITY = -1.8
ORDERS_PER_RATING = 30

BRANDS = {  # name: (brand_type, regional strength by region)
    "Levain & Loaf":       ("artisanal", {"South": 1.5}),
    "Crumb Theory":        ("artisanal", {"West": 1.6}),
    "Wildyeast Bakehouse": ("artisanal", {"North": 1.6}),
    "Daily Sour":          ("national", {}),
    "Morning Loaf":        ("national", {}),
    "Softbake":            ("national", {}),
}

# brand, name, pack, mrp, popularity, is_sourdough, platforms carrying it
PRODUCTS = [
    ("Levain & Loaf", "Classic White Sourdough", "400 g", 199, 1.0, True, "BIZ"),
    ("Levain & Loaf", "Whole Wheat Sourdough", "400 g", 219, 0.7, True, "BIZ"),
    ("Levain & Loaf", "Sourdough Focaccia Rosemary", "250 g", 179, 0.4, True, "BI"),
    ("Crumb Theory", "Country Sourdough Boule", "500 g", 249, 0.6, True, "BZ"),
    ("Crumb Theory", "Multigrain Sourdough Loaf", "450 g", 259, 0.5, True, "BIZ"),
    ("Crumb Theory", "Sourdough Pizza Base", "2 x 150 g", 169, 0.35, True, "BZ"),
    ("Wildyeast Bakehouse", "Rye Sourdough", "400 g", 289, 0.25, True, "BI"),
    ("Wildyeast Bakehouse", "Olive Herb Sourdough", "400 g", 299, 0.3, True, "BI"),
    ("Daily Sour", "Sliced Sourdough Sandwich Bread", "350 g", 129, 1.2, True, "BIZ"),
    ("Daily Sour", "Whole Wheat Sliced Sourdough", "350 g", 139, 0.9, True, "BIZ"),
    ("Daily Sour", "Sourdough Crackers Sea Salt", "100 g", 99, 0.4, True, "IZ"),
    ("Morning Loaf", "White Sandwich Bread", "400 g", 45, 6.0, False, "BIZ"),
    ("Morning Loaf", "Brown Bread", "400 g", 55, 3.5, False, "BIZ"),
    ("Softbake", "Multigrain Bread", "400 g", 65, 2.0, False, "BIZ"),
    ("Softbake", "Whole Wheat Bread", "450 g", 60, 2.5, False, "BIZ"),
    ("Softbake", "Pav", "250 g", 35, 3.0, False, "BIZ"),
]
PLATFORM_LETTER = {"B": "blinkit", "I": "instamart", "Z": "zepto"}

# city: (sourdough affinity, monthly sourdough growth, region, platform shares B/I/Z, pincodes)
CITIES = {
    "Bangalore":  (1.00, 0.035, "South", (0.30, 0.38, 0.32), [("560034", "Koramangala"), ("560038", "Indiranagar"), ("560066", "Whitefield")]),
    "Mumbai":     (0.90, 0.035, "West", (0.34, 0.28, 0.38), [("400050", "Bandra West"), ("400076", "Powai")]),
    "Delhi":      (0.75, 0.040, "North", (0.50, 0.25, 0.25), [("110017", "Saket"), ("110024", "Lajpat Nagar")]),
    "Gurgaon":    (0.85, 0.040, "North", (0.50, 0.22, 0.28), [("122002", "DLF Phase IV")]),
    "Noida":      (0.55, 0.050, "North", (0.50, 0.22, 0.28), [("201301", "Sector 18")]),
    "Pune":       (0.65, 0.045, "West", (0.35, 0.30, 0.35), [("411045", "Baner"), ("411001", "Camp")]),
    "Hyderabad":  (0.55, 0.050, "South", (0.36, 0.34, 0.30), [("500033", "Jubilee Hills"), ("500081", "Madhapur")]),
    "Chennai":    (0.40, 0.050, "South", (0.32, 0.40, 0.28), [("600020", "Adyar")]),
    "Kolkata":    (0.30, 0.055, "East", (0.45, 0.30, 0.25), [("700019", "Ballygunge")]),
    "Ahmedabad":  (0.25, 0.060, "West", (0.45, 0.30, 0.25), [("380015", "Satellite")]),
    "Jaipur":     (0.20, 0.070, "North", (0.50, 0.30, 0.20), [("302017", "Malviya Nagar")]),
    "Lucknow":    (0.15, 0.080, "North", (0.55, 0.30, 0.15), [("226010", "Gomti Nagar")]),
    "Chandigarh": (0.35, 0.060, "North", (0.50, 0.30, 0.20), [("160017", "Sector 17")]),
    "Indore":     (0.15, 0.080, "Central", (0.60, 0.40, 0.00), [("452010", "Vijay Nagar")]),
    "Kochi":      (0.30, 0.060, "South", (0.55, 0.45, 0.00), [("682024", "Edappally")]),
}

PLATFORM_CATEGORY = {"blinkit": "Bakery & Biscuits > Bread & Pav",
                     "instamart": "Bakery > Breads",
                     "zepto": "dairy bread and eggs"}
SOURDOUGH_DOW = np.array([0.90, 0.90, 0.95, 0.95, 1.05, 1.30, 1.35])  # Mon..Sun
REGULAR_DOW = np.array([1.00, 1.00, 1.00, 1.00, 1.00, 1.05, 1.08])


def _platform_name(platform: str, brand: str, name: str, pack: str) -> tuple[str, str]:
    """Each platform formats titles differently; matching has to cope."""
    if platform == "blinkit":
        return f"{brand} {name} - {pack}", brand
    if platform == "instamart":
        b = brand.replace("&", "and")
        return f"{b} {name} ({pack})", b
    return f"{name} by {brand}", brand


@dataclass
class Series:
    platform: str
    sku: str
    brand: str
    raw_name: str
    raw_brand: str
    pack: str
    mrp: float
    sourdough: bool
    city: str
    pincode: str
    locality: str
    base: float           # units/day at month 0, weekday 1.0, full price
    growth: float         # monthly
    cap_factor: float     # planned stock vs expected demand
    rating: float
    rating_count0: int


def _build_series(rng) -> list[Series]:
    skus = {}
    for brand, name, pack, mrp, pop, sd, plats in PRODUCTS:
        for letter in plats:
            p = PLATFORM_LETTER[letter]
            skus[(p, name)] = {"blinkit": lambda: str(rng.integers(100000, 999999)),
                               "instamart": lambda: "".join(rng.choice(list("ABCDEFGHJKLMNPQRSTUVWXYZ23456789"), 10)),
                               "zepto": lambda: f"{rng.integers(16**8):08x}-{rng.integers(16**4):04x}"}[p]()
    out = []
    for city, (aff, growth, region, shares, pins) in CITIES.items():
        for brand, name, pack, mrp, pop, sd, plats in PRODUCTS:
            regional = BRANDS[brand][1].get(region, 0.8 if BRANDS[brand][1] else 1.0)
            for letter in plats:
                p = PLATFORM_LETTER[letter]
                share = dict(zip("BIZ", shares))[letter]
                if share == 0:
                    continue
                for pin, loc in pins:
                    raw_name, raw_brand = _platform_name(p, brand, name, pack)
                    city_factor = aff if sd else rng.uniform(0.85, 1.15)
                    out.append(Series(
                        platform=p, sku=skus[(p, name)], brand=brand, raw_name=raw_name,
                        raw_brand=raw_brand, pack=pack, mrp=mrp, sourdough=sd, city=city,
                        pincode=pin, locality=loc,
                        base=UNITS_SCALE * pop * city_factor * regional * share * 3 * rng.uniform(0.8, 1.2),
                        growth=growth if sd else 0.005, cap_factor=rng.uniform(0.75, 1.6),
                        rating=round(rng.uniform(4.0, 4.6), 1), rating_count0=int(rng.integers(50, 3000)),
                    ))
    return out


def generate(out_dir: Path, seed: int = 42) -> list[Path]:
    rng = np.random.default_rng(seed)
    series = _build_series(rng)
    n = len(series)
    S = pd.DataFrame([s.__dict__ for s in series])
    base, growth, mrp = S.base.values, S.growth.values, S.mrp.values.astype(float)
    sd = S.sourdough.values
    has_inventory = (S.platform != "instamart").values

    # Promotions: per platform x brand x week, 15% chance of a 15-25% deal.
    weeks = DAYS // 7 + 1
    pb = S.platform + "|" + S.brand
    promo_keys = pb.unique()
    promo = {k: np.where(rng.random(weeks) < 0.15, rng.uniform(0.15, 0.25, weeks), 0.0) for k in promo_keys}
    base_disc = rng.uniform(0.0, 0.08, n)

    inventory = np.zeros(n)
    rating_count = S.rating_count0.values.astype(float)
    snaps, truth = [], []
    for d in range(DAYS):
        day = START + timedelta(days=d)
        month = d / 30.4
        dow = day.weekday()
        disc = np.maximum(base_disc, np.array([promo[k][d // 7] for k in pb]))
        price = np.round(mrp * (1 - disc))
        expected = base * (1 + growth) ** month
        lam = expected * np.where(sd, SOURDOUGH_DOW[dow], REGULAR_DOW[dow]) * (1 - disc) ** PRICE_ELASTICITY
        # 06:00 restock to a plan based on expected demand, re-planned as volume grows
        inventory = np.maximum(inventory, np.ceil(expected * S.cap_factor.values))
        sold_day, lost_day = np.zeros(n), np.zeros(n)
        for i, share in enumerate(INTERVAL_SHARE):
            demand = rng.poisson(lam * share)
            sold = np.minimum(demand, inventory)
            inventory -= sold
            sold_day += sold
            lost_day += demand - sold
            rating_count += rng.binomial(sold.astype(int), 1 / ORDERS_PER_RATING)
            if i < 4:  # intervals 0..3 end at a scrape time
                snaps.append(pd.DataFrame({
                    "idx": np.arange(n),
                    "scraped_at": datetime(day.year, day.month, day.day, SNAP_HOURS[i], rng.integers(0, 10)),
                    "selling_price": price, "inventory_qty": inventory.copy(),
                    "rating_count": rating_count.copy(),
                }))
        truth.append(pd.DataFrame({"idx": np.arange(n), "sales_date": day,
                                   "units_sold": sold_day.astype(int), "units_lost": lost_day.astype(int)}))
        inventory = inventory * 0.2  # fresh bread: most leftover stock is pulled before restock

    snap = pd.concat(snaps, ignore_index=True)
    meta = S.assign(idx=np.arange(n))
    snap = snap.merge(meta, on="idx")
    snap = pd.DataFrame({
        "platform": snap.platform, "platform_sku": snap.sku, "scraped_at": snap.scraped_at,
        "pincode": snap.pincode, "locality": snap.locality, "city": snap.city,
        "product_name": snap.raw_name, "brand": snap.raw_brand,
        "brand_type": snap.brand.map(lambda b: BRANDS[b][0]),
        "platform_category": snap.platform.map(PLATFORM_CATEGORY), "pack_size": snap.pack,
        "selling_price": snap.selling_price, "mrp": snap.mrp,
        "in_stock": snap.inventory_qty > 0,
        "inventory_qty": snap.inventory_qty.where(snap.platform != "instamart").astype("Int64"),
        "max_cart_qty": np.minimum(snap.inventory_qty, 5).astype(int),
        "eta_minutes": rng.integers(8, 19, len(snap)),
        "rating": snap.rating, "rating_count": snap.rating_count.astype(int),
        "is_sponsored": False, "is_synthetic": True,
    })

    search = _search_results(rng, meta, snap)
    truth = pd.concat(truth, ignore_index=True).merge(meta[["idx", "platform", "sku", "pincode", "locality"]], on="idx")

    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for p, g in snap.groupby("platform"):
        paths.append(out_dir / f"snapshots_{p}.parquet")
        g.to_parquet(paths[-1], index=False)
    paths.append(out_dir / "search_results.parquet")
    search.to_parquet(paths[-1], index=False)
    paths.append(out_dir / "truth_daily.parquet")
    truth.drop(columns="idx").rename(columns={"sku": "platform_sku"}).to_parquet(paths[-1], index=False)
    print(f"synthetic: {n} series, {len(snap):,} snapshots, {len(search):,} search rows -> {out_dir}")
    return paths


def _search_results(rng, meta: pd.DataFrame, snap: pd.DataFrame) -> pd.DataFrame:
    """Noon search scrape: rank by recent popularity + in-stock + noise; one sponsored slot."""
    noon = snap[snap.scraped_at.dt.hour == 12].merge(
        meta[["sku", "platform", "pincode", "base", "brand", "sourdough"]].rename(columns={"brand": "canon_brand"}),
        left_on=["platform_sku", "platform", "pincode"], right_on=["sku", "platform", "pincode"])
    rows = []
    for term, mask in [("sourdough", noon.sourdough), ("bread", np.ones(len(noon), bool))]:
        g = noon[mask].copy()
        g["score"] = np.log1p(g.base) + 1.5 * g.in_stock + rng.normal(0, 0.6, len(g))
        week = g.scraped_at.dt.isocalendar().week.astype(int)
        # each platform-week one brand buys the top slot
        sponsor = {(p, w): b for (p, w), b in
                   g.assign(week=week).groupby(["platform", "week"]).canon_brand
                    .agg(lambda s: rng.choice(s.unique())).items()}
        g["is_sponsored"] = [sponsor[(p, w)] == b for p, w, b in zip(g.platform, week, g.canon_brand)]
        g["score"] += 10 * g.is_sponsored
        g["is_sponsored"] &= g.groupby(["platform", "pincode", "scraped_at"]).score.rank(ascending=False) == 1
        g["rank_position"] = g.groupby(["platform", "pincode", "scraped_at"]).score.rank(
            ascending=False, method="first").astype(int)
        g = g[g.rank_position <= 10]
        rows.append(pd.DataFrame({
            "platform": g.platform, "platform_sku": g.platform_sku, "scraped_at": g.scraped_at,
            "pincode": g.pincode, "locality": g.locality, "city": g.city, "search_term": term,
            "rank_position": g.rank_position, "is_sponsored": g.is_sponsored, "is_synthetic": True}))
    return pd.concat(rows, ignore_index=True)


def load_truth(con, path: Path) -> int:
    n = con.execute("""
        INSERT OR IGNORE INTO core.synthetic_truth_daily
        SELECT t.sales_date, l.listing_id, lo.location_id, t.units_sold, t.units_lost
        FROM read_parquet(?) t
        JOIN ref.platform p ON p.platform_code = t.platform
        JOIN core.listing l ON l.platform_id = p.platform_id AND l.platform_sku = t.platform_sku
        JOIN ref.location lo ON lo.pincode = t.pincode AND lo.locality = t.locality
    """, [str(path)]).fetchone()[0]
    print(f"loaded {n:,} ground-truth rows")
    return n
