"""String normalisation used to match the same product across platforms."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

_UNIT_TO_GRAMS = {"g": 1, "gm": 1, "gms": 1, "gram": 1, "grams": 1, "kg": 1000,
                  "ml": 1, "l": 1000, "ltr": 1000, "litre": 1000}
_QTY = r"(\d+(?:\.\d+)?)\s*(kg|gms?|grams?|g|ml|ltr|litre|l)\b"
_PIECES = r"(\d+)\s*(?:pcs?|pieces?|slices?|units?|nos?)\b"
_STOPWORDS = {"the", "a", "of", "with", "by", "pack", "pc", "pcs", "piece", "pieces", "x", "and"}


def parse_pack_size(text: str | None) -> tuple[float | None, int | None]:
    """'2 x 400 g' -> (800, 2); '1 pc (450 - 550 g)' -> (500, 1); '1 kg' -> (1000, None).

    Returns (net grams, piece count). ml is treated as grams.
    """
    if not text:
        return None, None
    t = text.lower()

    m = re.search(r"(\d+)\s*x\s*" + _QTY, t)
    if m:
        n, qty, unit = int(m.group(1)), float(m.group(2)), m.group(3)
        return n * qty * _UNIT_TO_GRAMS[unit], n

    m = re.search(r"(\d+(?:\.\d+)?)\s*-\s*" + _QTY, t)   # a range: take the midpoint
    if m:
        lo, hi, unit = float(m.group(1)), float(m.group(2)), m.group(3)
        grams = (lo + hi) / 2 * _UNIT_TO_GRAMS[unit]
    else:
        m = re.search(_QTY, t)
        grams = float(m.group(1)) * _UNIT_TO_GRAMS[m.group(2)] if m else None

    p = re.search(_PIECES, t)
    pieces = int(p.group(1)) if p else None
    return grams, pieces


def normalize_text(s: str | None) -> str:
    s = (s or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def brand_key(brand: str | None) -> str:
    return normalize_text(brand).replace(" and ", " ").replace(" ", "") or "unknown"


def name_tokens(name: str, brand: str | None = None) -> list[str]:
    """Sorted content tokens of a product name, minus brand, pack size and filler words."""
    s = normalize_text(re.sub(_QTY, " ", (name or "").lower()))
    s = re.sub(_PIECES, " ", s)
    brand_words = set(normalize_text(brand).split())
    # Digits that survive pack-size removal are meaningful: '50% dark' vs '70% dark', 'V5' vs 'V7'.
    return sorted({w for w in s.split() if w not in _STOPWORDS and w not in brand_words})


def product_key(brand: str | None, name: str, grams: float | None) -> str:
    g = "" if grams is None else str(int(round(grams)))
    return f"{brand_key(brand)}|{' '.join(name_tokens(name, brand))}|{g}"


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def guess_brand(product_name: str, known_brand_keys: set[str] | None = None) -> str:
    """Best-effort brand for sources without a brand column (e.g. the Zepto Kaggle file).

    Prefers the longest known brand that prefixes the name; falls back to the first word.
    """
    words = product_name.split()
    if known_brand_keys:
        for n in (3, 2, 1):
            cand = " ".join(words[:n])
            if brand_key(cand) in known_brand_keys:
                return cand
    return words[0] if words else "Unknown"
