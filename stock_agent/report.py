"""盤前簡報的存取與組版。

檔名用**報告日**（今天），內文標明**依據的交易日**（前一交易日）。這兩個日期
不同，混淆會直接誤導 — 8/4 早上的報告依據的是 8/3 收盤。

所有標題與數字由 Python 決定性寫出，只有「綜合觀察」那一段來自模型。
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import config

from . import render

TAIPEI = ZoneInfo("Asia/Taipei")
DISCLAIMER = "本報告為公開數據整理，非投資建議。"


def today() -> str:
    """台北時區的今天。報告日以市場所在時區為準，不是 UTC 也不是機器時區。"""
    return datetime.now(TAIPEI).date().isoformat()


def _path(report_date: str):
    return config.REPORTS_DIR / f"{report_date}-premarket.md"


def exists(report_date: str) -> bool:
    return _path(report_date).is_file()


def load(report_date: str) -> str | None:
    path = _path(report_date)
    return path.read_text(encoding="utf-8") if path.is_file() else None


def save(report_date: str, text: str) -> str:
    path = _path(report_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)


def existing_trade_date(report_date: str) -> str | None:
    """從既有報告撈出它依據的交易日，用來判斷是否已無新資料可報。"""
    text = load(report_date)
    if not text:
        return None
    for line in text.splitlines():
        if line.startswith("# 盤前提要") and "依據" in line:
            # 「# 盤前提要 2026-08-04（依據 2026-08-03 收盤）」
            tail = line.split("依據", 1)[1]
            return tail.strip().split(" ", 1)[0].strip()
    return None


def _market_section(index: dict, breadth: dict) -> str:
    lines = [
        f"{index.get('index_name', '加權指數')} "
        f"{render.fmt_price(index.get('close'))}"
        f"（{render.fmt_price(index.get('change'))} 點，"
        f"{render.fmt_pct(index.get('change_pct'))}）",
        f"上漲 {breadth['advancing']} 家、下跌 {breadth['declining']} 家、"
        f"平盤 {breadth['unchanged']} 家（共 {breadth['total']} 檔普通股）",
        f"漲停 {breadth['limit_up']} 檔、跌停 {breadth['limit_down']} 檔",
    ]
    return "\n".join(lines)


def _watchlist_section(watchlist: dict) -> str:
    if not watchlist.get("codes"):
        return "（自選股清單為空。用 `watchlist add <代號或名稱>` 加入。）"

    # 把判斷軸寫出來，否則「符合」是什麼意思讀者無從得知
    lines = []
    if focus := watchlist.get("focus"):
        lines.append(f"_判斷軸：{focus}_")
        lines.append("")
    for reading in watchlist.get("readings", []):
        mark = "⚠" if reading["error"] else "・"
        note = reading["note"] or reading["error"] or "（無判讀）"
        lines.append(f"{mark} {reading['code']} {reading['name']}｜{reading['verdict']}｜{note}")

    for line in watchlist.get("quote_lines", []):
        lines.append(f"  {line}")

    if missing := watchlist.get("not_found"):
        lines.append(f"清單中查不到：{'、'.join(missing)}（可能已下市或代號有誤）")

    return "\n".join(lines) if lines else "（無判讀結果）"


def _movers_section(movers: dict) -> str:
    """市場焦點用精簡行。

    這一段是「誰在動」的掃視清單。改用精簡行後這一段從 1,717 降到 613 字元、
    整份報告從 2,799 降到 1,695 —— 而開高低收前收量這些欄位對「誰在動」沒有貢獻。
    要細節就用 stock_quote 查那幾檔。

    注意：這不是為了解決 Pi 的 context 壓縮。實測壓縮在不載入任何 extension
    與 skill、只問一個字時照樣觸發，與報告大小無關。
    """
    blocks = []
    for title, key in (("漲幅前段", "gainers"), ("跌幅前段", "losers"), ("成交金額前段", "turnover")):
        rows = movers.get(key, [])
        if not rows:
            continue
        blocks.append(f"**{title}**")
        blocks.extend(f"  {render.compact_line(q)}" for q in rows)
    return "\n".join(blocks) if blocks else "（無資料）"


def compose(
    report_date: str,
    trade_date: str,
    index: dict,
    breadth: dict,
    watchlist: dict,
    movers: dict,
    observation: str,
) -> str:
    """組出完整報告。標題與數字全部決定性產生，只有 observation 來自模型。"""
    watch_count = len(watchlist.get("codes", []))
    return "\n".join([
        f"# 盤前提要 {report_date}（依據 {trade_date} 收盤）",
        "",
        "## 大盤",
        _market_section(index, breadth),
        "",
        f"## 自選股（{watch_count} 檔）",
        _watchlist_section(watchlist),
        "",
        "## 市場焦點",
        _movers_section(movers),
        "",
        "## 綜合觀察",
        observation.strip() or "（未產生）",
        "",
        "---",
        DISCLAIMER,
        "",
    ])
