# -*- coding: utf-8 -*-
"""
台股收盤選股（上市＋上櫃；上市行情取自證交所網站，上櫃與股數取自官方 OpenAPI）
- 從證交所抓近 26 個交易日的每日收盤行情（MI_INDEX），存在 data/ 資料夾，之後只補抓新的日子
- 存成 data.js；畫面是「台股掃描.html」，直接打開即可篩選（更新畫面不必重跑本程式）

使用：python screener.py        （每天收盤後約 15:00 以後執行）
"""
import base64
import datetime
import hashlib
import json
import math
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "data")
PAGE = os.path.join(BASE, "台股掃描.html")   # 畫面（更新功能時只要換這個檔）
DATA_JS = os.path.join(BASE, "data.js")        # 每天抓的資料
GH_CFG = os.path.join(BASE, "github.json")     # GitHub 上傳設定（金鑰，勿外流）
SITE_URL = "https://twstock-scan.pages.dev"
APP_FILES = ["sw.js", "manifest.webmanifest", "icon-192.png", "icon-512.png", "apple-touch-icon.png"]  # 手機 App 用
NEED_DAYS = 120         # K 線圖顯示 60 根，前面多抓 60 天算季線
MAX_LOOKBACK = 200      # 最多往回找幾個日曆天
PAUSE = 3.5             # 每次連線間隔秒數（證交所有流量限制，太快會被暫時封鎖）

SHARES_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"   # 上市公司基本資料（已發行股數）
SHARES_FILE = os.path.join(CACHE, "shares.json")

INST_DAYS = 5           # 近幾日三大法人明細
STREAK_DAYS = 20        # 連買連賣最多往回看幾個交易日
T86_URL = "https://www.twse.com.tw/rwd/zh/fund/T86?date={d}&selectType=ALLBUT0999&response=json"

# 櫃買中心 OpenAPI（官方開放資料）：只提供最新一個交易日，抓到就依資料日期存起來
TPEX_QUOTE_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
TPEX_INST_URL = "https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading"

URLS = [
    "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={d}&type=ALLBUT0999&response=json",
    "https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date={d}&type=ALLBUT0999",
]


def ssl_ctx():
    c = ssl.create_default_context()
    # Python 3.13 起預設的嚴格檢查會拒絕證交所的憑證，只關掉這一項，其餘驗證照常
    if hasattr(ssl, "VERIFY_X509_STRICT"):
        c.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return c


CTX = ssl_ctx()


def http_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=25, context=CTX) as r:
        return json.loads(r.read().decode("utf-8"))


def num(s):
    s = str(s).replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def parse(j):
    """回傳 {代號: [名稱, 成交股數, 開, 高, 低, 收]}；沒資料（假日）回傳 None"""
    if not isinstance(j, dict) or j.get("stat") != "OK":
        return None
    tables = list(j.get("tables") or [])
    for k in j:  # 舊格式 fields9 / data9
        if k.startswith("fields") and isinstance(j[k], list):
            tables.append({"fields": j[k], "data": j.get("data" + k[6:], [])})
    for t in tables:
        f = t.get("fields") or []
        if "證券代號" in f and "收盤價" in f:
            i = {name: f.index(name) for name in ("證券代號", "證券名稱", "成交股數", "開盤價", "最高價", "最低價", "收盤價")}
            out = {}
            for row in t.get("data") or []:
                code = str(row[i["證券代號"]]).strip()
                out[code] = [str(row[i["證券名稱"]]).strip(), num(row[i["成交股數"]]),
                             num(row[i["開盤價"]]), num(row[i["最高價"]]),
                             num(row[i["最低價"]]), num(row[i["收盤價"]])]
            return out or None
    return None


def get_day(d, today):
    key = d.strftime("%Y%m%d")
    path = os.path.join(CACHE, key + ".json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            v = json.load(f)
        return None if v == "holiday" else v
    data, err = None, None
    for u in URLS:
        try:
            data = parse(http_json(u.format(d=key)))
            err = None
            break
        except Exception as e:  # 換下一個網址再試
            err = e
    time.sleep(PAUSE)
    if err is not None:
        raise RuntimeError(f"{key} 抓取失敗：{err}")
    if data:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    elif d < today:  # 過去的日子沒資料＝休市，記下來不再重抓；今天可能只是還沒公布
        with open(path, "w", encoding="utf-8") as f:
            json.dump("holiday", f)
    return data


def collect():
    os.makedirs(CACHE, exist_ok=True)
    today = datetime.date.today()
    days, d = [], today
    for _ in range(MAX_LOOKBACK):
        if len(days) >= NEED_DAYS:
            break
        if d.weekday() < 5:
            cached = os.path.exists(os.path.join(CACHE, d.strftime("%Y%m%d") + ".json"))
            print(f"  {d:%Y-%m-%d} {'(快取)' if cached else '下載中…'}", flush=True)
            data = get_day(d, today)
            if data:
                days.append((d, data))
        d -= datetime.timedelta(days=1)
    days.reverse()
    return days


def get_shares():
    """{代號: 已發行普通股數}，一週更新一次；抓不到就用舊的，再不行就略過市值計算"""
    old = {}
    if os.path.exists(SHARES_FILE):
        with open(SHARES_FILE, encoding="utf-8") as f:
            old = json.load(f)
        # 抓取時間記在檔案裡（雲端每次重新下載檔案，不能用檔案修改時間判斷）
        fetched = old.pop("_at", 0)
        if time.time() - fetched < 7 * 86400:
            return old
    try:
        print("  更新上市公司股數（計算市值用）…", flush=True)
        out = {}
        for r in http_json(SHARES_URL):
            key = next((k for k in r if k.startswith("已發行普通股數")), None)
            n = num(r.get(key, "")) if key else None
            if n:
                out[str(r.get("公司代號", "")).strip()] = n
        if out:
            with open(SHARES_FILE, "w", encoding="utf-8") as f:
                json.dump({**out, "_at": time.time()}, f)
            return out
    except Exception as e:
        print(f"  股數資料抓取失敗（{e}），市值排名改用舊資料" if old else f"  股數資料抓取失敗（{e}），本次無法排除權值股")
    return old


# 產業別（上市＋上櫃公司基本資料），代碼對照證交所產業分類
TPEX_PROFILE_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
INDUSTRY_FILE = os.path.join(CACHE, "industry.json")
IND_NAMES = {
    "01": "水泥", "02": "食品", "03": "塑膠", "04": "紡織纖維", "05": "電機機械", "06": "電器電纜",
    "07": "化學生技醫療", "08": "玻璃陶瓷", "09": "造紙", "10": "鋼鐵", "11": "橡膠", "12": "汽車",
    "13": "電子", "14": "建材營造", "15": "航運", "16": "觀光餐旅", "17": "金融保險", "18": "貿易百貨",
    "19": "綜合", "20": "其他", "21": "化學", "22": "生技醫療", "23": "油電燃氣", "24": "半導體",
    "25": "電腦及週邊設備", "26": "光電", "27": "通信網路", "28": "電子零組件", "29": "電子通路",
    "30": "資訊服務", "31": "其他電子", "32": "文化創意", "33": "農業科技", "34": "電子商務",
    "35": "綠能環保", "36": "數位雲端", "37": "運動休閒", "38": "居家生活", "80": "管理股票",
}


def ind_name(v):
    v = str(v or "").strip()
    if not v:
        return None
    if v.isdigit():
        return IND_NAMES.get(v.zfill(2))
    for suf in ("工業", "業"):
        if v.endswith(suf) and len(v) > len(suf) + 1:
            return v[:-len(suf)]
    return v


def get_industry():
    """{代號: 產業別}，一週更新一次；抓不到就用舊的"""
    old = {}
    if os.path.exists(INDUSTRY_FILE):
        with open(INDUSTRY_FILE, encoding="utf-8") as f:
            old = json.load(f)
        if time.time() - old.pop("_at", 0) < 7 * 86400 and old:
            return old
    out = {}
    for url in (SHARES_URL, TPEX_PROFILE_URL):
        try:
            print(f"  更新產業別：{url.rsplit('/', 1)[-1]}…", flush=True)
            for r in http_json(url):
                ck = next((k for k in r if k in ("公司代號", "SecuritiesCompanyCode")), None)
                ik = next((k for k in r if "產業" in k or "Industry" in k), None)
                code = stock_code(r.get(ck)) if ck else None
                name = ind_name(r.get(ik)) if ik else None
                if code and name:
                    out[code] = name
            time.sleep(1)
        except Exception as e:
            print(f"  產業別抓取失敗（{e}）", flush=True)
    if out:
        merged = {**old, **out}
        with open(INDUSTRY_FILE, "w", encoding="utf-8") as f:
            json.dump({**merged, "_at": time.time()}, f, ensure_ascii=False)
        print(f"  產業別：{len(out)} 檔", flush=True)
        return merged
    return old


def roc_date(v):
    """民國日期 1151123 → date(2026, 11, 23)"""
    v = str(v).strip()
    try:
        return datetime.date(int(v[:-4]) + 1911, int(v[-4:-2]), int(v[-2:]))
    except (ValueError, IndexError):
        return None


def stock_code(c):
    c = str(c).strip()
    return c if len(c) == 4 and c.isdigit() and c[0] != "0" else None


def fetch_tpex():
    """從櫃買中心 OpenAPI 抓最新一天的上櫃行情與三大法人，依資料日期存檔（存過的日子不會再抓）"""
    try:
        rows = http_json(TPEX_QUOTE_URL)
        out, sh, day = {}, {}, None
        for r in rows:
            code = stock_code(r.get("SecuritiesCompanyCode"))
            if not code:
                continue
            day = day or roc_date(r.get("Date"))
            out[code] = [str(r.get("CompanyName", "")).strip(), num(r.get("TradingShares")), num(r.get("Open")),
                         num(r.get("High")), num(r.get("Low")), num(r.get("Close"))]
            if num(r.get("Capitals")):
                sh[code] = num(r.get("Capitals"))
        if day and out:
            key = day.strftime("%Y%m%d")
            with open(os.path.join(CACHE, "otc_" + key + ".json"), "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False)
            with open(os.path.join(CACHE, "otc_shares.json"), "w", encoding="utf-8") as f:
                json.dump(sh, f)
            print(f"  上櫃行情：{day:%Y-%m-%d}（{len(out)} 檔）", flush=True)
    except Exception as e:
        print(f"  上櫃行情抓取失敗：{e}", flush=True)
    try:
        rows = http_json(TPEX_INST_URL)
        out, day = {}, None
        for r in rows:
            code = stock_code(r.get("SecuritiesCompanyCode"))
            if not code:
                continue
            day = day or roc_date(r.get("Date"))
            fk = next((k for k in r if "Foreign Dealers excluded" in k and k.strip().endswith("Difference")), None)
            vals = [num(r.get(fk)) if fk else None, num(r.get("SecuritiesInvestmentTrustCompanies-Difference")),
                    num(r.get("Dealers-Difference")), num(r.get("TotalDifference"))]
            out[code] = [round((v or 0) / 1000) for v in vals]
        if day and out:
            with open(os.path.join(CACHE, "otc_t86_" + day.strftime("%Y%m%d") + ".json"), "w", encoding="utf-8") as f:
                json.dump(out, f)
            print(f"  上櫃三大法人：{day:%Y-%m-%d}", flush=True)
    except Exception as e:
        print(f"  上櫃三大法人抓取失敗：{e}", flush=True)


def load_cache(name):
    path = os.path.join(CACHE, name)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


# 籌碼：融資融券（每天）、集保大戶持股（每週）
MARGIN_URL = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={d}&selectType=ALL&response=json"
MARGIN_OPEN_URL = "https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN"
TPEX_MARGIN_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_margin_balance"
TDCC_URL = "https://opendata.tdcc.com.tw/getOD.ashx?id=1-5"
MARGIN_DAYS = 5         # 融資「近 5 日增減」


def get_margin(d, latest=False):
    """上市融資融券（張）：{代號: [融資今日餘額, 融資前日餘額, 融券今日餘額, 融券前日餘額]}，依日期存檔"""
    key = d.strftime("%Y%m%d")
    path = os.path.join(CACHE, "margin_" + key + ".json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    out = {}
    try:
        j = http_json(MARGIN_URL.format(d=key))
        if isinstance(j, dict) and j.get("stat") == "OK":
            tables = list(j.get("tables") or [])
            if j.get("creditFields"):
                tables.append({"fields": j["creditFields"], "data": j.get("creditList", [])})
            for t in tables:
                f = t.get("fields") or []
                if len(f) >= 13 and "代號" in str(f[0]):
                    for row in t.get("data") or []:
                        code = stock_code(row[0])
                        if code:
                            out[code] = [num(row[6]), num(row[5]), num(row[12]), num(row[11])]
                    break
    except Exception as e:
        print(f"  上市融資融券（網站）抓取失敗：{e}", flush=True)
    if not out and latest:  # 備援：OpenAPI 沒有日期，用「前日餘額＝上一個存檔的今日餘額」確認是這一天的資料
        try:
            prev = None
            for name in sorted(os.listdir(CACHE), reverse=True):
                if name.startswith("margin_") and name < "margin_" + key:
                    prev = load_cache(name)
                    break
            tmp = {}
            for r in http_json(MARGIN_OPEN_URL):
                code = stock_code(r.get("股票代號"))
                if code:
                    tmp[code] = [num(r.get("融資今日餘額")), num(r.get("融資前日餘額")), num(r.get("融券今日餘額")), num(r.get("融券前日餘額"))]
            same = [c for c in tmp if prev and c in prev and prev[c][0] == tmp[c][1]]
            if prev and len(same) > 0.9 * len(tmp):
                out = tmp
            else:
                print("  上市融資融券（OpenAPI）無法確認資料日期，略過", flush=True)
        except Exception as e:
            print(f"  上市融資融券（OpenAPI）抓取失敗：{e}", flush=True)
    time.sleep(PAUSE)
    if out:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f)
        print(f"  上市融資融券：{d:%Y-%m-%d}（{len(out)} 檔）", flush=True)
    else:
        print(f"  上市融資融券：{d:%Y-%m-%d} 尚未公布", flush=True)
    return out or None


def fetch_tpex_margin():
    """上櫃融資融券（櫃買 OpenAPI，只有最新一天），依資料日期存檔"""
    try:
        rows = http_json(TPEX_MARGIN_URL)
        if rows:
            print(f"  上櫃融資融券欄位：{list(rows[0].keys())}", flush=True)
        out, day = {}, None
        for r in rows:
            code = stock_code(r.get("SecuritiesCompanyCode") or r.get("代號"))
            if not code:
                continue
            day = day or roc_date(r.get("Date") or r.get("資料日期"))
            ks = {k.lower().replace("_", ""): k for k in r}
            def pick(*must, no=()):
                for lk, k in ks.items():
                    if all(m in lk for m in must) and not any(n in lk for n in no):
                        return num(r[k])
                return None
            mb = pick("margin", "balance", no=("previous", "prev", "yesterday", "limit", "quota", "rate"))
            mp = pick("margin", "balance", "previous") or pick("margin", "balance", "prev")
            sb = pick("short", "balance", no=("previous", "prev", "yesterday", "limit", "quota", "rate"))
            sp = pick("short", "balance", "previous") or pick("short", "balance", "prev")
            if mb is not None:
                out[code] = [mb, mp, sb, sp]
        if day and out:
            with open(os.path.join(CACHE, "otc_margin_" + day.strftime("%Y%m%d") + ".json"), "w", encoding="utf-8") as f:
                json.dump(out, f)
            print(f"  上櫃融資融券：{day:%Y-%m-%d}（{len(out)} 檔）", flush=True)
        else:
            print("  上櫃融資融券：沒有可用的資料", flush=True)
    except Exception as e:
        print(f"  上櫃融資融券抓取失敗：{e}", flush=True)


def get_tdcc():
    """集保戶股權分散表（每週）：{代號: [千張以上持股比例%, 總股東人數]}，依資料日期存檔；回傳 (最新, 前一週, 最新日期)"""
    try:
        req = urllib.request.Request(TDCC_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60, context=CTX) as r:
            text = r.read().decode("utf-8-sig", errors="replace")
        out, day = {}, None
        for line in text.splitlines()[1:]:
            p = line.split(",")
            if len(p) < 6:
                continue
            code = stock_code(p[1])
            if not code:
                continue
            day = day or p[0].strip()
            lv = p[2].strip()
            if lv == "15":
                out.setdefault(code, [None, None])[0] = num(p[5])
            elif lv == "17":
                out.setdefault(code, [None, None])[1] = num(p[3])
        if day and out and day.isdigit():
            path = os.path.join(CACHE, "tdcc_" + day + ".json")
            if not os.path.exists(path):
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(out, f)
            print(f"  集保股權分散：{day}（{len(out)} 檔）", flush=True)
    except Exception as e:
        print(f"  集保股權分散抓取失敗：{e}", flush=True)
    files = sorted(n for n in os.listdir(CACHE) if n.startswith("tdcc_"))
    cur = load_cache(files[-1]) if files else None
    prev = load_cache(files[-2]) if len(files) > 1 else None
    dt = files[-1][5:13] if files else None
    return cur, prev, (f"{dt[:4]}-{dt[4:6]}-{dt[6:]}" if dt else None)


def margin_rows(days, otc_date):
    """每檔 [融資餘額, 融資今日增減, 融券餘額, 融券今日增減, 融資近 5 日增減]（張）"""
    last = days[-1][0]
    for d, _ in days[-(MARGIN_DAYS + 1):]:  # 第一次會補抓近 6 天，之後只抓新的一天
        get_margin(d, d == last)
    fetch_tpex_margin()
    out = {}
    for prefix, end in (("margin_", last), ("otc_margin_", otc_date)):
        if not end:
            continue
        ds = [d for d, _ in days if d <= end]
        cur = load_cache(prefix + end.strftime("%Y%m%d") + ".json")
        if not cur:
            continue
        old = load_cache(prefix + ds[-1 - MARGIN_DAYS].strftime("%Y%m%d") + ".json") if len(ds) > MARGIN_DAYS else None
        for code, (mb, mp, sb, sp) in cur.items():
            if mb is None:
                continue
            m5 = mb - old[code][0] if old and code in old and old[code][0] is not None else None
            out[code] = [mb, mb - mp if mp is not None else None, sb, sb - sp if sb is not None and sp is not None else None, m5]
    return out



def get_t86(d, latest):
    """上市三大法人（證交所 T86）：{代號: [外資, 投信, 自營商, 合計]}（張），依日期存檔"""
    key = d.strftime("%Y%m%d")
    path = os.path.join(CACHE, "t86_" + key + ".json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    out, failed = {}, False
    try:
        j = http_json(T86_URL.format(d=key))
        if isinstance(j, dict) and j.get("stat") == "OK":
            f = j.get("fields") or []
            rows = j.get("data") or []
            if not f and j.get("tables"):
                f = j["tables"][0].get("fields") or []
                rows = j["tables"][0].get("data") or []
            def idx(*names):
                for n in names:
                    for i, x in enumerate(f):
                        if str(x).strip() == n:
                            return i
                return None
            i_c = idx("證券代號")
            i_f = idx("外陸資買賣超股數(不含外資自營商)", "外資買賣超股數")
            i_t = idx("投信買賣超股數")
            i_d = idx("自營商買賣超股數")
            i_a = idx("三大法人買賣超股數")
            if None not in (i_c, i_f, i_t, i_d, i_a):
                for r in rows:
                    code = stock_code(r[i_c])
                    if code:
                        out[code] = [round((num(r[i]) or 0) / 1000) for i in (i_f, i_t, i_d, i_a)]
            else:
                print(f"  上市三大法人欄位不符：{f}", flush=True)
    except Exception as e:
        print(f"  上市三大法人 {key} 抓取失敗：{e}", flush=True)
        failed = True
    time.sleep(PAUSE)
    if out:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(out, fh)
        print(f"  上市三大法人：{d:%Y-%m-%d}（{len(out)} 檔）", flush=True)
    elif d < latest and not failed:  # 過去的日子證交所沒有資料就記成空的，不再重抓
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({}, fh)
    return out or None


def streak(hist, j):
    """由最新一天往回數連續買超（正）或賣超（負）的天數；沒資料或 0 就停"""
    n = 0
    for v in reversed(hist):
        x = v[j] if v else 0
        if not x or (n and (x > 0) != (n > 0)):
            break
        n += 1 if x > 0 else -1
    return n


def add_tpex(days):
    """把快取裡的上櫃資料併進每天的行情，上櫃股票標記 m=o；回傳 (上櫃代號, 上櫃最新資料日)"""
    fetch_tpex()
    otc, last = set(), None
    for d, data in days:
        t = load_cache("otc_" + d.strftime("%Y%m%d") + ".json")
        if t:
            last = d
            for code, v in t.items():
                if code not in data:
                    data[code] = v
                    otc.add(code)
    return otc, last


def compute(days, shares, inst_by_day, otc=frozenset(), ind=None, mg=None, bh=None):
    """只整理原始資料；均線、量比、策略都在網頁裡算，改畫面或條件不必重跑。
    上櫃若還沒更新到最新一天，就用它自己最新的那天（row["e"] = 落後幾天）"""
    last_date = days[-1][0]
    codes = set(days[-1][1]) | set(otc)
    rows = []
    for code in codes:
        k = []
        for _, data in days:
            r = data.get(code)
            ok = r and r[5] is not None and r[2] is not None
            k.append([r[2], r[3], r[4], r[5], round((r[1] or 0) / 1000)] if ok else None)
        lag = 0
        if code in otc:
            while lag < 3 and k and k[-1] is None and days[-1 - lag][1].get(code) is None:
                k.pop(); lag += 1
        if not k or k[-1] is None:
            continue
        name = next(data[code][0] for _, data in reversed(days) if code in data)
        end = len(days) - lag
        f = [inst_by_day.get(d.strftime("%Y%m%d"), {}).get(code) for d, _ in days[max(0, end - INST_DAYS):end]]
        row = {"c": code, "n": name, "sh": shares.get(code), "k": k, "f": f}  # 近 5 日法人 [外資, 投信, 自營, 合計]
        hist = [inst_by_day.get(d.strftime("%Y%m%d"), {}).get(code) for d, _ in days[max(0, end - STREAK_DAYS):end]]
        if any(hist):
            row["fs"] = [streak(hist, j) for j in range(4)]  # 連續買（正）賣（負）超天數 [外資, 投信, 自營, 合計]
        if ind and ind.get(code):
            row["i"] = ind[code]  # 產業別
        if mg and code in mg:
            row["mg"] = mg[code]  # [融資餘額, 今日增減, 融券餘額, 今日增減, 融資 5 日增減]（張）
        if bh and bh[0] and code in bh[0]:
            cur, prev = bh[0][code], (bh[1] or {}).get(code)
            row["bh"] = [cur[0], prev[0] if prev else None, cur[1], prev[1] if prev else None]  # [千張大戶%, 上週%, 股東人數, 上週人數]
        if code in otc:
            row["m"] = "o"  # 上櫃
        if lag:
            row["e"] = lag
        rows.append(row)
    return last_date, rows


LINK_TOP = 100        # 前一天成交量前幾名當「領漲股」
LINK_MIN = 80         # 聯動分數幾分以上才列為「跟漲股」


def _rank(a):
    o = sorted(range(len(a)), key=lambda i: a[i])
    r = [0.0] * len(a)
    i = 0
    while i < len(o):
        j = i
        while j + 1 < len(o) and a[o[j + 1]] == a[o[i]]:
            j += 1
        for k in range(i, j + 1):
            r[o[k]] = (i + j) / 2
        i = j + 1
    return r


def _pear(a, b, n, lag=0):
    """a[t] 對 b[t+lag] 的排名相關，取最近 n 組（與網頁的算法相同）"""
    L = min(len(a), len(b))
    xs, ys = [], []
    t = L - 1 - lag
    while t >= 0 and len(xs) < n:
        p, q = a[t], b[t + lag]
        if p is not None and q is not None:
            xs.append(p); ys.append(q)
        t -= 1
    if len(xs) < min(n, 15) * 0.8:
        return None
    xs, ys = _rank(xs), _rank(ys)
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = sum((x - mx) ** 2 for x in xs)
    sy = sum((y - my) ** 2 for y in ys)
    return sxy / (sx * sy) ** 0.5 if sx and sy else None


def _same_dir(a, b, n):
    L = min(len(a), len(b))
    m = k = 0
    for t in range(L - 1, -1, -1):
        if k >= n:
            break
        p, q = a[t], b[t]
        if p is None or q is None or (abs(p) >= .095 and abs(q) >= .095):
            continue
        k += 1
        sp = (round(p * 1e4) > 0) - (round(p * 1e4) < 0)
        sq = (round(q * 1e4) > 0) - (round(q * 1e4) < 0)
        if sp == sq:
            m += 1
    return m / k if k >= 10 else None


def _series(K):
    r, lv = [], []
    for i in range(len(K)):
        a, b = K[i - 1] if i else None, K[i]
        r.append(b[3] / a[3] - 1 if a and b and a[3] else None)
        lv.append(math.log(b[4] / a[4]) if a and b and a[4] and b[4] and a[4] > 0 and b[4] > 0 else None)
    return r, lv


def _score(A, B):
    cl = lambda v: max(0.0, min(1.0, v or 0))
    c20, c60, c120 = _pear(A[0], B[0], 20), _pear(A[0], B[0], 60), _pear(A[0], B[0], 120)
    if c20 is None and c60 is None:
        return None
    sd, vs = _same_dir(A[0], B[0], 20), _pear(A[1], B[1], 60)
    ab = ba = -1
    for d in (1, 2):
        p, q = _pear(A[0], B[0], 60, d), _pear(B[0], A[0], 60, d)
        if p is not None and p > ab: ab = p
        if q is not None and q > ba: ba = q
    best = max(ab, ba)
    nz = lambda *v: next((x for x in v if x is not None), None)
    cm = nz(c20, c60) * .5 + nz(c60, c20) * .3 + nz(c120, c60, c20) * .2
    sc = 100 * (.3 * cl(cm / .8) + .25 * cl(((sd if sd is not None else 0) - .5) / .4) + .2 * cl((vs or 0) / .6)
                + .15 * cl(max(nz(c60, c20), best) / .8) + .1)
    return int(math.floor(sc + 0.5))


def write_links(rows, last_date):
    """盤中「族群聯動」通知用：前一天成交量前 LINK_TOP 名（領漲股）→ 同族群聯動 LINK_MIN 分以上的股票（跟漲股）"""
    gpath = os.path.join(BASE, "groups.js")
    if not os.path.exists(gpath):
        return
    with open(gpath, encoding="utf-8") as f:
        m = re.search(r"TWS_GROUPS\s*=\s*(\{.*?\});", f.read(), re.S)
    groups = json.loads(m.group(1)) if m else {}
    ok = [r for r in rows if re.fullmatch(r"[1-9]\d{3}", r["c"]) and not r.get("e") and r["c"] in groups and r["k"][-1]]
    by = {r["c"]: r for r in ok}
    ser = {}
    S = lambda c: ser.setdefault(c, _series(by[c]["k"]))
    top = sorted(ok, key=lambda r: -(r["k"][-1][4] or 0))[:LINK_TOP]
    lead, used = {}, set()
    for r in top:
        x, out = r["c"], []
        for y, ry in by.items():
            if y == x:
                continue
            g = [n for n in groups[y] if n in groups[x]]
            if not g:
                continue
            s = _score(S(x), S(y))
            if s is not None and s >= LINK_MIN:
                out.append([y, s, g[0]])
        if out:
            out.sort(key=lambda v: -v[1])
            lead[x] = out[:8]
            used.add(x); used.update(v[0] for v in out[:8])
    data = {"date": last_date.strftime("%Y-%m-%d"), "lead": lead,
            "n": {c: by[c]["n"] for c in sorted(used)}, "o": sorted(c for c in used if by[c].get("m") == "o")}
    os.makedirs(os.path.join(BASE, "data"), exist_ok=True)
    with open(os.path.join(BASE, "data", "links.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"  族群聯動：領漲股 {len(lead)} 檔、追蹤 {len(used)} 檔")


def write_data(last_date, rows, dates, fdates, otc_date=None, bdate=None):
    payload = json.dumps({"date": last_date.strftime("%Y-%m-%d"), "dates": dates, "fdates": fdates,
                          "odate": otc_date.strftime("%Y-%m-%d") if otc_date else None, "bdate": bdate,
                          "built": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                          "rows": rows}, ensure_ascii=False, separators=(",", ":"))
    with open(DATA_JS, "w", encoding="utf-8") as f:
        f.write("window.TWS_DATA=" + payload + ";\n")


def github_upload():
    """把 data.js 上傳到 GitHub（網站會自動更新）。
    網頁檔現在直接在 GitHub 上維護，預設不上傳，避免電腦裡的舊版蓋掉新版；
    真的要用電腦裡的網頁覆蓋時，加 --with-page。"""
    if not os.path.exists(GH_CFG):
        print("\n（沒有 github.json，略過上傳。要用手機看請照說明設定）")
        return
    with open(GH_CFG, encoding="utf-8") as f:
        cfg = json.load(f)
    token, repo, branch = cfg["token"].strip(), cfg["repo"].strip(), cfg.get("branch", "main")
    hdr = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
           "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "tw-screener"}

    def api(method, url, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=hdr)
        with urllib.request.urlopen(req, timeout=120, context=CTX) as r:
            return json.loads(r.read().decode() or "{}")

    print("\n上傳到 GitHub…")
    files = [(DATA_JS, "data.js")]
    if "--with-page" in sys.argv:
        files = [(PAGE, "index.html")] + files + [(os.path.join(BASE, n), n) for n in APP_FILES]
    for local, remote in files:
        if not os.path.exists(local):
            continue
        with open(local, "rb") as f:
            content = f.read()
        url = f"https://api.github.com/repos/{repo}/contents/{remote}"
        sha = None
        try:
            sha = api("GET", url + "?ref=" + urllib.parse.quote(branch)).get("sha")
        except urllib.error.HTTPError as e:
            if e.code != 404:
                print(f"  {remote} 讀取失敗：HTTP {e.code}（請檢查 github.json 的 repo 與金鑰權限）")
                return
        if sha == hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest():
            print(f"  {remote} 沒有變更")
            continue
        body = {"message": f"update {remote} {datetime.datetime.now():%Y-%m-%d %H:%M}",
                "content": base64.b64encode(content).decode(), "branch": branch}
        if sha:
            body["sha"] = sha
        try:
            api("PUT", url, body)
            print(f"  {remote} 已上傳")
        except urllib.error.HTTPError as e:
            print(f"  {remote} 上傳失敗：HTTP {e.code} {e.read().decode(errors='replace')[:200]}")
            return
    print(f"手機網址：{SITE_URL}（上傳後約 1 分鐘生效）")


def main():
    if "--upload-only" in sys.argv:
        github_upload()
        return 0
    print("抓取證交所收盤資料（第一次約需 7 分鐘，之後只補新的日子）")
    try:
        days = collect()
    except RuntimeError as e:
        print("\n" + str(e))
        print("請確認網路正常，或過幾分鐘再執行（連線太頻繁時證交所會暫時封鎖）。")
        return 1
    if len(days) < 21:
        print(f"只取得 {len(days)} 個交易日，不足以計算月線。")
        return 1
    otc, otc_date = add_tpex(days)
    # 三大法人：上市取自證交所 T86（每日），上櫃取自櫃買 OpenAPI；近 STREAK_DAYS 日用來算連買連賣
    inst_by_day = {}
    for d, _ in days[-(STREAK_DAYS + 3):]:
        key = d.strftime("%Y%m%d")
        t = {**(get_t86(d, days[-1][0]) or {}), **(load_cache("otc_t86_" + key + ".json") or {})}
        if t:
            inst_by_day[key] = t
    recent = [d for d, _ in days[-INST_DAYS:]]
    shares = get_shares()
    otc_sh = os.path.join(CACHE, "otc_shares.json")
    if os.path.exists(otc_sh):
        with open(otc_sh, encoding="utf-8") as f:
            shares = {**json.load(f), **shares}
    mg = margin_rows(days, otc_date)
    last_date, rows = compute(days, shares, inst_by_day, otc, get_industry(), mg)
    write_data(last_date, rows, [d.strftime("%m/%d") for d, _ in days],
               [d.strftime("%m/%d") for d in recent], otc_date)
    try:
        write_links(rows, last_date)
    except Exception as e:
        print(f"  族群聯動表產生失敗：{e}")
    if otc_date and otc_date < last_date:
        print(f"  上櫃資料還停在 {otc_date:%Y-%m-%d}（櫃買中心尚未更新），晚點再執行一次即可補上")
    print(f"\n完成：資料日期 {last_date:%Y-%m-%d}，共 {len(rows)} 檔（上櫃 {sum(1 for r in rows if r.get('m') == 'o')} 檔）。請打開：{PAGE}")
    try:
        github_upload()
    except Exception as e:
        print(f"上傳 GitHub 失敗：{e}")
    if "--no-open" not in sys.argv:
        webbrowser.open("file:///" + PAGE.replace("\\", "/"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
