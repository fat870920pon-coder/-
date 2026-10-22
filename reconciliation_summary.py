import os
import sys
import re
import json
import argparse
from datetime import datetime, date
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

def calculate_monthly_summary(target_month=None, store_cash_data=None):
    config = load_config()
    stores_cfg = config.get("stores", {})
    target_month = target_month or "2026-10"
    m_clean = target_month.replace("-", "")

    gc = get_gspread_client()
    if not gc:
        print("❌ 無法連線至 Google 試算表（缺少 credentials.json）")
        return None

    # 1. 讀取 Google Sheets 上的各通路 POS 營收累計
    pos_data = {}
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
    cleared_voucher_cash_dates = set()
    if os.path.exists(bank_file):
        try:
            wb_check = openpyxl.load_workbook(bank_file, data_only=True)
            ws_chk = wb_check.active
            for r in range(3, ws_chk.max_row + 1):
                code = str(ws_chk.cell(r, 7).value or "")
                dc = ws_chk.cell(r, 6).value
                rem = str(ws_chk.cell(r, 16).value or "")
                if dc == 2 and "1130-01" in code:
                    # 解析沖銷了哪些日期 (支援跨月與任意月份格式)
                    m_p = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", rem)
                    for d_found in m_p:
                        cleared_voucher_cash_dates.add(d_found)
        except Exception:
            pass

    # 讀取次月銀行傳票 (若存在，以標記跨月已存入款項，例如 9/30 於 10/01 或 10/02 存入)
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
                code = str(ws_next.cell(r, 7).value or "")
                dc = ws_next.cell(r, 6).value
                rem = str(ws_next.cell(r, 16).value or "")
                if dc == 2 and "1130-01" in code:
                    m_p = re.findall(rf"\b{target_month}-\d{{2}}\b", rem)
                    for d_found in m_p:
                        cleared_voucher_cash_dates.add(d_found)
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

            # 整理每日現金與存入狀態
            cash_idx = headers.index("現金") if "現金" in headers else None
            dep_time_idx = headers.index("存款時間") if "存款時間" in headers else None
            dep_amt_idx = headers.index("存款金額") if "存款金額" in headers else None

            # 若外部有傳入精確對帳結果，優先使用
            passed_cash = store_cash_data.get(store_key, []) if store_cash_data else []
            passed_map = {c["date"]: c for c in passed_cash if "date" in c}

            daily_list = []
            for r in rows[2:]:
                if len(r) > 1 and r[1] and any(c.isdigit() for c in r[1]) and "小計" not in r[1]:
                    d_str = r[1].strip()
                    c_amt = parse_num(r[cash_idx]) if cash_idx and len(r) > cash_idx else 0.0
                    if d_str in passed_map:
                        daily_list.append(passed_map[d_str])
                    else:
                        d_time = str(r[dep_time_idx]).strip() if dep_time_idx and len(r) > dep_time_idx else ""
                        d_amt = parse_num(r[dep_amt_idx]) if dep_amt_idx and len(r) > dep_amt_idx else 0.0
                        is_used = bool(d_time or d_amt > 0 or d_str in cleared_voucher_cash_dates)
                        daily_list.append({
                            "date": d_str,
                            "cash": c_amt,
                            "used": is_used,
                            "deposit_date": d_time,
                            "deposit_amount": d_amt if d_amt > 0 else c_amt,
                            "period": d_str
                        })
            pos_data[store_key]["daily_cash"] = daily_list

            # 產生門市未存款與逾期穿透警示
            alerts = []
            today_ref = date(2026, 10, 22)  # 基準參考日 (當前對帳日)
            for item in daily_list:
                if not item.get("used", False) and item.get("cash", 0.0) > 0:
                    try:
                        s_dt = datetime.strptime(item["date"], "%Y-%m-%d").date()
                        delay = max(0, (today_ref - s_dt).days)
                    except:
                        delay = 0

                    if delay >= 5:
                        risk = "🚨 嚴重逾期 (超過 5 天)"
                        rec = "請向店長核實存款收據與銀行入帳進度"
                    elif delay >= 3:
                        risk = "⚠️ 一般逾期 (3~5 天)"
                        rec = "請提醒門市今日內存入銀行"
                    else:
                        risk = "⏳ 近期交班 (1~2 天)"
                        rec = "門市正常交班與銀行處理中"

                    alerts.append({
                        "store": s_name,
                        "date": item["date"],
                        "cash": item["cash"],
                        "delay_days": delay,
                        "risk": risk,
                        "recommendation": rec
                    })
            pos_data[store_key]["pending_alerts"] = alerts

        except Exception as e:
            print(f"⚠️ 讀取【{s_name}】營收試算表失敗: {e}")

    # 2. 讀取已產出的玉山銀行沖銷傳票累計
    if os.path.exists(bank_file):
        try:
            wb = openpyxl.load_workbook(bank_file, data_only=True)
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
                                sdata["channels"][code]["bank_cleared"] += amt
        except Exception as e:
            print(f"⚠️ 讀取玉山銀行沖銷傳票檔失敗: {e}")

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

    # 4. 計算差額與狀態說明
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
                    cdata["status"] = f"ℹ️ 銀行入帳包含上月結轉現金存款"
            elif code == "1130-02":
                if diff > 0:
                    cdata["status"] = f"⏳ 連加網路正常撥款作業中（約待撥 ${diff:,.0f}）"
                else:
                    cdata["status"] = "ℹ️ 撥款包含上月底營收"
            elif code == "1130-03":
                if diff > 0:
                    cdata["status"] = f"⏳ Uber Eats 包含平台手續費(待發票沖減)及在途款"
                else:
                    cdata["status"] = "ℹ️ 撥款包含上月底外送款"
            elif code == "1130-04":
                if diff > 0:
                    cdata["status"] = f"⏳ Foodpanda 外送款結算撥款中"
                else:
                    cdata["status"] = "ℹ️ 撥款包含上月底款項"
            else:
                cdata["status"] = "結算中" if diff > 0 else "已沖清"

    # 5. 持久化儲存 (自動記憶至 local JSON)
    memory_data = {}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                memory_data = json.load(f)
        except:
            memory_data = {}

    memory_data[target_month] = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stores": pos_data
    }

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(memory_data, f, ensure_ascii=False, indent=2)

    return pos_data

def print_and_export_summary(pos_data, target_month="2026-10"):
    m_clean = target_month.replace("-", "")
    print("\n" + "="*85)
    print(f"📊 【月下餐飲集團 - {target_month} 對帳差額與應收餘額記憶表】")
    print(f"🕒 結算記憶時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*85)

    excel_rows = [
        ["事業體", "會計項目代號", "應收通路", "POS 累計營收", "玉山銀行已入帳", "手續費發票已沖", "目前未收差額", "對帳狀態與說明"]
    ]

    for skey, sdata in pos_data.items():
        s_name = sdata["name"]
        print(f"\n🏢 事業體 ➔ 【{s_name}】")
        print(f"{'通路名稱':<16} {'POS營收累計':>12} {'銀行已沖銷':>12} {'發票已沖銷':>10} {'目前未清差額':>12}  狀態說明")
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
        for a in sdata.get("pending_alerts", []):
            all_alerts.append(a)

    all_alerts.sort(key=lambda x: x.get("delay_days", 0), reverse=True)

    print("\n" + "="*85)
    print("🚨 【門市現金未存入／異常追蹤預警清單】(依延遲天數排序)")
    print("="*85)
    if all_alerts:
        print(f"{'事業體':<14} {'營業日期':<12} {'當日現金營收':>12} {'已延遲':>8} {'風險等級':<18} 建議處置措施")
        print("-" * 85)
        for a in all_alerts:
            print(f"{a['store']:<12} {a['date']:<12} ${a['cash']:>10,.0f} {a['delay_days']:>6} 天  {a['risk']:<16} {a['recommendation']}")
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

    for col in ws.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = openpyxl.utils.get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(max_len * 1.5, 12)

    # 產出分頁 2: 玉山銀行單筆沖銷明細對照表
    bank_file = os.path.join(OUTPUT_DIR, f"文中傳票維護匯入_全部合併_玉山銀行沖銷_{m_clean}.xlsx")
    if os.path.exists(bank_file):
        try:
            wb_b = openpyxl.load_workbook(bank_file, data_only=True)
            ws_b = wb_b.active
            ws2 = wb.create_sheet(title="玉山單筆沖銷對照")

            detail_header = [
                "序號", "事業體", "銀行入帳日", "交易項目類別", "銀行入帳金額", "沖銷金額", "👉 所屬營收/款項日(哪幾日)", "傳票摘要"
            ]
            ws2.append(detail_header)

            # 解析傳票檔案中的每一筆完整沖銷交易（以借方 1102 銀行存款為單筆基準）
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

                # 組合精確備註：清楚說明銀行實收、沖銷應收與手續費或短溢調節
                final_rem = rem
                if v["fee"] > 0:
                    final_rem = f"{rem} (銀行實收 ${v['bank_amt']:,.0f}，內含平台手續費/稅 ${v['fee']:,.0f})"
                elif abs(v["short_over"]) > 0:
                    so_sign = "+" if v["short_over"] > 0 else "-"
                    final_rem = f"{rem} (銀行實收 ${v['bank_amt']:,.0f}，短溢調節 {so_sign}${abs(v['short_over']):,.0f})"

                # 欄位：序號, 事業體, 銀行入帳日, 交易項目類別, 銀行入帳金額, 沖銷金額, 所屬營收/款項日, 傳票摘要
                ws2.append([
                    cur_idx, v["dept"], v["date"], item_type, v["bank_amt"], v["cleared_amt"] or v["bank_amt"], matched_period, final_rem
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
                    cat_cell.fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid") # 暖金黃色
                    cat_cell.font = Font(bold=True, color="7F6000")
                elif "LINE" in cat_val:
                    cat_cell.fill = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid") # LINE 綠色
                    cat_cell.font = Font(bold=True, color="276A3C")
                elif "Uber" in cat_val:
                    cat_cell.fill = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid") # 沉穩灰黑
                    cat_cell.font = Font(bold=True, color="333333")
                elif "Foodpanda" in cat_val:
                    cat_cell.fill = PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid") # 熊貓粉紅
                    cat_cell.font = Font(bold=True, color="C00000")
                elif "偷訂" in cat_val:
                    cat_cell.fill = PatternFill(start_color="E8D7F1", end_color="E8D7F1", fill_type="solid") # 偷訂紫色
                    cat_cell.font = Font(bold=True, color="7030A0")

                ws2.cell(row=r_idx, column=5).number_format = "#,##0"
                ws2.cell(row=r_idx, column=6).number_format = "#,##0"
                target_cell = ws2.cell(row=r_idx, column=7)
                target_cell.font = Font(bold=True, color="8B0000")
                target_cell.fill = PatternFill(start_color="EDF2F8", end_color="EDF2F8", fill_type="solid")
                target_cell.alignment = Alignment(horizontal="center")

            for col in ws2.columns:
                max_len = max(len(str(cell.value or '')) for cell in col)
                col_letter = openpyxl.utils.get_column_letter(col[0].column)
                ws2.column_dimensions[col_letter].width = max(max_len * 1.5, 12)
        except Exception as e:
            print(f"⚠️ 產出單筆沖銷明細分頁失敗: {e}")

    # 產出分頁 3: 門市未存款與異常追蹤清單
    if all_alerts:
        ws3 = wb.create_sheet(title="門市未存款與異常追蹤")
        alert_header = ["序號", "事業體", "營業日期", "當日現金營收", "已延遲天數", "風險警示等級", "建議處置措施"]
        ws3.append(alert_header)

        for idx, a in enumerate(all_alerts, start=1):
            ws3.append([
                idx, a["store"], a["date"], a["cash"], f"{a['delay_days']} 天", a["risk"], a["recommendation"]
            ])

        for c_idx in range(1, len(alert_header) + 1):
            cell = ws3.cell(row=1, column=c_idx)
            cell.font = Font(bold=True, color="FFFFFF", size=11)
            cell.fill = PatternFill(start_color="C00000", end_color="C00000", fill_type="solid")
            cell.alignment = Alignment(horizontal="center", vertical="center")

        for r_idx in range(2, ws3.max_row + 1):
            ws3.cell(row=r_idx, column=1).alignment = Alignment(horizontal="center")
            ws3.cell(row=r_idx, column=3).alignment = Alignment(horizontal="center")
            ws3.cell(row=r_idx, column=4).number_format = "#,##0"
            ws3.cell(row=r_idx, column=5).alignment = Alignment(horizontal="center")
            
            # 依延遲程度標記顏色
            delay_cell = ws3.cell(row=r_idx, column=5)
            risk_cell = ws3.cell(row=r_idx, column=6)
            txt = str(risk_cell.value or "")
            if "嚴重逾期" in txt:
                risk_cell.font = Font(bold=True, color="9C0006")
                risk_cell.fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
            elif "一般逾期" in txt:
                risk_cell.font = Font(bold=True, color="9C6500")
                risk_cell.fill = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
            else:
                risk_cell.font = Font(color="006100")
                risk_cell.fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")

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
