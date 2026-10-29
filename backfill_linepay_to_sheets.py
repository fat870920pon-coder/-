# -*- coding: utf-8 -*-
"""
自動回填 LINE Pay 撥款入帳金額至 Google 試算表 Z 欄 (linepay 實際入款) 並備註營收時間
支援：
- 隨意小吃 (每週結算週期，次週二撥款)
- 月宮 / 月下餐飲 (每日營業款 T+2 撥款，遇假日順延，週五至週日合併次週二撥款)

優化特點：
- 使用 Google Sheets API 批次讀取與批次寫入 (BatchUpdate)
- 避免個別儲存格讀寫觸發 429 請求配額限制
- 數值與儲存格備註 (Note) 一次原子化更新
"""

import os, sys, json, re, time
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CRED_PATH = "/Users/liujiaxiang/Desktop/腳本資料夾/月下/文中會計傳票產生器/credentials.json"
LINEPAY_JSON_PATH = os.path.join(BASE_DIR, "linepay_merchant_data.json")

STORE_SHEET_CONFIGS = {
    "suiyi": {
        "name": "隨意小吃",
        "spreadsheet_id": "1MQAJfH8ZY1D9mtFdtoOPldXlcSdmuY9XOZRKOX0bfNw",
        "type": "WEEKLY",
        "items_key": "weekly_cycles"
    },
    "yuegong": {
        "name": "月下餐飲 (月宮)",
        "spreadsheet_id": "19QdWcBQWVNi-SWvzuWhA9lfZFInbtrbmpNIqINa1DYA",
        "type": "T_PLUS_2",
        "items_key": "t2_batches"
    }
}

def clean_money_val(val):
    if not val:
        return 0
    s = re.sub(r"[^\d.-]", "", str(val).strip())
    try:
        return int(round(float(s)))
    except Exception:
        return 0

def get_col_letter(col_idx):
    """0-indexed 欄位轉字母 (25 -> Z)"""
    if col_idx < 26:
        return chr(65 + col_idx)
    return chr(65 + (col_idx // 26) - 1) + chr(65 + (col_idx % 26))

def backfill_linepay_to_sheets(target_store=None, target_month=None, dry_run=False, only_empty=False):
    """
    將 LINE Pay 後台已撥款批次資料自動回填至 Google 試算表 Z 欄並備註營收時間
    - target_store: 'suiyi' | 'yuegong' | None (兩者皆更新)
    - target_month: 指定月份如 '2026-10'。若為 None，預設為當前月 (例如 2026-10)
    - dry_run: 若為 True 僅輸出將變更之內容，不實際寫入 Google 試算表
    - only_empty: 若為 True 僅在 Z 欄原本為空白時才回填
    """
    if not os.path.exists(LINEPAY_JSON_PATH):
        return {"ok": False, "error": f"找不到 LINE Pay 數據檔案：{LINEPAY_JSON_PATH}"}

    with open(LINEPAY_JSON_PATH, "r", encoding="utf-8") as f:
        lp_all = json.load(f)

    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    creds = Credentials.from_service_account_file(CRED_PATH, scopes=scopes)
    gc = gspread.authorize(creds)

    results = {}
    stores_to_process = [target_store] if target_store in STORE_SHEET_CONFIGS else list(STORE_SHEET_CONFIGS.keys())

    # 若未指定 target_month，預設聚焦當前月份（如 2026-10），避免重寫過去已封帳月份
    default_active_month = target_month or datetime.now().strftime("%Y-%m")

    for sk in stores_to_process:
        cfg = STORE_SHEET_CONFIGS[sk]
        s_data = lp_all.get("stores", {}).get(sk, {})
        items = s_data.get(cfg["items_key"], [])
        if not items and sk == "suiyi":
            items = lp_all.get("weekly_cycles", [])

        print(f"\n==========================================")
        print(f"開始處理門市：{cfg['name']} ({sk})")
        print(f"==========================================")

        sh = gc.open_by_key(cfg["spreadsheet_id"])
        
        # 決定要處理的工作表
        if target_month:
            target_months = [target_month]
        else:
            # 預設處理當前月份
            target_months = [default_active_month]

        store_updates = []

        for m_title in target_months:
            try:
                ws = sh.worksheet(m_title)
            except Exception as e:
                print(f"  ⚠️ 找不到工作表 {m_title}：{e}")
                continue

            # 篩選該月份已撥款批次
            month_items = []
            for it in items:
                p_date = it.get("payout_date", "")
                is_paid = (
                    it.get("is_paid") or 
                    it.get("status") in ["PAID", "🟢 已撥款入帳", "🟢 已撥款"] or 
                    (it.get("actual_payout") is not None and it.get("actual_payout") > 0)
                )
                if not is_paid:
                    continue
                if p_date.startswith(m_title):
                    month_items.append(it)

            if not month_items:
                print(f"  ℹ️ 工作表 {m_title} 無需處理的撥款資料。")
                continue

            print(f"  正在讀取 {m_title} 工作表（待比對撥款批次：{len(month_items)} 筆）...")
            vals = ws.get_values()
            if len(vals) < 3:
                continue

            # 尋找日期欄與 Z 欄 (linepay 實際入款)
            header_row_idx = 1
            for idx in range(min(4, len(vals))):
                row_str = " ".join(str(c) for c in vals[idx])
                if "日期" in row_str:
                    header_row_idx = idx
                    break

            header = vals[header_row_idx]
            date_col_idx = 1   # 預設 B 欄 (0-indexed: 1)
            z_col_idx = 25      # 預設 Z 欄 (0-indexed: 25)

            for c_i, h in enumerate(header):
                nh = re.sub(r"\s+", "", str(h)).lower()
                if nh == "日期":
                    date_col_idx = c_i
                elif "linepay" in nh and ("實際入款" in nh or "實際收款" in nh or "實際" in nh):
                    z_col_idx = c_i
                    break

            col_letter = get_col_letter(z_col_idx)

            # 建立日期到列號的映射 (1-indexed)
            date_to_row = {}
            for r_i, r in enumerate(vals):
                if r_i <= header_row_idx:
                    continue
                d_str = r[date_col_idx].strip() if len(r) > date_col_idx else ""
                if not d_str:
                    continue
                clean_d = d_str.split(" ")[0].replace("/", "-")
                m_match = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", clean_d)
                if m_match:
                    norm_d = f"{int(m_match.group(1)):04d}-{int(m_match.group(2)):02d}-{int(m_match.group(3)):02d}"
                    date_to_row[norm_d] = r_i + 1

            # 一次性批次取得該工作表該欄位的 notes (避免每格呼叫觸發 429)
            note_by_row = {}
            try:
                note_range = f"'{m_title}'!{col_letter}1:{col_letter}{len(vals)}"
                resp = gc.http_client.request('get', f"https://sheets.googleapis.com/v4/spreadsheets/{sh.id}?ranges={note_range}&fields=sheets.data.rowData.values.note")
                data = resp.json()
                if "sheets" in data and data["sheets"]:
                    sheet_data = data["sheets"][0].get("data", [])
                    if sheet_data:
                        row_data = sheet_data[0].get("rowData", [])
                        for r_idx, r_val in enumerate(row_data):
                            row_cells = r_val.get("values", [])
                            if row_cells:
                                n_val = row_cells[0].get("note", "")
                                if n_val:
                                    note_by_row[r_idx + 1] = n_val
            except Exception as e:
                print(f"    ⚠️ 批次取得備註時發生警告（將使用空白備註）：{e}")

            # 逐筆核對並準備 BatchUpdate
            batch_update_requests = []

            for it in month_items:
                p_date = it["payout_date"]
                row_num = date_to_row.get(p_date)
                if not row_num:
                    print(f"    ⚠️ 找不到撥款日 {p_date} 對應之表格列！")
                    continue

                payout_amt = int(round(it.get("actual_payout") or it.get("calc_payout") or 0))
                if cfg["type"] == "WEEKLY":
                    st = it["cycle_start"].replace("2026-", "").replace("-", "/")
                    ed = it["cycle_end"].replace("2026-", "").replace("-", "/")
                    note_str = f"{st}–{ed} 營收"
                    raw_range = f"{st}–{ed}"
                else:
                    tp = it["transaction_period"].replace("2026-", "").replace("-", "/")
                    note_str = f"{tp} 營收"
                    raw_range = tp

                cur_val_raw = vals[row_num - 1][z_col_idx] if len(vals[row_num - 1]) > z_col_idx else ""
                cur_val_num = clean_money_val(cur_val_raw)
                cell_a1 = f"{col_letter}{row_num}"

                if only_empty and cur_val_raw.strip():
                    continue

                cur_note = note_by_row.get(row_num, "")

                # 正規化比對備註是否已包含營收日期範圍
                norm_cur = re.sub(r"[\s～~–—]+", "-", cur_note)
                norm_target = re.sub(r"[\s～~–—]+", "-", raw_range)
                is_note_present = bool(norm_target and norm_target in norm_cur) or (note_str in cur_note)

                needs_val_update = (cur_val_num != payout_amt)
                needs_note_update = not is_note_present

                if needs_val_update or needs_note_update:
                    print(f"    📝 撥款日 {p_date} ({cell_a1})：")
                    if needs_val_update:
                        print(f"       數值更新：現值 [{cur_val_raw}] ➔ 新值 [${payout_amt:,}]")
                    if needs_note_update:
                        print(f"       備註更新：現有 [{repr(cur_note)}] ➔ 新增 [{note_str}]")

                    combined_note = (cur_note.strip() + "\n" + note_str).strip() if (cur_note and note_str not in cur_note) else (cur_note if not needs_note_update else note_str)

                    # 建立 Google Sheets API updateCells 請求
                    cell_data = {
                        "note": combined_note
                    }
                    fields_list = ["note"]

                    if needs_val_update or not cur_val_raw.strip():
                        cell_data["userEnteredValue"] = {"stringValue": f"${payout_amt:,}"}
                        fields_list.append("userEnteredValue")

                    req = {
                        "updateCells": {
                            "range": {
                                "sheetId": ws.id,
                                "startRowIndex": row_num - 1,
                                "endRowIndex": row_num,
                                "startColumnIndex": z_col_idx,
                                "endColumnIndex": z_col_idx + 1,
                            },
                            "rows": [{
                                "values": [cell_data]
                            }],
                            "fields": ",".join(fields_list)
                        }
                    }
                    batch_update_requests.append(req)

                    store_updates.append({
                        "payout_date": p_date,
                        "cell": cell_a1,
                        "amount": payout_amt,
                        "formatted_amount": f"${payout_amt:,}",
                        "note": combined_note,
                        "previous_value": cur_val_raw,
                        "status": "UPDATED" if not dry_run else "WOULD_UPDATE"
                    })
                else:
                    print(f"    ✅ 撥款日 {p_date} ({cell_a1})：已是最新數值 ${payout_amt:,} 且備註齊全，略過。")

            # 執行一次性批次寫入
            if not dry_run and batch_update_requests:
                print(f"  🚀 正在批次寫入 {len(batch_update_requests)} 項變更至 {cfg['name']} ({m_title})...")
                sh.batch_update({"requests": batch_update_requests})
                print(f"  ✨ {cfg['name']} ({m_title}) 批次更新完成！")

        results[sk] = {
            "name": cfg["name"],
            "updates_count": len(store_updates),
            "updates": store_updates
        }

    return {"ok": True, "dry_run": dry_run, "results": results}

if __name__ == "__main__":
    is_dry = "--dry-run" in sys.argv
    is_only_empty = "--only-empty" in sys.argv
    m_arg = None
    st_arg = None
    for arg in sys.argv[1:]:
        if arg.startswith("202"):
            m_arg = arg
        elif arg in STORE_SHEET_CONFIGS:
            st_arg = arg

    res = backfill_linepay_to_sheets(target_store=st_arg, target_month=m_arg, dry_run=is_dry, only_empty=is_only_empty)
    print("\n執行結果概要：")
    for s_k, s_r in res.get("results", {}).items():
        print(f"  [{s_r['name']}] 更新筆數：{s_r['updates_count']}")
