import os
import sys
import re
import json
import argparse
from datetime import datetime, date, timedelta
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
CREDENTIALS_PATH = os.path.join(os.path.dirname(__file__), "credentials.json")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
STATE_FILE = os.path.join(os.path.dirname(__file__), "reconciliation_balance.json")

def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def parse_num(v):
    if not v:
        return 0.0
    s = str(v).strip().replace("$", "").replace(",", "")
    if s == "-" or s == "$-" or s == "":
        return 0.0
    s_clean = re.sub(r"[^\d.-]", "", s)
    try:
        return float(s_clean) if s_clean else 0.0
    except:
        return 0.0

def get_gspread_client():
    import gspread
    from google.oauth2.service_account import Credentials
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    if os.path.exists(CREDENTIALS_PATH):
        creds = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=scopes)
        return gspread.authorize(creds)
    return None

def parse_cleared_entry_revenue_allocation(bank_date, dept, code, rem, amt, daily_cash_lookup):
    """
    解析沖銷傳票分錄所屬之營收月份（權責月份）與金額拆分。
    回傳：(allocations, tag, display_desc)
      - allocations: list of {"month": "YYYY-MM", "amount": float, "desc": str}
      - tag: "跨月拆分" / "跨月歸屬上月" / "當期款項"
      - display_desc: 清楚易讀之拆分/歸屬說明字串
    """
    bank_ym = bank_date[:7]
    try:
        y_int, m_int = int(bank_ym[:4]), int(bank_ym[5:7])
        prev_m = m_int - 1 if m_int > 1 else 12
        prev_y = y_int if m_int > 1 else y_int - 1
        prev_ym = f"{prev_y:04d}-{prev_m:02d}"
    except:
        prev_ym = bank_ym
    sname = "月宮" if ("月宮" in dept or "月下" in dept) else "隨意小吃"

    # 1. 現金多日跨月 (例如 2026-09-30~2026-10-01)
    range_match = re.search(r"(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})", rem)
    if range_match:
        d1_str, d2_str = range_match.group(1), range_match.group(2)
        try:
            d1 = datetime.strptime(d1_str, "%Y-%m-%d").date()
            d2 = datetime.strptime(d2_str, "%Y-%m-%d").date()
            if d1.strftime("%Y-%m") != d2.strftime("%Y-%m"):
                alloc_by_month = {}
                cur_d = d1
                while cur_d <= d2:
                    ym = cur_d.strftime("%Y-%m")
                    d_str = cur_d.strftime("%Y-%m-%d")
                    day_cash = daily_cash_lookup.get((sname, d_str), 0.0) or daily_cash_lookup.get((dept, d_str), 0.0)
                    alloc_by_month[ym] = alloc_by_month.get(ym, 0.0) + day_cash
                    cur_d += timedelta(days=1)
                
                tot_lookup = sum(alloc_by_month.values())
                if tot_lookup > 0:
                    allocs = []
                    descs = []
                    for ym, m_amt in sorted(alloc_by_month.items()):
                        real_amt = round(amt * (m_amt / tot_lookup)) if abs(tot_lookup - amt) > 1 else m_amt
                        allocs.append({"month": ym, "amount": real_amt, "desc": f"{ym[-2:]}月現金"})
                        descs.append(f"{int(ym[-2:])}月 ${real_amt:,.0f}")
                    return allocs, "跨月拆分", "跨月拆分 ➔ " + " / ".join(descs)

            if d1.strftime("%Y-%m") != bank_ym:
                ym = d1.strftime("%Y-%m")
                return [{"month": ym, "amount": amt, "desc": "跨月現金"}], "跨月歸屬上月", f"跨月入帳 ➔ 歸屬 {ym} 月營收"
        except:
            pass

    # 2. 單日現金跨月 (例如 2026-09-30 (上月跨月款))
    if "上月跨月款" in rem:
        return [{"month": prev_ym, "amount": amt, "desc": "上月跨月現金"}], "跨月歸屬上月", f"跨月入帳 ➔ 歸屬 {prev_ym} 月營收 (9/30款)"

    # 3. Foodpanda 上月 (例如 上月下半月(16~30日)外送款)
    if "上月下半月" in rem or "上月" in rem:
        return [{"month": prev_ym, "amount": amt, "desc": "上月外送款"}], "跨月歸屬上月", f"跨月入帳 ➔ 歸屬 {prev_ym} 月營收 (9月下半月款)"

    # 4. MM/DD~MM/DD 外送款 (例如 09/17~09/23外送款)
    m_range = re.search(r"(\d{2})/(\d{2})~(\d{2})/(\d{2})", rem)
    if m_range:
        m_s, m_e = m_range.group(1), m_range.group(3)
        y_str = bank_ym[:4]
        rev_ym = f"{y_str}-{m_s}"
        if rev_ym != bank_ym:
            return [{"month": rev_ym, "amount": amt, "desc": f"{m_s}月外送款"}], "跨月歸屬上月", f"跨月入帳 ➔ 歸屬 {rev_ym} 月營收 ({m_s}/{m_range.group(2)}~{m_e}/{m_range.group(4)})"
        else:
            return [{"month": rev_ym, "amount": amt, "desc": f"{m_s}月外送款"}], "當期款項", f"當期款項 ➔ 歸屬 {rev_ym} 當月營收"

    # 5. 約MM/DD 營業款/電支款 (例如 約09/29營業款)
    m_single = re.search(r"約(\d{2})/(\d{2})", rem)
    if m_single:
        m_s = m_single.group(1)
        y_str = bank_ym[:4]
        rev_ym = f"{y_str}-{m_s}"
        if rev_ym != bank_ym:
            return [{"month": rev_ym, "amount": amt, "desc": f"{m_s}月款"}], "跨月歸屬上月", f"跨月入帳 ➔ 歸屬 {rev_ym} 月營收"
        else:
            return [{"month": rev_ym, "amount": amt, "desc": f"{m_s}月款"}], "當期款項", f"當期款項 ➔ 歸屬 {rev_ym} 當月營收"

    if "2026-08" in rem and bank_ym != "2026-08": return [{"month": "2026-08", "amount": amt, "desc": "8月款"}], "跨月歸屬上月", "跨月入帳 ➔ 歸屬 2026-08 月營收"
    if "2026-09" in rem and bank_ym != "2026-09": return [{"month": "2026-09", "amount": amt, "desc": "9月款"}], "跨月歸屬上月", "跨月入帳 ➔ 歸屬 2026-09 月營收"

    return [{"month": bank_ym, "amount": amt, "desc": "當期款"}], "當期款項", f"當期款項 ➔ 歸屬 {bank_ym} 當月營收"

def calculate_monthly_summary(target_month=None, store_cash_data=None):
    import glob
    config = load_config()
    stores_cfg = config.get("stores", {})
    target_month = target_month or "2026-10"
    m_clean = target_month.replace("-", "")

    gc = get_gspread_client()
    if not gc:
        print("❌ 無法連線至 Google 試算表（缺少 credentials.json）")
        return None

    # 1. 讀取 Google Sheets 上的各通路 POS 營收累計與建立每日現金查找表
    pos_data = {}
    daily_cash_lookup = {}
    channel_mapping = {
        "現金": {"code": "1130-01", "name": "應收帳款-現金"},
        "Line Pay": {"code": "1130-02", "name": "應收帳款-linepay"},
        "Uber Eats": {"code": "1130-03", "name": "應收帳款-ubereats"},
        "Food Panda": {"code": "1130-04", "name": "應收帳款-Foodpanda"},
        "Foodpanda": {"code": "1130-04", "name": "應收帳款-Foodpanda"},
        "偷訂": {"code": "1130-05", "name": "應收帳款-偷訂"},
        "刷卡": {"code": "1130-06", "name": "應收帳款-刷卡"}
    }

    # 讀取已產出的玉山銀行沖銷傳票 (output/文中傳票維護匯入_全部合併_玉山銀行沖銷_YYYYMM.xlsx)
    bank_file = os.path.join(OUTPUT_DIR, f"文中傳票維護匯入_全部合併_玉山銀行沖銷_{m_clean}.xlsx")
    cleared_voucher_cash_by_dept = {}
    cleared_voucher_cash_details = {}
    if os.path.exists(bank_file):
        try:
            wb_check = openpyxl.load_workbook(bank_file, data_only=True)
            ws_chk = wb_check.active
            for r in range(3, ws_chk.max_row + 1):
                b_date = str(ws_chk.cell(r, 3).value or "")
                code = str(ws_chk.cell(r, 7).value or "")
                dept = str(ws_chk.cell(r, 8).value or "")
                amt = float(ws_chk.cell(r, 15).value or 0)
                dc = ws_chk.cell(r, 6).value
                rem = str(ws_chk.cell(r, 16).value or "")
                if dc == 2 and "1130-01" in code:
                    if dept not in cleared_voucher_cash_by_dept:
                        cleared_voucher_cash_by_dept[dept] = set()
                    rev_part = rem
                    if "沖銷" in rem:
                        rev_part = rem.split("沖銷", 1)[1]
                    if "營業現金應收" in rev_part:
                        rev_part = rev_part.split("營業現金應收", 1)[0]
                    elif "玉山" in rev_part:
                        rev_part = rev_part.split("玉山", 1)[0]

                    range_match = re.search(r"(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})", rev_part)
                    if range_match:
                        try:
                            cur_d = datetime.strptime(range_match.group(1), "%Y-%m-%d").date()
                            end_d = datetime.strptime(range_match.group(2), "%Y-%m-%d").date()
                            while cur_d <= end_d:
                                d_s = cur_d.strftime("%Y-%m-%d")
                                cleared_voucher_cash_by_dept[dept].add(d_s)
                                cleared_voucher_cash_details[(dept, d_s)] = {"bank_date": b_date, "bank_amt": amt, "rem": rem}
                                cur_d += timedelta(days=1)
                        except:
                            pass
                    else:
                        m_p = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", rev_part)
                        for d_found in m_p:
                            cleared_voucher_cash_by_dept[dept].add(d_found)
                            cleared_voucher_cash_details[(dept, d_found)] = {"bank_date": b_date, "bank_amt": amt, "rem": rem}
        except Exception:
            pass

    # 讀取次月銀行傳票 (若存在，以標記跨月已存入款項)
    try:
        cur_y, cur_m = [int(x) for x in target_month.split("-")]
        next_m = cur_m + 1
        next_y = cur_y
        if next_m > 12:
            next_y += 1
            next_m = 1
        next_m_clean = f"{next_y}{next_m:02d}"
        next_bank_file = os.path.join(OUTPUT_DIR, f"文中傳票維護匯入_全部合併_玉山銀行沖銷_{next_m_clean}.xlsx")
        if os.path.exists(next_bank_file):
            wb_next = openpyxl.load_workbook(next_bank_file, data_only=True)
            ws_next = wb_next.active
            for r in range(3, ws_next.max_row + 1):
                b_date = str(ws_next.cell(r, 3).value or "")
                code = str(ws_next.cell(r, 7).value or "")
                dept = str(ws_next.cell(r, 8).value or "")
                amt = float(ws_next.cell(r, 15).value or 0)
                dc = ws_next.cell(r, 6).value
                rem = str(ws_next.cell(r, 16).value or "")
                if dc == 2 and "1130-01" in code:
                    if dept not in cleared_voucher_cash_by_dept:
                        cleared_voucher_cash_by_dept[dept] = set()
                    rev_part = rem
                    if "沖銷" in rem:
                        rev_part = rem.split("沖銷", 1)[1]
                    if "營業現金應收" in rev_part:
                        rev_part = rev_part.split("營業現金應收", 1)[0]
                    elif "玉山" in rev_part:
                        rev_part = rev_part.split("玉山", 1)[0]

                    range_match = re.search(r"(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})", rev_part)
                    if range_match:
                        try:
                            cur_d = datetime.strptime(range_match.group(1), "%Y-%m-%d").date()
                            end_d = datetime.strptime(range_match.group(2), "%Y-%m-%d").date()
                            while cur_d <= end_d:
                                d_s = cur_d.strftime("%Y-%m-%d")
                                if d_s.startswith(target_month):
                                    cleared_voucher_cash_by_dept[dept].add(d_s)
                                    cleared_voucher_cash_details[(dept, d_s)] = {"bank_date": b_date, "bank_amt": amt, "rem": rem}
                                cur_d += timedelta(days=1)
                        except:
                            pass
                    else:
                        m_p = re.findall(rf"\b{target_month}-\d{{2}}\b", rev_part)
                        for d_found in m_p:
                            cleared_voucher_cash_by_dept[dept].add(d_found)
                            cleared_voucher_cash_details[(dept, d_found)] = {"bank_date": b_date, "bank_amt": amt, "rem": rem}
    except Exception:
        pass

    for store_key, scfg in stores_cfg.items():
        s_name = scfg.get("name", store_key)
        sid = scfg.get("spreadsheet_id")
        dept = scfg.get("department", s_name)
        pos_data[store_key] = {
            "name": s_name,
            "dept": dept,
            "channels": {},
            "daily_cash": [],
            "pending_alerts": []
        }

        try:
            sh = gc.open_by_key(sid)
            # 建立多月份每日現金查找表 (供跨月拆分使用)
            for ws_item in sh.worksheets():
                if re.match(r"^\d{4}-\d{2}$", ws_item.title):
                    w_rows = ws_item.get_all_values()
                    if len(w_rows) > 2:
                        w_h = w_rows[1]
                        w_c_idx = w_h.index("現金") if "現金" in w_h else None
                        if w_c_idx:
                            for r in w_rows[2:]:
                                if len(r) > 1 and len(r) > w_c_idx and r[1]:
                                    d_str_chk = r[1].strip()
                                    c_amt_chk = parse_num(r[w_c_idx])
                                    if c_amt_chk > 0:
                                        daily_cash_lookup[(s_name, d_str_chk)] = c_amt_chk
                                        daily_cash_lookup[(store_key, d_str_chk)] = c_amt_chk
                                        daily_cash_lookup[(dept, d_str_chk)] = c_amt_chk

            # 讀取當月試算表
            ws = [w for w in sh.worksheets() if w.title == target_month][0]
            rows = ws.get_all_values()
            headers = rows[1]
            
            for ch_name, info in channel_mapping.items():
                code = info["code"]
                if code not in pos_data[store_key]["channels"]:
                    pos_data[store_key]["channels"][code] = {
                        "name": info["name"],
                        "pos_sales": 0.0,
                        "bank_cleared": 0.0,
                        "invoice_cleared": 0.0
                    }
                
                if ch_name in headers:
                    idx = headers.index(ch_name)
                    total_ch = 0.0
                    for r in rows[2:]:
                        if len(r) > 1 and r[1] and any(c.isdigit() for c in r[1]) and "小計" not in r[1]:
                            total_ch += parse_num(r[idx]) if idx < len(r) else 0.0
                    pos_data[store_key]["channels"][code]["pos_sales"] += total_ch

            # 整理每日現金與存入狀態（雙層審核：第一層總表 U 欄存款金額 ➔ 第二層玉山銀行存摺流水）
            cash_idx = headers.index("現金") if "現金" in headers else None
            dep_time_idx = headers.index("存款時間") if "存款時間" in headers else None
            dep_amt_idx = headers.index("存款金額") if "存款金額" in headers else None
            voucher_idx = headers.index("存款傳票號碼") if "存款傳票號碼" in headers else None

            # 1. 建立門市每日營收列表與申報提存清單
            daily_list = []
            declared_deposits = []
            for r in rows[2:]:
                if len(r) > 1 and r[1] and any(c.isdigit() for c in r[1]) and "小計" not in r[1]:
                    d_str = r[1].strip()
                    c_amt = parse_num(r[cash_idx]) if cash_idx and len(r) > cash_idx else 0.0
                    d_time = str(r[dep_time_idx]).strip() if dep_time_idx and len(r) > dep_time_idx else ""
                    d_amt = parse_num(r[dep_amt_idx]) if dep_amt_idx and len(r) > dep_amt_idx else 0.0
                    v_str = str(r[voucher_idx]).strip() if voucher_idx and len(r) > voucher_idx else ""

                    if c_amt > 0:
                        daily_list.append({
                            "date": d_str,
                            "cash": c_amt,
                            "has_declared": False,
                            "declared_amt": 0.0,
                            "declared_time": "",
                            "declared_note": "",
                            "bank_cleared": False,
                            "bank_amt": 0.0,
                            "bank_date": "",
                            "bank_rem": "",
                            "audit_status": "UNPAID",
                            "audit_label": "🔴 門市未存 (留店未存)",
                            "used": False,
                            "deposit_date": "",
                            "period": d_str
                        })
                    if d_amt > 0:
                        declared_deposits.append({
                            "row_date": d_str,
                            "dep_time": d_time or d_str,
                            "dep_amt": d_amt,
                            "voucher": v_str,
                            "used": False
                        })

            # 2. 第一層審核：優先比對營運總表 U 欄【存款金額】
            # Pass 1: 單日金額完全相符 (容差 <= 1.0)
            for drec in daily_list:
                for decl in declared_deposits:
                    if not decl["used"] and abs(decl["dep_amt"] - drec["cash"]) <= 1.0:
                        decl["used"] = True
                        drec["has_declared"] = True
                        drec["declared_amt"] = decl["dep_amt"]
                        drec["declared_time"] = decl["dep_time"]
                        dt_s = decl["dep_time"]
                        da_s = decl["dep_amt"]
                        drec["declared_note"] = f"申報於 {dt_s} 存入 ${da_s:,.0f}"
                        drec["deposit_date"] = decl["dep_time"]
                        break

            # Pass 2: 多日累計存款比對 (例如 10/16 存 $31,720 是 10/14+10/15)
            for decl in declared_deposits:
                if decl["used"]:
                    continue
                for w in range(2, 8):
                    for i in range(len(daily_list) - w + 1):
                        chunk = daily_list[i:i+w]
                        if any(c["has_declared"] for c in chunk):
                            continue
                        c_sum = sum(c["cash"] for c in chunk)
                        if abs(c_sum - decl["dep_amt"]) <= 1500.0:
                            decl["used"] = True
                            p_lbl = f"{chunk[0]['date']}~{chunk[-1]['date']}"
                            dt_s = decl["dep_time"]
                            da_s = decl["dep_amt"]
                            for c in chunk:
                                c["has_declared"] = True
                                c["declared_amt"] = decl["dep_amt"]
                                c["declared_time"] = decl["dep_time"]
                                c["declared_note"] = f"申報於 {dt_s} 存入 ${da_s:,.0f} ({p_lbl}累計)"
                                c["deposit_date"] = decl["dep_time"]
                                c["period"] = p_lbl
                            break
                    if decl["used"]:
                        break

            # Pass 3: 當日列已填存款金額之兜底
            for drec in daily_list:
                if not drec["has_declared"]:
                    for decl in declared_deposits:
                        if not decl["used"] and decl["row_date"] == drec["date"]:
                            decl["used"] = True
                            drec["has_declared"] = True
                            drec["declared_amt"] = decl["dep_amt"]
                            drec["declared_time"] = decl["dep_time"]
                            dt_s = decl["dep_time"]
                            da_s = decl["dep_amt"]
                            drec["declared_note"] = f"申報於 {dt_s} 存入 ${da_s:,.0f}"
                            drec["deposit_date"] = decl["dep_time"]
                            break

            # 3. 第二層審核：比對玉山銀行存摺流水
            for drec in daily_list:
                is_cleared = False
                b_info = None
                for (k_dept, k_date), info in cleared_voucher_cash_details.items():
                    if k_date == drec["date"] and (s_name in k_dept or dept in k_dept or store_key in k_dept or k_dept in s_name or k_dept in dept):
                        is_cleared = True
                        b_info = info
                        break
                if not is_cleared:
                    for d_dept, dates_set in cleared_voucher_cash_by_dept.items():
                        if (s_name in d_dept or dept in d_dept or store_key in d_dept or d_dept in s_name or d_dept in dept) and (drec["date"] in dates_set):
                            is_cleared = True
                            break

                if is_cleared:
                    drec["bank_cleared"] = True
                    if b_info:
                        drec["bank_amt"] = b_info["bank_amt"]
                        drec["bank_date"] = b_info["bank_date"]
                        drec["bank_rem"] = b_info["rem"]
                        drec["deposit_date"] = b_info["bank_date"]

            # 4. 判定雙層審核結論 (Dual Tier Status)
            for drec in daily_list:
                if drec["bank_cleared"] and drec["has_declared"]:
                    drec["audit_status"] = "MATCHED"
                    drec["audit_label"] = "🟢 雙審通過 (已入帳結清)"
                    drec["used"] = True
                elif drec["has_declared"] and not drec["bank_cleared"]:
                    drec["audit_status"] = "DECLARED_PENDING_BANK"
                    drec["audit_label"] = "🟡 門市已申報 (待銀行入帳)"
                    drec["used"] = True
                elif not drec["has_declared"] and drec["bank_cleared"]:
                    drec["audit_status"] = "BANK_ONLY"
                    drec["audit_label"] = "🔵 門市漏填 (銀行已實收)"
                    drec["used"] = True
                else:
                    drec["audit_status"] = "UNPAID"
                    drec["audit_label"] = "🔴 門市未存 (留店未存)"
                    drec["used"] = False

            pos_data[store_key]["daily_cash"] = daily_list

            # 5. 產生門市現金追蹤警示 (區分：🔴 實質未存 vs 🟡 門市已申報待到帳)
            alerts = []
            today_ref = date(2026, 10, 22)
            for item in daily_list:
                if item["cash"] <= 0:
                    continue
                try:
                    s_dt = datetime.strptime(item["date"], "%Y-%m-%d").date()
                    delay = max(0, (today_ref - s_dt).days)
                except:
                    delay = 0

                if item["audit_status"] == "UNPAID":
                    if delay >= 5:
                        risk = "🚨 嚴重滯留 (超過 5 天未存未報)"
                        rec = "門市未申報提存且銀行未入帳，請向店長追查現金保管現況"
                    elif delay >= 3:
                        risk = "⚠️ 一般滯留 (3~5 天未存未報)"
                        rec = "門市未申報提存，請提醒門市今日內存入銀行"
                    else:
                        risk = "⏳ 近期交班 (1~2 天正常流程)"
                        rec = "門市正常交班處理中，待總表申報或存款"

                    alerts.append({
                        "store": s_name,
                        "date": item["date"],
                        "cash": item["cash"],
                        "delay_days": delay,
                        "risk": risk,
                        "recommendation": rec,
                        "audit_status": "UNPAID",
                        "audit_label": item["audit_label"],
                        "declared_info": "❌ 總表U欄空白未填",
                        "bank_info": "❌ 存摺未見入帳"
                    })
                elif item["audit_status"] == "DECLARED_PENDING_BANK":
                    note_txt = item.get("declared_note", "")
                    alerts.append({
                        "store": s_name,
                        "date": item["date"],
                        "cash": item["cash"],
                        "delay_days": delay,
                        "risk": "🟡 門市已申報 (待銀行入帳)",
                        "recommendation": f"總表已登記提存，待存摺更新自動結清 ({note_txt})",
                        "audit_status": "DECLARED_PENDING_BANK",
                        "audit_label": item["audit_label"],
                        "declared_info": f"✅ {note_txt}",
                        "bank_info": "⏳ 存摺尚未到帳/未更新"
                    })
            pos_data[store_key]["pending_alerts"] = alerts

        except Exception as e:
            print(f"⚠️ 讀取【{s_name}】營收試算表失敗: {e}")

    # 2. 跨月份智慧歸屬與沖銷傳票累計 (依照營業款項所屬月份歸屬)
    all_bank_files = sorted(glob.glob(os.path.join(OUTPUT_DIR, "文中傳票維護匯入_全部合併_玉山銀行沖銷_*.xlsx")))
    bridge_passbook_total = 0.0
    bridge_collected_for_prev = 0.0
    bridge_collected_in_next = 0.0
    bridge_revenue_cleared = 0.0

    for b_file in all_bank_files:
        try:
            wb_b = openpyxl.load_workbook(b_file, data_only=True)
            ws_b = wb_b.active
            is_cur_month_file = (f"_{m_clean}.xlsx" in b_file)

            for r in range(3, ws_b.max_row + 1):
                b_date = str(ws_b.cell(r, 3).value or "")
                code = str(ws_b.cell(r, 7).value or "")
                dept = str(ws_b.cell(r, 8).value or "")
                dc = ws_b.cell(r, 6).value
                amt = float(ws_b.cell(r, 15).value or 0)
                rem = str(ws_b.cell(r, 16).value or "")

                # 若是當月銀行傳票檔中的 1102 借方，累加為存摺當月實收流水
                if is_cur_month_file and dc == 1 and code == "1102":
                    bridge_passbook_total += amt

                # 針對所有貸方應收沖銷，進行跨月份拆分與歸屬
                if dc == 2 and code.startswith("1130-"):
                    allocations, tag, desc = parse_cleared_entry_revenue_allocation(
                        b_date, dept, code, rem, amt, daily_cash_lookup
                    )
                    
                    for alloc in allocations:
                        alloc_m = alloc["month"]
                        alloc_amt = alloc["amount"]

                        # 若該分錄款項歸屬本月營收 (權責月 == target_month)
                        if alloc_m == target_month:
                            for skey, sdata in pos_data.items():
                                if sdata["dept"] in dept or sdata["name"] in dept:
                                    if code in sdata["channels"]:
                                        sdata["channels"][code]["bank_cleared"] += alloc_amt
                            bridge_revenue_cleared += alloc_amt

                        # 統計跨月收付調節
                        if is_cur_month_file and alloc_m < target_month:
                            bridge_collected_for_prev += alloc_amt
                        elif (not is_cur_month_file) and alloc_m == target_month:
                            bridge_collected_in_next += alloc_amt
        except Exception as e:
            print(f"⚠️ 讀取銀行傳票檔【{os.path.basename(b_file)}】失敗: {e}")

    # 3. 讀取手續費發票沖銷傳票 (若有產出)
    inv_file = os.path.join(OUTPUT_DIR, f"文中傳票維護匯入_手續費發票_{m_clean}.xlsx")
    if os.path.exists(inv_file):
        try:
            wb = openpyxl.load_workbook(inv_file, data_only=True)
            ws = wb.active
            for r in range(3, ws.max_row + 1):
                dept = str(ws.cell(r, 8).value or "")
                code = str(ws.cell(r, 7).value or "")
                dc = ws.cell(r, 6).value
                amt = float(ws.cell(r, 15).value or 0)
                if dc == 2:  # 貸方沖銷
                    for skey, sdata in pos_data.items():
                        if sdata["dept"] in dept or sdata["name"] in dept:
                            if code in sdata["channels"]:
                                sdata["channels"][code]["invoice_cleared"] += amt
        except Exception as e:
            pass

    # 4. 計算差額與狀態說明 (依照月份真實權責營收計算誤差)
    for skey, sdata in pos_data.items():
        for code, cdata in sdata["channels"].items():
            pos = cdata["pos_sales"]
            bank = cdata["bank_cleared"]
            inv = cdata["invoice_cleared"]
            diff = pos - bank - inv
            cdata["diff"] = diff

            # 自動產生智慧狀態說明
            if abs(diff) < 1.0:
                cdata["status"] = "✅ 已全數結清沖銷"
            elif code == "1130-01":
                if diff > 0:
                    cdata["status"] = f"⏳ 門市尚有 ${diff:,.0f} 現金待存入銀行"
                else:
                    cdata["status"] = "✅ 門市現金已全數存入沖銷"
            elif code == "1130-02":
                if diff > 0:
                    cdata["status"] = f"⏳ 連加網路正常撥款作業中（約待撥 ${diff:,.0f}）"
                else:
                    cdata["status"] = "✅ LINE Pay 款項已全數入帳沖銷"
            elif code in ["1130-03", "1130-04"]:
                platform_name = "Uber Eats" if "03" in code else "Foodpanda"
                if diff > 0:
                    cdata["status"] = f"⏳ {platform_name} 外送週期結算撥款中（約待撥 ${diff:,.0f}）"
                else:
                    cdata["status"] = f"✅ {platform_name} 外送款已全數入帳沖銷"
            else:
                cdata["status"] = f"結算中（待撥 ${diff:,.0f}）" if diff > 0 else "✅ 已全數結清沖銷"

    # 5. 保存跨月調節指標與每日現金查找表
    pos_data["_daily_cash_lookup"] = daily_cash_lookup
    pos_data["_bridge"] = {
        "bank_passbook_total": bridge_passbook_total,
        "collected_for_prev_month": bridge_collected_for_prev,
        "collected_in_next_month_for_cur": bridge_collected_in_next,
        "revenue_cleared_total": bridge_revenue_cleared
    }

    # 6. 持久化儲存 (自動記憶至 local JSON)
    memory_data = {}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                memory_data = json.load(f)
        except:
            memory_data = {}

    memory_data[target_month] = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stores": {k: v for k, v in pos_data.items() if not k.startswith("_")},
        "_bridge": pos_data["_bridge"]
    }

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(memory_data, f, ensure_ascii=False, indent=2)

    return pos_data

def print_and_export_summary(pos_data, target_month="2026-10"):
    m_clean = target_month.replace("-", "")
    bridge = pos_data.get("_bridge", {})
    daily_cash_lookup = pos_data.get("_daily_cash_lookup", {})

    print("\n" + "="*85)
    print(f"📊 【月下餐飲集團 - {target_month} 對帳差額與應收餘額記憶表（依營收月份權責結算）】")
    print(f"🕒 結算記憶時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*85)

    if bridge:
        pb_total = bridge.get("bank_passbook_total", 0.0)
        c_prev = bridge.get("collected_for_prev_month", 0.0)
        c_next = bridge.get("collected_in_next_month_for_cur", 0.0)
        r_cleared = bridge.get("revenue_cleared_total", 0.0)
        print("\n🏦 【玉山銀行存摺收付流水 vs 權責營收沖銷調節】")
        print(f"   1. 玉山銀行存摺當月實收流水總額：$ {pb_total:>11,.0f} (現金流水)")
        print(f"   2. 扣除：當月存摺代收上月跨月款項：-$ {c_prev:>11,.0f} (屬於上月營收)")
        print(f"   3. 加計：次月存摺補存入之本月款項：+$ {c_next:>11,.0f} (屬於本月營收)")
        print(f"   ------------------------------------------------------------")
        print(f"   4. 等於：屬於本月營收之實收沖銷總額：$ {r_cleared:>11,.0f} (權責發生)")
        print("="*85)

    excel_rows = [
        ["事業體", "會計項目代號", "應收通路", "POS 累計營收 (本月權責)", "所屬本月實收沖銷", "手續費發票已沖", "目前未收差額 (真實誤差)", "對帳狀態與說明"]
    ]

    for skey, sdata in pos_data.items():
        if skey.startswith("_"):
            continue
        s_name = sdata["name"]
        print(f"\n🏢 事業體 ➔ 【{s_name}】")
        print(f"{'通路名稱':<16} {'POS營收累計':>12} {'所屬本月實收':>12} {'發票已沖銷':>10} {'真實誤差/未收':>12}  狀態說明")
        print("-" * 85)

        for code, cdata in sdata["channels"].items():
            if cdata["pos_sales"] == 0 and cdata["bank_cleared"] == 0:
                continue
            name_clean = cdata["name"].replace("應收帳款-", "")
            pos = cdata["pos_sales"]
            bank = cdata["bank_cleared"]
            inv = cdata["invoice_cleared"]
            diff = cdata["diff"]
            status = cdata["status"]

            print(f"{name_clean:<14} ${pos:>11,.0f} ${bank:>11,.0f} ${inv:>9,.0f} ${diff:>11,.0f}  {status}")
            excel_rows.append([s_name, code, name_clean, pos, bank, inv, diff, status])

    # 印出穿透式門市未存款與異常清單
    all_alerts = []
    for skey, sdata in pos_data.items():
        if skey.startswith("_"):
            continue
        for a in sdata.get("pending_alerts", []):
            all_alerts.append(a)

    all_alerts.sort(key=lambda x: x.get("delay_days", 0), reverse=True)

    print("\n" + "="*85)
    print("🚨 【門市現金雙層審核與異常追蹤清單】(第一層總表U欄 ➔ 第二層銀行存摺)")
    print("="*85)
    if all_alerts:
        print(f"{'事業體':<14} {'營業日期':<12} {'當日現金':>10} {'雙層審核結論':<20} 總表申報與銀行到帳說明")
        print("-" * 85)
        for a in all_alerts:
            decl_txt = a.get("declared_info", "")
            lbl = a.get("audit_label", a.get("risk", ""))
            print(f"{a['store']:<12} {a['date']:<12} ${a['cash']:>9,.0f}  {lbl:<22} {decl_txt}")
    else:
        print("✅ 太棒了！全體門市所有營業現金皆已全數存入銀行並完成沖銷。")
    print("="*85)

    # 產出 Excel 報表
    out_file = os.path.join(OUTPUT_DIR, f"本月對帳差額與應收餘額表_{m_clean}.xlsx")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "對帳差額總表"

    for r_idx, row in enumerate(excel_rows, start=1):
        for c_idx, val in enumerate(row, start=1):
            cell = ws.cell(row=r_idx, column=c_idx, value=val)
            if r_idx == 1:
                cell.font = Font(bold=True, color="FFFFFF", size=11)
                cell.fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
                cell.alignment = Alignment(horizontal="center", vertical="center")
            else:
                if isinstance(val, (int, float)):
                    cell.number_format = "#,##0"
                    cell.alignment = Alignment(horizontal="right")
                else:
                    cell.alignment = Alignment(horizontal="left")

    # 在總表下方增列跨月調節橋樑
    if bridge:
        r_start = len(excel_rows) + 3
        ws.cell(row=r_start, column=1, value="【玉山銀行存摺收付流水 vs 權責營收沖銷調節表】").font = Font(bold=True, size=11, color="1F4E78")
        bridge_lines = [
            ("1. 玉山銀行存摺當月實際入帳總額（現金流水）", bridge.get("bank_passbook_total", 0.0), "存摺各筆實際入帳金額之總和"),
            ("2. 扣除：當月存摺代收上月跨月款項", -bridge.get("collected_for_prev_month", 0.0), "月初入帳之上月營業款（外送、LINE Pay或上月底現金）"),
            ("3. 加計：次月存摺補存入之本月款項", bridge.get("collected_in_next_month_for_cur", 0.0), "月底營業款於次月初存入之金額"),
            ("4. 等於：屬於本月營收之實收沖銷總額（權責發生）", bridge.get("revenue_cleared_total", 0.0), "與上方「所屬本月實收沖銷」完全相符，可精確比對真實誤差")
        ]
        for i, (title_b, val_b, note_b) in enumerate(bridge_lines, start=r_start+1):
            c1 = ws.cell(row=i, column=1, value=title_b)
            c1.font = Font(bold=(i == r_start+4))
            c2 = ws.cell(row=i, column=5, value=val_b)
            c2.number_format = "#,##0"
            c2.font = Font(bold=True, color="002060" if i == r_start+4 else "000000")
            c3 = ws.cell(row=i, column=8, value=note_b)
            c3.font = Font(italic=True, color="595959")

    for col in ws.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = openpyxl.utils.get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(max_len * 1.5, 12)

    # 產出分頁 2: 玉山銀行單筆沖銷明細對照表（依照銀行帳務列出，並標記跨月拆分）
    bank_file = os.path.join(OUTPUT_DIR, f"文中傳票維護匯入_全部合併_玉山銀行沖銷_{m_clean}.xlsx")
    if os.path.exists(bank_file):
        try:
            wb_b = openpyxl.load_workbook(bank_file, data_only=True)
            ws_b = wb_b.active
            ws2 = wb.create_sheet(title="玉山單筆沖銷對照")

            detail_header = [
                "序號", "事業體", "銀行入帳日", "交易項目類別", "銀行入帳金額", "沖銷金額", "👉 所屬營收/款項日(哪幾日)", "👉 營收歸屬月份與跨月拆分", "傳票摘要"
            ]
            ws2.append(detail_header)

            vouchers = []
            cur_v = None
            for r in range(3, ws_b.max_row + 1):
                dc = ws_b.cell(r, 6).value  # 1: 借, 2: 貸
                code = str(ws_b.cell(r, 7).value or "")
                dept = str(ws_b.cell(r, 8).value or "")
                b_date = str(ws_b.cell(r, 3).value or "")
                amt = float(ws_b.cell(r, 15).value or 0)
                rem = str(ws_b.cell(r, 16).value or "")

                if dc == 1 and code == "1102":
                    if cur_v:
                        vouchers.append(cur_v)
                    cur_v = {
                        "date": b_date,
                        "dept": dept,
                        "bank_amt": amt,
                        "cleared_amt": 0.0,
                        "code": "",
                        "rem": rem,
                        "fee": 0.0,
                        "short_over": 0.0
                    }
                elif cur_v:
                    if code.startswith("1130-"):
                        cur_v["cleared_amt"] += amt
                        cur_v["code"] = code
                        cur_v["rem"] = rem
                    elif code in ["5151", "1268"]:
                        cur_v["fee"] += amt
                    elif code == "7112":
                        if dc == 2:
                            cur_v["short_over"] += amt
                        else:
                            cur_v["short_over"] -= amt

            if cur_v:
                vouchers.append(cur_v)

            row_tags = []
            for cur_idx, v in enumerate(vouchers, 1):
                code = v["code"]
                rem = v["rem"]
                matched_period = "當期款項"
                m_p = re.search(r"\(款項日[:\s]*([^)]+)\)", rem)
                if not m_p:
                    m_p = re.search(r"\(所屬款項日[:\s]*([^)]+)\)", rem)
                if not m_p:
                    m_p = re.search(r"沖銷\s*(.*?)\s*(?:營業現金|LINE|Uber|Foodpanda|偷訂)", rem)
                if m_p:
                    matched_period = m_p.group(1).strip()
                elif "LINE" in rem:
                    matched_period = "約 T-2 營業日款"
                elif "Uber" in rem:
                    matched_period = "前週結算外送款"
                elif "Foodpanda" in rem:
                    matched_period = "半月結算外送款"
                elif "偷訂" in rem:
                    matched_period = "線上訂餐平台款"

                item_type = "現金存入" if "1130-01" in code else (
                    "LINE Pay" if "1130-02" in code else (
                        "Uber Eats" if "1130-03" in code else (
                            "Foodpanda" if "1130-04" in code else (
                                "偷訂" if "1130-05" in code else "其他金流"
                            )
                        )
                    )
                )

                final_rem = rem
                if v["fee"] > 0:
                    final_rem = f"{rem} (銀行實收 ${v['bank_amt']:,.0f}，內含平台手續費/稅 ${v['fee']:,.0f})"
                elif abs(v["short_over"]) > 0:
                    so_sign = "+" if v["short_over"] > 0 else "-"
                    final_rem = f"{rem} (銀行實收 ${v['bank_amt']:,.0f}，短溢調節 {so_sign}${abs(v['short_over']):,.0f})"

                # 解析營收歸屬月份與跨月拆分
                allocations, tag, alloc_desc = parse_cleared_entry_revenue_allocation(
                    v["date"], v["dept"], code, rem, v["cleared_amt"] or v["bank_amt"], daily_cash_lookup
                )
                row_tags.append(tag)

                # 欄位：序號, 事業體, 銀行入帳日, 交易項目類別, 銀行入帳金額, 沖銷金額, 所屬營收/款項日, 營收歸屬月份與跨月拆分, 傳票摘要
                ws2.append([
                    cur_idx, v["dept"], v["date"], item_type, v["bank_amt"], v["cleared_amt"] or v["bank_amt"], matched_period, alloc_desc, final_rem
                ])

            for c_idx in range(1, len(detail_header) + 1):
                cell = ws2.cell(row=1, column=c_idx)
                cell.font = Font(bold=True, color="FFFFFF", size=11)
                cell.fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
                cell.alignment = Alignment(horizontal="center", vertical="center")

            for r_idx in range(2, ws2.max_row + 1):
                ws2.cell(row=r_idx, column=1).alignment = Alignment(horizontal="center")
                ws2.cell(row=r_idx, column=3).alignment = Alignment(horizontal="center")
                
                # 交易項目類別顏色區分 (現金 vs LINE Pay 等)
                cat_cell = ws2.cell(row=r_idx, column=4)
                cat_cell.alignment = Alignment(horizontal="center")
                cat_val = str(cat_cell.value or "")
                if "現金" in cat_val:
                    cat_cell.fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
                    cat_cell.font = Font(bold=True, color="7F6000")
                elif "LINE" in cat_val:
                    cat_cell.fill = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")
                    cat_cell.font = Font(bold=True, color="276A3C")
                elif "Uber" in cat_val:
                    cat_cell.fill = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")
                    cat_cell.font = Font(bold=True, color="333333")
                elif "Foodpanda" in cat_val:
                    cat_cell.fill = PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid")
                    cat_cell.font = Font(bold=True, color="C00000")
                elif "偷訂" in cat_val:
                    cat_cell.fill = PatternFill(start_color="E8D7F1", end_color="E8D7F1", fill_type="solid")
                    cat_cell.font = Font(bold=True, color="7030A0")

                ws2.cell(row=r_idx, column=5).number_format = "#,##0"
                ws2.cell(row=r_idx, column=6).number_format = "#,##0"
                
                target_cell = ws2.cell(row=r_idx, column=7)
                target_cell.font = Font(bold=True, color="1F4E78")
                target_cell.fill = PatternFill(start_color="EDF2F8", end_color="EDF2F8", fill_type="solid")
                target_cell.alignment = Alignment(horizontal="center")

                # 營收歸屬月份與跨月拆分樣式
                alloc_cell = ws2.cell(row=r_idx, column=8)
                alloc_cell.alignment = Alignment(horizontal="center")
                cur_tag = row_tags[r_idx - 2] if (r_idx - 2) < len(row_tags) else "當期款項"
                if cur_tag == "跨月拆分":
                    alloc_cell.fill = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid") # 淡紫色
                    alloc_cell.font = Font(bold=True, color="1F4E78")
                elif cur_tag == "跨月歸屬上月":
                    alloc_cell.fill = PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid") # 淡橘色
                    alloc_cell.font = Font(bold=True, color="C00000")
                else:
                    alloc_cell.fill = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid") # 淡綠色
                    alloc_cell.font = Font(bold=True, color="276A3C")

            for col in ws2.columns:
                max_len = max(len(str(cell.value or '')) for cell in col)
                col_letter = openpyxl.utils.get_column_letter(col[0].column)
                ws2.column_dimensions[col_letter].width = max(max_len * 1.5, 12)
        except Exception as e:
            print(f"⚠️ 產出單筆沖銷明細分頁失敗: {e}")

    # 產出分頁 3: 門市現金雙層審核與異常追蹤清單
    if all_alerts:
        ws3 = wb.create_sheet(title="門市現金雙層審核與異常")
        alert_header = [
            "序號", "事業體", "營業日期", "當日現金營收", "第一層：總表U欄申報狀況", "第二層：玉山銀行入帳狀態", "雙層審核結論", "已延遲天數", "處置建議"
        ]
        ws3.append(alert_header)

        for idx, a in enumerate(all_alerts, start=1):
            decl_info = a.get("declared_info", "")
            bank_info = a.get("bank_info", "")
            lbl = a.get("audit_label", a.get("risk", ""))
            ws3.append([
                idx, a["store"], a["date"], a["cash"], decl_info, bank_info, lbl, f"{a['delay_days']} 天", a["recommendation"]
            ])

        for c_idx in range(1, len(alert_header) + 1):
            cell = ws3.cell(row=1, column=c_idx)
            cell.font = Font(bold=True, color="FFFFFF", size=11)
            cell.fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
            cell.alignment = Alignment(horizontal="center", vertical="center")

        for r_idx in range(2, ws3.max_row + 1):
            ws3.cell(row=r_idx, column=1).alignment = Alignment(horizontal="center")
            ws3.cell(row=r_idx, column=3).alignment = Alignment(horizontal="center")
            ws3.cell(row=r_idx, column=4).number_format = "#,##0"
            ws3.cell(row=r_idx, column=5).alignment = Alignment(horizontal="left")
            ws3.cell(row=r_idx, column=6).alignment = Alignment(horizontal="center")
            ws3.cell(row=r_idx, column=7).alignment = Alignment(horizontal="center")
            ws3.cell(row=r_idx, column=8).alignment = Alignment(horizontal="center")

            # 依雙層審核結論標記顏色
            status_cell = ws3.cell(row=r_idx, column=7)
            txt = str(status_cell.value or "")
            if "門市已申報" in txt:
                status_cell.font = Font(bold=True, color="9C6500")
                status_cell.fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid") # 淡黃色
            elif "門市未存" in txt or "嚴重" in txt:
                status_cell.font = Font(bold=True, color="9C0006")
                status_cell.fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid") # 淡紅色
            else:
                status_cell.font = Font(bold=True, color="006100")
                status_cell.fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid") # 淡綠色

        for col in ws3.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            col_letter = openpyxl.utils.get_column_letter(col[0].column)
            ws3.column_dimensions[col_letter].width = max(max_len * 1.5, 12)

    wb.save(out_file)
    print("\n" + "="*85)
    print(f"💾 對帳差額記憶表已同步輸出 Excel（含 Sheet 3 異常穿透追蹤）：{out_file}")
    print(f"🧠 本地記憶庫已自動更新：{os.path.basename(STATE_FILE)}")
    print("="*85 + "\n")

    # 自動生成可視化 HTML 看板
    try:
        import generate_dashboard
        generate_dashboard.generate_html_dashboard(target_month=target_month)
    except Exception as ge:
        print(f"⚠️ 產出 HTML 看板時發生錯誤: {ge}")

    return out_file

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="本月份對帳差額與應收餘額記憶彙總器")
    parser.add_argument("--month", default="2026-10", help="指定查詢月份 (YYYY-MM)")
    args = parser.parse_args()

    data = calculate_monthly_summary(target_month=args.month)
    if data:
        print_and_export_summary(data, target_month=args.month)
