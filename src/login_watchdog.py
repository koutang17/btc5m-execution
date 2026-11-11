#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""登录守护：A/B 任一失效时，每5分钟自动生成并发送登录链接到企业微信"""
import json, os, subprocess, time, sys
from datetime import datetime, timezone, timedelta
import config as _cfg

SRC=os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0,SRC)
from notify import send

NODE=os.environ.get("NODE_EXE") or _cfg.NODE
BOOT=_cfg.BOOT
TZ=_cfg.TZ
CHECK=120
ALERT=300
LOG_F=os.path.join(_cfg.LOG_DIR,"login_watchdog.log")
SKIP_B_F=os.path.join(_cfg.STATE_DIR,"SKIP_B_LOGIN")
NOTIFY_F=os.path.join(_cfg.STATE_DIR,"login_notify.json")
ACCOUNTS=[{"id":a["id"],"js":a["baw_js"],
           "env":{"BINANCE_BAW_DIR":a["wallet_profile"]}} for a in _cfg.ACCOUNTS]

def baw(acc,args,timeout=60):
    e=os.environ.copy(); e.update(acc["env"])
    e["NODE_COMPILE_CACHE"]=os.path.join(_cfg.HOME,"node_cache")
    if _cfg.PROXY_URL: e["BTC5M_PROXY_URL"]=_cfg.PROXY_URL
    try:
        r=subprocess.run([NODE,"--import",BOOT,acc["js"]]+args,capture_output=True,text=True,timeout=timeout,env=e,
                         creationflags=subprocess.CREATE_NO_WINDOW)
        t=(r.stdout or "").strip(); i=t.find("{")
        return json.loads(t[i:]) if i>=0 else None
    except Exception:
        return None

def auth_state(acc):
    # 先用 wallet status 判断会话状态；不能用余额失败直接判定登录失效
    r=baw(acc,["wallet","status","--json"])
    if r and r.get("success"):
        s=str(((r.get("data") or {}).get("status") or "")).upper()
        if s=="CONNECTED": return "OK",r
        if s in ("UNCONNECTED","EXPIRED","LOGGED_OUT"): return "AUTH",r
    err=(r or {}).get("error") or {}
    code=str(err.get("code") or "")
    name=str(err.get("name") or "").upper()
    msg=str(err.get("message") or "").upper()
    if r is None or code=="50001001" or "NETWORK" in name or "TIMEOUT" in msg:
        return "NETWORK",r
    if code in ("10003000","401","403") or any(x in (name+" "+msg) for x in ("NOT_LOGGED_IN","UNCONNECTED","AUTH","UNAUTH","TOKEN","LOGIN","SESSION","EXPIRED")):
        return "AUTH",r
    return "UNKNOWN",r

def signin(acc):
    return baw(acc,["auth","signin","--json"])

def fmt_expire(ts):
    try: return datetime.fromtimestamp(int(ts)/1000,TZ).strftime("%H:%M")
    except Exception: return "?"

def read_notify():
    try:
        return json.load(open(NOTIFY_F,encoding="utf-8"))
    except Exception:
        return {}

def notify_enabled(aid):
    d=read_notify()
    if aid in d: return bool(d.get(aid))
    if aid=="B" and os.path.exists(SKIP_B_F): return False
    return aid=="A"

def write_health(aid,state,err=""):
    try:
        json.dump({"ts":time.time(),"state":state,"error":str(err or "")[:200]},
open(os.path.join(_cfg.STATE_DIR,f"account_health_{aid}.json"),"w",encoding="utf-8"),
                  ensure_ascii=False)
    except Exception: pass

def main():
    last={a["id"]:0 for a in ACCOUNTS}
    okstate={a["id"]:None for a in ACCOUNTS}
    net_fail={a["id"]:0 for a in ACCOUNTS}
    print("login watchdog started",flush=True)
    while True:
        phase=time.time()%300
        if phase<20 or phase>285:
            time.sleep(10); continue
        now=time.time()
        for acc in ACCOUNTS:
            enabled=notify_enabled(acc["id"])
            state,r=auth_state(acc)
            if state=="OK":
                write_health(acc["id"],"OK")
                net_fail[acc["id"]]=0
                okstate[acc["id"]]=True
                last[acc["id"]]=0
                continue
            if state=="NETWORK":
                msg=((r or {}).get("error") or {}).get("message","")[:100]
                write_health(acc["id"],"NETWORK",msg)
                net_fail[acc["id"]]=net_fail.get(acc["id"],0)+1
                if net_fail[acc["id"]]>=2 and okstate[acc["id"]]!="NETWORK":
                    log(f"network failed {acc['id']}: {msg} (x{net_fail[acc['id']]})")
                    if enabled:
                        try:
                            send(f"[账户{acc['id']}] 网络异常",
                                 f"账户{acc['id']}的BAW网络连接失败，暂时无法查询账户/下单。\n{msg}\n登录状态未判定为失效。")
                        except Exception:
                            pass
                okstate[acc["id"]]="NETWORK"
                continue
            if state=="UNKNOWN":
                write_health(acc["id"],"UNKNOWN","unknown auth state")
                log(f"check unknown {acc['id']}")
                okstate[acc["id"]]="UNKNOWN"
                continue
            write_health(acc["id"],"AUTH",((r or {}).get("error") or {}).get("message","")[:100])
            net_fail[acc["id"]]=0
            okstate[acc["id"]]=False
            if not enabled:
                log(f"login expired {acc['id']} (notify disabled)")
                continue
            if now-last[acc["id"]] < ALERT:
                continue
            last[acc["id"]]=now
            r=signin(acc)
            if r and r.get("success"):
                d=r.get("data") or {}
                url=d.get("urlForWeb") or ""
                code=d.get("pairingCode") or ""
                exp=fmt_expire(d.get("expireAt"))
                body=(f"账户{acc['id']}登录已失效。\n\n"
                      f"登录链接:\n{url}\n\n"
                      f"配对码: {code}\n"
                      f"有效期至: {exp}\n\n"
                      f"每5分钟自动重发新链接；登录成功后自动停止。")
                send(f"[账户{acc['id']}] 请重新登录",body)
                log(f"sent {acc['id']} {code}")
                qid=d.get("qrCodeId")
                if qid:
                    # QR有效期约5分钟；必须覆盖整个有效期，否则用户后段扫码不会完成验证
                    v=baw(acc,["auth","verify","--qrCodeId",qid,"--json"],timeout=360)
                    if v and v.get("success"):
                        write_health(acc["id"],"OK")
                        okstate[acc["id"]]=True
                        last[acc["id"]]=0
                        log(f"verified {acc['id']}")
            else:
                send(f"[账户{acc['id']}] 登录失效",f"账户{acc['id']}登录失效，且生成登录链接失败，请检查网络。")
                log(f"signin failed {acc['id']}")
        time.sleep(CHECK)

if __name__=="__main__": main()
