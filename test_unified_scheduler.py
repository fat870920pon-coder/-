#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_unified_scheduler.py
-------------------------
排程器核心規範與容錯實測套件：
1. 防重複執行 (Mutual Exclusion)：當工作處於 is_running 狀態時，防止並行重啟。
2. 逾時中斷 (Timeout Handling)：當工作耗時超過 timeout_sec 時，強制中斷行程並記錄逾時。
3. 失敗容錯與舊快取保護 (Stale Cache Retention)：同步失敗時保留成功快取，明確標示 is_stale: True，不更新 last_success_at。
4. foodpanda 防偽驗證：未連線成功時回傳 DISCONNECTED 與「尚未連線」，禁止更新時間冒充同步。
5. 銀行明細更新與連鎖對帳 (Chained Trigger)：明細更新成功後連鎖觸發重新對帳，產出 delivery_audit_results.json。
"""

import os
import sys
import time
import json
import unittest
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
        res = self.scheduler.execute_task(task_id, is_chain=True)  # 先以手動模式測單元
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
        self.assertEqual(rec_st["status"], "SUCCESS")
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
        for expected in ["linepay", "ubereats", "foodpanda", "google_sheets", "bank_deposits", "reconciliation"]:
            self.assertIn(expected, saved_state["sources"])


if __name__ == "__main__":
    unittest.main()
