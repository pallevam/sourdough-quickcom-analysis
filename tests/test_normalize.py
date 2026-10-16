import pytest

from qcom.normalize import parse_pack_size, product_key
from qcom.taxonomy import bread_attributes, classify, is_sourdough


@pytest.mark.parametrize("text, grams, pieces", [
    ("400 g", 400, None),
    ("1 kg", 1000, None),
    ("2 x 400 g", 800, 2),
    ("1 pc (450 - 550 g)", 500, 1),
    ("1 pack (10 pcs)", None, 10),
    ("6 x 500 ml", 3000, 6),
    (None, None, None),
])
def test_parse_pack_size(text, grams, pieces):
    assert parse_pack_size(text) == (grams, pieces)


def test_same_product_matches_across_platform_title_styles():
    blinkit = product_key("Levain & Loaf", "Levain & Loaf Classic White Sourdough - 400 g", 400)
    instamart = product_key("Levain and Loaf", "Levain and Loaf Classic White Sourdough (400 g)", 400)
    zepto = product_key("Levain & Loaf", "Classic White Sourdough by Levain & Loaf", 400)
    assert blinkit == instamart == zepto


def test_variant_numbers_distinguish_products():
    assert product_key("Cadbury", "Cadbury Bournville 50% Dark Chocolate", 80) != \
        product_key("Cadbury", "Cadbury Bournville 70% Dark Chocolate", 80)


def test_pack_size_distinguishes_products():
    assert product_key("X", "Sourdough", 400) != product_key("X", "Sourdough", 800)


@pytest.mark.parametrize("name, category_id", [
    ("Levain & Loaf Classic White Sourdough", 101),
    ("Whole Wheat Sourdough Loaf", 101),
    ("Sourdough Pizza Base 2 pc", 107),
    ("Pizza Base - Sourdough 100% Wholewheat", 107),
    ("Sourdough Crackers Sea Salt", 108),
    ("Organic Sourdough Starter", 120),
    ("Harvest Multigrain Bread", 104),
    ("Brown Bread", 103),
    ("Sandwich Bread", 102),
    ("Ladi Pav", 106),
    ("Aashirvaad Multigrain Atta", None),          # flour, not bread
    ("Amul Chocolate Brownie Ice Cream Tub", 702),  # ice cream, not cake
])
def test_classify(name, category_id):
    assert classify(name) == category_id


def test_sourdough_detection_and_attributes():
    assert is_sourdough("Naturally Leavened Rye Loaf")
    assert not is_sourdough("Sour Cream & Onion Chips")
    attrs = bread_attributes("Whole Wheat Sliced Sourdough with Walnut")
    assert attrs["flour"] == "whole wheat" and attrs["sliced"] and attrs["inclusions"] == ["walnut"]
