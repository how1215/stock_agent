"""盤前簡報 graph。

```
START ─┬─► market_snapshot   確定性：大盤指數 + 市場寬度
       ├─► watchlist_read    呼叫 scan graph 當 subgraph 判讀自選股
       └─► market_movers     確定性：漲幅 / 跌幅 / 成交金額前段
                  │
                  ▼  三個分支匯合（LangGraph 預設等齊才往下走）
            compose_briefing  1 次 LLM：只看上面算好的數字寫綜合觀察
                  │
                  ▼
            persist_report    確定性：寫 reports/<報告日>-premarket.md
                  │
                 END
```

這張 graph 與 scan graph 的形狀刻意不同，展示的是另一組能力：

|            | scan graph                   | briefing graph                     |
|------------|------------------------------|------------------------------------|
| 平行性     | 動態、同質（N 檔，執行時才知道） | 靜態、異質（3 個不同工作）           |
| 機制       | `Send` 條件邊                 | START 出三條邊，匯合節點自動等齊     |
| 組合       | 無                           | 內嵌 scan graph 當 subgraph         |

三個分支彼此獨立、且都要等齊才能綜合，這正是 graph 的 join 語意；用函式寫
就得自己開 thread pool 並手動等待。

節點分工照既有慣例：只有 compose_briefing 碰模型，其餘全部確定性。
"""

from functools import partial

from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, START, StateGraph

from config import BRIEFING_MOVERS_LIMIT, SCAN_MAX_CONCURRENCY

from .. import render, report as report_mod, screen as screening, watchlist as watchlist_mod
from ..sources import cache
from ..sources.schema import TAIEX_INDEX_NAME
from .graph import build_scan_graph
from .state import BriefingState

# 自選股判讀的判斷軸。必須是**會區分**的條件 — 早期版本問「是否值得留意」，
# 結果每一檔都判「符合」（弱勢股當然也值得留意），verdict 完全沒有資訊量。
# 改成問買盤強弱後，「不符合」本身就是有用訊息（= 昨日偏弱）。
WATCHLIST_FOCUS = (
    "昨日收盤是否呈現買盤佔優：收紅、收盤位置偏高（收在當日高位）、"
    "且沒有明顯的開高走低"
)

_OBSERVATION_PROMPT = """你是台股盤前助理。根據以下**已經算好**的數據寫一段綜合觀察。

依據交易日：{trade_date}

大盤：{index_name} 收 {index_close}，漲跌 {index_change} 點（{index_change_pct}）
市場寬度：上漲 {advancing} 家、下跌 {declining} 家、平盤 {unchanged} 家，\
漲停 {limit_up} 檔、跌停 {limit_down} 檔（共 {total} 檔普通股）

自選股判讀：
{watchlist_lines}

市場焦點：
{movers_lines}

要求：
- 繁體中文，3 到 5 句，先講大盤氛圍再點出自選股與焦點股值得留意的地方。
- **只能引用上面出現過的數字**，不得自行計算、換算或推估。
- 不要預測走勢、不要給買賣建議。
- 不要重複列出所有個股，挑值得講的講。
"""


# --- 三個平行分支 ---


def market_snapshot(state: BriefingState) -> dict:
    """大盤指數與市場寬度。純確定性。"""
    day = cache.load_or_fetch(state.get("date"))
    index = next((i for i in day["indices"] if i["index_name"] == TAIEX_INDEX_NAME), None)
    return {
        "trade_date": day["trade_date"],
        "market_index": index or {},
        "breadth": screening.breadth(day["quotes"]),
    }


def market_movers(state: BriefingState) -> dict:
    """漲幅 / 跌幅 / 成交金額前段。純確定性。"""
    day = cache.load_or_fetch(state.get("date"))
    limit = BRIEFING_MOVERS_LIMIT

    def top(sort_by: str, order: str) -> list[dict]:
        return screening.screen(day["quotes"], sort_by=sort_by, order=order, limit=limit)

    return {
        "movers": {
            "gainers": top("change_pct", "desc"),
            "losers": top("change_pct", "asc"),
            "turnover": top("turnover", "desc"),
        }
    }


def watchlist_read(llm: BaseChatModel, state: BriefingState) -> dict:
    """自選股逐檔判讀 — 直接重用 scan graph 當 subgraph。

    清單為空時不要讓 subgraph 空轉，直接回空結果。
    """
    codes = watchlist_mod.load()
    if not codes:
        return {
            "watchlist": {
                "focus": WATCHLIST_FOCUS,
                "codes": [],
                "readings": [],
                "not_found": [],
                "quote_lines": [],
            }
        }

    result = build_scan_graph(llm).invoke(
        {
            "focus": WATCHLIST_FOCUS,
            "criteria": {"codes": codes},
            "date": state.get("date"),
            "readings": [],
        },
        # subgraph 不會繼承父 graph 的 config，要明確傳並行度。
        # 少了這行，自選股判讀會退回序列執行（實測 4 檔多花約一分鐘）。
        config={"max_concurrency": SCAN_MAX_CONCURRENCY},
    )

    found_codes = {q["code"] for q in result["candidates"]}
    return {
        "watchlist": {
            "focus": WATCHLIST_FOCUS,
            "codes": codes,
            "readings": result["readings"],
            "not_found": [code for code in codes if code not in found_codes],
            "quote_lines": [render.quote_line(q) for q in result["candidates"]],
        }
    }


# --- 匯合後 ---


def compose_briefing(llm: BaseChatModel, state: BriefingState) -> dict:
    """唯一呼叫模型的節點。失敗時降級，不讓整份報告消失。"""
    index, breadth = state["market_index"], state["breadth"]

    watchlist_lines = "\n".join(
        f"- {r['code']} {r['name']}：{r['verdict']}｜{r['note'] or r['error']}"
        for r in state["watchlist"].get("readings", [])
    ) or "（自選股清單為空）"

    movers_lines = "\n".join(
        f"- {label}：" + "、".join(
            f"{q['code']} {q['name']} {render.fmt_pct(q['change_pct'])}"
            for q in state["movers"].get(key, [])
        )
        for label, key in (("漲幅", "gainers"), ("跌幅", "losers"), ("成交金額", "turnover"))
    )

    prompt = _OBSERVATION_PROMPT.format(
        trade_date=state["trade_date"],
        index_name=index.get("index_name", "加權指數"),
        index_close=render.fmt_price(index.get("close")),
        index_change=render.fmt_price(index.get("change")),
        index_change_pct=render.fmt_pct(index.get("change_pct")),
        watchlist_lines=watchlist_lines,
        movers_lines=movers_lines,
        **breadth,
    )

    try:
        observation = str(llm.invoke(prompt).content).strip()
    except Exception as exc:
        observation = f"（綜合觀察產生失敗：{exc!r}。上方各段數據仍為實際查得結果。）"

    return {"observation": observation}


def persist_report(state: BriefingState) -> dict:
    """組版並寫檔。純確定性 — 標題與數字不經模型。"""
    text = report_mod.compose(
        report_date=state["report_date"],
        trade_date=state["trade_date"],
        index=state["market_index"],
        breadth=state["breadth"],
        watchlist=state["watchlist"],
        movers=state["movers"],
        observation=state["observation"],
    )
    return {"report": text, "report_path": report_mod.save(state["report_date"], text)}


def build_briefing_graph(llm: BaseChatModel):
    """組出簡報 graph。llm 由外部注入，測試才能塞假模型。"""
    graph = StateGraph(BriefingState)

    graph.add_node("market_snapshot", market_snapshot)
    graph.add_node("watchlist_read", partial(watchlist_read, llm))
    graph.add_node("market_movers", market_movers)
    graph.add_node("compose_briefing", partial(compose_briefing, llm))
    graph.add_node("persist_report", persist_report)

    # 三條邊從 START 出發 → LangGraph 平行執行
    for branch in ("market_snapshot", "watchlist_read", "market_movers"):
        graph.add_edge(START, branch)
        # 匯合：compose_briefing 會等三個分支都完成才執行
        graph.add_edge(branch, "compose_briefing")

    graph.add_edge("compose_briefing", "persist_report")
    graph.add_edge("persist_report", END)

    return graph.compile()
