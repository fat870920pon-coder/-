# -*- coding: utf-8 -*-
import os, sys, json, re
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CRED_PATH = os.path.join(BASE_DIR, "credentials.json")
OUTPUT_JSON = os.path.join(BASE_DIR, "food_sheets_data.json")

def parse_num(val):
    if not val:
        return 0.0
    s = re.sub(r"[^\d.-]", "", str(val))
    try:
        return float(s)
    except Exception:
        return 0.0

def sync_sheets():
    import gspread
    from google.oauth2.service_account import Credentials

    print("Connecting to Google Sheets via Service Account...")
    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    creds = Credentials.from_service_account_file(CRED_PATH, scopes=scopes)
    gc = gspread.authorize(creds)

    configs = {
        "yuegong": {
            "name": "月下餐飲 (月宮)",
            "store_key": "月宮",
            "spreadsheet_id": "19QdWcBQWVNi-SWvzuWhA9lfZFInbtrbmpNIqINa1DYA",
            "gid_dashboard": 820145860
        },
        "suiyi": {
            "name": "隨意小吃",
            "store_key": "隨意小吃",
            "spreadsheet_id": "1MQAJfH8ZY1D9mtFdtoOPldXlcSdmuY9XOZRKOX0bfNw",
            "gid_dashboard": 820145860
        }
    }

    result = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stores": {}
    }

    for key, cfg in configs.items():
        print(f"\nFetching data for {cfg['name']} ({cfg['spreadsheet_id']})...")
        sh = gc.open_by_key(cfg["spreadsheet_id"])
        
        # 1. Read Dashboard (gid 820145860)
        dash_ws = sh.get_worksheet_by_id(cfg["gid_dashboard"])
        dash_rows = dash_ws.get_values()
        
        monthly_summary = {}
        for r in dash_rows[3:]: # Data starts from row 4
            if len(r) >= 4 and r[1] and re.match(r"^202[4-9]-\d{2}$", r[1].strip()):
                m = r[1].strip()
                monthly_summary[m] = {
                    "month": m,
                    "target": parse_num(r[2]) if len(r) > 2 else 0.0,
                    "target_str": r[2].strip() if len(r) > 2 else "",
                    "revenue": parse_num(r[3]) if len(r) > 3 else 0.0,
                    "revenue_str": r[3].strip() if len(r) > 3 else "",
                    "guests_count": parse_num(r[4]) if len(r) > 4 else 0,
                    "guests_str": r[4].strip() if len(r) > 4 else "",
                    "attainment_rate": r[5].strip() if len(r) > 5 else "",
                    "status": r[6].strip() if len(r) > 6 else "",
                    "avg_ticket": parse_num(r[7]) if len(r) > 7 else 0.0,
                    "avg_ticket_str": r[7].strip() if len(r) > 7 else "",
                    "issue_top1": r[8].strip() if len(r) > 8 else "",
                    "action_top1": r[9].strip() if len(r) > 9 else ""
                }
        print(f"  Loaded {len(monthly_summary)} monthly summary records.")

        # 2. Read Daily Worksheets (e.g. 2026-10, 2026-09, 2026-08...)
        daily_by_month = {}
        available_months = [ws.title for ws in sh.worksheets() if re.match(r"^202[4-9]-\d{2}$", ws.title)]
        
        for m_title in available_months:
            try:
                ws_m = sh.worksheet(m_title)
                all_vals = ws_m.get_values()
                if len(all_vals) < 3:
                    continue
                header = all_vals[1] # Row 2 is header
                rows_data = []
                for r in all_vals[2:]: # Row 3 onwards
                    if len(r) >= 5 and r[1] and re.match(r"^\d{4}-\d{2}-\d{2}$", r[1].strip()):
                        date_str = r[1].strip()
                        dow = r[2].strip() if len(r) > 2 else ""
                        weather = r[3].strip() if len(r) > 3 else ""
                        rev = parse_num(r[4]) if len(r) > 4 else 0.0
                        pend_bal = parse_num(r[5]) if len(r) > 5 else 0.0
                        cash = parse_num(r[6]) if len(r) > 6 else 0.0
                        card = parse_num(r[7]) if len(r) > 7 else 0.0
                        linepay = parse_num(r[8]) if len(r) > 8 else 0.0
                        ubereats = parse_num(r[9]) if len(r) > 9 else 0.0
                        
                        other_channel = 0.0
                        other_name = "其他"
                        if key == "suiyi":
                            foodpanda = parse_num(r[10]) if len(r) > 10 else 0.0
                            other_channel = foodpanda
                            other_name = "Foodpanda"
                        else:
                            touding = parse_num(r[10]) if len(r) > 10 else 0.0
                            other_channel = touding
                            other_name = "偷訂"

                        # Additional columns
                        inv_count = parse_num(r[13]) if len(r) > 13 else 0
                        deposit_time = r[19].strip() if len(r) > 19 else ""
                        deposit_amt = parse_num(r[20]) if len(r) > 20 else 0.0
                        depositor = r[21].strip() if len(r) > 21 else ""
                        voucher_lunch = r[28].strip() if len(r) > 28 else ""
                        voucher_dinner = r[29].strip() if len(r) > 29 else ""
                        voucher_deposit = r[30].strip() if len(r) > 30 else ""

                        rows_data.append({
                            "date": date_str,
                            "dow": dow,
                            "weather": weather,
                            "revenue": rev,
                            "pending_balance": pend_bal,
                            "cash": cash,
                            "card": card,
                            "linepay": linepay,
                            "ubereats": ubereats,
                            "other_channel": other_channel,
                            "other_name": other_name,
                            "invoice_count": int(inv_count),
                            "deposit_time": deposit_time,
                            "deposit_amt": deposit_amt,
                            "depositor": depositor,
                            "voucher_lunch": voucher_lunch,
                            "voucher_dinner": voucher_dinner,
                            "voucher_deposit": voucher_deposit
                        })
                daily_by_month[m_title] = rows_data
                print(f"  Loaded {len(rows_data)} daily records for {m_title}.")
            except Exception as e:
                print(f"  Error reading {m_title}: {e}")

        # 3. Read POS Product Sales with Cross-Entity Data Contamination Detection
        pos_products = []
        pos_products_by_month = {}
        pos_notice = ""
        cross_entity_detected = False

        try:
            ws_pos = sh.worksheet("09_POS商品銷售")
            pos_vals = ws_pos.get_values()
            if len(pos_vals) > 1:
                # Check for cross-entity duplicate template (e.g. Yuegong containing Suiyi fried rice items)
                sample_item_names = [r[1].strip() for r in pos_vals[1:10] if len(r) > 1 and r[1]]
                if key == "yuegong" and any(dish in sample_item_names for dish in ["白飯（小）", "蝦仁蛋炒飯", "青椒牛肉蛋炒飯"]):
                    cross_entity_detected = True
                    pos_notice = "⚠️ 月宮 Google 試算表「09_POS商品銷售」為隨意小吃模板複製檔，系統已自動阻斷混用並隔離，待門市補入月宮真實品項。"
                    print(f"  [SECURITY ALERT] Cross-entity data mixing detected in Yuegong: found Suiyi dishes {sample_item_names[:3]}. Blocked from Yuegong.")
                else:
                    # Parse valid items
                    for r in pos_vals[1:]:
                        if len(r) >= 8 and r[1] and r[1].strip() != "總計":
                            m_tag = r[0].strip() if len(r) > 0 and r[0] else "2026-05"
                            name = r[1].strip()
                            cat = r[2].strip() if len(r) > 2 else "一般"
                            price_str = r[6].strip() if len(r) > 6 else ""
                            qty = parse_num(r[7]) if len(r) > 7 else 0.0
                            # Parse actual price number
                            price_num = 0.0
                            if "NT$" in price_str:
                                p_clean = re.search(r"NT\$(\d+)", price_str)
                                if p_clean:
                                    price_num = float(p_clean.group(1))
                            sales_amt = parse_num(r[9]) if len(r) > 9 else (qty * price_num)
                            
                            item_obj = {
                                "month": m_tag,
                                "name": name,
                                "category": cat,
                                "price": price_str or f"NT${int(price_num)}",
                                "price_num": price_num,
                                "qty": int(qty),
                                "sales_amount": sales_amt
                            }
                            pos_products.append(item_obj)
                            if m_tag not in pos_products_by_month:
                                pos_products_by_month[m_tag] = []
                            pos_products_by_month[m_tag].append(item_obj)

                    # Compute revenue share % per month
                    for m_tag, items in pos_products_by_month.items():
                        tot_sales = sum(it["sales_amount"] for it in items) or 1.0
                        # sort by qty desc
                        items.sort(key=lambda x: x["qty"], reverse=True)
                        for it in items:
                            it["share_pct"] = round((it["sales_amount"] / tot_sales) * 100, 2)
                    
                    pos_products.sort(key=lambda x: x["qty"], reverse=True)
                    print(f"  Loaded {len(pos_products)} POS product records across {list(pos_products_by_month.keys())}.")
        except Exception as e:
            pos_notice = f"POS 資料讀取提示：{e}"

        # 4. Read Operational Improvements if exists
        improvements = []
        try:
            ws_imp = sh.worksheet("04_改善追蹤")
            imp_vals = ws_imp.get_values()
            if len(imp_vals) > 1:
                for r in imp_vals[1:]:
                    if len(r) >= 3 and r[0] and (r[1] or (len(r) > 2 and r[2])):
                        improvements.append({
                            "month": r[0].strip(),
                            "issue_top1": r[1].strip() if len(r) > 1 else "",
                            "action_top1": r[2].strip() if len(r) > 2 else "",
                            "issue_top2": r[3].strip() if len(r) > 3 else "",
                            "action_top2": r[4].strip() if len(r) > 4 else ""
                        })
                print(f"  Loaded {len(improvements)} operational improvement records.")
        except Exception:
            pass

        # 5. Run Smart Cash Deposit Auto-Attribution Engine across all months
        cash_tracking_by_month = run_smart_cash_attribution(key, cfg["name"], daily_by_month)

        result["stores"][key] = {
            "name": cfg["name"],
            "spreadsheet_id": cfg["spreadsheet_id"],
            "monthly_summary": monthly_summary,
            "daily_by_month": daily_by_month,
            "cash_tracking_by_month": cash_tracking_by_month,
            "pos_products": pos_products,
            "pos_products_by_month": pos_products_by_month,
            "pos_notice": pos_notice,
            "cross_entity_detected": cross_entity_detected,
            "improvements": improvements
        }

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n✅ All Google Sheets data successfully synced to: {OUTPUT_JSON}")
    print(f"Total file size: {os.path.getsize(OUTPUT_JSON)} bytes")
    return result


def parse_date_safe(d_str):
    if not d_str:
        return None
    s = str(d_str).strip().split(" ")[0].replace("/", "-")
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
        except Exception:
            return None
    return None


def run_smart_cash_attribution(store_key, store_name, daily_by_month):
    # Load existing bank passbook cleared status from dashboard_data.json if available
    dash_path = os.path.join(BASE_DIR, "dashboard_data.json")
    bank_cleared_lookup = {}
    if os.path.exists(dash_path):
        try:
            with open(dash_path, "r", encoding="utf-8") as f:
                d_json = json.load(f)
            st_label = "月宮" if store_key == "yuegong" else "隨意小吃"
            for m_k, m_val in d_json.items():
                if isinstance(m_val, dict) and "stores" in m_val:
                    st_obj = m_val["stores"].get(st_label, {})
                    for dc in st_obj.get("daily_cash", []):
                        if dc.get("bank_cleared"):
                            bank_cleared_lookup[dc["date"]] = {
                                "bank_cleared": True,
                                "bank_date": dc.get("bank_date") or "",
                                "bank_amt": float(dc.get("bank_amt") or 0.0)
                            }
        except Exception:
            pass

    months_chrono = sorted(daily_by_month.keys())
    prev_unpaid_days = []
    today_dt = datetime.now().date()

    for m in months_chrono:
        rows = daily_by_month[m]
        for r in rows:
            r["attr_status"] = "NO_CASH" if r.get("cash", 0) <= 0 else "UNPAID"
            r["attr_note"] = ""
            r["attr_deposit_date"] = ""
            r["attr_deposit_amt"] = 0.0
            r["attr_depositor"] = ""
            r["attr_period"] = ""
            r["attr_days_count"] = 0
            r["attr_group_cash_sum"] = r.get("cash", 0.0)
            r["attr_short_over"] = 0.0
            r["attr_lag_days"] = 0
            r["dep_target_period"] = ""
            r["dep_target_days"] = []
            r["dep_target_sum"] = 0.0
            r["dep_short_over"] = 0.0
            r["dep_is_cross_month"] = False

            b_info = bank_cleared_lookup.get(r["date"])
            r["bank_cleared"] = bool(b_info and b_info.get("bank_cleared"))
            r["bank_date"] = b_info.get("bank_date", "") if b_info else ""
            r["bank_amt"] = b_info.get("bank_amt", 0.0) if b_info else 0.0

        cash_days = [r for r in rows if r.get("cash", 0) > 0]
        dep_rows = [r for r in rows if r.get("deposit_amt", 0) > 0]

        decls = []
        for r in dep_rows:
            raw_t = (r.get("deposit_time") or r["date"]).strip()
            norm_t = raw_t.replace("/", "-")
            decls.append({
                "row": r,
                "row_date": r["date"],
                "dep_time": norm_t,
                "dep_time_raw": raw_t,
                "dep_amt": float(r["deposit_amt"]),
                "who": (r.get("depositor") or "").strip() or "門市人員",
                "used": False
            })

        # Pass 0: Cross-month tail deposit matching (e.g. 10/01 deposit matching 09/30 cash)
        if prev_unpaid_days:
            for decl in decls[:3]:
                if decl["used"]:
                    continue
                for w in range(1, len(prev_unpaid_days) + 1):
                    chunk = prev_unpaid_days[-w:]
                    if any(c.get("attr_status") != "UNPAID" for c in chunk):
                        continue
                    c_sum = sum(c["cash"] for c in chunk)
                    if abs(c_sum - decl["dep_amt"]) <= 15.0:
                        decl["used"] = True
                        p_short = chunk[0]["date"][5:] if len(chunk) == 1 else f"{chunk[0]['date'][5:]}~{chunk[-1]['date'][5:]}"
                        p_full = chunk[0]["date"] if len(chunk) == 1 else f"{chunk[0]['date']}~{chunk[-1]['date']}"
                        so = round(decl["dep_amt"] - c_sum, 0)
                        decl["row"]["dep_target_period"] = f"跨月沖銷 {p_short} ({len(chunk)}日現金)"
                        decl["row"]["dep_target_days"] = [c["date"] for c in chunk]
                        decl["row"]["dep_target_sum"] = c_sum
                        decl["row"]["dep_short_over"] = so
                        decl["row"]["dep_is_cross_month"] = True
                        for c in chunk:
                            c["attr_status"] = "MATCHED" if c.get("bank_cleared") else "DECLARED_PENDING_BANK"
                            c["attr_deposit_date"] = decl["dep_time_raw"]
                            c["attr_deposit_amt"] = decl["dep_amt"]
                            c["attr_depositor"] = decl["who"]
                            c["attr_period"] = p_full
                            c["attr_days_count"] = len(chunk)
                            c["attr_group_cash_sum"] = c_sum
                            c["attr_short_over"] = so
                            c["attr_note"] = f"跨月於 {decl['dep_time_raw']} 存入 ${decl['dep_amt']:,.0f} ({p_short} · {decl['who']})"
                        break

        # Pass 1: Single-day exact match (tol <= 1.0)
        for c in cash_days:
            if c["attr_status"] != "UNPAID":
                continue
            for decl in decls:
                if not decl["used"] and decl["row_date"] >= c["date"] and abs(decl["dep_amt"] - c["cash"]) <= 1.0:
                    decl["used"] = True
                    so = round(decl["dep_amt"] - c["cash"], 0)
                    c["attr_status"] = "MATCHED" if c.get("bank_cleared") else "DECLARED_PENDING_BANK"
                    c["attr_deposit_date"] = decl["dep_time_raw"]
                    c["attr_deposit_amt"] = decl["dep_amt"]
                    c["attr_depositor"] = decl["who"]
                    c["attr_period"] = c["date"]
                    c["attr_days_count"] = 1
                    c["attr_group_cash_sum"] = c["cash"]
                    c["attr_short_over"] = so
                    c["attr_note"] = f"申報於 {decl['dep_time_raw']} 存入 ${decl['dep_amt']:,.0f} ({c['date'][5:]}單日 · {decl['who']})"
                    decl["row"]["dep_target_period"] = f"沖銷 {c['date'][5:]} (單日)"
                    decl["row"]["dep_target_days"] = [c["date"]]
                    decl["row"]["dep_target_sum"] = c["cash"]
                    decl["row"]["dep_short_over"] = so
                    break

        # Pass 2A: Multi-day cumulative exact match (tol <= 10.0)
        for decl in decls:
            if decl["used"]:
                continue
            for w in range(2, 10):
                for i in range(len(cash_days) - w + 1):
                    chunk = cash_days[i:i+w]
                    if any(c["attr_status"] != "UNPAID" for c in chunk):
                        continue
                    if decl["row_date"] < chunk[-1]["date"]:
                        continue
                    c_sum = sum(c["cash"] for c in chunk)
                    if abs(c_sum - decl["dep_amt"]) <= 10.0:
                        decl["used"] = True
                        p_short = f"{chunk[0]['date'][5:]}~{chunk[-1]['date'][5:]}"
                        p_full = f"{chunk[0]['date']}~{chunk[-1]['date']}"
                        so = round(decl["dep_amt"] - c_sum, 0)
                        decl["row"]["dep_target_period"] = f"沖銷 {p_short} ({len(chunk)}日累計)"
                        decl["row"]["dep_target_days"] = [c["date"] for c in chunk]
                        decl["row"]["dep_target_sum"] = c_sum
                        decl["row"]["dep_short_over"] = so
                        for c in chunk:
                            c["attr_status"] = "MATCHED" if c.get("bank_cleared") else "DECLARED_PENDING_BANK"
                            c["attr_deposit_date"] = decl["dep_time_raw"]
                            c["attr_deposit_amt"] = decl["dep_amt"]
                            c["attr_depositor"] = decl["who"]
                            c["attr_period"] = p_full
                            c["attr_days_count"] = len(chunk)
                            c["attr_group_cash_sum"] = c_sum
                            c["attr_short_over"] = so
                            c["attr_note"] = f"申報於 {decl['dep_time_raw']} 存入 ${decl['dep_amt']:,.0f} ({p_short} 共{len(chunk)}日累計 · {decl['who']})"
                        break
                if decl["used"]:
                    break

        # Pass 2B: Split-batch 2 consecutive deposits matching a contiguous block of days (tol <= 10.0)
        unused_decls = [x for x in decls if not x["used"]]
        for j in range(len(unused_decls) - 1):
            d1, d2 = unused_decls[j], unused_decls[j+1]
            if d1["used"] or d2["used"]:
                continue
            comb_amt = d1["dep_amt"] + d2["dep_amt"]
            for w in range(2, 10):
                for i in range(len(cash_days) - w + 1):
                    chunk = cash_days[i:i+w]
                    if any(c["attr_status"] != "UNPAID" for c in chunk):
                        continue
                    c_sum = sum(c["cash"] for c in chunk)
                    if abs(c_sum - comb_amt) <= 10.0:
                        d1["used"] = d2["used"] = True
                        p_short = f"{chunk[0]['date'][5:]}~{chunk[-1]['date'][5:]}"
                        p_full = f"{chunk[0]['date']}~{chunk[-1]['date']}"
                        so = round(comb_amt - c_sum, 0)
                        for d_item in (d1, d2):
                            d_item["row"]["dep_target_period"] = f"分批沖銷 {p_short} ({len(chunk)}日合存 ${comb_amt:,.0f})"
                            d_item["row"]["dep_target_days"] = [c["date"] for c in chunk]
                            d_item["row"]["dep_target_sum"] = c_sum
                            d_item["row"]["dep_short_over"] = so
                        for c in chunk:
                            c["attr_status"] = "MATCHED" if c.get("bank_cleared") else "DECLARED_PENDING_BANK"
                            c["attr_deposit_date"] = f"{d1['dep_time_raw']} & {d2['dep_time_raw']}"
                            c["attr_deposit_amt"] = comb_amt
                            c["attr_depositor"] = d1["who"]
                            c["attr_period"] = p_full
                            c["attr_days_count"] = len(chunk)
                            c["attr_group_cash_sum"] = c_sum
                            c["attr_short_over"] = so
                            c["attr_note"] = f"分批於 {d1['dep_time_raw']}(${d1['dep_amt']:,.0f})＋{d2['dep_time_raw']}(${d2['dep_amt']:,.0f}) 合存 ${comb_amt:,.0f} ({p_short} 共{len(chunk)}日累計 · {d1['who']})"
                        break
                if d1["used"]:
                    break

        # Pass 2C: Multi-day tolerance match for short/over (tol <= 1500.0)
        for decl in decls:
            if decl["used"]:
                continue
            for w in range(2, 10):
                for i in range(len(cash_days) - w + 1):
                    chunk = cash_days[i:i+w]
                    if any(c["attr_status"] != "UNPAID" for c in chunk):
                        continue
                    if decl["row_date"] < chunk[-1]["date"]:
                        continue
                    c_sum = sum(c["cash"] for c in chunk)
                    if abs(c_sum - decl["dep_amt"]) <= 1500.0:
                        decl["used"] = True
                        p_short = f"{chunk[0]['date'][5:]}~{chunk[-1]['date'][5:]}"
                        p_full = f"{chunk[0]['date']}~{chunk[-1]['date']}"
                        so = round(decl["dep_amt"] - c_sum, 0)
                        decl["row"]["dep_target_period"] = f"沖銷 {p_short} ({len(chunk)}日累計)"
                        decl["row"]["dep_target_days"] = [c["date"] for c in chunk]
                        decl["row"]["dep_target_sum"] = c_sum
                        decl["row"]["dep_short_over"] = so
                        for c in chunk:
                            c["attr_status"] = "MATCHED" if c.get("bank_cleared") else "DECLARED_PENDING_BANK"
                            c["attr_deposit_date"] = decl["dep_time_raw"]
                            c["attr_deposit_amt"] = decl["dep_amt"]
                            c["attr_depositor"] = decl["who"]
                            c["attr_period"] = p_full
                            c["attr_days_count"] = len(chunk)
                            c["attr_group_cash_sum"] = c_sum
                            c["attr_short_over"] = so
                            c["attr_note"] = f"申報於 {decl['dep_time_raw']} 存入 ${decl['dep_amt']:,.0f} ({p_short} 共{len(chunk)}日累計 · {decl['who']})"
                        break
                if decl["used"]:
                    break

        prev_unpaid_days = [c for c in cash_days if c["attr_status"] == "UNPAID"]

    # Build monthly cash tracking summary & items for frontend display
    tracking_by_month = {}
    for m in months_chrono:
        rows = daily_by_month[m]
        cash_days = [r for r in rows if r.get("cash", 0) > 0]
        items = []
        tot_pos_cash = 0.0
        tot_decl_cash = 0.0
        tot_bank_cleared = 0.0
        tot_decl_pending_bank = 0.0
        tot_unpaid_cash = 0.0
        seen_periods_so = {}

        for r in cash_days:
            c_amt = float(r["cash"])
            tot_pos_cash += c_amt
            op_dt = parse_date_safe(r["date"])
            dep_first_str = (r.get("attr_deposit_date") or "").split("&")[0].strip()
            dep_dt = parse_date_safe(dep_first_str)

            st = r.get("attr_status", "UNPAID")
            so = float(r.get("attr_short_over") or 0.0)
            g_sum = float(r.get("attr_group_cash_sum") or c_amt)
            p_key = r.get("attr_period") or r["date"]

            if st in ("MATCHED", "DECLARED_PENDING_BANK"):
                tot_decl_cash += c_amt
                seen_periods_so[p_key] = so
                lag = max(0, (dep_dt - op_dt).days) if (op_dt and dep_dt) else 1
                r["attr_lag_days"] = lag
                if r.get("bank_cleared"):
                    tot_bank_cleared += c_amt
                    b_dt_s = r.get("bank_date") or dep_first_str
                    bank_info = f"🟢 玉山存摺已入帳 ({b_dt_s})"
                else:
                    tot_decl_pending_bank += c_amt
                    bank_info = f"⏳ 已於 {r.get('attr_deposit_date')} 申報存入・待存摺批次更新"

                declared_info = f"✅ {r.get('attr_note')}"
                if lag <= 2:
                    timeliness_label = f"🟢 準時提存 (隔 {lag} 天)"
                    is_overdue = False
                else:
                    timeliness_label = f"🔵 已彙總提存 ({dep_first_str[5:]} 存入 · 隔 {lag} 天)"
                    is_overdue = False

                if so < -1.0:
                    so_note = f"⚠️ 短少 -${abs(so):,.0f} (應收合 ${g_sum:,.0f})"
                    rec = f"【注意短溢：{so_note}】總表已於 {r.get('attr_deposit_date')} 登記提存 (${r.get('attr_deposit_amt'):,.0f})，待補短少差額或核對存摺"
                elif so > 1.0:
                    so_note = f"➕ 溢存 +${so:,.0f} (應收合 ${g_sum:,.0f})"
                    rec = f"總表已於 {r.get('attr_deposit_date')} 登記提存 (${r.get('attr_deposit_amt'):,.0f}，{so_note})，帳務已自動歸屬"
                else:
                    so_note = "✓ 吻合 ($0)"
                    rec = f"已由智慧引擎自動對碰歸屬 ({r.get('attr_note')})，金額 100% 吻合" if not r.get("bank_cleared") else f"總表申報與玉山存摺雙層核銷完成 ({r.get('attr_note')})"
            else:
                tot_unpaid_cash += c_amt
                lag = max(0, (today_dt - op_dt).days) if op_dt else 0
                r["attr_lag_days"] = lag
                declared_info = "❌ 總表U欄空白未填"
                bank_info = "❌ 存摺未見入帳"
                so_note = "⏳ 待存入"
                if lag > 2:
                    timeliness_label = f"🔴 逾期未存 ({lag} 天)"
                    is_overdue = True
                    rec = f"門市超過 {lag} 天尚未於總表 U 欄申報提存，請店長確認現金繳庫"
                else:
                    timeliness_label = f"🟡 待交班提存 ({lag} 天)"
                    is_overdue = False
                    rec = "門市近期營業現金，待下一批交班彙總存入玉山銀行"

            items.append({
                "store": store_name,
                "date": r["date"],
                "dow": r.get("dow", ""),
                "cash": c_amt,
                "audit_status": st,
                "declared_info": declared_info,
                "bank_info": bank_info,
                "short_over": so,
                "short_over_note": so_note,
                "delay_days": r["attr_lag_days"],
                "is_overdue": is_overdue,
                "timeliness_label": timeliness_label,
                "recommendation": rec,
                "depositor": r.get("attr_depositor", ""),
                "deposit_date": r.get("attr_deposit_date", ""),
                "period": p_key
            })

        net_so = sum(seen_periods_so.values())
        cross_in = sum(r.get("deposit_amt", 0.0) for r in rows if r.get("dep_is_cross_month"))
        tracking_by_month[m] = {
            "summary": {
                "total_pos_cash": tot_pos_cash,
                "total_declared_cash": tot_decl_cash,
                "total_bank_cleared": tot_bank_cleared,
                "total_declared_pending_bank": tot_decl_pending_bank,
                "total_truly_unpaid_cash": tot_unpaid_cash,
                "net_short_over": net_so,
                "cross_month_in_amt": cross_in,
                "operating_days_count": len(cash_days),
                "declared_days_count": sum(1 for x in items if x["audit_status"] != "UNPAID"),
                "unpaid_days_count": sum(1 for x in items if x["audit_status"] == "UNPAID")
            },
            "items": items
        }

    return tracking_by_month


if __name__ == "__main__":
    sync_sheets()

