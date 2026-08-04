"""map-reduce 掃描的 state 定義。

`readings` 用 reducer 累積 — fan-out 出去的每個分支各自回傳一筆，LangGraph
在 fan-in 時把它們合併。分支數在執行時才由篩選結果決定，這是 Send 存在的理由，
也是 create_react_agent 的線性 loop 做不到的事。
"""

import operator
from typing import Annotated, TypedDict

# 逐檔判讀的結論。刻意只有三檔，讓小模型好分類、也讓 aggregate 好分組。
VERDICTS = ("符合", "部分符合", "不符合")


class Reading(TypedDict):
    """單一個股的判讀結果。"""

    code: str
    name: str
    verdict: str
    note: str
    error: str  # 空字串表示成功；有值表示這個分支失敗但沒拖垮整批


class ReadTask(TypedDict):
    """Send 給 read_one 分支的 payload。

    刻意只帶該分支需要的東西，不帶整個 state — 分支之間互不可見，
    一檔的判讀不會被另一檔汙染。
    """

    focus: str
    trade_date: str
    quote: dict


class ScanState(TypedDict):
    """整個掃描流程的 state。"""

    # 輸入。criteria 支援兩種挑候選方式：
    #   {"codes": [...]}                    → 只判讀指定幾檔（自選股走這條）
    #   {"sort_by": ..., "limit": ..., ...} → 按條件篩全市場
    focus: str
    criteria: dict
    date: str | None

    # select_candidates 產出
    trade_date: str
    market_index: dict
    candidates: list[dict]

    # map 階段累積（reducer）
    readings: Annotated[list[Reading], operator.add]

    # aggregate 產出
    grouped: dict[str, list[Reading]]
    failed: list[Reading]

    # 輸出
    report: str


class BriefingState(TypedDict):
    """盤前簡報的 state。

    與 ScanState 的差別在平行的性質：這裡是三個**異質**分支
    （market_snapshot / watchlist_read / market_movers）從 START 同時出發、
    匯合到 compose_briefing。分支數固定，所以不需要 Send，也不需要 reducer —
    三個分支各寫不同欄位，不會互相覆寫。
    """

    # 輸入
    report_date: str
    date: str | None

    # 三個平行分支各自的產出
    trade_date: str
    market_index: dict
    breadth: dict
    watchlist: dict
    movers: dict

    # 綜合與落地
    observation: str
    report: str
    report_path: str
    skipped: str  # 非空表示這次沒有產生新報告（例如沒有新交易日）
