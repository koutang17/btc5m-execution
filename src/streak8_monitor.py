#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BTC/ETH 5m 8连涨/8连跌提醒：只在第8根刚形成时推送一次"""
import json, os, sys, time, subprocess, urllib.request
from datetime import datetime, timezone, timedelta
import config as _cfg
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from notify import send

TZ=_cfg.TZ
LOG_F=os.path.join(_cfg.LOG_DIR,"streak8.log")
def now_ms(): return int(time.time()*1000)
def log(s):
    line=f"[{datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')}] {s}"
    print(line,flush=True)
    try:
        with open(LOG_F,"a",encoding="utf-8") as f: f.write(line+"\n")
    except Exception: pass
def fmt(ms): return datetime.fromtimestamp(ms/1000,TZ).strftime("%m-%d %H:%M")
def klines(symbol="BTCUSDT"):
    url=f"{_cfg.EXCHANGE['api_base']}/api/v3/klines?symbol={symbol}&interval=5m&limit=20"
    try:
        req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
        d=json.loads(urllib.request.urlopen(req,timeout=15).read())
    except Exception:
        r=subprocess.run(["curl.exe","-sS","--noproxy","*","--max-time","15",url],capture_output=True,text=True,
                         creationflags=subprocess.CREATE_NO_WINDOW)
        d=json.loads(r.stdout)
    return [{"ot":int(k[0]),"o":float(k[1]),"c":float(k[4]),"ct":int(k[6])} for k in d]

def check(asset="BTC", alert=True):
    symbol=asset+"USDT"
    bars=[b for b in klines(symbol) if b["ct"]<=now_ms()]
    if len(bars)<9: return None
    l8=bars[-8:]; prev=bars[-9]
    up=all(b["c"]>b["o"] for b in l8)
    down=all(b["c"]<b["o"] for b in l8)
    prev_up=prev["c"]>prev["o"]
    prev_down=prev["c"]<prev["o"]
    if up and not prev_up:
        if alert:
            body=(f"8连涨\n"
                  f"时间: {fmt(l8[0]['ot'])} → {fmt(l8[-1]['ot'])}\n"
                  f"开盘: {l8[0]['o']:.2f}\n收盘: {l8[-1]['c']:.2f}\n"
                  f"累计涨幅: {l8[-1]['c']-l8[0]['o']:+.2f}")
            send(f"{asset} 5m 8连提醒",body)
        return "8连涨"
    if down and not prev_down:
        if alert:
            body=(f"8连跌\n"
                  f"时间: {fmt(l8[0]['ot'])} → {fmt(l8[-1]['ot'])}\n"
                  f"开盘: {l8[0]['o']:.2f}\n收盘: {l8[-1]['c']:.2f}\n"
                  f"累计跌幅: {l8[-1]['c']-l8[0]['o']:+.2f}")
            send(f"{asset} 5m 8连提醒",body)
        return "8连跌"
    return None

def main():
    if "--once" in sys.argv:
        for a in ("BTC","ETH"):
            try: log(f"{a} 当前8连检查: "+(check(a,alert=False) or "无"))
            except Exception as e: log(f"{a} 检查失败 {str(e)[:100]}")
        return
    log("8连提醒监控启动 BTC+ETH")
    while True:
        nxt=(now_ms()//300000+1)*300000+2000
        time.sleep(max(1,(nxt-now_ms())/1000))
        for a in ("BTC","ETH"):
            try:
                r=check(a,alert=True)
                if r: log(f"{a} 已发送 {r}")
            except Exception as e:
                log(f"{a} 检查失败 {str(e)[:120]}")

if __name__=="__main__": main()
