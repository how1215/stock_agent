"""正規化後的欄位定義 — 資料層的單一真相。

TWSE 的兩個端點欄位命名不一致（STOCK_DAY_ALL 用英文、MI_INDEX 用中文），
這裡把它們收斂成一組 snake_case 名稱，上層只認識這組。
"""

QUOTE_FIELDS = (
    "trade_date",
    "code",
    "name",
    "open",
    "high",
    "low",
    "close",
    "change",
    "change_pct",
    "prev_close",
    "volume",
    "turnover",
    "transactions",
)

INDEX_FIELDS = ("trade_date", "index_name", "close", "change", "change_pct")

# 可用於排序的欄位（screen_stocks 的白名單）
SORTABLE_FIELDS = ("change_pct", "volume", "turnover", "close")

# 大盤在 MI_INDEX 裡的名稱。不是「台股指數」或「加權指數」。
TAIEX_INDEX_NAME = "發行量加權股價指數"

# STOCK_DAY_ALL 原始欄位 → 正規化欄位
_QUOTE_NUMERIC = {
    "OpeningPrice": "open",
    "HighestPrice": "high",
    "LowestPrice": "low",
    "ClosingPrice": "close",
    "Change": "change",
}
_QUOTE_INTEGER = {
    "TradeVolume": "volume",
    "TradeValue": "turnover",
    "Transaction": "transactions",
}
