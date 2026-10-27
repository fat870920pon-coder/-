import os
import sys
import json
import subprocess
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = 8080

class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "online", "message": "即時連線後端運行中"}).encode("utf-8"))
            return
        elif parsed.path in ("", "/"):
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/sync":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
            req_data = {}
            try:
                req_data = json.loads(body)
            except:
                pass
            target_month = req_data.get("month", "2026-10")

            try:
                # 1. 執行玉山銀行存摺下載與自動沖銷勾稽 (從 Google 雲端重新下載最新銀行帳並產出沖銷傳票與看板)
                bank_proc = subprocess.run(
                    [sys.executable, os.path.join(BASE_DIR, "bank_reconciliation.py"), "--month", target_month],
                    cwd=BASE_DIR, capture_output=True, text=True
                )
                if bank_proc.returncode != 0:
                    print("⚠️ bank_reconciliation error:", bank_proc.stderr)
                    # 若銀行勾稽失敗，執行基礎對帳試算兜底
                    import reconciliation_summary
                    summary_data = reconciliation_summary.calculate_monthly_summary(target_month=target_month)
                    if summary_data:
                        reconciliation_summary.print_and_export_summary(summary_data, target_month=target_month)

                data_path = os.path.join(BASE_DIR, "dashboard_data.json")
                with open(data_path, "r", encoding="utf-8") as f:
                    fresh_json = json.load(f)

                updated_at = fresh_json.get(target_month, {}).get("updated_at", "")

                # 非同步背景推送 GitHub Pages，不阻塞前端回應
                def bg_git_push():
                    try:
                        subprocess.run(
                            ["git", "add", "index.html", "docs/", "output/", "dashboard_data.json", "reconciliation_balance.json", "invoices_record.json", "uploaded_invoices/"],
                            cwd=BASE_DIR, capture_output=True, text=True
                        )
                        diff_check = subprocess.run(["git", "diff", "--staged", "--quiet"], cwd=BASE_DIR)
                        if diff_check.returncode != 0:
                            subprocess.run(
                                ["git", "commit", "-m", f"chore(auto-sync): live sync from dashboard at {updated_at}"],
                                cwd=BASE_DIR, capture_output=True, text=True
                            )
                            subprocess.run(["git", "push", "origin", "main"], cwd=BASE_DIR, capture_output=True, text=True)
                    except Exception as ge:
                        print("Background git push error:", ge)

                threading.Thread(target=bg_git_push, daemon=True).start()

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                resp = {
                    "success": True,
                    "updated_at": updated_at,
                    "month": target_month,
                    "data": fresh_json
                }
                self.wfile.write(json.dumps(resp, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
            return

        elif parsed.path == "/api/invoices/upload":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
            req_data = {}
            try:
                req_data = json.loads(body)
            except:
                pass

            target_month = req_data.get("month", "2026-10")
            store = req_data.get("store", "月宮")
            platform = req_data.get("platform", "LINE Pay (連加網路)")
            inv_no = req_data.get("invoice_no", "").strip().upper()
            inv_date = req_data.get("invoice_date", "")
            amt_total = float(req_data.get("amount_total", 0.0) or 0.0)
            note = req_data.get("note", "").strip()

            if not inv_no or amt_total <= 0:
                self.send_response(400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": "請提供有效的發票號碼與含稅金額"}).encode("utf-8"))
                return

            try:
                import base64
                import time
                from datetime import datetime

                upload_dir = os.path.join(BASE_DIR, "uploaded_invoices")
                os.makedirs(upload_dir, exist_ok=True)

                saved_file_rel = ""
                orig_file_name = req_data.get("file_name", "")
                if req_data.get("file_data") and orig_file_name:
                    f_data = req_data["file_data"]
                    if "," in f_data:
                        f_data = f_data.split(",", 1)[1]
                    raw_bytes = base64.b64decode(f_data)
                    safe_name = os.path.basename(orig_file_name).replace(" ", "_")
                    ts = int(time.time())
                    dest_file_name = f"{ts}_{safe_name}"
                    full_dest = os.path.join(upload_dir, dest_file_name)
                    with open(full_dest, "wb") as f:
                        f.write(raw_bytes)
                    saved_file_rel = f"uploaded_invoices/{dest_file_name}"

                inv_record_path = os.path.join(BASE_DIR, "invoices_record.json")
                records = {}
                if os.path.exists(inv_record_path):
                    try:
                        with open(inv_record_path, "r", encoding="utf-8") as f:
                            records = json.load(f)
                    except:
                        records = {}

                if target_month not in records:
                    records[target_month] = []

                amt_untaxed = round(amt_total / 1.05)
                vat = round(amt_total - amt_untaxed)
                new_record = {
                    "id": f"inv_{int(time.time()*1000)}",
                    "store": store,
                    "platform": platform,
                    "invoice_no": inv_no,
                    "invoice_date": inv_date or datetime.now().strftime("%Y-%m-%d"),
                    "amount_total": amt_total,
                    "amount_untaxed": amt_untaxed,
                    "vat_tax": vat,
                    "file_path": saved_file_rel,
                    "file_name": orig_file_name,
                    "note": note,
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
                records[target_month].append(new_record)

                with open(inv_record_path, "w", encoding="utf-8") as f:
                    json.dump(records, f, ensure_ascii=False, indent=2)

                # 重新執行對帳試算與匯出
                import reconciliation_summary
                summary_data = reconciliation_summary.calculate_monthly_summary(target_month=target_month)
                if summary_data:
                    reconciliation_summary.print_and_export_summary(summary_data, target_month=target_month)

                data_path = os.path.join(BASE_DIR, "dashboard_data.json")
                with open(data_path, "r", encoding="utf-8") as f:
                    fresh_json = json.load(f)

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                resp = {
                    "success": True,
                    "message": f"發票 {inv_no} 已登記並成功扣抵手續費！",
                    "record": new_record,
                    "data": fresh_json
                }
                self.wfile.write(json.dumps(resp, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
            return

        elif parsed.path == "/api/invoices/delete":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
            req_data = {}
            try:
                req_data = json.loads(body)
            except:
                pass

            target_month = req_data.get("month", "2026-10")
            inv_id = req_data.get("id", "")

            try:
                inv_record_path = os.path.join(BASE_DIR, "invoices_record.json")
                if os.path.exists(inv_record_path):
                    with open(inv_record_path, "r", encoding="utf-8") as f:
                        records = json.load(f)
                    if target_month in records:
                        records[target_month] = [r for r in records[target_month] if r.get("id") != inv_id]
                        with open(inv_record_path, "w", encoding="utf-8") as f:
                            json.dump(records, f, ensure_ascii=False, indent=2)

                # 重新執行對帳試算與匯出
                import reconciliation_summary
                summary_data = reconciliation_summary.calculate_monthly_summary(target_month=target_month)
                if summary_data:
                    reconciliation_summary.print_and_export_summary(summary_data, target_month=target_month)

                data_path = os.path.join(BASE_DIR, "dashboard_data.json")
                with open(data_path, "r", encoding="utf-8") as f:
                    fresh_json = json.load(f)

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                resp = {
                    "success": True,
                    "message": "發票紀錄已刪除，對帳差額已重新計算",
                    "data": fresh_json
                }
                self.wfile.write(json.dumps(resp, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
            return

        self.send_error(404, "Endpoint not found")

def run():
    server = HTTPServer(("0.0.0.0", PORT), DashboardHandler)
    print(f"🚀 本機即時對帳服務已啟動：http://localhost:{PORT}")
    print("👉 在網頁點擊【即時更新】，會直接執行 Python 重新計算並即時反映最新數據！")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服務已停止。")

if __name__ == "__main__":
    run()
