
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日更新凯利分档胜率表 kelly_p_table.json
   口径：14 天 + 90 天各半混合
   p = 0.5*p14 + 0.5*p90（单窗口样本不足时退回另一个）
"""
import json, os, sys, time, urllib.request
from datetime import datetime, timezone, timedelta
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import config as _cfg

WD = _cfg.HOME
SYMBOL = (_cfg.ACCOUNTS[0]["asset"] if _cfg.ACCOUNTS else "BTC") + _cfg.EXCHANGE["symbol_suffix"]
OUT = os.path.join(_cfg.STATE_DIR, "kelly_p_table.json")
LOG = os.path.join(_cfg.LOG_DIR, "kelly_p_update.log")
DAYS = 90
W_FAST, W_SLOW = 14, 90
MIN_EDGE = 0.006
MIN_N = 10
PRIOR = {"2":52.02,"3":53.44,"4":54.70,"5":54.93,"6":55.97,"7":54.82,"8":57.16,"9":58.57,"10":59.74}
TZ = timezone(timedelta(hours=8))

def log(s):
    line = f"[{datetime.now(TZ).strftime('%m-%d %H:%M:%S')}] {s}"
    try:
        if sys.stdout: print(line, flush=True)
    except Exception:
        pass
    try:
        with open(LOG, "a", encoding="utf-8") as f: f.write(line + "\n")
    except Exception:
        pass

def fetch_klines(days):
    now = int(time.time()*1000)
    start = (now - days*86400000)//300000*300000
    out = []; cur = start
    while cur < now:
        url = (f"https://data-api.binance.vision/api/v3/klines?symbol={SYMBOL}&interval=5m"
               f"&startTime={cur}&endTime={cur+999*300000}&limit=1000")
        d = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url, timeout=20) as r:
                    d = json.loads(r.read().decode())
                break
            except Exception:
                d = None; time.sleep(1.5*(attempt+1))
        if not d: break
        out += d
        if len(d) < 1000: break
        cur = d[-1][0] + 300000
        time.sleep(0.1)
    return out

def analyse(ks):
    now = int(time.time()*1000)
    ks = [k for k in ks if int(k[6]) <= now and int(k[6]) > now - DAYS*86400000]
    d = [1 if float(k[4])>float(k[1]) else (-1 if float(k[4])<float(k[1]) else 0) for k in ks]
    per = {}
    for i in range(3, len(d)-1):
        if d[i] == 0: continue
        L = 1; j = i-1
        while j >= 0 and d[j] == d[i]:
            L += 1; j -= 1
        if L < 2: continue
        key = str(min(L, 10))
        b = per.setdefault(key, {"n14":0,"w14":0,"n90":0,"w90":0})
        age_days = (now - int(ks[i][6]))/86400000.0
        if age_days <= W_FAST:
            b["n14"] += 1
            if d[i+1] == -d[i]: b["w14"] += 1
        if age_days <= W_SLOW:
            b["n90"] += 1
            if d[i+1] == -d[i]: b["w90"] += 1
    return per

def main():
    ks = fetch_klines(DAYS)
    if len(ks) < 2000:
        log("拉取失败，保留旧表"); return
    per = analyse(ks)
    table = {"updated_at": datetime.now(TZ).isoformat(timespec="seconds"),
             "days": DAYS, "windows": [W_FAST, W_SLOW], "blend": "0.5/0.5",
             "window_bars": len(ks), "min_edge": MIN_EDGE, "min_n": MIN_N,
             "prior_source": "9y(仅参考)", "levels": {}}
    for key in sorted(per, key=lambda x: int(x)):
        b = per[key]
        p14 = (b["w14"]/b["n14"]) if b["n14"] >= MIN_N else None
        p90 = (b["w90"]/b["n90"]) if b["n90"] >= MIN_N else None
        if p14 is None and p90 is None: continue
        if p14 is None: p = p90
        elif p90 is None: p = p14
        else: p = 0.5*p14 + 0.5*p90
        table["levels"][key] = {"n14": b["n14"], "p14": round(p14,4) if p14 is not None else None,
                                "n90": b["n90"], "p90": round(p90,4) if p90 is not None else None,
                                "n": b["n90"], "p": round(p,4),
                                "raw_pct": round(p*100,2), "prior_pct": float(PRIOR.get(key, 56.0)),
                                "trade": bool(p - 0.51 >= MIN_EDGE)}
    json.dump(table, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    log("更新完成 窗口=%d根 混合=%d/%d" % (len(ks), W_FAST, W_SLOW))
    for k, v in sorted(table["levels"].items(), key=lambda x: int(x[0])):
        log("  L=%s n14=%d p14=%s n90=%d p90=%s -> p=%.2f%% %s" % (
            k, v["n14"], ("%.1f%%"%(v["p14"]*100)) if v["p14"] is not None else "-",
            v["n90"], ("%.1f%%"%(v["p90"]*100)) if v["p90"] is not None else "-",
            v["p"]*100, "交易" if v["trade"] else "跳过"))

if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        log("异常: " + traceback.format_exc().replace("\n"," | ")[:500])
        sys.exit(1)
