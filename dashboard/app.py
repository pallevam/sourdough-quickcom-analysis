"""Sourdough quick-commerce explorer.  Run: streamlit run dashboard/app.py

Sidebar filters compile into literal SQL (dashboard/queries.py). Each chart shows the
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
import queries as q

ROOT = Path(__file__).resolve().parents[1]
DB = Path(os.environ.get("QCOM_DB", ROOT / "data" / "qcom.duckdb"))

# Reference palette (dataviz skill): categorical slots in fixed order, light + dark steps.
CAT_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
CAT_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]
OTHER = "#8f8e8a"
SEQ_BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]

st.set_page_config(page_title="Sourdough quick-commerce explorer", page_icon="🍞", layout="wide")


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
    st.title("🍞 Sourdough on quick commerce")
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


@st.cache_data(show_spinner=False)
def run(sql: str) -> pd.DataFrame:
    return connection().cursor().sql(sql).df()


def is_dark() -> bool:
    return getattr(st.context.theme, "type", None) == "dark"


def palette() -> list[str]:
    return CAT_DARK if is_dark() else CAT_LIGHT


def ring() -> str:
    """Surface-coloured 2px ring that separates overlapping marks."""
    return "#0e1117" if is_dark() else "#ffffff"


@st.cache_data
def global_order(dim: str) -> list[str]:
    """Entities by all-time GMV, so a series keeps its color whatever the filters."""
    expr = q.SEGMENT if dim == "segment" else f"CAST({dim} AS VARCHAR)"
    return run(f"SELECT {expr} AS v FROM mart.sales_daily WHERE region <> 'Unknown' "
               f"GROUP BY 1 ORDER BY sum(est_gmv) DESC")["v"].tolist()


def color_map(dim: str, present) -> dict[str, str]:
    """Top-8 entities own a fixed slot; any other shown entity borrows an unused slot."""
    pal, order = palette(), global_order(dim)
    cmap = {v: pal[i] for i, v in enumerate(order[:8])}
    free = [c for c in pal if c not in {cmap[v] for v in present if v in cmap}]
    for v in present:
        if v == "Other":
            cmap[v] = OTHER
        elif v not in cmap:
            cmap[v] = free.pop(0) if free else OTHER
    return cmap


def sql_box(sql: str, key: str) -> None:
    with st.expander("SQL for DuckDB UI"):
        st.code(sql, language="sql")
        st.download_button("Download .sql", sql, file_name=f"{key}.sql", key=f"dl_{key}")


def chart(fig, height=380, top=36, key=None):
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=top, b=8), legend_title_text="",
                      hoverlabel=dict(namelength=-1))
    st.plotly_chart(fig, width="stretch", theme="streamlit", key=key)


def heatmap(df, x, y, z, title, fmt=".0f"):
    wide = df.pivot_table(index=y, columns=x, values=z, aggfunc="sum")
    fig = px.imshow(wide, color_continuous_scale=SEQ_BLUE, aspect="auto", text_auto=fmt, title=title)
    fig.update_traces(xgap=2, ygap=2)
    fig.update_xaxes(side="top", title=None)
    fig.update_yaxes(title=None)
    fig.update_layout(title_y=0.99, title_yanchor="top")
    chart(fig, height=max(320, 34 * len(wide) + 110), top=80)  # room for title + top axis labels


def inr(v) -> str:
    if v is None or pd.isna(v):
        return "–"
    return f"Rs {v / 1e7:,.2f} Cr" if v >= 1e7 else f"Rs {v / 1e5:,.1f} L" if v >= 1e5 else f"Rs {v:,.0f}"


# ------------------------------------------------------------------ sidebar --

@st.cache_data
def options(sql: str) -> list:
    return [v for v in run(sql).iloc[:, 0].tolist() if v is not None]


with st.sidebar:
    dataset = st.radio("Dataset", list(q.DATASETS), format_func=q.DATASETS.get,
                       help="The sourdough trends need sales estimates, which only the synthetic panel has. "
                            "The real Blinkit file has prices and stock-outs for 5 cities, but no sourdough.")
DS = q.dataset_sql(dataset)
SALES = q.has_sales(dataset)

bounds = run(f"SELECT min(sales_date) AS lo, max(sales_date) AS hi FROM mart.sales_daily "
             f"WHERE region <> 'Unknown' AND {DS}").iloc[0]
lo, hi = bounds.lo.date(), bounds.hi.date()

with st.sidebar:
    st.header("Filters")
    k = f"_{dataset}"  # per-dataset widget state: options and date bounds differ between datasets
    picked = st.date_input("Date range", (lo, hi), min_value=lo, max_value=hi, key="dates" + k)
    d_from, d_to = (picked if isinstance(picked, (tuple, list)) and len(picked) == 2 else (lo, hi))
    scope = st.radio("Category scope", list(q.SCOPES), format_func=q.SCOPES.get, index=0 if SALES else 2,
                     key="scope" + k, help=None if SALES else "This Blinkit file has no sourdough products.")
    metrics = q.metrics_for(dataset)
    metric = st.selectbox("Metric", list(metrics), format_func=metrics.get, key="metric" + k)
    grain = st.segmented_control("Time grain", q.GRAINS, default="week" if SALES else "day", key="grain" + k) \
        or ("week" if SALES else "day")
    st.divider()
    platforms = st.multiselect("Platform", options(
        f"SELECT DISTINCT platform_code FROM mart.sales_daily WHERE region <> 'Unknown' AND {DS} ORDER BY 1"),
        key="platforms" + k)
    regions = st.multiselect("Region", options(
        f"SELECT DISTINCT region FROM mart.sales_daily WHERE region <> 'Unknown' AND {DS} ORDER BY 1"), key="regions" + k)
    tiers = st.multiselect("City tier", options(
        f"SELECT DISTINCT tier FROM mart.sales_daily WHERE tier IS NOT NULL AND {DS} ORDER BY 1"), key="tiers" + k)
    region_sql = f" AND {q._in('region', regions)}" if regions else ""
    tier_sql = f" AND {q._in('tier', tiers)}" if tiers else ""
    markets = st.multiselect("Market / city", options(
        f"SELECT DISTINCT market FROM mart.sales_daily WHERE region <> 'Unknown' AND {DS}{region_sql}{tier_sql} "
        f"ORDER BY 1"), key="markets" + k)
    scope_sql = {"sourdough": "is_sourdough", "bakery": "l1 = 'Bakery'", "all": "TRUE"}[scope]
    l3s = st.multiselect("Sub-category", options(
        f"SELECT DISTINCT l3 FROM mart.sales_daily WHERE region <> 'Unknown' AND {DS} AND {scope_sql} ORDER BY 1"),
        key="l3s" + k)
    brands = st.multiselect("Brand", options(
        f"SELECT DISTINCT brand_name FROM mart.sales_daily WHERE region <> 'Unknown' AND {DS} AND {scope_sql} "
        f"ORDER BY 1"), key="brands" + k)
    flours = st.multiselect("Flour (sourdough)", options(
        f"SELECT DISTINCT coalesce(flour, '(unspecified)') FROM mart.sales_daily WHERE is_sourdough AND {DS} "
        f"ORDER BY 1"), key="flours" + k)

f = q.Filters(date_from=d_from, date_to=d_to, platforms=platforms, regions=regions, tiers=tiers,
              markets=markets, scope=scope, l3s=l3s, brands=brands, flours=flours, metric=metric,
              grain=grain, dataset=dataset)
metric_label = q.METRICS[metric]

# ------------------------------------------------------------------- header --

st.title("Sourdough on quick commerce")
check = run(q.synthetic_check(f)).iloc[0]
if SALES:
    st.warning("**Synthetic data in view.** Brands, volumes and trends come from the simulated panel "
               "(`python -m qcom synth`): use them to explore the method, not as market facts.", icon="⚠️")
else:
    st.info("**Real data: Blinkit home-page prices** for 5 cities, 17 Jan – 1 Feb 2024 (Kaggle, CC0). "
            "Prices, discounts and stock-outs are real; there are **no sales figures and no sourdough** in "
            "this file. See the Real catalogues tab for real sourdough listings.", icon="ℹ️")

# ----------------------------------------------------------------- overview --
def overview():
    k = run(q.kpis(f)).iloc[0]
    cols = st.columns(5)
    fmt = lambda v, f_: f_.format(v) if pd.notna(v) else "–"
    if SALES:
        share = run(q.sourdough_share(f)).iloc[0, 0]
        cols[0].metric("Est. units", fmt(k.units, "{:,.0f}"))
        cols[1].metric("Est. GMV", inr(k.gmv))
        cols[2].metric("Sourdough share of bakery GMV", fmt(share, "{:.1f}%"))
        cols[3].metric("Avg Rs / 100 g", fmt(k.rs_per_100g, "{:,.1f}"))
        cols[4].metric("Availability", fmt(k.availability_pct, "{:.1f}%"))
        sql_box(q.kpis(f) + ";\n\n" + q.sourdough_share(f), "kpis")
    else:
        cols[0].metric("Products listed", fmt(k.skus, "{:,.0f}"))
        cols[1].metric("Avg selling price", fmt(k.avg_price, "Rs {:,.0f}"))
        cols[2].metric("Avg discount", fmt(k.avg_discount_pct, "{:.1f}%"))
        cols[3].metric("Avg Rs / 100 g", fmt(k.avg_rs_per_100g, "{:,.1f}"))
        cols[4].metric("In stock", fmt(k.availability_pct, "{:.1f}%"))
        sql_box(q.kpis(f), "kpis")

    color = st.segmented_control("Colour by", list(q.COLOR_BY), format_func=q.COLOR_BY.get,
                                 default="segment" if scope == "bakery" else ("platform_code" if SALES else "market"),
                                 key="color_" + dataset) or "platform_code"
    sql = q.trend(f, color)
    df = run(sql)
    fig = px.line(df, x="period", y="value", color="series", title=f"{metric_label} by {q.COLOR_BY[color].lower()}",
                  color_discrete_map=color_map(color, df.series.unique()),
                  labels={"value": metric_label, "period": "", "series": q.COLOR_BY[color]})
    fig.update_traces(line_width=2)
    fig.update_layout(hovermode="x unified")
    chart(fig, 420)
    sql_box(sql, "trend")

# ---------------------------------------------------------------- geography --
def geography():
    left, right = st.columns(2)
    with left:
        sql = q.market_platform(f)
        heatmap(run(sql), "platform_code", "market", "value", f"{metric_label}: market × platform", ",.0f")
        sql_box(sql, "market_platform")
    with right:
        sql = q.city_map(f)
        df = run(sql)
        fig = px.scatter_geo(df, lat="lat", lon="lon", size="value", hover_name="city_name",
                             hover_data={"market": True, "value": ":,.0f", "lat": False, "lon": False},
                             title=f"{metric_label} by city", size_max=40)
        fig.update_traces(marker=dict(color=palette()[0], opacity=0.75, line=dict(width=2, color=ring())))
        fig.update_geos(lataxis_range=[6, 37], lonaxis_range=[67, 98], showcountries=True,
                        showland=True, landcolor="rgba(128,128,128,0.08)", bgcolor="rgba(0,0,0,0)",
                        projection_type="mercator")
        chart(fig, 520)
        sql_box(sql, "city_map")
    if not SALES:
        return
    sql = q.tier_share(f)
    df = run(sql)
    fig = px.line(df, x="month", y="sd_share_of_bakery_gmv_pct", color="tier", markers=True,
                  title="Sourdough share of bakery GMV by city tier (full months)",
                  color_discrete_map={t: palette()[i] for i, t in enumerate(["Tier 1", "Tier 2", "Tier 3"])},
                  labels={"sd_share_of_bakery_gmv_pct": "% of bakery GMV", "month": ""})
    fig.update_traces(line_width=2, marker_size=8)
    fig.update_layout(hovermode="x unified")
    chart(fig)
    sql_box(sql, "tier_share")

# ------------------------------------------------------------ brands & price --
def brands_price():
    if SALES:
        sql = q.brand_share(f)
        heatmap(run(sql), "brand_name", "market", "gmv_share_pct", "Brand share of GMV within each market (%)", ".1f")
        sql_box(sql, "brand_share")

    sql = q.price_ladder(f)
    df = run(sql)
    fig = px.scatter(df, x="rs_per_100g", y="canonical_name", color="platform_code",
                     color_discrete_map=color_map("platform_code", df.platform_code.unique()),
                     hover_data=["brand_name", "avg_selling_price", "avg_discount_pct"],
                     title="Price ladder: Rs per 100 g by product and platform",
                     labels={"rs_per_100g": "Rs per 100 g", "canonical_name": "", "platform_code": "Platform"})
    fig.update_traces(marker=dict(size=10, line=dict(width=2, color=ring())))
    fig.update_yaxes(categoryorder="total ascending")
    chart(fig, max(380, 26 * df.canonical_name.nunique() + 120))
    sql_box(sql, "price_ladder")

    if not SALES:
        return
    sql = q.discount_response(f)
    df = run(sql)
    fig = px.bar(df, x="discount_band", y="avg_units_per_sku_day", color="segment", barmode="group",
                 category_orders={"discount_band": ["0-5%", "5-12%", "12%+"]},
                 color_discrete_map=color_map("segment", df.segment.unique()),
                 title="Promo response: avg units per SKU-day by discount band",
                 labels={"avg_units_per_sku_day": "Units / SKU-day", "discount_band": "Discount", "segment": ""},
                 hover_data=["sku_location_days"])
    fig.update_layout(bargap=0.35, bargroupgap=0.08, showlegend=df.segment.nunique() > 1)
    chart(fig)
    sql_box(sql, "discount_response")

# ------------------------------------------------------------- availability --
def availability():
    sql = q.oos_by_category(f)
    heatmap(run(sql), "category", "market", "oos_pct", "Out-of-stock rate (%) by market and category", ".1f")
    sql_box(sql, "oos_by_category")
    if not SALES:
        st.caption("The Blinkit file has one reading per city per day, so there's no time-of-day view.")
        return

    sql = q.oos_heatmap(f)
    df = run(sql)
    df["weekday"] = pd.Categorical(df.weekday, ["Monday", "Tuesday", "Wednesday", "Thursday",
                                                "Friday", "Saturday", "Sunday"], ordered=True)
    df["hour"] = df.hour.map(lambda h: f"{h:02d}:00")
    heatmap(df, "hour", "weekday", "oos_pct", "Out-of-stock rate (%) by scrape hour and weekday", ".1f")
    st.caption("Scrapes run at 07, 12, 17 and 22h. Late-day stock-outs = demand the store couldn't serve.")
    sql_box(sql, "oos_heatmap")

    sql = q.lost_demand(f)
    st.subheader("Where stock-outs cost the most")
    st.dataframe(run(sql), width="stretch", hide_index=True)
    sql_box(sql, "lost_demand")

# ------------------------------------------------------------------- search --
def search_tab():
    terms = options("SELECT DISTINCT search_term FROM core.search_result ORDER BY 1")
    term = st.selectbox("Search term", terms, index=terms.index("sourdough") if "sourdough" in terms else 0)
    sql = q.search_share(f, term)
    df = run(sql)
    st.caption("Search data covers platform, market and date filters only.")
    fig = px.bar(df, x="share_of_top10_pct", y="brand_name", color="brand_name", facet_col="platform_code",
                 orientation="h", color_discrete_map=color_map("brand_name", df.brand_name.unique()),
                 title=f'Share of top-10 results for "{term}"',
                 labels={"share_of_top10_pct": "% of top-10 slots", "brand_name": ""},
                 hover_data=["avg_rank", "sponsored_slot_pct"])
    fig.for_each_annotation(lambda a: a.update(text=a.text.split("=")[-1]))
    fig.update_layout(showlegend=False, bargap=0.3)
    chart(fig)
    st.dataframe(df.round(1), width="stretch", hide_index=True)
    sql_box(sql, "search")

# ------------------------------------------------------------ catalogues --
def catalogues():
    st.markdown("**Real product listings** from BigBasket (catalogue, Jun 2022, CC BY-NC-SA 4.0), "
                "Blinkit (5 cities, Jan 2024, CC0) and Zepto (catalogue). Not affected by the sidebar filters.")
    sql = q.catalogue_summary()
    st.dataframe(run(sql), width="stretch", hide_index=True, column_config={
        "from_date": st.column_config.DateColumn("From"), "to_date": st.column_config.DateColumn("To")})
    sql_box(sql, "catalogue_summary")
    only_sd = st.toggle("Sourdough only", value=True, key="cat_sd")
    sql = q.catalogue_bakery(only_sd)
    df = run(sql)
    st.caption(f"{len(df):,} listings. Rs/100 g needs a pack size, which BigBasket titles usually omit.")
    st.dataframe(df, width="stretch", hide_index=True,
                 column_config={"last_seen": st.column_config.DateColumn("Last seen")})
    sql_box(sql, "catalogue_bakery")

# ---------------------------------------------------------------- real data --
def real_data():
    st.markdown("**Real data.** Premium food culture per market, from Swiggy and Zomato *restaurant* "
                "listings (Kaggle). The penetration column comes from the dataset picked in the sidebar.")
    sql = q.affinity(f)
    df = run(sql)
    st.dataframe(df, width="stretch", hide_index=True, column_config={
        "affinity_index": st.column_config.ProgressColumn("Affinity index", min_value=float(df.affinity_index.min()),
                                                          max_value=float(df.affinity_index.max()), format="%.2f")})
    sql_box(sql, "affinity")

# ----------------------------------------------------------- sql playground --
def playground():
    st.markdown("Read-only connection: any `SELECT`, `SUMMARIZE`, `DESCRIBE`, `PIVOT` works; writes are rejected. "
                "Start from any query above.")
    default = q.trend(f, "platform_code")
    sql = st.text_area("SQL", default, height=260)
    if st.button("Run", type="primary"):
        try:
            rel = connection().cursor().sql(sql)
            if rel is None:
                st.success("Statement executed.")
            else:
                out = rel.limit(10_000).df()
                st.caption(f"{len(out):,} rows (capped at 10,000)")
                st.dataframe(out, width="stretch", hide_index=True)
        except duckdb.Error as e:
            st.error(str(e))


# --------------------------------------------------------------- ask (chat) --
EXAMPLES = [
    "Which city is growing fastest for sourdough?",
    "How much more does sourdough cost than regular bread, per 100 g?",
    "Which brand leads in South India, and on which platform?",
    "Where do stock-outs lose the most sales?",
    "Which cities have the most premium food culture (real data)?",
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
                        st.dataframe(r.df.head(200), hide_index=True, width="stretch", key=f"chat_df_{n}_{i}")


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


# ----------------------------------------------------------------- layout --
SECTIONS = {"💬 Ask": ask_tab, "Overview": overview, "Geography": geography, "Brands & price": brands_price,
            "Availability": availability, "Search": search_tab, "Real catalogues": catalogues,
            "Restaurant data": real_data, "SQL playground": playground}
if not SALES:  # no search scrapes in the real Blinkit file
    SECTIONS.pop("Search")
if check.rows == 0:
    st.info("No rows match these filters. The Ask, catalogue, restaurant and SQL tabs are still available.")
    SECTIONS = {k: SECTIONS[k] for k in ("💬 Ask", "Real catalogues", "Restaurant data", "SQL playground")}
for tab, render in zip(st.tabs(list(SECTIONS)), SECTIONS.values()):
    with tab:
        render()
