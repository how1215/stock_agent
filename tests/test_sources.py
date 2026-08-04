import pytest

from stock_agent.sources import twse
from stock_agent.sources.schema import TAIEX_INDEX_NAME


class TestRocToIso:
    def test_converts_民國_to_西元(self):
        assert twse.roc_to_iso("1150803") == "2026-08-03"
        assert twse.roc_to_iso("1000101") == "2011-01-01"

    @pytest.mark.parametrize("bad", ["", "20260803", "115080", "abcdefg"])
    def test_rejects_bad_format(self, bad):
        with pytest.raises(ValueError):
            twse.roc_to_iso(bad)


class TestToFloat:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("2370.00", 2370.0),
            ("-55.0000", -55.0),
            ("35,209,944", 35209944.0),
            ("", None),
            ("  ", None),
            ("--", None),
            ("X", None),
            (None, None),
        ],
    )
    def test_conversions(self, raw, expected):
        assert twse._to_float(raw) == expected


class TestNormalizeQuote:
    def test_derives_prev_close_and_change_pct(self, raw_quotes):
        """API 只給絕對價差，漲跌幅與前收都要自己推。以 2330 的實際數字驗證。"""
        tsmc = next(twse.normalize_quote(r) for r in raw_quotes if r["Code"] == "2330")
        assert tsmc["close"] == 2370.0
        assert tsmc["change"] == -55.0
        assert tsmc["prev_close"] == 2425.0
        assert tsmc["change_pct"] == pytest.approx(-2.268, abs=0.001)

    def test_positive_change(self, raw_quotes):
        hon_hai = next(twse.normalize_quote(r) for r in raw_quotes if r["Code"] == "2317")
        assert hon_hai["prev_close"] == 250.5
        assert hon_hai["change_pct"] == pytest.approx(0.998, abs=0.001)

    def test_empty_price_becomes_none_not_crash(self, raw_quotes):
        """停牌／無成交時 API 給空字串，不能 float('') 炸掉。"""
        halted = next(twse.normalize_quote(r) for r in raw_quotes if r["Code"] == "9299")
        assert halted["close"] is None
        assert halted["prev_close"] is None
        assert halted["change_pct"] is None

    def test_converts_date_and_keeps_identity(self, raw_quotes):
        quote = twse.normalize_quote(raw_quotes[0])
        assert quote["trade_date"] == "2026-08-03"
        assert quote["code"] and quote["name"]

    def test_volume_is_integer(self, raw_quotes):
        tsmc = next(twse.normalize_quote(r) for r in raw_quotes if r["Code"] == "2330")
        assert tsmc["volume"] == 35209944
        assert isinstance(tsmc["volume"], int)


class TestNormalizeIndex:
    def test_applies_sign_from_separate_field(self, raw_indices):
        """漲跌點數永遠是絕對值，方向在獨立的「漲跌」欄位。"""
        down = next(twse.normalize_index(r) for r in raw_indices if r["漲跌"] == "-")
        assert down["change"] < 0
        assert down["change_pct"] < 0

    def test_taiex(self, raw_indices):
        taiex = next(
            twse.normalize_index(r) for r in raw_indices if r["指數"] == TAIEX_INDEX_NAME
        )
        assert taiex["close"] == 43386.41
        assert taiex["change"] == 266.66
        assert taiex["change_pct"] == 0.62
        assert taiex["trade_date"] == "2026-08-03"
