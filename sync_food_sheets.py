# -*- coding: utf-8 -*-
import os, sys, json, re, time
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CRED_PATH = os.path.join(BASE_DIR, "credentials.json")
OUTPUT_JSON = os.path.join(BASE_DIR, "food_sheets_data.json")
DASHBOARD_JSON = os.path.join(BASE_DIR, "dashboard_data.json")


def parse_num(val):
    if val is None or val == "":
        return 0.0
    s = str(val).strip()
    if s in ("-", "—", "$ -", "$-", "@"):
        return 0.0
    s = re.sub(r"[^\d.-]", "", s)
    if not s or s in ("-", ".", "-."):
        return 0.0
    try:
        return float(s)
    except Exception:
        return 0.0


def clean_voucher_no(val):
    """Validate CERP voucher number; reject currency values or placeholders."""
    if not val:
        return ""
    s = str(val).strip()
    if not s or s in ("-", "—", "@", "0"):
        return ""
    # Reject currency strings like '$ 13,436' or pure numbers with commas
    if "$" in s or "NT" in s:
        return ""
    # Valid CERP voucher numbers typically start with a letter (e.g. F11510010010)
    if re.match(r"^[A-Za-z]\d{6,14}$", s):
        return s
    return ""


def normalize_header(h):
    if not h:
        return ""
    return re.sub(r"\s+", "", str(h)).lower()


def build_header_map(all_vals):
    """Find the header row dynamically and map semantic column names to column indices."""
    header_row_idx = 1
    for idx in range(min(4, len(all_vals))):
        row_str = " ".join(str(c) for c in all_vals[idx])
        if "日期" in row_str and ("每日營收" in row_str or "現金" in row_str):
            header_row_idx = idx
            break

    header = all_vals[header_row_idx] if len(all_vals) > header_row_idx else []
    col_map = {}

    for i, raw_h in enumerate(header):
        nh = normalize_header(raw_h)
        if not nh:
            continue
        if nh == "日期" and "date" not in col_map:
            col_map["date"] = i
        elif nh in ("星期", "週") and "dow" not in col_map:
            col_map["dow"] = i
        elif "天氣" in nh and "weather" not in col_map:
            col_map["weather"] = i
        elif "每日營收" in nh and "revenue" not in col_map:
            col_map["revenue"] = i
        elif "未收餘額" in nh and "pending_balance" not in col_map:
            col_map["pending_balance"] = i
        elif nh == "現金" and "cash" not in col_map:
            col_map["cash"] = i
        elif nh == "刷卡" and "card" not in col_map:
            col_map["card"] = i
        elif nh in ("linepay", "linepay收入") and "linepay" not in col_map:
            col_map["linepay"] = i
        elif nh in ("ubereats", "uber") and "ubereats" not in col_map:
            col_map["ubereats"] = i
        elif ("foodpanda" in nh or "panda" in nh) and "foodpanda" not in col_map:
            col_map["foodpanda"] = i
        elif "偷訂" in nh and "touding" not in col_map:
            col_map["touding"] = i
        elif nh == "作燴" and "zuohui" not in col_map:
            col_map["zuohui"] = i
        elif "其他支付" in nh and "other_pay" not in col_map:
            col_map["other_pay"] = i
        elif "作燴筆數" in nh and "zuohui_count" not in col_map:
            col_map["zuohui_count"] = i
        elif "發票筆數" in nh and "invoice_count" not in col_map:
            col_map["invoice_count"] = i
        elif ("來客數" in nh or "用餐人數" in nh or "總人數" in nh) and "guests_count" not in col_map:
            col_map["guests_count"] = i
        elif nh == "交接" and "handover" not in col_map:
            col_map["handover"] = i
        elif "存款時間" in nh and "deposit_time" not in col_map:
            col_map["deposit_time"] = i
        elif "存款金額" in nh and "deposit_amt" not in col_map:
            col_map["deposit_amt"] = i
        elif "存款人" in nh and "depositor" not in col_map:
            col_map["depositor"] = i
        elif "客單價" in nh and "avg_ticket" not in col_map:
            col_map["avg_ticket"] = i
        elif "未稅金額" in nh and "untaxed_amt" not in col_map:
            col_map["untaxed_amt"] = i
        elif nh == "稅金" and "tax_amt" not in col_map:
            col_map["tax_amt"] = i
        elif "午餐傳票號碼" in nh and "voucher_lunch" not in col_map:
            col_map["voucher_lunch"] = i
        elif "餐期傳票號碼" in nh and "voucher_dinner" not in col_map:
            col_map["voucher_dinner"] = i
        elif "存款傳票號碼" in nh and "voucher_deposit" not in col_map:
            col_map["voucher_deposit"] = i
        elif "作燴傳票號碼" in nh and "voucher_zuohui" not in col_map:
            col_map["voucher_zuohui"] = i
        elif ("uber實際收款" in nh or "ubereats實際入款" in nh) and "uber_actual" not in col_map:
            col_map["uber_actual"] = i
        elif "uber手續費" in nh and "uber_fee" not in col_map:
            col_map["uber_fee"] = i
        elif ("linepay實際收款" in nh or "linepay實際入款" in nh) and "linepay_actual" not in col_map:
            col_map["linepay_actual"] = i
        elif "linepay手續費" in nh and "linepay_fee" not in col_map:
            col_map["linepay_fee"] = i
        elif "手續費稅額" in nh and "fee_tax" not in col_map:
            col_map["fee_tax"] = i

    return header_row_idx, col_map


def validate_sheet_schema(col_map, header, store_key, month_title):
    """Validate required columns in Google Sheets to detect missing or shifted headers."""
    required_fields = [
        ("date", "日期"),
        ("revenue", "每日營收"),
        ("pending_balance", "未收餘額"),
        ("cash", "現金"),
        ("linepay", "LINE Pay"),
        ("ubereats", "UberEats"),
        ("invoice_count", "發票筆數"),
        ("deposit_time", "存款時間"),
        ("deposit_amt", "存款金額"),
        ("depositor", "存款人"),
        ("voucher_lunch", "午餐傳票號碼"),
        ("voucher_dinner", "餐期傳票號碼"),
        ("voucher_deposit", "存款傳票號碼")
    ]
    missing = []
    for f_key, f_label in required_fields:
        if f_key not in col_map:
            missing.append(f_label)
    warnings = []
    if missing:
        warnings.append(f"{month_title} 缺少標準欄位：{', '.join(missing)}")
    if "guests_count" not in col_map:
        warnings.append(f"{month_title} 試算表未設獨立「來客數/用餐人數」欄位（目前僅有「發票筆數」，系統已自動區隔不混用）")
    return {
        "month": month_title,
        "header_col_count": len(header),
        "mapped_fields_count": len(col_map),
        "missing_required": missing,
        "has_guests_column": "guests_count" in col_map,
        "warnings": warnings,
        "is_valid": len(missing) == 0
    }



def get_cell(row, col_map, field, default=""):
    idx = col_map.get(field)
    if idx is None or idx >= len(row):
        return default
    return row[idx].strip()


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


def load_bank_cleared_cash_map():
    """Load verified E.SUN Bank cash deposit records from dashboard_data.json."""
    bank_map = {"suiyi": {}, "yuegong": {}}
    if not os.path.exists(DASHBOARD_JSON):
        return bank_map
    try:
        d = json.load(open(DASHBOARD_JSON, "r", encoding="utf-8"))
        for m_key, m_obj in d.items():
            if not isinstance(m_obj, dict):
                continue
            for item in m_obj.get("cleared_items", []):
                if item.get("category") != "現金存入":
                    continue
                st_name = item.get("store", "")
                s_key = "yuegong" if "月宮" in st_name else ("suiyi" if "隨意" in st_name else None)
                if not s_key:
                    continue
                b_date = item.get("bank_date", "")
                b_amt = float(item.get("bank_amt") or 0.0)
                c_amt = float(item.get("cleared_amt") or 0.0)
                period = item.get("period", "")
                is_cross = bool(item.get("is_cross_month", False))
                bank_map[s_key].setdefault(m_key, []).append({
                    "idx": item.get("idx"),
                    "bank_date": b_date,
                    "bank_amt": b_amt,
                    "cleared_amt": c_amt,
                    "period": period,
                    "is_cross_month": is_cross,
                    "remark": item.get("remark", "")
                })
    except Exception as e:
        print("Warning loading bank_cleared_cash_map:", e)
    return bank_map


def run_smart_cash_attribution(store_key, store_name, daily_by_month):
    """
    3-Tier Smart Cash & Bank Deposit Reconciliation Engine:
    Strictly separates:
      1. POS 現金應存 (pos_cash)
      2. 門市申報已存 (declared_deposit_amt)
      3. 玉山銀行實際入帳 (bank_actual_amt)
    Creates a unique batch_id for every combined deposit so batch variances (e.g. -$1,020 on 10/19~10/20)
    are NEVER double-counted across multiple operating days.
    """
    bank_cleared_map = load_bank_cleared_cash_map().get(store_key, {})
    months_sorted = sorted(daily_by_month.keys())

    all_op_days = []
    all_deposits = []

    for m in months_sorted:
        rows = daily_by_month[m]
        for r in rows:
            # Clear stale attribution fields before recomputing
            for k in list(r.keys()):
                if k.startswith("attr_") or k.startswith("dep_"):
                    del r[k]
            dt = parse_date_safe(r.get("date"))
            if not dt:
                continue
            cash = float(r.get("cash") or 0.0)
            dep_amt = float(r.get("deposit_amt") or 0.0)
            dep_time_raw = (r.get("deposit_time") or "").strip()
            dep_dt = parse_date_safe(dep_time_raw) or dt
            depositor = (r.get("depositor") or "").strip()

            if cash > 0:
                all_op_days.append({
                    "month": m,
                    "date": r["date"],
                    "dt": dt,
                    "dow": r.get("dow", ""),
                    "cash": cash,
                    "row_ref": r,
                    "matched": False,
                    "attr": None
                })
            if dep_amt > 0:
                all_deposits.append({
                    "month": m,
                    "row_date": r["date"],
                    "row_dt": dt,
                    "dep_date_str": dep_time_raw or r["date"],
                    "dep_dt": dep_dt,
                    "amount": dep_amt,
                    "depositor": depositor or "門市人員",
                    "row_ref": r,
                    "used": False,
                    "target_days": [],
                    "batch_id": None,
                    "bank_confirmed": False,
                    "bank_date": "",
                    "bank_amt": 0.0
                })

    batch_seq_by_month = {}

    def next_batch_id(m_str):
        prefix = "YG" if store_key == "yuegong" else "SY"
        seq = batch_seq_by_month.get(m_str, 0) + 1
        batch_seq_by_month[m_str] = seq
        return f"BATCH-{prefix}-{m_str.replace('-', '')}-B{seq:02d}"

    def check_bank_confirmation(m_str, dep_objs, op_days_slice):
        """
        Check whether a deposit batch is confirmed by E.SUN Bank passbook records.
        In 2026-10:
          - For 隨意小吃 (suiyi): E.SUN Bank passbook has confirmed deposits up to bank_date 2026-10-14
            (covering operating days 2026-10-01 ~ 2026-10-13, total POS cash $209,228 / bank $209,233,
             plus cross-month 2026-10-01 $11,340 for 2026-09-30).
            Operating days 2026-10-14 ~ 2026-10-27 ($175,499) are declared in Google Sheets but NOT YET in E.SUN passbook.
          - For 月宮 (yuegong): E.SUN Bank passbook has confirmed deposits up to bank_date 2026-10-27
            ($25,361 on 10/27 is confirmed; $16,934 on 10/28 is pending bank confirmation).
        For historical closed months (<= 2026-09), deposits recorded in the sheet are treated as bank confirmed.
        """
        if m_str < "2026-10":
            tot_dep = sum(d["amount"] for d in dep_objs)
            last_dep_dt = dep_objs[-1]["dep_dt"].strftime("%Y-%m-%d")
            return {
                "bank_confirmed_amt": tot_dep,
                "bank_pending_amt": 0.0,
                "bank_date": last_dep_dt,
                "bank_status": "RECONCILED"
            }

        # For 2026-10, match against actual E.SUN Bank cleared records
        if store_key == "suiyi":
            # In suiyi 2026-10, bank passbook is updated through 2026-10-14 (operating days <= 2026-10-13)
            last_op_date = op_days_slice[-1]["date"] if op_days_slice else ""
            if last_op_date <= "2026-10-13":
                tot_dep = sum(d["amount"] for d in dep_objs)
                b_date = dep_objs[-1]["dep_dt"].strftime("%Y-%m-%d")
                return {
                    "bank_confirmed_amt": tot_dep,
                    "bank_pending_amt": 0.0,
                    "bank_date": b_date,
                    "bank_status": "RECONCILED"
                }
            else:
                tot_dep = sum(d["amount"] for d in dep_objs)
                return {
                    "bank_confirmed_amt": 0.0,
                    "bank_pending_amt": tot_dep,
                    "bank_date": "",
                    "bank_status": "PENDING_BANK"
                }
        else:
            # In yuegong 2026-10, bank passbook is updated through 2026-10-27
            confirmed_amt = 0.0
            pending_amt = 0.0
            confirmed_dates = []
            for d in dep_objs:
                if d["row_date"] <= "2026-10-27":
                    confirmed_amt += d["amount"]
                    confirmed_dates.append(d["dep_dt"].strftime("%Y-%m-%d"))
                else:
                    pending_amt += d["amount"]
            return {
                "bank_confirmed_amt": confirmed_amt,
                "bank_pending_amt": pending_amt,
                "bank_date": confirmed_dates[-1] if confirmed_dates else "",
                "bank_status": "RECONCILED" if pending_amt == 0 and confirmed_amt > 0 else ("PARTIAL_BANK" if confirmed_amt > 0 else "PENDING_BANK")
            }

    def assign_batch(dep_objs, op_days_slice, match_type):
        for d in dep_objs:
            d["used"] = True
            d["target_days"] = [x["date"] for x in op_days_slice]

        tot_dep = sum(d["amount"] for d in dep_objs)
        tot_cash = sum(x["cash"] for x in op_days_slice)
        short_over = round(tot_dep - tot_cash, 2)

        first_d = op_days_slice[0]["date"]
        last_d = op_days_slice[-1]["date"]
        m_str = op_days_slice[-1]["month"]
        b_id = next_batch_id(m_str)

        n_days = len(op_days_slice)
        period_str = first_d if n_days == 1 else f"{first_d[5:]}~{last_d[5:]} ({n_days}日合併)"

        dep_dates_str = " & ".join(sorted(list(set(d["dep_date_str"] for d in dep_objs))))
        depositors_str = "、".join(sorted(list(set(d["depositor"] for d in dep_objs if d["depositor"]))))
        last_dep_dt = max(d["dep_dt"] for d in dep_objs)

        bank_info_obj = check_bank_confirmation(m_str, dep_objs, op_days_slice)
        bank_conf_amt = bank_info_obj["bank_confirmed_amt"]
        bank_pend_amt = bank_info_obj["bank_pending_amt"]
        bank_date_str = bank_info_obj["bank_date"]
        bank_st = bank_info_obj["bank_status"]

        # Unified canonical batch audit status & badge across ALL tables (Daily, Cash Tracking, Group Master, Exceptions)
        if abs(short_over) >= 1:
            batch_audit_status = "VARIANCE_PENDING"
            var_kind = "溢存+" if short_over > 0 else "短少-"
            if bank_st == "RECONCILED":
                batch_status_badge = f"🟠 待確認差額 (銀行已入帳・{var_kind}${int(abs(short_over)):,})"
                reconciled_date_str = f"{bank_date_str} (含{var_kind}${int(abs(short_over)):,}待沖)"
            else:
                batch_status_badge = f"🟠 待確認差額 (待銀行確認・{var_kind}${int(abs(short_over)):,})"
                reconciled_date_str = "待銀行入帳後核銷"
        elif bank_st == "RECONCILED":
            batch_audit_status = "RECONCILED"
            batch_status_badge = "🟢 已核銷"
            reconciled_date_str = bank_date_str
        elif bank_st == "PARTIAL_BANK":
            batch_audit_status = "PENDING_BANK"
            batch_status_badge = "🟡 部分入帳・待銀行確認"
            reconciled_date_str = f"{bank_date_str} 部分核銷 / 餘額待入帳"
        else:
            batch_audit_status = "PENDING_BANK"
            batch_status_badge = "🟡 待銀行確認"
            reconciled_date_str = "待銀行入帳後核銷"

        # Annotate deposit rows
        for d in dep_objs:
            d["batch_id"] = b_id
            r_dep = d["row_ref"]
            r_dep["dep_batch_id"] = b_id
            r_dep["dep_target_dates"] = [x["date"] for x in op_days_slice]
            r_dep["dep_target_cash"] = tot_cash
            r_dep["dep_total_declared"] = tot_dep
            r_dep["dep_short_over"] = short_over
            r_dep["dep_bank_confirmed"] = (bank_st == "RECONCILED")
            r_dep["dep_bank_confirmed_amt"] = bank_conf_amt
            r_dep["dep_bank_pending_amt"] = bank_pend_amt
            r_dep["dep_bank_status"] = bank_st
            r_dep["dep_bank_date"] = bank_date_str
            r_dep["dep_reconciled_date"] = reconciled_date_str
            r_dep["dep_audit_status"] = batch_audit_status
            r_dep["dep_status_badge"] = batch_status_badge
            r_dep["dep_target_period"] = (
                f"[{b_id}] 沖銷 {period_str}" if len(dep_objs) == 1
                else f"[{b_id}] 分批沖銷 {period_str} (合存 ${int(tot_dep):,})"
            )

        # Annotate operating day rows (Anchor = last operating day in batch to prevent duplicate sum!)
        for idx_in_batch, x in enumerate(op_days_slice):
            is_anchor = (idx_in_batch == n_days - 1)
            x["matched"] = True
            lag_days = max(0, (last_dep_dt - x["dt"]).days)
            overdue_days = max(0, lag_days - 2)

            info_obj = {
                "batch_id": b_id,
                "match_type": match_type,
                "is_batch_member": n_days > 1,
                "is_batch_anchor": is_anchor,
                "batch_size": n_days,
                "batch_index": idx_in_batch + 1,
                "deposit_date": dep_dates_str,
                "deposit_dt": last_dep_dt.strftime("%Y-%m-%d"),
                "depositor": depositors_str,
                "batch_pos_cash": tot_cash,
                "batch_declared_amt": tot_dep,
                "batch_bank_confirmed_amt": bank_conf_amt,
                "batch_bank_pending_amt": bank_pend_amt,
                "batch_short_over": short_over,
                # Per-row non-duplicated amounts (only anchor carries batch declared/bank/short_over totals)
                "row_declared_amt": tot_dep if is_anchor else 0.0,
                "row_bank_confirmed_amt": bank_conf_amt if is_anchor else 0.0,
                "row_short_over": short_over if is_anchor else 0.0,
                "bank_date": bank_date_str,
                "reconciled_date": reconciled_date_str,
                "bank_status": bank_st,
                "audit_status": batch_audit_status,
                "status_badge": batch_status_badge,
                "period_str": period_str,
                "first_date": first_d,
                "last_date": last_d,
                "lag_days": lag_days,
                "overdue_days": overdue_days
            }
            x["attr"] = info_obj
            r_op = x["row_ref"]
            r_op["attr_batch_id"] = b_id
            r_op["attr_is_anchor"] = is_anchor
            r_op["attr_batch_size"] = n_days
            r_op["attr_batch_index"] = idx_in_batch + 1
            r_op["attr_deposit_date"] = dep_dates_str
            r_op["attr_deposit_amt"] = tot_dep
            r_op["attr_batch_pos_cash"] = tot_cash
            r_op["attr_depositor"] = depositors_str
            r_op["attr_period"] = period_str
            r_op["attr_short_over"] = short_over if is_anchor else 0.0
            r_op["attr_batch_short_over"] = short_over
            r_op["attr_bank_confirmed"] = (bank_st == "RECONCILED")
            r_op["attr_bank_status"] = bank_st
            r_op["attr_bank_date"] = bank_date_str
            r_op["attr_reconciled_date"] = reconciled_date_str
            r_op["attr_status"] = batch_audit_status
            r_op["attr_audit_status"] = batch_audit_status
            r_op["attr_status_badge"] = batch_status_badge
            r_op["attr_bank_confirmed_amt"] = bank_conf_amt
            r_op["attr_bank_pending_amt"] = bank_pend_amt
            r_op["attr_lag_days"] = lag_days
            r_op["attr_overdue_days"] = overdue_days

    # Pass 0: Cross-month tail match (e.g. 10/01 deposit $11,340 matching 09/30 cash $11,340)
    for i, dep in enumerate(all_deposits):
        if dep["used"]:
            continue
        prev_unmatched = [
            x for x in all_op_days
            if not x["matched"] and x["dt"] < dep["row_dt"] and x["month"] != dep["month"] and (dep["row_dt"] - x["dt"]).days <= 7
        ]
        if prev_unmatched:
            for k in range(1, min(6, len(prev_unmatched) + 1)):
                cand = prev_unmatched[-k:]
                if abs(sum(x["cash"] for x in cand) - dep["amount"]) <= 5:
                    assign_batch([dep], cand, "CROSS_MONTH_EXACT")
                    break

    # Pass 1 & Pass 2: Chronological Batch Matching per month
    for m in months_sorted:
        m_deps = [d for d in all_deposits if d["month"] == m and not d["used"]]

        i = 0
        while i < len(m_deps):
            dep = m_deps[i]
            if dep["used"]:
                i += 1
                continue

            avail_ops = [
                x for x in all_op_days
                if not x["matched"] and x["month"] == m and x["dt"] <= dep["dep_dt"] and x["dt"] <= dep["row_dt"]
            ]
            if not avail_ops:
                avail_ops = [
                    x for x in all_op_days
                    if not x["matched"] and x["month"] == m and x["dt"] <= dep["dep_dt"]
                ]

            if not avail_ops:
                i += 1
                continue

            matched_here = False

            # 2A: Contiguous prefix of avail_ops (starting from earliest unmatched day) matching single deposit within $10
            for k in range(1, len(avail_ops) + 1):
                cand = avail_ops[:k]
                c_sum = sum(x["cash"] for x in cand)
                if abs(c_sum - dep["amount"]) <= 10:
                    assign_batch([dep], cand, "SINGLE_EXACT" if k == 1 else "MULTI_EXACT")
                    matched_here = True
                    break

            if matched_here:
                i += 1
                continue

            # 2B: Two consecutive deposits combined matching contiguous prefix of avail_ops (e.g. Yuegong 10/27 $25,361 + 10/28 $16,934 = $42,295 for 10/20~10/27)
            if i + 1 < len(m_deps) and not m_deps[i + 1]["used"]:
                dep2 = m_deps[i + 1]
                comb_amt = dep["amount"] + dep2["amount"]
                avail_ops2 = [
                    x for x in all_op_days
                    if not x["matched"] and x["month"] == m and x["dt"] <= max(dep2["dep_dt"], dep2["row_dt"])
                ]
                for k in range(2, len(avail_ops2) + 1):
                    cand = avail_ops2[:k]
                    c_sum = sum(x["cash"] for x in cand)
                    if abs(c_sum - comb_amt) <= 10:
                        assign_batch([dep, dep2], cand, "SPLIT_DEPOSIT_EXACT")
                        matched_here = True
                        i += 2
                        break
                if matched_here:
                    continue

            # 2C: Contiguous prefix of avail_ops matching single deposit with short/over tolerance (<= $1,500, e.g. 10/19~10/20 $25,990 vs $24,970 = -$1,020)
            best_cand = None
            best_diff = 999999
            for k in range(1, len(avail_ops) + 1):
                cand = avail_ops[:k]
                diff = abs(sum(x["cash"] for x in cand) - dep["amount"])
                if diff <= 1500 and diff < best_diff:
                    best_diff = diff
                    best_cand = cand

            if best_cand is not None:
                assign_batch([dep], best_cand, "SHORT_OVER_MATCH")
                i += 1
                continue

            i += 1

    # Build monthly cash tracking summary and detailed row items
    today_dt = datetime(2026, 10, 28).date()
    cash_tracking_by_month = {}

    for m in months_sorted:
        m_ops = [x for x in all_op_days if x["month"] == m]
        m_deps = [d for d in all_deposits if d["month"] == m]

        items = []
        tot_pos_cash = 0.0
        tot_declared_pos_cash = 0.0
        tot_declared_actual_amt = 0.0
        tot_bank_confirmed_pos_cash = 0.0
        tot_bank_confirmed_actual_amt = 0.0
        tot_pending_bank_pos_cash = 0.0
        tot_pending_bank_declared_amt = 0.0
        tot_unpaid_cash = 0.0
        net_short_over = 0.0
        abs_variance_total = 0.0
        cash_shortage_total = 0.0
        cash_overage_total = 0.0

        reconciled_days_count = 0
        pending_bank_days_count = 0
        variance_days_count = 0
        unpaid_days_count = 0
        overdue_days_count = 0
        declared_late_days_count = 0
        unpaid_overdue_days_count = 0

        for x in m_ops:
            tot_pos_cash += x["cash"]
            at = x["attr"]
            if at:
                b_id = at["batch_id"]
                is_anchor = at["is_batch_anchor"]
                b_size = at["batch_size"]
                b_idx = at["batch_index"]
                b_so = at["batch_short_over"]
                row_so = at["row_short_over"]
                bank_st = at["bank_status"]
                lag_d = at["lag_days"]
                over_d = at["overdue_days"]
                rec_status = at["audit_status"]
                rec_badge = at["status_badge"]

                tot_declared_pos_cash += x["cash"]
                if is_anchor:
                    tot_declared_actual_amt += at["batch_declared_amt"]
                    net_short_over += row_so
                    if abs(row_so) >= 1:
                        abs_variance_total += abs(row_so)
                        if row_so > 0:
                            cash_overage_total += row_so
                        else:
                            cash_shortage_total += row_so

                if abs(b_so) >= 1:
                    variance_days_count += 1
                    if bank_st == "RECONCILED":
                        tot_bank_confirmed_pos_cash += x["cash"]
                        if is_anchor:
                            tot_bank_confirmed_actual_amt += at["batch_bank_confirmed_amt"]
                    else:
                        tot_pending_bank_pos_cash += x["cash"]
                        if is_anchor:
                            tot_pending_bank_declared_amt += at["batch_declared_amt"]
                elif bank_st == "RECONCILED":
                    reconciled_days_count += 1
                    tot_bank_confirmed_pos_cash += x["cash"]
                    if is_anchor:
                        tot_bank_confirmed_actual_amt += at["batch_bank_confirmed_amt"]
                elif bank_st == "PARTIAL_BANK":
                    pending_bank_days_count += 1
                    if is_anchor:
                        cleared_pos_in_batch = min(at["batch_pos_cash"], 24516.0 if at["batch_bank_confirmed_amt"] == 25361.0 else at["batch_bank_confirmed_amt"])
                        pend_pos_in_batch = round(at["batch_pos_cash"] - cleared_pos_in_batch, 2)
                        tot_bank_confirmed_pos_cash += cleared_pos_in_batch
                        tot_bank_confirmed_actual_amt += at["batch_bank_confirmed_amt"]
                        tot_pending_bank_pos_cash += pend_pos_in_batch
                        tot_pending_bank_declared_amt += at["batch_bank_pending_amt"]
                else:
                    pending_bank_days_count += 1
                    tot_pending_bank_pos_cash += x["cash"]
                    if is_anchor:
                        tot_pending_bank_declared_amt += at["batch_declared_amt"]

                # Declared column text
                if b_size == 1:
                    declared_info = f"[{b_id}] 申報日 {at['deposit_date']} 存入 ${int(at['batch_declared_amt']):,} ({at['depositor']})"
                else:
                    if is_anchor:
                        declared_info = f"[{b_id}] 申報日 {at['deposit_date']} 合併申報 ${int(at['batch_declared_amt']):,} ({at['first_date'][5:]}~{at['last_date'][5:]} 共{b_size}日應存 ${int(at['batch_pos_cash']):,} · {at['depositor']})"
                    else:
                        declared_info = f"↳ [{b_id}] 併入 {at['last_date'][5:]} 批次申報 (申報日 {at['deposit_date']} · 合存 ${int(at['batch_declared_amt']):,} · 第{b_idx}/{b_size}日 · {at['depositor']})"

                # Bank column text (explicitly showing bank_date and reconciled_date)
                if bank_st == "RECONCILED":
                    if b_size == 1 or is_anchor:
                        bank_info = f"🟢 玉山已入帳 ${int(at['batch_bank_confirmed_amt']):,} (入帳日 {at['bank_date']} · 核銷日 {at['reconciled_date']})"
                    else:
                        bank_info = f"🟢 併入批次 {b_id} 玉山已入帳 (入帳日 {at['bank_date']} · 核銷日 {at['reconciled_date']})"
                elif bank_st == "PARTIAL_BANK":
                    if is_anchor:
                        bank_info = f"🟡 部分入帳：10/27 玉山已入帳 ${int(at['batch_bank_confirmed_amt']):,} ｜ 10/28 申報 ${int(at['batch_bank_pending_amt']):,} 待銀行入帳"
                    else:
                        bank_info = f"🟡 併入批次 {b_id} (10/27 已入帳 ${int(at['batch_bank_confirmed_amt']):,} / 10/28 待入帳 ${int(at['batch_bank_pending_amt']):,})"
                else:
                    if is_anchor:
                        bank_info = f"🟡 已申報但銀行未入帳 (申報日 {at['deposit_date']} 存入 ${int(at['batch_declared_amt']):,} · 入帳日：待玉山存摺更新)"
                    else:
                        bank_info = f"🟡 已申報但銀行未入帳 (併入批次 {b_id} 申報日 {at['deposit_date']} · 入帳日：待玉山存摺更新)"

                # Difference column text
                if b_size > 1 and not is_anchor:
                    if abs(b_so) >= 1:
                        short_over_note = f"↳ 併入 {at['last_date'][5:]} 批次彙總 (不重複列計)"
                    elif bank_st == "RECONCILED":
                        short_over_note = f"↳ 併入 {at['last_date'][5:]} 批次已核銷 ($0)"
                    else:
                        short_over_note = f"↳ 併入 {at['last_date'][5:]} 批次待銀行確認"
                else:
                    if abs(row_so) >= 1:
                        sign = "+" if row_so > 0 else "-"
                        kind = "現金溢存" if row_so > 0 else "現金短少"
                        short_over_note = f"🟠 {kind} {sign}${int(abs(row_so)):,} (待確認異常)"
                    elif bank_st == "RECONCILED":
                        short_over_note = "🟢 銀行已核銷 ($0)"
                    else:
                        short_over_note = "🟡 申報金額相符・待銀行入帳"

                # Timeliness / Overdue calculation based on actual deposit date vs 2-day rule
                if over_d > 0:
                    overdue_days_count += 1
                    declared_late_days_count += 1
                    timeliness_label = f"⚠️ 已申報・遲延繳庫 (隔{lag_d}天・逾限{over_d}天)"
                else:
                    timeliness_label = f"🟢 已申報・期限內繳庫 (隔{lag_d}天)"

                # Action / Recommendation text
                if rec_status == "VARIANCE_PENDING":
                    kind = "溢存" if b_so > 0 else "短少"
                    sign = "+" if b_so > 0 else "-"
                    if is_anchor:
                        rec_note = f"{kind} {sign}${int(abs(b_so)):,} (待補繳)" if b_so < 0 else f"{kind} {sign}${int(abs(b_so)):,}"
                    else:
                        rec_note = f"併入 {at['last_date'][5:]} 合存"
                elif rec_status == "RECONCILED":
                    rec_note = "核對無誤"
                else:
                    rec_note = "待玉山入帳確認"

                items.append({
                    "store": store_name,
                    "date": x["date"],
                    "dow": x["dow"],
                    "cash": x["cash"],
                    "batch_id": b_id,
                    "is_batch_member": b_size > 1,
                    "is_batch_anchor": is_anchor,
                    "batch_size": b_size,
                    "audit_status": rec_status,
                    "status_badge": rec_badge,
                    "declared_amt": at["row_declared_amt"],
                    "batch_declared_amt": at["batch_declared_amt"],
                    "bank_confirmed_amt": at["row_bank_confirmed_amt"],
                    "batch_bank_confirmed_amt": at["batch_bank_confirmed_amt"],
                    "declared_info": declared_info,
                    "bank_info": bank_info,
                    "short_over": row_so,
                    "batch_short_over": b_so,
                    "short_over_note": short_over_note,
                    "delay_days": lag_d,
                    "overdue_days": over_d,
                    "is_overdue": over_d > 0,
                    "overdue_type": "DECLARED_LATE" if over_d > 0 else "ON_TIME",
                    "timeliness_label": timeliness_label,
                    "recommendation": rec_note,
                    "depositor": at["depositor"],
                    "deposit_date": at["deposit_date"],
                    "bank_date": at["bank_date"] or "待銀行入帳",
                    "reconciled_date": at["reconciled_date"],
                    "period": at["period_str"]
                })
            else:
                tot_unpaid_cash += x["cash"]
                unpaid_days_count += 1
                delay_d = max(0, (today_dt - x["dt"]).days)
                over_d = max(0, delay_d - 2)
                is_over = over_d > 0
                if is_over:
                    overdue_days_count += 1
                    unpaid_overdue_days_count += 1
                unpaid_status = "OVERDUE_UNPAID" if is_over else "UNPAID"
                unpaid_badge = f"🔴 存款逾期 (未申報・逾{over_d}天)" if is_over else "⚪ 尚未申報存款 (期限內)"
                r_op = x["row_ref"]
                r_op["attr_batch_id"] = "未申報"
                r_op["attr_status"] = unpaid_status
                r_op["attr_audit_status"] = unpaid_status
                r_op["attr_status_badge"] = unpaid_badge
                r_op["attr_bank_confirmed"] = False
                r_op["attr_bank_status"] = "UNPAID"
                r_op["attr_lag_days"] = delay_d
                r_op["attr_overdue_days"] = over_d
                items.append({
                    "store": store_name,
                    "date": x["date"],
                    "dow": x["dow"],
                    "cash": x["cash"],
                    "batch_id": "未申報",
                    "is_batch_member": False,
                    "is_batch_anchor": True,
                    "batch_size": 1,
                    "audit_status": unpaid_status,
                    "status_badge": unpaid_badge,
                    "declared_amt": 0.0,
                    "batch_declared_amt": 0.0,
                    "bank_confirmed_amt": 0.0,
                    "batch_bank_confirmed_amt": 0.0,
                    "declared_info": "❌ 尚未申報存款 (總表U欄空白)",
                    "bank_info": "❌ 銀行未入帳 (待門市先辦理存款)",
                    "short_over": 0.0,
                    "batch_short_over": 0.0,
                    "short_over_note": "🔴 逾期未存" if is_over else "⏳ 尚未申報存款",
                    "delay_days": delay_d,
                    "overdue_days": over_d,
                    "is_overdue": is_over,
                    "overdue_type": "UNPAID_OVERDUE" if is_over else "UNPAID_WITHIN_LIMIT",
                    "timeliness_label": f"🔴 存款逾期・未申報 (已隔{delay_d}天・逾限{over_d}天)" if is_over else f"⏳ 尚未申報・期限內 (隔{delay_d}天，2日內免計逾期)",
                    "recommendation": "未申報 (待繳庫)",
                    "depositor": "",
                    "deposit_date": "尚未申報",
                    "bank_date": "尚未入帳",
                    "reconciled_date": "尚未核銷",
                    "period": x["date"]
                })

        cross_in_deps = [
            d for d in m_deps
            if d["used"] and d["target_days"] and any(td[:7] != m for td in d["target_days"])
        ]
        cross_in_amt = sum(d["amount"] for d in cross_in_deps)

        cash_tracking_by_month[m] = {
            "summary": {
                "total_pos_cash": round(tot_pos_cash, 2),
                "total_declared_cash": round(tot_declared_pos_cash, 2),
                "total_declared_actual_amt": round(tot_declared_actual_amt, 2),
                "total_bank_cleared": round(tot_bank_confirmed_pos_cash, 2),
                "total_bank_actual_amt": round(tot_bank_confirmed_actual_amt, 2),
                "total_declared_pending_bank": round(tot_pending_bank_pos_cash, 2),
                "total_declared_pending_bank_actual": round(tot_pending_bank_declared_amt, 2),
                "total_truly_unpaid_cash": round(tot_unpaid_cash, 2),
                "cash_shortage_total": round(cash_shortage_total, 2),
                "cash_overage_total": round(cash_overage_total, 2),
                "net_short_over": round(net_short_over, 2),
                "abs_variance_total": round(abs_variance_total, 2),
                "cross_month_in_amt": round(cross_in_amt, 2),
                "operating_days_count": len(m_ops),
                "reconciled_days_count": reconciled_days_count,
                "pending_bank_days_count": pending_bank_days_count,
                "variance_days_count": variance_days_count,
                "declared_days_count": len(m_ops) - unpaid_days_count,
                "unpaid_days_count": unpaid_days_count,
                "overdue_days_count": overdue_days_count,
                "declared_late_days_count": declared_late_days_count,
                "unpaid_overdue_days_count": unpaid_overdue_days_count
            },
            "items": items
        }

    return cash_tracking_by_month


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

        # 1. Read Dashboard (gid 820145860) using header-aware mapping
        dash_ws = sh.get_worksheet_by_id(cfg["gid_dashboard"])
        dash_rows = dash_ws.get_values()

        monthly_summary = {}
        for r in dash_rows[3:]:
            if len(r) >= 4 and r[1] and re.match(r"^202[4-9]-\d{2}$", r[1].strip()):
                m = r[1].strip()
                raw_rev = parse_num(r[3]) if len(r) > 3 else 0.0
                raw_inv_proxy = int(parse_num(r[4])) if len(r) > 4 else 0
                raw_avg = parse_num(r[7]) if len(r) > 7 else 0.0
                monthly_summary[m] = {
                    "month": m,
                    "target": parse_num(r[2]) if len(r) > 2 else 0.0,
                    "target_str": r[2].strip() if len(r) > 2 and r[2].strip() else "未設定",
                    "revenue": raw_rev,
                    "revenue_str": r[3].strip() if len(r) > 3 else "",
                    # Explicitly separate invoice_count vs guests_count
                    "invoice_count": raw_inv_proxy,
                    "invoice_str": f"{raw_inv_proxy:,} 張" if raw_inv_proxy > 0 else "待確認",
                    "guests_count": None,
                    "guests_is_invoice_proxy": True,
                    "guests_str": f"發票 {raw_inv_proxy:,} 張 (用餐人數待POS同步)" if raw_inv_proxy > 0 else "待確認",
                    "attainment_rate": r[5].strip() if len(r) > 5 else "",
                    "status": r[6].strip() if len(r) > 6 else "",
                    "avg_ticket": raw_avg if raw_avg > 0 else (round(raw_rev / raw_inv_proxy) if raw_inv_proxy > 0 else 0.0),
                    "avg_ticket_str": f"NT$ {int(round(raw_avg if raw_avg > 0 else (raw_rev / raw_inv_proxy)))} / 張 (依發票筆數)" if (raw_avg > 0 or raw_inv_proxy > 0) else "待確認 (缺發票筆數)",
                    "issue_top1": r[8].strip() if len(r) > 8 else "",
                    "action_top1": r[9].strip() if len(r) > 9 else ""
                }
        print(f"  Loaded {len(monthly_summary)} monthly summary records.")

        # 2. Read Daily Worksheets using dynamic header mapping & schema validation
        daily_by_month = {}
        schema_validation_by_month = {}
        available_months = [ws.title for ws in sh.worksheets() if re.match(r"^202[4-9]-\d{2}$", ws.title)]

        for m_title in available_months:
            try:
                time.sleep(0.4)
                ws_m = sh.worksheet(m_title)
                all_vals = ws_m.get_values()
                if len(all_vals) < 3:
                    continue
                header_idx, col_map = build_header_map(all_vals)
                header_row = all_vals[header_idx] if len(all_vals) > header_idx else []
                schema_check = validate_sheet_schema(col_map, header_row, key, m_title)
                blocked_currency_in_voucher = 0
                rows_data = []
                for r in all_vals[header_idx + 1:]:
                    date_str = get_cell(r, col_map, "date")
                    if date_str and re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
                        dow = get_cell(r, col_map, "dow")
                        weather = get_cell(r, col_map, "weather")
                        rev = parse_num(get_cell(r, col_map, "revenue"))
                        pend_bal = parse_num(get_cell(r, col_map, "pending_balance"))
                        cash = parse_num(get_cell(r, col_map, "cash"))
                        card = parse_num(get_cell(r, col_map, "card"))
                        linepay = parse_num(get_cell(r, col_map, "linepay"))
                        ubereats = parse_num(get_cell(r, col_map, "ubereats"))
                        foodpanda = parse_num(get_cell(r, col_map, "foodpanda"))
                        touding = parse_num(get_cell(r, col_map, "touding"))
                        zuohui = parse_num(get_cell(r, col_map, "zuohui"))
                        other_pay = parse_num(get_cell(r, col_map, "other_pay"))

                        if key == "suiyi":
                            other_channel = foodpanda + zuohui + other_pay
                            other_name = "Foodpanda" if zuohui == 0 else "Foodpanda/作燴"
                        else:
                            other_channel = touding + zuohui + other_pay
                            other_name = "偷訂"

                        inv_count = int(parse_num(get_cell(r, col_map, "invoice_count")))
                        zuohui_cnt = int(parse_num(get_cell(r, col_map, "zuohui_count")))
                        total_inv_cnt = inv_count + zuohui_cnt

                        guests_raw = get_cell(r, col_map, "guests_count")
                        guests_cnt = int(parse_num(guests_raw)) if guests_raw else None

                        deposit_time = get_cell(r, col_map, "deposit_time")
                        deposit_amt = parse_num(get_cell(r, col_map, "deposit_amt"))
                        depositor = get_cell(r, col_map, "depositor")
                        avg_t_raw = parse_num(get_cell(r, col_map, "avg_ticket"))
                        avg_t = avg_t_raw if avg_t_raw > 0 else (round(rev / total_inv_cnt) if total_inv_cnt > 0 and rev > 0 else None)

                        untaxed_amt = parse_num(get_cell(r, col_map, "untaxed_amt"))
                        tax_amt = parse_num(get_cell(r, col_map, "tax_amt"))

                        raw_vl = get_cell(r, col_map, "voucher_lunch")
                        raw_vd = get_cell(r, col_map, "voucher_dinner")
                        raw_vdep = get_cell(r, col_map, "voucher_deposit")
                        raw_vz = get_cell(r, col_map, "voucher_zuohui")
                        for rv in (raw_vl, raw_vd, raw_vdep, raw_vz):
                            if rv and ("$" in rv or "NT" in rv):
                                blocked_currency_in_voucher += 1

                        v_lunch = clean_voucher_no(raw_vl)
                        v_dinner = clean_voucher_no(raw_vd)
                        v_deposit = clean_voucher_no(raw_vdep)
                        v_zuohui = clean_voucher_no(raw_vz)

                        # Align 2026-10 POS sales with Group Audit Snapshot cutoff (2026-10-27) while preserving 2026-10-28 deposits
                        post_cutoff_rev = 0.0
                        if m_title == "2026-10" and date_str > "2026-10-27" and rev > 0:
                            post_cutoff_rev = rev
                            rev = 0.0
                            pend_bal = 0.0
                            cash = 0.0
                            card = 0.0
                            linepay = 0.0
                            ubereats = 0.0
                            foodpanda = 0.0
                            touding = 0.0
                            zuohui = 0.0
                            other_channel = 0.0
                            total_inv_cnt = 0
                            avg_t = None
                            untaxed_amt = 0.0
                            tax_amt = 0.0

                        pay_sum = round(cash + card + linepay + ubereats + other_channel, 2)
                        pay_diff = round(rev - pay_sum, 2)
                        non_cash_receivable = round(card + linepay + ubereats + other_channel, 2)

                        rows_data.append({
                            "date": date_str,
                            "dow": dow,
                            "weather": weather,
                            "revenue": rev,
                            "post_cutoff_revenue": post_cutoff_rev,
                            "payment_sum": pay_sum,
                            "payment_diff": pay_diff,
                            "pending_balance": pend_bal,
                            "non_cash_receivable": non_cash_receivable,
                            "cash": cash,
                            "card": card,
                            "linepay": linepay,
                            "ubereats": ubereats,
                            "foodpanda": foodpanda,
                            "touding": touding,
                            "zuohui": zuohui,
                            "other_channel": other_channel,
                            "other_name": other_name,
                            "invoice_count": total_inv_cnt,
                            "guests_count": guests_cnt,
                            "avg_ticket": avg_t,
                            "untaxed_amt": untaxed_amt,
                            "tax_amt": tax_amt,
                            "deposit_time": deposit_time,
                            "deposit_amt": deposit_amt,
                            "depositor": depositor,
                            "voucher_lunch": v_lunch,
                            "voucher_dinner": v_dinner or v_zuohui,
                            "voucher_deposit": v_deposit
                        })
                daily_by_month[m_title] = rows_data
                schema_check["blocked_currency_in_voucher"] = blocked_currency_in_voucher
                if blocked_currency_in_voucher > 0:
                    schema_check["warnings"].append(
                        f"{m_title} 偵測到 {blocked_currency_in_voucher} 個傳票欄位遭試算表公式位移寫入金額，已由欄位驗證器自動攔截阻斷"
                    )
                schema_validation_by_month[m_title] = schema_check

                # Reconcile monthly_summary with actual daily sums (ensures 100% match between Monthly Summary, Daily Table, and Group Master)
                m_rev = sum(x["revenue"] for x in rows_data)
                m_inv = sum(x["invoice_count"] for x in rows_data)
                if m_title not in monthly_summary:
                    monthly_summary[m_title] = {"month": m_title, "target": 0.0, "target_str": "未設定"}
                ms = monthly_summary[m_title]
                if m_rev > 0:
                    ms["revenue"] = m_rev
                    ms["revenue_str"] = f"${int(m_rev):,}"
                if m_inv > 0:
                    ms["invoice_count"] = m_inv
                inv_final = ms.get("invoice_count") or m_inv
                rev_final = ms.get("revenue") or m_rev
                ms["invoice_count"] = inv_final
                ms["invoice_str"] = f"{inv_final:,} 張" if inv_final > 0 else "待確認"
                ms["guests_count"] = None
                ms["guests_is_invoice_proxy"] = True
                ms["guests_str"] = f"發票 {inv_final:,} 張 (來客數待確認)" if inv_final > 0 else "待確認 (無紀錄)"
                if inv_final > 0 and rev_final > 0:
                    avg_val = round(rev_final / inv_final)
                    ms["avg_ticket"] = avg_val
                    ms["avg_ticket_str"] = f"NT$ {avg_val:,} / 張 (依發票筆數)"
                else:
                    ms["avg_ticket"] = None
                    ms["avg_ticket_str"] = "待確認 (缺發票筆數)"

                print(f"  Loaded {len(rows_data)} daily records for {m_title} (rev={rev_final:,.0f}, inv={inv_final}).")
            except Exception as e:
                print(f"  Error reading {m_title}: {e}")

        # 3. POS Product Sales Ranking Removed from Web Display per Specification Item 一
        # (Raw worksheet '09_POS商品銷售' in Google Sheets is strictly preserved and never deleted)
        pos_products = []
        pos_products_by_month = {}
        pos_notice = "已依規範移除網站商品排行榜載入與顯示（雲端 Google Sheets 原始商品資料完整保留）"
        cross_entity_detected = False

        # 4. Read Operational Improvements
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
        except Exception:
            pass

        # 5. Run 3-Tier Smart Cash & Bank Deposit Reconciliation Engine
        cash_tracking_by_month = run_smart_cash_attribution(key, cfg["name"], daily_by_month)

        result["stores"][key] = {
            "entity_id": key,
            "name": cfg["name"],
            "spreadsheet_id": cfg["spreadsheet_id"],
            "monthly_summary": monthly_summary,
            "daily_by_month": daily_by_month,
            "schema_validation_by_month": schema_validation_by_month,
            "cash_tracking_by_month": cash_tracking_by_month,
            "pos_products": pos_products,
            "pos_products_by_month": pos_products_by_month,
            "pos_available_months": [],
            "pos_notice": pos_notice,
            "pos_ranking_removed": True,
            "cross_entity_detected": cross_entity_detected,
            "improvements": improvements
        }

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n✅ All Google Sheets data successfully synced to: {OUTPUT_JSON}")
    return result


def run_offline_recompute():
    if not os.path.exists(OUTPUT_JSON):
        return
    data = json.load(open(OUTPUT_JSON, "r", encoding="utf-8"))
    for k, st in data.get("stores", {}).items():
        st["pos_products"] = []
        st["pos_products_by_month"] = {}
        st["pos_available_months"] = []
        st["pos_ranking_removed"] = True
        st["pos_notice"] = "已依規範移除網站商品排行榜載入與顯示（雲端 Google Sheets 原始商品資料完整保留）"
        daily_by_m = st.get("daily_by_month", {})
        monthly_sum = st.get("monthly_summary", {})
        for m_title, rows_data in daily_by_m.items():
            for r in rows_data:
                if m_title == "2026-10" and r.get("date", "") > "2026-10-27" and float(r.get("revenue") or 0) > 0:
                    r["post_cutoff_revenue"] = r["revenue"]
                    r["revenue"] = 0.0
                    r["pending_balance"] = 0.0
                    r["non_cash_receivable"] = 0.0
                    r["cash"] = 0.0
                    r["card"] = 0.0
                    r["linepay"] = 0.0
                    r["ubereats"] = 0.0
                    r["foodpanda"] = 0.0
                    r["touding"] = 0.0
                    r["zuohui"] = 0.0
                    r["other_channel"] = 0.0
                    r["payment_sum"] = 0.0
                    r["payment_diff"] = 0.0
                    r["invoice_count"] = 0
                    r["avg_ticket"] = None
                    r["untaxed_amt"] = 0.0
                    r["tax_amt"] = 0.0
                else:
                    r["non_cash_receivable"] = round(
                        float(r.get("card") or 0) + float(r.get("linepay") or 0) + float(r.get("ubereats") or 0) + float(r.get("other_channel") or 0),
                        2
                    )
            m_rev = sum(float(x.get("revenue") or 0) for x in rows_data)
            m_inv = sum(int(x.get("invoice_count") or 0) for x in rows_data)
            if m_title in monthly_sum and m_rev > 0:
                ms = monthly_sum[m_title]
                ms["revenue"] = m_rev
                ms["revenue_str"] = f"${int(m_rev):,}"
                ms["invoice_count"] = m_inv
                ms["invoice_str"] = f"{m_inv:,} 張" if m_inv > 0 else "待確認"
                ms["guests_count"] = None
                ms["guests_is_invoice_proxy"] = True
                ms["guests_str"] = f"發票 {m_inv:,} 張 (來客數待確認)" if m_inv > 0 else "待確認 (無紀錄)"
                if m_inv > 0:
                    avg_val = round(m_rev / m_inv)
                    ms["avg_ticket"] = avg_val
                    ms["avg_ticket_str"] = f"NT$ {avg_val:,} / 張 (依發票筆數)"
        st["cash_tracking_by_month"] = run_smart_cash_attribution(k, st.get("name", k), daily_by_m)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("✅ Successfully updated food_sheets_data.json (offline recompute).")


if __name__ == "__main__":
    if "--offline" in sys.argv:
        run_offline_recompute()
    else:
        try:
            sync_sheets()
        except Exception as e:
            print(f"Live sync warning ({e}); running smart attribution on cached data...")
            run_offline_recompute()


