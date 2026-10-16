-- Reference data that is independent of any scrape. Idempotent.

INSERT OR IGNORE INTO ref.platform VALUES
    (1, 'blinkit',          'Blinkit',            'quick_commerce', 'Eternal Ltd'),
    (2, 'instamart',        'Swiggy Instamart',   'quick_commerce', 'Swiggy Ltd'),
    (3, 'zepto',            'Zepto',              'quick_commerce', 'Kiranakart Technologies'),
    (4, 'bbnow',            'BigBasket BB Now',   'quick_commerce', 'Tata Digital'),
    (5, 'flipkart_minutes', 'Flipkart Minutes',   'quick_commerce', 'Flipkart'),
    (11, 'swiggy_food',     'Swiggy Food',        'food_delivery',  'Swiggy Ltd'),
    (12, 'zomato_food',     'Zomato',             'food_delivery',  'Eternal Ltd');

INSERT OR IGNORE INTO ref.location (location_id, pincode, locality, city_id)
VALUES (0, NULL, 'Unknown / national', NULL);

INSERT OR IGNORE INTO ref.category (category_id, l1, l2, l3, category_path)
SELECT id, l1, l2, l3, l1 || ' > ' || l2 || ' > ' || l3 FROM (VALUES
    (101, 'Bakery', 'Bread', 'Sourdough'),
    (102, 'Bakery', 'Bread', 'White'),
    (103, 'Bakery', 'Bread', 'Brown & Whole Wheat'),
    (104, 'Bakery', 'Bread', 'Multigrain & Seeded'),
    (105, 'Bakery', 'Bread', 'Specialty (Focaccia, Baguette, Bagel)'),
    (106, 'Bakery', 'Buns & Pav', 'Buns & Pav'),
    (107, 'Bakery', 'Sourdough Adjacent', 'Pizza Base & Flatbread'),
    (108, 'Bakery', 'Sourdough Adjacent', 'Crackers & Toasts'),
    (109, 'Bakery', 'Cakes & Pastries', 'Cakes & Pastries'),
    (110, 'Bakery', 'Rusk & Toast', 'Rusk & Toast'),
    (120, 'Baking Supplies', 'Starters & Yeast', 'Starters & Yeast'),
    (121, 'Baking Supplies', 'Flour', 'Specialty Flour'),
    (201, 'Dairy & Eggs', 'Cheese', 'Cheese'),
    (202, 'Dairy & Eggs', 'Milk & Curd', 'Milk & Curd'),
    (203, 'Dairy & Eggs', 'Butter & Spreads', 'Butter & Spreads'),
    (204, 'Dairy & Eggs', 'Eggs', 'Eggs'),
    (205, 'Dairy & Eggs', 'Dairy', 'Other Dairy'),
    (301, 'Fruits & Vegetables', 'Fresh', 'Fresh'),
    (401, 'Staples', 'Atta, Rice, Oil & Dal', 'Atta, Rice, Oil & Dal'),
    (402, 'Staples', 'Masala & Dry Fruits', 'Masala & Dry Fruits'),
    (501, 'Beverages', 'Tea & Coffee', 'Tea & Coffee'),
    (502, 'Beverages', 'Cold Drinks & Juices', 'Cold Drinks & Juices'),
    (601, 'Snacks & Packaged', 'Packaged Food', 'Packaged Food'),
    (602, 'Snacks & Packaged', 'Sweets & Chocolates', 'Sweets & Chocolates'),
    (603, 'Snacks & Packaged', 'Sauces & Spreads', 'Sauces & Spreads'),
    (701, 'Frozen', 'Frozen Food', 'Frozen Food'),
    (702, 'Frozen', 'Ice Cream', 'Ice Cream'),
    (801, 'Meat & Fish', 'Meat, Fish & Eggs', 'Meat, Fish & Eggs'),
    (901, 'Personal Care', 'Skincare', 'Skincare'),
    (951, 'Household', 'Stationery', 'Stationery'),
    (952, 'Household', 'Paan Corner', 'Paan Corner'),
    (999, 'Uncategorised', 'Uncategorised', 'Uncategorised')
) t(id, l1, l2, l3);

-- Zepto's category labels as they appear in the Kaggle export.
INSERT OR IGNORE INTO ref.platform_category_map VALUES
    (3, 'fresh fruit and vegetables', 301),
    (3, 'atta rice oil and daal',     401),
    (3, 'masala dry fruits',          402),
    (3, 'tea cofee and more',         501),
    (3, 'cold drinks and juices',     502),
    (3, 'packaged food',              601),
    (3, 'sweet cravings',             602),
    (3, 'breakfast sauces',           603),
    (3, 'frozen food',                701),
    (3, 'ice cream',                  702),
    (3, 'meats fish eggs',            801),
    (3, 'dairy bread and eggs',       205),
    (3, 'skincare',                   901),
    (3, 'stationery books',           951),
    (3, 'paan corners',               952);

INSERT OR IGNORE INTO ref.model_param VALUES
    ('orders_per_rating', 30,
     'Orders per new rating. Used when a platform does not expose inventory. Calibrate per platform/category against a panel where both signals exist.'),
    ('units_per_order', 1.1,
     'Average units of one SKU per order containing it.'),
    ('min_availability_for_uplift', 0.25,
     'Below this in-stock share, do not inflate observed units to unconstrained demand (too noisy).');
