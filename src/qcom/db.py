"""Database connection, schema bootstrap and dimension lookups."""

from __future__ import annotations

import hashlib
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
SQL_DIR = ROOT / "sql"
SEED_DIR = ROOT / "data" / "seed"
DEFAULT_DB = ROOT / "data" / "qcom.duckdb"


def connect(path: Path | str = DEFAULT_DB) -> duckdb.DuckDBPyConnection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path))


def run_sql_file(con: duckdb.DuckDBPyConnection, path: Path) -> None:
    con.execute(path.read_text())


def init(con: duckdb.DuckDBPyConnection) -> None:
    """Create schema, seed reference data, (re)create mart views. Safe to re-run."""
    run_sql_file(con, SQL_DIR / "01_schema.sql")
    run_sql_file(con, SQL_DIR / "02_seed.sql")
    cities, aliases = SEED_DIR / "cities.csv", SEED_DIR / "city_aliases.csv"
    con.execute(f"""
        INSERT OR IGNORE INTO ref.city
            (city_id, city_name, state, region, tier, metro_cluster, lat, lon, is_seeded)
        SELECT city_id, city_name, state, region, tier, metro_cluster, lat, lon, TRUE
        FROM read_csv('{cities}', header = true, all_varchar = false)
    """)
    con.execute(f"""
        INSERT OR IGNORE INTO ref.city_alias
        SELECT lower(city_name), city_id, 'canonical name' FROM ref.city
        UNION ALL
        SELECT lower(a.alias), c.city_id, a.note
        FROM read_csv('{aliases}', header = true, all_varchar = true) a
        JOIN ref.city c USING (city_name)
    """)
    run_sql_file(con, SQL_DIR / "03_marts.sql")


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def start_run(con, source_type: str, source_file: Path, notes: str = "") -> int | None:
    """Register a file load. Returns None if this exact file was already loaded."""
    sha = file_sha256(source_file)
    if con.execute("SELECT 1 FROM core.scrape_run WHERE file_sha256 = ?", [sha]).fetchone():
        return None
    return con.execute(
        "INSERT INTO core.scrape_run (source_type, source_file, file_sha256, notes) "
        "VALUES (?, ?, ?, ?) RETURNING run_id",
        [source_type, str(source_file.name), sha, notes],
    ).fetchone()[0]


def finish_run(con, run_id: int, row_count: int) -> None:
    con.execute("UPDATE core.scrape_run SET row_count = ? WHERE run_id = ?", [row_count, run_id])


def platform_id(con, code: str) -> int:
    row = con.execute("SELECT platform_id FROM ref.platform WHERE platform_code = ?", [code]).fetchone()
    if not row:
        raise ValueError(f"unknown platform {code!r}; add it to ref.platform")
    return row[0]


def resolve_cities(con, raw_names) -> dict[str, int]:
    """Map raw city spellings to city_id, auto-creating unseen cities (is_seeded = FALSE).

    Auto-created cities have no region/tier; `qcom report` lists them so the seed can be extended.
    """
    out = {}
    for raw in {r for r in raw_names if isinstance(r, str) and r.strip()}:
        key = raw.strip().lower()
        row = con.execute("SELECT city_id FROM ref.city_alias WHERE alias = ?", [key]).fetchone()
        if row is None:
            city_id = con.execute(
                "INSERT INTO ref.city (city_name) VALUES (?) "
                "ON CONFLICT (city_name) DO UPDATE SET city_name = excluded.city_name "
                "RETURNING city_id", [raw.strip().title()]).fetchone()[0]
            con.execute("INSERT OR IGNORE INTO ref.city_alias VALUES (?, ?, 'auto')", [key, city_id])
            row = (city_id,)
        out[raw] = row[0]
    return out
