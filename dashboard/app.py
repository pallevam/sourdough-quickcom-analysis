"""India quick-commerce explorer.  Run: streamlit run dashboard/app.py

Sidebar filters compile into literal SQL (dashboard/market_queries.py). Each chart shows the
exact query it ran under "SQL for DuckDB UI", ready to paste into `duckdb -ui`.
"""

from __future__ import annotations

import hmac
import os
import subprocess
import sys
from pathlib import Path

import anthropic
import duckdb
import pandas as pd
import plotly.express as px
import streamlit as st

import assistant as ai
import market_queries as mq

ROOT = Path(__file__).resolve().parents[1]
DB = Path(os.environ.get("QCOM_DB", ROOT / "data" / "qcom.duckdb"))

# Reference palette (dataviz skill): categorical slots in fixed order, light + dark steps.
CAT_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
CAT_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]

st.set_page_config(page_title="India quick-commerce explorer", page_icon=":material/storefront:", layout="wide")


def secret(name: str, default: str | None = None) -> str | None:
    """Streamlit secrets (.streamlit/secrets.toml or the host's secrets UI), then environment."""
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:  # no secrets file at all
        pass
    return os.environ.get(name, default)


def password_gate() -> None:
    """If APP_PASSWORD is set, ask for it once per browser session before showing anything."""
    expected = secret("APP_PASSWORD")
    if not expected or st.session_state.get("authed"):
        return
    st.title("India quick-commerce explorer")
    pw = st.text_input("Password", type="password")
    if pw and hmac.compare_digest(pw, expected):
        st.session_state.authed = True
        st.rerun()
    elif pw:
        st.error("Wrong password.")
    st.stop()


password_gate()


@st.cache_resource(show_spinner="Building the database (first start only, ~10 s)...")
def ensure_db() -> None:
    """A fresh server has only the repo: build data/qcom.duckdb from the Kaggle zips + synthetic panel."""
    if not DB.exists():
        subprocess.run([sys.executable, "-m", "qcom", "--db", str(DB), "build"], cwd=ROOT, check=True,
                       env={**os.environ, "PYTHONPATH": str(ROOT / "src")})


@st.cache_resource
def connection() -> duckdb.DuckDBPyConnection:
    ensure_db()
    # Read-only: coexists with the DuckDB UI; `qcom build` needs both closed.
    con = duckdb.connect(str(DB), read_only=True)
    # Sandbox for the SQL playground and the chat assistant: no local files, no network,
    # and the settings can't be switched back from SQL.
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")
    return con


@st.cache_data(show_spinner=False, ttl=300, max_entries=128)
def run(sql: str) -> pd.DataFrame:
    with connection().cursor() as cur:
        return cur.sql(sql).df()


def is_dark() -> bool:
    return getattr(st.context.theme, "type", None) == "dark"


def palette() -> list[str]:
    return CAT_DARK if is_dark() else CAT_LIGHT


def sql_box(sql: str, key: str) -> None:
    with st.expander("SQL for DuckDB UI"):
        st.code(sql, language="sql")
        st.download_button("Download .sql", sql, file_name=f"{key}.sql", key=f"dl_{key}")


def chart(fig, height=380, top=36, key=None):
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=top, b=8), legend_title_text="",
                      hoverlabel=dict(namelength=-1))
    st.plotly_chart(fig, width="stretch", theme="streamlit", key=key, alt=fig.layout.title.text or "Query result chart")


# All category exploration uses actual observations, never a sales proxy.
st.title("India quick-commerce explorer")
st.caption("Explore observed assortment, brands, pack prices and availability across categories.")

with st.sidebar:
    dataset = st.radio("Data", ["Real observations", "Synthetic demo"], key="dataset")
    synthetic = dataset == "Synthetic demo"
    st.caption("Empty selections include all available values. Filters apply to the four exploration views.")

coverage = run(mq.coverage(synthetic))
if synthetic:
    st.warning("Synthetic demo: fictional bakery brands and simulated observations, not market evidence.")
else:
    st.info("Historical samples, not the whole market. BigBasket is e-grocery; Swiggy/Zomato files are "
            "restaurant data. There are no real Instamart products or sales figures in this warehouse.")

if coverage.empty:
    st.info("No observations loaded for this dataset. Load source files or choose another dataset.")
    st.stop()

f = mq.MarketFilters(synthetic=synthetic)

def choices(column):
    return run(mq.options(f, column)).iloc[:, 0].tolist()

with st.sidebar:
    suffix = "_demo" if synthetic else "_real"
    f.platforms = st.multiselect("Platform", choices("platform_code"), key="platforms" + suffix)
    f.categories = st.multiselect("Category (source label or inferred fallback)", choices("category"), key="categories" + suffix)
    f.brands = st.multiselect("Brand", choices("brand_name"), key="brands" + suffix)
    f.markets = st.multiselect("Market / city", choices("market"), key="markets" + suffix,
                               help="Unknown means no usable location; it does not establish national availability.")
    f.search = st.text_input("Product search", key="search" + suffix, placeholder="Coffee, shampoo, pet food…")
    if st.toggle("Restrict record dates", key="restrict_dates" + suffix):
        lo, hi = coverage.first_record.min().date(), coverage.last_record.max().date()
        dates = st.date_input("Record dates", (lo, hi), min_value=lo, max_value=hi, key="dates" + suffix)
        if len(dates) != 2:
            st.info("Choose both a start and end date.")
            st.stop()
        f.date_from, f.date_to = dates
    st.caption("Zepto and BigBasket dates come from ZIP metadata, not verified observation dates.")
    if st.button("Refresh query cache"):
        st.cache_data.clear()
        st.rerun()


def table(sql, key):
    df = run(sql)
    st.dataframe(df, hide_index=True, alt=key.replace("_", " "))
    st.download_button("Download CSV", df.to_csv(index=False), file_name=key + ".csv", mime="text/csv", key="csv_" + key)
    sql_box(sql, key)
    return df


def assortment():
    st.subheader("Assortment overview")
    df = run(mq.products(f))
    if df.empty:
        st.info("No listings match these filters. Clear a selection or widen the record dates.")
        return
    with st.container(horizontal=True):
        st.metric("Observed listings", f"{len(df):,}", border=True)
        st.metric("Platforms", df.platform_code.nunique(), border=True)
        st.metric("Category labels", df.category.nunique(), border=True)
        st.metric("Known brand labels", df.loc[df.brand_name.ne("Unknown"), "brand_name"].nunique(), border=True)
    st.caption("Counts are platform listings, not unique cross-platform products or market share. "
               "Brand names may be inferred. Source category labels differ between platforms; inferred fallbacks need review.")
    summary = run(mq.summary(f))
    top = summary.head(20).copy()
    top["Category / platform"] = top.category + " · " + top.platform_code
    st.bar_chart(top, x="Category / platform", y="listings", horizontal=True,
                 alt="Top 20 observed category and platform combinations by listing count")
    table(mq.summary(f), "category_coverage")
    st.subheader("Product explorer")
    st.caption("One row per platform listing. Prices average its valid observations in the selected slice. "
               "Pack sizes differ; prices are not unit-normalised. Stock percentages use known stock observations only.")
    table(mq.products(f), "products")


def brands_prices():
    st.subheader("Brands and pack prices")
    st.caption("Compare within a category and source period. Each listing contributes one average pack price "
               "to the median and quartiles. Counts describe catalogue presence, not brand sales share.")
    df = table(mq.summary(f, "brand_name"), "brand_comparison")
    if not df.empty:
        top = df.head(20).copy()
        top["Brand / platform"] = top.brand_name.fillna("Unknown") + " · " + top.platform_code
        st.bar_chart(top, x="Brand / platform", y="listings", horizontal=True,
                     alt="Top 20 brand and platform combinations by observed listings")


def trends_availability():
    st.subheader("Price and availability observations")
    st.caption("Only observation timestamps appear in trends. Archive-dated catalogues are excluded. "
               "Daily averages are observation-weighted and can change with assortment mix; they are not inflation measures.")
    df = run(mq.trend(f))
    if df.empty or df.record_date.nunique() < 2:
        st.info("This selection has no multi-day observations with a usable observation timestamp.")
    else:
        df["Series"] = df.platform_code + " · " + df.market
        metric = st.selectbox("Trend metric", ["avg_pack_price", "in_stock_pct", "listings"],
                              format_func={"avg_pack_price": "Average pack price (Rs)",
                                           "in_stock_pct": "In stock (%)", "listings": "Observed listings"}.get)
        st.line_chart(df, x="record_date", y=metric, color="Series", alt="Daily observations by platform and market")
        sql_box(mq.trend(f), "observed_trend")
    st.caption("Missing stock data stays blank. Stock-outs can reflect supply issues or scrape gaps as well as demand. "
               "The historical Blinkit Kolkata sample records no stock-outs; validate before comparing cities.")
    table(mq.availability(f), "availability")


def research():
    st.subheader("Category research shortlist")
    st.write("Use these signals to choose where to investigate: assortment breadth, brand choice, price spread, "
             "discounting and observed availability. None establishes unmet demand on its own.")
    st.caption("Grouped within each platform and category. Prices are per pack; broad price spreads may simply "
               "reflect different products or pack sizes. Missing categories and uneven sampling can look like gaps.")
    table(mq.summary(f), "research_shortlist")
    with st.expander("What to validate before a brand decision"):
        st.markdown("- A comparable product and pack-size segment.\n"
                    "- Repeated observations across target pincodes and competing platforms.\n"
                    "- Demand evidence beyond assortment counts and stock-outs.\n"
                    "- Margin, platform fees, fulfilment, returns and repeat purchase.\n"
                    "- Source provenance and permitted dataset use.")


def sources():
    st.subheader("Source coverage and data quality")
    st.caption("Whole selected dataset; exploration filters do not apply to this view.")
    table(mq.coverage(synthetic), "source_coverage")
    st.markdown("**Category quality:** source labels are preserved in the explorer. Where absent, the existing "
                "rule-based taxonomy is a fallback, not a verified classification.\n\n"
                "**Units:** the legacy parser treats millilitres as grams. The generic explorer therefore uses "
                "pack prices and does not present universal Rs/100 g comparisons.\n\n"
                "**Freshness:** BigBasket and Zepto record dates are archive timestamps. These samples must not "
                "be read as current platform inventory or a common-period price comparison.")
    st.subheader("Restaurant context (real data)")
    st.caption("Food-delivery listings are kept separate from product assortment and brand-opportunity metrics.")
    table("SELECT p.platform_code, count(*) AS restaurant_rows, count(DISTINCT city_id) AS cities "
          "FROM core.restaurant_listing r JOIN ref.platform p USING (platform_id) GROUP BY 1", "restaurant_coverage")
    with st.expander("Load provenance"):
        st.dataframe(run("SELECT source_type, source_file, loaded_at, row_count, notes FROM core.scrape_run ORDER BY run_id"),
                     hide_index=True, alt="File ingestion provenance")


def playground():
    st.caption("Read-only SQL over the whole warehouse. Start with the current filtered category query.")
    sql = st.text_area("SQL", mq.summary(f), height=260)
    if st.button("Run", type="primary"):
        result = ai.run_sql(connection(), sql)
        if result.error:
            st.error(result.error)
        else:
            st.dataframe(result.df, hide_index=True, alt="SQL query result")
            st.caption(f"Up to {ai.ROWS_TO_UI:,} rows; queries time out after {ai.QUERY_TIMEOUT_S} seconds.")


EXAMPLES = [
    "Which categories have the most real Zepto listings?",
    "Compare brand assortment within BigBasket beverages.",
    "Which Blinkit categories have the weakest observed availability?",
    "What data is missing before I can assess a brand opportunity?",
]

@st.cache_resource
def system_prompt() -> str:
    return ai.build_system_prompt(connection())


def chart_for(df: pd.DataFrame):
    """A quick chart when a result has an obvious shape: time series or one category vs one number."""
    if df is None or len(df) < 2 or len(df.columns) < 2:
        return None
    first, nums = df.columns[0], df.select_dtypes("number").columns.tolist()
    if not nums:
        return None
    if pd.api.types.is_datetime64_any_dtype(df[first]) or "date" in first or "week" in first or "month" in first:
        long = df.melt(first, nums[:6]) if len(df.columns) > 2 and len(nums) > 1 else None
        if long is not None:
            fig = px.line(long, x=first, y="value", color="variable", color_discrete_sequence=palette())
        else:
            fig = px.line(df, x=first, y=nums[0], color=df.columns[1] if df.columns[1] not in nums else None,
                          color_discrete_sequence=palette())
        return fig
    if len(df.columns) == 2 and first not in nums and len(df) <= 30:
        return px.bar(df, x=nums[0], y=first, orientation="h", color_discrete_sequence=palette()[:1])
    return None


def show_turn(turn: ai.Turn, n: int) -> None:
    with st.chat_message("user"):
        st.markdown(turn.question)
    with st.chat_message("assistant"):
        if turn.answer:
            st.markdown(turn.answer)
        if turn.notice:
            st.warning(turn.notice)
        ok = [r for r in turn.queries if r.error is None]
        if ok and (fig := chart_for(ok[-1].df)) is not None:
            chart(fig, height=320, top=10, key=f"chat_fig_{n}")
        if turn.queries:
            with st.expander(f"How I got this ({len(turn.queries)} quer{'y' if len(turn.queries) == 1 else 'ies'})"):
                for i, r in enumerate(turn.queries, 1):
                    st.markdown(f"**{i}. {r.purpose or 'Query'}**")
                    st.code(r.sql, language="sql")
                    if r.error:
                        st.caption(f"Error (the assistant retried): {r.error[:300]}")
                    else:
                        st.dataframe(r.df.head(200), alt="Assistant query result", hide_index=True, width="stretch", key=f"chat_df_{n}_{i}")


def ask_tab():
    key = secret("ANTHROPIC_API_KEY")
    if not key:
        st.info("The assistant needs an Anthropic API key. Locally: `export ANTHROPIC_API_KEY=...` before "
                "`streamlit run`, or put `ANTHROPIC_API_KEY = \"...\"` in `.streamlit/secrets.toml`. "
                "On Streamlit Cloud: app settings → Secrets.")
        return
    limit = int(secret("CHAT_MAX_QUESTIONS", "25"))
    if "assistant" not in st.session_state:
        st.session_state.assistant = ai.Assistant(
            anthropic.Anthropic(api_key=key), connection(), system_prompt(),
            model=secret("CHAT_MODEL", ai.DEFAULT_MODEL), effort=secret("CHAT_EFFORT", "medium"))
        st.session_state.turns = []
    turns: list[ai.Turn] = st.session_state.turns

    top = st.columns([5, 1])
    top[0].markdown("Ask a question in plain English. The assistant writes and runs SQL on the whole "
                    "dataset (the sidebar filters don't apply here) and shows its working.")
    if top[1].button("New chat", disabled=not turns):
        del st.session_state.assistant
        st.rerun()

    if not turns:
        st.caption("Try one of these:")
        cols = st.columns(len(EXAMPLES))
        for c, ex in zip(cols, EXAMPLES):
            if c.button(ex, width="stretch"):
                st.session_state.pending = ex
    for n, t in enumerate(turns):
        show_turn(t, n)

    used = len(turns)
    question = st.chat_input(f"Ask about the data ({limit - used} questions left)" if used < limit
                             else "Question limit reached for this session", disabled=used >= limit)
    question = question or st.session_state.pop("pending", None)
    if question and used < limit:
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"), st.status("Thinking...", expanded=False) as status:
            turn = st.session_state.assistant.ask(
                question, on_query=lambda r: status.update(label=f"Querying: {r.purpose or 'running SQL'}"))
            status.update(label=f"Done ({len(turn.queries)} queries)", state="complete")
        turns.append(turn)
        st.rerun()
    if turns:
        tokens = sum(t.input_tokens for t in turns), sum(t.output_tokens for t in turns)
        st.caption(f"Session usage: {tokens[0]:,} input / {tokens[1]:,} output tokens.")


SECTIONS = {"Assortment": assortment, "Brands and prices": brands_prices,
            "Trends and availability": trends_availability, "Research shortlist": research,
            "Data coverage": sources, "Ask": ask_tab, "SQL playground": playground}
selected_view = st.selectbox("Explore", list(SECTIONS), key="view")
SECTIONS[selected_view]()
