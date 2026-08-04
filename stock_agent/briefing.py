"""盤前簡報的進入點。

負責「要不要重新生成」這個決定，graph 本身只管生成。

兩個不重算的情形：
1. 今天的報告已經存在，且沒有要求 refresh → 直接讀檔。生成要幾十秒（自選股
   逐檔判讀），每次現場等會讓助手不好用。
2. 已存在的報告依據的交易日，與現在能拿到的最新交易日相同 → 沒有新資料可報。
   假日與週末排程執行時就是這個情形，此時不該覆寫既有報告。
"""

from . import report as report_mod
from .llm import build_llm
from .sources import cache
from .workflow.briefing import build_briefing_graph

_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        _graph = build_briefing_graph(build_llm())
    return _graph


def _latest_trade_date(date: str | None) -> str:
    return cache.load_or_fetch(date)["trade_date"]


def get_briefing(
    *,
    report_date: str | None = None,
    date: str | None = None,
    refresh: bool = False,
) -> dict:
    """取得盤前簡報。

    Args:
        report_date: 報告日，預設為台北時區的今天。
        date: 依據的交易日，預設取最新（TWSE 端點只給最新交易日）。
        refresh: True 則強制重新生成。

    Returns:
        text / report_date / trade_date / source（cache | generated | skipped）
    """
    report_date = report_date or report_mod.today()

    if not refresh:
        if cached := report_mod.load(report_date):
            return {
                "report_date": report_date,
                "trade_date": report_mod.existing_trade_date(report_date) or "",
                "source": "cache",
                "text": cached,
            }

    trade_date = _latest_trade_date(date)

    # 盤前提要的定義是「今天的報告、依據之前的收盤」。資料日晚於報告日是不可能
    # 成立的組合（例如 --report-date 2020-01-02 配上最新的 2026 資料），
    # 若放行就會產出一份日期自相矛盾、看起來卻很正常的報告。
    if trade_date > report_date:
        raise ValueError(
            f"報告日 {report_date} 早於可取得的交易日 {trade_date}。"
            "盤前提要必須依據報告日之前的收盤資料，無法回溯產生歷史報告"
            "（TWSE 端點只提供最新交易日）。"
        )

    # 已有報告且依據的交易日沒變 → 沒有新資料，不覆寫
    if refresh and report_mod.existing_trade_date(report_date) == trade_date:
        return {
            "report_date": report_date,
            "trade_date": trade_date,
            "source": "skipped",
            "skipped": f"{report_date} 的報告已依據 {trade_date} 收盤產生，沒有更新的交易日資料。",
            "text": report_mod.load(report_date) or "",
        }

    result = _get_graph().invoke({"report_date": report_date, "date": date})
    return {
        "report_date": report_date,
        "trade_date": result["trade_date"],
        "source": "generated",
        "report_path": result["report_path"],
        "text": result["report"],
    }
