"""結構化資料查詢 — 回傳 dict，供 JSON CLI 與外部工具使用。

每個結果都同時帶結構化欄位與一個 `text` 欄位（給 LLM 讀的緊湊渲染，來自
render.py）。這樣外部呼叫者一次子行程就同時拿到兩種表示，不必為了取文字
再跑一次，也不必在別的語言裡重寫一遍數字格式。
"""

from . import render
from . import screen as screening
from .sources import cache
from .sources.schema import TAIEX_INDEX_NAME


def market(date: str | None = None) -> dict:
    """大盤（發行量加權股價指數）收盤資訊。"""
    day = cache.load_or_fetch(date)
    index = next((i for i in day["indices"] if i["index_name"] == TAIEX_INDEX_NAME), None)
    if index is None:
        raise LookupError(f"交易日 {day['trade_date']} 的資料中找不到 {TAIEX_INDEX_NAME}")
    return {
        "trade_date": day["trade_date"],
        "market": "TWSE",
        "index": index,
        "text": render.render_market(day["trade_date"], index),
    }


def quote(keys: list[str], date: str | None = None) -> dict:
    """指定個股的收盤資訊。keys 可為股票代號或公司名稱。"""
    if not keys:
        raise ValueError("請提供至少一個股票代號或公司名稱")

    day = cache.load_or_fetch(date)
    found, missing = screening.find_quotes(day["quotes"], keys)
    return {
        "trade_date": day["trade_date"],
        "market": "TWSE",
        "quotes": found,
        "not_found": missing,
        "text": render.render_quotes(day["trade_date"], found, missing),
    }


def screen(
    sort_by: str,
    order: str = "desc",
    limit: int = 20,
    min_change_pct: float | None = None,
    min_volume: int | None = None,
    common_stock_only: bool = True,
    date: str | None = None,
) -> dict:
    """依條件篩選並排序全市場個股。"""
    day = cache.load_or_fetch(date)
    matches = screening.screen(
        day["quotes"],
        sort_by=sort_by,
        order=order,
        limit=limit,
        min_change_pct=min_change_pct,
        min_volume=min_volume,
        common_stock_only=common_stock_only,
    )
    criteria = {
        "sort_by": sort_by,
        "order": order,
        "limit": limit,
        "min_change_pct": min_change_pct,
        "min_volume": min_volume,
        "common_stock_only": common_stock_only,
    }
    return {
        "trade_date": day["trade_date"],
        "market": "TWSE",
        "criteria": criteria,
        "count": len(matches),
        "matches": matches,
        "text": render.render_screen(day["trade_date"], criteria, matches),
    }
