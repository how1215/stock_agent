"""TWSE 開放 API 的抓取與正規化。

重要限制：`STOCK_DAY_ALL` 與 `MI_INDEX` 都**不接受日期參數**（Swagger 宣告無
query parameter，實測傳 ?date=... 會被忽略），永遠只回最新交易日。因此歷史
資料只能靠每天抓取後累積在本地 cache，無法回溯查詢。
"""

import httpx

from config import HTTP_TIMEOUT, TWSE_BASE_URL

from .schema import _QUOTE_INTEGER, _QUOTE_NUMERIC

STOCK_DAY_ALL_PATH = "/exchangeReport/STOCK_DAY_ALL"
MI_INDEX_PATH = "/exchangeReport/MI_INDEX"


def roc_to_iso(roc: str) -> str:
    """民國日期字串轉 ISO：'1150803' → '2026-08-03'。"""
    roc = roc.strip()
    if len(roc) != 7 or not roc.isdigit():
        raise ValueError(f"非預期的民國日期格式: {roc!r}")
    return f"{int(roc[:3]) + 1911}-{roc[3:5]}-{roc[5:7]}"


def _to_float(raw: str | None) -> float | None:
    """數值欄位轉 float。停牌／無成交時 API 會給空字串，轉成 None。"""
    if raw is None:
        return None
    raw = raw.strip().replace(",", "")
    if not raw or raw in {"--", "X"}:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _to_int(raw: str | None) -> int | None:
    value = _to_float(raw)
    return None if value is None else int(value)


def normalize_quote(row: dict) -> dict:
    """把 STOCK_DAY_ALL 的一筆原始資料轉成正規化欄位。

    API 只給 Change（絕對價差，帶正確負號），不給漲跌幅，所以 prev_close
    與 change_pct 都要自己推算。
    """
    quote: dict = {"trade_date": roc_to_iso(row["Date"]), "code": row["Code"], "name": row["Name"]}

    for raw_key, field in _QUOTE_NUMERIC.items():
        quote[field] = _to_float(row.get(raw_key))
    for raw_key, field in _QUOTE_INTEGER.items():
        quote[field] = _to_int(row.get(raw_key))

    close, change = quote["close"], quote["change"]
    if close is None or change is None:
        quote["prev_close"] = None
        quote["change_pct"] = None
    else:
        prev_close = close - change
        quote["prev_close"] = prev_close
        quote["change_pct"] = None if prev_close == 0 else change / prev_close * 100

    return quote


def normalize_index(row: dict) -> dict:
    """把 MI_INDEX 的一筆原始資料轉成正規化欄位。

    漲跌方向在獨立的「漲跌」欄位（'+' / '-'），「漲跌點數」永遠是絕對值，
    所以點數要自己套符號。「漲跌百分比」本身已帶正確符號。
    """
    points = _to_float(row.get("漲跌點數"))
    if points is not None and row.get("漲跌", "").strip() == "-":
        points = -points

    return {
        "trade_date": roc_to_iso(row["日期"]),
        "index_name": row["指數"].strip(),
        "close": _to_float(row.get("收盤指數")),
        "change": points,
        "change_pct": _to_float(row.get("漲跌百分比")),
    }


def _get(path: str) -> list[dict]:
    response = httpx.get(
        f"{TWSE_BASE_URL}{path}",
        headers={"accept": "application/json"},
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list) or not payload:
        raise RuntimeError(f"TWSE {path} 回傳非預期內容（可能尚未收盤或端點異動）")
    return payload


def fetch_latest() -> dict:
    """抓取最新交易日的全市場個股與大盤指數。

    交易日以回傳資料裡的 Date 為真值 — 盤中呼叫會拿到前一交易日，不能用
    本地日期推斷。
    """
    quotes = [normalize_quote(row) for row in _get(STOCK_DAY_ALL_PATH)]
    indices = [normalize_index(row) for row in _get(MI_INDEX_PATH)]

    trade_date = quotes[0]["trade_date"]
    index_date = indices[0]["trade_date"]
    if index_date != trade_date:
        raise RuntimeError(f"個股與大盤資料日期不一致：{trade_date} vs {index_date}")

    return {"trade_date": trade_date, "quotes": quotes, "indices": indices}
