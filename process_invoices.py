import os
import sys
import re
import glob
import shutil
import json
import argparse
from datetime import datetime
import openpyxl

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
INVOICE_DIR = os.path.join(os.path.dirname(__file__), "invoices")
PROCESSED_DIR = os.path.join(INVOICE_DIR, "processed")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")

# 平台與對象科目映射庫
KNOWN_VENDORS = {
    "83118125": {"name": "優步福爾摩沙(Uber Eats)", "platform": "ubereats", "credit_code": "1130-03", "credit_name": "應收帳款-ubereats"},
    "優步": {"name": "優步福爾摩沙(Uber Eats)", "platform": "ubereats", "credit_code": "1130-03", "credit_name": "應收帳款-ubereats"},
    "uber": {"name": "優步福爾摩沙(Uber Eats)", "platform": "ubereats", "credit_code": "1130-03", "credit_name": "應收帳款-ubereats"},
    "99024139": {"name": "連加網路(LINE Pay)", "platform": "linepay", "credit_code": "1130-02", "credit_name": "應收帳款-linepay"},
    "連加網路": {"name": "連加網路(LINE Pay)", "platform": "linepay", "credit_code": "1130-02", "credit_name": "應收帳款-linepay"},
    "line pay": {"name": "連加網路(LINE Pay)", "platform": "linepay", "credit_code": "1130-02", "credit_name": "應收帳款-linepay"},
    "53075607": {"name": "富胖達(Foodpanda)", "platform": "foodpanda", "credit_code": "1130-04", "credit_name": "應收帳款-Foodpanda"},
    "富胖達": {"name": "富胖達(Foodpanda)", "platform": "foodpanda", "credit_code": "1130-04", "credit_name": "應收帳款-Foodpanda"},
    "foodpanda": {"name": "富胖達(Foodpanda)", "platform": "foodpanda", "credit_code": "1130-04", "credit_name": "應收帳款-Foodpanda"},
    "93633536": {"name": "偷訂餐飲科技", "platform": "touding", "credit_code": "1130-05", "credit_name": "應收帳款-偷訂"},
    "偷訂": {"name": "偷訂餐飲科技", "platform": "touding", "credit_code": "1130-05", "credit_name": "應收帳款-偷訂"},
}

def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def ensure_dirs():
    os.makedirs(INVOICE_DIR, exist_ok=True)
    os.makedirs(PROCESSED_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

def parse_num(val):
    if not val:
        return 0.0
    s = re.sub(r"[^\d.-]", "", str(val))
    try:
        return float(s)
    except Exception:
        return 0.0

def standardize_date(date_str):
    if not date_str:
        return datetime.today().strftime("%Y-%m-%d")
    s = str(date_str).strip().replace("/", "-")
    # 民國年判斷 (e.g. 115-10-01)
    m = re.match(r"^(\d{2,3})-(\d{1,2})-(\d{1,2})", s)
    if m:
        roc_y, mm, dd = int(m.group(1)), int(m.group(2)), int(m.group(3))
        ad_y = roc_y + 1911 if roc_y < 1900 else roc_y
        return f"{ad_y:04d}-{mm:02d}-{dd:02d}"
    # 西元年判斷 (e.g. 2026-10-01)
    m2 = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m2:
        return f"{int(m2.group(1)):04d}-{int(m2.group(2)):02d}-{int(m2.group(3)):02d}"
    return datetime.today().strftime("%Y-%m-%d")

def extract_text_from_pdf(pdf_path):
    import pypdf
    text = ""
    try:
        reader = pypdf.PdfReader(pdf_path)
        for page in reader.pages:
            t = page.extract_text()
            if t:
                text += t + "\n"
    except Exception as e:
        print(f"⚠️ 讀取 PDF 失敗 ({pdf_path}): {e}")
    return text

def parse_invoice_text(text, filename=""):
    """
    從純文字內容（PDF 抽取、文字檔或 OCR）智慧擷取發票關鍵欄位
    """
    invoice_data = {
        "filename": filename,
        "invoice_no": "",
        "date": "",
        "seller_tax_id": "",
        "seller_name": "",
        "buyer_tax_id": "",
        "sales_amount": 0.0,
        "tax_amount": 0.0,
        "total_amount": 0.0,
        "store": "月宮",  # 預設
        "vendor_info": None
    }

    # 1. 發票號碼 Regex (兩個大寫英文 + 8位數字，例如 AB-12345678 或 AB12345678)
    inv_match = re.search(r"([A-Z]{2})[- ]?([0-9]{8})", text)
    if inv_match:
        invoice_data["invoice_no"] = f"{inv_match.group(1)}-{inv_match.group(2)}"

    # 2. 開立日期 Regex
    date_match = re.search(r"(?:開立日期|日期|Date)[：:\s]*([0-9]{2,4}[-/.][0-9]{1,2}[-/.][0-9]{1,2})", text)
    if not date_match:
        date_match = re.search(r"([0-9]{3,4}[-/.][0-9]{1,2}[-/.][0-9]{1,2})", text)
    if date_match:
        invoice_data["date"] = standardize_date(date_match.group(1))
    else:
        invoice_data["date"] = datetime.today().strftime("%Y-%m-%d")

    # 3. 賣方統編與名稱
    for key, vinfo in KNOWN_VENDORS.items():
        if key in text:
            invoice_data["seller_name"] = vinfo["name"]
            invoice_data["seller_tax_id"] = key if key.isdigit() and len(key) == 8 else ""
            invoice_data["vendor_info"] = vinfo
            break

    # 若未命中已知庫，搜尋常見統編格式
    if not invoice_data["seller_name"]:
        tax_matches = re.findall(r"(?:統一編號|統編|賣方)[：:\s]*([0-9]{8})", text)
        if tax_matches:
            invoice_data["seller_tax_id"] = tax_matches[0]
            invoice_data["seller_name"] = f"廠商({tax_matches[0]})"
        else:
            invoice_data["seller_name"] = "平台外送/金流廠商"

    # 4. 店別判斷 (買方辨識)
    if any(k in text for k in ["隨意", "隨意小吃"]):
        invoice_data["store"] = "隨意小吃"
    elif any(k in text for k in ["月下", "月宮"]):
        invoice_data["store"] = "月宮"

    # 5. 金額辨識
    # 尋找「總計」、「應付」、「含稅」
    total_m = re.search(r"(?:總計|總額|含稅金額|合計|Total Amount)[：:\s]*\$?([0-9,]+(?:\.[0-9]+)?)", text, re.I)
    sales_m = re.search(r"(?:銷售額|未稅金額|銷售額合計|Sales Amount)[：:\s]*\$?([0-9,]+(?:\.[0-9]+)?)", text, re.I)
    tax_m = re.search(r"(?:營業稅|稅額|進項稅額|Tax)[：:\s]*\$?([0-9,]+(?:\.[0-9]+)?)", text, re.I)

    tot = parse_num(total_m.group(1)) if total_m else 0.0
    sales = parse_num(sales_m.group(1)) if sales_m else 0.0
    tax = parse_num(tax_m.group(1)) if tax_m else 0.0

    # 互推邏輯 (若只抓到總額或銷售額，自動推算 5% 稅額)
    if tot > 0 and sales == 0:
        sales = round(tot / 1.05)
        tax = tot - sales
    elif sales > 0 and tot == 0:
        tax = round(sales * 0.05)
        tot = sales + tax
    elif tot > 0 and sales > 0 and tax == 0:
        tax = tot - sales

    invoice_data["sales_amount"] = sales
    invoice_data["tax_amount"] = tax
    invoice_data["total_amount"] = tot

    return invoice_data

def parse_excel_csv(file_path):
    """
    解析 Excel / CSV 格式的發票或明細表
    """
    records = []
    wb = openpyxl.load_workbook(file_path, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []

    # 尋找標題列
    header_idx = 0
    for i, r in enumerate(rows[:5]):
        line_str = " ".join([str(c) for c in r if c is not None])
        if any(k in line_str for k in ["發票", "手續費", "金額", "統編"]):
            header_idx = i
            break

    headers = [str(c).strip() if c is not None else "" for c in rows[header_idx]]
    for r in rows[header_idx + 1:]:
        if not any(r):
            continue
        row_str = " ".join([str(c) for c in r if c is not None])
        inv = parse_invoice_text(row_str, filename=os.path.basename(file_path))
        if inv["total_amount"] > 0 or inv["invoice_no"]:
            records.append(inv)

    return records

def scan_and_parse_invoices():
    """
    掃描 invoices/ 目錄下所有待處理檔案
    """
    ensure_dirs()
    ignore_names = ["readme.txt", "使用說明.txt", ".ds_store"]
    files = [
        f for f in glob.glob(os.path.join(INVOICE_DIR, "*"))
        if os.path.isfile(f) and not os.path.basename(f).startswith(".") and os.path.basename(f).lower() not in ignore_names
    ]
    
    if not files:
        print(f"📂 目前【invoices/】資料夾中沒有任何發票檔案。")
        print(f"👉 請將 Uber Eats、LINE Pay 等手續費發票（PDF、Excel 或文字檔）直接放入此資料夾：")
        print(f"   {os.path.abspath(INVOICE_DIR)}")
        return []

    print(f"🔍 掃描到 {len(files)} 個檔案，開始智慧辨識文字與發票內容...")
    parsed_invoices = []

    for fpath in files:
        fname = os.path.basename(fpath)
        ext = os.path.splitext(fname)[1].lower()
        print(f"\n📄 正在讀取：【{fname}】")

        if ext == ".pdf":
            text = extract_text_from_pdf(fpath)
            inv = parse_invoice_text(text, filename=fname)
            parsed_invoices.append((fpath, inv))
        elif ext in [".xlsx", ".xls", ".csv"]:
            inv_list = parse_excel_csv(fpath)
            for inv in inv_list:
                parsed_invoices.append((fpath, inv))
        elif ext in [".txt", ".json"]:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as tf:
                text = tf.read()
            inv = parse_invoice_text(text, filename=fname)
            parsed_invoices.append((fpath, inv))
        else:
            print(f"   ⚠️ 不支援的檔案格式: {ext} (目前支援: PDF, Excel, CSV, TXT)")

    return parsed_invoices

def generate_invoice_vouchers(parsed_invoices):
    """
    產生文中 42 欄標準傳票
    """
    config = load_config()
    stores_cfg = config.get("stores", {})

    voucher_rows = []
    
    # 42 欄中文標題與代碼
    chinese_header = [
        "傳票號碼", "傳票種類", "傳票日期", "傳票總摘要", "序號", "借貸", "會計項目代號",
        "部門代號", "專案代號", "對象類別", "對象編號", "幣別", "匯率", "原幣金額",
        "本幣金額", "摘要", "建立人員", "建立日期",
        "主檔自定欄位1", "主檔自定欄位2", "主檔自定欄位3", "主檔自定欄位4",
        "主檔自定欄位5", "主檔自定欄位6", "主檔自定欄位7", "主檔自定欄位8",
        "主檔自定欄位9", "主檔自定欄位10", "主檔自定欄位11", "主檔自定欄位12",
        "明細自定欄位1", "明細自定欄位2", "明細自定欄位3", "明細自定欄位4",
        "明細自定欄位5", "明細自定欄位6", "明細自定欄位7", "明細自定欄位8",
        "明細自定欄位9", "明細自定欄位10", "明細自定欄位11", "明細自定欄位12"
    ]
    code_header = [
        "DTGL004", "DTGL006", "DTGL007", "MFGL006", "DTGL005", "DTGL012", "DTGL008",
        "DTGL014", "DTGL015", "DTGL100", "DTGL009", "DTGL027", "DTGL028", "DTGL021",
        "DTGL013", "DTGL011", "MFGL996", "MFGL997",
        "MFGL801", "MFGL802", "MFGL803", "MFGL804", "MFGL805", "MFGL806",
        "MFGL807", "MFGL808", "MFGL809", "MFGL810", "MFGL811", "MFGL812",
        "DTGL801", "DTGL802", "DTGL803", "DTGL804", "DTGL805", "DTGL806",
        "DTGL807", "DTGL808", "DTGL809", "DTGL810", "DTGL811", "DTGL812"
    ]
    
    voucher_rows.append(chinese_header)
    voucher_rows.append(code_header)

    valid_count = 0

    for fpath, inv in parsed_invoices:
        tot = inv["total_amount"]
        sales = inv["sales_amount"]
        tax = inv["tax_amount"]
        inv_no = inv["invoice_no"] or os.path.splitext(inv["filename"])[0]
        store = inv["store"]
        store_cfg = stores_cfg.get(store, {})
        dept = store_cfg.get("department", store)

        if tot <= 0:
            print(f"   ⚠️ 跳過未解析出有效金額之項目: {inv['filename']}")
            continue

        valid_count += 1
        seller = inv["seller_name"]
        vendor_info = inv.get("vendor_info") or {}
        credit_code = vendor_info.get("credit_code", "1130-03" if "uber" in seller.lower() else "2145")
        credit_name = vendor_info.get("credit_name", "應收帳款-ubereats" if "uber" in seller.lower() else "應付費用")

        print(f"   ✅ 【發票成功解析】")
        print(f"      • 發票號碼: {inv_no} | 日期: {inv['date']} | 事業體: {store}")
        print(f"      • 廠商: {seller} | 銷售額(未稅): ${sales:>8,.0f} | 稅額(5%): ${tax:>5,.0f} | 總計: ${tot:>8,.0f}")
        print(f"      • 沖銷科目: {credit_code} ({credit_name})")

        total_remark = f"手續費發票_{seller}_{inv_no}_{store}"
        
        # 借方 1: 5151 手續費支出 (未稅)
        row_fee = [
            "@", 3, inv["date"], total_remark, 1, 1, "5151", dept, None, "02", "SU001",
            "NTD", 1, sales, sales, f"{seller} 手續費發票 {inv_no}", None, None,
            None, None, None, None, None, None, None, None, None, None, None, None,
            None, None, None, None, None, None, None, None, None, None, None, None
        ]
        voucher_rows.append(row_fee)

        # 借方 2: 1268 進項稅額 (稅額 5%)
        row_tax = [
            "@", 3, inv["date"], total_remark, 2, 1, "1268", dept, None, "02", "SU001",
            "NTD", 1, tax, tax, f"{seller} 手續費進項稅額 {inv_no}", None, None,
            None, None, None, None, None, None, None, None, None, None, None, None,
            None, None, None, None, None, None, None, None, None, None, None, None
        ]
        voucher_rows.append(row_tax)

        # 貸方 3: 沖銷應收帳款 (總額)
        row_credit = [
            "@", 3, inv["date"], total_remark, 3, 2, credit_code, dept, None, "01", "A01",
            "NTD", 1, tot, tot, f"沖銷 {seller} 應收帳款 (發票 {inv_no})", None, None,
            None, None, None, None, None, None, None, None, None, None, None, None,
            None, None, None, None, None, None, None, None, None, None, None, None
        ]
        voucher_rows.append(row_credit)

    if valid_count == 0:
        print("\n⚠️ 未產生任何傳票。")
        return None

    # 輸出為標準 Excel
    timestamp = datetime.today().strftime("%Y%m")
    out_file = os.path.join(OUTPUT_DIR, f"文中傳票維護匯入_手續費發票_{timestamp}.xlsx")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "sheet1"

    for r_idx, row in enumerate(voucher_rows, start=1):
        for c_idx, val in enumerate(row, start=1):
            cell = ws.cell(row=r_idx, column=c_idx, value=val)
            if r_idx <= 2:
                cell.font = openpyxl.styles.Font(bold=True)

    wb.save(out_file)
    print("\n" + "="*70)
    print(f"🎉 手續費發票傳票產生完畢！共成功處理 {valid_count} 張發票")
    print(f"💾 檔案路徑：{out_file}")
    print("="*70)
    return out_file

def archive_files(parsed_invoices):
    for fpath, _ in parsed_invoices:
        if os.path.exists(fpath):
            fname = os.path.basename(fpath)
            dest = os.path.join(PROCESSED_DIR, fname)
            # 若已存在同名檔案，加上時間戳
            if os.path.exists(dest):
                base, ext = os.path.splitext(fname)
                dest = os.path.join(PROCESSED_DIR, f"{base}_{datetime.now().strftime('%H%M%S')}{ext}")
            shutil.move(fpath, dest)
            print(f"📦 已歸檔檔案 ➔ invoices/processed/{os.path.basename(dest)}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="手續費發票辨識與傳票產生器")
    parser.add_argument("--archive", action="store_true", help="產生傳票後自動將已處理檔案移動至 processed 備份目錄")
    args = parser.parse_args()

    ensure_dirs()
    invoices = scan_and_parse_invoices()
    if invoices:
        out = generate_invoice_vouchers(invoices)
        if out and args.archive:
            archive_files(invoices)
        try:
            import reconciliation_summary
            summary_data = reconciliation_summary.calculate_monthly_summary()
            if summary_data:
                reconciliation_summary.print_and_export_summary(summary_data)
        except Exception:
            pass
