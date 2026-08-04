"""把資料渲染成給 LLM 讀的緊湊文字。

抽出來共用是為了避免同一組呈現規則被實作兩次 — tools.py（給 Python agent）
與 query.py 的 text 欄位（給 Pi extension 之類的外部呼叫者）都走這裡，
數字格式與措辭永遠一致。

原則：所有換算（張數、億元、漲跌幅）在這裡算完，LLM 不做算術。
"""

from .sources.schema import SORTABLE_FIELDS


def fmt_price(value: float | None) -> str:
    return "—" if value is None else f"{value:,.2f}"


def fmt_pct(value: float | None) -> str:
    return "—" if value is None else f"{value:+.2f}%"


def fmt_volume(shares: int | None) -> str:
    if shares is None:
        return "—"
    return f"{shares:,} 股（{shares // 1000:,} 張）"


def fmt_turnover(amount: int | None) -> str:
    if amount is None:
        return "—"
    return f"{amount / 1e8:,.2f} 億元"


def quote_line(quote: dict) -> str:
    return (
        f"{quote['code']} {quote['name']}｜收 {fmt_price(quote['close'])}"
        f"｜漲跌 {fmt_price(quote['change'])} ({fmt_pct(quote['change_pct'])})"
        f"｜前收 {fmt_price(quote['prev_close'])}"
        f"｜開 {fmt_price(quote['open'])} 高 {fmt_price(quote['high'])} 低 {fmt_price(quote['low'])}"
        f"｜量 {fmt_volume(quote['volume'])}｜額 {fmt_turnover(quote['turnover'])}"
    )


MISSING_CODE_HINT = (
    "上市普通股代號為 4 位數字、首碼 1–9（如 2330），ETF 以 0 開頭（如 0050）。"
    "也可以直接用公司全名查詢，例如「台積電」。"
    "若該檔為上櫃股票，本工具查不到（僅涵蓋上市）。"
)


def render_market(trade_date: str, index: dict) -> str:
    return (
        f"交易日 {trade_date}（上市）\n"
        f"{index['index_name']}｜收盤 {fmt_price(index['close'])}"
        f"｜漲跌 {fmt_price(index['change'])} 點 ({fmt_pct(index['change_pct'])})"
    )


def render_quotes(trade_date: str, quotes: list[dict], not_found: list[str]) -> str:
    lines = [f"交易日 {trade_date}（上市）"]
    lines.extend(quote_line(q) for q in quotes)
    if not_found:
        lines.append(f"查不到：{'、'.join(not_found)}。{MISSING_CODE_HINT}")
    return "\n".join(lines)


def render_screen(trade_date: str, criteria: dict, matches: list[dict]) -> str:
    if not matches:
        return f"交易日 {trade_date}：沒有符合條件的個股，可以放寬篩選條件再試。"

    filters = [f"依 {criteria['sort_by']} {'遞減' if criteria['order'] == 'desc' else '遞增'}"]
    if criteria.get("common_stock_only"):
        filters.append("僅普通股")
    if criteria.get("min_change_pct") is not None:
        filters.append(f"漲跌幅 ≥ {criteria['min_change_pct']}%")
    if criteria.get("min_volume") is not None:
        filters.append(f"成交量 ≥ {criteria['min_volume']:,} 股")

    header = f"交易日 {trade_date}（上市）｜{'、'.join(filters)}｜共 {len(matches)} 檔"
    return "\n".join([header, *(quote_line(q) for q in matches)])


def render_sort_field_error(message: str) -> str:
    return f"參數錯誤：{message}。可用的排序欄位為 {'、'.join(SORTABLE_FIELDS)}。"
