#!/bin/bash
# ==============================================================================
# daemon_manager.sh
# ------------------------------------------------------------------------------
# 月下（月宮）與隨意小吃 全事業體統一對帳與排程背景服務守護器
# 依據本機實際使用者權限（無需 root/sudo），管理常駐服務進程。
#
# 指令：
#   ./daemon_manager.sh start    - 啟動背景常駐服務
#   ./daemon_manager.sh stop     - 停止背景常駐服務
#   ./daemon_manager.sh restart  - 重新啟動背景常駐服務
#   ./daemon_manager.sh status   - 查看服務與各排程來源最新狀態
# ==============================================================================

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PID_FILE="$DIR/.web_daemon.pid"
LOG_FILE="$DIR/scheduler_daemon.log"
PYTHON_BIN="/usr/bin/python3"

get_pid() {
    if [ -f "$PID_FILE" ]; then
        cat "$PID_FILE"
    else
        pgrep -f "shopline_cerp_web.py --no-browser" | head -n 1
    fi
}

is_running() {
    local pid=$(get_pid)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        return 0
    else
        return 1
    fi
}

start_daemon() {
    if is_running; then
        echo "🟢 [已在運行中] 服務 PID: $(get_pid) (Port: 8688)"
        exit 0
    fi

    echo "🚀 正在啟動全事業體統一對帳 Web 服務與背景排程引擎..."
    nohup "$PYTHON_BIN" "$DIR/shopline_cerp_web.py" --no-browser > "$LOG_FILE" 2>&1 &
    local new_pid=$!
    echo $new_pid > "$PID_FILE"
    sleep 2

    if kill -0 $new_pid 2>/dev/null; then
        echo "✅ [啟動成功] PID: $new_pid"
        echo "   - 服務網址：http://localhost:8688"
        echo "   - 狀態監控：http://localhost:8688/api/scheduler_status"
        echo "   - 日誌檔案：$LOG_FILE"
    else
        echo "❌ [啟動失敗] 請檢查日誌：$LOG_FILE"
        exit 1
    fi
}

stop_daemon() {
    local pid=$(get_pid)
    if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
        echo "ℹ️ 服務未在運行。"
        rm -f "$PID_FILE"
        exit 0
    fi

    echo "🛑 正在停止服務 PID: $pid..."
    kill "$pid" 2>/dev/null
    sleep 2
    if kill -0 "$pid" 2>/dev/null; then
        echo "⚠️ 進程仍在運行，執行強制終止 (SIGKILL)..."
        kill -9 "$pid" 2>/dev/null
    fi
    rm -f "$PID_FILE"
    echo "✅ 服務已完全停止。"
}

status_daemon() {
    if is_running; then
        local pid=$(get_pid)
        echo "🟢 【全事業體對帳背景服務 - 運行中】"
        echo "   - PID: $pid"
        echo "   - 服務網址: http://localhost:8688"
        echo "   - 排程健康狀態："
        if command -v curl >/dev/null 2>&1; then
            curl -s http://localhost:8688/api/scheduler_status | python3 -m json.tool 2>/dev/null || echo "     (無法取得 JSON 狀態)"
        fi
    else
        echo "🔴 【全事業體對帳背景服務 - 已停止】"
    fi
}

case "$1" in
    start)
        start_daemon
        ;;
    stop)
        stop_daemon
        ;;
    restart)
        stop_daemon
        sleep 1
        start_daemon
        ;;
    status)
        status_daemon
        ;;
    *)
        echo "使用方式: $0 {start|stop|restart|status}"
        exit 1
        ;;
esac
