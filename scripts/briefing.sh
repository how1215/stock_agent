#!/bin/bash
# 每日盤前簡報 — 由 launchd 呼叫。
#
# launchd 給的環境變數極少（沒有你 shell 裡的 PATH），所以 uv 的位置要自己補。
# 也因此這裡不用 `uv run` 之外的任何工具，減少對環境的假設。

set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR" || exit 1

mkdir -p logs

UV="${UV_BIN:-$HOME/.local/bin/uv}"
if [[ ! -x "$UV" ]]; then
    UV="$(command -v uv 2>/dev/null || true)"
fi
if [[ -z "$UV" ]]; then
    echo "[$(date '+%F %T')] 找不到 uv，設定 UV_BIN 指向它" >&2
    exit 127
fi

echo "[$(date '+%F %T')] 開始產生盤前簡報"

# --refresh 讓它一定重新生成；briefing 內部若發現沒有新交易日會自行跳過，
# 所以假日執行不會覆寫既有報告。
"$UV" run python -m stock_agent.cli briefing --refresh
status=$?

if [[ $status -eq 0 ]]; then
    echo "[$(date '+%F %T')] 完成"
else
    echo "[$(date '+%F %T')] 失敗（exit $status）" >&2
fi
exit $status
