#!/usr/bin/env bash
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "================================================================="
echo "   玉山銀行明細 ➔ 智慧對帳與文中沖銷傳票產生工具"
echo "================================================================="
echo ""

./venv/bin/python3 bank_reconciliation.py "$@"

echo ""
read -p "請按 Enter 鍵關閉此視窗..."
