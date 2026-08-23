"""掃描流程的 StateGraph 組裝。

```
START → select_candidates ──(無候選)──────────────► no_candidates → END
                          └─(Send × N)─► read_one ─► aggregate → summarise → END
```

為什麼這裡值得自己寫 graph 而不是用 create_react_agent：

- fan-out 的分支數在執行時才由篩選結果決定 → 需要 Send
- 分支要並行、且單一分支失敗不能拖垮整批 → 需要節點級的隔離
- fan-in 要把 N 筆結果合併回一個 state → 需要 reducer

這三件事 ReAct 的線性 tool loop 都做不到。反過來說，若流程沒有動態分支、
不需要恢復狀態、也沒有真實的條件分歧，那用函式串起來就好 — 包成 graph 只是儀式。
"""

from functools import partial

from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, START, StateGraph

from . import nodes
from .state import ScanState


def build_scan_graph(llm: BaseChatModel):
    """組出掃描 graph。llm 由外部注入，測試才能塞假模型而不需要 vLLM。"""
    graph = StateGraph(ScanState)

    graph.add_node("select_candidates", nodes.select_candidates)
    graph.add_node("read_one", partial(nodes.read_one, llm))
    graph.add_node("aggregate", nodes.aggregate)
    graph.add_node("summarise", partial(nodes.summarise, llm))
    graph.add_node("no_candidates", nodes.no_candidates)

    graph.add_edge(START, "select_candidates")
    # fan_out 回傳 list[Send] 展開分支，或回傳 "no_candidates" 走短路
    graph.add_conditional_edges("select_candidates", nodes.fan_out, ["read_one", "no_candidates"])
    graph.add_edge("read_one", "aggregate")
    graph.add_edge("aggregate", "summarise")
    graph.add_edge("summarise", END)
    graph.add_edge("no_candidates", END)

    return graph.compile()
