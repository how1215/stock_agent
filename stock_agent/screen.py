"""全市場個股的篩選與排序。

純函式、無 I/O、無 LLM。所有數字在這裡算完，LLM 只負責轉述與解讀。
用 stdlib 而非 pandas — 全市場約 1400 筆，sorted 綽綽有餘。等要算跨多個
交易日的 rolling window（移動平均、量能比）時再考慮引入 pandas。
"""

from config import MAX_SCREEN_LIMIT, PRICE_LIMIT_PCT

from .sources.schema import SORTABLE_FIELDS


def is_common_stock(code: str) -> bool:
    """上市普通股代號為 4 位數字且首碼 1–9（1101 台泥 ～ 9958 世紀鋼）。

    首碼 0 是 ETF/受益證券，且它們也有 4 位純數字的（0050 元大台灣50、
    0056 元大高股息），所以只檢查「4 位數字」會把 ETF 誤判成普通股。
    """
    return len(code) == 4 and code.isdigit() and code[0] != "0"


def find_quotes(quotes: list[dict], keys: list[str]) -> tuple[list[dict], list[str]]:
    """依股票代號或公司名稱查找個股。

    接受名稱是刻意的：讓模型不必憑記憶把公司名轉成代號，避免它把代號記錯
    卻回報得理直氣壯。

    Returns:
        (找到的個股, 找不到的查詢字串)
    """
    by_code = {q["code"]: q for q in quotes}
    by_name = {q["name"]: q for q in quotes}

    found, missing = [], []
    for key in keys:
        key = key.strip()
        quote = by_code.get(key) or by_name.get(key)
        if quote is None:
            missing.append(key)
        else:
            found.append(quote)
    return found, missing


def breadth(quotes: list[dict]) -> dict:
    """市場寬度：漲跌平家數與漲跌停家數。

    只計普通股 — 把 ETF 混進「上漲家數」會讓這個指標失去意義。
    漲跌停用容差判定：台股限制 10%，但除權息與特殊情形會讓實際值略偏，
    實測 2026-08-03 的漲停檔位都落在 9.9x～10.00% 之間。
    """
    rows = [q for q in quotes if is_common_stock(q["code"]) and q["change_pct"] is not None]

    advancing = sum(1 for q in rows if q["change_pct"] > 0)
    declining = sum(1 for q in rows if q["change_pct"] < 0)

    return {
        "total": len(rows),
        "advancing": advancing,
        "declining": declining,
        "unchanged": len(rows) - advancing - declining,
        "limit_up": sum(1 for q in rows if q["change_pct"] >= PRICE_LIMIT_PCT - 0.15),
        "limit_down": sum(1 for q in rows if q["change_pct"] <= -PRICE_LIMIT_PCT + 0.15),
    }


def shape_metrics(quote: dict) -> dict:
    """從單日 OHLC 推導出的盤中型態指標。

    只用單日資料 — 量增／量縮這類需要多日均量的判斷做不出來，要等本地
    cache 累積足夠日檔。所有數值在這裡算完，LLM 只做文字判讀。

    Returns:
        amplitude_pct  振幅（高低差佔前收的百分比）
        close_position 收盤位置（0 = 收在最低，100 = 收在最高）
        body_pct       K 線實體（開收差佔前收的百分比）
        gap_pct        開盤跳空（開盤相對前收的百分比）
        任一項資料不足時為 None。
    """
    high, low, open_, close = quote["high"], quote["low"], quote["open"], quote["close"]
    prev_close = quote["prev_close"]

    def pct_of_prev(value: float | None) -> float | None:
        if value is None or not prev_close:
            return None
        return value / prev_close * 100

    span = None if high is None or low is None else high - low
    close_position = None
    if span is not None and close is not None:
        # 高低同價（如一價到底的漲停）無法定義收盤位置
        close_position = 100.0 if span == 0 else (close - low) / span * 100

    return {
        "amplitude_pct": pct_of_prev(span),
        "close_position": close_position,
        "body_pct": pct_of_prev(None if close is None or open_ is None else abs(close - open_)),
        "gap_pct": pct_of_prev(None if open_ is None or prev_close is None else open_ - prev_close),
    }


def screen(
    quotes: list[dict],
    *,
    sort_by: str,
    order: str = "desc",
    limit: int = 20,
    min_change_pct: float | None = None,
    min_volume: int | None = None,
    common_stock_only: bool = True,
) -> list[dict]:
    """依條件篩選並排序個股。

    Raises:
        ValueError: sort_by 或 order 不在白名單。
    """
    if sort_by not in SORTABLE_FIELDS:
        raise ValueError(f"sort_by 必須是 {', '.join(SORTABLE_FIELDS)} 之一，收到 {sort_by!r}")
    if order not in {"desc", "asc"}:
        raise ValueError(f"order 必須是 desc 或 asc，收到 {order!r}")

    rows = quotes
    if common_stock_only:
        rows = [q for q in rows if is_common_stock(q["code"])]
    if min_change_pct is not None:
        rows = [q for q in rows if q["change_pct"] is not None and q["change_pct"] >= min_change_pct]
    if min_volume is not None:
        rows = [q for q in rows if q["volume"] is not None and q["volume"] >= min_volume]

    # 排序欄位為 None（停牌、無成交）的個股無法參與排序，直接排除
    rows = [q for q in rows if q.get(sort_by) is not None]
    rows.sort(key=lambda q: q[sort_by], reverse=(order == "desc"))

    return rows[: max(1, min(limit, MAX_SCREEN_LIMIT))]
