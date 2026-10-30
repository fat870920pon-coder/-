#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_ubereats.py
----------------
自動化同步 Uber Eats 商家管理後台 (Uber Eats Manager) 財務資料：
1. 月宮餐飲 (月宮lunar Palace)
2. 東區隨意小吃 (隨意小吃)

功能：
- 提取雙週撥款記錄 (Bi-weekly Payouts)
- 提取月對帳單 (Monthly Statements)
- 提取當期進行中營收 (Current Payout Period)
- 產出 ubereats_merchant_data.json
"""
import os, sys, time, json, re
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(OUTPUT_DIR, "ubereats_merchant_data.json")
BACKUP_PATH = os.path.expanduser("~/Desktop/腳本資料夾/月下/文中會計傳票產生器/ubereats_merchant_data.json")

STORE_CONFIGS = {
    "yuegong": {
        "store_key": "yuegong",
        "brand_name": "月宮（餐飲）",
        "company_name": "月下餐飲股份有限公司",
        "merchant_name": "月宮lunar Palace",
        "session_file": os.path.join(OUTPUT_DIR, "ubereats_yuegong_session.json"),
        "fee_rate_est": 0.31,
    },
    "suiyi": {
        "store_key": "suiyi",
        "brand_name": "東區隨意小吃",
        "company_name": "頑全公關顧問股份有限公司隨意小吃本店",
        "merchant_name": "東區隨意小吃",
        "session_file": os.path.join(OUTPUT_DIR, "ubereats_suiyi_session.json"),
        "fee_rate_est": 0.31,
    }
}

def clean_money(val_str):
    if not val_str:
        return 0.0
    s = str(val_str).replace("$", "").replace(",", "").replace(" ", "").replace("NT", "").strip()
    try:
        return float(s)
    except Exception:
        return 0.0

def fetch_store_uber_data(store_key, config):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 正在同步 Uber Eats 財務資料: {config['brand_name']}...")
    session_file = config["session_file"]
    if not os.path.exists(session_file):
        print(f"  警告: Session 檔案不存在: {session_file}")
        return None
        
    with open(session_file, "r", encoding="utf-8") as f:
        sess = json.load(f)
        
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1440,900")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    driver = webdriver.Chrome(options=options)
    result = {
        "store_key": store_key,
        "brand_name": config["brand_name"],
        "merchant_name": config["merchant_name"],
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "current_period": {},
        "payouts": [],
        "monthly_statements": [],
        "invoices": []
    }
    
    try:
        # Load domain and inject cookies
        driver.get("https://merchants.ubereats.com/")
        time.sleep(2)
        for c in sess.get("cookies", []):
            try:
                driver.add_cookie({
                    "name": c["name"],
                    "value": c["value"],
                    "domain": c.get("domain", ".ubereats.com"),
                    "path": c.get("path", "/")
                })
            except Exception:
                pass
                
        # 1. 抓取月對帳單 (Monthly Statements)
        driver.get("https://merchants.ubereats.com/manager/payments/statements")
        time.sleep(5)
        
        statements_raw = driver.execute_script("""
            let rows = Array.from(document.querySelectorAll("table tbody tr"));
            return rows.map(r => {
                let cells = Array.from(r.querySelectorAll("td")).map(td => td.innerText.trim());
                return cells;
            });
        """)
        
        for r in statements_raw:
            # Handle row where r[0] is checkbox
            cells = [c for c in r if c != ""]
            if len(cells) >= 6:
                # Find month label (e.g. "9 2026", "8 2026", "7 2026")
                m_label = ""
                store_n = ""
                num_cells = []
                for c in cells:
                    if re.match(r"^\d+\s+\d{4}$", c):
                        m_label = c
                    elif any(s in c for s in ["月宮", "隨意"]):
                        store_n = c
                    elif "$" in c or re.match(r"^-?[\d,.]+$", c):
                        num_cells.append(clean_money(c))
                        
                m_match = re.search(r"(\d+)\s*(\d{4})", m_label) if m_label else None
                if m_match:
                    month_key = f"{m_match.group(2)}-{int(m_match.group(1)):02d}"
                else:
                    month_key = m_label
                    
                total_sales = num_cells[0] if len(num_cells) > 0 else 0.0
                marketing = num_cells[1] if len(num_cells) > 1 else 0.0
                fee = num_cells[2] if len(num_cells) > 2 else 0.0
                adj = num_cells[3] if len(num_cells) > 3 else 0.0
                net_payout = num_cells[4] if len(num_cells) > 4 else (total_sales + marketing + fee + adj)
                
                fee_rate = round(abs(fee) / total_sales * 100, 2) if total_sales > 0 else 0.0
                
                result["monthly_statements"].append({
                    "month": month_key,
                    "month_label": m_label,
                    "store_name": store_n,
                    "total_sales": total_sales,
                    "marketing": marketing,
                    "uber_fee": fee,
                    "fee_rate_pct": fee_rate,
                    "adjustments": adj,
                    "net_payout": net_payout,
                    "status": "已結清"
                })
                
        # 2. 抓取雙週撥款記錄 (Bi-weekly Payouts)
        driver.get("https://merchants.ubereats.com/manager/payments")
        time.sleep(5)
        
        # Current period (進行中)
        cur_text = driver.execute_script("""
            let body = document.body.innerText;
            return body;
        """)
        
        cur_match = re.search(r"費用期間為\s*([^\n]+)\n\$?([0-9,.]+)\n([^\n]+)\n預計存入日期為\s*([^\n]+)", cur_text)
        if cur_match:
            result["current_period"] = {
                "period_label": cur_match.group(1).strip(),
                "amount": clean_money(cur_match.group(2)),
                "status": cur_match.group(3).strip(),
                "est_payout_date": cur_match.group(4).strip()
            }
            
        payouts_raw = driver.execute_script("""
            let rows = Array.from(document.querySelectorAll("table tbody tr"));
            return rows.map(r => {
                let cells = Array.from(r.querySelectorAll("td")).map(td => td.innerText.trim());
                return cells;
            });
        """)
        
        for r in payouts_raw:
            if len(r) >= 8:
                # [period, status, payout_date, tx_id, total_sales, marketing, fee, adjustments, net_payout]
                sales = clean_money(r[4])
                mkt = clean_money(r[5])
                fee = clean_money(r[6])
                adj = clean_money(r[7])
                net = clean_money(r[8]) if len(r) > 8 else clean_money(r[7])
                
                fee_rate = round(abs(fee) / sales * 100, 2) if sales > 0 else 0.0
                
                result["payouts"].append({
                    "period": r[0].replace("\n", " "),
                    "status": r[1],
                    "payout_date": r[2],
                    "transaction_id": r[3],
                    "total_sales": sales,
                    "marketing": mkt,
                    "uber_fee": fee,
                    "fee_rate_pct": fee_rate,
                    "adjustments": adj,
                    "net_payout": net
                })
                
        print(f"  成功獲取 {len(result['monthly_statements'])} 筆月對帳單，{len(result['payouts'])} 筆雙週撥款明細。")

        # 3. 抓取官方電子發票 (Tax Invoices / Receipts)
        driver.get("https://merchants.ubereats.com/manager/invoices")
        time.sleep(6)
        
        invoices_raw = driver.execute_script("""
            let trs = Array.from(document.querySelectorAll("table tr"));
            return trs.map(tr => {
                let tds = Array.from(tr.querySelectorAll("td, th")).map(td => td.innerText.trim());
                let link = tr.querySelector("a");
                return {
                    tds: tds,
                    download_url: link ? link.href : ""
                };
            });
        """)
        
        cookie_dict = {c['name']: c['value'] for c in sess.get("cookies", [])}
        headers = {
            'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'referer': 'https://merchants.ubereats.com/manager/invoices'
        }
        
        for r in invoices_raw:
            tds = r["tds"]
            if len(tds) >= 3 and re.match(r"^[A-Z]{2}\d{8}$", tds[0]):
                inv_no = tds[0]
                desc = tds[1]
                issue_date = tds[2]
                d_url = r["download_url"]
                inv_uuid = ""
                if "invoiceUUID=" in d_url:
                    inv_uuid = d_url.split("invoiceUUID=")[1].split("&")[0]
                
                sales_amt = 0.0
                tax_amt = 0.0
                total_amt = 0.0
                
                if d_url:
                    try:
                        import requests, zipfile, io, xml.etree.ElementTree as ET
                        res = requests.get(d_url, cookies=cookie_dict, headers=headers, timeout=10)
                        if res.status_code == 200:
                            with zipfile.ZipFile(io.BytesIO(res.content)) as z:
                                for name in z.namelist():
                                    if name.endswith(".xml"):
                                        root = ET.fromstring(z.read(name))
                                        for el in root.iter():
                                            if el.tag.endswith("SalesAmount"):
                                                sales_amt = float(el.text or 0)
                                            elif el.tag.endswith("TaxAmount"):
                                                tax_amt = float(el.text or 0)
                                            elif el.tag.endswith("TotalAmount"):
                                                total_amt = float(el.text or 0)
                    except Exception:
                        pass
                
                if total_amt > 0 and sales_amt == 0:
                    sales_amt = round(total_amt - tax_amt)

                result["invoices"].append({
                    "invoice_number": inv_no,
                    "description": desc,
                    "issue_date": issue_date,
                    "download_url": d_url,
                    "invoice_uuid": inv_uuid,
                    "seller": "優食台灣股份有限公司 (83076928)",
                    "untaxed_amount": sales_amt,
                    "tax_amount": tax_amt,
                    "total_amount": total_amt,
                    "status": "已開立 (可全額扣抵)"
                })
                
        print(f"  成功獲取 {len(result['invoices'])} 張官方電子發票。")
        return result
    except Exception as e:
        print(f"  同步失敗: {e}")
        return None
    finally:
        driver.quit()

def sync_all_ubereats():
    print(f"==================================================")
    print(f"開始全事業體 Uber Eats 雙門市財務資料同步")
    print(f"執行時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"==================================================")
    
    data = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stores": {}
    }
    
    # Load existing data if available
    if os.path.exists(DATA_PATH):
        try:
            with open(DATA_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
            
    run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"
    data["run_id"] = run_id

    for sk, cfg in STORE_CONFIGS.items():
        read_start = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        store_res = fetch_store_uber_data(sk, cfg)
        read_end = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        tax_id = "94001820" if sk == "yuegong" else "94006775"
        if store_res:
            store_res["run_id"] = run_id
            store_res["read_started_at"] = read_start
            store_res["read_completed_at"] = read_end
            store_res["fetch_result"] = "SUCCESS"
            store_res["last_success_at"] = read_end
            for inv in store_res.get("invoices", []):
                inv["buyer_tax_id"] = tax_id
            data["stores"][sk] = store_res
        else:
            if sk in data.get("stores", {}):
                data["stores"][sk]["fetch_result"] = "FAILED"
                data["stores"][sk]["read_started_at"] = read_start
                data["stores"][sk]["read_completed_at"] = read_end
                data["stores"][sk]["last_attempt_at"] = read_end

    data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 已成功寫入主要資料檔: {DATA_PATH}")
    
    # Backup to 月下 project
    try:
        os.makedirs(os.path.dirname(BACKUP_PATH), exist_ok=True)
        with open(BACKUP_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"✅ 已成功備份至文中傳票專案: {BACKUP_PATH}")
    except Exception as e:
        print(f"備份失敗: {e}")
        
    return data

if __name__ == "__main__":
    sync_all_ubereats()
