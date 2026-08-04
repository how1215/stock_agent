"""LangGraph ReAct agent，接本地 vLLM。

vLLM 必須以 tool calling 啟用的方式啟動（--enable-auto-tool-choice
--tool-call-parser <parser>）。沒開的話 bind_tools 不會報錯，模型只會安靜地
不呼叫工具 —— 用 verify_tool_calling() 可以先確認。
"""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt import create_react_agent

from config import VLLM_BASE_URL, VLLM_MODEL

from .llm import build_llm
from .prompts import SYSTEM_PROMPT
from .tools import ALL_TOOLS

__all__ = ["build_agent", "build_llm", "verify_tool_calling"]


def build_agent(checkpointer=None):
    """組出 agent。checkpointer 讓同一 thread_id 的多輪對話記得上文。"""
    return create_react_agent(
        model=build_llm(),
        tools=ALL_TOOLS,
        prompt=SYSTEM_PROMPT,
        checkpointer=checkpointer if checkpointer is not None else InMemorySaver(),
    )


def verify_tool_calling() -> tuple[bool, str]:
    """確認 vLLM 端的 tool calling 真的可用。

    Returns:
        (是否可用, 說明訊息)
    """
    try:
        response = build_llm().bind_tools(ALL_TOOLS).invoke("台積電今天收多少？")
    except Exception as exc:  # 連不上或端點異常
        return False, f"無法連線到 vLLM（{VLLM_BASE_URL}）：{exc!r}"

    if not getattr(response, "tool_calls", None):
        return False, (
            f"模型 {VLLM_MODEL} 收到 tools 但沒有發出 tool call。"
            "請確認 vLLM 啟動時帶了 --enable-auto-tool-choice 與 "
            "--tool-call-parser（Qwen 用 hermes、Llama 3 用 llama3_json），"
            "且模型本身支援 tool calling。"
        )
    return True, f"tool calling 正常：{response.tool_calls}"
