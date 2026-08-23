"""自選股測試。全部離線 — 用 stub_cache 的 fixture 資料驗證代號。"""

import json

import pytest

import config
from stock_agent import watchlist


@pytest.fixture(autouse=True)
def temp_watchlist(tmp_path, monkeypatch, stub_cache):
    monkeypatch.setattr(config, "WATCHLIST_PATH", tmp_path / "watchlist.json")
    return tmp_path / "watchlist.json"


class TestLoad:
    def test_empty_when_file_absent(self):
        assert watchlist.load() == []

    def test_corrupt_file_treated_as_empty(self, temp_watchlist):
        """手改壞掉的檔案不該讓每日排程整個炸掉。"""
        temp_watchlist.write_text("{ not json", encoding="utf-8")
        assert watchlist.load() == []

    def test_preserves_insertion_order(self):
        watchlist.add(["2317", "2330"])
        assert watchlist.load() == ["2317", "2330"]


class TestAdd:
    def test_by_code(self):
        result = watchlist.add(["2330"])
        assert [q["code"] for q in result["added"]] == ["2330"]
        assert result["codes"] == ["2330"]

    def test_by_company_name(self):
        """使用者（和模型）常常只知道名稱。"""
        result = watchlist.add(["鴻海"])
        assert [q["code"] for q in result["added"]] == ["2317"]

    def test_unknown_code_reported_not_silently_stored(self):
        """打錯的代號若默默進清單，每天的簡報都會少一檔而沒人發現。"""
        result = watchlist.add(["2330", "9999"])
        assert result["not_found"] == ["9999"]
        assert result["codes"] == ["2330"]

    def test_duplicate_reported_separately(self):
        watchlist.add(["2330"])
        result = watchlist.add(["台積電"])
        assert result["added"] == []
        assert [q["code"] for q in result["already_present"]] == ["2330"]
        assert result["codes"] == ["2330"]

    def test_respects_size_cap(self, monkeypatch):
        monkeypatch.setattr(config, "MAX_WATCHLIST_SIZE", 2)
        result = watchlist.add(["2330", "2317", "2303"])
        assert len(result["codes"]) == 2
        assert any("上限" in msg for msg in result["not_found"])

    def test_persists_to_disk(self, temp_watchlist):
        watchlist.add(["2330"])
        saved = json.loads(temp_watchlist.read_text(encoding="utf-8"))
        assert saved["codes"] == ["2330"]
        assert saved["updated_at"]

    def test_no_write_when_nothing_added(self, temp_watchlist):
        watchlist.add(["9999"])
        assert not temp_watchlist.exists()


class TestRemove:
    def test_by_code_and_name(self):
        watchlist.add(["2330", "2317"])
        assert watchlist.remove(["台積電"])["codes"] == ["2317"]
        assert watchlist.remove(["2317"])["codes"] == []

    def test_reports_codes_not_in_list(self):
        watchlist.add(["2330"])
        result = watchlist.remove(["2317"])
        assert result["not_in_list"] == ["2317"]
        assert result["codes"] == ["2330"]

    def test_can_remove_unresolvable_code(self, temp_watchlist):
        """已下市的個股不在市場資料裡，但仍必須能從清單移除。"""
        temp_watchlist.write_text(json.dumps({"codes": ["2330", "1234"]}), encoding="utf-8")
        assert watchlist.remove(["1234"])["codes"] == ["2330"]


class TestDescribe:
    def test_attaches_quotes_and_text(self):
        watchlist.add(["2330", "2317"])
        described = watchlist.describe()
        assert described["trade_date"] == "2026-08-03"
        assert [q["code"] for q in described["quotes"]] == ["2330", "2317"]
        assert described["text"].startswith("交易日 2026-08-03")

    def test_reports_delisted_entries(self, temp_watchlist):
        temp_watchlist.write_text(json.dumps({"codes": ["2330", "1234"]}), encoding="utf-8")
        assert watchlist.describe()["not_found"] == ["1234"]

    def test_empty_list_text(self):
        assert "為空" in watchlist.describe()["text"]


class TestClear:
    def test_removes_everything(self):
        watchlist.add(["2330", "2317"])
        result = watchlist.clear()
        assert result["codes"] == []
        assert set(result["removed"]) == {"2330", "2317"}
        assert watchlist.load() == []


def test_every_mutation_returns_text_matching_cli_output():
    """CLI 與 JSON payload 共用 render.py，text 欄位必須存在且一致。"""
    for result in (watchlist.add(["2330"]), watchlist.remove(["2330"]), watchlist.clear()):
        assert "目前自選股" in result["text"]
