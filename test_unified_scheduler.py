#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_unified_scheduler.py
-------------------------
排程器核心規範與容錯實測套件（v2.16.0 審計強化版）：
1. 防重複執行 (Mutual Exclusion)：當工作處於 is_running 狀態時，防止並行重啟。
2. 逾時中斷 (Timeout Handling)：當工作耗時超過 timeout_sec 時，強制中斷行程並記錄逾時。
3. 失敗容錯與舊快取保護 (Stale Cache Retention)：同步失敗時保留成功快取，明確標示 is_stale: True，不更新 last_success_at。
4. foodpanda 防偽驗證：未連線成功時回傳 DISCONNECTED 與「尚未連線」，禁止更新時間冒充同步。
5. 銀行明細更新與連鎖對帳 (Chained Trigger)：明細更新成功後連鎖觸發重新對帳，產出 delivery_audit_results.json。
6. 排程頻率合規檢查：後台 5 分鐘 (300s)、營運表與銀行 1 小時 (3600s)、chain_reconciliation 旗標檢查。
7. 單來源同步成功觸發對帳 (Sync Success Triggers Reconciliation)。
8. 同步失敗阻擋連鎖對帳 (Sync Failure Blocks Reconciliation)：登入失敗、部分失敗、異常輸出嚴格阻擋。
9. 多來源接連完成合併重算 (Multiple Sources Coalesced Reconciliation)：單一 worker 鎖保護，不重複並行。
10. 重算期間新資料補算機制 (Pending Flag New Data During Reconcile)：記錄待處理旗標，完成後立即再跑一次，絕不漏算。
11. GitHub Pages 發布失敗獨立顯示 (Independent Publish Failure Reporting)：發布失敗時標記 PUBLISH_FAILED，絕不回報成功。
"""

import os
import sys
import time
import json
import unittest
import threading
import subprocess
from datetime import datetime
from unittest.mock import patch, MagicMock

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
from unified_scheduler import UnifiedScheduler, TASKS_CONFIG

class TestUnifiedScheduler(unittest.TestCase):
    def setUp(self):
        self.scheduler = UnifiedScheduler()

    def test_01_duplicate_execution_prevention(self):
        """測試 1：同一來源前次尚未完成時，不重複啟動"""
        task_id = "linepay"
        # 人為設定 is_running = True
        self.scheduler.state["sources"][task_id]["is_running"] = True
        res = self.scheduler.execute_task(task_id)
        self.assertFalse(res["ok"])
        self.assertTrue(res.get("skipped"))
        self.assertEqual(res.get("reason"), "PREVIOUS_RUN_ACTIVE")
        # 還原
        self.scheduler.state["sources"][task_id]["is_running"] = False

    def test_02_timeout_interruption(self):
        """測試 2：逾時中斷機制 (Subprocess timeout)"""
        task_id = "test_timeout"
        TASKS_CONFIG[task_id] = {
            "name": "逾時測試工作",
            "interval_sec": 60,
            "timeout_sec": 2,  # 2秒超時
            "max_retries": 1,
            "command": [sys.executable, "-c", "import time; time.sleep(10)"],
            "data_file": None,
            "chain_reconciliation": False
        }
        self.scheduler.state["sources"][task_id] = {
            "task_id": task_id,
            "name": "逾時測試工作",
            "interval_sec": 60,
            "timeout_sec": 2,
            "is_running": False,
            "last_attempt_at": None,
            "last_success_at": None,
            "data_coverage_range": "測試",
            "status": "INITIALIZED",
            "is_stale": False,
            "last_error": None,
            "retry_count": 0
        }

        start = time.time()
        res = self.scheduler.execute_task(task_id)
        duration = time.time() - start

        self.assertFalse(res["ok"])
        self.assertLess(duration, 5.0, "逾時中斷應在約 2 秒內完成強制中止")
        st = self.scheduler.state["sources"][task_id]
        self.assertEqual(st["status"], "FAILED")
        self.assertTrue(st["is_stale"])
        self.assertIn("逾時", st["last_error"])
        self.assertEqual(st["retry_count"], 1)

    def test_03_failure_cache_preservation_and_foodpanda_anti_spoofing(self):
        """測試 3：foodpanda 實體連線未通過時顯示「尚未連線」，保留既有成功快取，不竄改 last_success_at"""
        task_id = "foodpanda"
        cache_file = TASKS_CONFIG[task_id]["data_file"]
        self.assertTrue(os.path.exists(cache_file), "foodpanda 現存快取檔案應存在")

        with open(cache_file, "r", encoding="utf-8") as f:
            before_cache = json.load(f)
        before_success_at = before_cache.get("last_success_at")

        # 執行 foodpanda 同步
        res = self.scheduler.execute_task(task_id)

        # 驗證結果：因目前官方後台需要重新登入，應判定為尚未連線
        self.assertFalse(res["ok"])
        st = self.scheduler.state["sources"][task_id]
        self.assertEqual(st["status"], "DISCONNECTED")
        self.assertEqual(st["connection_status"], "尚未連線")
        self.assertTrue(st["is_stale"])
        self.assertIsNotNone(st["last_attempt_at"])
        self.assertIsNotNone(st["last_error"])

        # 驗證快取檔案：資料結構完整保留，last_success_at 絕不竄改為現在時間
        with open(cache_file, "r", encoding="utf-8") as f:
            after_cache = json.load(f)

        self.assertIn("suiyi", after_cache["stores"])
        self.assertEqual(after_cache.get("last_success_at"), before_success_at, "不可竄改 last_success_at 冒充同步成功")
        self.assertEqual(after_cache.get("connection_status"), "尚未連線")
        self.assertTrue(after_cache.get("is_stale"))

    def test_04_bank_deposits_sync_and_chained_reconciliation(self):
        """測試 4：玉山銀行明細檔更新成功，資料涵蓋日期計算，並連鎖觸發重新對帳"""
        task_id = "bank_deposits"
        res = self.scheduler.execute_task(task_id, is_chain=True)
        self.assertTrue(res["ok"], f"銀行明細讀取應成功：{res.get('error')}")

        st = self.scheduler.state["sources"][task_id]
        self.assertEqual(st["status"], "SUCCESS")
        self.assertFalse(st["is_stale"])
        self.assertIsNotNone(st["last_success_at"])
        self.assertIn("2026", st["data_coverage_range"])

        # 測試連鎖對帳任務
        rec_res = self.scheduler.execute_task("reconciliation", is_chain=True)
        self.assertTrue(rec_res["ok"], f"連鎖對帳任務應成功：{rec_res.get('error')}")
        rec_st = self.scheduler.state["sources"]["reconciliation"]
        self.assertIn(rec_st["status"], ["SUCCESS", "PUBLISH_FAILED"])
        self.assertFalse(rec_st["is_stale"])

        # 驗證對帳產出結果檔案
        audit_file = TASKS_CONFIG["reconciliation"]["data_file"]
        self.assertTrue(os.path.exists(audit_file), "delivery_audit_results.json 應產出")
        with open(audit_file, "r", encoding="utf-8") as f:
            audit_data = json.load(f)
        self.assertIn("summary", audit_data)
        self.assertIn("payout_audits", audit_data)

    def test_05_state_persistence(self):
        """測試 5：排程器狀態持久化至 scheduler_state.json"""
        self.scheduler.save_state()
        state_path = os.path.join(BASE_DIR, "scheduler_state.json")
        self.assertTrue(os.path.exists(state_path))
        with open(state_path, "r", encoding="utf-8") as f:
            saved_state = json.load(f)
        self.assertIn("sources", saved_state)
        for expected in ["linepay", "ubereats", "foodpanda", "google_sheets", "bank_deposits", "dilute", "reconciliation"]:
            self.assertIn(expected, saved_state["sources"])

    def test_06_schedule_intervals_compliance(self):
        """測試 6：驗證後台每5分鐘 (300s)、營運表與銀行每小時 (3600s)、連鎖旗標全數啟用"""
        self.assertEqual(TASKS_CONFIG["linepay"]["interval_sec"], 300, "LINE Pay 應為每 5 分鐘 (300s)")
        self.assertEqual(TASKS_CONFIG["ubereats"]["interval_sec"], 300, "Uber Eats 應為每 5 分鐘 (300s)")
        self.assertEqual(TASKS_CONFIG["foodpanda"]["interval_sec"], 300, "foodpanda 應為每 5 分鐘 (300s)")
        self.assertEqual(TASKS_CONFIG["dilute"]["interval_sec"], 300, "DILUTE 財報數據應為每 5 分鐘 (300s)")
        self.assertEqual(TASKS_CONFIG["google_sheets"]["interval_sec"], 3600, "Google 總表應為每小時 (3600s)")
        self.assertEqual(TASKS_CONFIG["bank_deposits"]["interval_sec"], 3600, "玉山銀行明細檔應為每小時 (3600s)")

        # 驗證連鎖對帳旗標
        for tid in ["linepay", "ubereats", "foodpanda", "dilute", "google_sheets", "bank_deposits"]:
            self.assertTrue(TASKS_CONFIG[tid].get("chain_reconciliation"), f"{tid} 應啟用 chain_reconciliation")

    def test_07_sync_success_triggers_reconciliation(self):
        """測試 7：單一來源（如 LINE Pay、Uber Eats、DILUTE）同步成功後，確認觸發 schedule_reconciliation"""
        with patch.object(self.scheduler, "schedule_reconciliation") as mock_sched_rec:
            with patch.object(self.scheduler, "verify_source_sync_success", return_value=(True, "完整同步成功")):
                # 模擬執行一個快速成功的指令
                test_task = "linepay"
                orig_cmd = TASKS_CONFIG[test_task]["command"]
                try:
                    TASKS_CONFIG[test_task]["command"] = [sys.executable, "-c", "print('OK')"]
                    res = self.scheduler.execute_task(test_task, is_chain=False)
                    self.assertTrue(res["ok"])
                    mock_sched_rec.assert_called_once_with(trigger_source=test_task)
                finally:
                    TASKS_CONFIG[test_task]["command"] = orig_cmd

    def test_08_sync_failure_blocks_reconciliation(self):
        """測試 8：同步失敗（登入失敗、部分門市缺漏、未連線）時，嚴格阻擋對帳觸發，保留舊快取"""
        with patch.object(self.scheduler, "schedule_reconciliation") as mock_sched_rec:
            with patch.object(self.scheduler, "verify_source_sync_success", return_value=(False, "登入失敗：帳號密碼過期")):
                test_task = "ubereats"
                orig_cmd = TASKS_CONFIG[test_task]["command"]
                before_success = self.scheduler.state["sources"][test_task].get("last_success_at")
                try:
                    TASKS_CONFIG[test_task]["command"] = [sys.executable, "-c", "print('Login Error')"]
                    res = self.scheduler.execute_task(test_task, is_chain=False)
                    self.assertFalse(res["ok"])
                    mock_sched_rec.assert_not_called()
                    st = self.scheduler.state["sources"][test_task]
                    self.assertTrue(st["is_stale"])
                    self.assertEqual(st["last_success_at"], before_success, "失敗時不可更新 last_success_at")
                finally:
                    TASKS_CONFIG[test_task]["command"] = orig_cmd

    def test_09_multiple_sources_coalesced_reconciliation(self):
        """測試 9：多個來源接連完成時，合併重算，避免多執行緒同時並行執行對帳"""
        rec_execution_count = 0
        executed_triggers = []

        def fake_execute_rec(trigger_source):
            nonlocal rec_execution_count
            rec_execution_count += 1
            executed_triggers.append(trigger_source)
            time.sleep(0.1)  # 模擬耗時

        with patch.object(self.scheduler, "_execute_reconciliation", side_effect=fake_execute_rec):
            # 同時發起兩個來源觸發
            self.scheduler.schedule_reconciliation("linepay")
            self.scheduler.schedule_reconciliation("ubereats")

            # 等待 worker 完成
            time.sleep(0.4)

            # 應該合併執行（第一次由 linepay 啟動，進行中收到 ubereats 設定 pending，完成後補跑一次合併）
            self.assertEqual(rec_execution_count, 2)
            self.assertEqual(executed_triggers[0], "linepay")
            self.assertIn("ubereats", executed_triggers[1])
            self.assertFalse(self.scheduler.reconcile_running)
            self.assertFalse(self.scheduler.reconcile_pending)

    def test_10_pending_flag_new_data_during_reconciliation(self):
        """測試 10：對帳重算期間若又有新資料更新完成，記錄待處理旗標，完成後再跑一次，不漏算"""
        run_count = 0
        observed_triggers = []

        def slow_reconcile(trigger_source):
            nonlocal run_count
            run_count += 1
            observed_triggers.append(trigger_source)
            # 在第一次執行期間，模擬有新來源 (dilute, bank_deposits) 完成並呼叫排程
            if run_count == 1:
                time.sleep(0.05)
                self.scheduler.schedule_reconciliation("dilute")
                self.scheduler.schedule_reconciliation("bank_deposits")
            time.sleep(0.1)

        with patch.object(self.scheduler, "_execute_reconciliation", side_effect=slow_reconcile):
            self.scheduler.schedule_reconciliation("initial_test")

            # 等待全部執行完成
            time.sleep(0.5)

            # 驗證總共跑了 2 次，且第二次的觸發包含 dilute 與 bank_deposits
            self.assertEqual(run_count, 2, "重算期間收到新資料應補跑第二次")
            self.assertEqual(observed_triggers[0], "initial_test")
            self.assertIn("bank_deposits", observed_triggers[1])
            self.assertIn("dilute", observed_triggers[1])
            self.assertFalse(self.scheduler.reconcile_running)
            self.assertFalse(self.scheduler.reconcile_pending)

    def test_11_publish_failure_independent_reporting(self):
        """測試 11：GitHub Pages 發布失敗時，對帳狀態獨立標示為 PUBLISH_FAILED，絕不回報成功"""
        with patch.object(self.scheduler, "publish_to_github_pages", return_value={"ok": False, "error": "Git push 網路連線逾時"}):
            with patch.object(subprocess, "run") as mock_subproc:
                mock_subproc.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
                res = self.scheduler._execute_reconciliation("測試發布失敗情境")

                self.assertFalse(res["ok"], "發布失敗時整體對帳流程不得回報 ok=True")
                self.assertEqual(res.get("stage"), "github_pages_publish")
                self.assertIn("Git push 網路連線逾時", res.get("error"))

                rec_st = self.scheduler.state["sources"]["reconciliation"]
                self.assertEqual(rec_st["status"], "PUBLISH_FAILED", "狀態應獨立標為 PUBLISH_FAILED")
                self.assertEqual(rec_st["publish_status"], "FAILED")
                self.assertIn("Git push 網路連線逾時", rec_st["last_error"])


if __name__ == "__main__":
    unittest.main()
