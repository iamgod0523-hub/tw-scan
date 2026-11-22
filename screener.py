# -*- coding: utf-8 -*-
"""
台股收盤選股（上市＋上櫃）
- 從證交所抓近 26 個交易日的每日收盤行情（MI_INDEX），存在 data/ 資料夾，之後只補抓新的日子
- 存成 data.js；畫面是「台股掃描.html」，直接打開即可篩選（更新畫面不必重跑本程式）

使用：python screener.py        （每天收盤後約 15:00 以後執行）
"""
import base64
import datetime
import hashlib
import json
import os
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

T86_URLS = [  # 三大法人買賣超日報
    "https://www.twse.com.tw/rwd/zh/fund/T86?date={d}&selectType=ALLBUT0999&response=json",
    "https://www.twse.com.tw/fund/T86?response=json&date={d}&selectType=ALLBUT0999",
]
INST_DAYS = 5

# 櫃買中心（上櫃）：每日收盤行情、三大法人。日期格式 2026/11/20
TPEX_QUOTE_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date={d}&id=&response=json"
TPEX_INST_URL = "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade?type=Daily&sect=EW&date={d}&id=&response=json"

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


def parse_t86(j):
    """{代號: [外資, 投信, 自營商, 三大法人合計]}（張）"""
    if not isinstance(j, dict) or j.get("stat") != "OK":
        return None
    f, data = j.get("fields") or [], j.get("data") or []
    if not f and j.get("tables"):
        f, data = j["tables"][0].get("fields", []), j["tables"][0].get("data", [])

    def col(*starts):
        for st in starts:
            for i, name in enumerate(f):
                if name.startswith(st):
                    return i
        return None
    ic = col("證券代號")
    idx = [col("外陸資買賣超股數(不含外資自營商)", "外陸資買賣超", "外資買賣超"),
           col("投信買賣超"), col("自營商買賣超股數", "自營商買賣超"), col("三大法人買賣超")]
    if ic is None or idx[3] is None:
        return None
    out = {}
    for row in data:
        vals = [round((num(row[i]) or 0) / 1000) if i is not None else None for i in idx]
        out[str(row[ic]).strip()] = vals
    return out or None


def get_t86(d, today):
    key = d.strftime("%Y%m%d")
    path = os.path.join(CACHE, "t86_" + key + ".json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    data = None
    for u in T86_URLS:
        try:
            data = parse_t86(http_json(u.format(d=key)))
            if data:
                break
        except Exception:
            pass
    time.sleep(PAUSE)
    if data:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    return data


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


def tpex_table(j):
    if not isinstance(j, dict) or str(j.get("stat", "")).lower() != "ok":
        return None, None
    t = (j.get("tables") or [None])[0]
    if not t or not t.get("data"):
        return None, None
    return t.get("fields") or [], t["data"]


def parse_tpex(j):
    """上櫃行情 → ({代號: [名稱, 成交股數, 開, 高, 低, 收]}, {代號: 發行股數})；只留一般股票（四碼）"""
    f, data = tpex_table(j)
    if not data:
        return None, None
    i = {n: f.index(n) for n in ("代號", "名稱", "收盤", "開盤", "最高", "最低", "成交股數")}
    ish = f.index("發行股數") if "發行股數" in f else None
    out, sh = {}, {}
    for row in data:
        code = str(row[i["代號"]]).strip()
        if not (len(code) == 4 and code.isdigit() and code[0] != "0"):
            continue
        out[code] = [str(row[i["名稱"]]).strip(), num(row[i["成交股數"]]), num(row[i["開盤"]]),
                     num(row[i["最高"]]), num(row[i["最低"]]), num(row[i["收盤"]])]
        if ish is not None and num(row[ish]):
            sh[code] = num(row[ish])
    return out or None, sh


def get_tpex_day(d, today):
    """上櫃某日行情；只在上市有開盤的日子呼叫。過去的日子抓到就存起來"""
    key = d.strftime("%Y%m%d")
    path = os.path.join(CACHE, "otc_" + key + ".json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    data = None
    try:
        data, sh = parse_tpex(http_json(TPEX_QUOTE_URL.format(d=d.strftime("%Y/%m/%d"))))
    except Exception as e:
        print(f"  {d:%Y-%m-%d} 上櫃行情抓取失敗：{e}")
    time.sleep(PAUSE)
    if data:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        if sh:  # 最新一天的發行股數，算市值用
            with open(os.path.join(CACHE, "otc_shares.json"), "w", encoding="utf-8") as f:
                json.dump(sh, f)
    return data


def parse_tpex_inst(j):
    """上櫃三大法人 → {代號: [外資, 投信, 自營商, 合計]}（張）"""
    f, data = tpex_table(j)
    if not data:
        return None
    # 欄位依序：外資(不含自營) 買/賣/超、外資自營 3 欄、外資合計 3 欄、投信 3 欄、自營(自行) 3 欄、自營(避險) 3 欄、自營合計 3 欄、三大合計
    if len(f) < 24:
        return None
    out = {}
    for row in data:
        code = str(row[0]).strip()
        if not (len(code) == 4 and code.isdigit()):
            continue
        out[code] = [round((num(row[k]) or 0) / 1000) for k in (4, 13, 22, 23)]
    return out or None


def get_tpex_inst(d, today):
    key = d.strftime("%Y%m%d")
    path = os.path.join(CACHE, "otc_t86_" + key + ".json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    data = None
    try:
        data = parse_tpex_inst(http_json(TPEX_INST_URL.format(d=d.strftime("%Y/%m/%d"))))
    except Exception:
        pass
    time.sleep(PAUSE)
    if data:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    return data


def add_tpex(days, today):
    """把上櫃資料併進每天的行情，上櫃股票標記 m=o"""
    print("  抓取上櫃收盤資料（第一次約需 8 分鐘，之後只補新的日子）…", flush=True)
    otc = set()
    for d, data in days:
        t = get_tpex_day(d, today)
        if t:
            for code, v in t.items():
                if code not in data:
                    data[code] = v
                    otc.add(code)
    return otc


def compute(days, shares, inst, otc=frozenset()):
    """只整理原始資料；均線、量比、策略都在網頁裡算，改畫面或條件不必重跑"""
    last_date, last = days[-1]
    rows = []
    for code, info in last.items():
        k = []
        for _, data in days:
            r = data.get(code)
            ok = r and r[5] is not None and r[2] is not None
            k.append([r[2], r[3], r[4], r[5], round((r[1] or 0) / 1000)] if ok else None)
        if k[-1] is None:
            continue
        row = {"c": code, "n": info[0], "sh": shares.get(code), "k": k,
               "f": [d.get(code) if d else None for d in inst]}  # 近 5 日法人 [外資, 投信, 自營, 合計]
        if code in otc:
            row["m"] = "o"  # 上櫃
        rows.append(row)
    return last_date, rows


def write_data(last_date, rows, dates, fdates):
    payload = json.dumps({"date": last_date.strftime("%Y-%m-%d"), "dates": dates, "fdates": fdates,
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
    today = datetime.date.today()
    otc = add_tpex(days, today)
    print("  抓取三大法人買賣超（近 5 日）…", flush=True)
    recent = [d for d, _ in days[-INST_DAYS:]]
    inst = []
    for d in recent:
        try:
            a = get_t86(d, today)
        except Exception:
            a = None
        b = get_tpex_inst(d, today) if otc else None
        inst.append({**(b or {}), **(a or {})} if (a or b) else None)
    if inst[-1] is None:
        print("  今日法人資料尚未公布或抓取失敗（約 16:00 後公布），晚點再執行一次即可補上")
    shares = get_shares()
    otc_sh = os.path.join(CACHE, "otc_shares.json")
    if os.path.exists(otc_sh):
        with open(otc_sh, encoding="utf-8") as f:
            shares = {**json.load(f), **shares}
    last_date, rows = compute(days, shares, inst, otc)
    write_data(last_date, rows, [d.strftime("%m/%d") for d, _ in days],
               [d.strftime("%m/%d") for d in recent])
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
