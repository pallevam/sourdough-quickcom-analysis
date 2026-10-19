"""Ask selects reviewed reports; only database results and fixed text reach the UI."""
from __future__ import annotations
import threading
from dataclasses import dataclass, field
from datetime import date
import anthropic
import duckdb
import pandas as pd
import market_queries as mq

DEFAULT_MODEL = "claude-haiku-5-5"
ROWS_TO_MODEL = 60
ROWS_TO_UI = 2_000
QUERY_TIMEOUT_S = 15
ALLOWED = {duckdb.StatementType.SELECT, duckdb.StatementType.EXPLAIN}
REPORTS = {
    "products": ("Observed products", mq.products),
    "categories": ("Observed categories", mq.summary),
    "brands": ("Observed brands", lambda f: mq.summary(f, "brand_name")),
    "platforms": ("Observed platforms", lambda f: mq.summary(f, "platform_code")),
    "availability": ("Observed availability", mq.availability),
    "trends": ("Observed price and stock trends", mq.trend),
    "coverage": ("Source coverage (whole warehouse)", lambda f: mq.coverage()),
}
GUIDANCE = """Select one report for the user's question using select_report. Never write SQL or answers.
Use unsupported for sales, revenue, market share, demand, predictions, recommendations, current
inventory, or questions that the available historical observations cannot answer. Do not substitute
listing counts for sales. Categories, brands and pack sizes are source labels. Missing labels and
stock stay unknown. Zepto/BigBasket dates are archive dates; trends exclude them. Prices are per pack.
Extract only filters explicitly requested. Use exact source labels from the supplied values; if a
requested label is absent, retain it so the result is empty. Each question is independent.
Reports: products, categories, brands, platforms, availability, trends, coverage (unfiltered).
"""

def build_system_prompt(con):
    parts = [GUIDANCE]
    for column in ("platform_code", "category", "brand_name", "market"):
        values = con.sql(mq.options(mq.MarketFilters(), column)).fetchall()
        parts.append(f"{column}: " + repr([r[0] for r in values]))
    return "\n".join(parts)

SELECT_REPORT_TOOL = {
    "name": "select_report", "description": "Select a supported historical observation report or unsupported.",
    "input_schema": {"type": "object", "properties": {
        "report": {"type": "string", "enum": [*REPORTS, "unsupported"]},
        "filters": {"type": "object", "properties": {
            **{k: {"type": "array", "items": {"type": "string"}} for k in ("platforms", "categories", "brands", "markets")},
            **{k: {"type": "string"} for k in ("search", "date_from", "date_to")},
        }, "additionalProperties": False},
    }, "required": ["report", "filters"], "additionalProperties": False},
}


def report_query(args):
    if not isinstance(args, dict) or set(args) != {"report", "filters"}:
        raise ValueError("Invalid report")
    if args["report"] not in REPORTS:
        raise ValueError("Unsupported question")
    filters = args["filters"]
    if not isinstance(filters, dict) or set(filters) - set(mq.MarketFilters.__dataclass_fields__):
        raise ValueError("Invalid filters")
    values = dict(filters)
    for key, value in values.items():
        if key in ("platforms", "categories", "brands", "markets"):
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                raise ValueError("Invalid filter values")
        elif not isinstance(value, str):
            raise ValueError("Invalid filter value")
        elif key.startswith("date_"):
            values[key] = date.fromisoformat(value)
    f = mq.MarketFilters(**values)
    if f.date_from and f.date_to and f.date_from > f.date_to:
        raise ValueError("Invalid dates")
    if args["report"] == "coverage" and filters:
        raise ValueError("Coverage is unfiltered")
    title, builder = REPORTS[args["report"]]
    return title, builder(f), repr(filters) if filters else "All available records"

@dataclass
class QueryResult:
    sql: str
    purpose: str
    df: pd.DataFrame | None = None
    error: str | None = None
    truncated: bool = False

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
            res.df = cur.sql(res.sql).limit(ROWS_TO_UI + 1).df()
            res.truncated = len(res.df) > ROWS_TO_UI
            res.df = res.df.head(ROWS_TO_UI)
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
    def __init__(self, client, con, system, model=DEFAULT_MODEL, effort="medium"):
        self.client, self.con, self.system, self.model = client, con, system, model

    def ask(self, question, on_query=None):
        turn = Turn(question)
        try:
            resp = self.client.messages.create(
                model=self.model, max_tokens=1200, system=self.system,
                tools=[SELECT_REPORT_TOOL], tool_choice={"type": "tool", "name": "select_report"},
                messages=[{"role": "user", "content": question}])
            turn.input_tokens = resp.usage.input_tokens
            turn.output_tokens = resp.usage.output_tokens
            blocks = [b for b in resp.content if b.type == "tool_use"]
            if resp.stop_reason != "tool_use" or len(blocks) != 1 or blocks[0].name != "select_report":
                raise ValueError("No supported report")
            title, sql, scope = report_query(blocks[0].input)
            result = run_sql(self.con, sql, title)
            turn.queries.append(result)
            if on_query:
                on_query(result)
            if result.error:
                turn.notice = "The report could not be loaded. No answer is available."
            elif result.df.empty:
                turn.answer = "No source records match this report. This does not establish absence in the market."
            else:
                turn.answer = f"{title}. Selected scope: {scope}. Results below are historical source records."
                turn.queries.append(run_sql(self.con, mq.coverage(), "Source dates and coverage (whole warehouse)"))
        except (ValueError, TypeError, KeyError):
            turn.notice = "Insufficient evidence: this question could not be mapped to a supported observation report."
        except anthropic.APIError:
            turn.notice = "The assistant service is unavailable. No answer is available."
        return turn
