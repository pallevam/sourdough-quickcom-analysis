"""Rule-based product classifier onto the canonical taxonomy in sql/02_seed.sql.

Rules are ordered: the first match wins, so specific rules (sourdough starter) sit
above general ones (sourdough). Returns None when no rule applies, in which case
the loader falls back to ref.platform_category_map.
"""

from __future__ import annotations

import re

SOURDOUGH_RE = re.compile(r"sour\s*-?\s*dough|levain|naturally\s+leavened", re.I)

# (category_id, pattern)
RULES: list[tuple[int, re.Pattern]] = [(cid, re.compile(p, re.I)) for cid, p in [
    (702, r"ice\s*cream|kulfi|gelato"),   # before bakery: 'brownie ice cream tub'
    (120, r"(sour\s*dough|levain)\s+starter|\bstarter culture|\byeast\b"),
    (107, r"(sour\s*dough).*(pizza|flatbread|pita)|(pizza|flatbread|pita).*sour\s*dough"),
    (108, r"(sour\s*dough).*(cracker|crisp|toast|chip|lavash)"),
    (101, r"sour\s*-?\s*dough|levain|naturally\s+leavened"),
    (105, r"focaccia|baguette|ciabatta|bagel|brioche|croissant|sub roll"),
    (106, r"\bpav\b|\bbuns?\b|burger bun|hot ?dog roll"),
    (110, r"\brusk\b|\btoast\b"),
    (104, r"(multi\s*-?grain|seeded|oats?|millet)\s+(bread|loaf)"),
    (103, r"(brown|whole\s*wheat|atta|wheat)\s+bread"),
    (102, r"(white|sandwich|milk|super soft)\s+bread|\bbread\b"),
    (109, r"\bcakes?\b|pastr(y|ies)|muffin"),
    (201, r"cheese|mozzarella|cheddar|paneer|feta|bocconcini|mascarpone"),
    (203, r"butter|spread|margarine"),
    (204, r"\beggs?\b"),
    (202, r"\bmilk\b|\bcurd\b|\bdahi\b|yogh?urt|lassi"),
]]


def classify(name: str) -> int | None:
    for cid, pat in RULES:
        if pat.search(name or ""):
            return cid
    return None


def is_sourdough(name: str) -> bool:
    return bool(SOURDOUGH_RE.search(name or ""))


def bread_attributes(name: str) -> dict:
    """Variant attributes worth slicing sourdough by."""
    n = (name or "").lower()
    flour = next((f for f, p in [
        ("rye", r"\brye\b"), ("whole wheat", r"whole\s*wheat|\batta\b"),
        ("multigrain", r"multi\s*-?grain|seeded"), ("millet", r"millet|ragi|jowar|bajra"),
        ("white", r"white|classic|country|plain"),
    ] if re.search(p, n)), None)
    return {
        "flour": flour,
        "sliced": bool(re.search(r"\bsliced?\b|sandwich", n)),
        "format": next((f for f in ("focaccia", "baguette", "boule", "batard", "bagel", "pizza")
                        if f in n), "loaf"),
        "inclusions": [w for w in ("olive", "garlic", "cranberry", "walnut", "jalapeno", "cheese",
                                   "herb", "rosemary", "sesame") if w in n] or None,
    }
