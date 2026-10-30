#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_pending_cases_generator.py
-------------------------------
統一待查清單產生器驗收測試套件：
1. 案件 ID 唯一且跨次更新穩定。
2. 每筆來源、差額公式與金額可追溯。
3. 13 案與 8 筆差異有明確對應或待確認原因。
4. 同款項多角度核對不重複加總（分項統計與 group_id / related_case_ids 檢核）。
5. 測試用 400 案件不進正式清單。
6. 來源缺值保留 null，不被強行當作 0 或湊平。
7. 敏感欄位安全性檢查（玉山銀行帳號遮罩，無任何密碼、Cookie、金鑰個資洩漏）。
8. JSON 與 Markdown 生成一致性。
"""

import unittest
import json
import os
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ALT_DIR = "/Users/liujiaxiang/Desktop/腳本資料夾/月下/文中會計傳票產生器"

from generate_pending_cases import generate_pending_cases, generate_handover_markdown_table

class TestPendingCasesGenerator(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.payload = generate_pending_cases()
        cls.cases = cls.payload.get("cases", [])
        cls.summary = cls.payload.get("summary", {})

    def test_01_case_ids_unique_and_stable(self):
        """驗證 1: 案件 ID 唯一且跨次更新穩定"""
        ids = [c["case_id"] for c in self.cases]
        self.assertEqual(len(ids), len(set(ids)), "案件 ID 必須唯一且無重複")
        self.assertEqual(len(ids), 13, "待查清單應剛好包含 13 案")

        expected_ids = {
            "PENDING-YG-CASH-B11",
            "PENDING-SY-CASH-B10",
            "PENDING-SY-FP-09H1-BANK",
            "PENDING-SY-FP-10H1-TRANSIT",
            "PENDING-SY-UE-1027-TRANSIT",
            "PENDING-YG-UE-1027-TRANSIT",
            "PENDING-SY-FP-08-REV",
            "PENDING-SY-FP-09-REV",
            "PENDING-SY-UE-07-REV",
            "PENDING-SY-UE-08-REV",
            "PENDING-SY-UE-09-REV",
            "PENDING-YG-UE-08-REV",
            "PENDING-YG-UE-09-REV"
        }
        self.assertEqual(set(ids), expected_ids, "案件 ID 集合必須完全符合規範")

        # 再次呼叫生成，驗證 ID 穩定性
        payload2 = generate_pending_cases()
        ids2 = [c["case_id"] for c in payload2.get("cases", [])]
        self.assertEqual(ids, ids2, "跨次執行產出之案件 ID 必須完全一致")

    def test_02_traceability_and_formula(self):
        """驗證 2: 每筆來源、差額公式與金額可追溯"""
        for c in self.cases:
            self.assertTrue(c.get("source_file"), f"案件 {c['case_id']} 必須有 source_file")
            self.assertTrue(c.get("sheet_or_row"), f"案件 {c['case_id']} 必須有 sheet_or_row")
            self.assertTrue(c.get("batch_id"), f"案件 {c['case_id']} 必須有 batch_id")
            self.assertTrue(c.get("diff_formula"), f"案件 {c['case_id']} 必須有差額公式")
            self.assertTrue(c.get("diff_sign_definition"), f"案件 {c['case_id']} 必須有正負號定義")
            self.assertIsNotNone(c.get("difference"), f"案件 {c['case_id']} 必須有明確差額數值")

    def test_03_mapping_13_cases_to_8_audit_diffs(self):
        """驗證 3: 13 案與 8 筆差異有明確對應或待確認原因"""
        mapped_cases = [c for c in self.cases if c["public_audit_mapping"]["mapped"]]
        unmapped_cases = [c for c in self.cases if not c["public_audit_mapping"]["mapped"]]

        self.assertEqual(len(mapped_cases), 8, "應有 8 案精確對應至公開稽核差異")
        self.assertEqual(len(unmapped_cases), 5, "應有 5 案為門市現金 (2案) 或正常在途款 (3案)")

        # 驗證 8 筆映射之索引不重複且落於 1~8
        mapped_indices = [c["public_audit_mapping"]["audit_item_index"] for c in mapped_cases]
        self.assertEqual(sorted(mapped_indices), [1, 2, 3, 4, 5, 6, 7, 8], "8 筆映射必須完整覆蓋 Item 1~8")

        # 驗證 5 筆未映射案件均有充分合理的審計原因
        for uc in unmapped_cases:
            self.assertTrue(len(uc["public_audit_mapping"]["reason"]) > 10, f"{uc['case_id']} 必須有清晰之未映射原因說明")

    def test_04_anti_double_counting_and_categories(self):
        """驗證 4: 同款項多角度核對不重複加總"""
        cat_totals = self.summary.get("categorized_totals", {})
        self.assertIn("cash_verification", cat_totals)
        self.assertIn("delivery_in_transit", cat_totals)
        self.assertIn("delivery_bank_unmatched", cat_totals)
        self.assertIn("delivery_revenue_differences", cat_totals)

        # 檢驗分類金額
        self.assertEqual(cat_totals["cash_verification"]["pending_bank_deposit"], 16934.0)
        self.assertEqual(cat_totals["cash_verification"]["declared_shortage"], -1020.0)
        self.assertEqual(cat_totals["delivery_in_transit"]["total_in_transit"], 38224.0)
        self.assertEqual(cat_totals["delivery_bank_unmatched"]["total_unmatched"], 13332.0)
        self.assertEqual(cat_totals["delivery_revenue_differences"]["net_difference"], 1536.0)

        # 檢驗 group_id 與 related_case_ids 關聯性
        fp_bank_case = next(c for c in self.cases if c["case_id"] == "PENDING-SY-FP-09H1-BANK")
        self.assertEqual(fp_bank_case["group_id"], "GROUP-SY-FP-2026-Q3")
        self.assertIn("PENDING-SY-FP-08-REV", fp_bank_case["related_case_ids"])
        self.assertIn("PENDING-SY-FP-09-REV", fp_bank_case["related_case_ids"])

        # 檢驗總清單中絕無粗暴直接加總
        self.assertIn("anti_double_counting_warning", self.summary)

    def test_05_synthetic_400_test_case_excluded(self):
        """驗證 5: 測試用 400 案件不進正式清單"""
        for c in self.cases:
            self.assertNotEqual(c["case_id"], "TEST-YG-UE-ROLLOVER-400", "測試案 TEST-YG-UE-ROLLOVER-400 嚴禁混入正式清單")
            if "400" in str(c["difference"]):
                self.fail(f"正式清單不應包含合成 400 元測試案件: {c}")

    def test_06_null_preservation_for_missing_values(self):
        """驗證 6: 缺值保留 null，不被強行當作 0 或湊平"""
        # 現金案不應有平台銷售額或佣金抽成
        cash_cases = [c for c in self.cases if c["category_code"] == "CASH_DEPOSIT"]
        for cc in cash_cases:
            self.assertIsNone(cc["platform_sales"], f"現金案 {cc['case_id']} platform_sales 應為 None/null")
            self.assertIsNone(cc["fee_amount"], f"現金案 {cc['case_id']} fee_amount 應為 None/null")

        # 在途款案不應有單一 POS 銷售
        transit_cases = [c for c in self.cases if c["category_code"] == "DELIVERY_PAYOUT" and c["status"] == "PENDING_BANK_UPDATE"]
        for tc in transit_cases:
            self.assertIsNone(tc["pos_amount"], f"在途款案 {tc['case_id']} pos_amount 應為 None/null")

    def test_07_sensitive_data_masking(self):
        """驗證 7: 敏感欄位安全性檢查（玉山銀行帳號遮罩，無任何密碼、Cookie、金鑰個資洩漏）"""
        payload_str = json.dumps(self.payload, ensure_ascii=False)

        # 嚴禁完整玉山銀行帳號出現
        self.assertNotIn("0462940067756", payload_str, "隨意小吃完整玉山帳號必須脫敏遮罩")
        self.assertNotIn("0303940018209", payload_str, "月宮完整玉山帳號必須脫敏遮罩")

        # 嚴禁帳密、Cookie、金鑰
        forbidden_keywords = ["password", "cookie", "secret", "private_key", "client_secret"]
        for kw in forbidden_keywords:
            self.assertNotIn(f'"{kw}"', payload_str.lower(), f"公開清單不得包含機密鍵名 {kw}")

    def test_08_markdown_generation_consistency(self):
        """驗證 8: Markdown 產生器產出格式與 JSON 數據一致"""
        md_text = generate_handover_markdown_table(self.payload)
        for c in self.cases:
            self.assertIn(c["case_id"], md_text, f"Markdown 表格中必須包含 {c['case_id']}")
        self.assertIn("TEST-YG-UE-ROLLOVER-400", md_text, "Markdown 必須於專屬測試專區包含測試隔離案例")

if __name__ == "__main__":
    unittest.main()
