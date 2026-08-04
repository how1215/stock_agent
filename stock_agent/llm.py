"""模型客戶端。

單獨一個模組是為了讓 agent.py 與 tools.py 都能用而不產生循環匯入 —
agent.py 匯入 tools.py 取得工具清單，而 scan_market 工具內部需要模型。
"""

from langchain_openai import ChatOpenAI

from config import VLLM_BASE_URL, VLLM_MODEL


def build_llm() -> ChatOpenAI:
    # temperature=0：查數據沒有創意需求，也讓 eval 結果可重現
    return ChatOpenAI(
        base_url=VLLM_BASE_URL,
        api_key="dummy",  # vLLM 不驗證，但 client 要求非空
        model=VLLM_MODEL,
        temperature=0,
    )
