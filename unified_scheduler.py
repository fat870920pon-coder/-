#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
unified_scheduler.py
--------------------
月下（月宮）與隨意小吃 全事業體統一讀取與對帳排程器

核心職責與審計合規（v2.16.0）：
1. 通路同步排程：
   - LINE Pay (雙門市) 後台：每 5 分鐘 (300s) 同步一次。
   - Uber Eats (雙門市) 後台：每 5 分鐘 (300s) 同步一次。
   - foodpanda (東區隨意小吃) 後台：每 5 分鐘 (300s) 同步一次。
   - DILUTE (SHOPLINE Payments) 財報數據：每 5 分鐘 (300s) 同步一次。
2. 營運表與銀行存摺排程：
   - Google 營運總表 (月宮 / 隨意小吃)：每小時 (3600s) 讀取一次。
   - 玉山銀行明細檔 (Google Drive 下載之存摺 Excel)：每小時 (3600s) 讀取一次（非登入網銀）。
3. 嚴格審計驗證與連鎖對帳 (Chained Reconciliation)：
   - 來源必須真正完整同步成功（無登入失敗、無部分門市缺漏、無舊快取替代），才觸發連鎖對帳。
   - 登入失敗、使用舊快取、部分門市讀取失敗，嚴禁視為同步成功，不觸發連鎖對帳。
4. 多來源接連更新與合併重算 (Coalesced Queue with Pending Flag)：
   - 對帳重算具備單一執行緒保護，避免多來源同時觸發時重複計算。
   - 若對帳執行期間有新資料完成更新，自動設定待處理旗標 (reconcile_pending)，待當次完成後立即自動合併補算，絕不漏算。
5. 獨立資料落後/缺件狀態追蹤：
   - 使用各來源最近成功快取重算，不重複連線後台。
   - 明確區分「各來源最後成功時間」與「對帳計算時間」，標記落後或缺件狀態。
6. GitHub Pages 快照發布與失敗獨立顯示：
   - 完成對帳後發布至 GitHub Pages 快照。
   - 若發布失敗獨立標示為 PUBLISH_FAILED，絕不回報整個流程成功。
7. foodpanda 連線防偽保護：
   - 官方未通過真實連線時維持「尚未連線」與保留舊快取，嚴禁竄改時間冒充成功。
"""

import os
import sys
import time
import json
import signal
import threading
import subprocess
from datetime import datetime
from typing import Dict, Any, Optional, Tuple, Set

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ALT_BASE_DIR = os.path.expanduser("~/Desktop/腳本資料夾/月下/文中會計傳票產生器")

STATE_FILE = os.path.join(BASE_DIR, "scheduler_state.json")
ALT_STATE_FILE = os.path.join(ALT_BASE_DIR, "scheduler_state.json")

VENV_PYTHON = os.path.join(ALT_BASE_DIR, "venv/bin/python3")
if not os.path.exists(VENV_PYTHON):
    VENV_PYTHON = sys.executable
SYSTEM_PYTHON = sys.executable

# 排程工作定義（保留後台每5分鐘、營運表與銀行每小時的頻率）
TASKS_CONFIG = {
    "dilute": {
        "name": "DILUTE 財報數據 (SHOPLINE)",
        "interval_sec": 300,  # 5 分鐘
        "timeout_sec": 60,
        "max_retries": 3,
        "command": [SYSTEM_PYTHON, os.path.join(BASE_DIR, "sync_dilute.py")],
        "data_file": os.path.join(BASE_DIR, "shopline_cached_analysis.json"),
        "chain_reconciliation": True
    },
    "linepay": {
        "name": "LINE Pay 後台",
        "interval_sec": 300,  # 5 分鐘
        "timeout_sec": 180,
        "max_retries": 3,
        "command": [SYSTEM_PYTHON, os.path.join(BASE_DIR, "sync_linepay.py")],
        "data_file": os.path.join(BASE_DIR, "linepay_merchant_data.json"),
        "chain_reconciliation": True
    },
    "ubereats": {
        "name": "Uber Eats 後台",
        "interval_sec": 300,  # 5 分鐘
        "timeout_sec": 240,
        "max_retries": 3,
        "command": [SYSTEM_PYTHON, os.path.join(BASE_DIR, "sync_ubereats.py")],
        "data_file": os.path.join(BASE_DIR, "ubereats_merchant_data.json"),
        "chain_reconciliation": True
    },
    "foodpanda": {
        "name": "foodpanda 後台",
        "interval_sec": 300,  # 5 分鐘
        "timeout_sec": 120,
        "max_retries": 3,
        "command": [SYSTEM_PYTHON, os.path.join(BASE_DIR, "sync_foodpanda.py")],
        "data_file": os.path.join(BASE_DIR, "foodpanda_merchant_data.json"),
        "chain_reconciliation": True
    },
    "google_sheets": {
        "name": "Google 營運總表",
        "interval_sec": 3600,  # 1 小時
        "timeout_sec": 180,
        "max_retries": 3,
        "command": [VENV_PYTHON, os.path.join(ALT_BASE_DIR, "sync_food_sheets.py"), "--strict"],
        "data_file": os.path.join(ALT_BASE_DIR, "food_sheets_data.json"),
        "chain_reconciliation": True
    },
    "bank_deposits": {
        "name": "玉山銀行明細檔",
        "interval_sec": 3600,  # 1 小時
        "timeout_sec": 120,
        "max_retries": 3,
        "command": [VENV_PYTHON, os.path.join(BASE_DIR, "sync_bank_deposits.py")],
        "data_file": os.path.join(BASE_DIR, "cached_esun_bank_deposits.json"),
        "chain_reconciliation": True
    },
    "reconciliation": {
        "name": "全事業體連鎖對帳與財報更新",
        "interval_sec": 0,  # 連鎖觸發或待處理重算
        "timeout_sec": 180,
        "max_retries": 2,
        "command": [
            VENV_PYTHON,
            os.path.join(BASE_DIR, "audit_delivery_reconciliation.py"),
            "--cache"
        ],
        "sub_command": [
            VENV_PYTHON,
            os.path.join(ALT_BASE_DIR, "reconciliation_summary.py")
        ],
        "data_file": os.path.join(BASE_DIR, "delivery_audit_results.json"),
        "chain_reconciliation": False
    }
}


class UnifiedScheduler:
    def __init__(self):
        self.lock = threading.Lock()
        self.reconcile_lock = threading.Lock()
        self.reconcile_running = False
        self.reconcile_pending = False
        self.reconcile_pending_triggers: Set[str] = set()
        self.running_processes: Dict[str, subprocess.Popen] = {}
        self.stop_requested = False
        self.state = self.load_state()

    def load_state(self) -> Dict[str, Any]:
        with self.lock:
            state = {
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "scheduler_pid": os.getpid(),
                "is_running": True,
                "sources": {},
                "reconciliation_meta": {}
            }
            if os.path.exists(STATE_FILE):
                try:
                    with open(STATE_FILE, "r", encoding="utf-8") as f:
                        saved = json.load(f)
                        if isinstance(saved.get("sources"), dict):
                            state["sources"] = saved["sources"]
                        if isinstance(saved.get("reconciliation_meta"), dict):
                            state["reconciliation_meta"] = saved["reconciliation_meta"]
                except Exception as e:
                    print(f"⚠️ 讀取現有排程狀態檔失敗：{e}")

            # 補齊所有定義的工作初始狀態
            for task_id, cfg in TASKS_CONFIG.items():
                if task_id not in state["sources"]:
                    coverage = self.extract_data_coverage(task_id, cfg.get("data_file"))
                    state["sources"][task_id] = {
                        "task_id": task_id,
                        "name": cfg["name"],
                        "interval_sec": cfg["interval_sec"],
                        "timeout_sec": cfg["timeout_sec"],
                        "is_running": False,
                        "last_attempt_at": None,
                        "last_success_at": None,
                        "data_coverage_range": coverage,
                        "status": "INITIALIZED",
                        "connection_status": "尚未連線" if task_id == "foodpanda" else "待排程",
                        "is_stale": False,
                        "last_error": None,
                        "retry_count": 0,
                        "last_duration_sec": 0.0
                    }
                else:
                    # 重置開機時的 running 狀態避免殭屍旗標
                    state["sources"][task_id]["is_running"] = False
                    state["sources"][task_id]["interval_sec"] = cfg["interval_sec"]
                    state["sources"][task_id]["timeout_sec"] = cfg["timeout_sec"]
                    state["sources"][task_id]["name"] = cfg["name"]
                    # 若現存資料檔案有涵蓋日期，重新整理
                    if not state["sources"][task_id].get("data_coverage_range"):
                        state["sources"][task_id]["data_coverage_range"] = self.extract_data_coverage(
                            task_id, cfg.get("data_file")
                        )

            # 清理不在 TASKS_CONFIG 的暫態工作
            stale_keys = [k for k in list(state["sources"].keys()) if k not in TASKS_CONFIG]
            for k in stale_keys:
                del state["sources"][k]

            return state

    def save_state(self):
        with self.lock:
            self.state["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.state["scheduler_pid"] = os.getpid()
            try:
                with open(STATE_FILE, "w", encoding="utf-8") as f:
                    json.dump(self.state, f, ensure_ascii=False, indent=2)
                # 鏡像備份至月下專案目錄
                os.makedirs(os.path.dirname(ALT_STATE_FILE), exist_ok=True)
                with open(ALT_STATE_FILE, "w", encoding="utf-8") as f:
                    json.dump(self.state, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"⚠️ 寫入排程狀態檔失敗：{e}")

    def extract_data_coverage(self, task_id: str, file_path: Optional[str]) -> str:
        """從資料快取中提取資料涵蓋日期範圍"""
        if not file_path or not os.path.exists(file_path):
            return "無資料"

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                d = json.load(f)

            if task_id == "linepay":
                all_months = []
                for sk, st in d.get("stores", {}).items():
                    for m in st.get("monthly_summaries", {}).keys():
                        all_months.append(m)
                if all_months:
                    all_months.sort()
                    return f"{all_months[0]} ~ {all_months[-1]}"

            elif task_id == "ubereats":
                months = set()
                for sk, st in d.get("stores", {}).items():
                    for ms in st.get("monthly_statements", []):
                        if ms.get("month"):
                            months.add(ms["month"])
                if months:
                    m_list = sorted(list(months))
                    return f"{m_list[0]} ~ {m_list[-1]}"

            elif task_id == "foodpanda":
                periods = []
                for sk, st in d.get("stores", {}).items():
                    for p in st.get("payouts", []):
                        if p.get("period"):
                            periods.append(p["period"])
                if periods:
                    return f"{periods[-1]} ~ {periods[0]} (快取)"

            elif task_id == "google_sheets":
                months = set()
                for sk, st in d.get("stores", {}).items():
                    for m in st.get("daily_by_month", {}).keys():
                        months.add(m)
                if months:
                    m_list = sorted(list(months))
                    return f"{m_list[0]} ~ {m_list[-1]}"

            elif task_id == "bank_deposits":
                dates = []
                for sk, rows in d.items():
                    if isinstance(rows, list):
                        for r in rows:
                            if isinstance(r, dict) and r.get("date"):
                                d_val = str(r["date"]).strip()
                                if d_val.startswith("20") and len(d_val) >= 10:
                                    dates.append(d_val[:10])
                if dates:
                    dates.sort()
                    return f"{dates[0]} ~ {dates[-1]}"

            elif task_id == "dilute":
                w_list = d.get("analysis", {}).get("withdrawals", [])
                if w_list:
                    dates = []
                    for w in w_list:
                        dt = w.get("transferDate") or w.get("applyDate") or ""
                        if dt:
                            dates.append(str(dt)[:10])
                    if dates:
                        dates.sort()
                        return f"{dates[0]} ~ {dates[-1]}"
                return "已記錄 (SHOPLINE 快取)"

            elif task_id == "reconciliation":
                meta = d.get("audit_meta", {})
                if meta.get("generated_at"):
                    return f"審計完成時間：{meta.get('generated_at')}"
        except Exception:
            pass
        return "已記錄"

    def verify_source_sync_success(
        self,
        task_id: str,
        retcode: int,
        stdout: str,
        stderr: str,
        data_file: Optional[str]
    ) -> Tuple[bool, Optional[str]]:
        """
        審計合規嚴格驗證（規格 2）：
        只有確認來源讀取成功才觸發連鎖對帳。
        登入失敗、使用舊快取、部分門市讀取失敗，不得當成全部同步成功。
        """
        if retcode != 0:
            return False, f"行程返回碼異常 (Exit {retcode})"

        combined_output = (stdout or "") + "\n" + (stderr or "")

        # 1. 檢驗常見致命錯誤關鍵字
        failure_keywords = [
            "登入失敗", "尚未連線", "DISCONNECTED", "部分失敗",
            "同步失敗", "出現異常", "Traceback (most recent call last)",
            "429 Too Many Requests", "Quota exceeded", "Unauthorized",
            "Please check your email and password"
        ]
        for kw in failure_keywords:
            if kw in combined_output:
                return False, f"輸出檢測到錯誤關鍵字：{kw}"

        # 2. 檢驗快取資料檔案實體完整度
        if data_file and not os.path.exists(data_file):
            return False, f"快取資料檔不存在：{os.path.basename(data_file)}"

        try:
            if data_file and os.path.exists(data_file):
                with open(data_file, "r", encoding="utf-8") as f:
                    d = json.load(f)

                if task_id == "foodpanda":
                    st_map = d.get("stores", {})
                    sy = st_map.get("suiyi", {})
                    conn = sy.get("connection_status") or d.get("connection_status")
                    if conn not in ("已連線同步", "CONNECTED"):
                        return False, "foodpanda 後台目前尚未連線，使用舊快取"

                elif task_id == "linepay":
                    stores = d.get("stores", {})
                    if "yuegong" not in stores or "suiyi" not in stores:
                        return False, "LINE Pay 未能完整取得雙門市 (月宮與隨意) 數據"
                    for sk in ["yuegong", "suiyi"]:
                        s_data = stores[sk]
                        if not s_data or ("error" in s_data) or (
                            not s_data.get("settlements")
                            and not s_data.get("daily_records")
                            and not s_data.get("monthly_summaries")
                        ):
                            return False, f"LINE Pay 門市 [{sk}] 數據缺失或未完整"

                elif task_id == "ubereats":
                    stores = d.get("stores", {})
                    if "yuegong" not in stores or "suiyi" not in stores:
                        return False, "Uber Eats 未能完整取得雙門市 (月宮與隨意) 數據"
                    for sk in ["yuegong", "suiyi"]:
                        s_data = stores[sk]
                        if not s_data or ("error" in s_data):
                            return False, f"Uber Eats 門市 [{sk}] 數據缺失"

                elif task_id == "google_sheets":
                    stores = d.get("stores", {})
                    if "yuegong" not in stores or "suiyi" not in stores:
                        return False, "營運總表未包含雙門市數據"

                elif task_id == "bank_deposits":
                    if not isinstance(d, dict) or ("yuegong" not in d and "suiyi" not in d):
                        return False, "玉山銀行存摺明細未包含完整門市帳戶"

                elif task_id == "dilute":
                    analysis = d.get("analysis", {})
                    if not analysis.get("withdrawals"):
                        return False, "DILUTE 未能取得有效提款與結算流水"
        except Exception as e:
            return False, f"解析快取檔案驗證失敗：{e}"

        return True, "完整同步成功"

    def schedule_reconciliation(self, trigger_source: str):
        """
        合併重算排程器（規格 3）：
        多個來源接連更新時，合併重算，避免同時執行。
        若重算期間又有新資料，記錄待處理旗標 (reconcile_pending)，完成後再跑一次。
        """
        with self.reconcile_lock:
            if self.reconcile_running:
                self.reconcile_pending = True
                self.reconcile_pending_triggers.add(trigger_source)
                print(f"[{datetime.now().strftime('%H:%M:%S')}] ⏳ [連鎖對帳] 目前正在重算中，設定待處理旗標 (新增來源: {trigger_source}，累計待處理: {list(self.reconcile_pending_triggers)})")
                return
            else:
                self.reconcile_running = True
                self.reconcile_pending = False
                self.reconcile_pending_triggers = set()
                t = threading.Thread(target=self._reconciliation_worker, args=(trigger_source,), daemon=True)
                t.start()

    def _reconciliation_worker(self, initial_trigger: str):
        """連鎖對帳背景常駐 Worker 迴圈"""
        current_trigger = initial_trigger
        while True:
            self._execute_reconciliation(trigger_source=current_trigger)

            with self.reconcile_lock:
                if self.reconcile_pending:
                    sources = ",".join(sorted(self.reconcile_pending_triggers))
                    current_trigger = f"合併後續更新({sources})"
                    self.reconcile_pending = False
                    self.reconcile_pending_triggers.clear()
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔁 [連鎖對帳] 檢測到重算期間有新來源更新完成，立即發動合併補算 ({current_trigger})！")
                    continue
                else:
                    self.reconcile_running = False
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] 🏁 [連鎖對帳] 全部連鎖重算與財報更新完成。")
                    break

    def generate_reconciliation_meta(self) -> Dict[str, Any]:
        """彙整對帳計算時間、各來源最後成功時間與缺件/落後狀態（規格 4）"""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        meta = {
            "reconciliation_calculated_at": now_str,
            "sources_summary": {}
        }
        for tid, cfg in TASKS_CONFIG.items():
            if tid == "reconciliation":
                continue
            t_state = self.state["sources"].get(tid, {})
            last_succ = t_state.get("last_success_at")
            is_stale = t_state.get("is_stale", True)
            coverage = t_state.get("data_coverage_range") or "無資料"
            status = t_state.get("status") or "UNKNOWN"

            lag_status = "正常最新"
            if tid == "foodpanda":
                lag_status = "🔴 尚未連線 (保留歷史快取至 10/15)"
            elif status != "SUCCESS":
                lag_status = f"🟡 連線異常 ({status})"
            elif not last_succ:
                lag_status = "⚪ 尚無同步成功紀錄"
            elif is_stale:
                lag_status = "🟡 快取待更新"
            elif "落後" in coverage:
                lag_status = "🟡 資料範圍落後"

            meta["sources_summary"][tid] = {
                "name": cfg["name"],
                "status": status,
                "last_success_at": last_succ,
                "data_coverage_range": coverage,
                "is_stale": is_stale,
                "lag_status": lag_status
            }
        return meta

    def publish_to_github_pages(self) -> Dict[str, Any]:
        """
        發布對帳結果與儀表板快照至 GitHub Pages（規格 5）。
        若發布失敗，獨立回傳 ok=False 與詳細原因，絕不掩蓋或冒充成功。
        """
        repo_dir = ALT_BASE_DIR
        if not os.path.exists(os.path.join(repo_dir, ".git")):
            return {"ok": False, "error": f"找不到 Git 儲存庫：{repo_dir}"}

        try:
            # 複製最新對帳與快取檔案至 repo
            files_to_sync = [
                "delivery_audit_results.json",
                "food_sheets_data.json",
                "cached_esun_bank_deposits.json",
                "linepay_merchant_data.json",
                "ubereats_merchant_data.json",
                "foodpanda_merchant_data.json",
                "reconciliation_balance.json"
            ]
            for fn in files_to_sync:
                src = os.path.join(BASE_DIR, fn)
                dst = os.path.join(repo_dir, fn)
                if os.path.exists(src) and src != dst:
                    try:
                        import shutil
                        shutil.copy2(src, dst)
                    except Exception:
                        pass

            # 執行 git add
            add_proc = subprocess.run(
                ["git", "add", "."],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                timeout=30
            )
            if add_proc.returncode != 0:
                return {"ok": False, "error": f"Git add 失敗: {add_proc.stderr.strip()}"}

            diff_proc = subprocess.run(
                ["git", "diff", "--staged", "--quiet"],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                timeout=10
            )
            if diff_proc.returncode != 0:
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                commit_proc = subprocess.run(
                    ["git", "commit", "-m", f"chore(reconcile): update reconciliation snapshot at {now_str}"],
                    cwd=repo_dir,
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                if commit_proc.returncode != 0:
                    return {"ok": False, "error": f"Git commit 失敗: {commit_proc.stderr.strip()}"}

                push_proc = subprocess.run(
                    ["git", "push", "origin", "main"],
                    cwd=repo_dir,
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                if push_proc.returncode != 0:
                    err_msg = (push_proc.stderr or push_proc.stdout or "Push rejected").strip()
                    return {"ok": False, "error": f"Git push 失敗: {err_msg}"}

            return {"ok": True, "published_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "Git 發布逾時 (超過 60 秒)"}
        except Exception as e:
            return {"ok": False, "error": f"發布發生例外：{str(e)}"}

    def _execute_reconciliation(self, trigger_source: str = "自動連鎖") -> Dict[str, Any]:
        """
        執行全事業體連鎖審計對帳與財報更新（規格 4 & 5）：
        1. 使用各來源最近成功快取，不額外重抓後台。
        2. 執行 audit_delivery_reconciliation.py --cache
        3. 執行 reconciliation_summary.py
        4. 彙整對帳計算時間、各來源成功時間與缺件/落後狀態。
        5. 發布至 GitHub Pages 快照；若發布失敗獨立標示，不回報全流程成功。
        """
        task_id = "reconciliation"
        cfg = TASKS_CONFIG[task_id]
        task_state = self.state["sources"][task_id]
        start_ts = time.time()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        task_state["is_running"] = True
        task_state["last_attempt_at"] = now_str
        task_state["trigger_source"] = trigger_source
        self.save_state()

        print(f"[{datetime.now().strftime('%H:%M:%S')}] 🚀 [全事業體連鎖對帳] 開始執行（觸發來源：{trigger_source}）...")

        retcode = 0
        err_msg = None

        # 階段 1: audit_delivery_reconciliation.py --cache
        try:
            p1 = subprocess.run(
                cfg["command"],
                cwd=BASE_DIR,
                capture_output=True,
                text=True,
                timeout=cfg.get("timeout_sec", 180)
            )
            if p1.returncode != 0:
                retcode = p1.returncode
                err_msg = f"審計對帳第一階段失敗: {p1.stderr.strip() or p1.stdout.strip()}"
        except Exception as e:
            retcode = -1
            err_msg = f"審計對帳第一階段例外: {e}"

        # 階段 2: reconciliation_summary.py
        if retcode == 0 and cfg.get("sub_command"):
            try:
                p2 = subprocess.run(
                    cfg["sub_command"],
                    cwd=ALT_BASE_DIR,
                    capture_output=True,
                    text=True,
                    timeout=cfg.get("timeout_sec", 180)
                )
                if p2.returncode != 0:
                    retcode = p2.returncode
                    err_msg = f"審計對帳第二階段失敗: {p2.stderr.strip() or p2.stdout.strip()}"
            except Exception as e:
                retcode = -1
                err_msg = f"審計對帳第二階段例外: {e}"

        # 彙整來源落後與缺件元數據
        recon_meta = self.generate_reconciliation_meta()
        self.state["reconciliation_meta"] = recon_meta

        elapsed = round(time.time() - start_ts, 2)
        task_state["last_duration_sec"] = elapsed
        task_state["is_running"] = False

        if retcode != 0:
            task_state["status"] = "FAILED"
            task_state["is_stale"] = True
            task_state["last_error"] = err_msg
            task_state["publish_status"] = "SKIPPED"
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ [全事業體連鎖對帳] 本機對帳計算失敗：{err_msg}")
            self.save_state()
            return {"ok": False, "stage": "reconciliation_calc", "error": err_msg}

        # 階段 3: 更新本機儀表板並發布至 GitHub Pages 快照
        pub_res = self.publish_to_github_pages()
        if not pub_res.get("ok"):
            # 關鍵審計合規：發布失敗須獨立顯示，不得回報整個流程成功！
            pub_err = pub_res.get("error", "未知發布錯誤")
            task_state["status"] = "PUBLISH_FAILED"
            task_state["is_stale"] = False
            task_state["publish_status"] = "FAILED"
            task_state["publish_error"] = pub_err
            task_state["last_error"] = f"本機對帳已更新，但 GitHub Pages 發布失敗：{pub_err}"
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ [全事業體連鎖對帳] 本機對帳完成 ({elapsed}s)，但 GitHub Pages 發布失敗：{pub_err}")
            self.save_state()
            return {"ok": False, "stage": "github_pages_publish", "error": pub_err}

        # 全部階段成功
        task_state["status"] = "SUCCESS"
        task_state["is_stale"] = False
        task_state["last_success_at"] = now_str
        task_state["last_error"] = None
        task_state["publish_status"] = "SUCCESS"
        task_state["publish_error"] = None
        task_state["retry_count"] = 0
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ [全事業體連鎖對帳] 本機對帳與 GitHub Pages 快照發布全部成功 (耗時 {elapsed}s)！")
        self.save_state()
        return {"ok": True, "published_at": pub_res.get("published_at")}

    def execute_task(self, task_id: str, is_chain: bool = False) -> Dict[str, Any]:
        """執行單一排程工作"""
        if task_id == "reconciliation":
            return self._execute_reconciliation(trigger_source="直接執行" if not is_chain else "連鎖觸發")

        cfg = TASKS_CONFIG.get(task_id)
        if not cfg:
            return {"ok": False, "error": f"未知的工作 ID: {task_id}"}

        task_state = self.state["sources"].get(task_id, {})

        # 1. 防重複執行檢查
        if task_state.get("is_running"):
            msg = f"⚠️ [{cfg['name']}] 前次執行尚未完成 (PID 運行中)，略過本次啟動，避免重複衝突。"
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
            return {"ok": False, "skipped": True, "reason": "PREVIOUS_RUN_ACTIVE"}

        start_ts = time.time()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        task_state["is_running"] = True
        task_state["last_attempt_at"] = now_str
        self.save_state()

        print(f"[{datetime.now().strftime('%H:%M:%S')}] 🚀 [排程啟動] {cfg['name']} (Timeout: {cfg['timeout_sec']}s)...")

        proc = None
        cmd = cfg["command"]
        timeout_sec = cfg["timeout_sec"]
        success = False
        error_msg = None
        stdout, stderr = "", ""
        retcode = -1

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                preexec_fn=os.setsid if hasattr(os, "setsid") else None
            )
            with self.lock:
                self.running_processes[task_id] = proc

            try:
                stdout, stderr = proc.communicate(timeout=timeout_sec)
                retcode = proc.returncode
            except subprocess.TimeoutExpired:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] ⏱️ [{cfg['name']}] 執行逾時（超過 {timeout_sec} 秒），排程器已強制終止子行程。")
                error_msg = f"執行逾時（超過 {timeout_sec} 秒），排程器已強制終止子行程。"
                try:
                    if hasattr(os, "killpg"):
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    else:
                        proc.kill()
                except Exception:
                    pass
                proc.communicate()
                retcode = -9

            elapsed = round(time.time() - start_ts, 2)
            task_state["last_duration_sec"] = elapsed

            # 審計驗證：嚴格確認來源讀取完整成功
            is_true_success, verify_reason = self.verify_source_sync_success(
                task_id, retcode, stdout, stderr, cfg.get("data_file")
            )
            if error_msg:
                is_true_success = False
                verify_reason = error_msg

            if is_true_success:
                success = True
                task_state["status"] = "SUCCESS"
                task_state["is_stale"] = False
                task_state["last_success_at"] = now_str
                task_state["last_error"] = None
                task_state["retry_count"] = 0
                if task_id == "foodpanda":
                    task_state["connection_status"] = "已連線同步"
                task_state["data_coverage_range"] = self.extract_data_coverage(task_id, cfg.get("data_file"))
                print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ [{cfg['name']}] 完整同步成功 (耗時 {elapsed}s，涵蓋：{task_state['data_coverage_range']})")
            else:
                success = False
                task_state["status"] = "DISCONNECTED" if task_id == "foodpanda" else "FAILED"
                task_state["is_stale"] = True
                task_state["last_error"] = verify_reason
                task_state["retry_count"] = task_state.get("retry_count", 0) + 1
                if task_id == "foodpanda":
                    task_state["connection_status"] = "尚未連線"
                print(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ [{cfg['name']}] 同步未通過完整檢驗：{verify_reason} (保留成功快取，標記舊資料，累計重試 {task_state['retry_count']} 次)")

        except Exception as e:
            elapsed = round(time.time() - start_ts, 2)
            task_state["last_duration_sec"] = elapsed
            task_state["status"] = "ERROR"
            task_state["is_stale"] = True
            task_state["last_error"] = f"排程器啟動例外：{str(e)}"
            task_state["retry_count"] = task_state.get("retry_count", 0) + 1
            if task_id == "foodpanda":
                task_state["connection_status"] = "尚未連線"
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 🚨 [{cfg['name']}] 發生未捕捉例外：{e}")

        finally:
            task_state["is_running"] = False
            with self.lock:
                self.running_processes.pop(task_id, None)
            self.save_state()

        # 連鎖觸發：只有確認來源讀取完整成功，才排入連鎖對帳重算
        if success and cfg.get("chain_reconciliation") and not is_chain:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔗 [{cfg['name']}] 完整同步成功，排入連鎖對帳重算...")
            self.schedule_reconciliation(trigger_source=task_id)

        return {
            "ok": success,
            "task_id": task_id,
            "status": task_state["status"],
            "duration": task_state["last_duration_sec"],
            "error": task_state.get("last_error")
        }

    def trigger_all_sources(self) -> Dict[str, Any]:
        """立即手動重新抓取所有來源（月宮、隨意小吃、DILUTE）"""
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚡ 收到「更新連線」指令，立即全源重新抓取...")
        threads = []
        sources_to_trigger = ["google_sheets", "bank_deposits", "dilute", "linepay", "ubereats", "foodpanda"]
        results = {}

        def _run_single(tid):
            res = self.execute_task(tid)
            results[tid] = res

        for tid in sources_to_trigger:
            t = threading.Thread(target=_run_single, args=(tid,), daemon=True)
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=120)

        # 確保最後強制執行一次連鎖審計重算
        rec_res = self._execute_reconciliation(trigger_source="全源手動重新抓取完成")
        results["reconciliation"] = rec_res

        return {
            "ok": rec_res.get("ok", False),
            "message": "全事業體連線數據已完成重新抓取並更新財報與對帳",
            "results": results
        }

    def run_loop(self):
        """主排程計時迴圈"""
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 🌟 統一對帳排程引擎正式啟動 (PID: {os.getpid()})")
        print("  - 通路 5 分鐘同步：LINE Pay、Uber Eats、foodpanda、DILUTE")
        print("  - 總表與銀行 1 小時同步：Google 營運總表、玉山銀行存摺明細檔")
        print("  - 連鎖機制：任一來源成功更新後自動觸發合併重算審計對帳與財報發布")

        last_check_ts: Dict[str, float] = {}

        while not self.stop_requested:
            try:
                now_ts = time.time()
                for task_id, cfg in TASKS_CONFIG.items():
                    interval = cfg["interval_sec"]
                    if interval <= 0:
                        continue

                    last_run = last_check_ts.get(task_id, 0)
                    time_diff = now_ts - last_run

                    task_state = self.state["sources"].get(task_id, {})
                    is_running = task_state.get("is_running", False)
                    retry_count = task_state.get("retry_count", 0)

                    should_run = False
                    if not is_running:
                        if time_diff >= interval:
                            should_run = True
                        elif retry_count > 0 and retry_count <= cfg["max_retries"]:
                            retry_delay = 15 * (3 ** (retry_count - 1))
                            if time_diff >= retry_delay:
                                print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔁 [{cfg['name']}] 執行重試機制 (第 {retry_count}/{cfg['max_retries']} 次)...")
                                should_run = True

                    if should_run:
                        last_check_ts[task_id] = now_ts
                        t = threading.Thread(
                            target=self.execute_task,
                            args=(task_id, False),
                            daemon=True
                        )
                        t.start()

                time.sleep(10)
            except Exception as e:
                print(f"⚠️ 排程迴圈異常：{e}")
                time.sleep(10)

    def start_in_thread(self) -> threading.Thread:
        t = threading.Thread(target=self.run_loop, daemon=True)
        t.start()
        return t

    def stop(self):
        self.stop_requested = True
        with self.lock:
            for task_id, proc in self.running_processes.items():
                try:
                    proc.terminate()
                except Exception:
                    pass


_scheduler_instance: Optional[UnifiedScheduler] = None


def get_scheduler() -> UnifiedScheduler:
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = UnifiedScheduler()
    return _scheduler_instance


if __name__ == "__main__":
    scheduler = get_scheduler()
    if "--run-once" in sys.argv:
        print("🧪 執行單次全源排程測試...")
        target_task = None
        for arg in sys.argv:
            if arg.startswith("--task="):
                target_task = arg.split("=")[1]

        if target_task:
            res = scheduler.execute_task(target_task)
            print("測試結果：", json.dumps(res, ensure_ascii=False, indent=2))
        else:
            for tid in ["foodpanda", "bank_deposits", "reconciliation"]:
                res = scheduler.execute_task(tid)
                print(f"[{tid}] 測試結果：", res)
    else:
        try:
            scheduler.run_loop()
        except KeyboardInterrupt:
            print("\n🛑 接收到中斷訊號，正在安全停止排程器...")
            scheduler.stop()
