"""結構化執行記錄。

把每次執行寫成 traces/<thread_id>.jsonl，一行一個事件。除錯時能分辨
「模型選錯工具」與「工具本身壞了」——這兩件事在終端輸出上看起來很像。
"""

import json
import time
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from config import TRACE_DIR


class TraceHandler(BaseCallbackHandler):
    """把 LLM 與 tool 事件寫成 JSONL，並在記憶體保留 tool 呼叫序列供 eval 檢查。"""

    def __init__(self, thread_id: str, echo: bool = False):
        self.thread_id = thread_id
        self.echo = echo
        self.tool_calls: list[dict] = []
        self._started: dict[UUID, tuple[float, str]] = {}

        TRACE_DIR.mkdir(parents=True, exist_ok=True)
        self._path = TRACE_DIR / f"{thread_id}.jsonl"

    def _emit(self, event: str, **fields: Any) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "thread_id": self.thread_id,
            "event": event,
            **fields,
        }
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _elapsed_ms(self, run_id: UUID) -> tuple[float | None, str]:
        started = self._started.pop(run_id, None)
        if started is None:
            return None, ""
        return round((time.perf_counter() - started[0]) * 1000, 1), started[1]

    # --- LLM ---

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        self._started[run_id] = (time.perf_counter(), "llm")

    def on_llm_end(self, response: LLMResult, *, run_id, **kwargs):
        latency_ms, _ = self._elapsed_ms(run_id)
        usage = (response.llm_output or {}).get("token_usage") or {}
        self._emit(
            "llm_end",
            latency_ms=latency_ms,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
        )

    def on_llm_error(self, error: BaseException, *, run_id, **kwargs):
        latency_ms, _ = self._elapsed_ms(run_id)
        self._emit("llm_error", latency_ms=latency_ms, error=repr(error))

    # --- Tools ---

    def on_tool_start(self, serialized, input_str, *, run_id, inputs=None, **kwargs):
        name = (serialized or {}).get("name", "?")
        self._started[run_id] = (time.perf_counter(), name)
        args = inputs if inputs is not None else input_str
        self.tool_calls.append({"name": name, "args": args})
        self._emit("tool_start", tool=name, args=args)
        if self.echo:
            print(f"  → {name}({args})", flush=True)

    def on_tool_end(self, output, *, run_id, **kwargs):
        latency_ms, name = self._elapsed_ms(run_id)
        text = output if isinstance(output, str) else getattr(output, "content", str(output))
        self._emit("tool_end", tool=name, latency_ms=latency_ms, output_chars=len(str(text)))
        if self.echo:
            print(f"  ← {name} ({latency_ms} ms, {len(str(text))} 字)", flush=True)

    def on_tool_error(self, error: BaseException, *, run_id, **kwargs):
        latency_ms, name = self._elapsed_ms(run_id)
        self._emit("tool_error", tool=name, latency_ms=latency_ms, error=repr(error))
        if self.echo:
            print(f"  ✗ {name}: {error!r}", flush=True)

    @property
    def trace_path(self):
        return self._path
