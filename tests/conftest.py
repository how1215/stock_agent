import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def raw_quotes() -> list[dict]:
    """STOCK_DAY_ALL 的真實回應片段（含 ETF、跌幅、停牌情形）。"""
    return json.loads((FIXTURES / "stock_day_all.json").read_text(encoding="utf-8"))


@pytest.fixture
def raw_indices() -> list[dict]:
    """MI_INDEX 的真實回應片段（含上漲與下跌的指數）。"""
    return json.loads((FIXTURES / "mi_index.json").read_text(encoding="utf-8"))


@pytest.fixture
def day(raw_quotes, raw_indices) -> dict:
    """正規化後的一日資料，形狀與 cache 檔案相同。"""
    from stock_agent.sources import twse

    return {
        "trade_date": "2026-08-03",
        "quotes": [twse.normalize_quote(row) for row in raw_quotes],
        "indices": [twse.normalize_index(row) for row in raw_indices],
    }


@pytest.fixture
def stub_cache(monkeypatch, day):
    """把 fixture 資料接到 query 層唯一的取資料接縫上。

    query 是 tools 與 JSON CLI 的共同入口，patch 這裡兩邊都生效。
    """
    from stock_agent import query
    from stock_agent.sources import cache

    def fake_load_or_fetch(trade_date=None):
        if trade_date in (None, "2026-08-03"):
            return day
        raise cache.DataUnavailable(f"沒有 {trade_date} 的資料。本地已有：2026-08-03")

    monkeypatch.setattr(query.cache, "load_or_fetch", fake_load_or_fetch)
    return day
