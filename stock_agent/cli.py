"""命令列介面。

Agent 模式（會呼叫 vLLM）：

    python -m stock_agent.cli                      互動模式（多輪追問共用上下文）
    python -m stock_agent.cli ask "今天大盤怎麼樣？"  單次查詢
    python -m stock_agent.cli ask "..." --trace      同時印出 tool 呼叫過程
    python -m stock_agent.cli verify                 檢查 vLLM 的 tool calling
    python -m stock_agent.cli scan "收在最高的股票"    逐檔判讀掃描（直接跑 StateGraph）

資料模式（純 JSON，不碰 LLM）— Pi extension 等外部工具走這條：

    python -m stock_agent.cli market
    python -m stock_agent.cli quote 2330 鴻海
    python -m stock_agent.cli screen --sort-by volume --limit 10

資料模式的契約：**stdout 只有 JSON**，日誌一律走 stderr；失敗時 exit code 非 0
且 stdout 給 {"error": "..."}。外部呼叫者依賴這個契約，不要在 stdout 印別的東西。
"""

import argparse
import json
import sys
import time
from uuid import uuid4

from config import SCAN_MAX_CONCURRENCY

from . import query
from .agent import build_agent, verify_tool_calling
from .scan import run_scan, scan_payload
from .sources.cache import DataUnavailable
from .trace import TraceHandler


def _answer(agent, question: str, thread_id: str, handler: TraceHandler) -> str:
    result = agent.invoke(
        {"messages": [("user", question)]},
        config={"configurable": {"thread_id": thread_id}, "callbacks": [handler]},
    )
    return result["messages"][-1].content


def _run_once(question: str, show_trace: bool) -> int:
    thread_id = uuid4().hex[:12]
    handler = TraceHandler(thread_id, echo=show_trace)
    print(_answer(build_agent(), question, thread_id, handler))
    if show_trace:
        print(f"\n[trace] {handler.trace_path}", file=sys.stderr)
    return 0


def _run_repl(show_trace: bool) -> int:
    agent = build_agent()
    thread_id = uuid4().hex[:12]
    handler = TraceHandler(thread_id, echo=show_trace)

    print("台股數據查詢（資料來源：TWSE 開放資料，僅上市、收盤日資料）")
    print(f"thread: {thread_id}　輸入 exit 或 Ctrl-D 結束\n")

    while True:
        try:
            question = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not question:
            continue
        if question in {"exit", "quit"}:
            return 0
        try:
            print(_answer(agent, question, thread_id, handler), end="\n\n")
        except Exception as exc:
            print(f"執行失敗：{exc!r}\n", file=sys.stderr)


def _emit_json(build) -> int:
    """跑一個查詢並把結果印成 JSON。錯誤也走 JSON，讓呼叫者只需解析一種格式。"""
    try:
        payload = build()
    except (DataUnavailable, ValueError, LookupError) as exc:
        json.dump({"error": str(exc)}, sys.stdout, ensure_ascii=False)
        print()
        return 1
    except Exception as exc:  # 網路或端點異常
        json.dump({"error": f"{type(exc).__name__}: {exc}"}, sys.stdout, ensure_ascii=False)
        print()
        return 1

    json.dump(payload, sys.stdout, ensure_ascii=False)
    print()
    return 0


def _run_scan(args) -> int:
    """直接跑掃描 graph，不經 ReAct — 方便單獨驗證 graph 行為與量測延遲。"""
    if args.json:
        # Pi extension 等外部呼叫者走這條：契約與其他 JSON 子命令一致
        return _emit_json(lambda: scan_payload(
            args.focus,
            sort_by=args.sort_by,
            order=args.order,
            limit=args.limit,
            min_change_pct=args.min_change_pct,
            min_volume=args.min_volume,
            common_stock_only=not args.include_etf,
            date=args.date,
            max_concurrency=args.concurrency,
        ))

    started = time.perf_counter()
    try:
        result = run_scan(
            args.focus,
            sort_by=args.sort_by,
            order=args.order,
            limit=args.limit,
            min_change_pct=args.min_change_pct,
            min_volume=args.min_volume,
            common_stock_only=not args.include_etf,
            date=args.date,
            max_concurrency=args.concurrency,
        )
    except (DataUnavailable, ValueError) as exc:
        print(f"掃描失敗：{exc}", file=sys.stderr)
        return 1

    print(result["report"])
    elapsed = time.perf_counter() - started
    scanned = len(result["readings"])
    print(
        f"\n[掃描 {scanned} 檔，並行 {args.concurrency}，"
        f"耗時 {elapsed:.1f}s（平均 {elapsed / max(1, scanned):.1f}s/檔）]",
        file=sys.stderr,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="stock_agent", description="台股數據查詢 agent")
    parser.add_argument("--trace", action="store_true", help="印出 tool 呼叫過程")
    sub = parser.add_subparsers(dest="command")

    ask = sub.add_parser("ask", help="單次查詢（會呼叫 vLLM）")
    ask.add_argument("question")
    ask.add_argument("--trace", action="store_true", help="印出 tool 呼叫過程")

    sub.add_parser("verify", help="檢查 vLLM 的 tool calling 是否可用")

    scan = sub.add_parser("scan", help="逐檔判讀掃描（跑 StateGraph，不經 ReAct）")
    scan.add_argument("focus", help="要找什麼特徵，自然語言描述")
    scan.add_argument("--sort-by", default="turnover",
                      choices=["change_pct", "volume", "turnover", "close"])
    scan.add_argument("--order", default="desc", choices=["desc", "asc"])
    scan.add_argument("--limit", type=int, default=15)
    scan.add_argument("--min-change-pct", type=float)
    scan.add_argument("--min-volume", type=int)
    scan.add_argument("--include-etf", action="store_true")
    scan.add_argument("--concurrency", type=int, default=SCAN_MAX_CONCURRENCY,
                      help=f"並行判讀數，預設 {SCAN_MAX_CONCURRENCY}")
    scan.add_argument("--date", help="交易日 YYYY-MM-DD，省略取最新")
    scan.add_argument("--json", action="store_true",
                      help="輸出 JSON（stdout 只有 JSON），供外部工具呼叫")

    market = sub.add_parser("market", help="大盤指數（JSON）")
    market.add_argument("--date", help="交易日 YYYY-MM-DD，省略取最新")

    quote = sub.add_parser("quote", help="指定個股（JSON）")
    quote.add_argument("keys", nargs="+", help="股票代號或公司名稱")
    quote.add_argument("--date", help="交易日 YYYY-MM-DD，省略取最新")

    screen = sub.add_parser("screen", help="篩選排序全市場個股（JSON）")
    screen.add_argument("--sort-by", required=True,
                        choices=["change_pct", "volume", "turnover", "close"])
    screen.add_argument("--order", default="desc", choices=["desc", "asc"])
    screen.add_argument("--limit", type=int, default=20)
    screen.add_argument("--min-change-pct", type=float)
    screen.add_argument("--min-volume", type=int)
    screen.add_argument("--include-etf", action="store_true",
                        help="納入 ETF 與受益證券（預設只看普通股）")
    screen.add_argument("--date", help="交易日 YYYY-MM-DD，省略取最新")

    args = parser.parse_args()

    if args.command == "market":
        return _emit_json(lambda: query.market(args.date))

    if args.command == "quote":
        return _emit_json(lambda: query.quote(args.keys, args.date))

    if args.command == "screen":
        return _emit_json(lambda: query.screen(
            sort_by=args.sort_by,
            order=args.order,
            limit=args.limit,
            min_change_pct=args.min_change_pct,
            min_volume=args.min_volume,
            common_stock_only=not args.include_etf,
            date=args.date,
        ))

    if args.command == "verify":
        ok, message = verify_tool_calling()
        print(("通過：" if ok else "失敗：") + message)
        return 0 if ok else 1

    if args.command == "scan":
        return _run_scan(args)

    if args.command == "ask":
        return _run_once(args.question, args.trace)

    return _run_repl(args.trace)


if __name__ == "__main__":
    sys.exit(main())
