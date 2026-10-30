#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_ubereats_accounting.py
----------------------------
外送雙平台（Uber Eats x foodpanda）會計核銷與傳票產出引擎（審計合規增強版）：
嚴格遵守七大會計審計規範：
1. 嚴禁修改來源金額湊平借貸：任何差額完整保留、揭露原因，並將傳票標記為草稿阻擋正式入帳。
2. 實扣金額與憑證稅額核實：依官方實扣明細計算淨應收，31%與15.5%僅供異常檢查；稅額依有效B2B憑證，嚴禁一律除以1.05概算。
3. 跨月批次明細精確拆解歸回原營業月份：如 foodpanda 撥款 $13,332 核實為 8月結轉 $3,642 + 9月本期 $9,690，分別沖原月應收，嚴禁重複計入或按比例估算。
4. 生命週期四態分離：分開「平台應收、平台已撥、平台待撥、銀行已核實」。存摺未入帳或待更新時阻擋正式入帳，讀取失敗不得當成零。
5. 銀行嚴格單次配對：核對金額、日期窗口、付款人統編及名稱；每筆銀行明細限用一次；泛用摘要或同額多筆列待確認。
6. 差異清單與月報審計：小額差異如實呈現，未解差額絕不標記 EXCELLENT。
7. 修正日期與公司實際科目：徹底修正 month-05 未遞增問題，依月結最後一日與存摺實存日為準。
"""

import os
import sys
import json
import re
import calendar
import csv
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UBER_JSON_PATH = os.path.join(BASE_DIR, "ubereats_merchant_data.json")
FP_JSON_PATH = os.path.join(BASE_DIR, "foodpanda_merchant_data.json")
CACHED_BANK_PATH = os.path.join(BASE_DIR, "cached_esun_bank_deposits.json")
FOOD_SHEETS_PATH = os.path.expanduser("~/Desktop/腳本資料夾/月下/文中會計傳票產生器/food_sheets_data.json")
OUTPUT_DIR = os.path.expanduser("~/Desktop/腳本資料夾/月下/文中會計傳票產生器/output")

os.makedirs(OUTPUT_DIR, exist_ok=True)

# 公司實際科目代碼表 (符合文中 CERP 與 config.json)
STORE_ACCOUNTS = {
    "suiyi": {
        "store_name": "東區隨意小吃",
        "company_name": "頑全公關顧問股份有限公司隨意小吃本店",
        "tax_id": "94006775",
        "bank_acct_code": "1103-01",
        "bank_name": "玉山銀行-隨意小吃 (0462940067756)",
        "ar_ue": "1130-03",      # 應收帳款-ubereats
        "ar_fp": "1130-04",      # 應收帳款-foodpanda
        "revenue": "4100-01",    # 銷貨收入-餐賣
        "sales_tax": "2290",     # 銷項稅額
        "fee_delivery": "6655-01", # 管-手續費-外送平台
        "discount_delivery": "6655-02", # 管-外送促銷與折讓
        "input_tax": "1268-01"   # 進項稅額
    },
    "yuegong": {
        "store_name": "月宮（餐飲）",
        "company_name": "月下餐飲股份有限公司",
        "tax_id": "94001820",
        "bank_acct_code": "1103-01",
        "bank_name": "玉山銀行-月宮 (0303940018209)",
        "ar_ue": "1130-03",      # 應收帳款-ubereats
        "ar_fp": "1130-04",      # 應收帳款-foodpanda (備用)
        "revenue": "4100-01",    # 銷貨收入-餐賣
        "sales_tax": "2290",     # 銷項稅額
        "fee_delivery": "6655-01", # 管-手續費-外送平台
        "discount_delivery": "6655-02", # 管-外送促銷與折讓
        "input_tax": "1268-01"   # 進項稅額
    }
}

def load_data():
    """載入外送雙平台後台數據與銀行存摺快取"""
    ue_data = {}
    if os.path.exists(UBER_JSON_PATH):
        with open(UBER_JSON_PATH, "r", encoding="utf-8") as f:
            ue_data = json.load(f)

    fp_data = {}
    if os.path.exists(FP_JSON_PATH):
        with open(FP_JSON_PATH, "r", encoding="utf-8") as f:
            fp_data = json.load(f)

    bank_data = {"suiyi": [], "yuegong": []}
    if os.path.exists(CACHED_BANK_PATH):
        with open(CACHED_BANK_PATH, "r", encoding="utf-8") as f:
            bank_data = json.load(f)

    return ue_data, fp_data, bank_data

def get_last_day_of_month(month_str):
    """取得月份最後一天 YYYY-MM-DD，杜絕 month-05 未遞增或日期錯誤問題"""
    try:
        parts = month_str.split("-")
        year, month = int(parts[0]), int(parts[1])
        _, last_day = calendar.monthrange(year, month)
        return f"{year:04d}-{month:02d}-{last_day:02d}"
    except Exception:
        return month_str

def parse_payout_date_range(payout):
    """
    從 payout 物件提取 (start_date, end_date) 字串。
    支援 'YYYY-MM-DD – YYYY-MM-DD'、'YYYY-MM-DD ~ YYYY-MM-DD' 或 payment_date
    """
    period = str(payout.get("period", ""))
    for sep in ["–", "~", "至", "to", " - "]:
        if sep in period:
            parts = period.split(sep)
            if len(parts) == 2:
                s = parts[0].strip()
                e = parts[1].strip()
                if len(s) >= 10 and len(e) >= 10:
                    return s[:10], e[:10]
    p_date = str(payout.get("payment_date") or "")
    if len(p_date) >= 10:
        return p_date[:10], p_date[:10]
    month = str(payout.get("month") or (period[:7] if len(period) >= 7 else ""))
    if len(month) == 7:
        return f"{month}-01", f"{month}-28"
    return "9999-99-99", "9999-99-99"

def resolve_invoice_tax_and_untaxed(invoices, target_month=None, statement_id=None, total_fee=0, store_tax_id=None):
    """
    依據有效官方 B2B 發票憑證檢索未稅手續費與進項稅額。
    嚴格遵循審計規範：
    1. 嚴格禁止在無發票憑證情況下擅自以 fee / 1.05 推算。
    2. 實際使用 store_tax_id 核對官方憑證的買受人統編，並驗證發票狀態、金額及結算關聯。公司不符或必要證據缺失時列待核，不得認定憑證已核實。
    3. 發票合計與實扣費用不符（差額 > 1.0 元），保留差異待查，嚴禁自行補差，進項稅額認列 0，暫不折抵。
    """
    total_fee = round(abs(float(total_fee)))
    if total_fee == 0:
        return 0, 0, None, True, None

    matched_invoices = []
    for inv in invoices:
        inv_stmt = inv.get("statement_id")
        inv_date = inv.get("issue_date") or inv.get("payment_date") or ""

        if statement_id and inv_stmt and str(inv_stmt) == str(statement_id):
            matched_invoices.append(inv)
        elif target_month and inv_date.startswith(target_month):
            matched_invoices.append(inv)

    if not matched_invoices:
        # 無扣抵憑證發票：稅額依法認列 0，全額認列費用
        return total_fee, 0, "無發票扣抵憑證(稅額認列$0)", False, "MISSING_INVOICE"

    # 發票狀態與買受人公司統編核驗 (store_tax_id)
    for inv in matched_invoices:
        inv_status = str(inv.get("status") or "").strip().upper()
        if any(bad in inv_status for bad in ["作廢", "已註銷", "VOID", "CANCELLED", "INVALID"]):
            err_msg = f"發票憑證狀態異常({inv_status})，稅額認列$0不予折抵"
            return total_fee, 0, err_msg, False, "INVOICE_STATUS_INVALID"

        if store_tax_id:
            expected_tax_id = str(store_tax_id).strip()
            inv_buyer_tax_id = str(
                inv.get("buyer_tax_id")
                or inv.get("buyer_id")
                or inv.get("customer_tax_id")
                or inv.get("company_tax_id")
                or inv.get("buyer_identifier")
                or ""
            ).strip()

            if inv_buyer_tax_id:
                if inv_buyer_tax_id != expected_tax_id:
                    err_msg = f"買受人公司統編不符 (發票買受人統編 {inv_buyer_tax_id} != 本店統編 {expected_tax_id}，拒收折抵)"
                    return total_fee, 0, err_msg, False, "BUYER_TAX_ID_MISMATCH"
            else:
                # 買受人統編缺失，檢查是否有明確買受人公司名稱
                buyer_name = str(inv.get("buyer") or inv.get("buyer_name") or "").strip()
                if not (buyer_name and any(k in buyer_name for k in ["頑全", "隨意", "月下", "月宮"])):
                    err_msg = "發票買受人統編缺失待核(無有效統編佐證，稅額認列$0)"
                    return total_fee, 0, err_msg, False, "MISSING_BUYER_TAX_ID"

    inv_tax = round(sum(float(inv.get("tax_amount", 0)) for inv in matched_invoices))
    inv_untaxed = round(sum(float(inv.get("untaxed_amount", 0)) for inv in matched_invoices))
    inv_total = inv_untaxed + inv_tax
    inv_nums = [inv.get("invoice_number", "") for inv in matched_invoices if inv.get("invoice_number")]
    inv_num_str = ", ".join(inv_nums) if inv_nums else "官方發票"

    # 發票合計與實扣費用核對 (門檻：差額 > 1.0 元即判定不符)
    diff = abs(inv_total - total_fee)
    if diff > 1:
        # 發票合計與實扣費用不符：依審計規範，保留差異待查，嚴格禁止自行補差，稅額認列 0，不予折抵！
        err_msg = f"發票金額不符(發票合計${inv_total:,} != 實扣手續費${total_fee:,}，差額${diff:,}，稅額認列$0不予折抵)"
        return total_fee, 0, err_msg, False, "AMOUNT_MISMATCH"

    calculated_untaxed = total_fee - inv_tax
    return calculated_untaxed, inv_tax, inv_num_str, True, None

def match_bank_deposit(payout, bank_rows, platform_name, used_bank_keys, store_key="default", target_month=None):
    """
    審計級銀行單次核銷配對引擎：
    1. 交易識別包含門市/帳戶、來源檔、分頁與交易列識別 (store_key, sheet, date, amount, desc, remark, idx)
    2. 金額精確核對 (< 1.0)
    3. 合理入帳日期窗口 (一般撥款 T-1 至 T+5；月結算涵蓋當月至次月25日)
    4. 付款方統編與關鍵字嚴格核查（泛用摘要『匯款存入』無備註者直接拒絕匹配）
    5. 每筆銀行存摺流水僅限單次使用
    6. 候選筆數大於 1 筆時標記 AMBIGUOUS_MULTIPLE_MATCH，嚴禁取第一筆直接核銷
    """
    net_payout = round(float(payout.get("net_payout", 0)))
    exp_date_str = payout.get("payment_date") or payout.get("payout_date") or ""

    # 日期正規化
    exp_date = None
    for fmt in ["%Y-%m-%d", "%b %d, %Y"]:
        try:
            exp_date = datetime.strptime(exp_date_str, fmt)
            break
        except Exception:
            pass

    if net_payout <= 0:
        return None, "INVALID_PAYOUT_INFO"

    candidates = []
    for idx, b in enumerate(bank_rows):
        b_key = f"{store_key}_{b.get('sheet')}_{b.get('date')}_{b.get('amount')}_{b.get('desc')}_{b.get('remark')}_{idx}"
        if b_key in used_bank_keys:
            continue

        b_amt = round(float(b.get("amount", 0)))
        if b_amt != net_payout:
            continue

        b_date_str = str(b.get("date", ""))[:10]
        try:
            b_date = datetime.strptime(b_date_str, "%Y-%m-%d")
        except Exception:
            continue

        # 日期合理窗口檢查
        if exp_date:
            day_diff = (b_date - exp_date).days
            if not (-1 <= day_diff <= 5):
                continue
        elif target_month:
            # 月結算款項入帳合理區間：當月 1 日至次月 25 日
            m_parts = target_month.split("-")
            y_val, m_val = int(m_parts[0]), int(m_parts[1])
            start_d = datetime(y_val, m_val, 1)
            next_y = y_val if m_val < 12 else y_val + 1
            next_m = m_val + 1 if m_val < 12 else 1
            end_d = datetime(next_y, next_m, 25)
            if not (start_d <= b_date <= end_d):
                continue
        else:
            continue

        # 付款方嚴格識別
        desc = str(b.get("desc", ""))
        remark = str(b.get("remark", ""))
        combined_text = f"{desc} {remark}"

        is_valid_payer = False
        p_lower = platform_name.lower()
        if "uber" in p_lower:
            if any(k in combined_text for k in ["優步", "Uber", "83118125", "優食", "83076928"]):
                is_valid_payer = True
        elif "panda" in p_lower:
            if any(k in combined_text for k in ["富胖達", "53926705", "熊貓", "foodpanda"]):
                is_valid_payer = True
                is_valid_payer = True

        # 泛用摘要單獨出現（無平台統編或關鍵字）一律拒絕
        if not is_valid_payer:
            continue

        candidates.append((b, b_key))

    if len(candidates) == 0:
        return None, "NO_MATCH"
    elif len(candidates) == 1:
        matched_b, b_key = candidates[0]
        used_bank_keys.add(b_key)
        return matched_b, "MATCHED"
    else:
        # 同額多筆，證據不足，列待確認，嚴禁直接核銷第一筆
        return None, "AMBIGUOUS_MULTIPLE_MATCH"

def generate_wenzhong_vouchers(ue_data, fp_data=None, bank_data=None):
    """
    產出符合審計規範的文中會計傳票草稿與差異檢核清單。
    若借貸不平衡，嚴禁修改原始金額湊平，保留完整差額並標記 is_postable=False。
    """
    vouchers = []
    discrepancy_list = []
    used_bank_keys = set()
    fp_data = fp_data or {}
    bank_data = bank_data or {"suiyi": [], "yuegong": []}

    # ─────────────────────────────────────────────────────────────
    # 1. Uber Eats 傳票產出
    # ─────────────────────────────────────────────────────────────
    ue_stores = ue_data.get("stores", {})
    for store_key, store_info in ue_stores.items():
        acct_cfg = STORE_ACCOUNTS.get(store_key, STORE_ACCOUNTS["suiyi"])
        brand_name = store_info.get("brand_name", store_key)
        statements = store_info.get("monthly_statements", [])
        invoices = store_info.get("invoices", [])
        bank_rows = bank_data.get(store_key, [])

        for s in statements:
            month = s.get("month")
            sales = round(float(s.get("total_sales", 0)))
            if sales <= 0:
                continue

            fee_gross = round(abs(float(s.get("uber_fee", 0))))
            net_payout = round(float(s.get("net_payout", 0)))
            adj = round(float(s.get("adjustments", 0)) + float(s.get("marketing", 0)))

            # 異常費率檢查 (31% 僅供預警參考，不取代實扣)
            expected_fee_31 = round(sales * 0.31)
            fee_diff_vs_31 = fee_gross - expected_fee_31

            # 檢索有效發票憑證 (嚴格核對買受人統編)
            store_tax_id = acct_cfg.get("tax_id")
            fee_untaxed, fee_tax, inv_desc, has_inv, inv_err = resolve_invoice_tax_and_untaxed(
                invoices, target_month=month, total_fee=fee_gross, store_tax_id=store_tax_id
            )
            if not has_inv:
                if inv_err == "BUYER_TAX_ID_MISMATCH":
                    discrepancy_list.append({
                        "category": "發票公司不符",
                        "store": brand_name,
                        "platform": "Uber Eats",
                        "period": month,
                        "difference_amount": fee_gross,
                        "reason": f"{month} Uber 服務費發票買受人統編與本店統編 {store_tax_id} 不符，拒收折抵，進項稅額認列$0",
                        "action": "向優食台灣申請作廢並重開正確統編發票"
                    })
                elif inv_err == "AMOUNT_MISMATCH":
                    discrepancy_list.append({
                        "category": "發票金額不符",
                        "store": brand_name,
                        "platform": "Uber Eats",
                        "period": month,
                        "difference_amount": fee_gross,
                        "reason": f"{month} Uber 服務費發票金額與實扣 ${fee_gross:,} 不符，進項稅額認列$0，暫不扣抵",
                        "action": "人工核對發票開立金額與官方明細"
                    })
                else:
                    discrepancy_list.append({
                        "category": "憑證缺漏",
                        "store": brand_name,
                        "platform": "Uber Eats",
                        "period": month,
                        "difference_amount": round(fee_gross * 0.05 / 1.05),
                        "reason": f"{month} Uber 服務費 ${fee_gross:,} 未查得有效 B2B 扣抵發票，依規進項稅額認列$0，暫不扣抵",
                        "action": "至 Uber 後台補載完整扣抵憑證發票"
                    })

            # 日期政策：營業月結認列日為該營業月最後一日
            accrual_date = get_last_day_of_month(month)
            v_title = f"{month} Uber Eats 外送月結算核銷 ({brand_name})"

            # 銀行入帳核實檢查（審計規範第3條：使用唯一配對函式 match_bank_deposit，嚴禁自行取第一筆）
            payout_q = {"net_payout": net_payout, "target_month": month}
            matched_bank_row, match_status = match_bank_deposit(
                payout_q, bank_rows, "ubereats", used_bank_keys, store_key=store_key, target_month=month
            )
            bank_verified = (matched_bank_row is not None)
            voucher_date = matched_bank_row["date"] if bank_verified else accrual_date
            voucher_date_desc = matched_bank_row["date"] if bank_verified else f"{accrual_date} (營業月結認列日，銀行待核實)"

            entries = [
                {
                    "acct_code": acct_cfg["bank_acct_code"],
                    "acct_name": acct_cfg["bank_name"],
                    "dc": "借",
                    "amt": net_payout,
                    "summary": f"{month} Uber Eats 外送實撥入帳淨額" if bank_verified else f"{month} Uber Eats 應撥淨額 (待銀行更新入帳)"
                },
                {
                    "acct_code": acct_cfg["fee_delivery"],
                    "acct_name": "管-手續費-外送平台",
                    "dc": "借",
                    "amt": fee_untaxed,
                    "summary": f"{month} Uber 服務費實扣 (憑證: {inv_desc})"
                }
            ]

            if fee_tax > 0:
                entries.append({
                    "acct_code": acct_cfg["input_tax"],
                    "acct_name": "進項稅額",
                    "dc": "借",
                    "amt": fee_tax,
                    "summary": f"{month} Uber 服務費進項稅額 (憑證: {inv_desc})"
                })

            if adj != 0:
                if adj < 0:
                    entries.append({
                        "acct_code": acct_cfg["discount_delivery"],
                        "acct_name": "管-外送促銷與折讓",
                        "dc": "借",
                        "amt": abs(adj),
                        "summary": f"{month} Uber 行銷扣款與商戶調整款"
                    })
                else:
                    entries.append({
                        "acct_code": acct_cfg["discount_delivery"],
                        "acct_name": "管-外送促銷與折讓",
                        "dc": "貸",
                        "amt": adj,
                        "summary": f"{month} Uber 官方補貼與正向修正"
                    })

            entries.append({
                "acct_code": acct_cfg["ar_ue"],
                "acct_name": "應收帳款-ubereats",
                "dc": "貸",
                "amt": sales,
                "summary": f"{month} 沖銷外送營業應收帳款總額"
            })

            # 平衡檢查（審計規範第1條：嚴禁修改來源金額湊平）
            tot_dr = sum(e["amt"] for e in entries if e["dc"] == "借")
            tot_cr = sum(e["amt"] for e in entries if e["dc"] == "貸")
            diff = tot_dr - tot_cr

            is_postable = False
            status = "DRAFT"
            block_reasons = []

            if diff != 0:
                # 保留差額，停止產生可正式入帳之傳票，絕不執行 entries[0]["amt"] -= diff
                block_reasons.append(f"借貸不平衡差額 ${diff:,.0f}（借方 ${tot_dr:,.0f} != 貸方 ${tot_cr:,.0f}），依審計法規嚴禁篡改原始金額")
                status = "DRAFT_DISCREPANCY_BLOCKED"
                discrepancy_list.append({
                    "category": "傳票借貸不平",
                    "store": brand_name,
                    "platform": "Uber Eats",
                    "period": month,
                    "difference_amount": diff,
                    "reason": f"借方合計 ${tot_dr:,} 與貸方合計 ${tot_cr:,} 存在差額 ${diff:,}",
                    "action": "人工查核平台月報組成明細或調整款"
                })
            elif not bank_verified:
                status = "DRAFT_PENDING_BANK_DEPOSIT"
                if match_status == "AMBIGUOUS_MULTIPLE_MATCH":
                    block_reasons.append(f"存摺查得多筆同額 ${net_payout:,} 入帳流水，證據不足待人工指定，阻擋自動核銷")
                    discrepancy_list.append({
                        "category": "同額多筆待確認",
                        "store": brand_name,
                        "platform": "Uber Eats",
                        "period": month,
                        "difference_amount": net_payout,
                        "reason": f"存摺明細中查得多筆同額 ${net_payout:,} 存入，證據不足嚴禁任意核銷，列為待確認",
                        "action": "核對各筆之完整交易傳票編號與受款明細後人工配對"
                    })
                else:
                    block_reasons.append(f"存摺尚未查得該筆實撥淨額 ${net_payout:,} 之入帳流水，阻擋正式入帳")
            else:
                is_postable = True
                status = "VERIFIED_BALANCED"

            vouchers.append({
                "voucher_type": "外送月結核銷",
                "platform": "Uber Eats",
                "store_key": store_key,
                "brand_name": brand_name,
                "month": month,
                "date": voucher_date,
                "date_description": voucher_date_desc,
                "title": v_title,
                "entries": entries,
                "total_dr": tot_dr,
                "total_cr": tot_cr,
                "diff": diff,
                "bank_verified": bank_verified,
                "is_postable": is_postable,
                "status": status,
                "block_reasons": block_reasons,
                "rate_check_note": f"實扣抽成率 {s.get('fee_rate_pct')}%, 與31%基準公差 ${fee_diff_vs_31:,}"
            })

    # ─────────────────────────────────────────────────────────────
    # 2. foodpanda 傳票產出（含跨月結轉明細精準拆分）
    # ─────────────────────────────────────────────────────────────
    fp_stores = fp_data.get("stores", {})
    for store_key, store_info in fp_stores.items():
        acct_cfg = STORE_ACCOUNTS.get(store_key, STORE_ACCOUNTS["suiyi"])
        brand_name = store_info.get("brand_name", store_key)
        payouts = store_info.get("payouts", [])
        invoices = store_info.get("invoices", [])
        bank_rows = bank_data.get(store_key, [])

        # ─────────────────────────────────────────────────────────
        # 預先計算各批次收支，建立通用跨期/跨月結轉關聯（審計規範第3 & 5條）
        # 移除任何寫死 13,332 / 3,642 / 9,690 或特定對帳單號之邏輯
        # ─────────────────────────────────────────────────────────
        payout_metrics = []
        for p in payouts:
            p_sales = round(float(p.get("total_sales", 0)))
            p_disc = round(abs(float(p.get("merchant_discount", 0))))
            p_comm = round(abs(float(p.get("commission", 0))))
            p_ad = round(abs(float(p.get("ad_fee", 0))))
            p_oth = round(abs(float(p.get("other_fee", 0))))
            p_fee_gross = p_comm + p_ad + p_oth
            p_computed_net = p_sales - p_disc - p_fee_gross
            p_net_payout = round(float(p.get("net_payout", 0)))
            p_delta = p_net_payout - p_computed_net

            payout_metrics.append({
                "payout": p,
                "sales": p_sales,
                "disc": p_disc,
                "fee_gross": p_fee_gross,
                "computed_net": p_computed_net,
                "net_payout": p_net_payout,
                "delta": p_delta,
                "rollover_from": None,
                "rolled_over_into": None,
                "unattributed_delta": 0
            })

        # 配對結轉關聯：嚴格落實審計規範（禁止只靠同額配對，必須有官方結轉明細、檢查日期先後、原款是否已撥付及多候選檢查）
        for pm in payout_metrics:
            if pm["delta"] > 0:
                pm_p = pm["payout"]
                pm_start, pm_end = parse_payout_date_range(pm_p)
                pm_pay_date = str(pm_p.get("payment_date") or "")

                candidates = []
                for prior in payout_metrics:
                    if prior is pm or prior["rolled_over_into"]:
                        continue

                    prior_p = prior["payout"]
                    prior_start, prior_end = parse_payout_date_range(prior_p)
                    prior_pay_date = str(prior_p.get("payment_date") or "")

                    # 1. 檢查日期先後：prior 必須嚴格早於 pm，未來批次絕對禁止作為前期結轉！
                    is_prior_in_time = False
                    if prior_end < pm_start:
                        is_prior_in_time = True
                    elif prior_start < pm_start and prior_end <= pm_end:
                        is_prior_in_time = True
                    elif prior_pay_date and pm_pay_date and prior_pay_date < pm_pay_date:
                        is_prior_in_time = True

                    if not is_prior_in_time:
                        continue

                    # 2. 檢查原款是否已撥付：原款若已獨立入帳撥付，不可再次結轉沖銷
                    prior_bank_match, _ = match_bank_deposit(
                        prior_p, bank_rows, "foodpanda", set(used_bank_keys), store_key=store_key
                    )
                    if prior_bank_match is not None and round(prior["net_payout"]) == round(prior["computed_net"]):
                        # 原款已獨立入帳撥付完畢，不得重用
                        continue

                    # 3. 檢查金額是否吻合
                    if round(prior["computed_net"]) != pm["delta"]:
                        continue

                    # 4. 檢查官方結轉或交易明細關聯證據（禁止無憑無據僅靠同額猜配）
                    has_official_linkage = False
                    pm_note = str(pm_p.get("adjustment_note") or pm_p.get("notes") or pm_p.get("rollover_note") or "")
                    prior_note = str(prior_p.get("adjustment_note") or prior_p.get("notes") or prior_p.get("status") or "")
                    pm_adj = str(pm_p.get("adjustments") or "")
                    prior_stmt = str(prior_p.get("statement_id") or "")
                    prior_pid = str(prior_p.get("payout_id") or "")
                    prior_month = prior_p.get("period", "")[:7] or prior_start[:7]

                    if prior_stmt and (prior_stmt in pm_note or prior_stmt in pm_adj):
                        has_official_linkage = True
                    elif prior_pid and (prior_pid in pm_note or prior_pid in pm_adj):
                        has_official_linkage = True
                    elif any(kw in pm_note for kw in ["結轉", "前期", "微調結轉", "rollover", "carryover"]) and (
                        prior_month in pm_note or (prior_month.split("-")[-1].lstrip("0") + "月" in pm_note if "-" in prior_month else False)
                    ):
                        has_official_linkage = True
                    elif any(kw in prior_note for kw in ["結轉", "未撥付", "ROLLED_OVER", "DEFERRED", "CARRY_FORWARD"]):
                        has_official_linkage = True
                    elif prior_p.get("rollover_target_statement_id") == pm_p.get("statement_id"):
                        has_official_linkage = True

                    if not has_official_linkage:
                        continue

                    candidates.append(prior)

                if len(candidates) == 1:
                    matched_prior = candidates[0]
                    pm["rollover_from"] = matched_prior
                    matched_prior["rolled_over_into"] = pm
                elif len(candidates) > 1:
                    # 多個候選，證據不足列待確認，嚴禁直接核銷第一筆
                    pm["unattributed_delta"] = pm["delta"]
                    pm["unattributed_reason"] = "AMBIGUOUS_MULTIPLE_ROLLOVER_CANDIDATES"
                else:
                    # 無充分官方證據或時間先後不符，列「待歸屬」並阻擋正式入帳
                    pm["unattributed_delta"] = pm["delta"]
                    pm["unattributed_reason"] = "NO_OFFICIAL_ROLLOVER_EVIDENCE"

        for pm in payout_metrics:
            p = pm["payout"]
            pid = p.get("payout_id")
            stmt_id = p.get("statement_id")
            period = p.get("period", "")
            period_label = p.get("period_label", period)
            exp_date = p.get("payment_date", "")
            net_payout = pm["net_payout"]
            sales = pm["sales"]
            fee_gross = pm["fee_gross"]
            merchant_disc = pm["disc"]

            if pm["rolled_over_into"]:
                target_p = pm["rolled_over_into"]["payout"]
                target_label = target_p.get("period_label") or target_p.get("period") or target_p.get("statement_id")
                vouchers.append({
                    "voucher_type": "前期結轉提示",
                    "platform": "foodpanda",
                    "store_key": store_key,
                    "brand_name": brand_name,
                    "month": period[:7] if len(period) >= 7 else "2026-08",
                    "date": exp_date or "2026-08-31",
                    "date_description": f"{exp_date or '2026-08-31'} (結轉次期撥付，不重複產生正式傳票)",
                    "title": f"{period_label} foodpanda 結轉次期提示傳票",
                    "entries": [],
                    "total_dr": 0,
                    "total_cr": 0,
                    "diff": 0,
                    "bank_verified": False,
                    "is_postable": False,
                    "status": "ROLLED_OVER_NO_DUPLICATE",
                    "block_reasons": [f"本期淨額 ${pm['computed_net']:,} 已於後續 {target_label} 中合併出帳，防重複沖銷阻擋入帳"]
                })
                continue

            # 進行銀行存摺配對 (支援 store_key 門市隔離識別)
            matched_bank_row, match_status = match_bank_deposit(p, bank_rows, "foodpanda", used_bank_keys, store_key=store_key)
            bank_verified = (matched_bank_row is not None)

            # 發票憑證檢核 (嚴格核對買受人統編)
            store_tax_id = acct_cfg.get("tax_id")
            fee_untaxed, fee_tax, inv_desc, has_inv, inv_err = resolve_invoice_tax_and_untaxed(
                invoices, statement_id=stmt_id, total_fee=fee_gross, store_tax_id=store_tax_id
            )
            if not has_inv:
                if inv_err == "BUYER_TAX_ID_MISMATCH":
                    discrepancy_list.append({
                        "category": "發票公司不符",
                        "store": brand_name,
                        "platform": "foodpanda",
                        "period": period_label,
                        "difference_amount": fee_gross,
                        "reason": f"{period_label} foodpanda 服務費發票買受人統編與本店統編 {store_tax_id} 不符，拒收折抵，進項稅額認列$0",
                        "action": "向富胖達申請更換買受人統一編號發票"
                    })
                elif inv_err == "AMOUNT_MISMATCH":
                    discrepancy_list.append({
                        "category": "發票金額不符",
                        "store": brand_name,
                        "platform": "foodpanda",
                        "period": period_label,
                        "difference_amount": fee_gross,
                        "reason": f"{period_label} foodpanda 服務費發票金額與實扣 ${fee_gross:,} 不符，進項稅額認列$0，暫不扣抵",
                        "action": "人工查核發票折讓單或補載正確憑證"
                    })
                else:
                    discrepancy_list.append({
                        "category": "憑證缺漏",
                        "store": brand_name,
                        "platform": "foodpanda",
                        "period": period_label,
                        "difference_amount": round(fee_gross * 0.05 / 1.05),
                        "reason": f"{period_label} foodpanda 服務費 ${fee_gross:,} 未查得扣抵發票，依規進項稅額認列$0，暫不扣抵",
                        "action": "向富胖達後台下載完整發票扣抵憑證"
                    })

            v_date = matched_bank_row["date"] if bank_verified else exp_date

            entries = [
                {
                    "acct_code": acct_cfg["bank_acct_code"],
                    "acct_name": acct_cfg["bank_name"],
                    "dc": "借",
                    "amt": net_payout,
                    "summary": f"{period_label} foodpanda 實撥入帳淨額" if bank_verified else f"{period_label} foodpanda 應撥淨額 (待銀行更新入帳)"
                },
                {
                    "acct_code": acct_cfg["fee_delivery"],
                    "acct_name": "管-手續費-外送平台",
                    "dc": "借",
                    "amt": fee_untaxed,
                    "summary": f"{period_label} foodpanda 服務費未稅 (發票: {inv_desc})"
                }
            ]
            if fee_tax > 0:
                entries.append({
                    "acct_code": acct_cfg["input_tax"],
                    "acct_name": "進項稅額",
                    "dc": "借",
                    "amt": fee_tax,
                    "summary": f"{period_label} foodpanda 服務費進項稅額 (發票: {inv_desc})"
                })
            if merchant_disc > 0:
                entries.append({
                    "acct_code": acct_cfg["discount_delivery"],
                    "acct_name": "管-外送促銷與折讓",
                    "dc": "借",
                    "amt": merchant_disc,
                    "summary": f"{period_label} foodpanda 顧客折讓負擔"
                })

            block_reasons = []
            if pm["rollover_from"]:
                prior = pm["rollover_from"]
                prior_p = prior["payout"]
                prior_amt = round(prior["computed_net"])
                prior_stmt = prior_p.get("statement_id", "")
                prior_period = prior_p.get("period", "")
                prior_month = prior_period[:7] if len(prior_period) >= 7 else "前期"

                entries.append({
                    "acct_code": acct_cfg["ar_fp"],
                    "acct_name": "應收帳款-foodpanda",
                    "dc": "貸",
                    "amt": prior_amt,
                    "summary": f"沖銷 {prior_month} 原營業月份應收帳款 (對帳單 {prior_stmt} 結轉款)"
                })
                entries.append({
                    "acct_code": acct_cfg["ar_fp"],
                    "acct_name": "應收帳款-foodpanda",
                    "dc": "貸",
                    "amt": sales,
                    "summary": f"沖銷 {period[:7]} 原營業月份營業應收銷售總額 ({period_label})"
                })
                v_title = f"{period_label} foodpanda 跨月結轉核銷 (含{prior_month}結轉${prior_amt:,} + 本期${round(pm['computed_net']):,})"
                v_type = "跨月結轉核銷"
            elif pm["unattributed_delta"] > 0:
                unattr = pm["unattributed_delta"]
                entries.append({
                    "acct_code": acct_cfg["ar_fp"],
                    "acct_name": "應收帳款-待歸屬跨月款",
                    "dc": "貸",
                    "amt": unattr,
                    "summary": f"待歸屬款項 (來源官方出帳多出 ${unattr:,}，未查得前期待沖關聯)"
                })
                entries.append({
                    "acct_code": acct_cfg["ar_fp"],
                    "acct_name": "應收帳款-foodpanda",
                    "dc": "貸",
                    "amt": sales,
                    "summary": f"沖銷 {period[:7]} 原營業月份營業應收總額"
                })
                block_reasons.append(f"包含未歸屬跨月款項 ${unattr:,}，官方明細未查得前期待沖關聯，列為『待歸屬』並阻擋正式入帳")
                discrepancy_list.append({
                    "category": "跨月未歸屬",
                    "store": brand_name,
                    "platform": "foodpanda",
                    "period": period_label,
                    "difference_amount": unattr,
                    "reason": f"foodpanda 出帳包含未歸屬款項 ${unattr:,}，未查得原營業月明細關聯",
                    "action": "人工查核平台跨月對帳單調整項目"
                })
                v_title = f"{period_label} foodpanda 半月結算 (含待歸屬款${unattr:,})"
                v_type = "待歸屬半月結算"
            else:
                entries.append({
                    "acct_code": acct_cfg["ar_fp"],
                    "acct_name": "應收帳款-foodpanda",
                    "dc": "貸",
                    "amt": sales,
                    "summary": f"{period_label} 沖銷 foodpanda 營業應收總額"
                })
                v_title = f"{period_label} foodpanda 半月結算核銷 ({brand_name})"
                v_type = "半月結算核銷"

            tot_dr = sum(e["amt"] for e in entries if e["dc"] == "借")
            tot_cr = sum(e["amt"] for e in entries if e["dc"] == "貸")
            diff = tot_dr - tot_cr

            is_postable = False
            status = "DRAFT"

            if diff != 0:
                block_reasons.append(f"借貸不平衡差額 ${diff:,.0f}（借方 ${tot_dr:,.0f} != 貸方 ${tot_cr:,.0f}），嚴禁篡改原始金額湊平")
                status = "DRAFT_DISCREPANCY_BLOCKED"
                discrepancy_list.append({
                    "category": "傳票借貸不平",
                    "store": brand_name,
                    "platform": "foodpanda",
                    "period": period_label,
                    "difference_amount": diff,
                    "reason": f"借方合計 ${tot_dr:,} 與貸方合計 ${tot_cr:,} 存在差額 ${diff:,}",
                    "action": "人工查核平台明細與手續費組成"
                })
            elif pm["unattributed_delta"] > 0:
                status = "DRAFT_UNATTRIBUTED_BLOCKED"
            elif not bank_verified:
                status = "DRAFT_PENDING_BANK_DEPOSIT"
                if match_status == "AMBIGUOUS_MULTIPLE_MATCH":
                    block_reasons.append(f"存摺查得多筆同額 ${net_payout:,} 入帳流水，證據不足待人工指定，阻擋自動核銷")
                else:
                    block_reasons.append(f"官方出帳撥款 ${net_payout:,} 經存摺核驗未見入帳流水，阻擋正式入帳並提列差異排查")
                discrepancy_list.append({
                    "category": "銀行未見存入",
                    "store": brand_name,
                    "platform": "foodpanda",
                    "period": period_label,
                    "difference_amount": net_payout,
                    "reason": f"foodpanda 官方記錄於 {exp_date} 出帳 ${net_payout:,}，但玉山存摺中無此筆存入流水",
                    "action": "向富胖達財會確認電匯水單與受款帳號是否遭退匯或延後撥付"
                })
            else:
                is_postable = True
                status = "VERIFIED_BALANCED"

            vouchers.append({
                "voucher_type": v_type,
                "platform": "foodpanda",
                "store_key": store_key,
                "brand_name": brand_name,
                "month": period[:7] if len(period) >= 7 else "2026-10",
                "date": v_date,
                "date_description": f"{v_date} (存摺入帳日)" if bank_verified else f"{v_date} (預定撥款日，銀行未見存入阻擋入帳)",
                "title": v_title,
                "entries": entries,
                "total_dr": tot_dr,
                "total_cr": tot_cr,
                "diff": diff,
                "bank_verified": bank_verified,
                "is_postable": is_postable,
                "status": status,
                "block_reasons": block_reasons
            })

    return vouchers, discrepancy_list

def export_vouchers_draft_csv(vouchers, discrepancy_list):
    """匯出傳票草稿與審計差異清單 CSV，標註正式入帳許可狀態"""
    csv_file = os.path.join(OUTPUT_DIR, "delivery_accounting_vouchers_draft.csv")
    with open(csv_file, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["傳票日期", "平台", "門市名稱", "核算週期", "科目代號", "科目名稱", "借貸", "金額", "摘要", "傳票狀態", "允許正式入帳", "阻擋或異常原因"])
        for v in vouchers:
            v_status = v.get("status", "DRAFT")
            can_post = "YES" if v.get("is_postable") else "NO (阻擋)"
            reasons = " | ".join(v.get("block_reasons", [])) if v.get("block_reasons") else "平衡無誤已核實"
            for e in v["entries"]:
                writer.writerow([
                    v["date"], v.get("platform", ""), v["brand_name"], v["month"],
                    e["acct_code"], e["acct_name"], e["dc"], e["amt"],
                    e["summary"], v_status, can_post, reasons
                ])

    # 相容既有舊版 ubereats_accounting_vouchers.csv 路徑
    legacy_csv = os.path.join(OUTPUT_DIR, "ubereats_accounting_vouchers.csv")
    with open(legacy_csv, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["傳票日期", "門市名稱", "結算月份", "科目代號", "科目名稱", "借貸", "金額", "摘要", "入帳狀態"])
        for v in vouchers:
            can_post = "可入帳" if v.get("is_postable") else "草稿阻擋"
            for e in v["entries"]:
                writer.writerow([
                    v["date"], v["brand_name"], v["month"],
                    e["acct_code"], e["acct_name"], e["dc"], e["amt"],
                    e["summary"], can_post
                ])

    disc_file = os.path.join(OUTPUT_DIR, "delivery_discrepancy_list.json")
    with open(disc_file, "w", encoding="utf-8") as f:
        json.dump(discrepancy_list, f, ensure_ascii=False, indent=2)

    print(f"✅ 文中會計外送傳票草稿 CSV 已產出：{csv_file}")
    print(f"✅ 審計差異清單 JSON 已產出：{disc_file}")
    return csv_file, disc_file

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
        p_date = p.get("payout_date", "")
        net = round(float(p.get("net_payout", 0)))
        tx_id = p.get("transaction_id", "")
        period = p.get("period", "")
        
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
                "value": net,
                "note": f"Uber Eats 撥款期間：{period}，交易序號：{tx_id}"
            })

    print(f"隨意小吃試算表待回填項目：{len(results)} 筆")
    for r in results:
        print(f"  📅 {r['date']} ➔ {r['col']} 欄 ({r['col_name']}) 回填純數字：${r['value']:,} (備註: {r['note']})")

    return results

if __name__ == "__main__":
    ue_data, fp_data, bank_data = load_data()
    vouchers, discrepancies = generate_wenzhong_vouchers(ue_data, fp_data, bank_data)
    export_vouchers_draft_csv(vouchers, discrepancies)
    backfill_items = backfill_suiyi_sheets(ue_data, dry_run=True)
    print("\nUber Eats & foodpanda 審計核銷傳票引擎執行完畢！")
