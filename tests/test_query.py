"""JSON 查詢層與 CLI 契約測試。

Pi extension 依賴這個契約（stdout 只有 JSON、錯誤時 exit 非 0 且給 {"error":...}），
所以契約本身要有測試守著，不能只靠人工試一次。
"""

import json

import pytest

from stock_agent import query, tools
from stock_agent.cli import main
from stock_agent.sources.cache import DataUnavailable


@pytest.fixture(autouse=True)
def _use_fixture_data(stub_cache):
    """所有測試都走 fixture 資料，不打網路。"""


class TestMarket:
    def test_shape(self):
        result = query.market()
        assert result["trade_date"] == "2026-08-03"
        assert result["market"] == "TWSE"
        assert result["index"]["index_name"] == "發行量加權股價指數"
        assert result["index"]["close"] == 43386.41

    def test_unavailable_date_raises(self):
        with pytest.raises(DataUnavailable):
            query.market("2020-01-02")


class TestQuote:
    def test_finds_and_reports_missing(self):
        result = query.quote(["2330", "鴻海", "9999"])
        assert [q["code"] for q in result["quotes"]] == ["2330", "2317"]
        assert result["not_found"] == ["9999"]

    def test_numbers_are_raw_not_formatted(self):
        """結構化欄位要是可計算的數值，格式化只出現在 text 欄位。"""
        quote = query.quote(["2330"])["quotes"][0]
        assert quote["close"] == 2370.0
        assert quote["prev_close"] == 2425.0
        assert isinstance(quote["volume"], int)

    def test_empty_keys_raises(self):
        with pytest.raises(ValueError):
            query.quote([])


class TestScreen:
    def test_echoes_criteria_back(self):
        """呼叫者要能確認實際套用的條件，而不是猜。"""
        result = query.screen(sort_by="volume", limit=3, min_volume=1000)
        assert result["criteria"] == {
            "sort_by": "volume",
            "order": "desc",
            "limit": 3,
            "min_change_pct": None,
            "min_volume": 1000,
            "common_stock_only": True,
        }
        assert result["count"] == len(result["matches"])

    def test_bad_sort_field_raises(self):
        with pytest.raises(ValueError):
            query.screen(sort_by="pe_ratio")


class TestTextFieldMatchesToolOutput:
    """text 欄位與 Python agent 看到的文字必須一致 — 兩者共用 render.py，
    這幾個斷言把「不會漂移」這件事鎖住。"""

    def test_market(self):
        assert query.market()["text"] == tools.get_market_index.invoke({})

    def test_quote(self):
        assert query.quote(["2330", "9999"])["text"] == tools.get_stock_quote.invoke(
            {"codes": ["2330", "9999"]}
        )

    def test_screen(self):
        assert query.screen(sort_by="volume", limit=2)["text"] == tools.screen_stocks.invoke(
            {"sort_by": "volume", "limit": 2}
        )


class TestCliJsonContract:
    def _run(self, capsys, argv: list[str]) -> tuple[int, dict, str]:
        import sys

        sys.argv = ["stock_agent.cli", *argv]
        code = main()
        captured = capsys.readouterr()
        return code, json.loads(captured.out), captured.err

    def test_market_stdout_is_pure_json(self, capsys):
        code, payload, _ = self._run(capsys, ["market"])
        assert code == 0
        assert payload["index"]["close"] == 43386.41

    def test_quote_accepts_multiple_keys(self, capsys):
        code, payload, _ = self._run(capsys, ["quote", "2330", "鴻海"])
        assert code == 0
        assert len(payload["quotes"]) == 2

    def test_screen_include_etf_flag(self, capsys):
        code, payload, _ = self._run(
            capsys, ["screen", "--sort-by", "close", "--limit", "50", "--include-etf"]
        )
        assert code == 0
        assert payload["criteria"]["common_stock_only"] is False
        assert "0050" in {m["code"] for m in payload["matches"]}

    def test_error_goes_to_stdout_as_json_with_nonzero_exit(self, capsys):
        """呼叫者只需解析一種格式 — 成功與失敗都是 stdout 上的 JSON。"""
        code, payload, _ = self._run(capsys, ["market", "--date", "2020-01-02"])
        assert code == 1
        assert "2020-01-02" in payload["error"]

    def test_every_payload_carries_trade_date_and_text(self, capsys):
        for argv in (["market"], ["quote", "2330"], ["screen", "--sort-by", "volume", "--limit", "2"]):
            code, payload, _ = self._run(capsys, argv)
            assert code == 0
            assert payload["trade_date"] == "2026-08-03"
            assert payload["text"].startswith("交易日 2026-08-03")
