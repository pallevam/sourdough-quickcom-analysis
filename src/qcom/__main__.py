"""CLI: python -m qcom <command>. Run with no arguments for help."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from qcom import db
from qcom.ingest import kaggle, snapshots

QUERIES = db.SQL_DIR / "queries" / "observations.sql"


def parse_queries(path: Path) -> list[tuple[str, str, str]]:
    blocks = re.split(r"^-- name: ", path.read_text(), flags=re.M)[1:]
    out = []
    for b in blocks:
        name, rest = b.split("\n", 1)
        title = re.search(r"^-- title: (.*)$", rest, re.M).group(1)
        out.append((name.strip(), title.strip(), rest.strip().rstrip(";")))
    return out


def cmd_load_kaggle(con, args):
    d = Path(args.dir)
    if (d / "zepto.zip").exists():
        kaggle.load_zepto(con, d / "zepto.zip")
    if (d / "bigbasket.zip").exists():
        kaggle.load_bigbasket(con, d / "bigbasket.zip")
    if (d / "blinkit.zip").exists():
        kaggle.load_blinkit(con, d / "blinkit.zip")
    if (d / "swiggy.zip").exists():
        kaggle.load_restaurants(con, d / "swiggy.zip", "swiggy_food", "swiggy_file.csv")
    if (d / "zomato.zip").exists():
        kaggle.load_restaurants(con, d / "zomato.zip", "zomato_food", "zomato_dataset.csv")


def cmd_load(con, args):
    for f in args.snapshots or []:
        snapshots.load_snapshots(con, Path(f), source_type=args.source_type)
    for f in args.search or []:
        snapshots.load_search(con, Path(f), source_type=args.source_type)


def cmd_report(con, args):
    export = Path(args.export) if args.export else None
    if export:
        export.mkdir(parents=True, exist_ok=True)
    for name, title, sql in parse_queries(QUERIES):
        if args.only and name not in args.only:
            continue
        print(f"\n=== {title}  [{name}]")
        rel = con.sql(sql)
        rel.show(max_rows=40, max_width=200)
        if export:
            rel.write_csv(str(export / f"{name}.csv"))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="qcom")
    ap.add_argument("--db", default=str(db.DEFAULT_DB))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create schema, seed reference data, create views")
    p = sub.add_parser("load-kaggle", help="load zepto/swiggy/zomato zips")
    p.add_argument("--dir", default=str(db.ROOT))
    p = sub.add_parser("load", help="load scrape files that follow the contract")
    p.add_argument("--snapshots", nargs="*")
    p.add_argument("--search", nargs="*")
    p.add_argument("--source-type", default="own_scrape", choices=["own_scrape", "vendor", "kaggle"])
    p = sub.add_parser("report", help="run source observation reports")
    p.add_argument("--only", nargs="*")
    p.add_argument("--export", help="also write each result to <dir>/<name>.csv")
    p = sub.add_parser("build", help="init + load source datasets")
    p.add_argument("--dir", default=str(db.ROOT))
    args = ap.parse_args(argv)

    con = db.connect(args.db)
    db.init(con)
    if args.cmd == "load-kaggle":
        cmd_load_kaggle(con, args)
    elif args.cmd == "load":
        cmd_load(con, args)
    elif args.cmd == "report":
        cmd_report(con, args)
    elif args.cmd == "build":
        cmd_load_kaggle(con, args)
    con.close()


if __name__ == "__main__":
    main()
