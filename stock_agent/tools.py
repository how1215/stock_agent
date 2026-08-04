"""給 ReAct agent 用的工具。

四個原則（決定 agent 好不好用）：
1. 回傳緊湊文字而非 raw JSON dump — 全市場 1377 筆會塞爆 context。渲染邏輯在
   render.py，與 query.py 的 text 欄位共用，避免同一組格式被實作兩次。
2. 所有數字在 Python 算完（含張數、億元換算），LLM 不做算術。
3. 錯誤回可行動的訊息而非丟出例外，讓模型能自我修正重試。
4. 每次回傳都帶交易日 — 盤中問「今天」會拿到前一交易日，模型必須有資訊如實告知。
"""

from langchain_core.tools import tool

from . import query, render
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


ALL_TOOLS = [get_market_index, get_stock_quote, screen_stocks]
