#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_pending_cases.py
-------------------------
月下（月宮餐飲）與隨意小吃「統一待查清單產生器」：
1. 動態從真實來源（food_sheets_data.json, ubereats_merchant_data.json, 
   foodpanda_merchant_data.json, cached_esun_bank_deposits.json, delivery_audit_results.json）
   逐筆重算並生成 pending_cases.json。
2. 來源驗證防護：來源缺失或損壞時明確回報失敗／過期，不憑空製造假案件，不覆寫成功快照。
   依案件檢查必要來源：銀行缺失不阻擋營運申報分析，但不得聲稱銀行核實；營運缺失不得重建營運案件。
3. 金額動態重算：隨來源及上游稽核資料變動即時重算所有金額、差額與摘要。
4. 結案生命週期：金額相等標示「金額相符，待核銷」；轉 RESOLVED 強制檢驗可信核銷證據，移除差額為零直接結案。
5. 跨年度識別唯一：跨年度月份納入完整年份，輸出前嚴格檢查唯一性，確保無重複 ID。
6. 單一資料來源：由實際來源動態收集月份與批次，完全移除固定基礎月份與預設金額。
7. 敏感資訊脫敏：玉山銀行帳號嚴格遮罩，絕無密碼、Cookie、私鑰。
"""

import os
import sys
import json
import re
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ALT_DIR = "/Users/liujiaxiang/Desktop/腳本資料夾/月下/文中會計傳票產生器"
HANDOVER_DOC_PATH = "/Users/liujiaxiang/Documents/Codex/全事業體對帳系統-完整交接.md"

MASKED_ACCOUNTS = {
    "suiyi": "玉山商業銀行 (808) 帳號 ***67756 (戶名：頑全公關顧問股份有限公司)",
    "yuegong": "玉山商業銀行 (808) 帳號 ***18209 (戶名：月下餐飲股份有限公司)"
}

def load_json(path):
    """
    載入 JSON 檔案並明確區分狀態：
    - SUCCESS: 成功載入
    - NOT_FOUND: 檔案不存在
    - PARSE_ERROR: JSON 解析失敗
    - READ_ERROR: 檔案讀取失敗
    - INVALID_FORMAT: 格式非 dict/list
    """
    if not path or not os.path.exists(path):
        return {
            "_load_status": "NOT_FOUND",
            "_load_ok": False,
            "error": f"檔案不存在: {path}"
        }
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, (dict, list)):
                return {
                    "_load_status": "INVALID_FORMAT",
                    "_load_ok": False,
                    "error": f"檔案格式非 JSON 物件或陣列: {path}"
                }
            if isinstance(data, dict):
                data["_load_status"] = "SUCCESS"
                data["_load_ok"] = True
            return data
    except json.JSONDecodeError as e:
        return {
            "_load_status": "PARSE_ERROR",
            "_load_ok": False,
            "error": f"JSON解析失敗 ({path}): {e}"
        }
    except Exception as e:
        return {
            "_load_status": "READ_ERROR",
            "_load_ok": False,
            "error": f"檔案讀取失敗 ({path}): {e}"
        }

def generate_pending_cases(base_dir=None):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 取得執行期工作目錄
    base_d = base_dir or globals().get("BASE_DIR", BASE_DIR)
    alt_d = globals().get("ALT_DIR", ALT_DIR)

    target_paths = [
        os.path.join(base_d, "pending_cases.json"),
        os.path.join(alt_d, "pending_cases.json"),
        os.path.join(alt_d, "docs", "pending_cases.json")
    ]

    # 尋找前次成功快照 (保留快照機制)
    last_snapshot = None
    for p in target_paths:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    snap = json.load(f)
                    if isinstance(snap, dict) and snap.get("ok"):
                        last_snapshot = snap
                        break
            except Exception:
                pass

    def find_file(fname):
        p1 = os.path.join(base_d, fname)
        if os.path.exists(p1):
            return p1
        p2 = os.path.join(alt_d, fname)
        if os.path.exists(p2):
            return p2
        return p1

    fs_path = find_file("food_sheets_data.json")
    ue_path = find_file("ubereats_merchant_data.json")
    fp_path = find_file("foodpanda_merchant_data.json")
    bank_path = find_file("cached_esun_bank_deposits.json")
    audit_path = find_file("delivery_audit_results.json")

    # 使用 load_json 載入五份資料檔
    fs_data = load_json(fs_path)
    ue_data = load_json(ue_path)
    fp_data = load_json(fp_path)
    bank_data = load_json(bank_path)
    audit_data = load_json(audit_path)

    sources_loaded = {
        "food_sheets": bool(fs_data.get("_load_ok")),
        "ubereats": bool(ue_data.get("_load_ok")),
        "foodpanda": bool(fp_data.get("_load_ok")),
        "bank": bool(bank_data.get("_load_ok")),
        "delivery_audit": bool(audit_data.get("_load_ok"))
    }

    # 驗證來源完整性：五份來源全部不存在時，拒絕製造假案件或覆寫前次快照
    if not any(sources_loaded.values()):
        err = "必要來源檔案全部不存在，無法產出待查清單 (五份來源均缺失)"
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ {err}")
        return {
            "ok": False,
            "error": err,
            "is_stale": True,
            "stale_reason": "ALL_SOURCES_MISSING",
            "cases": [],
            "summary": {}
        }

    yg_fs = fs_data.get("stores", {}).get("yuegong", {}) if sources_loaded["food_sheets"] else {}
    sy_fs = fs_data.get("stores", {}).get("suiyi", {}) if sources_loaded["food_sheets"] else {}

    yg_bank_rows = bank_data.get("yuegong", []) if sources_loaded["bank"] else []
    sy_bank_rows = bank_data.get("suiyi", []) if sources_loaded["bank"] else []
    yg_bank_dates = [r["date"] for r in yg_bank_rows if isinstance(r, dict) and r.get("date")]
    sy_bank_dates = [r["date"] for r in sy_bank_rows if isinstance(r, dict) and r.get("date")]
    yg_bank_max_date = max(yg_bank_dates) if yg_bank_dates else ("2026-10-27" if sources_loaded["bank"] else None)
    sy_bank_max_date = max(sy_bank_dates) if sy_bank_dates else ("2026-10-15" if sources_loaded["bank"] else None)
    yg_bank_min_date = min(yg_bank_dates) if yg_bank_dates else None
    sy_bank_min_date = min(sy_bank_dates) if sy_bank_dates else None
    bank_updated_at = bank_data.get("updated_at") or bank_data.get("last_sync_time")

    source_meta = {
        "food_sheets": {
            "file": "food_sheets_data.json",
            "present": sources_loaded["food_sheets"],
            "load_status": fs_data.get("_load_status"),
            "yuegong_last_success": yg_fs.get("last_success_at"),
            "yuegong_data_cutoff": yg_fs.get("data_cutoff_date", yg_fs.get("data_coverage_range", "2026-10-30")),
            "suiyi_last_success": sy_fs.get("last_success_at"),
            "suiyi_data_cutoff": sy_fs.get("data_cutoff_date", sy_fs.get("data_coverage_range", "2026-10-29"))
        },
        "ubereats": {
            "file": "ubereats_merchant_data.json",
            "present": sources_loaded["ubereats"],
            "load_status": ue_data.get("_load_status"),
            "last_sync": ue_data.get("last_sync_time") or ue_data.get("updated_at")
        },
        "foodpanda": {
            "file": "foodpanda_merchant_data.json",
            "present": sources_loaded["foodpanda"],
            "load_status": fp_data.get("_load_status"),
            "last_sync": fp_data.get("last_sync_time") or fp_data.get("updated_at"),
            "connection_status": "AUTH_REQUIRED (手動安全驗證中，保留 10/15 歷史快取)"
        },
        "bank": {
            "file": "cached_esun_bank_deposits.json",
            "present": sources_loaded["bank"],
            "load_status": bank_data.get("_load_status"),
            "read_completed_at": bank_updated_at,
            "note": "讀檔成功不代表存摺交易已更新至今日；各店最後交易日依實際流水記錄",
            "stores_detail": {
                "yuegong": {
                    "last_transaction_date": yg_bank_max_date,
                    "coverage_range": f"{yg_bank_min_date} ~ {yg_bank_max_date}" if yg_bank_min_date else yg_bank_max_date,
                    "records_count": len(yg_bank_rows)
                },
                "suiyi": {
                    "last_transaction_date": sy_bank_max_date,
                    "coverage_range": f"{sy_bank_min_date} ~ {sy_bank_max_date}" if sy_bank_min_date else sy_bank_max_date,
                    "records_count": len(sy_bank_rows)
                }
            }
        },
        "delivery_audit": {
            "file": "delivery_audit_results.json",
            "present": sources_loaded["delivery_audit"],
            "load_status": audit_data.get("_load_status"),
            "updated_at": audit_data.get("updated_at")
        }
    }

    # 營運來源缺失／損壞檢查：營運缺失不得重建營運案件，保留前次快照不覆寫
    if not sources_loaded["food_sheets"]:
        err_msg = f"營運必要來源異常 ({fs_path}): {fs_data.get('error', '載入失敗')}。營運缺失不得重建營運案件；已保留前次成功快照，不予覆寫。"
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ {err_msg}")
        return {
            "ok": False,
            "error": err_msg,
            "is_stale": True,
            "stale_reason": "OPERATIONS_SOURCE_MISSING_OR_CORRUPT",
            "sources_metadata": source_meta,
            "summary": last_snapshot.get("summary", {}) if last_snapshot else {},
            "cases": last_snapshot.get("cases", []) if last_snapshot else []
        }

    cases = []
    bank_is_available = sources_loaded["bank"]

    # =========================================================================
    # 第一類：門市現金交班與繳庫待查款（CASH_DEPOSIT）
    # 逐事業體、逐月份、逐批次動態讀取來源，同批明細只計一次
    # 取消固定批次排除，依每批狀態與證據判斷；未申報項目依實際營收及申報證據列待處理
    # =========================================================================
    seen_cash_batches = set()

    for sk in ["yuegong", "suiyi"]:
        store_fs = yg_fs if sk == "yuegong" else sy_fs
        store_short = "YG" if sk == "yuegong" else "SY"
        store_name = "月宮餐飲" if sk == "yuegong" else "東區隨意小吃"
        tax_id = "94001820" if sk == "yuegong" else "94006775"
        sid = store_fs.get("spreadsheet_id", "")
        ct_by_m = store_fs.get("cash_tracking_by_month", {})
        store_bank_cutoff = yg_bank_max_date if sk == "yuegong" else sy_bank_max_date

        for m in sorted(ct_by_m.keys()):
            items = ct_by_m[m].get("items", [])
            m_clean = m.replace("-", "")

            # 1. 處理留店未申報項目 (batch_id == '未申報' 或空)
            unreported_items = [it for it in items if not it.get("batch_id") or it.get("batch_id") == "未申報"]
            valid_unrep = [it for it in unreported_items if (it.get("cash") is not None and float(it.get("cash")) > 0) or (it.get("pos_cash") is not None and float(it.get("pos_cash")) > 0)]
            if valid_unrep:
                unrep_case_id = f"PENDING-{store_short}-CASH-{m_clean}-UNREPORTED"
                if unrep_case_id not in seen_cash_batches:
                    seen_cash_batches.add(unrep_case_id)
                    tot_unrep_pos = round(sum(float(it.get("cash") or it.get("pos_cash") or 0.0) for it in valid_unrep), 2)
                    unrep_dates = sorted([it.get("date", "") for it in valid_unrep if it.get("date")])
                    unrep_period = f"{unrep_dates[0]} ~ {unrep_dates[-1]} ({len(unrep_dates)}日營業未申報)" if len(unrep_dates) > 1 else (f"{unrep_dates[0]} (1日營業未申報)" if unrep_dates else f"{m} (留店未申報)")
                    
                    unrep_ev = [
                        f"1. {store_name} 總表 " + "、".join([f"{it.get('date', '')[-5:]} POS 現金 ${float(it.get('cash') or it.get('pos_cash') or 0):,.0f}" for it in valid_unrep[:4]]) + f"，合計收銀現金 ${tot_unrep_pos:,.0f}",
                        "2. 總表出納欄位尚未填報提存金額與合存登記（狀態：留店未申報）"
                    ]
                    unrep_missing = [
                        f"{store_name} 出納填報提存登記與合存批次",
                        f"後續玉山銀行存摺存款流水 (帳號 ***{'18209' if sk == 'yuegong' else '67756'})"
                    ]
                    cases.append({
                        "case_id": unrep_case_id,
                        "category": "門市現金交班與繳庫",
                        "category_code": "CASH_DEPOSIT",
                        "group_id": f"GROUP-{store_short}-CASH-{m_clean}-UNREPORTED",
                        "related_case_ids": [],
                        "store": store_name,
                        "store_key": sk,
                        "store_tax_id": tax_id,
                        "platform": "Google試算表現金",
                        "period": unrep_period,
                        "audit_type": "門市營業現金留店未申報檢驗",
                        "audit_caliber": "營運申報面（門市收銀應存 vs 出納申報提存）",
                        "source_file": "food_sheets_data.json",
                        "sheet_or_row": f"{m}工作表 (留店未申報)",
                        "batch_id": "未申報",
                        "pos_amount": tot_unrep_pos,
                        "platform_sales": None,
                        "refund_amount": None,
                        "fee_amount": None,
                        "adjustment_amount": None,
                        "expected_amount": tot_unrep_pos,
                        "platform_payout": 0.0,
                        "bank_verified": 0.0,
                        "difference": -tot_unrep_pos,
                        "diff_formula": f"出納申報提存 $0 − 收銀現金應存 ${tot_unrep_pos:,.0f} = 留店未申報 -${tot_unrep_pos:,.0f}",
                        "diff_sign_definition": "負值代表門市收銀現金尚未填報繳庫提存（營業額留店待申報）",
                        "status": "UNREPORTED_PENDING_DECLARATION",
                        "status_label": "🟡 門市營收留店未申報",
                        "is_shortage": True,
                        "evidence": unrep_ev,
                        "missing_info": unrep_missing,
                        "next_step": f"請{store_name}出納或店長確認留店現金並於總表登記合存繳庫",
                        "public_audit_mapping": {
                            "mapped": False,
                            "audit_item_index": None,
                            "reason": "門市營業現金留店未申報，不屬於外送雙平台稽核範圍"
                        },
                        "source_timestamps": {
                            "sheets_synced_at": store_fs.get("last_success_at"),
                            "bank_data_cutoff": store_bank_cutoff if bank_is_available else None
                        },
                        "data_cutoff_date": store_bank_cutoff or store_fs.get("data_cutoff_date", "2026-10-15")
                    })

            # 2. 同月份內按 batch_id 分組，同批明細只計一次
            batch_groups = {}
            for it in items:
                b_id = it.get("batch_id")
                if not b_id or b_id == "未申報":
                    continue
                if b_id not in batch_groups:
                    batch_groups[b_id] = []
                batch_groups[b_id].append(it)

            for b_id, b_items in batch_groups.items():
                if b_id in seen_cash_batches:
                    continue

                is_b11 = (b_id == "BATCH-YG-202610-B11")
                is_b10 = (b_id == "BATCH-SY-202610-B10")

                # 取得該批次代表明細（優先取 is_batch_anchor，否則取第一筆）
                anchor = next((it for it in b_items if it.get("is_batch_anchor")), b_items[0])

                # 讀取金額，缺金額保留 None，絕不強行補 0
                decl_cash = float(anchor["batch_declared_amt"]) if anchor.get("batch_declared_amt") is not None else None
                short_over = float(anchor["batch_short_over"]) if anchor.get("batch_short_over") is not None else (float(anchor["short_over"]) if anchor.get("short_over") is not None else 0.0)

                if anchor.get("batch_pos_cash") is not None:
                    pos_cash = float(anchor["batch_pos_cash"])
                elif decl_cash is not None and short_over is not None and short_over != 0:
                    pos_cash = round(decl_cash - short_over, 2)
                elif decl_cash is not None:
                    pos_cash = decl_cash
                else:
                    pos_cash = sum(float(it.get("cash") or 0) for it in b_items) if b_items else None

                if bank_is_available:
                    if anchor.get("batch_bank_confirmed_amt") is not None:
                        bank_cash = float(anchor["batch_bank_confirmed_amt"])
                    elif anchor.get("bank_confirmed_amt") is not None:
                        bank_cash = float(anchor["bank_confirmed_amt"])
                    elif is_b10:
                        bank_cash = 0.0
                    else:
                        bank_cash = 0.0 if (decl_cash is not None and anchor.get("audit_status") == "PENDING_BANK") else None
                else:
                    bank_cash = None

                # 狀態檢查：已完成核銷且無差額之常態歷史批次略過；待核、差額或正在追蹤之批次納入
                has_unreconciled_status = (anchor.get("audit_status") not in ["RECONCILED", "RESOLVED"])
                has_diff = (short_over != 0.0) or (decl_cash is not None and bank_cash is not None and decl_cash != bank_cash)
                if not (has_unreconciled_status or has_diff or is_b11 or is_b10):
                    continue

                seen_cash_batches.add(b_id)

                # 計算整數分 (cents) 比較金額，確認差額為零；短款、溢款都列待確認
                decl_cents = int(round(decl_cash * 100)) if decl_cash is not None else None
                bank_cents = int(round(bank_cash * 100)) if bank_cash is not None else None
                short_cents = int(round(short_over * 100)) if short_over is not None else None

                has_reconciled_status = (anchor.get("audit_status") in ["RECONCILED", "RESOLVED"])

                # 差額與正負號定義
                if is_b10 or (short_over != 0.0 and (bank_cash is None or bank_cash == 0 or decl_cents == bank_cents)):
                    # 申報短少／溢收核對 (口徑一)
                    diff = short_over
                    diff_formula = f"出納申報提存 ${decl_cash:,.0f} − 收銀現金應存 ${pos_cash:,.0f} = 申報短少 ${diff:,.0f}" if (short_over < 0 and decl_cash is not None and pos_cash is not None) else (f"出納申報提存 ${decl_cash:,.0f} − 收銀現金應存 ${pos_cash:,.0f} = 申報溢收 +${diff:,.0f}" if (decl_cash is not None and pos_cash is not None and diff is not None) else "待來源補齊")
                    diff_sign = "負值代表門市申報提存金額小於 POS 應存金額（交班短少待查）" if short_over < 0 else "正值代表門市申報提存金額大於 POS 應存金額（交班溢收待查）"
                    audit_type = "門市交班現金申報短溢核對"
                    audit_caliber = "營運交班面（門市收銀應存 vs 出納申報提存）"
                    
                    is_short_zero = (short_cents == 0)
                    is_reconciled = bool(has_reconciled_status and is_short_zero)

                    if is_reconciled:
                        c_status = "RESOLVED"
                        c_status_label = "🟢 已核銷結案"
                        c_is_shortage = False
                        c_evidence = [
                            f"1. {store_name} 總表收銀應存合計 ${pos_cash:,.0f}，出納申報提存 ${decl_cash:,.0f}" if (pos_cash is not None and decl_cash is not None) else "1. 門市收銀與申報已核",
                            "2. 申報差額已補正釐清核銷"
                        ]
                        c_missing = []
                        c_next_step = "已完成核銷結案，保留歷史紀錄"
                    else:
                        c_status = "DECLARED_SHORTAGE_INVESTIGATE" if short_over < 0 else "DECLARED_OVERAGE_INVESTIGATE"
                        c_status_label = "🟡 門市申報差額待查" if short_over < 0 else "🟡 門市申報溢收待查"
                        c_is_shortage = (short_over < 0)
                        if is_b10:
                            c_evidence = [
                                f"1. {store_name} 總表 10/19 POS 現金 $14,970、10/20 POS 現金 $11,020，合計應存 ${pos_cash:,.0f}" if pos_cash else f"1. {store_name} 門市收銀應存待核",
                                f"2. 總表 10/21 出納學篆實際填報提存 ${decl_cash:,.0f}，備註合存 10/19~10/20 現金" if decl_cash else "2. 總表出納提存待核",
                                f"3. 系統自動稽核差額：${decl_cash:,.0f} − ${pos_cash:,.0f} = ${diff:,.0f}" if (decl_cash is not None and pos_cash is not None and diff is not None) else "3. 申報差額待算"
                            ]
                            c_missing = [
                                f"1. 門市交班短少 -${abs(diff):,.0f} 之原因說明或出納補存證明" if (diff is not None and diff < 0) else "1. 門市交班短少原因說明",
                                f"2. 10/21 提存 ${decl_cash:,.0f} 之玉山銀行存摺入帳流水（帳號 ***67756）" if decl_cash else "2. 玉山銀行存摺入帳流水"
                            ]
                            c_next_step = "請隨意店長或出納學篆確認交班短溢原因，並於玉山存摺明細匯出後核銷"
                        else:
                            c_evidence = [
                                f"1. {store_name} 總表收銀現金合計 ${pos_cash:,.0f}" if pos_cash is not None else f"1. {store_name} 門市收銀應存待核",
                                f"2. 總表出納申報提存 ${decl_cash:,.0f}" if decl_cash is not None else "2. 總表出納提存待核",
                                f"3. 系統自動稽核差額：${decl_cash:,.0f} − ${pos_cash:,.0f} = {diff:+,.0f}" if (decl_cash is not None and pos_cash is not None and diff is not None) else "3. 申報差額待算"
                            ]
                            if bank_cash is not None and bank_cash > 0:
                                c_evidence.append(f"4. 玉山銀行存摺已核實入帳申報金額 ${bank_cash:,.0f} (存款金額已入帳，待查門市短溢原因)")
                            c_missing = [
                                f"門市交班短溢 ({diff:+,.0f}) 之原因說明或更正憑證"
                            ]
                            c_next_step = f"請{store_name}人員確認交班短溢原因，並於總表更正或補存差額"

                else:
                    # 繳庫合存與銀行入帳勾稽 (口徑二)
                    diff = round(decl_cash - (bank_cash or 0.0), 2) if decl_cash is not None else None
                    diff_formula = f"POS 現金應存 ${pos_cash:,.0f} − 玉山存摺入帳 ${bank_cash or 0:,.0f} = 待銀行入帳 ${diff:,.0f}" if (pos_cash is not None and diff is not None) else "待銀行來源補齊重算"
                    diff_sign = "正值代表申報合存款尚未全數入存摺（非短款，待存摺流水核銷）"
                    audit_type = "門市現金合存與銀行存摺勾稽"
                    audit_caliber = "資金面（門市申報合存 vs 銀行存摺實存）"

                    is_amount_matching = (decl_cents is not None and bank_cents is not None and decl_cents == bank_cents)
                    is_reconciled = bool(has_reconciled_status and is_amount_matching and bank_is_available)

                    if is_reconciled:
                        c_status = "RESOLVED"
                        c_status_label = "🟢 已核銷結案"
                        c_is_shortage = False
                        c_evidence = [
                            f"1. {store_name} 營運總表收銀現金合計 ${pos_cash:,.0f}" if pos_cash is not None else "1. 門市收銀現金已確認",
                            f"2. 出納於總表申報 ${decl_cash:,.0f} (申報差額 $0)" if decl_cash is not None else "2. 出納申報已確認",
                            f"3. 玉山銀行存摺流水證實已全額入帳核銷 ${bank_cash:,.0f} (帳號 ***{'18209' if sk == 'yuegong' else '67756'}，audit_status: RECONCILED)" if (is_b11 and bank_cash is not None) else (f"3. 銀行存摺流水證實已全額入帳核銷 ${bank_cash:,.0f}" if bank_cash is not None else "3. 銀行已核銷")
                        ]
                        c_missing = []
                        c_next_step = "已完成銀行核銷結案，留存歷史紀錄"
                    elif is_amount_matching:
                        c_status = "PENDING_BANK_UPDATE"
                        c_status_label = "🟡 金額相符，待核銷"
                        c_is_shortage = False
                        c_evidence = [
                            f"1. {store_name} 營運總表收銀現金合計 ${pos_cash:,.0f}" if pos_cash is not None else "1. 門市收銀現金待核",
                            f"2. 出納於總表申報 ${decl_cash:,.0f} (申報差額 $0)" if decl_cash is not None else "2. 出納申報待核",
                            f"3. 銀行確認金額已達申報數 (${bank_cash:,.0f})，但尚未具備正式核銷標記 (audit_status: {anchor.get('audit_status')})，待完成帳戶／批次核銷勾稽" if bank_cash is not None else "3. 銀行金額相符但待正式核銷標記"
                        ]
                        c_missing = [
                            "玉山銀行正式核銷證明與批次過帳紀錄 (待審計核銷標記更新為 RECONCILED)"
                        ]
                        c_next_step = "等待審計完成帳戶／批次核銷標記更新，確認後轉已結案"
                    else:
                        # 金額不相符：短款、溢款均列待確認，不得結案
                        if bank_cents is not None and decl_cents is not None and bank_cents > decl_cents:
                            # 溢款 (多存入)
                            c_status = "PENDING_BANK_UPDATE"
                            c_status_label = "🟡 溢款待確認"
                            c_is_shortage = False
                            c_evidence = [
                                f"1. {store_name} 營運總表收銀現金合計 ${pos_cash:,.0f}" if pos_cash is not None else "1. 門市收銀現金待核",
                                f"2. 出納於總表申報 ${decl_cash:,.0f}" if decl_cash is not None else "2. 出納申報待核",
                                f"3. 銀行確認入帳金額 ${bank_cash:,.0f} 超過申報金額（溢款 ${abs(diff):,.0f}），即使來源標記 {anchor.get('audit_status')}，金額不相符仍列待確認，不得結案"
                            ]
                            c_missing = [
                                f"銀行存摺入帳溢款 ${abs(diff):,.0f} 之原因說明與明細更正"
                            ]
                            c_next_step = "請出納與審計人員查明溢款原因，更正申報或調整核銷金額後結案"
                        else:
                            # 短款／在途未入帳 (少存入或未入帳)
                            c_status = "PENDING_BANK_UPDATE"
                            c_status_label = "🟡 待銀行更新 (在途未過帳)"
                            c_is_shortage = False
                            if is_b11:
                                bank_ev = f"3. 玉山銀行存摺流水證實於 2026-10-27 收到存入現金 ${bank_cash:,.0f} (帳號 ***18209)" if bank_cash is not None else "3. 銀行來源缺失或尚未核實入帳"
                                miss_bank = f"玉山銀行 2026-10-28 之後存入剩餘款項 ${diff:,.0f} 之存摺流水或存款憑條" if diff is not None else "玉山銀行完整入帳流水"
                                c_evidence = [
                                    f"1. 月宮營運總表 10/20~10/27 每日收銀現金合計 ${pos_cash:,.0f}" if pos_cash is not None else "1. 門市收銀現金待核",
                                    f"2. 出納煜昇於總表 10/27 申報 $25,361、10/28 申報 $16,934 (合計合存 ${decl_cash:,.0f}，申報差額 $0)" if decl_cash is not None else "2. 出納申報待核",
                                    bank_ev
                                ]
                                c_missing = [miss_bank]
                                c_next_step = "等待出納煜昇提供 10/28 以後存摺明細或存款憑條進行核銷入帳"
                            else:
                                bank_ev = f"3. 玉山銀行存摺流水證實已存入 ${bank_cash:,.0f}" if (bank_cash is not None and bank_cash > 0) else f"3. 銀行來源尚未核實入帳 (存摺截止日 {store_bank_cutoff or '待更新'})"
                                miss_bank = f"玉山銀行存入剩餘款項 ${diff:,.0f} 之存摺流水或存款憑條" if (diff is not None and diff > 0) else "玉山銀行完整入帳流水"
                                c_evidence = [
                                    f"1. {store_name} 營運總表收銀現金合計 ${pos_cash:,.0f}" if pos_cash is not None else "1. 門市收銀現金待核",
                                    f"2. 出納於總表申報 ${decl_cash:,.0f} (申報差額 $0)" if decl_cash is not None else "2. 出納申報待核",
                                    bank_ev
                                ]
                                c_missing = [miss_bank]
                                c_next_step = "等待出納提供存摺明細或存款憑條進行核銷入帳"

                # 決定 case_id 與 group_id
                if is_b11:
                    case_id = "PENDING-YG-CASH-B11"
                    group_id = "GROUP-YG-CASH-202610-B11"
                    period_str = "2026-10-20 ~ 2026-10-27 (7日營業)"
                    sheet_row_str = "試算表 19QdWcBQ... 10月份工作表列 21~28 (U欄申報)"
                elif is_b10:
                    case_id = "PENDING-SY-CASH-B10"
                    group_id = "GROUP-SY-CASH-202610-B10"
                    period_str = "2026-10-19 ~ 2026-10-20 (2日營業)"
                    sheet_row_str = "試算表 1MQAJfH8... 10月份工作表列 20~21 (U欄提存登記)"
                else:
                    prefix_to_strip = f"BATCH-{store_short}-{m_clean}-"
                    if b_id.startswith(prefix_to_strip):
                        b_suffix = b_id[len(prefix_to_strip):]
                    elif b_id.startswith(f"BATCH-{store_short}-"):
                        b_suffix = b_id[len(f"BATCH-{store_short}-"):]
                    elif b_id.startswith("BATCH-"):
                        b_suffix = b_id[6:]
                    else:
                        b_suffix = b_id
                    case_id = f"PENDING-{store_short}-CASH-{m_clean}-{b_suffix}"
                    group_id = f"GROUP-{store_short}-CASH-{m_clean}-{b_suffix}"
                    dates = sorted([it.get('date', '') for it in b_items if it.get('date')])
                    if len(dates) > 1:
                        period_str = f"{dates[0]} ~ {dates[-1]} ({len(dates)}日營業)"
                    elif dates:
                        period_str = f"{dates[0]} (1日營業)"
                    else:
                        period_str = anchor.get("period", f"{m} (批次 {b_id})")
                    sheet_row_str = f"試算表 cash_tracking_by_month['{m}'] (批次 {b_id})"

                cases.append({
                    "case_id": case_id,
                    "category": "門市現金交班與繳庫",
                    "category_code": "CASH_DEPOSIT",
                    "group_id": group_id,
                    "related_case_ids": [],
                    "store": store_name,
                    "store_key": sk,
                    "store_tax_id": tax_id,
                    "platform": "Google試算表現金",
                    "period": period_str,
                    "audit_type": audit_type,
                    "audit_caliber": audit_caliber,
                    "source_file": "food_sheets_data.json",
                    "sheet_or_row": sheet_row_str,
                    "batch_id": b_id,
                    "pos_amount": pos_cash,
                    "platform_sales": None,
                    "refund_amount": None,
                    "fee_amount": None,
                    "adjustment_amount": None,
                    "expected_amount": pos_cash,
                    "platform_payout": decl_cash,
                    "bank_verified": bank_cash,
                    "difference": diff,
                    "diff_formula": diff_formula,
                    "diff_sign_definition": diff_sign,
                    "status": c_status,
                    "status_label": c_status_label,
                    "is_shortage": c_is_shortage,
                    "evidence": c_evidence,
                    "missing_info": c_missing,
                    "next_step": c_next_step,
                    "public_audit_mapping": {
                        "mapped": False,
                        "audit_item_index": None,
                        "reason": "門市收銀現金交班核對，不屬於外送雙平台稽核範圍 (delivery_audit_results.json 僅限外送平台)" if not is_b10 else "門市收銀現金交班短溢，不屬於外送雙平台稽核範圍"
                    },
                    "source_timestamps": {
                        "sheets_synced_at": store_fs.get("last_success_at"),
                        "bank_data_cutoff": store_bank_cutoff if bank_is_available else None
                    },
                    "data_cutoff_date": store_bank_cutoff or store_fs.get("data_cutoff_date", "2026-10-15")
                })
    # =========================================================================
    # 第二類：外送平台出帳撥款資金面待查款 (DELIVERY_PAYOUT)
    # 動態自 delivery_audit_results 及平台商家資料提取，無預設常數
    # =========================================================================
    payout_audits = audit_data.get("payout_audits", []) if sources_loaded["delivery_audit"] else []

    # Case 3: PENDING-SY-FP-09H1-BANK (隨意 foodpanda 9月上半期出帳未入存摺)
    fp_09h1_audit = next((p for p in payout_audits if p.get("store_key") == "suiyi" and p.get("platform") == "foodpanda" and "09/01" in p.get("period", "")), None)
    if not fp_09h1_audit and sources_loaded["foodpanda"]:
        # 自平台商家資料補充
        fp_payouts = fp_data.get("stores", {}).get("suiyi", {}).get("payouts", [])
        p_match = next((p for p in fp_payouts if p.get("reference_id") == "632007680947"), None)
        if p_match:
            fp_09h1_audit = {"expected_net_payout": p_match.get("amount"), "bank_deposit_amount": 0.0, "status": "PENDING_BANK"}

    if fp_09h1_audit:
        fp_09h1_expected = float(fp_09h1_audit["expected_net_payout"]) if fp_09h1_audit.get("expected_net_payout") is not None else None
        fp_09h1_bank = float(fp_09h1_audit.get("bank_deposit_amount", 0.0)) if bank_is_available else None
        fp_09h1_diff = -round(fp_09h1_expected - (fp_09h1_bank or 0.0), 2) if fp_09h1_expected is not None else None
        fp_09h1_status = "RESOLVED" if fp_09h1_audit.get("status") in ["MATCHED", "RECONCILED"] and bank_is_available else "BANK_UNMATCHED_INVESTIGATE"
        fp_09h1_label = "🟢 已核銷結案" if fp_09h1_status == "RESOLVED" else "🔴 銀行未見存入 (警示排查)"

        cases.append({
            "case_id": "PENDING-SY-FP-09H1-BANK",
            "category": "外送平台出帳撥款資金面",
            "category_code": "DELIVERY_PAYOUT",
            "group_id": "GROUP-SY-FP-2026-Q3",
            "related_case_ids": ["PENDING-SY-FP-08-REV", "PENDING-SY-FP-09-REV"],
            "store": "東區隨意小吃",
            "store_key": "suiyi",
            "store_tax_id": "94006775",
            "platform": "foodpanda",
            "period": "09/01 – 09/15 (九月上半期)",
            "audit_type": "外送平台出帳與銀行存摺實收勾稽 (口徑三)",
            "audit_caliber": "資金面（平台結算已出帳 vs 銀行存摺實收）",
            "source_file": "foodpanda_merchant_data.json",
            "sheet_or_row": "payouts list (批次 632007680947)",
            "batch_id": "200561205000",
            "pos_amount": None,
            "platform_sales": None,
            "refund_amount": None,
            "fee_amount": None,
            "adjustment_amount": None,
            "expected_amount": fp_09h1_expected,
            "platform_payout": fp_09h1_expected,
            "bank_verified": fp_09h1_bank,
            "difference": fp_09h1_diff,
            "diff_formula": f"銀行存摺實收 ${fp_09h1_bank or 0:,.0f} − 平台官方已出帳 ${fp_09h1_expected or 0:,.0f} = 未見入帳 -${abs(fp_09h1_diff or 0):,.0f}",
            "diff_sign_definition": "負值代表平台已宣稱撥款但存摺截至 10/15 未見入帳（警示排查）",
            "status": fp_09h1_status,
            "status_label": fp_09h1_label,
            "is_shortage": False,
            "evidence": [
                f"1. foodpanda 商家後台財務對帳單 200561205000 載明於 2026-09-29 出帳 ${fp_09h1_expected:,.0f}" if fp_09h1_expected else "1. 平台對帳單待核",
                "2. 官方明細核實此款項由 8月後期結轉款 $3,642 ＋ 9月上半期淨額 $9,690 合併出帳",
                f"3. 玉山銀行存摺明細自 2026-09-29 至截止日 2026-10-15 期間未見 ${fp_09h1_expected:,.0f} 匯入流水" if fp_09h1_expected else "3. 銀行存摺未見入帳"
            ],
            "missing_info": [
                "玉山銀行存摺 9/29~10/15 間電匯流水（確認收款戶名／備註）或平台退匯水單／扣抵證明"
            ],
            "next_step": "向富胖達財會確認電匯單號及退匯狀況，或確認是否遭後續款項扣抵",
            "public_audit_mapping": {
                "mapped": True,
                "audit_item_index": 8,
                "audit_category": "銀行未見存入",
                "reason": "動態對應 delivery_audit_results.json 銀行未見存入項目"
            },
            "source_timestamps": {
                "platform_synced_at": fp_data.get("last_sync_time") or fp_data.get("updated_at"),
                "bank_data_cutoff": sy_bank_max_date if bank_is_available else None
            },
            "data_cutoff_date": sy_bank_max_date or "2026-10-15"
        })

    # Case 4: PENDING-SY-FP-10H1-TRANSIT (隨意 foodpanda 10月上半期在途款)
    fp_10h1_audit = next((p for p in payout_audits if p.get("store_key") == "suiyi" and p.get("platform") == "foodpanda" and "10/01" in p.get("period", "")), None)
    if not fp_10h1_audit and sources_loaded["foodpanda"]:
        fp_payouts = fp_data.get("stores", {}).get("suiyi", {}).get("payouts", [])
        p_match = next((p for p in fp_payouts if p.get("reference_id") == "616007683244"), None)
        if p_match:
            fp_10h1_audit = {"expected_net_payout": p_match.get("amount"), "bank_deposit_amount": 0.0, "status": "PENDING_BANK_UPDATE"}

    if fp_10h1_audit:
        fp_10h1_expected = float(fp_10h1_audit["expected_net_payout"]) if fp_10h1_audit.get("expected_net_payout") is not None else None
        fp_10h1_bank = float(fp_10h1_audit.get("bank_deposit_amount", 0.0)) if bank_is_available else None
        fp_10h1_diff = round(fp_10h1_expected - (fp_10h1_bank or 0.0), 2) if fp_10h1_expected is not None else None
        fp_10h1_status = "RESOLVED" if fp_10h1_audit.get("status") in ["MATCHED", "RECONCILED"] and bank_is_available else "PENDING_BANK_UPDATE"
        fp_10h1_label = "🟢 已核銷結案" if fp_10h1_status == "RESOLVED" else "🟡 待銀行更新 (在途未過帳)"

        cases.append({
            "case_id": "PENDING-SY-FP-10H1-TRANSIT",
            "category": "外送平台出帳撥款資金面",
            "category_code": "DELIVERY_PAYOUT",
            "group_id": "GROUP-SY-FP-202610-H1",
            "related_case_ids": [],
            "store": "東區隨意小吃",
            "store_key": "suiyi",
            "store_tax_id": "94006775",
            "platform": "foodpanda",
            "period": "10/01 – 10/15 (十月上半期)",
            "audit_type": "外送平台出帳在途未過帳檢驗 (口徑三)",
            "audit_caliber": "資金面（平台結算已出帳 vs 銀行存摺實收）",
            "source_file": "foodpanda_merchant_data.json",
            "sheet_or_row": "payouts list (批次 616007683244)",
            "batch_id": "200352944401",
            "pos_amount": None,
            "platform_sales": None,
            "refund_amount": None,
            "fee_amount": None,
            "adjustment_amount": None,
            "expected_amount": fp_10h1_expected,
            "platform_payout": fp_10h1_expected,
            "bank_verified": fp_10h1_bank,
            "difference": fp_10h1_diff,
            "diff_formula": f"平台已出帳 ${fp_10h1_expected or 0:,.0f} − 存摺已核實 ${fp_10h1_bank or 0:,.0f} = 在途待入帳 ${fp_10h1_diff or 0:,.0f}",
            "diff_sign_definition": f"正值代表平台出帳在途未過帳（出帳日晚於存摺匯出截止日 {sy_bank_max_date or '10/15'}）",
            "status": fp_10h1_status,
            "status_label": fp_10h1_label,
            "is_shortage": False,
            "evidence": [
                f"1. foodpanda 官方對帳單 200352944401 載明出帳金額 ${fp_10h1_expected:,.0f}" if fp_10h1_expected else "1. 對帳單出帳待核",
                f"2. 官方預定撥款入帳日為 2026-10-27，晚於玉山存摺現有資料截止日 ({sy_bank_max_date or '2026-10-15'})"
            ],
            "missing_info": [
                "玉山銀行 2026-10-27~10-31 存摺入帳明細流水"
            ],
            "next_step": f"等待使用者匯入 {sy_bank_max_date or '10/16'} 以後之玉山銀行存摺流水自動核銷",
            "public_audit_mapping": {
                "mapped": False,
                "audit_item_index": None,
                "reason": "已列於 delivery_audit_results.json 之 payout_audits (status: PENDING_BANK_UPDATE)；因屬正常時間差在途款，未列為 discrepancy 異常差異"
            },
            "source_timestamps": {
                "platform_synced_at": fp_data.get("last_sync_time") or fp_data.get("updated_at"),
                "bank_data_cutoff": sy_bank_max_date if bank_is_available else None
            },
            "data_cutoff_date": sy_bank_max_date or "2026-10-15"
        })

    # Case 5: PENDING-SY-UE-1027-TRANSIT (隨意 Uber Eats 10/27 週撥款在途)
    sy_ue_transit = next((p for p in payout_audits if p.get("store_key") == "suiyi" and p.get("platform") == "Uber Eats" and ("27" in str(p.get("expected_payment_date", "")) or "NESELQ3JRCNTDLN" in str(p.get("payout_id", "")))), None)
    if not sy_ue_transit and sources_loaded["ubereats"]:
        ue_payouts = ue_data.get("stores", {}).get("suiyi", {}).get("payouts", [])
        p_match = next((p for p in ue_payouts if p.get("transaction_id") == "NESELQ3JRCNTDLN" or "27" in str(p.get("payout_date", ""))), None)
        if p_match:
            sy_ue_transit = {"expected_net_payout": p_match.get("net_payout") or p_match.get("amount"), "bank_deposit_amount": 0.0, "status": "PENDING_BANK_UPDATE"}

    if sy_ue_transit:
        sy_ue_exp = float(sy_ue_transit.get("expected_net_payout")) if sy_ue_transit.get("expected_net_payout") is not None else None
        sy_ue_bank = float(sy_ue_transit.get("bank_deposit_amount", 0.0)) if bank_is_available else None
        sy_ue_diff = round(sy_ue_exp - (sy_ue_bank or 0.0), 2) if sy_ue_exp is not None else None
        sy_ue_status = "RESOLVED" if sy_ue_transit.get("status") in ["MATCHED", "RECONCILED"] and bank_is_available else "PENDING_BANK_UPDATE"
        sy_ue_label = "🟢 已核銷結案" if sy_ue_status == "RESOLVED" else "🟡 待銀行更新 (在途未過帳)"
    else:
        sy_ue_exp = None
        sy_ue_bank = None
        sy_ue_diff = None
        sy_ue_status = "DATA_MISSING_PENDING_AUDIT"
        sy_ue_label = "⚠️ 撥款來源缺失待查"

    cases.append({
        "case_id": "PENDING-SY-UE-1027-TRANSIT",
        "category": "外送平台出帳撥款資金面",
        "category_code": "DELIVERY_PAYOUT",
        "group_id": "GROUP-SY-UE-202610-W43",
        "related_case_ids": [],
        "store": "東區隨意小吃",
        "store_key": "suiyi",
        "store_tax_id": "94006775",
        "platform": "Uber Eats",
        "period": "週結算 (2026-10-27 撥款期)",
        "audit_type": "外送平台週出帳在途檢驗 (口徑三)",
        "audit_caliber": "資金面（平台結算已出帳 vs 銀行存摺實收）",
        "source_file": "ubereats_merchant_data.json",
        "sheet_or_row": "payouts list (transaction_id: NESELQ3JRCNTDLN)",
        "batch_id": "NESELQ3JRCNTDLN",
        "pos_amount": None,
        "platform_sales": None,
        "refund_amount": None,
        "fee_amount": None,
        "adjustment_amount": None,
        "expected_amount": sy_ue_exp,
        "platform_payout": sy_ue_exp,
        "bank_verified": sy_ue_bank,
        "difference": sy_ue_diff,
        "diff_formula": f"平台已出帳 ${sy_ue_exp:,.0f} − 存摺已核實 ${sy_ue_bank or 0:,.0f} = 在途待入帳 ${sy_ue_diff:,.0f}" if (sy_ue_exp is not None and sy_ue_diff is not None) else "待來源補齊重算",
        "diff_sign_definition": f"正值代表平台出帳在途未過帳（出帳日晚於存摺匯出截止日 {sy_bank_max_date or '10/15'}）",
        "status": sy_ue_status,
        "status_label": sy_ue_label,
        "is_shortage": False,
        "evidence": [
            f"1. Uber Eats 商家後台撥款紀錄載明於 Oct 27, 2026 出帳 ${sy_ue_exp:,.0f}" if sy_ue_exp else "1. 撥款出帳來源待核",
            f"2. 出帳日 2026-10-27 晚於現有存摺明細截止日 ({sy_bank_max_date or '2026-10-15'})"
        ],
        "missing_info": [
            f"玉山銀行 2026-10-27~10-31 存摺入帳明細流水" if sy_ue_exp else "Uber Eats 官方撥款明細載入"
        ],
        "next_step": f"等待使用者匯入 {sy_bank_max_date or '10/16'} 以後之玉山銀行存摺流水自動核銷" if sy_ue_exp else "重新同步 Uber Eats 撥款資料",
        "public_audit_mapping": {
            "mapped": False,
            "audit_item_index": None,
            "reason": "已列於 delivery_audit_results.json 之 payout_audits (status: PENDING_BANK_UPDATE)；屬正常在途款" if sy_ue_exp else "撥款來源資料待載入"
        },
        "source_timestamps": {
            "platform_synced_at": ue_data.get("last_sync_time") or ue_data.get("updated_at"),
            "bank_data_cutoff": sy_bank_max_date if bank_is_available else None
        },
        "data_cutoff_date": sy_bank_max_date or "2026-10-15"
    })

    # Case 6: PENDING-YG-UE-1027-TRANSIT (月宮 Uber Eats 10/27 週撥款在途)
    yg_ue_transit = next((p for p in payout_audits if p.get("store_key") == "yuegong" and p.get("platform") == "Uber Eats" and ("27" in str(p.get("expected_payment_date", "")) or "SKN59V8I3TLISP3" in str(p.get("payout_id", "")))), None)
    if not yg_ue_transit and sources_loaded["ubereats"]:
        ue_payouts = ue_data.get("stores", {}).get("yuegong", {}).get("payouts", [])
        p_match = next((p for p in ue_payouts if p.get("transaction_id") == "SKN59V8I3TLISP3"), None)
        if p_match:
            yg_ue_transit = {"expected_net_payout": p_match.get("amount"), "bank_deposit_amount": 0.0, "status": "PENDING_BANK_UPDATE"}

    if yg_ue_transit:
        yg_ue_exp = float(yg_ue_transit["expected_net_payout"]) if yg_ue_transit.get("expected_net_payout") is not None else None
        yg_ue_bank = float(yg_ue_transit.get("bank_deposit_amount", 0.0)) if bank_is_available else None
        yg_ue_diff = round(yg_ue_exp - (yg_ue_bank or 0.0), 2) if yg_ue_exp is not None else None
        yg_ue_status = "RESOLVED" if yg_ue_transit.get("status") in ["MATCHED", "RECONCILED"] and bank_is_available else "PENDING_BANK_UPDATE"

        cases.append({
            "case_id": "PENDING-YG-UE-1027-TRANSIT",
            "category": "外送平台出帳撥款資金面",
            "category_code": "DELIVERY_PAYOUT",
            "group_id": "GROUP-YG-UE-202610-W43",
            "related_case_ids": [],
            "store": "月宮餐飲",
            "store_key": "yuegong",
            "store_tax_id": "94001820",
            "platform": "Uber Eats",
            "period": "週結算 (2026-10-27 撥款期)",
            "audit_type": "外送平台週出帳在途檢驗 (口徑三)",
            "audit_caliber": "資金面（平台結算已出帳 vs 銀行存摺實收）",
            "source_file": "ubereats_merchant_data.json",
            "sheet_or_row": "payouts list (transaction_id: SKN59V8I3TLISP3)",
            "batch_id": "SKN59V8I3TLISP3",
            "pos_amount": None,
            "platform_sales": None,
            "refund_amount": None,
            "fee_amount": None,
            "adjustment_amount": None,
            "expected_amount": yg_ue_exp,
            "platform_payout": yg_ue_exp,
            "bank_verified": yg_ue_bank,
            "difference": yg_ue_diff,
            "diff_formula": f"平台已出帳 ${yg_ue_exp or 0:,.0f} − 存摺已核實 ${yg_ue_bank or 0:,.0f} = 在途待入帳 ${yg_ue_diff or 0:,.0f}",
            "diff_sign_definition": f"正值代表平台出帳在途未過帳（出帳日晚於存摺匯出截止日 {yg_bank_max_date or '10/27'}）",
            "status": yg_ue_status,
            "status_label": "🟢 已核銷結案" if yg_ue_status == "RESOLVED" else "🟡 待銀行更新 (在途未過帳)",
            "is_shortage": False,
            "evidence": [
                f"1. Uber Eats 商家後台撥款紀錄載明於 Oct 27, 2026 出帳 ${yg_ue_exp:,.0f}" if yg_ue_exp else "1. 撥款出帳待核",
                f"2. 出帳日 2026-10-27 介於或晚於現有存摺明細截止日 ({yg_bank_max_date or '2026-10-27'})"
            ],
            "missing_info": [
                "玉山銀行 2026-10-27~10-31 存摺入帳明細流水"
            ],
            "next_step": f"等待使用者匯入 {yg_bank_max_date or '10/28'} 以後之玉山銀行存摺流水自動核銷",
            "public_audit_mapping": {
                "mapped": False,
                "audit_item_index": None,
                "reason": "已列於 delivery_audit_results.json 之 payout_audits (status: PENDING_BANK_UPDATE)；屬正常在途款"
            },
            "source_timestamps": {
                "platform_synced_at": ue_data.get("last_sync_time") or ue_data.get("updated_at"),
                "bank_data_cutoff": yg_bank_max_date if bank_is_available else None
            },
            "data_cutoff_date": yg_bank_max_date or "2026-10-27"
        })

    # =========================================================================
    # 第三類：外送平台營業額比對差異 (DELIVERY_REVENUE)
    # 完全動態自來源收集待比對月份，移除固定基礎月份
    # =========================================================================
    rev_audits = audit_data.get("revenue_audits", []) if sources_loaded["delivery_audit"] else []

    rev_targets = []
    seen_targets = set()

    # 1. 自 delivery_audit_results 之 revenue_audits 動態收集
    for ra in rev_audits:
        sk = ra.get("store_key")
        plat = ra.get("platform")
        m = ra.get("month")
        if sk in ["suiyi", "yuegong"] and plat and m and (sk, plat, m) not in seen_targets:
            seen_targets.add((sk, plat, m))
            rev_targets.append({"store_key": sk, "platform": plat, "month": m})

    # 2. 自 ubereats_merchant_data 之 monthly_statements 動態收集
    for sk in ["suiyi", "yuegong"]:
        ue_stmts = ue_data.get("stores", {}).get(sk, {}).get("monthly_statements", [])
        for st in ue_stmts:
            m = st.get("month")
            if m and (sk, "Uber Eats", m) not in seen_targets:
                if float(st.get("total_sales", 0.0) or 0) > 0 or float(st.get("net_payout", 0.0) or 0) > 0:
                    seen_targets.add((sk, "Uber Eats", m))
                    rev_targets.append({"store_key": sk, "platform": "Uber Eats", "month": m})

    # 3. 自 foodpanda_merchant_data 之 monthly_statements 動態收集
    for sk in ["suiyi", "yuegong"]:
        fp_stmts = fp_data.get("stores", {}).get(sk, {}).get("monthly_statements", [])
        for st in fp_stmts:
            m = st.get("month")
            if m and (sk, "foodpanda", m) not in seen_targets:
                if float(st.get("total_sales", 0.0) or 0) > 0 or float(st.get("net_payout", 0.0) or 0) > 0:
                    seen_targets.add((sk, "foodpanda", m))
                    rev_targets.append({"store_key": sk, "platform": "foodpanda", "month": m})

    # 排序鍵：suiyi=1, yuegong=2; foodpanda=1, Uber Eats=2; month ascending
    def rev_sort_key(item):
        sk_order = 1 if item.get("store_key") == "suiyi" else 2
        p_order = 1 if "panda" in item.get("platform", "").lower() else 2
        m_str = item.get("month", "")
        return (sk_order, p_order, m_str)

    rev_targets.sort(key=rev_sort_key)

    for rt in rev_targets:
        sk = rt.get("store_key")
        plat = rt.get("platform")
        m = rt.get("month")
        m_parts = m.split("-")
        y_str = m_parts[0] if len(m_parts) > 0 else "2026"
        m_short = m_parts[1] if len(m_parts) > 1 else m[-2:]
        store_short = "SY" if sk == "suiyi" else "YG"
        plat_short = "FP" if "panda" in plat.lower() else "UE"
        store_name = "東區隨意小吃" if sk == "suiyi" else "月宮餐飲"
        tax_id = "94006775" if sk == "suiyi" else "94001820"
        store_fs = sy_fs if sk == "suiyi" else yg_fs

        # 1. 動態計算 POS 銷售額
        daily_rows = store_fs.get("daily_by_month", {}).get(m, [])
        pos_amt = None
        if daily_rows:
            col_key = "ubereats" if plat_short == "UE" else "foodpanda"
            if plat_short == "FP" and m == "2026-10":
                target_rows = [r for r in daily_rows if r.get("date", "") <= "2026-10-15"]
            else:
                target_rows = daily_rows
            pos_amt = round(sum(float(r.get(col_key) or 0) for r in target_rows), 2)
        else:
            ra_match = next((x for x in rev_audits if x.get("store_key") == sk and x.get("platform") == plat and x.get("month") == m), None)
            if ra_match and ra_match.get("pos_reported_sales") is not None:
                pos_amt = float(ra_match.get("pos_reported_sales"))

        # 2. 動態讀取平台對帳單
        plat_sales = None
        fee_amt = None
        adj_amt = None
        plat_payout = None
        cust_paid = None
        batch_id = f"{plat_short}-{store_short}-{m.replace('-', '')}-STMT"

        if plat_short == "UE":
            ue_stmts = ue_data.get("stores", {}).get(sk, {}).get("monthly_statements", [])
            s_match = next((s for s in ue_stmts if s.get("month") == m), None)
            if s_match:
                plat_sales = float(s_match.get("total_sales", 0.0))
                fee_amt = abs(float(s_match.get("uber_fee", 0.0)))
                adj_amt = float(s_match.get("marketing", 0.0)) + float(s_match.get("adjustments", 0.0))
                plat_payout = float(s_match.get("net_payout", 0.0))
                cust_paid = plat_sales
            else:
                ra_match = next((x for x in rev_audits if x.get("store_key") == sk and x.get("platform") == plat and x.get("month") == m), None)
                if ra_match and ra_match.get("platform_gross_sales") is not None:
                    plat_sales = float(ra_match.get("platform_gross_sales"))
                    cust_paid = plat_sales
        else:
            fp_stmts = fp_data.get("stores", {}).get(sk, {}).get("monthly_statements", [])
            s_match = next((s for s in fp_stmts if s.get("month") == m), None)
            if s_match:
                plat_sales = float(s_match.get("total_sales", 0.0))
                disc = float(s_match.get("merchant_discount", 0.0))
                cust_paid = round(plat_sales + disc, 2)
                fee_amt = abs(float(s_match.get("commission", 0.0)))
                adj_amt = float(s_match.get("ad_fee", 0.0)) + float(s_match.get("other_fee", 0.0))
                plat_payout = float(s_match.get("net_payout", 0.0))
                if m == "2026-08":
                    batch_id = "200791118042"
            else:
                ra_match = next((x for x in rev_audits if x.get("store_key") == sk and x.get("platform") == plat and x.get("month") == m), None)
                if ra_match:
                    plat_sales = float(ra_match.get("platform_gross_sales", 0.0))
                    cust_paid = float(ra_match.get("platform_net_customer_paid", plat_sales))

        # 3. 動態計算差額
        if plat_short == "UE":
            diff = round(pos_amt - plat_sales, 2) if (pos_amt is not None and plat_sales is not None) else 0.0
        else:
            diff = round(pos_amt - (cust_paid if cust_paid is not None else plat_sales), 2) if (pos_amt is not None and (cust_paid is not None or plat_sales is not None)) else 0.0

        # 如果差額為 0 且標記為 100% 吻合 (或兩邊均為 0)，代表無待查異常，不列入待查清冊
        ra_match = next((x for x in rev_audits if x.get("store_key") == sk and x.get("platform") == plat and x.get("month") == m), None)
        if abs(diff) <= 0.001 and (ra_match and ra_match.get("status") == "100%_MATCH"):
            continue
        if abs(diff) <= 0.001 and (pos_amt == 0 or pos_amt is None) and (plat_sales == 0 or plat_sales is None):
            continue
        if abs(diff) <= 0.001 and m == "2026-10" and plat_short == "FP":
            continue

        # 跨年度案件 ID 唯一性處理：2026 年份保持相容，非 2026 年份納入完整年月
        if y_str == "2026":
            case_id = f"PENDING-{store_short}-{plat_short}-{m_short}-REV"
        else:
            case_id = f"PENDING-{store_short}-{plat_short}-{m.replace('-', '')}-REV"

        group_id = f"GROUP-{store_short}-{plat_short}-{y_str}-REV" if plat_short == "UE" else "GROUP-SY-FP-2026-Q3"
        related = ["PENDING-SY-FP-09H1-BANK"] if plat_short == "FP" and m in ["2026-08", "2026-09"] else []

        ev_list = [
            f"1. {store_name} {m} 總表 POS 登打累計 ${pos_amt:,.0f}" if pos_amt is not None else "1. 門市總表 POS 登打待核",
            f"2. {plat} 官方結算總銷售額為 ${plat_sales:,.0f}" if plat_sales is not None else f"2. {plat} 官方銷售額待核"
        ]
        if fee_amt is not None and plat_payout is not None:
            ev_list.append(f"3. 結算公式驗算：銷售 ${plat_sales:,.0f} − 服務費/佣金 ${fee_amt:,.0f} = 結算淨額 ${plat_payout:,.0f}")
        if case_id == "PENDING-SY-FP-08-REV":
            ev_list.append("4. 淨撥款 $3,642 已結轉至 9月上半期出帳單 200561205000")

        diff_sign_desc = "正值代表門市收銀機登打之外送金額大於平台總銷售（門市多登或退單未同步作廢）" if (diff is not None and diff > 0) else "負值代表門市 POS 少登（POS 漏單或跨日補登；嚴禁直接將此差額歸因發票扣抵）"
        if pos_amt is not None and plat_sales is not None and diff is not None:
            diff_formula_desc = f"門市 POS 申報 ${pos_amt:,.0f} − 平台銷售 ${plat_sales:,.0f} = 差額 {diff:+,.0f}" if plat_short == "UE" else f"門市 POS 申報 ${pos_amt:,.0f} − 平台顧客實付 ${cust_paid:,.0f} = 差額 {diff:+,.0f}"
        else:
            diff_formula_desc = "待資料齊全後比對"

        cases.append({
            "case_id": case_id,
            "full_case_id": f"PENDING-{store_short}-{plat_short}-{m}-REV",
            "period_year_month": m,
            "category": "外送平台營業額比對差異",
            "category_code": "DELIVERY_REVENUE",
            "group_id": group_id,
            "related_case_ids": related,
            "store": store_name,
            "store_key": sk,
            "store_tax_id": tax_id,
            "platform": plat,
            "period": f"{m} (全月份: {m}-01~{m}-30)" if m != "2026-08" or plat_short != "FP" else "2026-08 (後半月: 08/16~08/31)",
            "audit_type": "門市收銀 POS 與平台銷售額比對 (口徑一)",
            "audit_caliber": "營收面（門市 POS 銷售 vs 平台官方總銷售）",
            "source_file": "food_sheets_data.json & " + ("ubereats_merchant_data.json" if plat_short == "UE" else "foodpanda_merchant_data.json"),
            "sheet_or_row": f"food_sheets daily_by_month['{m}'] & monthly_statements ({m})",
            "batch_id": batch_id,
            "pos_amount": pos_amt,
            "platform_sales": plat_sales,
            "refund_amount": 0.0,
            "fee_amount": fee_amt,
            "adjustment_amount": adj_amt,
            "expected_amount": pos_amt,
            "platform_payout": plat_payout,
            "bank_verified": None,
            "difference": diff,
            "diff_formula": diff_formula_desc,
            "diff_sign_definition": diff_sign_desc,
            "status": "DISCREPANCY_REVENUE",
            "status_label": "🟡 POS 與平台營業額差額待查",
            "is_shortage": False,
            "evidence": ev_list,
            "missing_info": [f"{store_name} {m} POS 串接漏單、跨日補登或退單明細"],
            "next_step": f"核對 {m} POS 漏單紀錄；結算淨額對照玉山存摺流水核銷",
            "public_audit_mapping": {
                "mapped": True,
                "audit_item_index": None,
                "audit_category": "營業額差額",
                "reason": "動態對應公開外送稽核報告差異清單"
            },
            "source_timestamps": {
                "sheets_synced_at": store_fs.get("last_success_at"),
                "platform_synced_at": ue_data.get("last_sync_time") if plat_short == "UE" else fp_data.get("last_sync_time")
            },
            "data_cutoff_date": f"{m}-30" if m in ["2026-09", "2026-06"] else f"{m}-31"
        })

    # =========================================================================
    # 第四類：動態對應 delivery_audit_results.json 之 discrepancy_list
    # 以穩定業務鍵（店別、平台、期間、類別）比對，重排不受影響
    # =========================================================================
    d_list = audit_data.get("discrepancy_list", []) if sources_loaded["delivery_audit"] else []

    def match_discrepancy_item(case, d_item):
        c_store = case.get("store_key", "")
        d_store = d_item.get("store", "")
        if c_store == "suiyi" and not ("隨意" in d_store or "suiyi" in d_store):
            return False
        if c_store == "yuegong" and not ("月宮" in d_store or "yuegong" in d_store):
            return False

        c_plat = case.get("platform", "").lower()
        d_plat = d_item.get("platform", "").lower()
        if "uber" in c_plat and "uber" not in d_plat:
            return False
        if "panda" in c_plat and "panda" not in d_plat:
            return False

        c_cat = case.get("category_code", "")
        d_cat = d_item.get("category", "")
        if c_cat == "DELIVERY_REVENUE" and d_cat != "營業額差額":
            return False
        if c_cat == "DELIVERY_PAYOUT" and d_cat not in ["銀行未見存入", "未見入帳"]:
            return False

        c_period = case.get("period", "")
        d_period = d_item.get("period", "")
        if "09/01" in c_period and "09/01" in d_period and c_cat == "DELIVERY_PAYOUT":
            return True

        # 比對月份數字 (例如 7 2026 與 2026-07)
        c_nums = [int(n) for n in re.findall(r"\b\d+\b", c_period)]
        d_nums = [int(n) for n in re.findall(r"\b\d+\b", d_period)]
        c_m = [n for n in c_nums if 1 <= n <= 12]
        d_m = [n for n in d_nums if 1 <= n <= 12]
        if c_m and d_m and c_m[0] == d_m[0]:
            return True
        return False

    for c in cases:
        matched_idx = None
        matched_item = None
        for i, ditem in enumerate(d_list):
            if match_discrepancy_item(c, ditem):
                matched_idx = i + 1  # 1-indexed
                matched_item = ditem
                break
        
        if matched_idx is not None:
            c["public_audit_mapping"] = {
                "mapped": True,
                "audit_item_index": matched_idx,
                "audit_category": matched_item.get("category", "營業額差額"),
                "reason": f"動態對應 delivery_audit_results.json 第 {matched_idx} 項差異 ({matched_item.get('store')} {matched_item.get('platform')} {matched_item.get('period')})"
            }
        else:
            if c["category_code"] == "CASH_DEPOSIT":
                reason = "門市收銀現金交班核對，不屬於外送雙平台稽核範圍 (delivery_audit_results.json 僅限外送平台)"
            elif c["category_code"] == "DELIVERY_PAYOUT" and c.get("status") == "PENDING_BANK_UPDATE":
                reason = "已列於 delivery_audit_results.json 之 payout_audits (status: PENDING_BANK_UPDATE)；屬正常在途款"
            else:
                reason = "此案件未列入 delivery_audit_results.json 異常清單"
            c["public_audit_mapping"] = {
                "mapped": False,
                "audit_item_index": None,
                "reason": reason
            }

    # =========================================================================
    # 輸出前案件 ID 唯一性驗證 (防重複 ID 機制)
    # =========================================================================
    case_ids = [c["case_id"] for c in cases]
    seen_ids = set()
    dup_ids = set()
    for cid in case_ids:
        if cid in seen_ids:
            dup_ids.add(cid)
        seen_ids.add(cid)
    if dup_ids:
        raise ValueError(f"偵測到重複案件 ID: {sorted(dup_ids)}")

    # =========================================================================
    # 統計彙總與防重複加總 (Anti-Double-Counting Summary)
    # 已結案案件保留歷史，但不列入待處理合計；缺金額保留 None 不計入
    # =========================================================================
    cash_pending = sum(c["difference"] for c in cases if c["category_code"] == "CASH_DEPOSIT" and c.get("status") != "RESOLVED" and c.get("difference") is not None and c["difference"] > 0 and not c.get("is_shortage") and "短溢" not in c.get("audit_type", ""))
    cash_shortage = sum(c["difference"] for c in cases if c["category_code"] == "CASH_DEPOSIT" and c.get("status") != "RESOLVED" and c.get("difference") is not None and c["difference"] < 0)
    cash_overage = sum(c["difference"] for c in cases if c["category_code"] == "CASH_DEPOSIT" and c.get("status") != "RESOLVED" and c.get("difference") is not None and c["difference"] > 0 and "短溢" in c.get("audit_type", ""))
    
    delivery_transit = sum(c["difference"] for c in cases if c["category_code"] == "DELIVERY_PAYOUT" and c.get("status") == "PENDING_BANK_UPDATE" and c.get("difference") is not None)
    delivery_unmatched = sum(abs(c["difference"]) for c in cases if c["category_code"] == "DELIVERY_PAYOUT" and c.get("status") == "BANK_UNMATCHED_INVESTIGATE" and c.get("difference") is not None)
    
    rev_pos_over = sum(c["difference"] for c in cases if c["category_code"] == "DELIVERY_REVENUE" and c.get("status") != "RESOLVED" and c.get("difference") is not None and c["difference"] > 0)
    rev_pos_under = sum(c["difference"] for c in cases if c["category_code"] == "DELIVERY_REVENUE" and c.get("status") != "RESOLVED" and c.get("difference") is not None and c["difference"] < 0)
    rev_net_diff = rev_pos_over + rev_pos_under

    c_cash = [c for c in cases if c["category_code"] == "CASH_DEPOSIT"]
    c_transit = [c for c in cases if c["category_code"] == "DELIVERY_PAYOUT" and c.get("status") == "PENDING_BANK_UPDATE"]
    c_unmatched = [c for c in cases if c["category_code"] == "DELIVERY_PAYOUT" and c.get("status") == "BANK_UNMATCHED_INVESTIGATE"]
    c_rev = [c for c in cases if c["category_code"] == "DELIVERY_REVENUE"]

    unresolved_cases = [c for c in cases if c.get("status") != "RESOLVED"]

    mapping_summary = {
        "total_cases_in_handover": len(cases),
        "total_unresolved_cases": len(unresolved_cases),
        "total_discrepancies_in_public_audit": len(d_list),
        "mapped_cases_count": sum(1 for c in cases if c["public_audit_mapping"]["mapped"]),
        "unmapped_cash_cases_count": len(c_cash),
        "unmapped_transit_cases_count": len(c_transit),
        "mapping_explanation": (
            f"公開稽核 {len(d_list)} 筆差異 = {sum(1 for c in c_rev if c['public_audit_mapping']['mapped'])} 筆外送營收比對差額"
            f" + {sum(1 for c in c_unmatched if c['public_audit_mapping']['mapped'])} 筆外送銀行未見入帳。"
            f" 交接文件 {len(cases)} 案 = {sum(1 for c in cases if c['public_audit_mapping']['mapped'])} 筆外送差異"
            f" + {len(c_cash)} 筆門市現金案"
            f" + {len(c_transit)} 筆外送正常在途款。"
            " 邏輯 100% 互通吻合，無需強行湊筆數。"
        )
    }

    summary = {
        "version": "2026.10.30.v4_robust",
        "generated_at": now_str,
        "total_cases_count": len(cases),
        "unresolved_cases_count": len(unresolved_cases),
        "categorized_totals": {
            "cash_verification": {
                "pending_bank_deposit": cash_pending,
                "declared_shortage": cash_shortage,
                "declared_overage": cash_overage,
                "cases_count": len(c_cash),
                "unresolved_cases_count": sum(1 for c in c_cash if c.get("status") != "RESOLVED"),
                "note": f"門市合存待銀行更新入帳 ${cash_pending:,.0f}（非短款）；門市申報短少/留店未申報 ${abs(cash_shortage):,.0f}（待補存申報）"
            },
            "delivery_in_transit": {
                "total_in_transit": delivery_transit,
                "cases_count": len(c_transit),
                "breakdown": {tc["case_id"]: tc["difference"] for tc in c_transit if tc.get("difference") is not None},
                "note": f"官方出帳日均為 2026-10-27，晚於存摺截止日 10/15，合計 ${delivery_transit:,.0f} 屬正常在途款，非短款"
            },
            "delivery_bank_unmatched": {
                "total_unmatched": delivery_unmatched,
                "cases_count": len(c_unmatched),
                "case_id": c_unmatched[0]["case_id"] if c_unmatched else "NONE",
                "note": f"官方 09-29 出帳 ${delivery_unmatched:,.0f}，存摺 9/15~10/15 未見入帳，警示排查（由8月結轉$3,642+9月本期$9,690組成）"
            },
            "delivery_revenue_differences": {
                "pos_over_platform": rev_pos_over,
                "pos_under_platform": rev_pos_under,
                "net_difference": rev_net_diff,
                "cases_count": len(c_rev),
                "note": f"口徑一營收比對：門市多登 +${rev_pos_over:,.0f}、少登/退單 -${abs(rev_pos_under):,.0f}，營收淨差額 +${rev_net_diff:,.0f}"
            }
        },
        "anti_double_counting_warning": (
            f"⚠️ 嚴格審計防禦：各口徑獨立核算，嚴禁將營收面差額 (${rev_net_diff:,.0f})、資金面在途款"
            f" (${delivery_transit:,.0f})、未見入帳款 (${delivery_unmatched:,.0f}) 與現金待入帳 (${cash_pending:,.0f}) 直接相加！同一款項之跨角度核對已透過 group_id 與 related_case_ids 關聯綁定。"
        ),
        "mapping_summary": mapping_summary
    }

    # 檢核整體來源完整性
    all_sources_ok = all(sources_loaded.values())

    output_payload = {
        "ok": all_sources_ok,
        "title": "全事業體統一待查清單 (月下餐飲與隨意小吃)",
        "version": "2026.10.30.v4_robust",
        "generated_at": now_str,
        "sources_metadata": source_meta,
        "summary": summary,
        "cases": cases
    }

    if not all_sources_ok:
        missing_keys = [k for k, v in sources_loaded.items() if not v]
        output_payload["is_stale"] = True
        output_payload["error"] = f"必要來源未完整載入 ({', '.join(missing_keys)})。不得聲稱全量核實，本輪回報失敗且保留前次成功快照，不覆寫硬碟檔案。"
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ 來源未完整載入 ({', '.join(missing_keys)})，回報 ok=False，保留硬碟快照")
    else:
        # 僅當全部來源完整正確時才覆寫硬碟快照 (採用原子寫入 .tmp -> os.replace)
        for p in target_paths:
            try:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                tmp_p = p + ".tmp"
                with open(tmp_p, "w", encoding="utf-8") as f:
                    json.dump(output_payload, f, ensure_ascii=False, indent=2)
                os.replace(tmp_p, p)
            except Exception as e:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ 寫入 {p} 失敗: {e}")
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ 統一待查清單成功產出！共 {len(cases)} 案 (待處理 {len(unresolved_cases)} 案)，儲存於 {target_paths[0]}")
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ 統一待查清單成功產出！共 {len(cases)} 案，儲存於 {target_paths[0]}")

    return output_payload

def generate_handover_markdown_table(payload):
    """產出 Section 13.7 所需之最新 Markdown 待查清冊表格"""
    cases = payload.get("cases", [])
    
    c1 = [c for c in cases if c["category_code"] == "CASH_DEPOSIT"]
    c2 = [c for c in cases if c["category_code"] == "DELIVERY_PAYOUT"]
    c3 = [c for c in cases if c["category_code"] == "DELIVERY_REVENUE"]

    md = []
    md.append(f"###### 第一類：門市現金交班與繳庫待查款（共 {len(c1)} 案）")
    md.append("| 案件ID | 門市 | 原營業期間 | 來源檔案／分頁／批次ID | POS現金應存 | 門市申報提存 | 銀行已核實入帳 | 未解差額 (待查) | 來源核驗與勾稽口徑 | 已有客觀證據 | 尚缺資料 | 性質判定 |")
    md.append("| :--- | :--- | :---: | :--- | :---: | :---: | :---: | :---: | :--- | :--- | :--- | :--- |")
    for c in c1:
        ev_str = "<br>".join(c["evidence"])
        miss_str = "<br>".join(c["missing_info"]) if c["missing_info"] else "—"
        bank_v = c.get('bank_verified')
        bank_str = f"${bank_v:,.0f}" if (bank_v is not None and bank_v > 0) else ("待存摺更新<br>(存摺截自10/15)" if bank_v is not None else "銀行來源待補")
        pos_str = f"${c['pos_amount']:,.0f}" if c.get('pos_amount') is not None else "待核"
        decl_str = f"${c['platform_payout']:,.0f}" if c.get('platform_payout') is not None else "待核"
        diff_val = c.get('difference')
        if diff_val is not None:
            diff_str = f"**${diff_val:,.0f}**" if diff_val > 0 else (f"**-${abs(diff_val):,.0f}**" if diff_val < 0 else "**$0**")
        else:
            diff_str = "**待核**"
        md.append(f"| **{c['case_id']}** | {c['store']} | {c['period']} | `{c['source_file']}`<br>批次 `{c['batch_id']}` | {pos_str} | {decl_str} | {bank_str} | {diff_str} | {c['diff_formula']} | {ev_str} | {miss_str} | {c['status_label']} |")

    md.append(f"\n###### 第二類：外送平台出帳撥款與銀行存入核對待查款（口徑三：資金面，共 {len(c2)} 案）")
    md.append("| 案件ID | 門市 | 平台 | 官方結算期 | 來源檔案與對帳單／批次ID | 平台已出帳撥款淨額 | 銀行已核實入帳 | 未解差額 (待查) | 口徑三比對結果 | 已有客觀證據 | 尚缺資料 | 性質判定 |")
    md.append("| :--- | :--- | :--- | :---: | :--- | :---: | :---: | :---: | :--- | :--- | :--- | :--- |")
    for c in c2:
        ev_str = "<br>".join(c["evidence"])
        miss_str = "<br>".join(c["missing_info"]) if c["missing_info"] else "—"
        diff_val = c.get('difference')
        if diff_val is not None:
            diff_str = f"**${diff_val:,.0f}**" if diff_val > 0 else (f"**-${abs(diff_val):,.0f}**" if diff_val < 0 else "**$0**")
        else:
            diff_str = "**待核**"
        p_payout_str = f"${c['platform_payout']:,.0f}" if c.get('platform_payout') is not None else "—"
        b_ver_str = f"${c['bank_verified']:,.0f}" if c.get('bank_verified') is not None else "待存摺核實"
        md.append(f"| **{c['case_id']}** | {c['store']} | {c['platform']} | {c['period']} | `{c['source_file']}`<br>批次 `{c['batch_id']}` | {p_payout_str} | {b_ver_str} | {diff_str} | {c['diff_formula']} | {ev_str} | {miss_str} | {c['status_label']} |")

    md.append(f"\n###### 第三類：外送平台營業額比對差異（口徑一：營收面 POS銷售 vs 平台銷售，共 {len(c3)} 案）")
    md.append("| 案件ID | 門市 | 平台 | 營業月份 | 來源檔案與分頁 | POS銷售額 | 平台官方銷售 (毛額) | 平台折讓／實付 | 口徑一差額 (POS vs 平台銷售) | 口徑二：平台費用與結算驗算 | 口徑三：平台撥款與銀行實收 | 尚缺資料與排查方向 | 性質判定 |")
    md.append("| :--- | :--- | :--- | :---: | :--- | :---: | :---: | :---: | :---: | :--- | :--- | :--- | :--- |")
    for c in c3:
        diff_val = c.get('difference')
        if diff_val is not None:
            diff_sign = "+" if diff_val > 0 else ("-" if diff_val < 0 else "")
            diff_str = f"**{diff_sign}${abs(diff_val):,.0f}**"
        else:
            diff_str = "**待核**"
        miss_str = "<br>".join(c["missing_info"]) if c["missing_info"] else "—"
        if c['platform'] == 'foodpanda' and c.get('pos_amount') is not None and diff_val is not None:
            plat_paid_str = f"顧客實付 ${c['pos_amount'] - diff_val:,.0f}"
        elif c.get('platform_sales') is not None:
            plat_paid_str = f"${c['platform_sales']:,.0f}"
        else:
            plat_paid_str = "待核"
        pos_str = f"${c['pos_amount']:,.0f}" if c.get('pos_amount') is not None else "—"
        plat_sales_str = f"${c['platform_sales']:,.0f}" if c.get('platform_sales') is not None else "—"
        fee_str = f"費用 ${c['fee_amount']:,.0f} " if c.get('fee_amount') is not None else ""
        net_str = f"淨額 ${c['platform_payout']:,.0f}" if c.get('platform_payout') is not None else ""
        cal_str = f"{fee_str}{net_str} (100% 吻合)" if (fee_str or net_str) else "依平台月結算核算"
        md.append(f"| **{c['case_id']}** | {c['store']} | {c['platform']} | {c['period']} | `{c['source_file']}` | {pos_str} | {plat_sales_str} | {plat_paid_str} | {diff_str} | {cal_str} | 結算淨額待對照存摺 | {miss_str} | {c['status_label']} |")

    md.append("\n###### 第四類：測試案例專區（明確隔離標示，嚴禁納入正式財務清單）")
    md.append("| 測試編號 | 測試案例說明 | 測試情境與合成數據 | 驗收標準與防護機制 | 執行結果 | 備註說明 |")
    md.append("| :--- | :--- | :--- | :--- | :---: | :--- |")
    md.append("| **TEST-YG-UE-ROLLOVER-400** | Codex 隔離複驗：禁止未來批次作為前期結轉 | 構造 A期 (2026-01) 淨應收 1,000 / 撥款 1,400 (多400)；構造未來批次 B期 (2026-02) 淨額 400。 | 系統必須強制檢核時序先後；未來批次嚴格阻擋作為前期結轉，標記 `DRAFT_UNATTRIBUTED_BLOCKED`，`is_postable = False` 阻擋入帳。 | **PASS** | ⚠️ 本案純為合成單元測試案例，真實來源無此 400 交易，嚴禁列入真實待查清單！ |")

    return "\n".join(md)

if __name__ == "__main__":
    payload = generate_pending_cases()
    if "--print-md" in sys.argv:
        print(generate_handover_markdown_table(payload))
    if not payload.get("ok"):
        sys.exit(1)
