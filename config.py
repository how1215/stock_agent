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
