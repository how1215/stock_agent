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


class TestShapeMetrics:
    def test_computes_from_single_day_ohlc(self, day):
        """以 2330 的實際數字驗算：開 2390 高 2395 低 2365 收 2370 前收 2425。"""
        tsmc = next(q for q in day["quotes"] if q["code"] == "2330")
        m = screen.shape_metrics(tsmc)
        assert m["amplitude_pct"] == pytest.approx((2395 - 2365) / 2425 * 100, abs=1e-6)
        assert m["close_position"] == pytest.approx((2370 - 2365) / (2395 - 2365) * 100, abs=1e-6)
        assert m["body_pct"] == pytest.approx(abs(2370 - 2390) / 2425 * 100, abs=1e-6)
        assert m["gap_pct"] == pytest.approx((2390 - 2425) / 2425 * 100, abs=1e-6)

    def test_close_position_is_high_when_closing_at_the_top(self):
        m = screen.shape_metrics(
            {"open": 100.0, "high": 110.0, "low": 99.0, "close": 110.0, "prev_close": 100.0}
        )
        assert m["close_position"] == pytest.approx(100.0)

    def test_flat_day_has_defined_close_position(self):
        """一價到底（漲停鎖死）高低同價，不能除以零。"""
        m = screen.shape_metrics(
            {"open": 110.0, "high": 110.0, "low": 110.0, "close": 110.0, "prev_close": 100.0}
        )
        assert m["close_position"] == 100.0
        assert m["amplitude_pct"] == 0.0

    def test_missing_values_give_none_not_crash(self, day):
        halted = next(q for q in day["quotes"] if q["code"] == "9299")
        assert screen.shape_metrics(halted) == {
            "amplitude_pct": None,
            "close_position": None,
            "body_pct": None,
            "gap_pct": None,
        }


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
