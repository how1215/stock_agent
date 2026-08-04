"""逐檔掃描的進入點。

包住 workflow/ 的 graph，處理參數上限與並行度。與 query.py 的差別是這裡會
呼叫 LLM（N+1 次），所以它屬於 agent 側而非資料側。
"""

from config import MAX_SCAN_LIMIT, SCAN_MAX_CONCURRENCY

from .llm import build_llm
from .workflow.graph import build_scan_graph

_graph = None


def _get_graph():
    """graph 編譯一次就好，之後重用。"""
    global _graph
    if _graph is None:
        _graph = build_scan_graph(build_llm())
    return _graph


def run_scan(
    focus: str,
    *,
    sort_by: str = "turnover",
    order: str = "desc",
    limit: int = 15,
    min_change_pct: float | None = None,
    min_volume: int | None = None,
    common_stock_only: bool = True,
    date: str | None = None,
    max_concurrency: int = SCAN_MAX_CONCURRENCY,
) -> dict:
    """篩出候選後逐檔判讀，回傳完整的 graph 最終 state。

    limit 直接決定 LLM 呼叫次數，所以夾在 MAX_SCAN_LIMIT 以內。
    """
    state = {
        "focus": focus,
        "criteria": {
            "sort_by": sort_by,
            "order": order,
            "limit": max(1, min(limit, MAX_SCAN_LIMIT)),
            "min_change_pct": min_change_pct,
            "min_volume": min_volume,
            "common_stock_only": common_stock_only,
        },
        "date": date,
        "readings": [],
    }
    return _get_graph().invoke(state, config={"max_concurrency": max_concurrency})


def scan_payload(focus: str, **kwargs) -> dict:
    """run_scan 的 JSON 友善版本，形狀與 query.py 的結果一致。

    同樣帶 `text` 欄位（就是報告本身），外部呼叫者一次子行程就同時拿到
    給模型讀的文字與結構化結果 — 與 query.py 走同一個約定。
    """
    result = run_scan(focus, **kwargs)
    grouped = result.get("grouped", {})
    return {
        "trade_date": result["trade_date"],
        "market": "TWSE",
        "focus": focus,
        "criteria": result["criteria"],
        "scanned": len(result["readings"]),
        "counts": {verdict: len(rows) for verdict, rows in grouped.items()},
        "readings": result["readings"],
        "failed": result.get("failed", []),
        "text": result["report"],
    }
