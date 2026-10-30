#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_linepay.py
---------------
自動化登入 LINE Pay 合作商店後台 (Merchant Center)
支援雙商戶自動排程登入與清算核銷：
1. 「隨意小吃」 (tpilf.00682206.pos)
   - 結算週期：每週一至每週日帳務，週一清算，週二撥款
   - 手續費率：2.31% (四捨五入)
2. 「月宮（餐飲）」 (moonlitdining2026)
   - 結算週期：每日營業款 T+2 撥款，遇假日順延 (週五～週日於次週二合併撥款)
   - 手續費率：2.31% (四捨五入)

爬取每日付款、退款與清算撥款明細，輸出至 linepay_merchant_data.json。
"""

import os
import sys
import time
import json
import re
import logging
from datetime import datetime, date, timedelta
import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

OUTPUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "linepay_merchant_data.json")
BACKUP_COPY_PATH = os.path.expanduser("~/Desktop/腳本資料夾/月下/文中會計傳票產生器/linepay_merchant_data.json")
FOOD_SHEETS_PATH = os.path.expanduser("~/Desktop/腳本資料夾/月下/文中會計傳票產生器/food_sheets_data.json")

STORE_CONFIGS = {
    "suiyi": {
        "store_key": "suiyi",
        "account_id": os.environ.get("LINEPAY_ACCOUNT_SUIYI", "tpilf.00682206.pos"),
        "password": os.environ.get("LINEPAY_PASSWORD_SUIYI", "Chilling00682206"),
        "center_id": "TPILF.00682206.POS",
        "brand_name": "東區隨意小吃",
        "company_name": "頑全公關顧問股份有限公司隨意小吃本店",
        "settlement_type": "WEEKLY",
        "settlement_desc": "每週一至每週日結算，週一清算，週二撥款",
        "fee_rate": 0.0231,
        "food_sheets_key": "suiyi"
    },
    "yuegong": {
        "store_key": "yuegong",
        "account_id": os.environ.get("LINEPAY_ACCOUNT_YUEGONG", "moonlitdining2026"),
        "password": os.environ.get("LINEPAY_PASSWORD_YUEGONG", "Huachen0826"),
        "center_id": "moonlitdining2026",
        "brand_name": "月宮（餐飲）",
        "company_name": "月下餐飲股份有限公司",
        "settlement_type": "T_PLUS_2",
        "settlement_desc": "每日營業款 T+2 撥款，遇假日順延 (週五～週日於次週二合併撥款)",
        "fee_rate": 0.0231,
        "food_sheets_key": "yuegong"
    }
}

# 臺灣國定銀行假日清單 (遇假日順延判定)
TAIWAN_BANK_HOLIDAYS = {
    "2026-09-17", # 中秋節
    "2026-10-09", # 雙十節補假
    "2026-10-10", # 國慶日
}

def login_and_get_session(account, password):
    """使用 Headless Chrome 登入 LINE Pay 後台並取得 Session Cookies"""
    logging.info(f"啟動 Headless Chrome 準備登入 LINE Pay 後台 ({account})...")
    
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1400,900")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    driver = webdriver.Chrome(options=options)
    try:
        # Step 1: 首頁輸入商店 ID
        driver.get("https://pay.line.me/portal/tw/auth/login/id?locale=zh_TW")
        time.sleep(3)

        id_input = None
        for inp in driver.find_elements(By.TAG_NAME, "input"):
            if inp.get_attribute("type") == "text" or "商店ID" in (inp.get_attribute("placeholder") or ""):
                id_input = inp
                break
        if not id_input:
            raise RuntimeError("找不到商店ID輸入框")

        id_input.clear()
        id_input.send_keys(account)
        time.sleep(1)

        # 點擊「下一步」
        for btn in driver.find_elements(By.TAG_NAME, "button"):
            if "下一步" in btn.text:
                btn.click()
                break
        time.sleep(3)

        # Step 2: 輸入密碼
        pw_input = None
        for inp in driver.find_elements(By.TAG_NAME, "input"):
            if inp.get_attribute("type") == "password" or inp.get_attribute("name") == "password":
                pw_input = inp
                break
        if not pw_input:
            raise RuntimeError("找不到密碼輸入框")

        pw_input.clear()
        pw_input.send_keys(password)
        time.sleep(1)

        # 點擊「登入」
        for btn in driver.find_elements(By.TAG_NAME, "button"):
            if "登入" in btn.text:
                btn.click()
                break
        time.sleep(4)

        # Step 3: 若出現定期變更密碼提醒，點擊「下次再變更」
        for btn in driver.find_elements(By.TAG_NAME, "button"):
            if "下次" in btn.text:
                logging.info("偵測到定期變更密碼提醒，自動點擊「下次再變更」...")
                btn.click()
                time.sleep(3)
                break

        logging.info(f"登入成功！當前頁面 URL: {driver.current_url}")
        selenium_cookies = driver.get_cookies()
        driver.quit()
        return selenium_cookies
    except Exception as e:
        driver.quit()
        raise e

def create_requests_session(selenium_cookies):
    """將 Selenium cookies 轉移至 requests.Session 供高速呼叫 REST API"""
    sess = requests.Session()
    for c in selenium_cookies:
        sess.cookies.set(c["name"], c["value"], domain=c.get("domain", ".line.me"))

    sess.headers.update({
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "X-Atom-Api-Version": "250701_R",
        "Referer": "https://pay.line.me/epi/center?locale=zh_TW",
        "Origin": "https://pay.line.me",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    return sess

def load_pos_daily_for_store(store_key, food_sheets_path=FOOD_SHEETS_PATH):
    """從門市 Google Sheets 快取檔載入該店逐日 LINE Pay 申報金額"""
    pos_daily = {}
    if os.path.exists(food_sheets_path):
        try:
            with open(food_sheets_path, "r", encoding="utf-8") as f:
                fs = json.load(f)
            s_data = fs.get("stores", {}).get(store_key, {})
            for m, days in s_data.get("daily_by_month", {}).items():
                for d in days:
                    pos_daily[d["date"]] = float(d.get("linepay") or 0.0)
        except Exception as e:
            logging.warning(f"讀取 {store_key} 門市 POS 檔案失敗: {e}")
    return pos_daily

def generate_weekly_cycles(daily_records, settlements, pos_daily):
    """
    隨意小吃核銷規則：
    - 結帳週期：每週一至每週日的帳務
    - 週一清算
    - 週二撥款
    - 清算後金額 2.31% 手續費 (四捨五入)
    - 週二撥款金額 = 清算金額 - 手續費
    """
    payout_settles = {}
    for s in settlements:
        payout_settles.setdefault(s["schedule_date"], []).append(s)

    start_mon = datetime.strptime("2026-08-24", "%Y-%m-%d")
    today = datetime.now()
    today_str = today.strftime("%Y-%m-%d")
    cur_mon = today - timedelta(days=today.weekday())

    cycles = []
    c = start_mon
    w_idx = 1
    while c <= cur_mon:
        c_sun = c + timedelta(days=6)
        c_settle = c + timedelta(days=7)
        c_payout = c + timedelta(days=8)

        mon_s = c.strftime("%Y-%m-%d")
        sun_s = c_sun.strftime("%Y-%m-%d")
        settle_s = c_settle.strftime("%Y-%m-%d")
        payout_s = c_payout.strftime("%Y-%m-%d")

        d_cur = c
        week_pos = 0.0
        week_pay = 0.0
        week_can = 0.0
        week_net = 0.0
        days_list = []

        while d_cur <= c_sun:
            ds = d_cur.strftime("%Y-%m-%d")
            pos_v = pos_daily.get(ds, 0.0)
            rec = daily_records.get(ds, {})
            bp = float(rec.get("payment_amount") or 0.0)
            bc = float(rec.get("cancel_amount") or 0.0)
            bn = float(rec.get("net_amount") or 0.0)
            cnt = int(rec.get("payment_count") or 0)

            week_pos += pos_v
            week_pay += bp
            week_can += bc
            week_net += bn

            is_future_or_ongoing = (ds > today_str) or (ds == today_str and pos_v == 0)
            day_diff = 0 if is_future_or_ongoing else round(pos_v - bn)

            days_list.append({
                "date": ds,
                "dow": ["週一", "週二", "週三", "週四", "週五", "週六", "週日"][d_cur.weekday()],
                "pos_amount": pos_v,
                "payment_amount": bp,
                "cancel_amount": bc,
                "net_amount": bn,
                "count": cnt,
                "diff": day_diff
            })
            d_cur += timedelta(days=1)

        s_items = payout_settles.get(payout_s, [])
        is_paid = len(s_items) > 0

        target_amt = sum(s["target_amount"] for s in s_items) if is_paid else week_net
        act_fee = sum(s["fee_amount"] for s in s_items) if is_paid else None
        act_payout = sum(s["schedule_amount"] for s in s_items) if is_paid else None

        calc_fee = round(target_amt * 0.0231)
        calc_payout = target_amt - calc_fee

        if is_paid:
            status = "PAID"
            status_label = "🟢 已撥款入帳"
            status_badge = "green"
        elif today_str > sun_s:
            if today_str >= payout_s:
                status = "PENDING_PAYOUT"
                status_label = "🟡 待撥款入帳"
                status_badge = "yellow"
            else:
                status = "PENDING_SETTLE"
                status_label = "🟡 待週一清算"
                status_badge = "yellow"
        else:
            status = "ONGOING"
            status_label = f"🔵 本週營業進行中 (預計 {payout_s.split('-')[1]}/{payout_s.split('-')[2]} 撥款)"
            status_badge = "blue"

        base_reconcile = target_amt if is_paid else week_net
        diff_val = 0 if status == "ONGOING" else round(week_pos - base_reconcile)

        cycles.append({
            "week_index": w_idx,
            "week_id": f"W-{settle_s.replace('-', '')}",
            "cycle_period": f"{mon_s} ~ {sun_s}",
            "cycle_start": mon_s,
            "cycle_end": sun_s,
            "settle_date": settle_s,
            "payout_date": payout_s,
            "pos_amount": week_pos,
            "backend_pay": week_pay,
            "backend_cancel": week_can,
            "backend_net": week_net,
            "pos_vs_backend_diff": diff_val,
            "settle_target_amount": target_amt,
            "fee_rate": 0.0231,
            "calc_fee_231": calc_fee,
            "calc_payout": calc_payout,
            "is_paid": is_paid,
            "actual_fee": act_fee,
            "actual_payout": act_payout,
            "fee_tolerance_diff": round(act_fee - calc_fee) if is_paid else 0,
            "payout_diff": round(act_payout - calc_payout) if is_paid else 0,
            "status": status,
            "status_label": status_label,
            "status_badge": status_badge,
            "settlement_batches": s_items,
            "days": days_list
        })
        w_idx += 1
        c += timedelta(days=7)

    return cycles

def get_t2_payout_date(d_str):
    """計算 T+2 撥款日 (遇週末/國定假日順延)"""
    dt = datetime.strptime(d_str, "%Y-%m-%d")
    wd = dt.weekday() # Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5, Sun=6

    if d_str == "2026-10-07": # 遇 10/09 國慶補假順延至下週一
        return "2026-10-12"

    if wd == 0:   # 週一 -> 週三
        p_dt = dt + timedelta(days=2)
    elif wd == 1: # 週二 -> 週四
        p_dt = dt + timedelta(days=2)
    elif wd == 2: # 週三 -> 週五
        p_dt = dt + timedelta(days=2)
    elif wd == 3: # 週四 -> 下週一 (+4天)
        p_dt = dt + timedelta(days=4)
    elif wd == 4: # 週五 -> 下週二 (+4天)
        p_dt = dt + timedelta(days=4)
    elif wd == 5: # 週六 -> 下週二 (+3天)
        p_dt = dt + timedelta(days=3)
    elif wd == 6: # 週日 -> 下週二 (+2天)
        p_dt = dt + timedelta(days=2)

    res = p_dt.strftime("%Y-%m-%d")
    while res in TAIWAN_BANK_HOLIDAYS or p_dt.weekday() >= 5:
        p_dt += timedelta(days=1)
        res = p_dt.strftime("%Y-%m-%d")
    return res

def generate_t2_batches(daily_records, settlements, pos_daily):
    """
    月宮（餐飲）核銷規則：
    - 每日營業款 T+2 撥款 (遇假日順延)
    - 週五、週六、週日 3 日營業款於次週二合併撥款
    - 手續費率：2.31% (四捨五入)
    - 實撥入帳 = 請款金額 - round(請款金額 * 2.31%)
    """
    by_sched = {}
    for s in settlements:
        by_sched.setdefault(s["schedule_date"], []).append(s)

    all_dates = sorted(daily_records.keys())
    used_dates = set()
    today = datetime.now()
    today_str = today.strftime("%Y-%m-%d")

    # 1. 優先將官方已核定之清算撥款批次，精準比對回門市營業日（單日或連續營業日總和）
    settlement_matched_days = {}
    for p_date in sorted(by_sched.keys()):
        if p_date < "2026-08-01":
            continue
        s_items = by_sched[p_date]
        tgt = sum(s["target_amount"] for s in s_items)

        matched_days = []
        if p_date == "2026-08-04":
            matched_days = [d for d in ["2026-08-01", "2026-08-02"] if d in daily_records]
        else:
            for dt in all_dates:
                if dt not in used_dates and dt < p_date and abs(daily_records[dt]["net_amount"] - tgt) < 1:
                    matched_days = [dt]
                    break
            if not matched_days:
                for i in range(len(all_dates)):
                    if all_dates[i] in used_dates or all_dates[i] >= p_date:
                        continue
                    cur_days = []
                    cur_sum = 0
                    for j in range(i, min(i + 6, len(all_dates))):
                        if all_dates[j] in used_dates or all_dates[j] >= p_date:
                            break
                        cur_days.append(all_dates[j])
                        cur_sum += daily_records[all_dates[j]]["net_amount"]
                        if abs(cur_sum - tgt) < 1:
                            matched_days = cur_days
                            break
                    if matched_days:
                        break

        if matched_days:
            for dt in matched_days:
                used_dates.add(dt)
            settlement_matched_days[p_date] = matched_days
        else:
            agg_d = s_items[0].get("aggregation_date")
            settlement_matched_days[p_date] = [agg_d] if agg_d else []

    # 2. 針對尚未生成清算單之近幾日 (如昨日、今日)，以標準 T+2 撥款推算預計撥款日
    pending_by_payout = {}
    for dt in all_dates:
        if dt not in used_dates:
            p_dt = get_t2_payout_date(dt)
            pending_by_payout.setdefault(p_dt, []).append(dt)

    # 3. 彙總全期撥款批次清單
    all_p_dates = sorted(list(set(list(settlement_matched_days.keys()) + list(pending_by_payout.keys()))))
    batches = []
    b_idx = 1

    for p_date in all_p_dates:
        if p_date < "2026-08-01":
            continue

        s_items = by_sched.get(p_date, [])
        is_paid = len(s_items) > 0

        t_days = settlement_matched_days.get(p_date) or pending_by_payout.get(p_date, [])
        t_days_sorted = sorted(list(set([d for d in t_days if d])))
        t_period_str = f"{t_days_sorted[0]} ～ {t_days_sorted[-1]}" if len(t_days_sorted) > 1 else (t_days_sorted[0] if t_days_sorted else "—")

        pos_amt = sum(pos_daily.get(d, 0.0) for d in t_days_sorted)
        b_pay = sum(float(daily_records.get(d, {}).get("payment_amount") or 0.0) for d in t_days_sorted)
        b_can = sum(float(daily_records.get(d, {}).get("cancel_amount") or 0.0) for d in t_days_sorted)
        b_net = b_pay - b_can

        act_target = sum(s["target_amount"] for s in s_items) if is_paid else None
        act_fee = sum(s["fee_amount"] for s in s_items) if is_paid else None
        act_payout = sum(s["schedule_amount"] for s in s_items) if is_paid else None

        base_amt = act_target if is_paid else b_net
        calc_fee = round(base_amt * 0.0231)
        calc_payout = base_amt - calc_fee

        if is_paid:
            status = "PAID"
            status_label = "🟢 已撥款入帳"
            status_badge = "green"
        elif p_date == today_str:
            status = "TODAY_PAYOUT"
            status_label = "🟡 今日撥款入帳"
            status_badge = "yellow"
        elif p_date > today_str:
            if any(d == today_str for d in t_days_sorted):
                status = "ONGOING"
                status_label = f"🔵 今日營業中 (預計 {p_date[5:]} 撥款)"
                status_badge = "blue"
            else:
                status = "PENDING"
                status_label = f"🟡 待撥款 (預計 {p_date[5:]})"
                status_badge = "yellow"
        else:
            status = "PAID"
            status_label = "🟢 已撥款"
            status_badge = "green"

        diff_val = 0 if is_paid else (0 if status == "ONGOING" else round(pos_amt - b_net))

        days_detail = []
        for d in t_days_sorted:
            rec = daily_records.get(d, {})
            dt_obj = datetime.strptime(d, "%Y-%m-%d")
            dow = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"][dt_obj.weekday()]
            p_val = pos_daily.get(d, 0.0)
            pay_v = float(rec.get("payment_amount") or 0.0)
            can_v = float(rec.get("cancel_amount") or 0.0)
            net_v = pay_v - can_v
            is_ongoing_day = (d >= today_str and p_val == 0)
            day_diff = 0 if is_ongoing_day else round(p_val - net_v)

            days_detail.append({
                "date": d,
                "dow": dow,
                "pos_amount": p_val,
                "payment_amount": pay_v,
                "cancel_amount": can_v,
                "net_amount": net_v,
                "diff": day_diff
            })

        p_dow = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"][datetime.strptime(p_date, "%Y-%m-%d").weekday()]

        batches.append({
            "batch_index": b_idx,
            "batch_id": f"T2-{p_date.replace('-', '')}",
            "payout_date": p_date,
            "payout_dow": p_dow,
            "transaction_period": t_period_str,
            "transaction_days": t_days_sorted,
            "pos_amount": pos_amt,
            "backend_pay": b_pay,
            "backend_cancel": b_can,
            "backend_net": b_net,
            "diff": diff_val,
            "target_amount": base_amt,
            "fee_rate": 0.0231,
            "calc_fee_231": calc_fee,
            "calc_payout": calc_payout,
            "actual_fee": act_fee,
            "actual_payout": act_payout,
            "is_paid": is_paid,
            "status": status,
            "status_label": status_label,
            "status_badge": status_badge,
            "settlement_batches": s_items,
            "days": days_detail
        })
        b_idx += 1

    return batches

def fetch_monthly_settlements_and_invoices(sess, active_center_id, s_key, months=None):
    """
    抓取 LINE Pay 官方月結算對帳單與手續費電子發票 (切換按照月份)，
    自動解析電子發票號碼、稅額、手續費、請款金額並下載 PDF 發票備存。
    """
    if months is None:
        months = ["2026-08", "2026-09", "2026-10"]

    url_monthly = "https://merchant-paymoney.line.me/mrc/merchant/settlement/statements/monthly"
    url_inv = "https://merchant-paymoney.line.me/mrc/merchant/center/settlement/statements/monthly/invoice"
    payload = {
        "merchantId": active_center_id.lower(),
        "startYearMonth": min(months),
        "endYearMonth": max(months)
    }

    monthly_invoices = {}
    base_dir = os.path.dirname(os.path.abspath(__file__))
    target_dirs = [
        os.path.join(base_dir, "invoices", s_key),
        os.path.expanduser(f"~/Desktop/腳本資料夾/月下/文中會計傳票產生器/invoices/{s_key}")
    ]
    for d in target_dirs:
        os.makedirs(d, exist_ok=True)

    try:
        logging.info(f"[{s_key}] 正在查詢官方月結算對帳單與電子發票 ({payload['startYearMonth']} ~ {payload['endYearMonth']})...")
        r = sess.post(url_monthly, json=payload)
        if r.status_code == 200:
            stmts = r.json().get("info", {}).get("statements", {})
            for m in sorted(stmts.keys()):
                raw_items = stmts[m]
                parsed_invs = []
                m_target = 0.0
                m_fee = 0.0
                m_tax = 0.0
                m_sched = 0.0
                has_pending_inv = False

                for it in raw_items:
                    sp = it.get("serviceProvider")
                    p_name = "連加網路商業股份有限公司 (信用卡收單)" if sp == "TSP" else "連加電子支付股份有限公司 (一卡通/LINE Pay Money)"
                    target_amt = float(it.get("targetAmount") or 0.0)
                    fee_amt = float(it.get("feeAmount") or 0.0)
                    tax_amt = float(it.get("taxAmount") or 0.0)
                    sched_amt = float(it.get("scheduleAmount") or 0.0)
                    fee_tot = round(fee_amt + tax_amt, 2)

                    m_target += target_amt
                    m_fee += fee_amt
                    m_tax += tax_amt
                    m_sched += sched_amt

                    inv_path = it.get("invoiceFilePathUrl")
                    inv_no = "—"
                    inv_date = "—"
                    pdf_fname = None
                    inv_status = "PENDING_NEXT_MONTH"
                    status_lbl = "⏳ 次月初開立"

                    if inv_path:
                        m_no = re.search(r'([A-Z]{2})(\d{8})', inv_path)
                        if m_no:
                            inv_no = f"{m_no.group(1)}-{m_no.group(2)}"
                        m_d = re.search(r'/(\d{8})_', inv_path)
                        if m_d:
                            ds = m_d.group(1)
                            inv_date = f"{ds[:4]}-{ds[4:6]}-{ds[6:]}"

                        pdf_fname = os.path.basename(inv_path)
                        for d in target_dirs:
                            local_fpath = os.path.join(d, pdf_fname)
                            if not os.path.exists(local_fpath):
                                try:
                                    r_pdf = sess.post(url_inv, json={"invoiceFile": inv_path})
                                    if r_pdf.status_code == 200:
                                        with open(local_fpath, "wb") as pf:
                                            pf.write(r_pdf.content)
                                        logging.info(f"[{s_key}] 成功下載並備存發票 PDF: {pdf_fname}")
                                except Exception as e_pdf:
                                    logging.warning(f"[{s_key}] 下載發票 PDF 失敗: {e_pdf}")

                        inv_status = "ISSUED"
                        status_lbl = "🟢 已開立"
                    else:
                        has_pending_inv = True

                    parsed_invs.append({
                        "service_provider": sp,
                        "provider_name": p_name,
                        "invoice_no": inv_no,
                        "invoice_date": inv_date,
                        "target_amount": target_amt,
                        "fee_amount": fee_amt,
                        "tax_amount": tax_amt,
                        "fee_total": fee_tot,
                        "schedule_amount": sched_amt,
                        "pdf_filename": pdf_fname,
                        "invoice_status": inv_status,
                        "status_label": status_lbl,
                        "aggregation_seq": it.get("settlementAggregationSequence")
                    })

                tot_inv_amt = round(m_fee + m_tax, 2)
                calc_fee_231 = round(m_target * 0.0231)
                fee_diff = round(tot_inv_amt - calc_fee_231)
                is_fee_matched = (abs(fee_diff) <= 3)

                if has_pending_inv:
                    month_status = "PENDING_NEXT_MONTH"
                    month_badge = "blue"
                    month_label = "🔵 當月進行中 (次月初統一開立發票)"
                elif is_fee_matched:
                    month_status = "VERIFIED_OK"
                    month_badge = "green"
                    month_label = "🟢 100% 勾稽吻合無誤"
                else:
                    month_status = "DIFF_FOUND"
                    month_badge = "red"
                    month_label = f"🔴 手續費差異 ${fee_diff}"

                monthly_invoices[m] = {
                    "month": m,
                    "total_target_amount": m_target,
                    "total_fee_amount": m_fee,
                    "total_tax_amount": m_tax,
                    "total_invoice_amount": tot_inv_amt,
                    "total_schedule_amount": m_sched,
                    "calc_fee_231": calc_fee_231,
                    "fee_diff": fee_diff,
                    "is_fee_matched": is_fee_matched,
                    "status": month_status,
                    "status_badge": month_badge,
                    "status_label": month_label,
                    "invoices": parsed_invs
                }
            logging.info(f"[{s_key}] 官方月結算與發票彙總完成：共 {len(monthly_invoices)} 個月份")
        else:
            logging.warning(f"[{s_key}] 抓取月結算單失敗 (HTTP {r.status_code}): {r.text}")
    except Exception as e:
        logging.warning(f"[{s_key}] 抓取月結算單與發票異常: {e}")

    return monthly_invoices

def generate_monthly_cutoff_reconciliations(store_key, daily_records, weekly_cycles, t2_batches, monthly_invoices, pos_daily, months=None):
    """
    計算每月跨月時間差會計調節表 (In-Transit Timing Difference Reconciliation)：
    針對 LINE Pay 官方月發票以「撥款日」為認列基準，而門市以「交易營業日」認列營收所產生之跨月時間差：
    公式：
    調節後應開立發票請款金額 = 本月門市 POS 營收 + 期初在途款項 (上月交易、本月撥款) - 期末在途款項 (本月交易、次月撥款)
    自動核對 2.31% 發票手續費、5% 營業稅與實撥金額，如有任何差異即時產出異常警示。
    """
    if months is None:
        months = ["2026-08", "2026-09", "2026-10"]

    is_sy = (store_key == "suiyi")
    items = weekly_cycles if is_sy else t2_batches
    reconciliations = {}
    today_str = datetime.now().strftime("%Y-%m-%d")
    current_month = today_str[:7]

    for m in months:
        pos_m_days = [d for d in daily_records.keys() if d.startswith(m)]
        pos_tot = sum(pos_daily.get(d, daily_records[d].get("net_amount", 0.0)) for d in pos_m_days)
        backend_net_tot = sum(daily_records[d].get("net_amount", 0.0) for d in pos_m_days)

        beg_in_transit = []
        end_in_transit = []

        for it in items:
            p_date = it.get("payout_date") or ""
            days = it.get("days") or it.get("days_detail") or []
            for d in days:
                dt = d.get("date") or ""
                amt = float(d.get("net_amount") or 0.0)
                if p_date.startswith(m) and dt < f"{m}-01":
                    beg_in_transit.append({
                        "transaction_date": dt,
                        "payout_date": p_date,
                        "amount": amt,
                        "fee_231": round(amt * 0.0231),
                        "note": f"上月 ({dt[:7]}) 營業款於本月 ({p_date}) 撥款，手續費開立於本月發票"
                    })
                elif p_date > f"{m}-31" and dt.startswith(m):
                    end_in_transit.append({
                        "transaction_date": dt,
                        "payout_date": p_date,
                        "amount": amt,
                        "fee_231": round(amt * 0.0231),
                        "note": f"本月 ({dt}) 營業款跨至次月 ({p_date}) 撥款，手續費將開立於次月發票"
                    })

        beg_sum = sum(x["amount"] for x in beg_in_transit)
        beg_fee = sum(x["fee_231"] for x in beg_in_transit)
        end_sum = sum(x["amount"] for x in end_in_transit)
        end_fee = sum(x["fee_231"] for x in end_in_transit)

        reconciled_target = round(pos_tot + beg_sum - end_sum, 2)

        m_inv = monthly_invoices.get(m, {})
        actual_inv_target = m_inv.get("total_target_amount", 0.0)
        target_diff = round(reconciled_target - actual_inv_target) if actual_inv_target > 0 else 0

        actual_inv_fee = m_inv.get("total_invoice_amount", 0.0)
        actual_inv_tax = m_inv.get("total_tax_amount", 0.0)
        calc_fee_231 = round(actual_inv_target * 0.0231) if actual_inv_target > 0 else round(reconciled_target * 0.0231)
        fee_diff = round(actual_inv_fee - calc_fee_231) if actual_inv_fee > 0 else 0
        actual_sched = m_inv.get("total_schedule_amount", 0.0)

        is_current = (m == current_month)
        diagnostic_alerts = []

        if is_current and m_inv.get("status") == "PENDING_NEXT_MONTH":
            status = "ONGOING"
            status_badge = "blue"
            status_label = "🔵 當月進行中 (次月初統一開立發票)"
            diagnostic_alerts.append(f"當月截至目前累計請款 NT$ {actual_inv_target:,.0f}，次月初 (約 11/05) LINE Pay 將統一結算並開立電子發票。")
            if end_sum > 0:
                diagnostic_alerts.append(f"已預先追蹤到本月底在途款 NT$ {end_sum:,.0f} (預計次月撥款)，次月初對帳時將自動從本月發票中扣除並計入下月。")
        elif m == "2026-08":
            status = "SYSTEM_INIT"
            status_badge = "gray"
            status_label = "⚪ 系統啟用初期"
            diagnostic_alerts.append("8月份為系統啟用第一個月，7月份在途款未納入連線查詢範圍，9月份起已全面完整勾稽。")
        elif abs(target_diff) <= 1 and abs(fee_diff) <= 3:
            status = "PERFECT_MATCH"
            status_badge = "green"
            status_label = "🟢 完全吻合 ($0 差額)"
        else:
            status = "DISCREPANCY_ALERT"
            status_badge = "red"
            status_label = f"🔴 差額待查 (請款 {target_diff:+,} / 手續費 {fee_diff:+,})"
            if abs(target_diff) > 1:
                diagnostic_alerts.append(f"【請款總額不符】調節後請款 ${reconciled_target:,.0f} 與發票銷售額 ${actual_inv_target:,.0f} 相差 {target_diff:+,}。請排查是否有未入帳之退款或門市交易漏登。")
            if abs(fee_diff) > 3:
                diagnostic_alerts.append(f"【手續費率異常】發票實收手續費 ${actual_inv_fee:,.0f} 與 2.31% 標準手續費 ${calc_fee_231:,.0f} 相差 ${fee_diff:+,}。")

        reconciliations[m] = {
            "month": m,
            "pos_sales": pos_tot,
            "backend_sales": backend_net_tot,
            "beginning_in_transit_sum": beg_sum,
            "beginning_in_transit_fee": beg_fee,
            "beginning_in_transit_items": beg_in_transit,
            "ending_in_transit_sum": end_sum,
            "ending_in_transit_fee": end_fee,
            "ending_in_transit_items": end_in_transit,
            "reconciled_target_amount": reconciled_target,
            "actual_invoice_target_amount": actual_inv_target,
            "target_diff": target_diff,
            "actual_invoice_fee_amount": actual_inv_fee,
            "actual_invoice_tax_amount": actual_inv_tax,
            "calc_fee_231": calc_fee_231,
            "fee_diff": fee_diff,
            "actual_schedule_payout": actual_sched,
            "status": status,
            "status_badge": status_badge,
            "status_label": status_label,
            "diagnostic_alerts": diagnostic_alerts,
            "invoices": m_inv.get("invoices", [])
        }

    return reconciliations

def fetch_single_store(store_cfg, months=None):
    """抓取單一商戶後台數據"""
    if months is None:
        months = ["2026-08", "2026-09", "2026-10"]

    s_key = store_cfg["store_key"]
    acct = store_cfg["account_id"]
    pw = store_cfg["password"]
    c_id = store_cfg["center_id"]

    logging.info(f"========== 開始同步商戶 [{store_cfg['brand_name']}] ({acct}) ==========")
    cookies = login_and_get_session(acct, pw)
    sess = create_requests_session(cookies)

    # 驗證 Session
    try:
        resp_session = sess.post("https://merchant-paymoney.line.me/mrc/merchant/session/info", json={})
        session_info = resp_session.json().get("info", {})
        active_center_id = session_info.get("centerId") or c_id
        logging.info(f"[{s_key}] Session 驗證有效：Center ID = {active_center_id}")
    except Exception as e:
        logging.warning(f"[{s_key}] Session 查詢警示: {e}")
        active_center_id = c_id

    today_str = datetime.now().strftime("%Y-%m-%d")
    monthly_data = {}
    daily_records = {}
    all_settlements = []

    for m in months:
        y, mon = map(int, m.split("-"))
        start_date = f"{m}-01"
        if mon in [1, 3, 5, 7, 8, 10, 12]:
            last_day = 31
        elif mon in [4, 6, 9, 11]:
            last_day = 30
        else:
            last_day = 29 if y % 4 == 0 else 28

        end_date = f"{m}-{last_day:02d}"
        if end_date > today_str:
            end_date = today_str

        logging.info(f"  正在查詢 {m} 期間交易明細 ({start_date} ~ {end_date})...")

        # 每日交易統計 API
        url_daily = "https://merchant-paymoney.line.me/mrc/merchant/home/payment/summaries/daily"
        payload_daily = {
            "merchantId": active_center_id.lower(),
            "startDate": start_date,
            "endDate": end_date
        }
        r_daily = sess.post(url_daily, json=payload_daily)
        if r_daily.status_code == 200:
            res_info = r_daily.json().get("info", {})
            tot_pay_amt = float(res_info.get("totalPaymentAmount") or 0.0)
            tot_pay_cnt = int(res_info.get("totalPaymentCount") or 0)
            tot_cancel_amt = float(res_info.get("totalCancelAmount") or 0.0)
            tot_cancel_cnt = int(res_info.get("totalCancelCount") or 0)
            net_amt = tot_pay_amt - tot_cancel_amt

            monthly_data[m] = {
                "month": m,
                "start_date": start_date,
                "end_date": end_date,
                "total_payment_amount": tot_pay_amt,
                "total_payment_count": tot_pay_cnt,
                "total_cancel_amount": tot_cancel_amt,
                "total_cancel_count": tot_cancel_cnt,
                "net_amount": net_amt,
                "status": "SUCCESS"
            }

            p_summaries = res_info.get("paymentSummaries", {})
            for d_str, d_info in p_summaries.items():
                pay_amt = float(d_info.get("paymentAmount") or 0.0)
                pay_cnt = int(d_info.get("paymentCount") or 0)
                can_amt = float(d_info.get("cancelAmount") or 0.0)
                can_cnt = int(d_info.get("cancelCount") or 0)
                daily_records[d_str] = {
                    "date": d_str,
                    "payment_amount": pay_amt,
                    "payment_count": pay_cnt,
                    "cancel_amount": can_amt,
                    "cancel_count": can_cnt,
                    "net_amount": pay_amt - can_amt,
                    "has_activity": (pay_cnt > 0 or can_cnt > 0)
                }
            logging.info(f"  {m} 統計完成：實收總額 NT$ {net_amt:,.0f} (付款 ${tot_pay_amt:,.0f} / 退款 ${tot_cancel_amt:,.0f})")
        else:
            logging.warning(f"  {m} 每日統計失敗 (HTTP {r_daily.status_code}): {r_daily.text}")

        # 每日撥款結算單 API
        url_settle = "https://merchant-paymoney.line.me/mrc/merchant/settlement/statements/daily"
        payload_settle = {
            "merchantId": active_center_id.lower(),
            "startDate": start_date,
            "endDate": end_date
        }
        r_settle = sess.post(url_settle, json=payload_settle)
        if r_settle.status_code == 200:
            s_info = r_settle.json().get("info", {})
            statements = s_info.get("statements", {})
            for s_date, s_list in sorted(statements.items()):
                for item in s_list:
                    all_settlements.append({
                        "schedule_date": item.get("scheduleDate") or item.get("originalScheduleDate"),
                        "aggregation_date": item.get("aggregationDate"),
                        "settlement_completion_date": item.get("settlementCompletionDate"),
                        "target_amount": float(item.get("targetAmount") or 0.0),
                        "schedule_amount": float(item.get("scheduleAmount") or 0.0),
                        "fee_amount": round(float(item.get("feeAmount") or 0.0) + float(item.get("taxAmount") or 0.0), 2),
                        "service_provider": item.get("serviceProvider"),
                        "status": item.get("payResultStatusCode"),
                        "aggregation_seq": item.get("settlementAggregationSequence")
                    })
            logging.info(f"  {m} 撥款結算單完成：共 {len(statements)} 個撥款日")
        else:
            logging.warning(f"  {m} 撥款結算單失敗 (HTTP {r_settle.status_code}): {r_settle.text}")

    # 載入門市 POS 金額
    pos_daily = load_pos_daily_for_store(store_cfg["food_sheets_key"])

    # 根據商戶核銷型態生成對帳模型
    if store_cfg["settlement_type"] == "WEEKLY":
        weekly_cycles = generate_weekly_cycles(daily_records, all_settlements, pos_daily)
        t2_batches = []
    else:
        weekly_cycles = []
        t2_batches = generate_t2_batches(daily_records, all_settlements, pos_daily)

    # 抓取官方月結算對帳單與電子發票
    monthly_invoices = fetch_monthly_settlements_and_invoices(sess, active_center_id, s_key, months=months)

    # 計算每月跨月時間差會計調節表與發票即時勾稽
    monthly_reconciliations = generate_monthly_cutoff_reconciliations(
        s_key, daily_records, weekly_cycles, t2_batches, monthly_invoices, pos_daily, months=months
    )

    store_result = {
        "store_key": s_key,
        "merchant_info": {
            "center_id": active_center_id,
            "account_id": acct,
            "brand_name": store_cfg["brand_name"],
            "company_name": store_cfg["company_name"],
            "settlement_type": store_cfg["settlement_type"],
            "settlement_desc": store_cfg["settlement_desc"],
            "fee_rate_standard": store_cfg["fee_rate"],
            "portal_url": "https://pay.line.me/epi/center?locale=zh_TW"
        },
        "monthly_summaries": monthly_data,
        "daily_records": daily_records,
        "settlements": all_settlements,
        "weekly_cycles": weekly_cycles,
        "t2_batches": t2_batches,
        "monthly_invoices": monthly_invoices,
        "monthly_reconciliations": monthly_reconciliations
    }
    return store_result

def sync_all_stores(target_store=None, months=None):
    """同步指定或全部商戶，並整合輸出至 JSON 檔案"""
    if months is None:
        months = ["2026-08", "2026-09", "2026-10"]

    # 讀取現有既有資料以利部分更新
    existing_data = {}
    if os.path.exists(OUTPUT_PATH):
        try:
            with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
        except Exception:
            pass

    existing_stores = existing_data.get("stores", {})

    target_keys = [target_store] if (target_store and target_store in STORE_CONFIGS) else list(STORE_CONFIGS.keys())
    run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"

    for k in target_keys:
        cfg = STORE_CONFIGS[k]
        read_start = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            s_res = fetch_single_store(cfg, months=months)
            read_end = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            s_res["run_id"] = run_id
            s_res["read_started_at"] = read_start
            s_res["read_completed_at"] = read_end
            s_res["fetch_result"] = "SUCCESS"
            s_res["result"] = "SUCCESS"
            s_res["last_success_at"] = read_end
            cutoff = None
            if s_res.get("daily_records"):
                cutoff = max(s_res["daily_records"].keys())
            elif s_res.get("settlements"):
                cutoff = max(st.get("settlement_date", "") for st in s_res["settlements"] if st.get("settlement_date"))
            s_res["data_cutoff_date"] = cutoff or (months[-1] if months else datetime.now().strftime("%Y-%m-%d"))
            existing_stores[k] = s_res
        except Exception as e:
            read_end = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            logging.error(f"同步商戶 [{k}] 出現異常: {e}", exc_info=True)
            if k in existing_stores and isinstance(existing_stores[k], dict):
                existing_stores[k]["fetch_result"] = "FAILED"
                existing_stores[k]["result"] = "FAILED"
                existing_stores[k]["run_id"] = run_id
                existing_stores[k]["last_error"] = str(e)
                existing_stores[k]["read_started_at"] = read_start
                existing_stores[k]["read_completed_at"] = read_end
                existing_stores[k]["last_attempt_at"] = read_end
                # 保留該店既有之 data_cutoff_date 與 last_success_at，嚴禁重設成功時間

    # 封裝總體結果
    sy_data = existing_stores.get("suiyi", {})
    final_output = {
        "run_id": run_id,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stores": existing_stores,
        # 頂層向後相容欄位 (預設保留隨意小吃)
        "merchant_info": sy_data.get("merchant_info", {}),
        "monthly_summaries": sy_data.get("monthly_summaries", {}),
        "daily_records": sy_data.get("daily_records", {}),
        "settlements": sy_data.get("settlements", []),
        "weekly_cycles": sy_data.get("weekly_cycles", [])
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(final_output, f, ensure_ascii=False, indent=2)
    logging.info(f"✅ 全商戶 LINE Pay 數據同步完成！已寫入 {OUTPUT_PATH}")

    # 備份同步至文中目錄
    try:
        with open(BACKUP_COPY_PATH, "w", encoding="utf-8") as f:
            json.dump(final_output, f, ensure_ascii=False, indent=2)
        logging.info(f"✅ 備份複本已寫入 {BACKUP_COPY_PATH}")
    except Exception as e:
        logging.warning(f"寫入備份目錄失敗: {e}")

    return final_output

if __name__ == "__main__":
    t_store = sys.argv[1] if len(sys.argv) > 1 else None
    sync_all_stores(target_store=t_store)
