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

        # 3. Read POS Product Sales if exists
        pos_products = []
        try:
            ws_pos = sh.worksheet("09_POS商品銷售")
            pos_vals = ws_pos.get_values()
            if len(pos_vals) > 1:
                for r in pos_vals[1:60]:
                    if len(r) >= 8 and r[1]:
                        pos_products.append({
                            "month": r[0].strip(),
                            "name": r[1].strip(),
                            "category": r[2].strip() if len(r) > 2 else "",
                            "price": r[6].strip() if len(r) > 6 else "",
                            "qty": parse_num(r[7]) if len(r) > 7 else 0
                        })
                print(f"  Loaded {len(pos_products)} POS product records.")
        except Exception:
            pass

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

        result["stores"][key] = {
            "name": cfg["name"],
            "spreadsheet_id": cfg["spreadsheet_id"],
            "monthly_summary": monthly_summary,
            "daily_by_month": daily_by_month,
            "pos_products": pos_products,
            "improvements": improvements
        }

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n✅ All Google Sheets data successfully synced to: {OUTPUT_JSON}")
    print(f"Total file size: {os.path.getsize(OUTPUT_JSON)} bytes")
    return result

if __name__ == "__main__":
    sync_sheets()
