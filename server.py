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
                # 執行完整計算並匯出 JSON / Excel / HTML
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
                            ["git", "add", "index.html", "docs/", "output/", "dashboard_data.json", "reconciliation_balance.json"],
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
