"""給 ReAct agent 用的工具。

四個原則（決定 agent 好不好用）：
1. 回傳緊湊文字而非 raw JSON dump — 全市場 1377 筆會塞爆 context。渲染邏輯在
   render.py，與 query.py 的 text 欄位共用，避免同一組格式被實作兩次。
2. 所有數字在 Python 算完（含張數、億元換算），LLM 不做算術。
3. 錯誤回可行動的訊息而非丟出例外，讓模型能自我修正重試。
4. 每次回傳都帶交易日 — 盤中問「今天」會拿到前一交易日，模型必須有資訊如實告知。
"""

from langchain_core.tools import tool

from config import MAX_SCAN_LIMIT

from . import query, render
from .scan import run_scan
from .sources.cache import DataUnavailable


@tool
def get_market_index(date: str | None = None) -> str:
    """查詢台股大盤（發行量加權股價指數）的收盤點數與漲跌。

    用於回答「大盤怎麼樣」、「今天盤勢如何」、「加權指數收多少」這類問題。

    Args:
        date: 交易日，格式 YYYY-MM-DD。省略則取最新交易日（建議省略）。
    """
    try:
        result = query.market(date)
    except (DataUnavailable, LookupError) as exc:
        return f"查不到資料：{exc}"
    return render.render_market(result["trade_date"], result["index"])


@tool
def get_stock_quote(codes: list[str], date: str | None = None) -> str:
    """查詢指定台股上市個股的當日開高低收、漲跌幅與成交量。

    Args:
        codes: 股票代號或公司名稱的清單，例如 ["2330", "鴻海"]。
            兩種都支援，不確定代號時直接傳公司名稱，不要憑記憶猜代號。
        date: 交易日，格式 YYYY-MM-DD。省略則取最新交易日（建議省略）。
    """
    try:
        result = query.quote(codes, date)
    except ValueError as exc:
        return f"請提供至少一個股票代號或公司名稱。（{exc}）"
    except DataUnavailable as exc:
        return f"查不到資料：{exc}"
    return render.render_quotes(result["trade_date"], result["quotes"], result["not_found"])


@tool
def screen_stocks(
    sort_by: str,
    order: str = "desc",
    limit: int = 20,
    min_change_pct: float | None = None,
    min_volume: int | None = None,
    common_stock_only: bool = True,
    date: str | None = None,
) -> str:
    """依條件篩選並排序全市場台股上市個股。

    用於「今天漲最多的前 N 檔」、「成交量最大的股票」、「漲超過 5% 的股票」
    這類需要掃過整個市場的問題。查特定幾檔請改用 get_stock_quote。

    Args:
        sort_by: 排序欄位，必須是 change_pct（漲跌幅）、volume（成交股數）、
            turnover（成交金額）、close（收盤價）之一。
        order: desc 由大到小（漲最多、量最大），asc 由小到大（跌最多）。
        limit: 回傳檔數，1 到 100。
        min_change_pct: 只留漲跌幅大於等於此值的個股，例如 5 表示漲超過 5%。
        min_volume: 只留成交股數大於等於此值的個股。
        common_stock_only: True 只看普通股（排除 ETF 與受益證券），預設 True。
        date: 交易日，格式 YYYY-MM-DD。省略則取最新交易日（建議省略）。
    """
    try:
        result = query.screen(
            sort_by=sort_by,
            order=order,
            limit=limit,
            min_change_pct=min_change_pct,
            min_volume=min_volume,
            common_stock_only=common_stock_only,
            date=date,
        )
    except DataUnavailable as exc:
        return f"查不到資料：{exc}"
    except ValueError as exc:
        return render.render_sort_field_error(str(exc))
    return render.render_screen(result["trade_date"], result["criteria"], result["matches"])


@tool
def scan_market(
    focus: str,
    sort_by: str = "turnover",
    order: str = "desc",
    limit: int = 15,
    min_change_pct: float | None = None,
    min_volume: int | None = None,
    date: str | None = None,
) -> str:
    """對候選個股逐檔判讀，找出符合某種「說得出來但寫不成數字條件」的股票。

    適用於開放式的描述性問題，例如「找出今天收盤收在最高、看起來買盤強勢的股票」、
    「哪些股票今天開高走低、賣壓明顯」。這類條件無法用 screen_stocks 的數值篩選
    表達，需要逐檔判讀。

    比 screen_stocks 慢很多（每檔一次判讀），所以：
    - 純數值條件（漲幅前 N 名、成交量最大）請用 screen_stocks，不要用這個
    - 查特定幾檔請用 get_stock_quote

    Args:
        focus: 使用者想找的特徵，用自然語言描述，越具體越好。
        sort_by: 先用哪個欄位挑候選：turnover（成交金額）、volume（成交股數）、
            change_pct（漲跌幅）、close（收盤價）。預設 turnover 取市場焦點股。
        order: desc 由大到小，asc 由小到大。
        limit: 掃描幾檔，1 到 30。預設 15。越大越慢。
        min_change_pct: 候選只留漲跌幅大於等於此值的個股。
        min_volume: 候選只留成交股數大於等於此值的個股。
        date: 交易日 YYYY-MM-DD，省略取最新交易日（建議省略）。
    """
    if not focus.strip():
        return "請說明要找什麼特徵（focus），例如「收盤收在最高且成交金額大」。"

    try:
        result = run_scan(
            focus,
            sort_by=sort_by,
            order=order,
            limit=limit,
            min_change_pct=min_change_pct,
            min_volume=min_volume,
            date=date,
        )
    except DataUnavailable as exc:
        return f"查不到資料：{exc}"
    except ValueError as exc:
        return render.render_sort_field_error(str(exc))

    report = result["report"]
    if len(result.get("candidates", [])) >= MAX_SCAN_LIMIT:
        report += f"\n（註：一次最多掃描 {MAX_SCAN_LIMIT} 檔，結果已截斷）"
    return report


ALL_TOOLS = [get_market_index, get_stock_quote, screen_stocks, scan_market]
