"""Natural-language Q&A over the warehouse: Claude writes DuckDB SQL, we run it, Claude answers.

Kept free of Streamlit so it can be tested on its own. Safety comes from three layers:
  1. the connection is read-only with external access disabled and config locked (app.py),
     so SQL can't write, read local files or reach the network
  2. run_sql() accepts a single SELECT-type statement and interrupts it after a timeout
  3. a per-session question cap in the app bounds API spend
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

import anthropic
import duckdb
import pandas as pd

DEFAULT_MODEL = "claude-haiku-5-5"   # cheapest current Claude; set CHAT_MODEL to change
# Models with server-side refusal fallback. Claude Haiku 5.5 has none (the parameter is ignored or rejected).
FALLBACK_MODELS = ("claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1")
MAX_TOOL_ROUNDS = 8
ROWS_TO_MODEL = 60          # rows of each result Claude sees
ROWS_TO_UI = 2_000          # rows kept for display
QUERY_TIMEOUT_S = 15
ALLOWED = {duckdb.StatementType.SELECT, duckdb.StatementType.EXPLAIN}

# Objects described to the model, in a fixed order so the system prompt is byte-stable (cacheable).
SCHEMA_OBJECTS = [
    ("mart", "sales_daily", "START HERE. One row per SKU x pincode x day, every dimension pre-joined."),
    ("mart", "sales_weekly_geo", "Weekly rollup by platform x geography x category. days_observed < 7 = partial week."),
    ("mart", "share_of_search", "Search-page share of top-10 slots by brand, per week x platform x market x term."),
    ("mart", "market_affinity", "REAL data: premium food culture per market from Swiggy/Zomato restaurant listings."),
    ("mart", "v_listing", "Platform SKU with its product, brand and category."),
    ("mart", "v_location", "Pincode with city, state, region, tier, market, lat/lon."),
    ("core", "listing_snapshot", "Raw scrape rows: price, stock, inventory, rating_count per SKU x pincode x time."),
    ("core", "restaurant_listing", "REAL data: Swiggy/Zomato food-delivery restaurants (not quick commerce)."),
    ("ref", "model_param", "Assumptions behind the sales estimates."),
]

PANEL = "FROM mart.sales_daily WHERE is_synthetic"                    # synthetic sourdough panel
REAL_BLINKIT = "FROM mart.sales_daily WHERE NOT is_synthetic AND region <> 'Unknown'"  # real Blinkit 2024
ENUMS = [  # (label, sql) - small value lists that help the model write correct filters
    ("SYNTHETIC panel platform_code", f"SELECT DISTINCT platform_code {PANEL} ORDER BY 1"),
    ("SYNTHETIC panel region", f"SELECT DISTINCT region {PANEL} ORDER BY 1"),
    ("SYNTHETIC panel market", f"SELECT DISTINCT market {PANEL} ORDER BY 1"),
    ("SYNTHETIC panel tier", f"SELECT DISTINCT tier {PANEL} ORDER BY 1"),
    ("SYNTHETIC panel brand_name", f"SELECT DISTINCT brand_name {PANEL} ORDER BY 1"),
    ("SYNTHETIC panel l2 > l3", f"SELECT DISTINCT l2 || ' > ' || l3 {PANEL} ORDER BY 1"),
    ("SYNTHETIC panel flour (sourdough)", f"SELECT DISTINCT coalesce(flour, 'NULL') {PANEL} AND is_sourdough ORDER BY 1"),
    ("SYNTHETIC panel sales_date range", f"SELECT min(sales_date) || ' to ' || max(sales_date) {PANEL}"),
    ("REAL Blinkit market", f"SELECT DISTINCT market {REAL_BLINKIT} ORDER BY 1"),
    ("REAL Blinkit dates", f"SELECT count(DISTINCT sales_date) || ' dates, ' || min(sales_date) || ' to ' || max(sales_date) {REAL_BLINKIT}"),
    ("REAL Blinkit l1 categories", f"SELECT DISTINCT l1 {REAL_BLINKIT} ORDER BY 1"),
    ("REAL catalogues in mart.v_listing (platform_code: products)", "SELECT string_agg(platform_code || ': ' || n, ', ') FROM (SELECT l.platform_code, count(*) AS n FROM mart.v_listing l WHERE NOT l.brand_is_synthetic GROUP BY 1 ORDER BY 1)"),
    ("REAL sourdough products (all sources)", "SELECT string_agg(platform_code || ' / ' || brand_name || ' / ' || canonical_name, '; ') FROM mart.v_listing WHERE is_sourdough AND NOT brand_is_synthetic"),
    ("mart.market_affinity markets (real)", "SELECT count(*) || ' markets, e.g. ' || string_agg(market, ', ' ORDER BY affinity_index DESC) FILTER (WHERE rn <= 8) FROM (SELECT *, row_number() OVER (ORDER BY affinity_index DESC) rn FROM mart.market_affinity)"),
]

GUIDANCE = """\
You answer questions about a quick-commerce analytics warehouse (Blinkit, Swiggy Instamart, Zepto and BigBasket
in India), focused on sourdough bread. The people asking are friends and family of the analyst: answer in plain English,
lead with the answer, keep it short, and use Indian number formats (lakh = 1e5, crore = 1e7; Rs for rupees).

How to work:
- Use the run_sql tool to query DuckDB. One SELECT per call; CTEs, PIVOT, SUMMARIZE, window functions all work.
  Prefer mart.sales_daily. Aggregate in SQL rather than pulling raw rows. Round numbers in SQL.
- If a query errors, read the error, fix the SQL and retry. Don't guess numbers you did not query.
- When the question is ambiguous, pick the most sensible reading, say which one you used, and answer.
- Questions unrelated to this data: say briefly that you can only answer questions about this dataset.

Facts you must respect:
- Platforms do not publish sales. est_units / est_gmv are ESTIMATES (inventory depletion or rating velocity,
  see est_method); est_units_demand adds sales lost to stock-outs. Say "estimated" when quoting them.
- The warehouse mixes SYNTHETIC and REAL data. ALWAYS filter on is_synthetic, because platform_code
  'blinkit' exists in both. The data sources:
  1. SYNTHETIC sourdough panel (is_synthetic = TRUE): fictional brands and made-up numbers for Blinkit,
     Instamart and Zepto, Apr-Sep 2026, 13 markets, with sales estimates, stock and search. It is the only
     source with sourdough sales or trends. Whenever an answer uses it, add a one-line note that these are
     illustrative demo numbers, not real market figures.
  2. REAL Blinkit prices (NOT is_synthetic AND region <> 'Unknown'): Kaggle, CC0. Blinkit home-page
     products in 5 cities, one reading per city per day, 17 Jan - 1 Feb 2024. Real prices, MRP, discounts
     and in-stock flags. NO sales (est_units / est_gmv are NULL), NO sourdough, pincode unknown (city-wide
     locations), no Blinkit categories (many products are 'Uncategorised'). Kolkata shows no stock-outs at
     all, which looks like a gap in the source data: say so if it matters.
  3. REAL catalogues, single date, no location (region = 'Unknown' in mart.sales_daily; use mart.v_listing
     joined to core.listing_snapshot for prices): BigBasket (Jun 2022, 27.5k products, CC BY-NC-SA 4.0,
     real brands, pack sizes mostly missing; includes 3 real sourdough products from The Baker's Dozen)
     and Zepto (765 products, no bread).
  4. REAL restaurant data: mart.market_affinity and core.restaurant_listing (Swiggy/Zomato food delivery).
- For a question about "real" sourdough, the only real sourdough rows are the 3 BigBasket products.
- Average prices across many products move with the product mix: compare like-for-like products when
  the question is about price changes.
- Weeks/months at the edges of the date range are partial: use full periods for growth comparisons.
- est_gmv is in rupees. price_per_100g is Rs per 100 g. Shares: compute them, don't sum percentages.
"""


def build_system_prompt(con: duckdb.DuckDBPyConnection) -> str:
    cur = con.cursor()
    parts = [GUIDANCE, "\n# Schema (DuckDB)\n"]
    for schema, name, note in SCHEMA_OBJECTS:
        cols = cur.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = ? AND table_name = ? ORDER BY ordinal_position", [schema, name]).fetchall()
        parts.append(f"## {schema}.{name}: {note}\n" + ", ".join(f"{c} {t}" for c, t in cols) + "\n")
    parts.append("\n# Values present\n")
    for label, sql in ENUMS:
        vals = [str(r[0]) for r in cur.execute(sql).fetchall()]
        parts.append(f"- {label}: {', '.join(vals)}\n")
    return "".join(parts)


RUN_SQL_TOOL = {
    "name": "run_sql",
    "description": (
        "Run one read-only DuckDB SELECT against the warehouse and get back the column names, row count "
        f"and up to {ROWS_TO_MODEL} rows as CSV. The query is interrupted after {QUERY_TIMEOUT_S}s."),
    "input_schema": {
        "type": "object",
        "properties": {
            "sql": {"type": "string", "description": "A single DuckDB SELECT statement."},
            "purpose": {"type": "string", "description": "Under 12 words: what this query finds out."},
        },
        "required": ["sql", "purpose"],
        "additionalProperties": False,
    },
    "strict": True,
}


@dataclass
class QueryResult:
    sql: str
    purpose: str
    df: pd.DataFrame | None = None
    error: str | None = None

    def for_model(self) -> str:
        if self.error:
            return f"ERROR: {self.error}"
        head = self.df.head(ROWS_TO_MODEL)
        note = "" if len(self.df) <= ROWS_TO_MODEL else f" (showing first {ROWS_TO_MODEL})"
        return f"{len(self.df)} rows{note}\n{head.to_csv(index=False)}"


def run_sql(con: duckdb.DuckDBPyConnection, sql: str, purpose: str = "") -> QueryResult:
    res = QueryResult(sql=sql.strip(), purpose=purpose)
    cur = con.cursor()
    try:
        stmts = cur.extract_statements(res.sql)
        # A standalone PIVOT expands into several internal statements (enum creation + SELECT),
        # so allow it by keyword; the read-only, locked connection still blocks any write.
        is_pivot = res.sql.lstrip("( \n\t").upper().startswith(("PIVOT", "UNPIVOT"))
        if not is_pivot and (len(stmts) != 1 or stmts[0].type not in ALLOWED):
            res.error = "Only a single SELECT statement is allowed."
            return res
        timer = threading.Timer(QUERY_TIMEOUT_S, cur.interrupt)
        timer.start()
        try:
            res.df = cur.sql(res.sql).limit(ROWS_TO_UI).df()
        finally:
            timer.cancel()
    except duckdb.InterruptException:
        res.error = f"Query took longer than {QUERY_TIMEOUT_S}s and was stopped. Aggregate more or filter first."
    except duckdb.Error as e:
        res.error = str(e).split("\n\nLINE")[0][:800]
    return res


@dataclass
class Turn:
    """What the UI shows for one question."""
    question: str
    answer: str = ""
    queries: list[QueryResult] = field(default_factory=list)
    notice: str | None = None          # refusal / limit / API error, shown instead of or after the answer
    input_tokens: int = 0
    output_tokens: int = 0


class Assistant:
    """One conversation. `messages` is append-only API history (thinking blocks echoed back unchanged)."""

    def __init__(self, client: anthropic.Anthropic, con: duckdb.DuckDBPyConnection, system: str,
                 model: str = DEFAULT_MODEL, effort: str = "medium"):
        self.client, self.con, self.model, self.effort = client, con, model, effort
        self.system = [{"type": "text", "text": system}]
        self.messages: list[dict] = []

    def _create(self):
        params = dict(
            model=self.model,
            max_tokens=16000,
            system=self.system,
            tools=[RUN_SQL_TOOL],
            messages=self.messages,
            output_config={"effort": self.effort},
            cache_control={"type": "ephemeral"},        # caches system prompt + history between rounds
        )
        if self.model in FALLBACK_MODELS:               # a safety decline is retried on a fallback model
            return self.client.beta.messages.create(
                **params, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        return self.client.messages.create(**params)

    def ask(self, question: str, on_query=None) -> Turn:
        turn = Turn(question)
        self.messages.append({"role": "user", "content": question})
        for _ in range(MAX_TOOL_ROUNDS):
            try:
                resp = self._create()
            except anthropic.RateLimitError:
                turn.notice = "The assistant is busy right now (rate limited). Try again in a minute."
                break
            except anthropic.APIStatusError as e:
                turn.notice = f"The assistant couldn't answer (API error {e.status_code})."
                break
            except anthropic.APIConnectionError:
                turn.notice = "Couldn't reach the assistant service. Check the internet connection."
                break
            turn.input_tokens += resp.usage.input_tokens + (resp.usage.cache_read_input_tokens or 0) \
                + (resp.usage.cache_creation_input_tokens or 0)
            turn.output_tokens += resp.usage.output_tokens

            if resp.stop_reason == "refusal":
                turn.notice = "The assistant declined to answer that one. Try rephrasing the question."
                break
            self.messages.append({"role": "assistant", "content": resp.content})
            text = "".join(b.text for b in resp.content if b.type == "text").strip()

            if resp.stop_reason != "tool_use":
                turn.answer = text
                if resp.stop_reason == "max_tokens":
                    turn.notice = "The answer was cut off. Try a narrower question."
                break

            results = []
            for block in resp.content:
                if block.type != "tool_use":
                    continue
                args = block.input if isinstance(block.input, dict) else {}
                if block.name != "run_sql" or not isinstance(args.get("sql"), str):
                    results.append({"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                                    "content": "Unknown tool or missing 'sql' argument."})
                    continue
                qr = run_sql(self.con, args["sql"], args.get("purpose", ""))
                turn.queries.append(qr)
                if on_query:
                    on_query(qr)
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": qr.for_model(), "is_error": qr.error is not None})
            self.messages.append({"role": "user", "content": results})
        else:
            turn.notice = "That needed too many queries. Try breaking it into smaller questions."
            # Close the open tool round so the history stays valid for the next question.
            self.messages.append({"role": "assistant", "content": "I stopped before finishing that answer."})
        return turn
