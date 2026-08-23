"""map-reduce 掃描 graph 的測試。

全部用假模型，不需要 vLLM — graph 的組裝方式（llm 由外部注入）就是為了讓
這件事可能。fan-out 分支數、失敗隔離、輸出解析都在這裡驗證。
"""

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langgraph.types import Send

from stock_agent.workflow import nodes
from stock_agent.workflow.graph import build_scan_graph
from stock_agent.workflow.state import VERDICTS, ReadTask


class RaisingLLM:
    """模擬模型呼叫失敗。"""

    def invoke(self, _prompt):
        raise RuntimeError("模型爆了")


@pytest.fixture
def quote(day):
    return next(q for q in day["quotes"] if q["code"] == "2330")


def _task(quote, focus="收在最高的股票"):
    return ReadTask(focus=focus, trade_date="2026-08-03", quote=quote)


class TestGraphShape:
    def test_nodes_and_conditional_fan_out(self):
        graph = build_scan_graph(FakeListChatModel(responses=["符合|||x"])).get_graph()
        assert {"select_candidates", "read_one", "aggregate", "summarise", "no_candidates"} <= set(
            graph.nodes
        )
        conditional = {(e.source, e.target) for e in graph.edges if e.conditional}
        assert ("select_candidates", "read_one") in conditional
        assert ("select_candidates", "no_candidates") in conditional


class TestFanOut:
    def test_one_send_per_candidate(self, day):
        state = {"candidates": day["quotes"][:4], "focus": "f", "trade_date": "2026-08-03"}
        sends = nodes.fan_out(state)
        assert len(sends) == 4
        assert all(isinstance(s, Send) and s.node == "read_one" for s in sends)

    def test_branch_payload_carries_only_its_own_stock(self, day):
        """分支之間互不可見 — 一檔的判讀不能被另一檔汙染。"""
        state = {"candidates": day["quotes"][:2], "focus": "f", "trade_date": "2026-08-03"}
        first = nodes.fan_out(state)[0].arg
        assert first["quote"]["code"] == day["quotes"][0]["code"]
        assert "candidates" not in first

    def test_routes_to_short_circuit_when_empty(self):
        state = {"candidates": [], "focus": "f", "trade_date": "2026-08-03"}
        assert nodes.fan_out(state) == "no_candidates"


class TestReadOne:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("符合|||收在最高", "符合"),
            ("部分符合|||量能普通", "部分符合"),
            ("不符合|||收在低點", "不符合"),
            ("  符合 ||| 前後有空白  ", "符合"),
        ],
    )
    def test_parses_delimited_output(self, quote, raw, expected):
        llm = FakeListChatModel(responses=[raw])
        reading = nodes.read_one(llm, _task(quote))["readings"][0]
        assert reading["verdict"] == expected
        assert reading["error"] == ""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("這檔部分符合，量能普通", "部分符合"),
            ("我認為不符合這個條件", "不符合"),
            ("完全符合使用者的條件", "符合"),
        ],
    )
    def test_substring_fallback_prefers_longest_label(self, quote, raw, expected):
        """回歸測試：「符合」是「部分符合」與「不符合」的子字串。

        由短到長比對會把「不符合」誤判成「符合」— 那會直接產出相反的結論。
        """
        llm = FakeListChatModel(responses=[raw])
        assert nodes.read_one(llm, _task(quote))["readings"][0]["verdict"] == expected

    def test_unparseable_output_recorded_as_error(self, quote):
        llm = FakeListChatModel(responses=["無法判斷"])
        reading = nodes.read_one(llm, _task(quote))["readings"][0]
        assert reading["error"].startswith("無法解析模型輸出")
        assert reading["verdict"] == "不符合"  # 保守預設，不會誤報為符合

    def test_llm_failure_isolated_not_raised(self, quote):
        """單一分支失敗不能拋出 — 否則整批 N 檔都沒了。"""
        reading = nodes.read_one(RaisingLLM(), _task(quote))["readings"][0]
        assert "模型爆了" in reading["error"]
        assert reading["code"] == "2330"

    def test_facts_include_derived_metrics_not_raw_only(self, quote):
        facts = nodes._facts(quote)
        for label in ["振幅", "收盤位置", "開盤跳空", "漲跌幅"]:
            assert label in facts


class TestAggregate:
    def test_groups_by_verdict_and_separates_failures(self):
        readings = [
            {"code": "1", "name": "a", "verdict": "符合", "note": "", "error": ""},
            {"code": "2", "name": "b", "verdict": "符合", "note": "", "error": ""},
            {"code": "3", "name": "c", "verdict": "不符合", "note": "", "error": ""},
            {"code": "4", "name": "d", "verdict": "不符合", "note": "", "error": "boom"},
        ]
        out = nodes.aggregate({"readings": readings})
        assert [r["code"] for r in out["grouped"]["符合"]] == ["1", "2"]
        assert [r["code"] for r in out["grouped"]["不符合"]] == ["3"]
        assert [r["code"] for r in out["failed"]] == ["4"]

    def test_all_verdicts_present_even_when_empty(self):
        out = nodes.aggregate({"readings": []})
        assert set(out["grouped"]) == set(VERDICTS)


class TestSummarise:
    def test_degrades_when_llm_fails(self):
        state = {
            "focus": "f",
            "trade_date": "2026-08-03",
            "market_index": {"index_name": "加權", "close": 1.0, "change": 1.0, "change_pct": 1.0},
            "grouped": {"符合": [{"code": "2330", "name": "台積電", "note": "x", "error": ""}]},
            "readings": [
                {"code": "2330", "name": "台積電", "verdict": "符合", "note": "x", "error": ""}
            ],
            "failed": [],
        }
        report = nodes.summarise(RaisingLLM(), state)["report"]
        assert "總結產生失敗" in report
        assert "2330 台積電" in report  # 逐檔結果仍然保留
        # 連總結都失敗時，交易日仍然要在 — 它不是模型產生的
        assert report.startswith("交易日 2026-08-03")


class TestScanPayload:
    """Pi extension 依賴 `scan --json` 的契約，所以契約要有測試守著。"""

    @pytest.fixture(autouse=True)
    def fake_graph(self, monkeypatch, stub_cache):
        from stock_agent import scan

        graph = build_scan_graph(FakeListChatModel(responses=["符合|||收在最高"]))
        monkeypatch.setattr(scan, "_graph", graph)

    def test_payload_shape(self):
        from stock_agent.scan import scan_payload

        payload = scan_payload("收在最高的股票", sort_by="turnover", limit=3)
        assert payload["trade_date"] == "2026-08-03"
        assert payload["scanned"] == 3
        assert payload["counts"]["符合"] == 3
        assert payload["failed"] == []
        assert payload["criteria"]["sort_by"] == "turnover"
        # 交易日必須是 Python 決定性寫出的，不能靠模型記得提
        assert payload["text"].startswith("交易日 2026-08-03")

    def test_limit_clamped_to_max(self):
        from config import MAX_SCAN_LIMIT
        from stock_agent.scan import scan_payload

        payload = scan_payload("f", sort_by="turnover", limit=9999)
        assert payload["criteria"]["limit"] == MAX_SCAN_LIMIT

    def test_cli_json_is_pure_json_exit_zero(self, capsys):
        import json
        import sys

        from stock_agent.cli import main

        sys.argv = ["cli", "scan", "收在最高", "--json", "--limit", "2"]
        code = main()
        payload = json.loads(capsys.readouterr().out)
        assert code == 0
        assert payload["scanned"] == 2

    def test_cli_json_error_exits_nonzero(self, capsys):
        import json
        import sys

        from stock_agent.cli import main

        sys.argv = ["cli", "scan", "f", "--json", "--date", "2020-01-02"]
        code = main()
        payload = json.loads(capsys.readouterr().out)
        assert code == 1
        assert "2020-01-02" in payload["error"]


class TestEndToEnd:
    def test_full_graph_with_fake_llm(self, stub_cache):
        graph = build_scan_graph(FakeListChatModel(responses=["符合|||收在最高"]))
        out = graph.invoke(
            {
                "focus": "收在最高的股票",
                "criteria": {"sort_by": "turnover", "limit": 3},
                "date": None,
                "readings": [],
            }
        )
        assert out["trade_date"] == "2026-08-03"
        assert len(out["readings"]) == len(out["candidates"]) == 3
        assert "逐檔判讀" in out["report"]

    def test_no_candidates_short_circuits_without_calling_llm(self, stub_cache):
        graph = build_scan_graph(RaisingLLM())  # 一旦呼叫模型就會爆
        out = graph.invoke(
            {
                "focus": "f",
                "criteria": {"sort_by": "change_pct", "limit": 5, "min_change_pct": 999},
                "date": None,
                "readings": [],
            }
        )
        assert "沒有符合篩選條件" in out["report"]
        assert out["readings"] == []

    def test_one_failing_branch_does_not_lose_the_batch(self, stub_cache, monkeypatch):
        """關鍵性質：fan-out 的失敗隔離。"""
        original = nodes.read_one
        calls = {"n": 0}

        def flaky(llm, task):
            calls["n"] += 1
            if calls["n"] == 2:
                return original(RaisingLLM(), task)
            return original(llm, task)

        monkeypatch.setattr(nodes, "read_one", flaky)
        graph = build_scan_graph(FakeListChatModel(responses=["符合|||ok"]))
        out = graph.invoke(
            {
                "focus": "f",
                "criteria": {"sort_by": "turnover", "limit": 4},
                "date": None,
                "readings": [],
            }
        )
        assert len(out["readings"]) == 4  # 四檔都有結果
        assert len(out["failed"]) == 1  # 其中一檔標記失敗
        assert len(out["grouped"]["符合"]) == 3  # 其餘照常完成
