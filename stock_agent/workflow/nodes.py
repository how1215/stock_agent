"""掃描流程的節點。

節點分兩類，界線刻意畫清楚：

- 確定性節點（select_candidates / aggregate / no_candidates）完全不碰 LLM，
  可以用 fixtures 直接測。
- LLM 節點（read_one / summarise）把 llm 當第一個參數收，graph 組裝時用
  functools.partial 綁進去 — 測試因此能塞假模型，不需要 vLLM 在跑。
"""

from langchain_core.language_models import BaseChatModel
from langgraph.types import Send

from .. import render, screen as screening
from ..sources import cache
from ..sources.schema import TAIEX_INDEX_NAME
from .state import VERDICTS, ReadTask, Reading, ScanState

# read_one 的輸出格式。刻意不用 JSON — 小模型產生單行分隔字串比產生合法
# JSON 可靠得多，而這裡只需要兩個欄位。
_DELIMITER = "|||"

# 由長到短，供模型未照格式輸出時的子字串退路使用。順序有意義：
# 「符合」是「部分符合」與「不符合」的子字串，短的先比會誤判。
_VERDICTS_BY_SPECIFICITY = tuple(sorted(VERDICTS, key=len, reverse=True))

_READ_PROMPT = """你是台股數據判讀助理。判斷以下這一檔個股是否符合使用者的關注點。

使用者的關注點：{focus}

交易日 {trade_date} 的數據（全部已算好，不要自己再計算）：
{facts}

只輸出一行，格式如下（不要加任何其他文字、不要換行）：
判斷{delimiter}一句話理由

判斷只能是這三個之一：{verdicts}
理由必須引用上面的數據，不得引入上面沒有的數字。"""

_SUMMARY_PROMPT = """你是台股數據判讀助理。以下是逐檔掃描的結果，請寫一段總結。

使用者的關注點：{focus}
交易日 {trade_date}
大盤（{index_name}）：收盤 {index_close}，漲跌 {index_change} 點（{index_change_pct}）

掃描了 {scanned} 檔，其中符合 {matched} 檔、部分符合 {partial} 檔。

符合的個股：
{matched_lines}

要求：
- 用繁體中文，簡潔，先講整體再點出值得注意的幾檔。
- 只能引用上面出現過的數字，不得自行計算或推估。
- 明確講出交易日。
- 結尾加一句：以上為公開數據整理，非投資建議。"""


def _facts(quote: dict) -> str:
    """把一檔個股的數字整理成 LLM 讀的事實清單。"""
    metrics = screening.shape_metrics(quote)

    def pct(value: float | None) -> str:
        return "—" if value is None else f"{value:+.2f}%"

    position = metrics["close_position"]
    position_text = "—" if position is None else f"{position:.0f}"

    return "\n".join([
        f"- 代號名稱：{quote['code']} {quote['name']}",
        f"- 收盤：{render.fmt_price(quote['close'])}（前收 {render.fmt_price(quote['prev_close'])}）",
        f"- 漲跌幅：{render.fmt_pct(quote['change_pct'])}",
        f"- 開盤：{render.fmt_price(quote['open'])}"
        f"　最高：{render.fmt_price(quote['high'])}　最低：{render.fmt_price(quote['low'])}",
        f"- 開盤跳空：{pct(metrics['gap_pct'])}",
        f"- 振幅：{pct(metrics['amplitude_pct'])}",
        f"- 收盤位置：{position_text}（0 表示收在當日最低，100 表示收在最高）",
        f"- 成交量：{render.fmt_volume(quote['volume'])}",
        f"- 成交金額：{render.fmt_turnover(quote['turnover'])}",
    ])


# --- 確定性節點 ---


def select_candidates(state: ScanState) -> dict:
    """篩出候選個股。純確定性 — 縮小範圍這件事不需要 LLM。"""
    day = cache.load_or_fetch(state.get("date"))
    criteria = state["criteria"]
    candidates = screening.screen(
        day["quotes"],
        sort_by=criteria["sort_by"],
        order=criteria.get("order", "desc"),
        limit=criteria.get("limit", 20),
        min_change_pct=criteria.get("min_change_pct"),
        min_volume=criteria.get("min_volume"),
        common_stock_only=criteria.get("common_stock_only", True),
    )
    index = next((i for i in day["indices"] if i["index_name"] == TAIEX_INDEX_NAME), None)
    return {
        "trade_date": day["trade_date"],
        "market_index": index or {},
        "candidates": candidates,
    }


def fan_out(state: ScanState) -> list[Send] | str:
    """動態 fan-out：分支數由篩選結果決定，執行時才知道。

    這是 Send 的用途，也是這整張 graph 存在的理由 — create_react_agent
    的線性 loop 一次只能呼叫一個工具，無法展開 N 個並行判讀。
    """
    if not state["candidates"]:
        return "no_candidates"
    return [
        Send("read_one", ReadTask(
            focus=state["focus"],
            trade_date=state["trade_date"],
            quote=quote,
        ))
        for quote in state["candidates"]
    ]


def aggregate(state: ScanState) -> dict:
    """按判斷分組。純確定性 — 分類統計不該交給 LLM。"""
    readings = state["readings"]
    grouped = {verdict: [] for verdict in VERDICTS}
    failed = []
    for reading in readings:
        if reading["error"]:
            failed.append(reading)
        else:
            grouped.setdefault(reading["verdict"], []).append(reading)
    return {"grouped": grouped, "failed": failed}


def no_candidates(state: ScanState) -> dict:
    return {
        "report": (
            f"交易日 {state['trade_date']}：沒有符合篩選條件的個股，"
            "可以放寬條件（例如降低 min_change_pct 或提高 limit）再試。"
        )
    }


# --- LLM 節點 ---


def read_one(llm: BaseChatModel, task: ReadTask) -> dict:
    """判讀單一個股。一個分支一次 LLM 呼叫。

    失敗不拋出 — 單一分支的模型錯誤或格式錯誤只影響那一檔，整批照樣完成。
    這是 fan-out 相對於「一個大 prompt 塞 N 檔」的實質好處之一。
    """
    quote = task["quote"]
    base = {"code": quote["code"], "name": quote["name"]}

    prompt = _READ_PROMPT.format(
        focus=task["focus"],
        trade_date=task["trade_date"],
        facts=_facts(quote),
        delimiter=_DELIMITER,
        verdicts=" / ".join(VERDICTS),
    )

    try:
        raw = llm.invoke(prompt).content
    except Exception as exc:
        return {"readings": [Reading(**base, verdict="不符合", note="", error=repr(exc))]}

    text = (raw if isinstance(raw, str) else str(raw)).strip()
    verdict, _, note = text.partition(_DELIMITER)
    verdict, note = verdict.strip(), note.strip()

    if verdict not in VERDICTS:
        # 模型沒照格式輸出：能撈到判斷詞就用，否則整段當理由留存以便除錯。
        # 必須由長到短比對 —「符合」是「部分符合」與「不符合」的子字串，
        # 照原順序比會把「不符合」誤判成「符合」。
        matched = next((v for v in _VERDICTS_BY_SPECIFICITY if v in text), None)
        if matched is None:
            return {"readings": [Reading(
                **base, verdict="不符合", note="",
                error=f"無法解析模型輸出：{text[:120]}",
            )]}
        verdict, note = matched, note or text

    return {"readings": [Reading(**base, verdict=verdict, note=note, error="")]}


def summarise(llm: BaseChatModel, state: ScanState) -> dict:
    """一次 LLM 呼叫，只看聚合結果與大盤 — 不重看原始個股資料。"""
    grouped, index = state["grouped"], state["market_index"]
    matched = grouped.get("符合", [])
    partial = grouped.get("部分符合", [])

    matched_lines = "\n".join(
        f"- {r['code']} {r['name']}：{r['note']}" for r in matched
    ) or "（無）"

    prompt = _SUMMARY_PROMPT.format(
        focus=state["focus"],
        trade_date=state["trade_date"],
        index_name=index.get("index_name", "加權指數"),
        index_close=render.fmt_price(index.get("close")),
        index_change=render.fmt_price(index.get("change")),
        index_change_pct=render.fmt_pct(index.get("change_pct")),
        scanned=len(state["readings"]),
        matched=len(matched),
        partial=len(partial),
        matched_lines=matched_lines,
    )

    try:
        summary = llm.invoke(prompt).content
    except Exception as exc:
        summary = f"（總結產生失敗：{exc!r}，以下僅列出逐檔判讀結果）"

    # 交易日由 Python 決定性寫出，不依賴模型記得提 — 其他三個工具都是這樣，
    # 這裡若只靠總結裡的措辭，模型漏講就等於少了「這是哪一天的數據」。
    lines = [
        f"交易日 {state['trade_date']}（上市）｜關注點：{state['focus']}",
        "",
        str(summary).strip(),
        "",
        f"逐檔判讀（掃描 {len(state['readings'])} 檔）：",
    ]
    for verdict in VERDICTS:
        for reading in grouped.get(verdict, []):
            lines.append(f"  [{verdict}] {reading['code']} {reading['name']}：{reading['note']}")
    if state["failed"]:
        lines.append(f"  判讀失敗 {len(state['failed'])} 檔："
                     + "、".join(f"{r['code']} {r['name']}" for r in state["failed"]))

    return {"report": "\n".join(lines)}
