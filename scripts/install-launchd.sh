#!/bin/bash
# 安裝／移除每日盤前簡報的 launchd 排程。
#
#   ./scripts/install-launchd.sh install    安裝並啟用（週一至五 08:30）
#   ./scripts/install-launchd.sh uninstall  移除
#   ./scripts/install-launchd.sh run        立刻執行一次（測試用）
#   ./scripts/install-launchd.sh status     查看狀態
#
# plist 是從模板產生的，因為 launchd 不接受相對路徑也不展開 ~。

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.how1215.stock-briefing"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

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
