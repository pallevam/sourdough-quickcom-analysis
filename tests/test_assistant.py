"""Chat assistant: SQL sandbox and the tool loop, driven by a scripted fake client (no API calls)."""

import os
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))
import assistant as ai  # noqa: E402

DB = Path(os.environ.get("QCOM_DB", ROOT / "data" / "qcom-observations-v2.duckdb"))  # override to test another build
pytestmark = pytest.mark.skipif(not DB.exists(), reason="run `python -m qcom build` first")


@pytest.fixture(scope="module")
def con():
    c = duckdb.connect(str(DB), read_only=True)
    c.execute("SET enable_external_access = false")
    c.execute("SET lock_configuration = true")
    yield c
    c.close()


@pytest.mark.parametrize("sql", [
    "SELECT listing_id, count(*) FROM core.listing_snapshot GROUP BY 1",
    "WITH x AS (SELECT 1 AS a) SELECT * FROM x",
    "FROM ref.platform",
    "SUMMARIZE mart.v_location",
    "PIVOT (SELECT platform_code, listing_id FROM mart.v_listing) "
    "ON platform_code USING count(listing_id)",
])
def test_read_queries_allowed(con, sql):
    r = ai.run_sql(con, sql)
    assert r.error is None and len(r.df) > 0


@pytest.mark.parametrize("sql", [
    "CREATE TABLE t AS SELECT 1",
    "SELECT 1; SELECT 2",
    "SET enable_external_access = true",
    "ATTACH 'other.db'",
    "SELECT * FROM read_csv('/etc/hosts')",
    "COPY (SELECT 1) TO 'out.csv'",
])
def test_writes_files_and_config_blocked(con, sql):
    assert ai.run_sql(con, sql).error


def test_system_prompt_uses_observations(con):
    p = ai.build_system_prompt(con)
    assert "archive dates" in p and "sales" in p
    assert "Levain & Loaf" not in p


def _resp(content, stop):
    return NS(content=content, stop_reason=stop,
              usage=NS(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0))


class FakeClient:
    """Plays back scripted responses and records what the assistant sent."""

    def __init__(self, script):
        self.script, self.calls = list(script), []
        self.beta = NS(messages=NS(create=self._create))
        self.messages = NS(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        return self.script.pop(0)


def test_report_only_ignores_fabricated_prose(con):
    client = FakeClient([_resp([
        NS(type="text", text="Revenue is 999 crore"),
        NS(type="tool_use", name="select_report", input={"report": "platforms", "filters": {}}),
    ], "tool_use")])
    turn = ai.Assistant(client, con, "system").ask("Show platforms")
    assert "999" not in turn.answer
    assert len(turn.queries) == 2 and not turn.queries[0].error
    assert set(turn.queries[0].df.platform_code) == {"blinkit", "zepto", "bigbasket"}


@pytest.mark.parametrize("blocks", [
    [NS(type="text", text="Revenue is 999 crore")],
    [NS(type="tool_use", name="run_sql", input={"sql": "SELECT 999"})],
    [NS(type="tool_use", name="select_report", input={"report": "unsupported", "filters": {}})],
    [NS(type="tool_use", name="select_report", input={"report": "products", "filters": {"sql": "SELECT 999"}})],
])
def test_unsupported_output_cannot_become_answer(con, blocks):
    client = FakeClient([_resp(blocks, "tool_use")])
    turn = ai.Assistant(client, con, "system").ask("Revenue?")
    assert not turn.answer and not turn.queries and "Insufficient evidence" in turn.notice


def test_empty_and_escaped_filter(con):
    args = {"report": "products", "filters": {"search": "' OR TRUE --"}}
    client = FakeClient([_resp([NS(type="tool_use", name="select_report", input=args)], "tool_use")])
    turn = ai.Assistant(client, con, "system").ask("Find product")
    assert turn.queries[0].df.empty and "No source records" in turn.answer


def test_truncation_is_explicit(con, monkeypatch):
    monkeypatch.setattr(ai, "ROWS_TO_UI", 2)
    result = ai.run_sql(con, "SELECT * FROM range(3)")
    assert result.truncated and len(result.df) == 2
