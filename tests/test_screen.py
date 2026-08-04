import pytest

from config import MAX_SCREEN_LIMIT
from stock_agent import screen


class TestIsCommonStock:
    @pytest.mark.parametrize("code,expected", [
        ("1101", True), ("2330", True), ("9958", True),
        # 首碼 0 的 4 位純數字是 ETF，不是普通股 — 只檢查「4 位數字」會誤判
        ("0050", False), ("0056", False),
        ("00400A", False), ("006201", False), ("233", False), ("23300", False),
    ])
    def test_four_digits_and_leading_nonzero(self, code, expected):
        assert screen.is_common_stock(code) is expected


class TestFindQuotes:
    def test_finds_by_code(self, day):
        found, missing = screen.find_quotes(day["quotes"], ["2330"])
        assert [q["name"] for q in found] == ["台積電"]
        assert missing == []

    def test_finds_by_company_name(self, day):
        """模型常常不確定代號，讓它傳名稱比讓它憑記憶猜代號安全。"""
        found, missing = screen.find_quotes(day["quotes"], ["鴻海"])
        assert [q["code"] for q in found] == ["2317"]
        assert missing == []

    def test_reports_missing_separately(self, day):
        found, missing = screen.find_quotes(day["quotes"], ["2330", "9999", "不存在公司"])
        assert len(found) == 1
        assert missing == ["9999", "不存在公司"]

    def test_strips_whitespace(self, day):
        found, _ = screen.find_quotes(day["quotes"], [" 2330 "])
        assert len(found) == 1


class TestScreen:
    def test_sorts_desc_by_default(self, day):
        rows = screen.screen(day["quotes"], sort_by="close", limit=10)
        closes = [q["close"] for q in rows]
        assert closes == sorted(closes, reverse=True)

    def test_sorts_asc(self, day):
        rows = screen.screen(day["quotes"], sort_by="close", order="asc", limit=10)
        closes = [q["close"] for q in rows]
        assert closes == sorted(closes)

    def test_common_stock_only_excludes_etf(self, day):
        rows = screen.screen(day["quotes"], sort_by="close", limit=50, common_stock_only=True)
        assert all(screen.is_common_stock(q["code"]) for q in rows)
        assert "0050" not in {q["code"] for q in rows}

    def test_etf_included_when_disabled(self, day):
        rows = screen.screen(day["quotes"], sort_by="close", limit=50, common_stock_only=False)
        assert "0050" in {q["code"] for q in rows}

    def test_min_change_pct(self, day):
        rows = screen.screen(day["quotes"], sort_by="change_pct", limit=50, min_change_pct=5)
        assert rows and all(q["change_pct"] >= 5 for q in rows)

    def test_min_volume(self, day):
        rows = screen.screen(day["quotes"], sort_by="volume", limit=50, min_volume=100_000_000)
        assert rows and all(q["volume"] >= 100_000_000 for q in rows)

    def test_excludes_rows_with_none_sort_field(self, day):
        """停牌個股的 change_pct 是 None，無法參與排序，必須排除而非炸掉。"""
        rows = screen.screen(day["quotes"], sort_by="change_pct", limit=50, common_stock_only=False)
        assert "9299" not in {q["code"] for q in rows}

    def test_limit_clamped_to_max(self, day):
        rows = screen.screen(day["quotes"], sort_by="close", limit=10_000, common_stock_only=False)
        assert len(rows) <= MAX_SCREEN_LIMIT

    def test_limit_at_least_one(self, day):
        assert len(screen.screen(day["quotes"], sort_by="close", limit=0)) == 1

    @pytest.mark.parametrize("bad_field", ["pe_ratio", "name", ""])
    def test_rejects_field_outside_whitelist(self, day, bad_field):
        with pytest.raises(ValueError, match="sort_by"):
            screen.screen(day["quotes"], sort_by=bad_field)

    def test_rejects_bad_order(self, day):
        with pytest.raises(ValueError, match="order"):
            screen.screen(day["quotes"], sort_by="close", order="sideways")

    def test_returns_empty_when_nothing_matches(self, day):
        assert screen.screen(day["quotes"], sort_by="change_pct", min_change_pct=999) == []
