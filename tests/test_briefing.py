"""盤前簡報測試 — graph、報告組版、快取決策。全部用假模型，不需要 vLLM。"""

import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

import config
from stock_agent import briefing as briefing_mod
from stock_agent import report as report_mod
from stock_agent import screen, watchlist
from stock_agent.workflow import briefing as nodes
from stock_agent.workflow.briefing import build_briefing_graph


class RaisingLLM:
    def invoke(self, _prompt):
        raise RuntimeError("模型爆了")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch, stub_cache):
    monkeypatch.setattr(config, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(config, "WATCHLIST_PATH", tmp_path / "watchlist.json")
    monkeypatch.setattr(briefing_mod, "_graph", None)
    return tmp_path


def _llm(*responses):
    return FakeListChatModel(responses=list(responses) or ["綜合觀察測試"])


class TestBreadth:
    def test_counts_add_up(self, day):
        b = screen.breadth(day["quotes"])
        assert b["advancing"] + b["declining"] + b["unchanged"] == b["total"]

    def test_excludes_etf_and_missing(self, day):
        """把 ETF 算進上漲家數會讓這個指標失去意義。"""
        b = screen.breadth(day["quotes"])
        common = [q for q in day["quotes"] if screen.is_common_stock(q["code"])]
        assert b["total"] <= len(common)

    def test_detects_limit_up(self):
        quotes = [
            {"code": "1111", "change_pct": 10.0},
            {"code": "2222", "change_pct": 9.95},
            {"code": "3333", "change_pct": 5.0},
            {"code": "4444", "change_pct": -10.0},
        ]
        b = screen.breadth(quotes)
        assert b["limit_up"] == 2   # 10.00 與 9.95 都算漲停（容差）
        assert b["limit_down"] == 1

    def test_ignores_none_change_pct(self, day):
        b = screen.breadth(day["quotes"])
        halted = [q for q in day["quotes"] if q["code"] == "9299"]
        assert halted and halted[0]["change_pct"] is None
        assert b["total"] == len([
            q for q in day["quotes"]
            if screen.is_common_stock(q["code"]) and q["change_pct"] is not None
        ])


class TestGraphShape:
    def test_three_parallel_branches_join_on_compose(self):
        graph = build_briefing_graph(_llm()).get_graph()
        edges = {(e.source, e.target) for e in graph.edges}
        for branch in ("market_snapshot", "watchlist_read", "market_movers"):
            assert ("__start__", branch) in edges
            assert (branch, "compose_briefing") in edges
        assert ("compose_briefing", "persist_report") in edges


class TestSelectCandidatesByCodes:
    def test_codes_path_bypasses_screening(self, day):
        """自選股走這條，重用同一套 fan-out。"""
        from stock_agent.workflow.nodes import select_candidates

        out = select_candidates({"criteria": {"codes": ["2330", "2317"]}, "date": None})
        assert [q["code"] for q in out["candidates"]] == ["2330", "2317"]

    def test_screening_path_still_works(self):
        from stock_agent.workflow.nodes import select_candidates

        out = select_candidates({"criteria": {"sort_by": "volume", "limit": 2}, "date": None})
        assert len(out["candidates"]) == 2


class TestWatchlistRead:
    def test_empty_watchlist_short_circuits_without_llm(self):
        """清單為空時不該讓 subgraph 空轉。"""
        out = nodes.watchlist_read(RaisingLLM(), {"date": None})
        assert out["watchlist"]["codes"] == []
        assert out["watchlist"]["readings"] == []

    def test_reads_each_holding(self):
        watchlist.add(["2330", "2317"])
        out = nodes.watchlist_read(_llm("符合|||收在高點"), {"date": None})
        assert len(out["watchlist"]["readings"]) == 2
        assert out["watchlist"]["focus"]  # 判斷軸要帶出來，否則「符合」沒有意義

    def test_reports_delisted_holdings(self, isolated):
        (isolated / "watchlist.json").write_text(
            json.dumps({"codes": ["2330", "1234"]}), encoding="utf-8"
        )
        out = nodes.watchlist_read(_llm("符合|||x"), {"date": None})
        assert out["watchlist"]["not_found"] == ["1234"]


class TestComposeAndPersist:
    def _state(self, day):
        snapshot = nodes.market_snapshot({"date": None})
        return {
            "report_date": "2026-08-04",
            **snapshot,
            **nodes.market_movers({"date": None}),
            **nodes.watchlist_read(_llm("符合|||x"), {"date": None}),
        }

    def test_llm_failure_still_produces_full_report(self, day):
        state = {**self._state(day), "observation": ""}
        state["observation"] = nodes.compose_briefing(RaisingLLM(), state)["observation"]
        assert "產生失敗" in state["observation"]

        report = nodes.persist_report(state)["report"]
        # 各段數據都是確定性產生的，模型掛掉也還在
        assert report.startswith("# 盤前提要 2026-08-04（依據 2026-08-03 收盤）")
        assert "## 大盤" in report and "## 市場焦點" in report
        assert "43,386.41" in report

    def test_writes_file_named_by_report_date(self, day, isolated):
        state = {**self._state(day), "observation": "測試觀察"}
        path = nodes.persist_report(state)["report_path"]
        assert path.endswith("2026-08-04-premarket.md")
        assert (isolated / "reports" / "2026-08-04-premarket.md").is_file()


class TestReportModule:
    def test_report_date_and_trade_date_are_distinct(self):
        text = report_mod.compose(
            report_date="2026-08-04",
            trade_date="2026-08-03",
            index={"index_name": "加權", "close": 1.0, "change": 1.0, "change_pct": 1.0},
            breadth={"total": 1, "advancing": 1, "declining": 0, "unchanged": 0,
                     "limit_up": 0, "limit_down": 0},
            watchlist={"codes": [], "readings": []},
            movers={},
            observation="x",
        )
        assert "盤前提要 2026-08-04（依據 2026-08-03 收盤）" in text
        assert report_mod.DISCLAIMER in text

    def test_existing_trade_date_round_trips(self):
        report_mod.save("2026-08-04", "# 盤前提要 2026-08-04（依據 2026-08-03 收盤）\n")
        assert report_mod.existing_trade_date("2026-08-04") == "2026-08-03"

    def test_existing_trade_date_none_when_absent(self):
        assert report_mod.existing_trade_date("1999-01-01") is None


class TestGetBriefingCaching:
    def test_reads_cache_without_touching_graph(self, monkeypatch):
        report_mod.save("2026-08-04", "# 盤前提要 2026-08-04（依據 2026-08-03 收盤）\n內容")
        monkeypatch.setattr(
            briefing_mod, "_get_graph", lambda: pytest.fail("有存檔時不該跑 graph")
        )
        result = briefing_mod.get_briefing(report_date="2026-08-04")
        assert result["source"] == "cache"
        assert result["trade_date"] == "2026-08-03"

    def test_refresh_skips_when_no_new_trading_day(self):
        """假日與週末排程執行時就是這個情形 — 不該覆寫既有報告。"""
        report_mod.save("2026-08-04", "# 盤前提要 2026-08-04（依據 2026-08-03 收盤）\n舊內容")
        result = briefing_mod.get_briefing(report_date="2026-08-04", refresh=True)
        assert result["source"] == "skipped"
        assert "舊內容" in result["text"]

    def test_rejects_report_date_before_trade_date(self):
        """報告日早於資料日是不可能成立的組合，放行會產出日期自相矛盾的報告。"""
        with pytest.raises(ValueError, match="早於可取得的交易日"):
            briefing_mod.get_briefing(report_date="2020-01-02", refresh=True)

    def test_generates_when_absent(self, monkeypatch):
        monkeypatch.setattr(
            briefing_mod, "_graph", build_briefing_graph(_llm("符合|||x", "觀察"))
        )
        result = briefing_mod.get_briefing(report_date="2026-08-04")
        assert result["source"] == "generated"
        assert result["text"].startswith("# 盤前提要 2026-08-04")


class TestCliContract:
    def _run(self, capsys, argv):
        import sys

        from stock_agent.cli import main

        sys.argv = ["cli", *argv]
        code = main()
        return code, capsys.readouterr()

    def test_briefing_json_is_pure_json(self, capsys, monkeypatch):
        monkeypatch.setattr(
            briefing_mod, "_graph", build_briefing_graph(_llm("符合|||x", "觀察"))
        )
        code, captured = self._run(capsys, ["briefing", "--json", "--report-date", "2026-08-04"])
        payload = json.loads(captured.out)
        assert code == 0
        assert payload["text"].startswith("# 盤前提要")

    def test_briefing_error_exits_nonzero(self, capsys):
        code, captured = self._run(
            capsys, ["briefing", "--json", "--report-date", "2020-01-02", "--refresh"]
        )
        assert code == 1
        assert "早於可取得的交易日" in json.loads(captured.out)["error"]

    def test_watchlist_add_then_list(self, capsys):
        code, _ = self._run(capsys, ["watchlist", "add", "2330"])
        assert code == 0
        code, captured = self._run(capsys, ["watchlist", "--json"])
        assert json.loads(captured.out)["codes"] == ["2330"]

    def test_watchlist_add_without_keys_exits_2(self, capsys):
        code, _ = self._run(capsys, ["watchlist", "add"])
        assert code == 2
