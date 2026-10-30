#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_dilute.py
--------------
DILUTE 服飾電商 (SHOPLINE Payments) 財報與金流同步模組

排程頻率：每 5 分鐘 (300 秒) 巡檢同步一次
審計原則：
1. 嘗試由本機 Chrome (payments.shoplineapp.com) 取得最新帳戶餘額、提款與結算流水。
2. 連線逾時或未登入時，保留現有成功快取 (shopline_cached_analysis.json 與 dilute_integrated_summary.json)，明確標示未連線，不得虛報成功。
3. 成功時更新快取並回寫資料。
"""

import os
import sys
import json
import time
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(BASE_DIR, "shopline_cached_analysis.json")
SUMMARY_FILE = os.path.join(BASE_DIR, "dilute_integrated_summary.json")


def sync_dilute_data():
    try:
        import shopline_cerp_bot as bot
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🚀 開始同步 DILUTE (SHOPLINE Payments) 財報與金流數據...")

        raw_data = bot.fetch_shopline_audit_data(days_back=40)
        analysis = bot.analyze_audit_data(raw_data)
        analysis["overview"] = raw_data.get("overview") or {}

        withdrawals = analysis.get("withdrawals", [])
        if not withdrawals:
            if os.path.exists(CACHE_FILE):
                print("⚠️ 目前 SHOPLINE 未回傳有效提款週期（可能處於未登入狀態），保留既有快取。")
                sys.exit(1)
            else:
                print("❌ 尚無 SHOPLINE 提款資料。")
                sys.exit(1)

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"lastSyncTime": now_str, "analysis": analysis}, f, ensure_ascii=False, indent=2)

        print(f"✅ DILUTE SHOPLINE Payments 同步成功！共 {len(withdrawals)} 個提款週期。")
        sys.exit(0)
    except Exception as e:
        err_msg = str(e)
        print(f"❌ DILUTE 同步失敗：{err_msg}")
        if os.path.exists(CACHE_FILE):
            print("ℹ️ 保留現有 SHOPLINE 帳務快取資料。")
        sys.exit(1)


if __name__ == "__main__":
    sync_dilute_data()
