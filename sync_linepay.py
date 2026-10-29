#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_linepay.py
---------------
自動化登入 LINE Pay 合作商店後台 (Merchant Center)
爬取並同步「東區隨意小吃」(tpilf.00682206.pos) 之每日交易、退款與清算撥款明細，
輸出至 linepay_merchant_data.json 供財務對帳系統整合比對。
"""

import os
import sys
import time
import json
import logging
from datetime import datetime, date
import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

ACCOUNT_ID = os.environ.get("LINEPAY_ACCOUNT", "tpilf.00682206.pos")
PASSWORD = os.environ.get("LINEPAY_PASSWORD", "Chilling00682206")
OUTPUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "linepay_merchant_data.json")

def login_and_get_session(account=ACCOUNT_ID, password=PASSWORD):
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
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Origin": "https://pay.line.me",
        "Referer": "https://pay.line.me/epi/center?locale=zh_TW"
    })
    return sess

def fetch_linepay_data(months=None):
    """
    抓取指定月份 (預設 2026-09 與 2026-10) 的 LINE Pay 後台數據
    包含：
    1. 商店 Session 與基本資訊
    2. 每日付款/退款統計
    3. 每日撥款清算明細
    """
    if months is None:
        months = ["2026-09", "2026-10"]

    cookies = login_and_get_session()
    sess = create_requests_session(cookies)

    # 1. 驗證 Session 資訊
    resp_session = sess.post("https://merchant-paymoney.line.me/mrc/merchant/session/info", json={})
    session_info = resp_session.json().get("info", {})
    center_id = session_info.get("centerId", "TPILF.00682206.POS")
    logging.info(f"LINE Pay Session 驗證有效：Center ID = {center_id}")

    today_str = datetime.now().strftime("%Y-%m-%d")
    
    monthly_data = {}
    daily_records = {}
    all_settlements = []

    for m in months:
        y, mon = map(int, m.split("-"))
        # 計算該月起迄日
        start_date = f"{m}-01"
        if mon in [1, 3, 5, 7, 8, 10, 12]:
            last_day = 31
        elif mon in [4, 6, 9, 11]:
            last_day = 30
        else:
            last_day = 29 if y % 4 == 0 else 28

        end_date = f"{m}-{last_day:02d}"
        if end_date > today_str:
            end_date = today_str  # 不得超過當日

        logging.info(f"正在查詢 {m} 期間交易明細 ({start_date} ~ {end_date})...")

        # 每日交易統計 API
        url_daily = "https://merchant-paymoney.line.me/mrc/merchant/home/payment/summaries/daily"
        payload_daily = {
            "merchantId": center_id.lower(),
            "startDate": start_date,
            "endDate": end_date
        }
        r_daily = sess.post(url_daily, json=payload_daily)
        if r_daily.status_code == 200:
            res_info = r_daily.json().get("info", {})
            tot_pay_amt = res_info.get("totalPaymentAmount") or 0.0
            tot_pay_cnt = res_info.get("totalPaymentCount") or 0
            tot_cancel_amt = res_info.get("totalCancelAmount") or 0.0
            tot_cancel_cnt = res_info.get("totalCancelCount") or 0
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

        # 清算撥款每日結算單 API
        url_settle = "https://merchant-paymoney.line.me/mrc/merchant/settlement/statements/daily"
        payload_settle = {
            "merchantId": center_id.lower(),
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
                        "settlement_completion_date": item.get("settlementCompletionDate"),
                        "target_amount": float(item.get("targetAmount") or 0.0),
                        "schedule_amount": float(item.get("scheduleAmount") or 0.0),
                        "fee_amount": round(float(item.get("targetAmount") or 0.0) - float(item.get("scheduleAmount") or 0.0), 2),
                        "service_provider": item.get("serviceProvider"),
                        "status": item.get("payResultStatusCode"),
                        "aggregation_seq": item.get("settlementAggregationSequence")
                    })
            logging.info(f"  {m} 撥款結算單完成：共 {len(statements)} 個撥款日")
        else:
            logging.warning(f"  {m} 撥款結算單失敗 (HTTP {r_settle.status_code}): {r_settle.text}")

    result = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "merchant_info": {
            "center_id": center_id,
            "brand_name": "東區隨意小吃",
            "company_name": "頑全公關顧問股份有限公司隨意小吃本店",
            "portal_url": "https://pay.line.me/epi/center?locale=zh_TW",
            "fee_rate_standard": 0.0231
        },
        "monthly_summaries": monthly_data,
        "daily_records": daily_records,
        "settlements": all_settlements
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    logging.info(f"✅ LINE Pay 後台數據同步完成！已寫入 {OUTPUT_PATH}")
    return result

if __name__ == "__main__":
    fetch_linepay_data()
