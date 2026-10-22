/**
 * ==============================================================================
 * 月下餐飲集團 · 營運對帳與財務可視化看板 (Google Apps Script 雲端網頁版)
 * 24小時無人值守 · 免開電腦終端機 · 手機/平板/電腦全平台即時查帳
 * ==============================================================================
 */

var CONFIG = {
  // 月宮 POS 試算表 ID (內建【雲端對帳資料庫】分頁)
  palaceSpreadsheetId: "19QdWcBQWVNi-SWvzuWhA9lfZFInbtrbmpNIqINa1DYA",
  // 隨意小吃 POS 試算表 ID
  suiyiSpreadsheetId: "1MQAJfH8ZY1D9mtFdtoOPldXlcSdmuY9XOZRKOX0bfNw",
  // 同步資料庫分頁名稱
  dbSheetName: "【雲端對帳資料庫】",
  defaultMonth: "2026-10"
};

/**
 * 網頁進入點 (GET 請求)
 */
function doGet(e) {
  var template = HtmlService.createTemplateFromFile("Index");
  
  // 支援 URL 參數，例如 ?month=2026-10&store=月宮
  template.initialMonth = (e && e.parameter && e.parameter.month) ? e.parameter.month : CONFIG.defaultMonth;
  template.initialStore = (e && e.parameter && e.parameter.store) ? e.parameter.store : "ALL";
  
  return template.evaluate()
    .setTitle("月下餐飲集團 · 營運對帳與財務可視化看板")
    .addMetaTag("viewport", "width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no")
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

/**
 * 取得系統內所有可選月份清單 (自動掃描雲端資料庫與 POS 歷史分頁)
 */
function getAvailableMonths() {
  var monthsSet = {};

  try {
    var ss = SpreadsheetApp.openById(CONFIG.palaceSpreadsheetId);
    
    // 1. 優先從【雲端對帳資料庫】分頁讀取
    var dbSheet = ss.getSheetByName(CONFIG.dbSheetName);
    if (dbSheet) {
      var data = dbSheet.getDataRange().getValues();
      for (var i = 1; i < data.length; i++) {
        var m = String(data[i][0]).trim();
        if (/^\d{4}-\d{2}$/.test(m)) {
          monthsSet[m] = true;
        }
      }
    }
    
    // 2. 掃描月宮試算表內所有月份格式分頁
    var sheets = ss.getSheets();
    for (var j = 0; j < sheets.length; j++) {
      var sname = sheets[j].getName().trim();
      if (/^\d{4}-\d{2}$/.test(sname)) {
        monthsSet[sname] = true;
      }
    }
  } catch (err) {
    Logger.log("讀取可用月份錯誤: " + err.toString());
  }

  // 確保預設月份存在
  monthsSet[CONFIG.defaultMonth] = true;
  
  // 依時間新至舊排序 (例如 2026-10, 2026-09, 2026-08 ...)
  var sortedMonths = Object.keys(monthsSet).sort().reverse();
  return sortedMonths;
}

/**
 * 取得指定月份之完整對帳與看板資料
 */
function getReconciliationData(targetMonth) {
  if (!targetMonth) targetMonth = CONFIG.defaultMonth;
  
  // 1. 嘗試從【雲端對帳資料庫】讀取高精度對帳快取 (含玉山銀行單筆沖銷與手續費發票扣抵)
  try {
    var ss = SpreadsheetApp.openById(CONFIG.palaceSpreadsheetId);
    var dbSheet = ss.getSheetByName(CONFIG.dbSheetName);
    
    if (dbSheet) {
      var rows = dbSheet.getDataRange().getValues();
      for (var i = 1; i < rows.length; i++) {
        var rowMonth = String(rows[i][0]).trim();
        if (rowMonth === targetMonth) {
          return {
            success: true,
            source: "cloud_database",
            target_month: targetMonth,
            updated_at: String(rows[i][1] || ""),
            kpi: rows[i][2] ? JSON.parse(rows[i][2]) : {},
            stores: rows[i][3] ? JSON.parse(rows[i][3]) : {},
            alerts: rows[i][4] ? JSON.parse(rows[i][4]) : [],
            cleared_items: rows[i][5] ? JSON.parse(rows[i][5]) : []
          };
        }
      }
    }
  } catch (err) {
    Logger.log("讀取雲端快取資料失敗: " + err.toString());
  }

  // 2. 若雲端資料庫尚未同步該月份，啟動即時雲端解析引擎 (直接解析 POS 分頁)
  return computeFromPOSSheets(targetMonth);
}

/**
 * 一次取得所有已同步月份資料 (供前端秒切換月份，完全無等待)
 */
function getAllMonthsData() {
  var allData = {};
  try {
    var ss = SpreadsheetApp.openById(CONFIG.palaceSpreadsheetId);
    var dbSheet = ss.getSheetByName(CONFIG.dbSheetName);
    if (dbSheet) {
      var rows = dbSheet.getDataRange().getValues();
      for (var i = 1; i < rows.length; i++) {
        var m = String(rows[i][0]).trim();
        if (/^\d{4}-\d{2}$/.test(m)) {
          allData[m] = {
            target_month: m,
            updated_at: String(rows[i][1] || ""),
            kpi: rows[i][2] ? JSON.parse(rows[i][2]) : {},
            stores: rows[i][3] ? JSON.parse(rows[i][3]) : {},
            alerts: rows[i][4] ? JSON.parse(rows[i][4]) : [],
            cleared_items: rows[i][5] ? JSON.parse(rows[i][5]) : []
          };
        }
      }
    }
  } catch (err) {
    Logger.log("讀取全部月份失敗: " + err.toString());
  }
  return allData;
}

/**
 * 即時雲端解析引擎：直接讀取月宮與隨意小吃 POS 分頁
 */
function computeFromPOSSheets(targetMonth) {
  var storeConfigs = [
    { name: "月宮", id: CONFIG.palaceSpreadsheetId },
    { name: "隨意小吃", id: CONFIG.suiyiSpreadsheetId }
  ];

  var storesData = {};
  var allAlerts = [];
  var totalPos = 0;
  var totalBank = 0;
  var totalInv = 0;
  var totalPendingCash = 0;
  var now = new Date();

  storeConfigs.forEach(function(cfg) {
    var storeName = cfg.name;
    var dailyCash = {};
    var storeAlerts = [];
    var posSum = 0;
    var bankSum = 0;
    var cashDiff = 0;
    var linepaySum = 0;
    var uberSum = 0;
    var pandaSum = 0;
    var toudingSum = 0;
    var cardSum = 0;

    try {
      var ss = SpreadsheetApp.openById(cfg.id);
      var ws = ss.getSheetByName(targetMonth);
      if (!ws) {
        return;
      }

      var values = ws.getDataRange().getValues();
      if (values.length < 3) return;

      var header = values[1]; // 第二列通常為標題列
      var colMap = {};
      for (var c = 0; c < header.length; c++) {
        var h = String(header[c] || "").replace(/\s+/g, "");
        if (h.indexOf("日期") !== -1) colMap.date = c;
        if (h.indexOf("每日營收") !== -1) colMap.total_sales = c;
        if (h === "現金") colMap.cash = c;
        if (h.indexOf("LinePay") !== -1 || h.indexOf("Line") !== -1) colMap.linepay = c;
        if (h.indexOf("Uber") !== -1) colMap.ubereats = c;
        if (h.indexOf("FoodPanda") !== -1 || h.indexOf("Foodpanda") !== -1) colMap.foodpanda = c;
        if (h.indexOf("偷訂") !== -1) colMap.touding = c;
        if (h.indexOf("刷卡") !== -1 || h.indexOf("信用卡") !== -1) colMap.card = c;
        if (h.indexOf("存款時間") !== -1) colMap.deposit_date = c;
        if (h.indexOf("存款金額") !== -1) colMap.deposit_amt = c;
        if (h.indexOf("存款傳票號碼") !== -1) colMap.deposit_voucher = c;
      }

      for (var r = 2; r < values.length; r++) {
        var row = values[r];
        var dStr = String(row[colMap.date] || "").trim();
        if (!/^\d{4}-\d{2}-\d{2}$/.test(dStr)) continue;

        var cashVal = parseMoney(row[colMap.cash]);
        var salesVal = parseMoney(row[colMap.total_sales]);
        var lpVal = colMap.linepay !== undefined ? parseMoney(row[colMap.linepay]) : 0;
        var ubVal = colMap.ubereats !== undefined ? parseMoney(row[colMap.ubereats]) : 0;
        var fpVal = colMap.foodpanda !== undefined ? parseMoney(row[colMap.foodpanda]) : 0;
        var tdVal = colMap.touding !== undefined ? parseMoney(row[colMap.touding]) : 0;
        var cdVal = colMap.card !== undefined ? parseMoney(row[colMap.card]) : 0;

        var depDate = String(row[colMap.deposit_date] || "").trim();
        var depAmt = parseMoney(row[colMap.deposit_amt]);
        var isDeposited = (depDate !== "" && depAmt > 0);

        posSum += salesVal;
        linepaySum += lpVal;
        uberSum += ubVal;
        pandaSum += fpVal;
        toudingSum += tdVal;
        cardSum += cdVal;

        if (isDeposited) {
          bankSum += depAmt;
        } else if (cashVal > 0) {
          cashDiff += cashVal;
          // 計算延遲天數
          var bDate = new Date(dStr);
          var diffTime = now.getTime() - bDate.getTime();
          var delayDays = Math.max(0, Math.floor(diffTime / (1000 * 3600 * 24)));
          var risk = delayDays >= 5 ? "🚨 嚴重逾期 (>5天)" : (delayDays >= 3 ? "⚠️ 延遲提醒 (3~4天)" : "⏳ 正常交班中 (1~2天)");

          var alertObj = {
            store: storeName,
            date: dStr,
            cash: cashVal,
            delay_days: delayDays,
            risk: risk
          };
          storeAlerts.push(alertObj);
          allAlerts.push(alertObj);
        }

        dailyCash[dStr] = {
          cash: cashVal,
          deposited: isDeposited,
          deposit_date: depDate,
          deposit_amt: depAmt,
          is_cross_month: false
        };
      }

      storesData[storeName] = {
        channels: {
          "1130-01": {
            name: "現金",
            pos_sales: cashDiff + bankSum,
            bank_cleared: bankSum,
            invoice_cleared: 0,
            diff: cashDiff
          },
          "1130-02": {
            name: "linepay",
            pos_sales: linepaySum,
            bank_cleared: 0,
            invoice_cleared: 0,
            diff: linepaySum
          },
          "1130-03": {
            name: "ubereats",
            pos_sales: uberSum,
            bank_cleared: 0,
            invoice_cleared: 0,
            diff: uberSum
          },
          "1130-04": {
            name: "Foodpanda",
            pos_sales: pandaSum,
            bank_cleared: 0,
            invoice_cleared: 0,
            diff: pandaSum
          },
          "1130-05": {
            name: "偷訂",
            pos_sales: toudingSum,
            bank_cleared: 0,
            invoice_cleared: 0,
            diff: toudingSum
          },
          "1130-06": {
            name: "刷卡",
            pos_sales: cardSum,
            bank_cleared: 0,
            invoice_cleared: 0,
            diff: cardSum
          }
        },
        daily_cash: dailyCash,
        pending_alerts: storeAlerts
      };

      totalPos += posSum;
      totalBank += bankSum;
      totalPendingCash += cashDiff;

    } catch (e) {
      Logger.log("解析門市 " + storeName + " 失敗: " + e.toString());
    }
  });

  allAlerts.sort(function(a, b) {
    return b.delay_days - a.delay_days;
  });

  var nowStr = Utilities.formatDate(now, "Asia/Taipei", "yyyy-MM-dd HH:mm:ss");

  return {
    success: true,
    source: "live_pos_calculation",
    target_month: targetMonth,
    updated_at: nowStr,
    kpi: {
      total_pos: totalPos,
      total_bank: totalBank,
      total_inv: 0,
      total_pending_cash: totalPendingCash,
      alert_count: allAlerts.length
    },
    stores: storesData,
    alerts: allAlerts,
    cleared_items: []
  };
}

function parseMoney(val) {
  if (!val) return 0;
  if (typeof val === "number") return val;
  var s = String(val).replace(/[$,\s]/g, "");
  var n = parseFloat(s);
  return isNaN(n) ? 0 : n;
}
