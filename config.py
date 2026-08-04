"""Central configuration for the TW stock agent."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent

# --- vLLM (OpenAI-compatible endpoint) ---
VLLM_BASE_URL = os.getenv("VLLM_BASE_URL", "http://127.0.0.1:8000/v1")
VLLM_MODEL = os.getenv("VLLM_MODEL", "local-llm")

# --- TWSE open API ---
TWSE_BASE_URL = "https://openapi.twse.com.tw/v1"
HTTP_TIMEOUT = 30.0

# --- Paths ---
CACHE_DIR = Path(os.getenv("CACHE_DIR", PROJECT_ROOT / "data"))
TRACE_DIR = Path(os.getenv("TRACE_DIR", PROJECT_ROOT / "traces"))

# 最新交易日資料在磁碟上視為新鮮的秒數。TWSE 端點只回最新交易日，
# 所以「最新」無法用日期判斷，只能用抓取時間。
FRESH_TTL_SECONDS = 600

# 篩選結果回傳上限，避免把全市場塞進 LLM context
MAX_SCREEN_LIMIT = 100

# 逐檔掃描（workflow/）的上限與並行度。掃描是 N+1 次 LLM 呼叫，N 直接決定
# 延遲，所以上限訂得比 MAX_SCREEN_LIMIT 保守得多。
#
# 並行度 16 是量測出來的：同樣 8 檔，並行 1 需 84.9s、並行 8 需 45.9s；
# 16 檔並行 16 只需 46.6s（2.9s/檔）。本地模型 compute-bound，加速非線性，
# 但提高並行度到 16 仍有明顯效益。
MAX_SCAN_LIMIT = 30
SCAN_MAX_CONCURRENCY = 16
