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
                        cleared_items.append({
                            "idx": r[0],
                            "store": r[1],
                            "bank_date": str(r[2]),
                            "category": str(r[3]),
                            "bank_amt": float(r[4] or 0),
                            "cleared_amt": float(r[5] or 0),
                            "period": str(r[6]),
                            "remark": str(r[7])
                        })
        except Exception as e:
            pass

    total_pos = 0.0
    total_bank = 0.0
    total_inv = 0.0
    total_pending_cash = 0.0

    all_pending_alerts = []
    for skey, sinfo in stores_data.items():
        channels = sinfo.get("channels", {})
        for ccode, cdata in channels.items():
            total_pos += cdata.get("pos_sales", 0.0)
            total_bank += cdata.get("bank_cleared", 0.0)
            total_inv += cdata.get("invoice_cleared", 0.0)
            if ccode == "1130-01":
                diff = cdata.get("diff", 0.0)
                if diff > 0:
                    total_pending_cash += diff
        
        alerts = sinfo.get("pending_alerts", [])
        for a in alerts:
            all_pending_alerts.append(a)

    all_pending_alerts.sort(key=lambda x: x.get("delay_days", 0), reverse=True)

    return {
        "target_month": target_month,
        "updated_at": updated_at,
        "kpi": {
            "total_pos": total_pos,
            "total_bank": total_bank,
            "total_inv": total_inv,
            "total_pending_cash": total_pending_cash,
            "alert_count": len(all_pending_alerts)
        },
        "stores": stores_data,
        "alerts": all_pending_alerts,
        "cleared_items": cleared_items
    }

def sync_to_cloud_database(multi_months_data):
    """將多月份對帳完整資料同步至月宮 Google 試算表【雲端對帳資料庫】分頁"""
    try:
        from bank_reconciliation import get_drive_headers
        import gspread
        headers, creds = get_drive_headers()
        gc = gspread.authorize(creds)
        ss = gc.open_by_key("19QdWcBQWVNi-SWvzuWhA9lfZFInbtrbmpNIqINa1DYA")
        db_sheet_name = "【雲端對帳資料庫】"
        try:
            ws = ss.worksheet(db_sheet_name)
        except Exception:
            ws = ss.add_worksheet(title=db_sheet_name, rows=50, cols=10)

        rows = [["月份", "更新時間", "KPI_JSON", "STORES_JSON", "ALERTS_JSON", "CLEARED_ITEMS_JSON"]]
        for m, payload in multi_months_data.items():
            rows.append([
                m,
                payload.get("updated_at", ""),
                json.dumps(payload.get("kpi", {}), ensure_ascii=False),
                json.dumps(payload.get("stores", {}), ensure_ascii=False),
                json.dumps(payload.get("alerts", []), ensure_ascii=False),
                json.dumps(payload.get("cleared_items", []), ensure_ascii=False)
            ])
        ws.clear()
        ws.update(values=rows, range_name=f"A1:F{len(rows)}")
        print(f"☁️ 已成功同步 {len(multi_months_data)} 個月份資料至 Google 雲端試算表【雲端對帳資料庫】！")
    except Exception as e:
        print(f"ℹ️ Google 雲端試算表同步略過: {e}")

def generate_html_dashboard(target_month="2026-10", sync_cloud=True):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    m_clean = target_month.replace("-", "")

    state_data = {}
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state_data = json.load(f)

    # 取得所有已存在月份，排序新至舊
    all_months = sorted(list(state_data.keys()), reverse=True)
    if target_month not in all_months:
        all_months.insert(0, target_month)

    multi_months_data = {}
    for m in all_months:
        multi_months_data[m] = load_month_payload(m, state_data)

    current_data = multi_months_data.get(target_month, multi_months_data[all_months[0]])

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
  <title>月下餐飲集團 · 營運對帳與財務可視化看板</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;600;700&display=swap');
    body {{
      font-family: 'Noto Sans TC', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background-color: #f8fafc;
      color: #1e293b;
    }}
    .calendar-cell {{
      min-height: 110px;
      transition: all 0.2s ease;
    }}
    .calendar-cell:hover {{
      transform: translateY(-2px);
      box-shadow: 0 4px 12px rgba(0,0,0,0.06);
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
<body class="p-4 md:p-8 max-w-7xl mx-auto">

  <!-- Header -->
  <header class="flex flex-col md:flex-row justify-between items-start md:items-center pb-6 border-b border-slate-200 gap-4">
    <div>
      <div class="flex items-center gap-3">
        <span class="p-2.5 bg-indigo-600 text-white rounded-xl shadow-md">
          <i class="fa-solid fa-chart-pie text-xl"></i>
        </span>
        <div>
          <h1 class="text-2xl font-bold tracking-tight text-slate-900">月下餐飲集團 · 營運對帳與財務可視化看板</h1>
          <p class="text-sm text-slate-500 mt-0.5">自動追蹤每日營業額、銀行存款沖銷、跨月與多日勾稽、逾期預警</p>
        </div>
      </div>
    </div>
    <div class="flex items-center gap-3 bg-white px-4 py-2.5 rounded-xl shadow-sm border border-slate-200">
      <div class="flex items-center gap-2">
        <i class="fa-regular fa-calendar text-indigo-600"></i>
        <div>
          <div class="text-[11px] text-slate-400 font-medium">選擇查帳月份</div>
          <select id="month-select" onchange="switchMonth(this.value)" class="font-bold text-slate-800 text-sm bg-transparent cursor-pointer focus:outline-none pr-2">
            {month_options_html}
          </select>
        </div>
      </div>
      <div class="h-8 w-px bg-slate-200"></div>
      <div class="text-xs text-slate-500">
        最後更新：<br><span class="font-medium text-slate-700" id="header-updated-at">{current_data['updated_at']}</span>
      </div>
    </div>
  </header>

  <!-- Store Switcher Tabs -->
  <div class="mt-6 flex flex-wrap gap-2">
    <button onclick="switchStore('ALL')" id="tab-ALL" class="store-tab px-5 py-2.5 rounded-xl font-semibold text-sm transition-all shadow-sm bg-indigo-600 text-white shadow-indigo-200">
      <i class="fa-solid fa-layer-group mr-1.5"></i> 集團合併總覽
    </button>
    <button onclick="switchStore('月宮')" id="tab-月宮" class="store-tab px-5 py-2.5 rounded-xl font-semibold text-sm transition-all bg-white text-slate-600 hover:bg-slate-100 border border-slate-200">
      <i class="fa-solid fa-moon mr-1.5 text-amber-500"></i> 月宮 (月下餐飲)
    </button>
    <button onclick="switchStore('隨意小吃')" id="tab-隨意小吃" class="store-tab px-5 py-2.5 rounded-xl font-semibold text-sm transition-all bg-white text-slate-600 hover:bg-slate-100 border border-slate-200">
      <i class="fa-solid fa-utensils mr-1.5 text-rose-500"></i> 隨意小吃
    </button>
  </div>

  <!-- KPI Cards -->
  <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mt-6">
    <div class="bg-white p-5 rounded-2xl shadow-sm border border-slate-200 relative overflow-hidden">
      <div class="text-xs font-semibold uppercase tracking-wider text-slate-400">當月累計 POS 營收</div>
      <div class="text-2xl font-bold text-slate-800 mt-2" id="kpi-pos">$ 0</div>
      <div class="text-xs text-slate-500 mt-1 flex items-center gap-1">
        <i class="fa-solid fa-cash-register text-indigo-500"></i> 含所有門市全通路業績
      </div>
      <div class="absolute -right-2 -bottom-2 text-slate-100 text-6xl pointer-events-none -z-0">
        <i class="fa-solid fa-receipt"></i>
      </div>
    </div>

    <div class="bg-white p-5 rounded-2xl shadow-sm border border-slate-200 relative overflow-hidden">
      <div class="text-xs font-semibold uppercase tracking-wider text-slate-400">玉山銀行已入帳沖銷</div>
      <div class="text-2xl font-bold text-emerald-600 mt-2" id="kpi-bank">$ 0</div>
      <div class="text-xs text-slate-500 mt-1 flex items-center gap-1">
        <i class="fa-solid fa-building-columns text-emerald-500"></i> 現金存入與金流撥款
      </div>
      <div class="absolute -right-2 -bottom-2 text-slate-100 text-6xl pointer-events-none -z-0">
        <i class="fa-solid fa-wallet"></i>
      </div>
    </div>

    <div class="bg-white p-5 rounded-2xl shadow-sm border border-slate-200 relative overflow-hidden">
      <div class="text-xs font-semibold uppercase tracking-wider text-slate-400">待存入門市現金</div>
      <div class="text-2xl font-bold text-rose-600 mt-2" id="kpi-pending-cash">$ 0</div>
      <div class="text-xs text-rose-500 font-medium mt-1 flex items-center gap-1" id="kpi-alert-badge">
        <i class="fa-solid fa-triangle-exclamation"></i> 尚有未存入營業款
      </div>
      <div class="absolute -right-2 -bottom-2 text-rose-50 text-6xl pointer-events-none -z-0">
        <i class="fa-solid fa-hand-holding-dollar"></i>
      </div>
    </div>

    <div class="bg-white p-5 rounded-2xl shadow-sm border border-slate-200 relative overflow-hidden">
      <div class="text-xs font-semibold uppercase tracking-wider text-slate-400">發票已沖手續費 / 稅額</div>
      <div class="text-2xl font-bold text-blue-600 mt-2" id="kpi-inv">$ 0</div>
      <div class="text-xs text-slate-500 mt-1 flex items-center gap-1">
        <i class="fa-solid fa-file-invoice text-blue-500"></i> 自動扣抵外送與金流費
      </div>
      <div class="absolute -right-2 -bottom-2 text-slate-100 text-6xl pointer-events-none -z-0">
        <i class="fa-solid fa-percent"></i>
      </div>
    </div>
  </div>

  <!-- Third-Party Platforms Section: 各大金流與外送平台智慧勾稽總覽 -->
  <div class="mt-8 bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden">
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
    <div class="p-6">
      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4" id="platform-cards-container">
        <!-- Rendered by JS -->
      </div>
    </div>
  </div>

  <!-- Alert Section: 門市現金逾期與未存入穿透清單 -->
  <div class="mt-8 bg-white rounded-2xl shadow-sm border border-rose-200 overflow-hidden" id="alert-section">
    <div class="px-6 py-4 bg-gradient-to-r from-rose-50 to-orange-50 border-b border-rose-100 flex items-center justify-between">
      <div class="flex items-center gap-2 text-rose-800 font-bold">
        <i class="fa-solid fa-bell text-rose-600 animate-pulse"></i>
        <span id="alert-title-text">門市未存款與逾期異常穿透追蹤</span>
      </div>
      <span class="text-xs font-semibold px-2.5 py-1 bg-rose-100 text-rose-700 rounded-lg">即時風險預警</span>
    </div>
    <div class="p-6">
      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4" id="alert-cards-container">
        <!-- Rendered by JS -->
      </div>
    </div>
  </div>

  <!-- Interactive Calendar Section: 門市對帳月曆 -->
  <div class="mt-8 bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden">
    <div class="px-6 py-4 border-b border-slate-100 flex flex-col sm:flex-row justify-between items-start sm:items-center gap-2">
      <div>
        <h2 class="font-bold text-slate-800 text-lg flex items-center gap-2">
          <i class="fa-regular fa-calendar-days text-indigo-600"></i>
          <span id="calendar-title-text">營業與存款核對月曆</span>
        </h2>
        <p class="text-xs text-slate-400">綠色表示已存入玉山銀行（含跨月/多日累計），紅色表示門市營收尚未存入</p>
      </div>
      <div class="flex items-center gap-3 text-xs">
        <span class="flex items-center gap-1.5"><span class="w-3 h-3 rounded-full bg-emerald-500"></span> 現金已存入</span>
        <span class="flex items-center gap-1.5"><span class="w-3 h-3 rounded-full bg-rose-500"></span> 現金逾期未存</span>
        <span class="flex items-center gap-1.5"><span class="w-3 h-3 rounded-full bg-amber-400"></span> 金流撥款中</span>
      </div>
    </div>

    <!-- Calendar Grid -->
    <div class="p-6">
      <div class="grid grid-cols-7 gap-2 mb-2 text-center text-xs font-semibold text-slate-400 uppercase tracking-wider">
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
  <div class="mt-8 bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden">
    <div class="px-6 py-4 border-b border-slate-100 flex flex-col md:flex-row justify-between items-start md:items-center gap-3">
      <div>
        <h2 class="font-bold text-slate-800 text-lg flex items-center gap-2">
          <i class="fa-solid fa-list-check text-indigo-600"></i>
          <span>玉山銀行已沖銷之單筆明細（精準標註為哪幾日款項 / 跨月累計）</span>
        </h2>
        <p class="text-xs text-slate-400">點擊分類標籤或搜尋可快速核對每筆銀行入帳對應的門市營業日</p>
      </div>
    </div>

    <!-- Quick Category Filter Pills & Search Input -->
    <div class="px-6 py-3 bg-slate-50 border-b border-slate-100 flex flex-wrap items-center justify-between gap-3">
      <div class="flex flex-wrap items-center gap-1.5" id="category-filter-pills">
        <button onclick="setCategoryFilter('ALL')" class="cat-pill px-3 py-1 rounded-lg text-xs font-semibold bg-indigo-600 text-white shadow-sm" id="cat-pill-ALL">全部沖銷</button>
        <button onclick="setCategoryFilter('現金')" class="cat-pill px-3 py-1 rounded-lg text-xs font-semibold bg-white text-slate-600 border border-slate-200 hover:bg-slate-100" id="cat-pill-現金">💵 現金存入</button>
        <button onclick="setCategoryFilter('LINE')" class="cat-pill px-3 py-1 rounded-lg text-xs font-semibold bg-white text-slate-600 border border-slate-200 hover:bg-slate-100" id="cat-pill-LINE">💚 LINE Pay</button>
        <button onclick="setCategoryFilter('Uber')" class="cat-pill px-3 py-1 rounded-lg text-xs font-semibold bg-white text-slate-600 border border-slate-200 hover:bg-slate-100" id="cat-pill-Uber">🚗 Uber Eats</button>
        <button onclick="setCategoryFilter('Foodpanda')" class="cat-pill px-3 py-1 rounded-lg text-xs font-semibold bg-white text-slate-600 border border-slate-200 hover:bg-slate-100" id="cat-pill-Foodpanda">🐼 Foodpanda</button>
        <button onclick="setCategoryFilter('偷訂')" class="cat-pill px-3 py-1 rounded-lg text-xs font-semibold bg-white text-slate-600 border border-slate-200 hover:bg-slate-100" id="cat-pill-偷訂">🛒 偷訂科技</button>
      </div>
      <div class="w-full sm:w-64 relative">
        <i class="fa-solid fa-magnifying-glass absolute left-3 top-2.5 text-slate-400 text-xs"></i>
        <input type="text" id="search-input" oninput="filterTransactions()" placeholder="搜尋日期、門市、金額..." 
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
            <th class="py-3 px-4 text-center">👉 所屬款項日 (哪幾日)</th>
            <th class="py-3 px-4">文中傳票摘要</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-slate-100" id="transaction-tbody">
          <!-- Rendered by JS -->
        </tbody>
      </table>
    </div>
  </div>

  <footer class="mt-12 text-center text-xs text-slate-400 pb-8">
    月下餐飲集團 文中會計傳票產生系統 · 自動勾稽與智慧查帳引擎 · 資料安全本地儲存
  </footer>

  <!-- Script: Data & Interactivity -->
  <script>
    const allMonthsData = {json.dumps(multi_months_data, ensure_ascii=False)};
    let currentMonth = '{target_month}';
    let currentStore = 'ALL';
    let currentCategoryFilter = 'ALL';

    function setCategoryFilter(cat) {{
      currentCategoryFilter = cat;
      document.querySelectorAll('.cat-pill').forEach(btn => {{
        btn.className = 'cat-pill px-3 py-1 rounded-lg text-xs font-semibold bg-white text-slate-600 border border-slate-200 hover:bg-slate-100';
      }});
      const activeBtn = document.getElementById('cat-pill-' + cat);
      if (activeBtn) {{
        activeBtn.className = 'cat-pill px-3 py-1 rounded-lg text-xs font-semibold bg-indigo-600 text-white shadow-sm';
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
        tab.className = 'store-tab px-5 py-2.5 rounded-xl font-semibold text-sm transition-all bg-white text-slate-600 hover:bg-slate-100 border border-slate-200';
      }});
      const activeTab = document.getElementById('tab-' + storeKey);
      if (activeTab) {{
        activeTab.className = 'store-tab px-5 py-2.5 rounded-xl font-semibold text-sm transition-all shadow-sm bg-indigo-600 text-white shadow-indigo-200';
      }}
      renderDashboard();
    }}

    function renderDashboard() {{
      const rawData = allMonthsData[currentMonth];
      if (!rawData) return;

      renderKPI(rawData);
      renderPlatforms(rawData);
      renderAlerts(rawData);
      renderCalendar(rawData);
      filterTransactions(rawData);
    }}

    function renderPlatforms(rawData) {{
      const container = document.getElementById('platform-cards-container');
      if (!container) return;
      container.innerHTML = '';

      const channelSummary = {{
        '1130-01': {{ name: '門市營業現金', icon: 'fa-solid fa-money-bill-wave text-amber-500', bg: 'border-amber-200 bg-amber-50/30', pos: 0, bank: 0, inv: 0, diff: 0 }},
        '1130-02': {{ name: 'LINE Pay (連加網路)', icon: 'fa-brands fa-line text-emerald-500', bg: 'border-emerald-200 bg-emerald-50/30', pos: 0, bank: 0, inv: 0, diff: 0 }},
        '1130-03': {{ name: 'Uber Eats (優步外送)', icon: 'fa-solid fa-car-side text-slate-700', bg: 'border-slate-200 bg-slate-50/40', pos: 0, bank: 0, inv: 0, diff: 0 }},
        '1130-04': {{ name: 'Foodpanda (富胖達)', icon: 'fa-solid fa-box text-pink-500', bg: 'border-pink-200 bg-pink-50/30', pos: 0, bank: 0, inv: 0, diff: 0 }},
        '1130-05': {{ name: '偷訂餐飲科技', icon: 'fa-solid fa-store text-indigo-500', bg: 'border-indigo-200 bg-indigo-50/30', pos: 0, bank: 0, inv: 0, diff: 0 }}
      }};

      const storesToInclude = currentStore === 'ALL' ? Object.keys(rawData.stores || {{}}) : [currentStore];
      storesToInclude.forEach(sk => {{
        const s = rawData.stores[sk];
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

        const card = document.createElement('div');
        card.className = `p-4 rounded-xl border ${{p.bg}} flex flex-col justify-between hover:shadow-md transition-all`;
        card.innerHTML = `
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
        `;
        container.appendChild(card);
      }});
    }}

    function renderKPI(rawData) {{
      let pos = 0, bank = 0, inv = 0, pendingCash = 0;
      if (currentStore === 'ALL') {{
        pos = rawData.kpi.total_pos;
        bank = rawData.kpi.total_bank;
        inv = rawData.kpi.total_inv;
        pendingCash = rawData.kpi.total_pending_cash;
      }} else {{
        const s = rawData.stores[currentStore];
        if (s) {{
          Object.values(s.channels || {{}}).forEach(c => {{
            pos += c.pos_sales || 0;
            bank += c.bank_cleared || 0;
            inv += c.invoice_cleared || 0;
            if (c.code === '1130-01' && c.diff > 0) {{
              pendingCash += c.diff;
            }}
          }});
        }}
      }}
      document.getElementById('kpi-pos').textContent = '$ ' + Math.round(pos).toLocaleString();
      document.getElementById('kpi-bank').textContent = '$ ' + Math.round(bank).toLocaleString();
      document.getElementById('kpi-inv').textContent = '$ ' + Math.round(inv).toLocaleString();
      document.getElementById('kpi-pending-cash').textContent = '$ ' + Math.round(pendingCash).toLocaleString();
    }}

    function renderAlerts(rawData) {{
      const container = document.getElementById('alert-cards-container');
      container.innerHTML = '';
      
      const filtered = currentStore === 'ALL' 
        ? rawData.alerts 
        : rawData.alerts.filter(a => a.store.includes(currentStore));

      document.getElementById('alert-title-text').textContent = 
        `門市未存款與逾期異常穿透追蹤 (${{filtered.length}} 筆需追蹤)`;

      if (filtered.length === 0) {{
        container.innerHTML = `
          <div class="col-span-full py-6 text-center text-emerald-600 bg-emerald-50 rounded-xl font-medium text-sm">
            <i class="fa-solid fa-circle-check text-lg mr-1.5"></i> 太棒了！此月份目前無任何逾期未存之現金款項。
          </div>
        `;
        return;
      }}

      filtered.forEach(a => {{
        const isSevere = a.delay_days >= 5;
        const borderClass = isSevere ? 'border-rose-300 bg-rose-50/50' : 'border-amber-200 bg-amber-50/40';
        const badgeClass = isSevere ? 'bg-rose-100 text-rose-700' : 'bg-amber-100 text-amber-700';
        const card = document.createElement('div');
        card.className = `p-4 rounded-xl border ${{borderClass}} flex flex-col justify-between transition-all hover:shadow-md`;
        card.innerHTML = `
          <div>
            <div class="flex justify-between items-start">
              <span class="font-bold text-slate-800 text-sm">${{a.store}}</span>
              <span class="text-xs font-semibold px-2 py-0.5 rounded-md ${{badgeClass}}">延遲 ${{a.delay_days}} 天</span>
            </div>
            <div class="text-xs text-slate-500 mt-1">營業日：<span class="font-semibold text-slate-700">${{a.date}}</span></div>
            <div class="text-lg font-bold text-rose-600 mt-1.5">$ ${{Math.round(a.cash).toLocaleString()}}</div>
          </div>
          <div class="mt-3 pt-2.5 border-t border-slate-200/60 text-xs text-slate-600 flex items-center justify-between">
            <span class="truncate"><i class="fa-solid fa-circle-exclamation text-amber-500 mr-1"></i>${{a.risk}}</span>
            <button onclick="scrollToDate('${{a.date}}')" class="text-indigo-600 font-semibold hover:underline text-[11px]">查看月曆</button>
          </div>
        `;
        container.appendChild(card);
      }});
    }}

    function renderCalendar(rawData) {{
      const grid = document.getElementById('calendar-grid');
      grid.innerHTML = '';
      
      const [yr, mo] = currentMonth.split('-').map(Number);
      document.getElementById('calendar-title-text').textContent = 
        `營業與存款核對月曆 (${{yr}} 年 ${{mo}} 月)`;

      const daysInMonth = new Date(yr, mo, 0).getDate();
      const firstDayOfWeek = new Date(yr, mo - 1, 1).getDay(); // 0=Sun, 1=Mon, ..., 6=Sat

      for (let i = 0; i < firstDayOfWeek; i++) {{
        const emptyCell = document.createElement('div');
        emptyCell.className = 'calendar-cell bg-slate-50/50 rounded-xl border border-dashed border-slate-200';
        grid.appendChild(emptyCell);
      }}

      let dailyCashMap = {{}};
      const storesToInclude = currentStore === 'ALL' ? Object.keys(rawData.stores) : [currentStore];
      
      storesToInclude.forEach(sk => {{
        const s = rawData.stores[sk];
        if (s && s.daily_cash) {{
          s.daily_cash.forEach(d => {{
            if (!dailyCashMap[d.date]) {{
              dailyCashMap[d.date] = [];
            }}
            dailyCashMap[d.date].push({{ store: s.name, ...d }});
          }});
        }}
      }});

      for (let day = 1; day <= daysInMonth; day++) {{
        const dayStr = day < 10 ? '0' + day : '' + day;
        const moStr = mo < 10 ? '0' + mo : '' + mo;
        const dateStr = `${{yr}}-${{moStr}}-${{dayStr}}`;
        const dayRecords = dailyCashMap[dateStr] || [];

        const cell = document.createElement('div');
        cell.id = `cal-date-${{dateStr}}`;
        cell.className = 'calendar-cell bg-white p-2.5 rounded-xl border border-slate-200 flex flex-col justify-between text-xs';

        let innerBadges = '';
        if (dayRecords.length > 0) {{
          dayRecords.forEach(r => {{
            if (r.cash > 0) {{
              if (r.used) {{
                const depTxt = r.period && r.period.includes('~') ? (r.period.includes('跨月') ? '跨月存' : '多日存') : (r.deposit_date ? r.deposit_date.slice(5) : '已存');
                innerBadges += `<div class="badge-deposit px-1.5 py-0.5 rounded text-[10px] font-medium mt-1 truncate" title="玉山 ${{r.deposit_date}} 存入 $${{r.cash}} (所屬: ${{r.period}})">
                  🟢 ${{currentStore === 'ALL' ? (r.store.slice(0,2)+': ') : ''}}$${{Math.round(r.cash).toLocaleString()}} (${{depTxt}})
                </div>`;
              }} else {{
                innerBadges += `<div class="badge-overdue px-1.5 py-0.5 rounded text-[10px] font-bold mt-1 truncate" title="尚未存入銀行">
                  🔴 ${{currentStore === 'ALL' ? (r.store.slice(0,2)+': ') : ''}}$${{Math.round(r.cash).toLocaleString()}} (未存)
                </div>`;
              }}
            }}
          }});
        }}

        cell.innerHTML = `
          <div>
            <div class="flex justify-between items-center">
              <span class="font-bold text-slate-700 text-sm">${{day}}</span>
              <span class="text-[10px] text-slate-400">${{mo}}/${{dayStr}}</span>
            </div>
            <div class="mt-0.5 space-y-1">
              ${{innerBadges || '<div class="text-[10px] text-slate-300 mt-2">無現金異動</div>'}}
            </div>
          </div>
        `;
        grid.appendChild(cell);
      }}
    }}

    function filterTransactions(rawDataArg) {{
      const rawData = rawDataArg || allMonthsData[currentMonth];
      if (!rawData) return;

      const tbody = document.getElementById('transaction-tbody');
      tbody.innerHTML = '';
      const query = (document.getElementById('search-input').value || '').toLowerCase();

      const filtered = (rawData.cleared_items || []).filter(item => {{
        if (currentStore !== 'ALL' && !item.store.includes(currentStore)) {{
          return false;
        }}
        if (currentCategoryFilter !== 'ALL') {{
          if (!item.category.includes(currentCategoryFilter)) return false;
        }}
        if (query) {{
          const str = (item.store + ' ' + item.bank_date + ' ' + item.category + ' ' + item.period + ' ' + item.remark + ' ' + item.bank_amt).toLowerCase();
          return str.includes(query);
        }}
        return true;
      }});

      if (filtered.length === 0) {{
        tbody.innerHTML = `<tr><td colspan="7" class="py-6 text-center text-slate-400">查無相符之單筆沖銷明細</td></tr>`;
        return;
      }}

      filtered.forEach((item, idx) => {{
        const tr = document.createElement('tr');
        const isCash = item.category.includes('現金');
        const isLine = item.category.includes('LINE');
        const isUber = item.category.includes('Uber');
        const isPanda = item.category.includes('Foodpanda');
        const isTouding = item.category.includes('偷訂');

        tr.className = isCash ? 'hover:bg-amber-50/50 transition-colors border-l-4 border-amber-400' :
          isLine ? 'hover:bg-emerald-50/50 transition-colors border-l-4 border-[#06C755]' :
          isUber ? 'hover:bg-slate-50 transition-colors border-l-4 border-slate-700' :
          isPanda ? 'hover:bg-pink-50/40 transition-colors border-l-4 border-pink-400' :
          isTouding ? 'hover:bg-purple-50/40 transition-colors border-l-4 border-purple-400' :
          'hover:bg-slate-50 transition-colors border-l-4 border-transparent';

        const catBadge = isCash ? '<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md font-bold text-xs bg-amber-100 text-amber-900 border border-amber-300 shadow-xs"><i class="fa-solid fa-money-bill-wave text-amber-600"></i> 現金存入</span>' :
          isLine ? '<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md font-bold text-xs bg-[#06C755] text-white shadow-xs"><i class="fa-brands fa-line text-white text-sm"></i> LINE Pay</span>' :
          isUber ? '<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md font-bold text-xs bg-slate-900 text-white shadow-xs"><i class="fa-solid fa-car-side text-slate-300"></i> Uber Eats</span>' :
          isPanda ? '<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md font-bold text-xs bg-pink-100 text-pink-800 border border-pink-300 shadow-xs"><i class="fa-solid fa-box text-pink-600"></i> Foodpanda</span>' :
          isTouding ? '<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md font-bold text-xs bg-purple-100 text-purple-800 border border-purple-300 shadow-xs"><i class="fa-solid fa-store text-purple-600"></i> 偷訂科技</span>' :
          `<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md font-bold text-xs bg-indigo-100 text-indigo-800 border border-indigo-200">${{item.category}}</span>`;

        const isMultiDay = item.period.includes('~') || item.period.includes('累計');
        const periodBadge = isMultiDay
          ? `<span class="px-2.5 py-1 rounded-md font-bold text-blue-900 bg-blue-50 border border-blue-200 text-xs">${{item.period}}</span>`
          : `<span class="px-2.5 py-1 rounded-md font-medium text-slate-700 bg-slate-100 border border-slate-200 text-xs">${{item.period}}</span>`;

        tr.innerHTML = `
          <td class="py-3 px-4 font-mono text-slate-400 text-center">${{item.idx}}</td>
          <td class="py-3 px-4 font-semibold text-slate-800">${{item.store}}</td>
          <td class="py-3 px-4 font-mono text-slate-600">${{item.bank_date}}</td>
          <td class="py-3 px-4">${{catBadge}}</td>
          <td class="py-3 px-4 text-right font-mono font-bold text-slate-900">$ ${{Math.round(item.bank_amt).toLocaleString()}}</td>
          <td class="py-3 px-4 text-center">${{periodBadge}}</td>
          <td class="py-3 px-4 text-slate-500 text-xs truncate max-w-xs" title="${{item.remark}}">${{item.remark}}</td>
        `;
        tbody.appendChild(tr);
      }});
    }}

    function scrollToDate(dateStr) {{
      const el = document.getElementById('cal-date-' + dateStr);
      if (el) {{
        el.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
        el.classList.add('ring-4', 'ring-rose-400');
        setTimeout(() => el.classList.remove('ring-4', 'ring-rose-400'), 2500);
      }}
    }}

    // Initial render
    renderDashboard();
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

    if sync_cloud:
        sync_to_cloud_database(multi_months_data)

    return out_file

if __name__ == "__main__":
    month = sys.argv[1] if len(sys.argv) > 1 else "2026-10"
    generate_html_dashboard(target_month=month)
