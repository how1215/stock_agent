"""跑 evals/cases.yaml，檢查 agent 的 tool 選擇與答案內容。

這是回歸測試：換模型、改 prompt、改 tool description 之後跑一次就知道有沒有退步。
與 pytest 分開，因為它需要 vLLM 在跑、而且慢。

    uv run python evals/run_evals.py
    uv run python evals/run_evals.py --case 漲幅排行
"""

import argparse
import re
import sys
from pathlib import Path
from uuid import uuid4

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_agent import render  # noqa: E402
from stock_agent.agent import build_agent, verify_tool_calling  # noqa: E402
from stock_agent.screen import find_quotes  # noqa: E402
from stock_agent.sources import cache  # noqa: E402
from stock_agent.sources.schema import TAIEX_INDEX_NAME  # noqa: E402
from stock_agent.trace import TraceHandler  # noqa: E402

CASES_PATH = Path(__file__).parent / "cases.yaml"


def _ground_truth(day: dict) -> dict[str, str]:
    """把佔位符對應到實際數字，格式與 tool 輸出一致，才能做子字串比對。"""
    truth = {"trade_date": day["trade_date"]}

    taiex = next((i for i in day["indices"] if i["index_name"] == TAIEX_INDEX_NAME), None)
    if taiex:
        truth["taiex_close"] = render.fmt_price(taiex["close"])

    for quote in day["quotes"]:
        truth[f"close:{quote['code']}"] = render.fmt_price(quote["close"])
        truth[f"change_pct:{quote['code']}"] = render.fmt_pct(quote["change_pct"])

    return truth


def _resolve(text: str, truth: dict[str, str]) -> str:
    def replace(match: re.Match) -> str:
        key = match.group(1)
        if key not in truth:
            raise KeyError(f"eval 案例用了未知的佔位符 {{{key}}}")
        return truth[key]

    return re.sub(r"\{([^}]+)\}", replace, text)


def _check(case: dict, answer: str, calls: list[dict], truth: dict[str, str]) -> list[str]:
    """回傳失敗原因清單，空 list 表示通過。"""
    failures = []
    called = [c["name"] for c in calls]

    for expected in case.get("expect_tools", []):
        if expected not in called:
            failures.append(f"未呼叫 {expected}（實際：{called or '無'}）")

    for forbidden in case.get("forbid_tools", []):
        if forbidden in called:
            failures.append(f"不該呼叫 {forbidden}（實際：{called}）")

    if (want_count := case.get("expect_tool_call_count")) is not None:
        target = case.get("expect_tools", [None])[0]
        actual = called.count(target)
        if actual != want_count:
            failures.append(f"{target} 應呼叫 {want_count} 次，實際 {actual} 次")

    if expected_args := case.get("expect_tool_args"):
        target = case.get("expect_tools", [None])[0]
        matched = next((c["args"] for c in calls if c["name"] == target), None)
        if matched is None:
            failures.append(f"沒有 {target} 的呼叫可檢查參數")
        else:
            args = matched if isinstance(matched, dict) else {}
            for key, want in expected_args.items():
                if args.get(key) != want:
                    failures.append(f"參數 {key} 應為 {want!r}，實際 {args.get(key)!r}")

    for fragment in case.get("expect_contains", []):
        resolved = _resolve(fragment, truth)
        if resolved not in answer:
            failures.append(f"答案缺少 {resolved!r}")

    if alternatives := case.get("expect_contains_any", []):
        resolved = [_resolve(a, truth) for a in alternatives]
        if not any(a in answer for a in resolved):
            failures.append(f"答案未包含以下任一：{resolved}")

    for fragment in case.get("forbid_contains", []):
        resolved = _resolve(fragment, truth)
        if resolved in answer:
            failures.append(f"答案不該出現 {resolved!r}（疑似編造或用錯資料）")

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="跑 agent 行為回歸測試")
    parser.add_argument("--case", help="只跑名稱含此字串的案例")
    parser.add_argument("--verbose", "-v", action="store_true", help="印出完整答案")
    args = parser.parse_args()

    ok, message = verify_tool_calling()
    if not ok:
        print(f"前置檢查失敗：{message}", file=sys.stderr)
        return 2

    day = cache.load_or_fetch()
    truth = _ground_truth(day)
    print(f"資料交易日 {day['trade_date']}｜個股 {len(day['quotes'])} 筆\n")

    cases = yaml.safe_load(CASES_PATH.read_text(encoding="utf-8"))
    if args.case:
        cases = [c for c in cases if args.case in c["name"]]

    agent = build_agent()
    passed = 0

    for case in cases:
        handler = TraceHandler(f"eval-{uuid4().hex[:8]}")
        try:
            result = agent.invoke(
                {"messages": [("user", case["question"])]},
                config={"configurable": {"thread_id": uuid4().hex[:12]}, "callbacks": [handler]},
            )
            answer = result["messages"][-1].content
        except Exception as exc:
            print(f"✗ {case['name']}\n    執行失敗：{exc!r}\n")
            continue

        failures = _check(case, answer, handler.tool_calls, truth)
        if failures:
            print(f"✗ {case['name']}")
            print(f"    問題：{case['question']}")
            for failure in failures:
                print(f"    - {failure}")
            print(f"    tool 呼叫：{handler.tool_calls}")
            print(f"    答案：{answer.strip()[:300]}\n")
        else:
            passed += 1
            print(f"✓ {case['name']}")
            if args.verbose:
                print(f"    {answer.strip()[:300]}\n")

    total = len(cases)
    print(f"\n通過 {passed}/{total}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
