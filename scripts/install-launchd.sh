#!/bin/bash
# 安裝／移除每日盤前簡報的 launchd 排程。
#
#   ./scripts/install-launchd.sh install    安裝並啟用（週一至五 08:30）
#   ./scripts/install-launchd.sh uninstall  移除
#   ./scripts/install-launchd.sh run        立刻執行一次（測試用）
#   ./scripts/install-launchd.sh status     查看狀態
#
# plist 是從模板產生的，因為 launchd 不接受相對路徑也不展開 ~。
#
# 排程是選用的。專案若位於 ~/Documents、~/Desktop、~/Downloads，macOS 的 TCC
# 會讓 launchd 無法執行（install 會擋下並說明）。此時改為需要時主動產生：
#   uv run python -m stock_agent.cli briefing --refresh

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.how1215.stock-briefing"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

# macOS TCC：~/Documents、~/Desktop、~/Downloads 是受保護目錄。你的終端機被授權
# 存取它們，但 launchd 執行的 /bin/bash 沒有 — 排程會以 "Operation not permitted"
# 失敗，而且只會出現在日誌裡。與其讓人事後才發現，安裝時就直接擋下。
check_tcc() {
    for protected in "$HOME/Documents" "$HOME/Desktop" "$HOME/Downloads"; do
        if [[ "$PROJECT_DIR" == "$protected"/* || "$PROJECT_DIR" == "$protected" ]]; then
            cat >&2 <<MSG
無法安裝排程：專案位於 macOS 受保護目錄。

  專案路徑：$PROJECT_DIR
  受保護的：$protected

launchd 執行的程式沒有這個目錄的存取權，排程會以
"Operation not permitted" 失敗（終端機手動執行則正常，因為終端機已被授權）。

兩個選擇：

  1. 把專案移到不受保護的位置，例如：
       mv "$PROJECT_DIR" ~/dev/stock_agent
       cd ~/dev/stock_agent && ./scripts/install-launchd.sh install

  2. 不用排程，改為需要時主動產生：
       uv run python -m stock_agent.cli briefing --refresh
     或在 Pi 裡用 stock_briefing({refresh: true})。
     代價：歷史資料只在你實際查詢的日子累積，沒用的日子會有缺口
     （端點不接受日期參數，補不回來）。

若你確定要在此路徑安裝並已手動授權 Full Disk Access，加上 --force。
MSG
            [[ "${2:-}" == "--force" ]] || exit 1
            echo "（--force：略過受保護目錄檢查）" >&2
        fi
    done
}

write_plist() {
    mkdir -p "$(dirname "$PLIST")" "$PROJECT_DIR/logs"
    cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$PROJECT_DIR/scripts/briefing.sh</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$PROJECT_DIR</string>

    <!-- 週一至五 08:30（台股 09:00 開盤前）-->
    <key>StartCalendarInterval</key>
    <array>
        <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>8</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>8</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>8</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>8</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>8</integer><key>Minute</key><integer>30</integer></dict>
    </array>

    <key>StandardOutPath</key>
    <string>$PROJECT_DIR/logs/briefing.log</string>
    <key>StandardErrorPath</key>
    <string>$PROJECT_DIR/logs/briefing.log</string>

    <!-- 開機或睡眠錯過排程時補跑一次 -->
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
PLIST_EOF
    echo "已寫入 $PLIST"
}

case "${1:-}" in
install)
    check_tcc "$@"
    write_plist
    # bootout 失敗代表本來沒載入，忽略即可
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    launchctl bootstrap "$DOMAIN" "$PLIST"
    echo "已安裝並啟用。每週一至五 08:30 執行。"
    echo "日誌：$PROJECT_DIR/logs/briefing.log"
    ;;
uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "已移除。"
    ;;
run)
    launchctl kickstart -k "$DOMAIN/$LABEL"
    echo "已觸發一次執行。看 $PROJECT_DIR/logs/briefing.log"
    ;;
status)
    launchctl print "$DOMAIN/$LABEL" 2>/dev/null | head -20 || echo "未安裝"
    ;;
*)
    # 跳過 shebang，只印說明區塊。用 [[:space:]]* 而非 \? — BSD sed 不支援 \?
    sed -n '2,/^set -/p' "$0" | grep '^#' | sed 's/^#[[:space:]]*//'
    exit 1
    ;;
esac
