import os
import sys
import json
import re
from datetime import datetime, date, timedelta
import openpyxl

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
STATE_FILE = os.path.join(BASE_DIR, "reconciliation_balance.json")

def load_month_payload(target_month, state_data):
    m_clean = target_month.replace("-", "")
    month_data = state_data.get(target_month, {})
    updated_at = month_data.get("updated_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    stores_data = month_data.get("stores", {})

    bank_excel = os.path.join(OUTPUT_DIR, f"本月對帳差額與應收餘額表_{m_clean}.xlsx")
    cleared_items = []
    if os.path.exists(bank_excel):
        try:
            wb = openpyxl.load_workbook(bank_excel, data_only=True)
            if "玉山單筆沖銷對照" in wb.sheetnames:
                ws2 = wb["玉山單筆沖銷對照"]
                for r in list(ws2.iter_rows(values_only=True))[1:]:
                    if r[0] is not None:
                        alloc_val = str(r[7] or "") if len(r) >= 9 else ""
                        rem_val = str(r[8] or "") if len(r) >= 9 else str(r[7] or "")
                        is_cross = bool("跨月" in alloc_val or "上月" in alloc_val or "拆分" in alloc_val)
                        cleared_items.append({
                            "idx": r[0],
                            "store": r[1],
                            "bank_date": str(r[2]),
                            "category": str(r[3]),
                            "bank_amt": float(r[4] or 0),
                            "cleared_amt": float(r[5] or 0),
                            "period": str(r[6]),
                            "revenue_alloc": alloc_val,
                            "is_cross_month": is_cross,
                            "remark": rem_val
                        })
        except Exception:
            pass

    total_pos = 0.0
    total_bank = 0.0
    total_inv = 0.0
    total_pending_cash = 0.0

    total_unpaid_cash = 0.0
    total_declared_pending = 0.0
    all_pending_alerts = []
    for skey, sinfo in stores_data.items():
        if skey.startswith("_"):
            continue
        channels = sinfo.get("channels", {})
        for ccode, cdata in channels.items():
            total_pos += cdata.get("pos_sales", 0.0)
            total_bank += cdata.get("bank_cleared", 0.0)
            total_inv += cdata.get("invoice_cleared", 0.0)
            if ccode == "1130-01":
                diff = cdata.get("diff", 0.0)
                if diff > 0:
                    total_pending_cash += diff

        for d in sinfo.get("daily_cash", []):
            st = d.get("audit_status", "")
            amt = d.get("cash", 0.0)
            if st == "UNPAID":
                total_unpaid_cash += amt
            elif st == "DECLARED_PENDING_BANK":
                total_declared_pending += amt
        
        alerts = sinfo.get("pending_alerts", [])
        for a in alerts:
            all_pending_alerts.append(a)

    all_pending_alerts.sort(key=lambda x: (0 if x.get("audit_status") == "UNPAID" else 1, -x.get("delay_days", 0)))
    bridge_data = month_data.get("_bridge", {})

    return {
        "target_month": target_month,
        "updated_at": updated_at,
        "kpi": {
            "total_pos": total_pos,
            "total_bank": total_bank,
            "total_inv": total_inv,
            "total_pending_cash": total_pending_cash,
            "total_unpaid_cash": total_unpaid_cash,
            "total_declared_pending": total_declared_pending,
            "alert_count": len(all_pending_alerts)
        },
        "bridge": bridge_data,
        "stores": stores_data,
        "alerts": all_pending_alerts,
        "cleared_items": cleared_items
    }

def generate_html_dashboard(target_month="2026-10", sync_cloud=False):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    m_clean = target_month.replace("-", "")

    state_data = {}
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state_data = json.load(f)

    # 取得所有已存在月份，排序新至舊
    all_months = sorted([k for k in state_data.keys() if re.match(r"^\d{4}-\d{2}$", str(k))], reverse=True)
    if target_month not in all_months:
        all_months.insert(0, target_month)

    multi_months_data = {}
    for m in all_months:
        multi_months_data[m] = load_month_payload(m, state_data)

    current_data = multi_months_data.get(target_month, multi_months_data[all_months[0]])

    # 輸出獨立 JSON 供前端無快取即時拉取 (Instant Refresh)
    json_payload_str = json.dumps(multi_months_data, ensure_ascii=False)
    for json_path in [
        os.path.join(BASE_DIR, "dashboard_data.json"),
        os.path.join(BASE_DIR, "docs", "dashboard_data.json"),
        os.path.join(OUTPUT_DIR, "dashboard_data.json")
    ]:
        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as jf:
            jf.write(json_payload_str)

    # 產生月份選項 HTML
    month_options_html = ""
    for m in all_months:
        y, mo = m.split("-")
        selected_attr = "selected" if m == target_month else ""
        month_options_html += f'<option value="{m}" {selected_attr}>{y} 年 {mo} 月份</option>'

    html_content = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
  <meta http-equiv="Pragma" content="no-cache">
  <meta http-equiv="Expires" content="0">
  <title>月下餐飲集團 · 營運對帳與雙層審核看板</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "PingFang TC", "Noto Sans TC", "Microsoft JhengHei", "Segoe UI", Roboto, sans-serif;
      background-color: #f8fafc;
      color: #1e293b;
    }}
    .calendar-cell {{
      min-height: 108px;
      transition: transform 0.15s ease, box-shadow 0.15s ease;
    }}
    .calendar-cell:hover {{
      transform: translateY(-2px);
      box-shadow: 0 6px 14px rgba(0,0,0,0.07);
    }}
    .badge-deposit {{
      background-color: #ecfdf5;
      color: #065f46;
      border: 1px solid #a7f3d0;
    }}
    .badge-overdue {{
      background-color: #fef2f2;
      color: #991b1b;
      border: 1px solid #fecaca;
    }}
    .badge-warning {{
      background-color: #fffbeb;
      color: #92400e;
      border: 1px solid #fde68a;
    }}
    .badge-info {{
      background-color: #eff6ff;
      color: #1e40af;
      border: 1px solid #bfdbfe;
    }}
  </style>
</head>
<body class="p-3 sm:p-6 md:p-8 max-w-7xl mx-auto">

  <!-- Toast Notification -->
  <div id="sync-toast" class="fixed top-4 right-4 z-50 hidden items-center gap-2 px-4 py-2.5 rounded-xl bg-slate-900 text-white text-xs font-semibold shadow-lg transition-all">
    <i class="fa-solid fa-circle-check text-emerald-400"></i>
    <span id="sync-toast-text">已同步最新帳務資料</span>
  </div>

  <!-- Header -->
  <header class="flex flex-col md:flex-row justify-between items-start md:items-center pb-5 border-b border-slate-200 gap-4">
    <div>
      <div class="flex items-center gap-3">
        <span class="p-2.5 bg-indigo-600 text-white rounded-xl shadow-md">
          <i class="fa-solid fa-shield-halved text-xl"></i>
        </span>
        <div>
          <h1 class="text-xl sm:text-2xl font-bold tracking-tight text-slate-900">月下餐飲集團 · 營運對帳與雙層審核看板</h1>
          <p class="text-xs sm:text-sm text-slate-500 mt-0.5">第一層：優先判定營運總表 U 欄【存款金額】 ➔ 第二層：核對玉山銀行存摺入帳流水</p>
        </div>
      </div>
    </div>
    <div class="flex flex-wrap items-center gap-2.5 bg-white px-3.5 py-2 rounded-xl shadow-sm border border-slate-200 w-full md:w-auto justify-between md:justify-end">
      <div class="flex items-center gap-2">
        <i class="fa-regular fa-calendar text-indigo-600"></i>
        <div>
          <div class="text-[10px] text-slate-400 font-medium">選擇查帳月份</div>
          <select id="month-select" onchange="switchMonth(this.value)" class="font-bold text-slate-800 text-sm bg-transparent cursor-pointer focus:outline-none pr-1">
            {month_options_html}
          </select>
        </div>
      </div>
      <div class="h-7 w-px bg-slate-200"></div>
      <div class="text-[11px] text-slate-500">
        最後更新：<span class="font-mono font-semibold text-slate-700" id="header-updated-at">{current_data['updated_at']}</span>
      </div>
      <button onclick="refreshLiveData(true)" id="btn-live-refresh" class="px-3 py-1.5 bg-indigo-50 hover:bg-indigo-100 text-indigo-700 rounded-lg text-xs font-bold transition-all flex items-center gap-1.5 border border-indigo-200 cursor-pointer active:scale-95">
        <i class="fa-solid fa-rotate" id="refresh-icon"></i>
        <span>即時刷新</span>
      </button>
    </div>
  </header>

  <!-- Store Switcher Tabs -->
  <div class="mt-5 flex flex-wrap items-center justify-between gap-2">
    <div class="flex flex-wrap gap-2">
      <button onclick="switchStore('ALL')" id="tab-ALL" class="store-tab px-4 py-2.5 rounded-xl font-bold text-sm transition-all shadow-sm bg-indigo-600 text-white cursor-pointer">
        <i class="fa-solid fa-layer-group mr-1.5"></i> 集團合併總覽
      </button>
      <button onclick="switchStore('月宮')" id="tab-月宮" class="store-tab px-4 py-2.5 rounded-xl font-bold text-sm transition-all bg-white text-slate-600 hover:bg-slate-100 border border-slate-200 cursor-pointer">
        <i class="fa-solid fa-moon mr-1.5 text-amber-500"></i> 月宮 (月下餐飲)
      </button>
      <button onclick="switchStore('隨意小吃')" id="tab-隨意小吃" class="store-tab px-4 py-2.5 rounded-xl font-bold text-sm transition-all bg-white text-slate-600 hover:bg-slate-100 border border-slate-200 cursor-pointer">
        <i class="fa-solid fa-utensils mr-1.5 text-rose-500"></i> 隨意小吃
      </button>
    </div>
    <div class="text-xs text-slate-500 hidden sm:flex items-center gap-2">
      <span class="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-emerald-50 text-emerald-700 border border-emerald-200 font-medium">
        <span class="w-2 h-2 rounded-full bg-emerald-500 animate-ping"></span> 點擊任意按鈕或日曆皆可 0 延遲即時切換
      </span>
    </div>
  </div>

  <!-- KPI Cards -->
  <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mt-5">
    <div class="bg-white p-5 rounded-2xl shadow-sm border border-slate-200 relative overflow-hidden">
      <div class="text-xs font-semibold uppercase tracking-wider text-slate-400">當月累計 POS 營收</div>
      <div class="text-2xl font-bold text-slate-800 mt-1.5 font-mono" id="kpi-pos">$ 0</div>
      <div class="text-xs text-slate-500 mt-1.5 flex items-center gap-1">
        <i class="fa-solid fa-cash-register text-indigo-500"></i> 含所有門市全通路業績
      </div>
    </div>

    <div class="bg-white p-5 rounded-2xl shadow-sm border border-slate-200 relative overflow-hidden">
      <div class="text-xs font-semibold uppercase tracking-wider text-slate-400">玉山銀行已入帳沖銷</div>
      <div class="text-2xl font-bold text-emerald-600 mt-1.5 font-mono" id="kpi-bank">$ 0</div>
      <div class="text-xs text-slate-500 mt-1.5 flex items-center gap-1">
        <i class="fa-solid fa-building-columns text-emerald-500"></i> 現金存入與金流撥款
      </div>
    </div>

    <div class="bg-white p-5 rounded-2xl shadow-sm border-2 border-rose-200 relative overflow-hidden">
      <div class="flex items-center justify-between">
        <div class="text-xs font-bold uppercase tracking-wider text-rose-600">待存入門市現金 (雙層審核)</div>
        <span class="text-[10px] px-1.5 py-0.5 bg-rose-50 text-rose-600 rounded font-bold border border-rose-200">點擊下方標籤篩選</span>
      </div>
      <div class="text-2xl font-bold text-rose-600 mt-1.5 font-mono" id="kpi-pending-cash">$ 0</div>
      <div class="text-[11px] mt-2 flex flex-wrap items-center gap-1.5" id="kpi-alert-badge">
        <button onclick="jumpAndFilterAudit('UNPAID')" class="px-2 py-0.5 rounded-md bg-rose-100 hover:bg-rose-200 text-rose-800 font-bold border border-rose-300 cursor-pointer transition-all" id="kpi-unpaid-sub" title="點擊查看留店未存明細">🔴 留店未存: $0</button>
        <button onclick="jumpAndFilterAudit('DECLARED_PENDING_BANK')" class="px-2 py-0.5 rounded-md bg-amber-100 hover:bg-amber-200 text-amber-900 font-bold border border-amber-300 cursor-pointer transition-all" id="kpi-declared-sub" title="點擊查看門市已申報在途明細">🟡 門市已報: $0</button>
      </div>
    </div>

    <div class="bg-white p-5 rounded-2xl shadow-sm border border-slate-200 relative overflow-hidden">
      <div class="text-xs font-semibold uppercase tracking-wider text-slate-400">發票已沖手續費 / 稅額</div>
      <div class="text-2xl font-bold text-blue-600 mt-1.5 font-mono" id="kpi-inv">$ 0</div>
      <div class="text-xs text-slate-500 mt-1.5 flex items-center gap-1">
        <i class="fa-solid fa-file-invoice text-blue-500"></i> 自動扣抵外送與金流費
      </div>
    </div>
  </div>

  <!-- Dual-Tier Audit Section (核心雙層審核即時互動專區 - 移至最顯眼位置) -->
  <div class="mt-6 bg-white rounded-2xl shadow-md border-2 border-indigo-100 overflow-hidden" id="alert-section">
    <div class="px-5 py-4 bg-gradient-to-r from-indigo-950 via-slate-900 to-indigo-950 text-white flex flex-col lg:flex-row justify-between items-start lg:items-center gap-3">
      <div>
        <div class="flex items-center gap-2">
          <span class="px-2 py-0.5 rounded bg-amber-400 text-slate-950 font-extrabold text-xs">雙層審核核心</span>
          <h2 class="font-bold text-base sm:text-lg" id="alert-title-text">門市現金雙層審核與異常追蹤</h2>
        </div>
        <p class="text-xs text-slate-300 mt-1">
          <span class="text-amber-300 font-semibold">第一層審核：</span>優先檢查營運總表 U 欄【存款金額】與 T 欄【存款時間】（支援多日合併累計） ➔ 
          <span class="text-emerald-300 font-semibold">第二層審核：</span>核對玉山銀行存摺是否實收入帳
        </p>
      </div>
      <!-- Instant Audit Status Filter Buttons -->
      <div class="flex flex-wrap items-center gap-1.5" id="audit-filter-pills">
        <button onclick="setAuditFilter('PENDING_ALL')" id="audit-pill-PENDING_ALL" class="audit-pill px-3 py-1.5 rounded-lg text-xs font-bold bg-amber-400 text-slate-950 shadow-sm cursor-pointer transition-all">
          🚨 待入帳與未存 (<span id="count-PENDING_ALL">0</span>)
        </button>
        <button onclick="setAuditFilter('DECLARED_PENDING_BANK')" id="audit-pill-DECLARED_PENDING_BANK" class="audit-pill px-3 py-1.5 rounded-lg text-xs font-bold bg-white/10 text-amber-200 hover:bg-white/20 border border-amber-400/30 cursor-pointer transition-all">
          🟡 門市已申報 (<span id="count-DECLARED_PENDING_BANK">0</span>)
        </button>
        <button onclick="setAuditFilter('UNPAID')" id="audit-pill-UNPAID" class="audit-pill px-3 py-1.5 rounded-lg text-xs font-bold bg-white/10 text-rose-200 hover:bg-white/20 border border-rose-400/30 cursor-pointer transition-all">
          🔴 門市未存 (<span id="count-UNPAID">0</span>)
        </button>
        <button onclick="setAuditFilter('MATCHED')" id="audit-pill-MATCHED" class="audit-pill px-3 py-1.5 rounded-lg text-xs font-bold bg-white/10 text-emerald-200 hover:bg-white/20 border border-emerald-400/30 cursor-pointer transition-all">
          🟢 雙審通過 (<span id="count-MATCHED">0</span>)
        </button>
        <button onclick="setAuditFilter('ALL_DAYS')" id="audit-pill-ALL_DAYS" class="audit-pill px-3 py-1.5 rounded-lg text-xs font-bold bg-white/10 text-white hover:bg-white/20 border border-white/20 cursor-pointer transition-all">
          📋 全月逐日總表 (<span id="count-ALL_DAYS">0</span>)
        </button>
      </div>
    </div>

    <!-- Cards Container (for Pending / Declared / Unpaid quick cards) -->
    <div class="p-5" id="audit-cards-wrapper">
      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3.5" id="alert-cards-container">
        <!-- Rendered by JS -->
      </div>
    </div>

    <!-- Full Dual-Tier Audit Table (逐日雙層審核完整對照表) -->
    <div class="border-t border-slate-200 overflow-x-auto" id="audit-table-wrapper">
      <div class="px-5 py-2.5 bg-slate-50 border-b border-slate-200 flex flex-wrap justify-between items-center gap-2">
        <span class="text-xs font-bold text-slate-700 flex items-center gap-1.5">
          <i class="fa-solid fa-table-list text-indigo-600"></i>
          <span>逐日雙層審核對照表（第一層：營運總表 U 欄存款金額 vs 第二層：玉山銀行存摺）</span>
        </span>
        <span class="text-[11px] text-slate-500" id="audit-table-subtitle">顯示篩選結果</span>
      </div>
      <table class="w-full text-left text-xs text-slate-700">
        <thead class="bg-slate-100/80 text-slate-600 font-bold border-b border-slate-200">
          <tr>
            <th class="py-2.5 px-4">門市</th>
            <th class="py-2.5 px-4">營業日期</th>
            <th class="py-2.5 px-4 text-right">當日 POS 現金</th>
            <th class="py-2.5 px-4 text-center bg-amber-50/60">第一層：營運總表 U 欄 (門市申報)</th>
            <th class="py-2.5 px-4 bg-amber-50/60">申報存款金額 / T 欄時間說明</th>
            <th class="py-2.5 px-4 text-center bg-emerald-50/60">第二層：玉山銀行存摺</th>
            <th class="py-2.5 px-4 bg-emerald-50/60">銀行實收入帳日 / 金額</th>
            <th class="py-2.5 px-4 text-center">雙層審核最終結論</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-slate-100" id="dual-audit-tbody">
          <!-- Rendered by JS -->
        </tbody>
      </table>
    </div>
  </div>

  <!-- Cross-Month Cash Flow vs Accrual Bridge Banner -->
  <div class="mt-6 bg-gradient-to-r from-slate-900 via-indigo-950 to-slate-900 rounded-2xl p-5 text-white shadow-sm relative overflow-hidden" id="bridge-container">
    <div class="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-4 relative z-10">
      <div>
        <div class="flex items-center gap-2">
          <span class="px-2.5 py-0.5 rounded-md bg-indigo-500/30 text-indigo-200 border border-indigo-400/30 text-xs font-bold">
            <i class="fa-solid fa-scale-balanced mr-1"></i> 權責發生 vs 存摺收付跨月調節
          </span>
          <span class="text-xs text-slate-300 hidden sm:inline">依照營業月份拆分入帳，精準核算真實誤差</span>
        </div>
        <div class="text-sm font-bold text-slate-100 mt-1.5 flex items-center gap-2">
          <span>玉山銀行存摺流水與營收所屬月份沖銷對照</span>
          <span class="text-[11px] font-normal px-2 py-0.5 bg-white/10 rounded text-slate-300" id="bridge-month-tag">當月對照</span>
        </div>
      </div>
      <div class="flex flex-wrap items-center gap-2 sm:gap-3 text-xs w-full lg:w-auto">
        <div class="bg-white/10 px-3 py-2 rounded-xl border border-white/10 flex-1 sm:flex-none">
          <span class="text-slate-400 block text-[10px] font-medium">🏦 存摺當月實收流水</span>
          <span class="font-bold text-white font-mono text-sm" id="bridge-passbook">$ 0</span>
        </div>
        <div class="bg-white/10 px-3 py-2 rounded-xl border border-white/10 flex-1 sm:flex-none">
          <span class="text-amber-300 block text-[10px] font-medium">➖ 代收上月款 (跨月)</span>
          <span class="font-bold text-amber-200 font-mono text-sm" id="bridge-prev">-$ 0</span>
        </div>
        <div class="bg-white/10 px-3 py-2 rounded-xl border border-white/10 flex-1 sm:flex-none">
          <span class="text-emerald-300 block text-[10px] font-medium">➕ 次月補存本月款</span>
          <span class="font-bold text-emerald-200 font-mono text-sm" id="bridge-next">+$ 0</span>
        </div>
        <div class="bg-indigo-600/80 px-3.5 py-2 rounded-xl border border-indigo-400/40 flex-1 sm:flex-none">
          <span class="text-indigo-200 block text-[10px] font-medium">📊 所屬本月實收沖銷</span>
          <span class="font-bold text-emerald-300 font-mono text-sm" id="bridge-revenue">$ 0</span>
        </div>
      </div>
    </div>
  </div>

  <!-- Third-Party Platforms Section: 各大金流與外送平台智慧勾稽總覽 -->
  <div class="mt-6 bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden">
    <div class="px-6 py-4 bg-gradient-to-r from-slate-50 to-indigo-50/40 border-b border-slate-100 flex flex-col sm:flex-row justify-between items-start sm:items-center gap-2">
      <div>
        <h2 class="font-bold text-slate-800 text-lg flex items-center gap-2">
          <i class="fa-solid fa-arrows-split-up-and-left text-indigo-600"></i>
          <span>第三方交易平台與通路核對總覽</span>
        </h2>
        <p class="text-xs text-slate-500">LINE Pay、Uber Eats、Foodpanda、偷訂線上訂餐及門市現金各平台即時核對</p>
      </div>
      <span class="text-xs font-semibold px-2.5 py-1 bg-indigo-100 text-indigo-700 rounded-lg">各通路自動勾稽</span>
    </div>
    <div class="p-5">
      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4" id="platform-cards-container">
        <!-- Rendered by JS -->
      </div>
    </div>
  </div>

  <!-- Interactive Calendar Section: 門市對帳月曆 -->
  <div class="mt-6 bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden">
    <div class="px-6 py-4 border-b border-slate-100 flex flex-col lg:flex-row justify-between items-start lg:items-center gap-2">
      <div>
        <h2 class="font-bold text-slate-800 text-lg flex items-center gap-2">
          <i class="fa-regular fa-calendar-days text-indigo-600"></i>
          <span id="calendar-title-text">營業與存款核對月曆</span>
        </h2>
        <p class="text-xs text-slate-500">點擊任意日期格，可立即檢視當日「第一層總表申報」與「第二層銀行入帳」雙層審核詳情</p>
      </div>
      <div class="flex flex-wrap items-center gap-3 text-xs font-semibold">
        <span class="flex items-center gap-1.5"><span class="w-3 h-3 rounded-full bg-emerald-500"></span> 🟢 雙審通過 (已入帳)</span>
        <span class="flex items-center gap-1.5"><span class="w-3 h-3 rounded-full bg-amber-400"></span> 🟡 門市已報 (待到帳)</span>
        <span class="flex items-center gap-1.5"><span class="w-3 h-3 rounded-full bg-rose-500"></span> 🔴 門市未存 (留店)</span>
        <span class="flex items-center gap-1.5"><span class="w-3 h-3 rounded-full bg-blue-500"></span> 🔵 門市漏填 (銀行已收)</span>
      </div>
    </div>

    <!-- Selected Day Instant Detail Banner -->
    <div id="calendar-day-detail" class="hidden mx-6 mt-4 p-4 rounded-xl bg-indigo-50/90 border border-indigo-200 text-xs">
      <!-- Populated when clicking a calendar cell -->
    </div>

    <!-- Calendar Grid -->
    <div class="p-4 sm:p-6">
      <div class="grid grid-cols-7 gap-2 mb-2 text-center text-xs font-bold text-slate-500 uppercase tracking-wider">
        <div class="text-rose-500">週日</div>
        <div>週一</div>
        <div>週二</div>
        <div>週三</div>
        <div>週四</div>
        <div>週五</div>
        <div class="text-indigo-500">週六</div>
      </div>
      <div class="grid grid-cols-7 gap-2" id="calendar-grid">
        <!-- Rendered by JS -->
      </div>
    </div>
  </div>

  <!-- Single Transactions Table: 玉山單筆沖銷明細 -->
  <div class="mt-6 bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden">
    <div class="px-6 py-4 border-b border-slate-100 flex flex-col md:flex-row justify-between items-start md:items-center gap-3">
      <div>
        <h2 class="font-bold text-slate-800 text-lg flex items-center gap-2">
          <i class="fa-solid fa-list-check text-indigo-600"></i>
          <span>玉山銀行已沖銷之單筆明細（精準標註為哪幾日款項 / 跨月累計）</span>
        </h2>
        <p class="text-xs text-slate-400">點擊分類標籤或輸入關鍵字可 0 延遲即時篩選每筆銀行入帳對應的門市營業日</p>
      </div>
    </div>

    <!-- Quick Category Filter Pills & Search Input -->
    <div class="px-6 py-3 bg-slate-50 border-b border-slate-100 flex flex-wrap items-center justify-between gap-3">
      <div class="flex flex-wrap items-center gap-1.5" id="category-filter-pills">
        <button onclick="setCategoryFilter('ALL')" class="cat-pill px-3 py-1.5 rounded-lg text-xs font-semibold bg-indigo-600 text-white shadow-sm cursor-pointer" id="cat-pill-ALL">全部沖銷</button>
        <button onclick="setCategoryFilter('CROSS')" class="cat-pill px-3 py-1.5 rounded-lg text-xs font-semibold bg-white text-slate-600 border border-slate-200 hover:bg-slate-100 cursor-pointer" id="cat-pill-CROSS">🔄 跨月拆分/跨月款</button>
        <button onclick="setCategoryFilter('現金')" class="cat-pill px-3 py-1.5 rounded-lg text-xs font-semibold bg-white text-slate-600 border border-slate-200 hover:bg-slate-100 cursor-pointer" id="cat-pill-現金">💵 現金存入</button>
        <button onclick="setCategoryFilter('LINE')" class="cat-pill px-3 py-1.5 rounded-lg text-xs font-semibold bg-white text-slate-600 border border-slate-200 hover:bg-slate-100 cursor-pointer" id="cat-pill-LINE">💚 LINE Pay</button>
        <button onclick="setCategoryFilter('Uber')" class="cat-pill px-3 py-1.5 rounded-lg text-xs font-semibold bg-white text-slate-600 border border-slate-200 hover:bg-slate-100 cursor-pointer" id="cat-pill-Uber">🚗 Uber Eats</button>
        <button onclick="setCategoryFilter('Foodpanda')" class="cat-pill px-3 py-1.5 rounded-lg text-xs font-semibold bg-white text-slate-600 border border-slate-200 hover:bg-slate-100 cursor-pointer" id="cat-pill-Foodpanda">🐼 Foodpanda</button>
        <button onclick="setCategoryFilter('偷訂')" class="cat-pill px-3 py-1.5 rounded-lg text-xs font-semibold bg-white text-slate-600 border border-slate-200 hover:bg-slate-100 cursor-pointer" id="cat-pill-偷訂">🛒 偷訂科技</button>
      </div>
      <div class="w-full sm:w-64 relative">
        <i class="fa-solid fa-magnifying-glass absolute left-3 top-2.5 text-slate-400 text-xs"></i>
        <input type="text" id="search-input" oninput="filterTransactions()" placeholder="搜尋日期、門市、金額、跨月..." 
               class="w-full pl-8 pr-3 py-1.5 bg-white border border-slate-200 rounded-xl text-xs focus:outline-none focus:ring-2 focus:ring-indigo-500 shadow-sm">
      </div>
    </div>
    
    <div class="overflow-x-auto">
      <table class="w-full text-left text-xs text-slate-600">
        <thead class="bg-slate-50 text-slate-500 font-semibold border-b border-slate-100">
          <tr>
            <th class="py-3 px-4">序號</th>
            <th class="py-3 px-4">事業體</th>
            <th class="py-3 px-4">銀行入帳日</th>
            <th class="py-3 px-4">交易類別</th>
            <th class="py-3 px-4 text-right">銀行入帳金額</th>
            <th class="py-3 px-4 text-center">👉 營收歸屬月份與跨月拆分</th>
            <th class="py-3 px-4 text-center">所屬款項日 (哪幾日)</th>
            <th class="py-3 px-4">文中傳票摘要</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-slate-100" id="transaction-tbody">
          <!-- Rendered by JS -->
        </tbody>
      </table>
    </div>
  </div>

  <footer class="mt-10 text-center text-xs text-slate-400 pb-8">
    月下餐飲集團 文中會計傳票產生系統 · 雙層審核與智慧查帳引擎
  </footer>

  <!-- Script: Data & Interactivity -->
  <script>
    let allMonthsData = {json_payload_str};
    let currentMonth = '{target_month}';
    let currentStore = 'ALL';
    let currentCategoryFilter = 'ALL';
    let currentAuditFilter = 'PENDING_ALL';

    function showToast(msg) {{
      const t = document.getElementById('sync-toast');
      const txt = document.getElementById('sync-toast-text');
      if (!t || !txt) return;
      txt.textContent = msg;
      t.classList.remove('hidden');
      t.classList.add('flex');
      setTimeout(() => {{
        t.classList.add('hidden');
        t.classList.remove('flex');
      }}, 2500);
    }}

    async function refreshLiveData(manual = false) {{
      const icon = document.getElementById('refresh-icon');
      if (icon) icon.classList.add('fa-spin');
      try {{
        const res = await fetch('./dashboard_data.json?t=' + Date.now(), {{ cache: 'no-store' }});
        if (res.ok) {{
          const freshData = await res.json();
          if (freshData && Object.keys(freshData).length > 0) {{
            allMonthsData = freshData;
            if (allMonthsData[currentMonth]) {{
              document.getElementById('header-updated-at').textContent = allMonthsData[currentMonth].updated_at || '';
            }}
            renderDashboard();
            if (manual) showToast('已即時同步最新雲端帳務資料！');
          }}
        }}
      }} catch (e) {{
        if (manual) {{
          renderDashboard();
          showToast('已完成本地資料即時重整！');
        }}
      }} finally {{
        if (icon) setTimeout(() => icon.classList.remove('fa-spin'), 300);
      }}
    }}

    function jumpAndFilterAudit(statusFilter) {{
      setAuditFilter(statusFilter);
      const sec = document.getElementById('alert-section');
      if (sec) sec.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
    }}

    function setAuditFilter(filterKey) {{
      currentAuditFilter = filterKey;
      const styles = {{
        'PENDING_ALL': 'bg-amber-400 text-slate-950 shadow-sm',
        'DECLARED_PENDING_BANK': 'bg-amber-400 text-slate-950 shadow-sm',
        'UNPAID': 'bg-rose-500 text-white shadow-sm',
        'MATCHED': 'bg-emerald-500 text-white shadow-sm',
        'ALL_DAYS': 'bg-indigo-500 text-white shadow-sm'
      }};
      const inactiveStyles = {{
        'PENDING_ALL': 'bg-white/10 text-white hover:bg-white/20 border border-white/20',
        'DECLARED_PENDING_BANK': 'bg-white/10 text-amber-200 hover:bg-white/20 border border-amber-400/30',
        'UNPAID': 'bg-white/10 text-rose-200 hover:bg-white/20 border border-rose-400/30',
        'MATCHED': 'bg-white/10 text-emerald-200 hover:bg-white/20 border border-emerald-400/30',
        'ALL_DAYS': 'bg-white/10 text-white hover:bg-white/20 border border-white/20'
      }};
      Object.keys(styles).forEach(k => {{
        const btn = document.getElementById('audit-pill-' + k);
        if (btn) {{
          btn.className = 'audit-pill px-3 py-1.5 rounded-lg text-xs font-bold cursor-pointer transition-all ' + (k === filterKey ? styles[k] : inactiveStyles[k]);
        }}
      }});
      const rawData = allMonthsData[currentMonth];
      if (rawData) renderAlertsAndAuditTable(rawData);
    }}

    function setCategoryFilter(cat) {{
      currentCategoryFilter = cat;
      document.querySelectorAll('.cat-pill').forEach(btn => {{
        btn.className = 'cat-pill px-3 py-1.5 rounded-lg text-xs font-semibold bg-white text-slate-600 border border-slate-200 hover:bg-slate-100 cursor-pointer';
      }});
      const activeBtn = document.getElementById('cat-pill-' + cat);
      if (activeBtn) {{
        activeBtn.className = 'cat-pill px-3 py-1.5 rounded-lg text-xs font-semibold bg-indigo-600 text-white shadow-sm cursor-pointer';
      }}
      filterTransactions();
    }}

    function switchMonth(selectedMonth) {{
      if (allMonthsData[selectedMonth]) {{
        currentMonth = selectedMonth;
        document.getElementById('header-updated-at').textContent = allMonthsData[currentMonth].updated_at || '';
        renderDashboard();
      }}
    }}

    function switchStore(storeKey) {{
      currentStore = storeKey;
      document.querySelectorAll('.store-tab').forEach(tab => {{
        tab.className = 'store-tab px-4 py-2.5 rounded-xl font-bold text-sm transition-all bg-white text-slate-600 hover:bg-slate-100 border border-slate-200 cursor-pointer';
      }});
      const activeTab = document.getElementById('tab-' + storeKey);
      if (activeTab) {{
        activeTab.className = 'store-tab px-4 py-2.5 rounded-xl font-bold text-sm transition-all shadow-sm bg-indigo-600 text-white cursor-pointer';
      }}
      renderDashboard();
    }}

    function renderDashboard() {{
      const rawData = allMonthsData[currentMonth];
      if (!rawData) return;

      try {{ renderBridge(rawData); }} catch (e) {{ console.error('renderBridge error:', e); }}
      try {{ renderKPI(rawData); }} catch (e) {{ console.error('renderKPI error:', e); }}
      try {{ renderAlertsAndAuditTable(rawData); }} catch (e) {{ console.error('renderAlertsAndAuditTable error:', e); }}
      try {{ renderPlatforms(rawData); }} catch (e) {{ console.error('renderPlatforms error:', e); }}
      try {{ renderCalendar(rawData); }} catch (e) {{ console.error('renderCalendar error:', e); }}
      try {{ filterTransactions(rawData); }} catch (e) {{ console.error('filterTransactions error:', e); }}
    }}

    function renderBridge(rawData) {{
      const b = rawData.bridge || {{}};
      const [yr, mo] = currentMonth.split('-').map(Number);
      const prevMo = mo === 1 ? 12 : mo - 1;
      const nextMo = mo === 12 ? 1 : mo + 1;

      const tagEl = document.getElementById('bridge-month-tag');
      if (tagEl) tagEl.textContent = `${{mo}}月 權責調節核對`;

      const passbookVal = b.bank_passbook_total || 0;
      const prevVal = b.collected_for_prev_month || 0;
      const nextVal = b.collected_in_next_month || 0;
      const revVal = b.accrual_cleared_for_this_month || (passbookVal - prevVal + nextVal);

      const elPassbook = document.getElementById('bridge-passbook');
      if (elPassbook) elPassbook.textContent = '$ ' + Math.round(passbookVal).toLocaleString();

      const elPrev = document.getElementById('bridge-prev');
      if (elPrev) elPrev.textContent = '-$ ' + Math.round(prevVal).toLocaleString() + (prevVal > 0 ? ` (${{prevMo}}月款)` : '');

      const elNext = document.getElementById('bridge-next');
      if (elNext) elNext.textContent = '+$ ' + Math.round(nextVal).toLocaleString() + (nextVal > 0 ? ` (${{nextMo}}月存)` : '');

      const elRev = document.getElementById('bridge-revenue');
      if (elRev) elRev.textContent = '$ ' + Math.round(revVal).toLocaleString();
    }}

    function renderKPI(rawData) {{
      let pos = 0, bank = 0, inv = 0, pendingCash = 0, unpaidCash = 0, declaredPending = 0;
      if (currentStore === 'ALL') {{
        pos = rawData.kpi.total_pos || 0;
        bank = rawData.kpi.total_bank || 0;
        inv = rawData.kpi.total_inv || 0;
        pendingCash = rawData.kpi.total_pending_cash || 0;
        unpaidCash = rawData.kpi.total_unpaid_cash || 0;
        declaredPending = rawData.kpi.total_declared_pending || 0;
      }} else {{
        const s = (rawData.stores || {{}})[currentStore];
        if (s) {{
          Object.entries(s.channels || {{}}).forEach(([code, c]) => {{
            pos += c.pos_sales || 0;
            bank += c.bank_cleared || 0;
            inv += c.invoice_cleared || 0;
            if (code === '1130-01' && c.diff > 0) {{
              pendingCash += c.diff;
            }}
          }});
          (s.daily_cash || []).forEach(d => {{
            if (d.audit_status === 'UNPAID') unpaidCash += (d.cash || 0);
            else if (d.audit_status === 'DECLARED_PENDING_BANK') declaredPending += (d.cash || 0);
          }});
        }}
      }}
      document.getElementById('kpi-pos').textContent = '$ ' + Math.round(pos).toLocaleString();
      document.getElementById('kpi-bank').textContent = '$ ' + Math.round(bank).toLocaleString();
      document.getElementById('kpi-inv').textContent = '$ ' + Math.round(inv).toLocaleString();
      document.getElementById('kpi-pending-cash').textContent = '$ ' + Math.round(pendingCash).toLocaleString();

      const elUnpaid = document.getElementById('kpi-unpaid-sub');
      if (elUnpaid) elUnpaid.textContent = '🔴 留店未存: $' + Math.round(unpaidCash).toLocaleString();
      const elDecl = document.getElementById('kpi-declared-sub');
      if (elDecl) elDecl.textContent = '🟡 門市已報: $' + Math.round(declaredPending).toLocaleString();
    }}

    function renderAlertsAndAuditTable(rawData) {{
      const cardsContainer = document.getElementById('alert-cards-container');
      const cardsWrapper = document.getElementById('audit-cards-wrapper');
      const tbody = document.getElementById('dual-audit-tbody');
      const subtitle = document.getElementById('audit-table-subtitle');

      // 1. 收集所有門市的逐日雙層審核紀錄
      const storesToInclude = currentStore === 'ALL' ? Object.keys(rawData.stores || {{}}) : [currentStore];
      let allDailyRows = [];
      storesToInclude.forEach(sk => {{
        const s = (rawData.stores || {{}})[sk];
        if (!s) return;
        (s.daily_cash || []).forEach(d => {{
          if ((d.cash || 0) > 0) {{
            allDailyRows.push({{
              storeKey: sk,
              storeName: s.name || sk,
              ...d
            }});
          }}
        }});
      }});

      // 依日期新到舊排序（或異常優先）
      allDailyRows.sort((a, b) => a.date.localeCompare(b.date));

      const countDeclared = allDailyRows.filter(r => r.audit_status === 'DECLARED_PENDING_BANK').length;
      const countUnpaid = allDailyRows.filter(r => r.audit_status === 'UNPAID').length;
      const countMatched = allDailyRows.filter(r => r.audit_status === 'MATCHED' || r.audit_status === 'BANK_ONLY').length;
      const countPendingAll = countDeclared + countUnpaid;

      const setCnt = (id, val) => {{ const el = document.getElementById(id); if (el) el.textContent = val; }};
      setCnt('count-PENDING_ALL', countPendingAll);
      setCnt('count-DECLARED_PENDING_BANK', countDeclared);
      setCnt('count-UNPAID', countUnpaid);
      setCnt('count-MATCHED', countMatched);
      setCnt('count-ALL_DAYS', allDailyRows.length);

      // 2. 渲染上方異常與在途追蹤卡片
      const filteredAlerts = (rawData.alerts || []).filter(a => {{
        if (currentStore !== 'ALL' && !a.store.includes(currentStore)) return false;
        if (currentAuditFilter === 'DECLARED_PENDING_BANK') return a.audit_status === 'DECLARED_PENDING_BANK';
        if (currentAuditFilter === 'UNPAID') return a.audit_status === 'UNPAID';
        return true;
      }});

      document.getElementById('alert-title-text').textContent = 
        `門市現金雙層審核與異常追蹤（待入帳/未存共 ${{countPendingAll}} 筆）`;

      if (currentAuditFilter === 'MATCHED' || currentAuditFilter === 'ALL_DAYS') {{
        cardsWrapper.classList.add('hidden');
      }} else {{
        cardsWrapper.classList.remove('hidden');
        if (filteredAlerts.length === 0) {{
          cardsContainer.innerHTML = `
            <div class="col-span-full py-5 text-center text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-xl font-bold text-sm">
              <i class="fa-solid fa-circle-check text-lg mr-1.5"></i> 此篩選條件下無待辦異常項目！
            </div>
          `;
        }} else {{
          const cardsHtml = filteredAlerts.map(a => {{
            const isDeclared = (a.audit_status === 'DECLARED_PENDING_BANK');
            const borderClass = isDeclared
              ? 'border-amber-300 bg-amber-50/70'
              : 'border-rose-300 bg-rose-50/60';
            const badgeClass = isDeclared
              ? 'bg-amber-200/80 text-amber-950 border border-amber-400'
              : 'bg-rose-600 text-white';
            const badgeText = isDeclared ? '🟡 門市已申報 (待銀行入帳)' : '🔴 門市未存 (留店未存)';
            return `
              <div class="p-4 rounded-xl border-2 ${{borderClass}} flex flex-col justify-between transition-all hover:shadow-md">
                <div>
                  <div class="flex justify-between items-center gap-2">
                    <span class="font-bold text-slate-900 text-sm">${{a.store}}</span>
                    <span class="text-[11px] font-bold px-2 py-0.5 rounded-md ${{badgeClass}}">${{badgeText}}</span>
                  </div>
                  <div class="flex justify-between items-baseline mt-2">
                    <div class="text-xs text-slate-600">營業日：<span class="font-mono font-bold text-slate-900 text-sm">${{a.date}}</span></div>
                    <div class="text-lg font-extrabold font-mono ${{isDeclared ? 'text-amber-800' : 'text-rose-600'}}">$ ${{Math.round(a.cash).toLocaleString()}}</div>
                  </div>
                  <div class="mt-2 px-2.5 py-1.5 rounded-lg text-xs ${{isDeclared ? 'bg-amber-100/90 text-amber-950 font-semibold border border-amber-300/80' : 'bg-rose-100/80 text-rose-900 font-semibold border border-rose-200'}}">
                    <div><span class="font-bold">第一層(總表U欄)：</span>${{a.declared_info || (isDeclared ? '已填報存款金額' : '❌ 總表U欄空白未填')}}</div>
                    <div class="mt-0.5"><span class="font-bold">第二層(玉山存摺)：</span>⏳ 銀行存摺尚未入帳</div>
                  </div>
                </div>
                <div class="mt-2.5 pt-2 border-t border-slate-200/70 text-[11px] text-slate-600 flex items-center justify-between">
                  <span class="truncate font-medium">${{a.risk}}</span>
                  <button onclick="scrollToDate('${{a.date}}')" class="text-indigo-600 font-bold hover:underline shrink-0 ml-2 cursor-pointer">定位月曆 ➔</button>
                </div>
              </div>
            `;
          }});
          cardsContainer.innerHTML = cardsHtml.join('');
        }}
      }}

      // 3. 渲染逐日雙層審核完整對照表
      const tableRows = allDailyRows.filter(r => {{
        if (currentAuditFilter === 'PENDING_ALL') return r.audit_status === 'DECLARED_PENDING_BANK' || r.audit_status === 'UNPAID';
        if (currentAuditFilter === 'DECLARED_PENDING_BANK') return r.audit_status === 'DECLARED_PENDING_BANK';
        if (currentAuditFilter === 'UNPAID') return r.audit_status === 'UNPAID';
        if (currentAuditFilter === 'MATCHED') return r.audit_status === 'MATCHED' || r.audit_status === 'BANK_ONLY';
        return true; // ALL_DAYS
      }});

      if (subtitle) {{
        subtitle.textContent = `目前顯示 ${{tableRows.length}} 筆營業日雙層審核紀錄（點擊上方按鈕可切換「全部營業日」或「指定狀態」）`;
      }}

      if (tableRows.length === 0) {{
        tbody.innerHTML = `<tr><td colspan="8" class="py-6 text-center text-slate-400 font-medium">目前篩選條件下無紀錄，請點擊上方「📋 全月逐日總表」查看所有營業日</td></tr>`;
      }} else {{
        const trsHtml = tableRows.map(r => {{
          const st = r.audit_status;
          const rowBg = st === 'UNPAID' ? 'bg-rose-50/40 hover:bg-rose-50' :
                        st === 'DECLARED_PENDING_BANK' ? 'bg-amber-50/40 hover:bg-amber-50' :
                        'hover:bg-slate-50';

          const tier1Badge = r.has_declared
            ? '<span class="px-2 py-0.5 rounded bg-emerald-100 text-emerald-800 font-bold text-[11px] border border-emerald-300">✅ 已申報</span>'
            : '<span class="px-2 py-0.5 rounded bg-rose-100 text-rose-800 font-bold text-[11px] border border-rose-300">❌ 未填寫</span>';

          const tier1Detail = r.has_declared
            ? `<span class="font-semibold text-slate-800">${{r.declared_note || ('存入 $' + Math.round(r.declared_amt || 0).toLocaleString())}}</span>`
            : `<span class="text-rose-500 font-medium">總表 U 欄存款金額空白</span>`;

          const tier2Badge = r.bank_cleared
            ? '<span class="px-2 py-0.5 rounded bg-emerald-100 text-emerald-800 font-bold text-[11px] border border-emerald-300">🏦 已入帳</span>'
            : '<span class="px-2 py-0.5 rounded bg-amber-100 text-amber-800 font-bold text-[11px] border border-amber-300">⏳ 待入帳</span>';

          const tier2Detail = r.bank_cleared
            ? `<span class="font-mono font-semibold text-emerald-700">玉山 ${{r.bank_date || r.deposit_date || ''}} 入帳 ($${{Math.round(r.bank_amt || r.cash).toLocaleString()}})</span>`
            : `<span class="text-slate-400">存摺尚無對應沖銷流水</span>`;

          let finalBadge = '';
          if (st === 'MATCHED') {{
            finalBadge = '<span class="px-2.5 py-1 rounded-md bg-emerald-100 text-emerald-900 font-bold text-xs border border-emerald-300">🟢 雙審通過 (已結清)</span>';
          }} else if (st === 'DECLARED_PENDING_BANK') {{
            finalBadge = '<span class="px-2.5 py-1 rounded-md bg-amber-100 text-amber-900 font-bold text-xs border border-amber-400">🟡 門市已申報 (待入帳)</span>';
          }} else if (st === 'BANK_ONLY') {{
            finalBadge = '<span class="px-2.5 py-1 rounded-md bg-blue-100 text-blue-900 font-bold text-xs border border-blue-300">🔵 門市漏填 (銀行已收)</span>';
          }} else {{
            finalBadge = '<span class="px-2.5 py-1 rounded-md bg-rose-100 text-rose-900 font-bold text-xs border border-rose-300">🔴 門市未存 (留店未存)</span>';
          }}

          return `
            <tr class="${{rowBg}} transition-colors">
              <td class="py-2.5 px-4 font-bold text-slate-800">${{r.storeName}}</td>
              <td class="py-2.5 px-4 font-mono font-semibold text-slate-700">${{r.date}}</td>
              <td class="py-2.5 px-4 text-right font-mono font-bold text-slate-900">$ ${{Math.round(r.cash).toLocaleString()}}</td>
              <td class="py-2.5 px-4 text-center">${{tier1Badge}}</td>
              <td class="py-2.5 px-4">${{tier1Detail}}</td>
              <td class="py-2.5 px-4 text-center">${{tier2Badge}}</td>
              <td class="py-2.5 px-4">${{tier2Detail}}</td>
              <td class="py-2.5 px-4 text-center">${{finalBadge}}</td>
            </tr>
          `;
        }});
        tbody.innerHTML = trsHtml.join('');
      }}
    }}

    function renderPlatforms(rawData) {{
      const container = document.getElementById('platform-cards-container');
      if (!container) return;

      const channelSummary = {{
        '1130-01': {{ name: '門市營業現金', icon: 'fa-solid fa-money-bill-wave text-amber-500', bg: 'border-amber-200 bg-amber-50/30', pos: 0, bank: 0, inv: 0, diff: 0 }},
        '1130-02': {{ name: 'LINE Pay (連加網路)', icon: 'fa-brands fa-line text-emerald-500', bg: 'border-emerald-200 bg-emerald-50/30', pos: 0, bank: 0, inv: 0, diff: 0 }},
        '1130-03': {{ name: 'Uber Eats (優步外送)', icon: 'fa-solid fa-car-side text-slate-700', bg: 'border-slate-200 bg-slate-50/40', pos: 0, bank: 0, inv: 0, diff: 0 }},
        '1130-04': {{ name: 'Foodpanda (富胖達)', icon: 'fa-solid fa-box text-pink-500', bg: 'border-pink-200 bg-pink-50/30', pos: 0, bank: 0, inv: 0, diff: 0 }},
        '1130-05': {{ name: '偷訂餐飲科技', icon: 'fa-solid fa-store text-indigo-500', bg: 'border-indigo-200 bg-indigo-50/30', pos: 0, bank: 0, inv: 0, diff: 0 }}
      }};

      const storesToInclude = currentStore === 'ALL' ? Object.keys(rawData.stores || {{}}) : [currentStore];
      storesToInclude.forEach(sk => {{
        const s = (rawData.stores || {{}})[sk];
        if (s && s.channels) {{
          Object.entries(s.channels).forEach(([code, c]) => {{
            if (channelSummary[code]) {{
              channelSummary[code].pos += (c.pos_sales || 0);
              channelSummary[code].bank += (c.bank_cleared || 0);
              channelSummary[code].inv += (c.invoice_cleared || 0);
              channelSummary[code].diff += (c.diff || 0);
            }}
          }});
        }}
      }});

      const cardsHtml = [];
      Object.entries(channelSummary).forEach(([code, p]) => {{
        if (p.pos === 0 && p.bank === 0 && p.diff === 0) return;

        let statusText = '';
        let statusClass = '';
        if (code === '1130-01') {{
          if (p.diff <= 0) {{
            statusText = '✅ 現金全數如期存入';
            statusClass = 'text-emerald-700 bg-emerald-100';
          }} else {{
            statusText = `🚨 尚有 $${{Math.round(p.diff).toLocaleString()}} 待存`;
            statusClass = 'text-rose-700 bg-rose-100 font-bold';
          }}
        }} else if (code === '1130-02') {{
          if (p.diff <= 0) {{
            statusText = '✅ 連加已全數撥款沖銷';
            statusClass = 'text-emerald-700 bg-emerald-100';
          }} else {{
            statusText = `⏳ 每日撥款中 (待撥 $${{Math.round(p.diff).toLocaleString()}})`;
            statusClass = 'text-amber-800 bg-amber-100';
          }}
        }} else if (code === '1130-03') {{
          if (p.diff <= 0) {{
            statusText = 'ℹ️ 撥款含上期外送款';
            statusClass = 'text-blue-700 bg-blue-100';
          }} else {{
            statusText = `⏳ 週結款在途 (含手續費待沖)`;
            statusClass = 'text-slate-700 bg-slate-100';
          }}
        }} else if (code === '1130-04') {{
          if (p.diff <= 0) {{
            statusText = '✅ 平台已沖清';
            statusClass = 'text-emerald-700 bg-emerald-100';
          }} else {{
            statusText = `⏳ 半月結算中 (待撥 $${{Math.round(p.diff).toLocaleString()}})`;
            statusClass = 'text-pink-700 bg-pink-100';
          }}
        }} else {{
          statusText = p.diff <= 0 ? '✅ 平台已沖清' : `⏳ 結算撥款中 ($${{Math.round(p.diff).toLocaleString()}})`;
          statusClass = 'text-indigo-700 bg-indigo-100';
        }}

        cardsHtml.push(`
          <div class="p-4 rounded-xl border ${{p.bg}} flex flex-col justify-between hover:shadow-md transition-all">
            <div>
              <div class="flex justify-between items-center mb-2">
                <span class="font-bold text-slate-800 text-sm flex items-center gap-1.5">
                  <i class="${{p.icon}}"></i> ${{p.name}}
                </span>
                <span class="text-[10px] font-semibold px-2 py-0.5 rounded-md ${{statusClass}}">
                  ${{statusText}}
                </span>
              </div>
              <div class="grid grid-cols-2 gap-2 mt-3 pt-2 border-t border-slate-200/60 text-xs">
                <div>
                  <span class="text-slate-400 block text-[11px]">POS 應收累計</span>
                  <span class="font-bold text-slate-800 font-mono text-sm">$ ${{Math.round(p.pos).toLocaleString()}}</span>
                </div>
                <div>
                  <span class="text-slate-400 block text-[11px]">玉山已入帳沖銷</span>
                  <span class="font-bold text-emerald-600 font-mono text-sm">$ ${{Math.round(p.bank).toLocaleString()}}</span>
                </div>
              </div>
            </div>
            <div class="mt-3 pt-2 border-t border-slate-200/60 flex justify-between items-center text-xs">
              <span class="text-slate-500">在途款項 / 差額：</span>
              <span class="font-bold font-mono ${{p.diff > 0 ? (code === '1130-01' ? 'text-rose-600' : 'text-amber-600') : 'text-slate-600'}}">
                $ ${{Math.round(p.diff).toLocaleString()}}
              </span>
            </div>
          </div>
        `);
      }});
      container.innerHTML = cardsHtml.join('');
    }}

    let currentDailyCashMap = {{}};

    function showCalendarDayDetail(dateStr) {{
      const detailBox = document.getElementById('calendar-day-detail');
      if (!detailBox) return;
      const records = currentDailyCashMap[dateStr] || [];
      if (records.length === 0) {{
        detailBox.classList.remove('hidden');
        detailBox.innerHTML = `<div class="flex justify-between items-center"><span>📅 <b>${{dateStr}}</b>：當日無門市現金營收資料</span><button onclick="document.getElementById('calendar-day-detail').classList.add('hidden')" class="text-slate-400 hover:text-slate-600 font-bold">✕ 關閉</button></div>`;
        return;
      }}
      const itemsHtml = records.map(r => {{
        const t1 = r.has_declared ? `✅ 已申報 (${{r.declared_note || '$'+Math.round(r.declared_amt||0).toLocaleString()}})` : '❌ 總表U欄未填';
        const t2 = r.bank_cleared ? `🏦 玉山已入帳 (${{r.bank_date || r.deposit_date || ''}})` : '⏳ 銀行待入帳';
        return `<div class="mt-1.5 pt-1.5 border-t border-indigo-200/60 flex flex-wrap justify-between items-center gap-2">
          <span class="font-bold text-slate-900">${{r.store}}｜當日現金：<span class="font-mono text-indigo-700">$${{Math.round(r.cash).toLocaleString()}}</span></span>
          <span><b>第一層(總表U欄)：</b>${{t1}}</span>
          <span><b>第二層(銀行存摺)：</b>${{t2}}</span>
          <span class="font-bold">${{r.audit_label || ''}}</span>
        </div>`;
      }}).join('');
      detailBox.classList.remove('hidden');
      detailBox.innerHTML = `
        <div class="flex justify-between items-center font-bold text-indigo-950">
          <span>🔍 【${{dateStr}}】雙層審核穿透詳情</span>
          <button onclick="document.getElementById('calendar-day-detail').classList.add('hidden')" class="text-slate-500 hover:text-slate-800 px-2 py-0.5 rounded bg-white border border-slate-200 cursor-pointer">✕ 關閉</button>
        </div>
        ${{itemsHtml}}
      `;
    }}

    function renderCalendar(rawData) {{
      const grid = document.getElementById('calendar-grid');
      if (!grid) return;
      
      const [yr, mo] = currentMonth.split('-').map(Number);
      document.getElementById('calendar-title-text').textContent = 
        `營業與存款核對月曆 (${{yr}} 年 ${{mo}} 月)`;

      const daysInMonth = new Date(yr, mo, 0).getDate();
      const firstDayOfWeek = new Date(yr, mo - 1, 1).getDay();

      let cellsHtml = [];
      for (let i = 0; i < firstDayOfWeek; i++) {{
        cellsHtml.push('<div class="calendar-cell bg-slate-50/50 rounded-xl border border-dashed border-slate-200"></div>');
      }}

      let dailyCashMap = {{}};
      const storesToInclude = currentStore === 'ALL' ? Object.keys(rawData.stores || {{}}) : [currentStore];
      
      storesToInclude.forEach(sk => {{
        const s = (rawData.stores || {{}})[sk];
        if (s && s.daily_cash) {{
          s.daily_cash.forEach(d => {{
            if (!dailyCashMap[d.date]) {{
              dailyCashMap[d.date] = [];
            }}
            dailyCashMap[d.date].push({{ store: s.name, ...d }});
          }});
        }}
      }});
      currentDailyCashMap = dailyCashMap;

      for (let day = 1; day <= daysInMonth; day++) {{
        const dayStr = day < 10 ? '0' + day : '' + day;
        const moStr = mo < 10 ? '0' + mo : '' + mo;
        const dateStr = `${{yr}}-${{moStr}}-${{dayStr}}`;
        const dayRecords = dailyCashMap[dateStr] || [];

        let innerBadges = '';
        if (dayRecords.length > 0) {{
          dayRecords.forEach(r => {{
            if (r.cash > 0) {{
              const storeTag = currentStore === 'ALL' ? (r.store.slice(0,2)+': ') : '';
              const amtStr = '$' + Math.round(r.cash).toLocaleString();
              if (r.audit_status === 'MATCHED') {{
                const bDate = r.bank_date ? r.bank_date.slice(5) : (r.deposit_date ? r.deposit_date.slice(5) : '已入');
                innerBadges += `<div class="badge-deposit px-1.5 py-0.5 rounded text-[10px] font-medium mt-1 truncate" title="雙審通過：${{r.declared_note || ''}} / 玉山 ${{r.bank_date || r.deposit_date}} 入帳">
                  🟢 ${{storeTag}}${{amtStr}} (玉山${{bDate}})
                </div>`;
              }} else if (r.audit_status === 'DECLARED_PENDING_BANK') {{
                const dDate = r.declared_time ? r.declared_time.slice(5) : '已報';
                innerBadges += `<div class="px-1.5 py-0.5 rounded text-[10px] font-bold mt-1 truncate bg-amber-100 text-amber-900 border border-amber-300" title="${{r.declared_note || '門市已申報提存待到帳'}}">
                  🟡 ${{storeTag}}${{amtStr}} (已報${{dDate}})
                </div>`;
              }} else if (r.audit_status === 'BANK_ONLY') {{
                innerBadges += `<div class="px-1.5 py-0.5 rounded text-[10px] font-bold mt-1 truncate bg-blue-100 text-blue-900 border border-blue-300" title="玉山已入帳但總表U欄漏填">
                  🔵 ${{storeTag}}${{amtStr}} (漏填)
                </div>`;
              }} else {{
                innerBadges += `<div class="badge-overdue px-1.5 py-0.5 rounded text-[10px] font-bold mt-1 truncate" title="門市總表未填申報，銀行亦無入帳">
                  🔴 ${{storeTag}}${{amtStr}} (未存)
                </div>`;
              }}
            }}
          }});
        }}

        cellsHtml.push(`
          <div id="cal-date-${{dateStr}}" onclick="showCalendarDayDetail('${{dateStr}}')" class="calendar-cell bg-white p-2.5 rounded-xl border border-slate-200 flex flex-col justify-between text-xs cursor-pointer hover:border-indigo-400">
            <div>
              <div class="flex justify-between items-center">
                <span class="font-bold text-slate-700 text-sm">${{day}}</span>
                <span class="text-[10px] text-slate-400">${{mo}}/${{dayStr}}</span>
              </div>
              <div class="mt-0.5 space-y-1">
                ${{innerBadges || '<div class="text-[10px] text-slate-300 mt-2">無現金異動</div>'}}
              </div>
            </div>
          </div>
        `);
      }}
      grid.innerHTML = cellsHtml.join('');
    }}

    function filterTransactions(rawDataArg) {{
      const rawData = rawDataArg || allMonthsData[currentMonth];
      if (!rawData) return;

      const tbody = document.getElementById('transaction-tbody');
      if (!tbody) return;
      const query = ((document.getElementById('search-input') || {{}}).value || '').toLowerCase();

      const filtered = (rawData.cleared_items || []).filter(item => {{
        if (currentStore !== 'ALL' && !item.store.includes(currentStore)) {{
          return false;
        }}
        if (currentCategoryFilter === 'CROSS') {{
          const isCross = item.is_cross_month || (item.revenue_alloc && (item.revenue_alloc.includes('跨月') || item.revenue_alloc.includes('上月') || item.revenue_alloc.includes('拆分')));
          if (!isCross) return false;
        }} else if (currentCategoryFilter !== 'ALL') {{
          if (!item.category.includes(currentCategoryFilter)) return false;
        }}
        if (query) {{
          const str = (item.store + ' ' + item.bank_date + ' ' + item.category + ' ' + item.period + ' ' + (item.revenue_alloc || '') + ' ' + item.remark + ' ' + item.bank_amt).toLowerCase();
          return str.includes(query);
        }}
        return true;
      }});

      if (filtered.length === 0) {{
        tbody.innerHTML = `<tr><td colspan="8" class="py-6 text-center text-slate-400">查無相符之單筆沖銷明細</td></tr>`;
        return;
      }}

      const rowsHtml = filtered.map(item => {{
        const isCash = item.category.includes('現金');
        const isLine = item.category.includes('LINE');
        const isUber = item.category.includes('Uber');
        const isPanda = item.category.includes('Foodpanda');
        const isTouding = item.category.includes('偷訂');

        const trClass = isCash ? 'hover:bg-amber-50/50 transition-colors border-l-4 border-amber-400' :
          isLine ? 'hover:bg-emerald-50/50 transition-colors border-l-4 border-[#06C755]' :
          isUber ? 'hover:bg-slate-50 transition-colors border-l-4 border-slate-700' :
          isPanda ? 'hover:bg-pink-50/40 transition-colors border-l-4 border-pink-400' :
          isTouding ? 'hover:bg-purple-50/40 transition-colors border-l-4 border-purple-400' :
          'hover:bg-slate-50 transition-colors border-l-4 border-transparent';

        const catBadge = isCash ? '<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md font-bold text-xs bg-amber-100 text-amber-900 border border-amber-300"><i class="fa-solid fa-money-bill-wave text-amber-600"></i> 現金存入</span>' :
          isLine ? '<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md font-bold text-xs bg-[#06C755] text-white"><i class="fa-brands fa-line text-white text-sm"></i> LINE Pay</span>' :
          isUber ? '<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md font-bold text-xs bg-slate-900 text-white"><i class="fa-solid fa-car-side text-slate-300"></i> Uber Eats</span>' :
          isPanda ? '<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md font-bold text-xs bg-pink-100 text-pink-800 border border-pink-300"><i class="fa-solid fa-box text-pink-600"></i> Foodpanda</span>' :
          isTouding ? '<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md font-bold text-xs bg-purple-100 text-purple-800 border border-purple-300"><i class="fa-solid fa-store text-purple-600"></i> 偷訂科技</span>' :
          `<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md font-bold text-xs bg-indigo-100 text-indigo-800 border border-indigo-200">${{item.category}}</span>`;

        const isMultiDay = item.period.includes('~') || item.period.includes('累計');
        const periodBadge = isMultiDay
          ? `<span class="px-2.5 py-1 rounded-md font-bold text-blue-900 bg-blue-50 border border-blue-200 text-xs">${{item.period}}</span>`
          : `<span class="px-2.5 py-1 rounded-md font-medium text-slate-700 bg-slate-100 border border-slate-200 text-xs">${{item.period}}</span>`;

        let allocBadge = '';
        const alloc = item.revenue_alloc || '';
        if (alloc.includes('跨月拆分') || alloc.includes('部分歸')) {{
          allocBadge = `<span class="px-2 py-0.5 rounded-md font-bold text-indigo-900 bg-indigo-100 border border-indigo-200 text-xs" title="${{alloc}}">✂️ ${{alloc}}</span>`;
        }} else if (alloc.includes('上月') || alloc.includes('跨月歸屬')) {{
          allocBadge = `<span class="px-2 py-0.5 rounded-md font-bold text-amber-900 bg-amber-100 border border-amber-300 text-xs" title="${{alloc}}">⏪ ${{alloc}}</span>`;
        }} else if (alloc.includes('次月')) {{
          allocBadge = `<span class="px-2 py-0.5 rounded-md font-bold text-blue-900 bg-blue-100 border border-blue-300 text-xs" title="${{alloc}}">⏩ ${{alloc}}</span>`;
        }} else if (alloc) {{
          allocBadge = `<span class="px-2 py-0.5 rounded-md font-medium text-emerald-800 bg-emerald-50 border border-emerald-200 text-xs">${{alloc}}</span>`;
        }} else {{
          allocBadge = `<span class="text-slate-300 text-xs">-</span>`;
        }}

        return `
          <tr class="${{trClass}}">
            <td class="py-3 px-4 font-mono text-slate-400 text-center">${{item.idx}}</td>
            <td class="py-3 px-4 font-semibold text-slate-800">${{item.store}}</td>
            <td class="py-3 px-4 font-mono text-slate-600">${{item.bank_date}}</td>
            <td class="py-3 px-4">${{catBadge}}</td>
            <td class="py-3 px-4 text-right font-mono font-bold text-slate-900">$ ${{Math.round(item.bank_amt).toLocaleString()}}</td>
            <td class="py-3 px-4 text-center">${{allocBadge}}</td>
            <td class="py-3 px-4 text-center">${{periodBadge}}</td>
            <td class="py-3 px-4 text-slate-500 text-xs truncate max-w-xs" title="${{item.remark}}">${{item.remark}}</td>
          </tr>
        `;
      }});
      tbody.innerHTML = rowsHtml.join('');
    }}

    function scrollToDate(dateStr) {{
      showCalendarDayDetail(dateStr);
      const el = document.getElementById('cal-date-' + dateStr);
      if (el) {{
        el.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
        el.classList.add('ring-4', 'ring-indigo-500');
        setTimeout(() => el.classList.remove('ring-4', 'ring-indigo-500'), 2500);
      }}
    }}

    // 1. 立即同步渲染內嵌資料 (0ms 秒開)
    renderDashboard();
    // 2. 背景無快取拉取最新 JSON，確保瀏覽器不讀到舊快取
    refreshLiveData(false);
  </script>
</body>
</html>
"""

    out_file = os.path.join(OUTPUT_DIR, f"對帳看板_{m_clean}.html")
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(html_content)

    latest_file = os.path.join(OUTPUT_DIR, "最新對帳看板.html")
    with open(latest_file, "w", encoding="utf-8") as f:
        f.write(html_content)

    # 同步產出 GitHub Pages 入口網頁 (index.html 及 docs/index.html)
    root_index = os.path.join(BASE_DIR, "index.html")
    with open(root_index, "w", encoding="utf-8") as f:
        f.write(html_content)

    docs_dir = os.path.join(BASE_DIR, "docs")
    os.makedirs(docs_dir, exist_ok=True)
    docs_index = os.path.join(docs_dir, "index.html")
    with open(docs_index, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"🌐 可視化查帳互動看板已產出：{out_file}")
    print(f"👉 點擊開啟最新看板：{latest_file}")
    print(f"🚀 GitHub Pages 網頁入口已更新：{root_index}")

    return out_file

if __name__ == "__main__":
    month = sys.argv[1] if len(sys.argv) > 1 else "2026-10"
    generate_html_dashboard(target_month=month)
