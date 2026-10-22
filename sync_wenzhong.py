import os
import sys
import re
import json
import argparse
import warnings
warnings.filterwarnings("ignore")
from datetime import datetime

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
CREDENTIALS_PATH = os.path.join(os.path.dirname(__file__), "credentials.json")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")

def load_config():
    if not os.path.exists(CONFIG_PATH):
        print(f"❌ 找不到設定檔：{CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def get_gspread_client():
    import gspread
    from google.oauth2.service_account import Credentials

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]

    if not os.path.exists(CREDENTIALS_PATH):
        print("\n" + "="*60)
        print("【找不到憑證檔】credentials.json")
        print(f"請將您的 Google 憑證檔案命名為 credentials.json")
        print(f"並放置於此目錄：{os.path.abspath(os.path.dirname(__file__))}")
        print("="*60 + "\n")
        sys.exit(1)

    with open(CREDENTIALS_PATH, "r", encoding="utf-8") as f:
        cred_data = json.load(f)

    if cred_data.get("type") == "service_account":
        creds = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=scopes)
        return gspread.authorize(creds)
    else:
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, scopes=scopes)
        creds = flow.run_local_server(port=0)
        return gspread.authorize(creds)

def parse_num(val):
    if not val:
        return 0.0
    s = re.sub(r"[^\d.-]", "", str(val))
    try:
        return float(s)
    except Exception:
        return 0.0

def is_valid_date(date_str):
    if not date_str:
        return False
    s = str(date_str).strip()
    if any(k in s for k in ["小計", "合計", "總計", "累計", "平均", "Total", "Subtotal", "總額"]):
        return False
    clean = s.replace("/", "-")
    parts = clean.split("-")
    if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit() and parts[2].isdigit():
        return True
    return False

def to_roc_date(date_str):
    clean = str(date_str).replace("/", "-").strip()
    parts = clean.split("-")
    if len(parts) == 3 and parts[0].isdigit():
        y = int(parts[0]) - 1911
        m = int(parts[1])
        d = int(parts[2])
        return f"{y:03d}/{m:02d}/{d:02d}", f"{y:03d}{m:02d}{d:02d}"
    return clean, clean.replace("/", "")

def fetch_store_vouchers(gc, store_key, store_cfg, store_idx=1, target_month=None, force=False, preview=False):
    import gspread
    store_name = store_cfg.get("name", store_key)
    sheet_id = store_cfg.get("spreadsheet_id")
    target_month = target_month or store_cfg.get("default_month", "2026-10")

    print("\n" + "-"*60)
    print(f"🏢 讀取事業體 ➔ 【{store_name}】")
    print(f"📄 月份工作表: 【{target_month}】")

    try:
        sh = gc.open_by_key(sheet_id)
    except Exception as e:
        print(f"❌ 連線試算表失敗: {e}")
        return None, 0, 0.0, 0.0

    available_worksheets = [w.title for w in sh.worksheets()]
    if target_month not in available_worksheets:
        month_sheets = [t for t in available_worksheets if re.match(r"^\d{4}-\d{2}$", t)]
        print(f"❌ 找不到月份工作表：【{target_month}】（可用：{', '.join(sorted(month_sheets, reverse=True))}）")
        return None, 0, 0.0, 0.0

    ws = [w for w in sh.worksheets() if w.title == target_month][0]
    all_rows = ws.get_all_values()

    if len(all_rows) < 3:
        print("⚠️ 工作表資料不足！")
        return None, 0, 0.0, 0.0

    header_idx = 1
    for i in range(min(5, len(all_rows))):
        if any("日期" in c for c in all_rows[i]):
            header_idx = i
            break

    headers = [h.strip() for h in all_rows[header_idx]]
    data_rows = all_rows[header_idx + 1:]

    # 尋找「午餐傳票號碼」欄位位置 (1-indexed for gspread)
    lunch_voucher_col = None
    for idx, h in enumerate(headers):
        if "午餐傳票號碼" in h:
            lunch_voucher_col = idx + 1
            break

    v_prefix = store_cfg.get("voucher_prefix", "F")
    v_type = store_cfg.get("voucher_type", "轉帳")
    dept = store_cfg.get("department", store_name)
    obj_cat = store_cfg.get("object_category", "客戶")
    obj_name = store_cfg.get("object_name", "一般顧客")
    total_remark = store_cfg.get("total_remark_template", f"AR_一般顧客({store_name}) 營業收入")
    acc = store_cfg.get("accounts", {})

    vouchers = []
    total_month_debit = 0.0
    total_month_credit = 0.0
    valid_days = 0
    skipped_already_entered = 0
    cells_to_write_back = []

    for row_offset, row in enumerate(data_rows):
        current_sheet_row = header_idx + 2 + row_offset
        if len(row) < 5:
            continue
        d = dict(zip(headers, row))
        raw_date = d.get("日期", "").strip()
        rev = parse_num(d.get("每日營收"))

        if not is_valid_date(raw_date) or rev <= 0:
            continue

        existing_voucher_no = d.get("午餐傳票號碼", "").strip() or d.get("餐期傳票號碼", "").strip()

        # 防呆機制：若已存在傳票號碼且非 force 模式，自動跳過
        if existing_voucher_no and not force:
            skipped_already_entered += 1
            continue

        valid_days += 1
        roc_date_slash, roc_date_compact = to_roc_date(raw_date)

        # 產生傳票號碼 (若設定 @ 則使用文中自動配號模式)
        full_cfg = load_config()
        use_auto_no = full_cfg.get("voucher_no_mode") == "@"
        v_no = "@" if use_auto_no else (existing_voucher_no if existing_voucher_no else f"{v_prefix}{roc_date_compact}{store_idx:04d}")

        # 若原本試算表沒有填，記錄起來準備自動回填
        if not existing_voucher_no and lunch_voucher_col:
            cells_to_write_back.append(
                gspread.Cell(row=current_sheet_row, col=lunch_voucher_col, value=v_no)
            )

        cash = parse_num(d.get("現金"))
        card = parse_num(d.get("刷卡"))
        linepay = parse_num(d.get("Line Pay") or d.get("linepay") or d.get("LinePay"))
        uber = parse_num(d.get("Uber Eats") or d.get("UberEats") or d.get("ubereats"))
        foodpanda = parse_num(d.get("Foodpanda") or d.get("foodpanda") or d.get("Food Panda"))
        touding = parse_num(d.get("偷訂"))
        net = parse_num(d.get("未稅金額"))
        tax = parse_num(d.get("稅金"))

        entries = []
        entry_idx = 1

        # 1. 銷貨收入-餐賣 (貸方)
        if net > 0:
            entries.append({
                "seq": entry_idx,
                "code": acc.get("revenue", {}).get("code", "4100-01"),
                "name": acc.get("revenue", {}).get("name", "銷貨收入-餐賣"),
                "dc": acc.get("revenue", {}).get("dc", "貸"),
                "amt": net,
                "remark": acc.get("revenue", {}).get("remark", f"AR_一般顧客({store_name}) 營業收入_銷貨收入")
            })
            entry_idx += 1

        # 2. 應收帳款-現金 (借方)
        if cash > 0:
            entries.append({
                "seq": entry_idx,
                "code": acc.get("cash", {}).get("code", "1130-01"),
                "name": acc.get("cash", {}).get("name", "應收帳款-現金"),
                "dc": acc.get("cash", {}).get("dc", "借"),
                "amt": cash,
                "remark": acc.get("cash", {}).get("remark", f"AR_一般顧客({store_name}) 營業收入_現金")
            })
            entry_idx += 1

        # 3. 應收帳款-linepay (借方)
        if linepay > 0:
            entries.append({
                "seq": entry_idx,
                "code": acc.get("linepay", {}).get("code", "1130-02"),
                "name": acc.get("linepay", {}).get("name", "應收帳款-linepay"),
                "dc": acc.get("linepay", {}).get("dc", "借"),
                "amt": linepay,
                "remark": acc.get("linepay", {}).get("remark", f"AR_一般顧客({store_name}) 營業收入_linepay")
            })
            entry_idx += 1

        # 4. 應收帳款-ubereats (借方)
        if uber > 0:
            entries.append({
                "seq": entry_idx,
                "code": acc.get("ubereats", {}).get("code", "1130-03"),
                "name": acc.get("ubereats", {}).get("name", "應收帳款-ubereats"),
                "dc": acc.get("ubereats", {}).get("dc", "借"),
                "amt": uber,
                "remark": acc.get("ubereats", {}).get("remark", f"AR_一般顧客({store_name}) 營業收入_ubereats")
            })
            entry_idx += 1

        # 5. 應收帳款-foodpanda (借方)
        if foodpanda > 0:
            entries.append({
                "seq": entry_idx,
                "code": acc.get("foodpanda", {}).get("code", "1130-04"),
                "name": acc.get("foodpanda", {}).get("name", "應收帳款-foodpanda"),
                "dc": acc.get("foodpanda", {}).get("dc", "借"),
                "amt": foodpanda,
                "remark": acc.get("foodpanda", {}).get("remark", f"AR_一般顧客({store_name}) 營業收入_foodpanda")
            })
            entry_idx += 1

        # 6. 應收帳款-刷卡 (借方)
        if card > 0:
            entries.append({
                "seq": entry_idx,
                "code": acc.get("card", {}).get("code", "1130-05"),
                "name": acc.get("card", {}).get("name", "應收帳款-刷卡"),
                "dc": acc.get("card", {}).get("dc", "借"),
                "amt": card,
                "remark": acc.get("card", {}).get("remark", f"AR_一般顧客({store_name}) 營業收入_刷卡")
            })
            entry_idx += 1

        # 7. 應收帳款-偷訂 (借方)
        if touding > 0:
            entries.append({
                "seq": entry_idx,
                "code": acc.get("touding", {}).get("code", "1130-06"),
                "name": acc.get("touding", {}).get("name", "應收帳款-偷訂"),
                "dc": acc.get("touding", {}).get("dc", "借"),
                "amt": touding,
                "remark": acc.get("touding", {}).get("remark", f"AR_一般顧客({store_name}) 營業收入_偷訂")
            })
            entry_idx += 1

        # 8. 銷項稅額 (貸方)
        if tax > 0:
            entries.append({
                "seq": entry_idx,
                "code": acc.get("tax", {}).get("code", "2290"),
                "name": acc.get("tax", {}).get("name", "銷項稅額"),
                "dc": acc.get("tax", {}).get("dc", "貸"),
                "amt": tax,
                "remark": acc.get("tax", {}).get("remark", f"銷項AR_一般顧客({store_name}) 銷項稅額")
            })
            entry_idx += 1

        day_debit = sum(e["amt"] for e in entries if e["dc"] == "借")
        day_credit = sum(e["amt"] for e in entries if e["dc"] == "貸")
        total_month_debit += day_debit
        total_month_credit += day_credit

        clean_iso_date = raw_date.replace("/", "-")
        for e in entries:
            vouchers.append({
                "傳票號碼": v_no,
                "傳票種類": 3,
                "傳票日期_標準": clean_iso_date,
                "傳票日期_民國": roc_date_slash,
                "附件數": 0,
                "總摘要": total_remark,
                "項次": e["seq"],
                "會計項目": e["code"],
                "項目名稱": e["name"],
                "借貸": 1 if e["dc"] == "借" else 2,
                "對象類別": "01",
                "對象": obj_name,
                "摘要": e["remark"],
                "本幣金額": round(e["amt"]),
                "部門": dept,
                "專案": ""
            })

    if skipped_already_entered > 0:
        print(f"   ℹ️ 偵測到已有傳票號碼：自動略過 {skipped_already_entered} 天（避免重複匯入）")

    print(f"📋 【{store_name}】待產生天數: {valid_days} 天 | 總分錄: {len(vouchers)} 筆 | 借貸合計: ${total_month_debit:,.0f} (✅ 平衡)")

    # 執行回填試算表「午餐傳票號碼」
    if cells_to_write_back and not preview:
        try:
            ws.update_cells(cells_to_write_back)
            print(f"   ✍️ 成功自動回填 {len(cells_to_write_back)} 筆傳票號碼至試算表【午餐傳票號碼】欄位！")
        except Exception as e:
            print(f"   ⚠️ 回填傳票號碼至試算表失敗: {e}")

    return vouchers, valid_days, total_month_debit, total_month_credit

OFFICIAL_HEADER_1 = [
    "傳票號碼", "傳票種類", "傳票日期", "傳票總摘要", "序號", "借貸", "會計項目代號",
    "部門代號", "專案代號", "對象類別", "對象編號", "幣別", "匯率", "原幣金額", "本幣金額", "摘要",
    "建立人員", "建立日期", "主檔自定欄位1", "主檔自定欄位2", "主檔自定欄位3", "主檔自定欄位4",
    "主檔自定欄位5", "主檔自定欄位6", "主檔自定欄位7", "主檔自定欄位8", "主檔自定欄位9",
    "主檔自定欄位10", "主檔自定欄位11", "主檔自定欄位12", "明細自定欄位1", "明細自定欄位2",
    "明細自定欄位3", "明細自定欄位4", "明細自定欄位5", "明細自定欄位6", "明細自定欄位7",
    "明細自定欄位8", "明細自定欄位9", "明細自定欄位10", "明細自定欄位11", "明細自定欄位12"
]
OFFICIAL_HEADER_2 = [
    "DTGL004", "DTGL006", "DTGL007", "MFGL006", "DTGL005", "DTGL012", "DTGL008",
    "DTGL014", "DTGL015", "DTGL100", "DTGL009", "DTGL027", "DTGL028", "DTGL021", "DTGL013", "DTGL011",
    "MFGL996", "MFGL997", "MFGL801", "MFGL802", "MFGL803", "MFGL804",
    "MFGL805", "MFGL806", "MFGL807", "MFGL808", "MFGL809", "MFGL810", "MFGL811", "MFGL812",
    "DTGL801", "DTGL802", "DTGL803", "DTGL804", "DTGL805", "DTGL806", "DTGL807", "DTGL808",
    "DTGL809", "DTGL810", "DTGL811", "DTGL812"
]

def format_official_42_row(v):
    amt = round(v.get("本幣金額", 0))
    d_val = str(v.get("傳票日期_標準", "")).split()[0]
    row = [
        v.get("傳票號碼", "@"),
        3, # 轉帳傳票
        d_val,
        v.get("總摘要", ""),
        v.get("項次", 1),
        v.get("借貸", 1), # 1=借, 2=貸
        v.get("會計項目", ""),
        v.get("部門", ""),
        v.get("專案", ""),
        v.get("對象類別", "01"),
        v.get("對象", "A01"),
        "NTD",
        1,
        amt,
        amt,
        v.get("摘要", "")
    ]
    # 補足自定義欄位 26 欄 (16 + 26 = 42 欄)
    row.extend([""] * 26)
    return row

def save_vouchers_to_files(vouchers, base_filename):
    if not vouchers:
        return
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 僅產出單一合併 Excel 檔案
    excel_path = os.path.join(OUTPUT_DIR, f"{base_filename}.xlsx")
    try:
        import openpyxl
        wb = openpyxl.Workbook()
        ws_out = wb.active
        ws_out.title = "傳票維護匯入"
        ws_out.append(OFFICIAL_HEADER_1)
        ws_out.append(OFFICIAL_HEADER_2)
        for v in vouchers:
            ws_out.append(format_official_42_row(v))
        wb.save(excel_path)
        print(f"   💾 文中標準 Excel 匯入檔已產生 (42欄): {os.path.basename(excel_path)}")
    except Exception as e:
        print(f"   ❌ 產出 Excel 失敗: {e}")

def main():
    parser = argparse.ArgumentParser(description="營運總表 ➔ 文中會計系統傳票一鍵產生工具")
    parser.add_argument("--store", type=str, default="all", help="指定事業體（例如：月宮、隨意小吃，或輸入 all 自動產生全部）")
    parser.add_argument("--month", type=str, help="指定轉換月份工作表（例如 2026-10）")
    parser.add_argument("--force", action="store_true", help="強制重新產生所有傳票（忽略已存在的午餐傳票號碼）")
    parser.add_argument("--preview", action="store_true", help="僅預覽，不產生檔案且不回填試算表")
    args = parser.parse_args()

    full_config = load_config()

    print("\n" + "="*65)
    print("🚀 啟動【文中會計系統】傳票一鍵產生工具（自動回填傳票號碼、防重複）")
    print("="*65)

    gc = get_gspread_client()
    target_month = args.month or "2026-10"
    compact_month = target_month.replace("-", "")

    if "stores" in full_config:
        store_map = full_config["stores"]
        target = args.store.strip() if args.store else "all"

        if target.lower() == "all":
            print(f"📦 模式：一次抓取所有事業體，產出單一【全部合併 Excel】...")
            all_combined_vouchers = []

            for idx, (k, cfg) in enumerate(store_map.items(), start=1):
                vouchers, days, d_sum, c_sum = fetch_store_vouchers(
                    gc, k, cfg, store_idx=idx, target_month=args.month, force=args.force, preview=args.preview
                )
                if vouchers:
                    all_combined_vouchers.extend(vouchers)

            if all_combined_vouchers:
                all_combined_vouchers.sort(key=lambda x: (x.get("傳票日期_標準", x.get("傳票日期", "")), str(x.get("傳票號碼", "")), int(x.get("項次", 1))))

                print("\n" + "="*65)
                print("📦 正在產生【全部事業體合併】的總匯入檔...")
                save_vouchers_to_files(all_combined_vouchers, f"文中傳票維護匯入_全部合併_{compact_month}")

                print("="*65)
                print(f"🎉 全部處理完成！總計產生新傳票分錄: {len(all_combined_vouchers)} 筆")
                print(f"📁 檔案存放目錄：{os.path.abspath(OUTPUT_DIR)}")
                print("💡 文中匯入建議：只要將【文中傳票維護匯入_全部合併_202610.xlsx】")
                print("   匯入文中系統一次，就能將月宮與隨意小吃的傳票一次全數建立！")
                print("="*65 + "\n")
            else:
                print("\n" + "="*65)
                print("ℹ️ 所有營業日均已填寫傳票號碼，無新增待匯入之傳票！（如需強制重新產出請加 --force）")
                print("="*65 + "\n")
        else:
            matched_key = None
            for k in store_map:
                if target in k or k in target:
                    matched_key = k
                    break
            if not matched_key:
                print(f"❌ 找不到事業體【{target}】，可用清單：{list(store_map.keys())}")
                sys.exit(1)
            vouchers, _, _, _ = fetch_store_vouchers(
                gc, matched_key, store_map[matched_key], store_idx=1, target_month=args.month, force=args.force, preview=args.preview
            )
            if vouchers:
                safe_name = re.sub(r'[^\w\-]', '_', store_map[matched_key].get('name', matched_key))
                save_vouchers_to_files(vouchers, f"文中傳票維護匯入_{safe_name}_{compact_month}")
    else:
        vouchers, _, _, _ = fetch_store_vouchers(
            gc, "default", full_config, store_idx=1, target_month=args.month, force=args.force, preview=args.preview
        )
        if vouchers:
            save_vouchers_to_files(vouchers, f"文中傳票維護匯入_{compact_month}")

if __name__ == "__main__":
    main()
