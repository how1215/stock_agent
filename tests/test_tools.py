"""Tool 層測試 — 不經 LLM，直接 invoke。

這層的回傳文字是模型看到的唯一輸入，所以格式與錯誤訊息本身就是規格。
"""

import pytest

from stock_agent import tools


@pytest.fixture(autouse=True)
def _use_fixture_data(stub_cache):
    """所有 tool 測試都走 fixture 資料，不打網路。"""


class TestGetMarketIndex:
    def test_reports_taiex_and_trade_date(self):
        out = tools.get_market_index.invoke({})
        assert "2026-08-03" in out
        assert "發行量加權股價指數" in out
        assert "43,386.41" in out
        assert "+0.62%" in out

    def test_unavailable_date_returns_actionable_message(self):
        out = tools.get_market_index.invoke({"date": "2026-07-31"})
        assert "查不到資料" in out
        assert "2026-07-31" in out


class TestGetStockQuote:
    def test_single_code(self):
        out = tools.get_stock_quote.invoke({"codes": ["2330"]})
        assert "2026-08-03" in out
        assert "台積電" in out
        assert "2,370.00" in out
        assert "-2.27%" in out

    def test_accepts_company_name(self):
        out = tools.get_stock_quote.invoke({"codes": ["鴻海"]})
        assert "2317" in out

    def test_multiple_codes_in_one_call(self):
        out = tools.get_stock_quote.invoke({"codes": ["2330", "2317"]})
        assert "台積電" in out and "鴻海" in out

    def test_unknown_code_message_is_actionable(self):
        """訊息要讓模型能自我修正，而不是只說「錯誤」。"""
        out = tools.get_stock_quote.invoke({"codes": ["9999"]})
        assert "9999" in out
        assert "4 位數字" in out
        assert "上櫃" in out

    def test_partial_success_still_returns_found_ones(self):
        out = tools.get_stock_quote.invoke({"codes": ["2330", "9999"]})
        assert "台積電" in out
        assert "9999" in out

    def test_empty_codes(self):
        assert "至少一個" in tools.get_stock_quote.invoke({"codes": []})

    def test_volume_pre_converted_to_lots(self):
        """張數在 Python 換算好，避免模型自己做除法。"""
        out = tools.get_stock_quote.invoke({"codes": ["2330"]})
        assert "35,209,944 股" in out
        assert "35,209 張" in out


class TestScreenStocks:
    def test_header_states_trade_date_and_filters(self):
        out = tools.screen_stocks.invoke({"sort_by": "change_pct", "limit": 3})
        assert "2026-08-03" in out
        assert "僅普通股" in out

    def test_bad_sort_field_lists_valid_options(self):
        out = tools.screen_stocks.invoke({"sort_by": "pe_ratio", "limit": 3})
        assert "參數錯誤" in out
        assert "change_pct" in out and "turnover" in out

    def test_no_match_suggests_relaxing(self):
        out = tools.screen_stocks.invoke({"sort_by": "change_pct", "min_change_pct": 999})
        assert "沒有符合條件" in out
        assert "放寬" in out

    def test_min_change_pct_shown_in_header(self):
        out = tools.screen_stocks.invoke({"sort_by": "change_pct", "min_change_pct": 5})
        assert "≥ 5" in out

    def test_unavailable_date(self):
        out = tools.screen_stocks.invoke({"sort_by": "close", "date": "2026-07-31"})
        assert "查不到資料" in out


def test_every_tool_output_carries_trade_date():
    """盤中問「今天」會拿到前一交易日，模型必須每次都有交易日可以轉述。"""
    outputs = [
        tools.get_market_index.invoke({}),
        tools.get_stock_quote.invoke({"codes": ["2330"]}),
        tools.screen_stocks.invoke({"sort_by": "volume", "limit": 2}),
    ]
    assert all("2026-08-03" in out for out in outputs)
