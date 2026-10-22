#!/bin/bash
cd "$(dirname "$0")"

echo "=========================================="
echo "🚀 月下餐飲集團 - 雲端查帳看板同步發布工具"
echo "=========================================="

# 1. 產生最新看板網頁並同步雲端資料庫
./venv/bin/python3 generate_dashboard.py 2026-10

# 2. 檢查 Git 遠端狀態
if git remote get-url origin >/dev/null 2>&1; then
    echo "📦 正在提交並推送至 GitHub Pages..."
    git add .
    git commit -m "Update reconciliation dashboard: $(date '+%Y-%m-%d %H:%M:%S')"
    git push origin main
    echo ""
    echo "🎉 成功發布！您可以在任何裝置（手機、平板、筆電）透過 GitHub Pages 網址隨時查看。"
else
    echo ""
    echo "ℹ️ 尚未連結 GitHub 遠端儲存庫 (origin)。"
    echo "請先設定您的 GitHub 儲存庫網址，例如："
    echo "git remote add origin https://github.com/<您的GitHub帳號>/<儲存庫名稱>.git"
    echo "git push -u origin main"
fi

echo "=========================================="
read -p "按 Enter 鍵關閉視窗..."
