#!/bin/bash
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "=================================================="
echo "🚀 正在更新月下餐飲集團智慧對帳資料與可視化看板..."
echo "=================================================="

./venv/bin/python3 reconciliation_summary.py --month 2026-10

if [ -f "output/最新對帳看板.html" ]; then
    echo "🌐 正在開啟可視化對帳看板..."
    open "output/最新對帳看板.html"
elif [ -f "output/對帳看板_202610.html" ]; then
    echo "🌐 正在開啟可視化對帳看板..."
    open "output/對帳看板_202610.html"
fi

echo "✅ 完成！"
