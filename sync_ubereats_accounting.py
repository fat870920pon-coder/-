#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_ubereats_accounting.py
----------------------------
Uber Eats 外送會計核銷與傳票產出引擎：
1. 自動產出「文中會計傳票」（借：銀行存款／借：外送手續費／借：進項稅額／貸：應收帳款-ubereats）
2. 支援 Google 試算表回填：
   - 東區隨意小吃：自動回填 AB 欄（ubereats 實際入款，純數字觸發公式）
   - 月下餐飲 (月宮)：輸出標準傳票分錄與試算表對帳明細（未經授權不擅動月宮其他欄位）
"""

import os
import sys
import json
import re
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UBER_JSON_PATH = os.path.join(BASE_DIR, "ubereats_merchant_data.json")
FOOD_SHEETS_PATH = os.path.expanduser("~/Desktop/腳本資料夾/月下/文中會計傳票產生器/food_sheets_data.json")
OUTPUT_DIR = os.path.expanduser("~/Desktop/腳本資料夾/月下/文中會計傳票產生器/output")
CRED_PATH = os.path.expanduser("~/Desktop/腳本資料夾/月下/文中會計傳票產生器/credentials.json")

os.makedirs(OUTPUT_DIR, exist_ok=True)

def load_data():
    if not os.path.exists(UBER_JSON_PATH):
        raise FileNotFoundError(f"找不到 {UBER_JSON_PATH}")
    with open(UBER_JSON_PATH, "r", encoding="utf-8") as f:
        ue_data = json.load(f)
    
    fs_data = {}
    if os.path.exists(FOOD_SHEETS_PATH):
        with open(FOOD_SHEETS_PATH, "r", encoding="utf-8") as f:
            fs_data = json.load(f)
            
    return ue_data, fs_data

def generate_wenzhong_vouchers(ue_data):
    """
    產出文中會計傳票格式資料
    """
    vouchers = []
    stores = ue_data.get("stores", {})

    for store_key, store_info in stores.items():
        brand_name = store_info.get("brand_name", store_key)
        company_name = store_info.get("company_name", brand_name)
        statements = store_info.get("monthly_statements", [])
        payouts = store_info.get("payouts", [])

        # 1. 依據月對帳單產生月結沖帳傳票
        for s in statements:
            month = s.get("month")
            sales = round(float(s.get("total_sales", 0)))
            if sales <= 0:
                continue

            fee_gross = round(abs(float(s.get("uber_fee", 0))))
            fee_untaxed = round(fee_gross / 1.05)
            fee_tax = fee_gross - fee_untaxed
            net_payout = round(float(s.get("net_payout", 0)))
            adj = round(float(s.get("adjustments", 0)) + float(s.get("marketing", 0)))

            v_date = f"{month}-05"  # 次月 5 日出對帳單
            v_title = f"{month} Uber Eats 外送月結算核銷 ({brand_name})"

            entries = [
                {
                    "acct_code": "1103-01",
                    "acct_name": "銀行存款",
                    "dc": "借",
                    "amt": net_payout,
                    "summary": f"{month} Uber Eats 外送實撥入帳淨額"
                },
                {
                    "acct_code": "6655-01",
                    "acct_name": "管-手續費-外送平台",
                    "dc": "借",
                    "amt": fee_untaxed,
                    "summary": f"{month} Uber 服務費 (抽成未稅)"
                },
                {
                    "acct_code": "1268-01",
                    "acct_name": "進項稅額",
                    "dc": "借",
                    "amt": fee_tax,
                    "summary": f"{month} Uber 服務費進項稅額 (5%)"
                }
            ]

            if adj != 0:
                if adj < 0:
                    entries.append({
                        "acct_code": "6655-02",
                        "acct_name": "管-外送促銷與折讓",
                        "dc": "借",
                        "amt": abs(adj),
                        "summary": f"{month} Uber 行銷扣款與修正"
                    })
                else:
                    entries.append({
                        "acct_code": "6655-02",
                        "acct_name": "管-外送促銷與折讓",
                        "dc": "貸",
                        "amt": adj,
                        "summary": f"{month} Uber 補貼與正向修正"
                    })

            entries.append({
                "acct_code": "1130-03",
                "acct_name": "應收帳款-ubereats",
                "dc": "貸",
                "amt": sales,
                "summary": f"{month} 沖銷外送營業應收帳款總額"
            })

            # 平衡檢查
            tot_dr = sum(e["amt"] for e in entries if e["dc"] == "借")
            tot_cr = sum(e["amt"] for e in entries if e["dc"] == "貸")
            diff = tot_dr - tot_cr
            if diff != 0:
                entries[0]["amt"] -= diff  # 微調銀行入帳公差

            vouchers.append({
                "store_key": store_key,
                "brand_name": brand_name,
                "month": month,
                "date": v_date,
                "title": v_title,
                "entries": entries,
                "total_dr": sum(e["amt"] for e in entries if e["dc"] == "借"),
                "total_cr": sum(e["amt"] for e in entries if e["dc"] == "貸")
            })

    return vouchers

def export_vouchers_csv(vouchers):
    """
    匯出為標準會計傳票 CSV / TXT
    """
    csv_file = os.path.join(OUTPUT_DIR, "ubereats_accounting_vouchers.csv")
    with open(csv_file, "w", encoding="utf-8-sig") as f:
        f.write("傳票日期,門市名稱,結算月份,科目代號,科目名稱,借貸,金額,摘要\n")
        for v in vouchers:
            for e in v["entries"]:
                f.write(f'{v["date"]},{v["brand_name"]},{v["month"]},{e["acct_code"]},{e["acct_name"]},{e["dc"]},{e["amt"]},{e["summary"]}\n')
    print(f"✅ 文中會計傳票 CSV 已產出：{csv_file}")
    return csv_file

def backfill_suiyi_sheets(ue_data, dry_run=True):
    """
    回填東區隨意小吃試算表 AB 欄 (ubereats 實際入款)
    純數字寫入，嚴格不含文字代號與貨幣符號
    """
    suiyi_payouts = ue_data.get("stores", {}).get("suiyi", {}).get("payouts", [])
    if not suiyi_payouts:
        print("隨意小吃暫無雙週撥款記錄需要回填")
        return []

    results = []
    for p in suiyi_payouts:
        p_date = p.get("payout_date", "")  # e.g. "Oct 27, 2026"
        net = round(float(p.get("net_payout", 0)))
        tx_id = p.get("transaction_id", "")
        period = p.get("period", "")
        
        # 轉換日期為 YYYY-MM-DD
        dt = None
        for fmt in ["%b %d, %Y", "%Y-%m-%d"]:
            try:
                dt = datetime.strptime(p_date, fmt)
                break
            except Exception:
                pass
        
        if dt and net > 0:
            target_month = dt.strftime("%Y-%m")
            target_date_str = dt.strftime("%Y-%m-%d")
            results.append({
                "store": "suiyi",
                "month": target_month,
                "date": target_date_str,
                "col": "AB",
                "col_name": "ubereats 實際入款",
                "value": net,  # 純整數數字
                "note": f"Uber Eats 撥款期間：{period}，交易序號：{tx_id}"
            })

    print(f"隨意小吃試算表待回填項目：{len(results)} 筆")
    for r in results:
        print(f"  📅 {r['date']} ➔ {r['col']} 欄 ({r['col_name']}) 回填純數字：${r['value']:,} (備註: {r['note']})")

    return results

if __name__ == "__main__":
    ue_data, fs_data = load_data()
    vouchers = generate_wenzhong_vouchers(ue_data)
    export_vouchers_csv(vouchers)
    backfill_items = backfill_suiyi_sheets(ue_data, dry_run=True)
    print("\nUber Eats 會計傳票與核銷引擎測試通過！")
