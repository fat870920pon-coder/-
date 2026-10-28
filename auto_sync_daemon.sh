#!/bin/bash
export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR" || exit 1

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 開始自動背景同步 Google 試算表與玉山存摺..."

# 1. 執行玉山銀行存摺與門市總表雙層審核對帳
./venv/bin/python3 bank_reconciliation.py --month 2026-10
./venv/bin/python3 reconciliation_summary.py --month 2026-10

# 2. 若資料有異動，自動推送至 GitHub Pages
git add index.html docs/ output/ dashboard_data.json reconciliation_balance.json dilute/
if ! git diff --staged --quiet; then
    git commit -m "chore(auto-sync): background sync dashboard $(date '+%Y-%m-%d %H:%M:%S')"
    git push origin main
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ✅ 已自動推送最新對帳結果至 GitHub Pages！"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ℹ️ 帳務資料無異動，略過推送。"
fi
