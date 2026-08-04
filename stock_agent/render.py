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


def compact_line(quote: dict) -> str:
    """一行摘要，只有掃一眼需要的欄位。

    給市場焦點這種「誰在動」的清單用。完整的 quote_line 有開高低收前收量額，
    一行約 130 字元；15 檔就佔掉報告六成篇幅（實測 1,717 / 2,799 字元），
    而那些欄位對「誰在動」這個問題沒有貢獻。需要細節時再用 stock_quote 查那幾檔。
    """
    return (
        f"{quote['code']} {quote['name']}｜收 {fmt_price(quote['close'])}"
        f"｜{fmt_pct(quote['change_pct'])}｜額 {fmt_turnover(quote['turnover'])}"
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


def render_watchlist(described: dict) -> str:
    """自選股清單。CLI 與 JSON payload 的 text 欄位共用。"""
    if not described["codes"]:
        return "自選股清單為空。用 `watchlist add <代號或名稱>` 加入。"

    lines = [
        f"交易日 {described['trade_date']}（上市）｜自選股 {len(described['codes'])} 檔",
        *(quote_line(q) for q in described["quotes"]),
    ]
    if described.get("not_found"):
        lines.append(
            f"清單中查不到：{'、'.join(described['not_found'])}（可能已下市或代號有誤）"
        )
    return "\n".join(lines)


_CHANGE_LABELS = (
    ("加入", "added"),
    ("已在清單", "already_present"),
    ("移除", "removed"),
    ("查不到", "not_found"),
    ("不在清單中", "not_in_list"),
)


def render_watchlist_change(result: dict) -> str:
    """add / remove / clear 的結果。always 回報操作後的完整清單，
    讓使用者看得到實際發生了什麼，而不只是「成功」。"""
    lines = []
    for label, key in _CHANGE_LABELS:
        rows = result.get(key) or []
        if not rows:
            continue
        shown = [r if isinstance(r, str) else f"{r['code']} {r['name']}" for r in rows]
        lines.append(f"{label}：{'、'.join(shown)}")

    codes = result.get("codes", [])
    watchlist = result.get("watchlist") or []
    shown = "、".join(f"{w['code']} {w['name']}" for w in watchlist) or "、".join(codes)
    lines.append(f"目前自選股（{len(codes)} 檔）：{shown or '（空）'}")
    return "\n".join(lines)


def render_sort_field_error(message: str) -> str:
    return f"參數錯誤：{message}。可用的排序欄位為 {'、'.join(SORTABLE_FIELDS)}。"
