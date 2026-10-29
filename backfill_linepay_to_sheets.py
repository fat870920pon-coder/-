# -*- coding: utf-8 -*-
"""
自動回填 LINE Pay 撥款入帳金額至 Google 試算表 Z 欄 (linepay 實際入款) 並備註營收時間
支援：
- 隨意小吃 (每週結算週期，次週二撥款)
- 月下餐飲 (月宮) (每日營業款 T+2 撥款，遇假日順延，週五至週日合併次週二撥款)

跨月拆算規則：
- 若週期跨月份（如每週週期 09/28–10/04 於 10/06 撥款）：
  1. 上月份營收（09/28–09/30）：依實際營收比例拆算淨額，統一計入「上月份月底（09/30）」，
     備註：「實際營收：MM/DD–MM/DD、實際存款日期：MM/DD、金額：$X,XXX（整筆撥款 $XX,XXX）」
  2. 下月份營收（10/01–10/04）：計入下月份「原匯款日（10/06）」，
     備註：「MM/DD–MM/DD 營收：$XX,XXX（整筆 MM/DD 撥款 $XX,XXX，其中 MM/DD–MM/DD 營收 $X,XXX 已入上月底）」
- 若營收全屬上月，但撥款日在次月月初（如 09/29 於 10/01 撥款、09/30 於 10/02 撥款）：
  - 統一歸入上月份月底（09/30），次月月初不重複計入。
- 若同一撥款日有多筆款項（如月宮 10/21 與 10/22 營收合併於 10/27 撥款）：
  - 自動依實際到帳日合併金額與備註。
"""

import os, sys, json, re, time
from datetime import datetime, timedelta
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
    if col_idx < 26:
        return chr(65 + col_idx)
    return chr(65 + (col_idx // 26) - 1) + chr(65 + (col_idx % 26))

def get_month_last_day(year_month_str):
    parts = year_month_str.split("-")
    y, m = int(parts[0]), int(parts[1])
    if m == 12:
        next_m = datetime(y + 1, 1, 1)
    else:
        next_m = datetime(y, m + 1, 1)
    last_d = next_m - timedelta(days=1)
    return last_d.strftime("%Y-%m-%d")

def generate_target_entries(store_key, store_data):
    entries = []
    cfg = STORE_SHEET_CONFIGS[store_key]
    items = store_data.get(cfg["items_key"], [])
    dr = store_data.get("daily_records", {})

    for it in items:
        # 計算實際到帳日期與實際入款金額
        settle_batches = it.get("settlement_batches", [])
        completed_payout = 0
        comp_dates = []

        if settle_batches:
            for sb in settle_batches:
                if sb.get("status") == "PAID" or sb.get("settlement_completion_date"):
                    completed_payout += sb.get("schedule_amount", 0)
                    if sb.get("settlement_completion_date"):
                        comp_dates.append(sb["settlement_completion_date"])
            act_deposit_date = max(comp_dates) if comp_dates else it.get("payout_date", "")
        else:
            completed_payout = it.get("actual_payout") or 0
            act_deposit_date = it.get("payout_date", "")

        is_paid = (
            it.get("is_paid") is True or 
            it.get("status") in ["PAID", "🟢 已撥款入帳", "🟢 已撥款"] or
            (completed_payout > 0 and len(comp_dates) > 0)
        ) and (completed_payout > 0 or (it.get("actual_payout") is not None and it.get("actual_payout") > 0))

        if not is_paid:
            continue

        act_p = int(round(completed_payout if completed_payout > 0 else (it.get("actual_payout") or it.get("calc_payout") or 0)))
        if act_p <= 0:
            continue

        if cfg["type"] == "WEEKLY":
            c_st = it["cycle_start"]
            c_ed = it["cycle_end"]
            p_date = it.get("payout_date", "")
            m_st, m_ed = c_st[:7], c_ed[:7]

            if m_st != m_ed:
                # ─── 跨月週期拆算 ───
                dt = datetime.strptime(c_st, "%Y-%m-%d")
                dt_ed = datetime.strptime(c_ed, "%Y-%m-%d")
                s1, s2 = 0, 0
                days1, days2 = [], []
                while dt <= dt_ed:
                    ds = dt.strftime("%Y-%m-%d")
                    sa = dr.get(ds, {}).get("net_amount", 0)
                    if ds.startswith(m_st):
                        days1.append(ds)
                        s1 += sa
                    else:
                        days2.append(ds)
                        s2 += sa
                    dt += timedelta(days=1)

                tot_s = s1 + s2
                net1 = int(round(act_p * (s1 / tot_s))) if tot_s > 0 else 0
                net2 = act_p - net1

                # 1. 上月份部分：入上月月底
                st_m1 = days1[0][5:].replace("-", "/")
                ed_m1 = days1[-1][5:].replace("-", "/")
                p_m_d = p_date[5:].replace("-", "/")
                note_m1 = f"實際營收：{st_m1}–{ed_m1}、實際存款日期：{p_m_d}、金額：${net1:,}（整筆撥款 ${act_p:,}）"
                entries.append({
                    "sheet_title": m_st,
                    "target_date": days1[-1],
                    "amount": net1,
                    "note": note_m1,
                    "desc": f"跨月拆算-歸{m_st}月底"
                })

                # 2. 下月份部分：入下月原匯款日
                st_m2 = days2[0][5:].replace("-", "/")
                ed_m2 = days2[-1][5:].replace("-", "/")
                prev_month_num = int(m_st.split("-")[1])
                note_m2 = f"{st_m2}–{ed_m2} 營收：${net2:,}（整筆 {p_m_d} 撥款 ${act_p:,}，其中 {st_m1}–{ed_m1} 營收 ${net1:,} 已入 {prev_month_num} 月底）"
                entries.append({
                    "sheet_title": m_ed,
                    "target_date": p_date,
                    "amount": net2,
                    "note": note_m2,
                    "desc": f"跨月拆算-歸{m_ed}匯款日"
                })

            else:
                p_m = p_date[:7]
                st_s = c_st[5:].replace("-", "/")
                ed_s = c_ed[5:].replace("-", "/")
                if p_m == m_st:
                    entries.append({
                        "sheet_title": m_st,
                        "target_date": p_date,
                        "amount": act_p,
                        "note": f"{st_s}–{ed_s} 營收",
                        "desc": "一般同月撥款"
                    })
                else:
                    p_m_d = p_date[5:].replace("-", "/")
                    last_day_m = get_month_last_day(m_st)
                    entries.append({
                        "sheet_title": m_st,
                        "target_date": last_day_m,
                        "amount": act_p,
                        "note": f"實際營收：{st_s}–{ed_s}、實際存款日期：{p_m_d}、金額：${act_p:,}",
                        "desc": "全屬上月-歸上月底"
                    })

        else:
            # ─── T+2 每日/週末撥款 (月宮) ───
            tp = it.get("transaction_period", "")
            tp_clean = tp.replace("2026-", "").replace("-", "/")
            parts = [p.strip() for p in tp.split("～")]
            m_st, m_ed = parts[0][:7], parts[-1][:7]
            p_m = act_deposit_date[:7]

            if m_st == m_ed:
                if p_m == m_st:
                    entries.append({
                        "sheet_title": m_st,
                        "target_date": act_deposit_date,
                        "amount": act_p,
                        "note": f"{tp_clean} 營收",
                        "desc": "一般T+2撥款"
                    })
                else:
                    p_m_d = act_deposit_date[5:].replace("-", "/")
                    last_day_m = get_month_last_day(m_st)
                    entries.append({
                        "sheet_title": m_st,
                        "target_date": last_day_m,
                        "amount": act_p,
                        "note": f"實際營收：{tp_clean}、實際存款日期：{p_m_d}、金額：${act_p:,}",
                        "desc": "全屬上月-歸上月底"
                    })

    by_sheet = {}
    for e in entries:
        st_title = e["sheet_title"]
        if st_title not in by_sheet:
            by_sheet[st_title] = []
        by_sheet[st_title].append(e)

    return by_sheet

def is_date_already_noted(cur_note, target_note):
    """檢查既有備註是否已包含此項營收說明，避免重複追加"""
    if not cur_note:
        return False
    if target_note in cur_note:
        return True

    def norm_d(s):
        s = re.sub(r"0(\d)", r"\1", str(s))
        return re.sub(r"[\s～~–—]+", "-", s)

    norm_cur = norm_d(cur_note)
    m_range = re.search(r"(\d{1,2}/\d{1,2}(?:[–～~-]\d{1,2}/\d{1,2})?)", target_note)
    if m_range:
        dr_str = norm_d(m_range.group(1))
        if dr_str in norm_cur:
            return True

    return False

def backfill_linepay_to_sheets(target_store=None, target_month=None, dry_run=False, only_empty=False):
    if not os.path.exists(LINEPAY_JSON_PATH):
        return {"ok": False, "error": f"找不到 LINE Pay 數據檔案：{LINEPAY_JSON_PATH}"}

    with open(LINEPAY_JSON_PATH, "r", encoding="utf-8") as f:
        lp_all = json.load(f)

    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    creds = Credentials.from_service_account_file(CRED_PATH, scopes=scopes)
    gc = gspread.authorize(creds)

    results = {}
    stores_to_process = [target_store] if target_store in STORE_SHEET_CONFIGS else list(STORE_SHEET_CONFIGS.keys())
    default_active_month = target_month or datetime.now().strftime("%Y-%m")

    for sk in stores_to_process:
        cfg = STORE_SHEET_CONFIGS[sk]
        s_data = lp_all.get("stores", {}).get(sk, {})
        entries_by_sheet = generate_target_entries(sk, s_data)

        print(f"\n==========================================")
        print(f"開始處理門市：{cfg['name']} ({sk})")
        print(f"==========================================")

        sh = gc.open_by_key(cfg["spreadsheet_id"])

        if target_month:
            sheets_to_check = [target_month]
        else:
            prev_m_dt = datetime.strptime(default_active_month + "-01", "%Y-%m-%d") - timedelta(days=1)
            prev_month = prev_m_dt.strftime("%Y-%m")
            sheets_to_check = [prev_month, default_active_month]

        store_updates = []

        for m_title in sorted(sheets_to_check):
            m_entries = entries_by_sheet.get(m_title, [])
            if not m_entries:
                continue

            try:
                ws = sh.worksheet(m_title)
            except Exception as e:
                print(f"  ⚠️ 找不到工作表 {m_title}：{e}")
                continue

            print(f"  正在讀取 {m_title} 工作表（待比對目標筆數：{len(m_entries)} 筆）...")
            vals = ws.get_values()
            if len(vals) < 3:
                continue

            header_row_idx = 1
            for idx in range(min(4, len(vals))):
                row_str = " ".join(str(c) for c in vals[idx])
                if "日期" in row_str:
                    header_row_idx = idx
                    break

            header = vals[header_row_idx]
            date_col_idx = 1
            z_col_idx = 25

            for c_i, h in enumerate(header):
                nh = re.sub(r"\s+", "", str(h)).lower()
                if nh == "日期":
                    date_col_idx = c_i
                elif "linepay" in nh and ("實際入款" in nh or "實際收款" in nh or "實際" in nh):
                    z_col_idx = c_i
                    break

            col_letter = get_col_letter(z_col_idx)

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
                print(f"    ⚠️ 批次取得備註警告：{e}")

            # 合併針對同一儲存格的 entry
            entries_by_row = {}
            for e in m_entries:
                t_date = e["target_date"]
                row_num = date_to_row.get(t_date)
                if not row_num:
                    continue
                if row_num not in entries_by_row:
                    entries_by_row[row_num] = []
                entries_by_row[row_num].append(e)

            batch_update_requests = []

            for row_num, r_entries in sorted(entries_by_row.items()):
                cur_val_raw = vals[row_num - 1][z_col_idx] if len(vals[row_num - 1]) > z_col_idx else ""
                cur_val_num = clean_money_val(cur_val_raw)
                cur_note = note_by_row.get(row_num, "")
                cell_a1 = f"{col_letter}{row_num}"

                if only_empty and cur_val_raw.strip():
                    continue

                target_amt = sum(e["amount"] for e in r_entries)
                target_notes = [e["note"] for e in r_entries]

                needs_val_update = False
                if not cur_val_raw.strip():
                    needs_val_update = True
                elif cur_val_num != target_amt:
                    needs_val_update = True

                # 備註檢查
                new_notes_to_add = []
                for n_str in target_notes:
                    if not is_date_already_noted(cur_note, n_str):
                        new_notes_to_add.append(n_str)

                needs_note_update = bool(new_notes_to_add)

                # 歷史月份保護：若為歷史月份且已經有已核定數值，絕對不異動數值與備註
                if m_title < default_active_month and cur_val_num > 0:
                    needs_val_update = False
                    needs_note_update = False
                new_combined_note = cur_note
                if needs_note_update:
                    combined_add = "\n".join(new_notes_to_add)
                    new_combined_note = (new_combined_note.strip() + "\n" + combined_add).strip() if new_combined_note else combined_add

                if needs_val_update or needs_note_update:
                    final_amt = target_amt if needs_val_update else cur_val_num
                    print(f"    📝 目標格 {m_title} {cell_a1}：")
                    if needs_val_update:
                        print(f"       數值更新：現值 [{cur_val_raw}] ➔ 新值 [${final_amt:,}]")
                    if needs_note_update:
                        print(f"       備註更新：現有 [{repr(cur_note)}] ➔ 新增後 [{repr(new_combined_note)}]")

                    cell_data = {"note": new_combined_note}
                    fields_list = ["note"]

                    if needs_val_update or not cur_val_raw.strip():
                        cell_data["userEnteredValue"] = {"stringValue": f"${final_amt:,}"}
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
                        "sheet": m_title,
                        "cell": cell_a1,
                        "amount": final_amt,
                        "note": new_combined_note,
                        "previous_value": cur_val_raw,
                        "status": "UPDATED" if not dry_run else "WOULD_UPDATE"
                    })
                else:
                    print(f"    ✅ 目標格 {m_title} {cell_a1}：已是正確數值 ${cur_val_num:,} 且備註齊全，略過。")

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
