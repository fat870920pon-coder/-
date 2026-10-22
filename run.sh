#!/usr/bin/env bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

# 檢查虛擬環境是否存在
if [ ! -d "venv" ]; then
    echo "📦 正在建立 Python 虛擬環境..."
    python3 -m venv venv
    ./venv/bin/pip install --upgrade pip
    ./venv/bin/pip install gspread google-auth google-auth-oauthlib openpyxl
fi

# 執行同步腳本（未指定參數時預設自動產生所有事業體傳票）
if [ $# -eq 0 ]; then
    ./venv/bin/python3 sync_wenzhong.py --store all
else
    ./venv/bin/python3 sync_wenzhong.py "$@"
fi
