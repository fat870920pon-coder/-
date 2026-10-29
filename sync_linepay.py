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
        "t2_batches": t2_batches
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

    for k in target_keys:
        cfg = STORE_CONFIGS[k]
        try:
            s_res = fetch_single_store(cfg, months=months)
            existing_stores[k] = s_res
        except Exception as e:
            logging.error(f"同步商戶 [{k}] 出現異常: {e}", exc_info=True)

    # 封裝總體結果
    sy_data = existing_stores.get("suiyi", {})
    final_output = {
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
