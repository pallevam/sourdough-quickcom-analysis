"""Chat assistant: SQL sandbox and the tool loop, driven by a scripted fake client (no API calls)."""

import sys
from pathlib import Path
from types import SimpleNamespace as NS

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))
import assistant as ai  # noqa: E402

DB = ROOT / "data" / "qcom.duckdb"
pytestmark = pytest.mark.skipif(not DB.exists(), reason="run `python -m qcom build` first")


@pytest.fixture(scope="module")
def con():
    c = duckdb.connect(str(DB), read_only=True)
    c.execute("SET enable_external_access = false")
    c.execute("SET lock_configuration = true")
    yield c
    c.close()


@pytest.mark.parametrize("sql", [
    "SELECT platform_code, count(*) FROM mart.sales_daily GROUP BY 1",
    "WITH x AS (SELECT 1 AS a) SELECT * FROM x",
    "FROM ref.model_param",
    "SUMMARIZE mart.v_location",
    "PIVOT (SELECT region, platform_code, est_gmv FROM mart.sales_daily WHERE region <> 'Unknown') "
    "ON platform_code USING sum(est_gmv) GROUP BY region",
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


def test_system_prompt_lists_panel_values_only(con):
    p = ai.build_system_prompt(con)
    assert "mart.sales_daily" in p and "Levain & Loaf" in p
    assert "Cadbury" not in p  # real Zepto catalogue brands stay out of the value lists


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


def test_tool_loop_runs_sql_and_returns_answer(con):
    client = FakeClient([
        _resp([NS(type="tool_use", id="t1", name="run_sql",
                  input={"sql": "SELECT nope FROM mart.sales_daily", "purpose": "bad"})], "tool_use"),
        _resp([NS(type="tool_use", id="t2", name="run_sql",
                  input={"sql": "SELECT count(*) AS n FROM mart.sales_daily", "purpose": "count rows"})], "tool_use"),
        _resp([NS(type="text", text="There are many rows.")], "end_turn"),
    ])
    a = ai.Assistant(client, con, "system", model="claude-opus-5-5")
    turn = a.ask("How many rows?")
    assert turn.answer == "There are many rows."
    assert [q.error is None for q in turn.queries] == [False, True]
    # The error went back to the model flagged as an error, so it can fix its SQL.
    # history: [question, assistant tool_use, tool results, assistant tool_use, tool results, answer]
    first_results = a.messages[2]["content"][0]
    assert first_results["is_error"] and "nope" in first_results["content"]
    assert client.calls[0]["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in client.calls[0]["betas"]


def test_refusal_and_round_limit(con, monkeypatch):
    a = ai.Assistant(FakeClient([_resp([], "refusal")]), con, "system")
    assert "declined" in a.ask("?").notice

    monkeypatch.setattr(ai, "MAX_TOOL_ROUNDS", 2)
    loop = [_resp([NS(type="tool_use", id=f"t{i}", name="run_sql",
                      input={"sql": "SELECT 1", "purpose": ""})], "tool_use") for i in range(2)]
    a = ai.Assistant(FakeClient(loop), con, "system")
    turn = a.ask("?")
    assert "too many" in turn.notice
    assert a.messages[-1]["role"] == "assistant"  # history stays valid for the next question


def test_haiku_default_sends_no_fallback(con):
    client = FakeClient([_resp([NS(type="text", text="ok")], "end_turn")])
    ai.Assistant(client, con, "system").ask("hi")
    assert client.calls[0]["model"] == "claude-haiku-5-5"
    assert "fallbacks" not in client.calls[0] and "betas" not in client.calls[0]
