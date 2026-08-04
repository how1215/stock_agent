import json
from datetime import datetime, timedelta, timezone

import pytest

import config
from stock_agent.sources import cache


@pytest.fixture(autouse=True)
def temp_cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "data")
    return tmp_path / "data"


def _write_day(cache_dir, trade_date: str, age_seconds: float, payload: dict | None = None):
    cache_dir.mkdir(parents=True, exist_ok=True)
    fetched_at = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    day = {
        "trade_date": trade_date,
        "fetched_at": fetched_at.isoformat(),
        "quotes": [],
        "indices": [],
        **(payload or {}),
    }
    (cache_dir / f"{trade_date}.json").write_text(json.dumps(day), encoding="utf-8")
    return day


class TestSpecificDate:
    def test_reads_from_disk(self, temp_cache_dir, day):
        _write_day(temp_cache_dir, "2026-08-03", 0, {"quotes": day["quotes"]})
        loaded = cache.load_or_fetch("2026-08-03")
        assert loaded["trade_date"] == "2026-08-03"
        assert len(loaded["quotes"]) == len(day["quotes"])

    def test_missing_date_raises_without_hitting_api(self, temp_cache_dir, monkeypatch):
        """指定日期查不到時絕不能打 API — 打了也只會拿到最新交易日的數字，
        回錯日期的數據比誠實報錯更糟。"""
        monkeypatch.setattr(
            cache.twse, "fetch_latest", lambda: pytest.fail("不該呼叫 API")
        )
        _write_day(temp_cache_dir, "2026-08-03", 0)
        with pytest.raises(cache.DataUnavailable) as exc:
            cache.load_or_fetch("2026-07-31")
        assert "2026-07-31" in str(exc.value)
        assert "2026-08-03" in str(exc.value)  # 有提示本地已有哪些日期

    def test_error_message_when_cache_empty(self, monkeypatch):
        monkeypatch.setattr(
            cache.twse, "fetch_latest", lambda: pytest.fail("不該呼叫 API")
        )
        with pytest.raises(cache.DataUnavailable) as exc:
            cache.load_or_fetch("2026-07-31")
        assert "沒有任何日期" in str(exc.value)


class TestLatest:
    def test_fresh_cache_does_not_hit_api(self, temp_cache_dir, monkeypatch):
        monkeypatch.setattr(
            cache.twse, "fetch_latest", lambda: pytest.fail("cache 新鮮時不該呼叫 API")
        )
        _write_day(temp_cache_dir, "2026-08-03", age_seconds=1)
        assert cache.load_or_fetch()["trade_date"] == "2026-08-03"

    def test_stale_cache_refetches(self, temp_cache_dir, monkeypatch):
        calls = []

        def fake_fetch():
            calls.append(1)
            return {"trade_date": "2026-08-04", "quotes": [], "indices": []}

        monkeypatch.setattr(cache.twse, "fetch_latest", fake_fetch)
        _write_day(temp_cache_dir, "2026-08-03", age_seconds=config.FRESH_TTL_SECONDS + 1)

        assert cache.load_or_fetch()["trade_date"] == "2026-08-04"
        assert len(calls) == 1

    def test_empty_cache_fetches_and_writes(self, temp_cache_dir, monkeypatch):
        monkeypatch.setattr(
            cache.twse,
            "fetch_latest",
            lambda: {"trade_date": "2026-08-03", "quotes": [], "indices": []},
        )
        day = cache.load_or_fetch()
        assert day["fetched_at"]  # 寫入時補上時間戳，否則永遠判定為過期
        assert (temp_cache_dir / "2026-08-03.json").is_file()

    def test_missing_fetched_at_is_treated_as_stale(self, temp_cache_dir, monkeypatch):
        """手動放進來的檔案（例如從 fixtures 複製）沒有時間戳，應視為過期。"""
        temp_cache_dir.mkdir(parents=True, exist_ok=True)
        (temp_cache_dir / "2026-08-03.json").write_text(
            json.dumps({"trade_date": "2026-08-03", "quotes": [], "indices": []}),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            cache.twse,
            "fetch_latest",
            lambda: {"trade_date": "2026-08-04", "quotes": [], "indices": []},
        )
        assert cache.load_or_fetch()["trade_date"] == "2026-08-04"


def test_cached_dates_sorted(temp_cache_dir):
    for date in ["2026-08-03", "2026-07-31", "2026-08-01"]:
        _write_day(temp_cache_dir, date, 0)
    assert cache.cached_dates() == ["2026-07-31", "2026-08-01", "2026-08-03"]
