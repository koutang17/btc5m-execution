#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BTC 5m 双账户实时看板：http://127.0.0.1:8600"""
import json, os, sys, subprocess, threading, time, base64, hmac, secrets, urllib.request
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from notify import send as notify_send
try:
    import botctl as _bc
except Exception:
    _bc=None

import config as _cfg

NODE=os.environ.get("NODE_EXE") or _cfg.NODE
BOOT=_cfg.BOOT
WORK=_cfg.HOME
PORT=int(os.environ.get("DASH_PORT") or _cfg.DASH.get("port",8600))
REFRESH=int(_cfg.DASH.get("refresh_sec",30))
SLOW_REFRESH=int(_cfg.DASH.get("slow_refresh_sec",60))
ACCOUNT_STAGGER=6
SLOW_CACHE={}
AUTH_USER=os.environ.get("DASH_USER") or _cfg.DASH.get("user","admin")
AUTH_PASS=_cfg.secret("DASH_PASS","")
AUTH_VIEW_PASS=_cfg.secret("DASH_VIEW_PASS","")
AUTH_TOKEN=_cfg.secret("DASH_TOKEN","")
SESSIONS={}
SESS_LOCK=threading.Lock()
LOGIN_FAILS={}
SESSION_COOKIE="dash_session"


def _acct_row(a):
    env={"BINANCE_BAW_DIR":a["wallet_profile"]}
    if a["baw_js"]:
        env["BAW_JS"]=a["baw_js"]
    base="" if a["id"]=="A" else "_"+a["id"]
    return {"id":a["id"],"label":a["name"],"js":a["baw_js"],"env":env,
            "state":os.path.join(_cfg.STATE_DIR,f"predict_live_state{base}.json"),
            "base":float(a["base_stake"] or 1.0)}


ACCOUNTS=[_acct_row(a) for a in _cfg.ACCOUNTS]
ACCOUNT_MODES_F=os.path.join(WORK,"account_modes.json")
def read_account_modes():
    try:
        d=json.load(open(ACCOUNT_MODES_F,encoding="utf-8"))
    except Exception:
        d={}
    out={}
    for a in ACCOUNTS:
        m=str(d.get(a["id"]) or "none").lower()
        out[a["id"]]=m if m in ("martingale","kelly","none") else "none"
    return out
def write_account_modes(d):
    json.dump(d,open(ACCOUNT_MODES_F,"w",encoding="utf-8"),ensure_ascii=False,indent=1)
def account_mode(acc):
    return read_account_modes().get(acc["id"],"none")
def mode_suffix(mode):
    return "_kelly" if mode=="kelly" else ""

LOGIN_NOTIFY_F=os.path.join(WORK,"login_notify.json")
def read_login_notify():
    try:
        d=json.load(open(LOGIN_NOTIFY_F,encoding="utf-8"))
    except Exception:
        d={}
    out={}
    for a in ACCOUNTS:
        out[a["id"]]=bool(d.get(a["id"], a["id"]=="A"))
    return out
def write_login_notify(d):
    json.dump(d,open(LOGIN_NOTIFY_F,"w",encoding="utf-8"),ensure_ascii=False,indent=1)

def read_login_health(acc):
    try:
        return json.load(open(os.path.join(WORK,f"account_health_{acc['id']}.json"),encoding="utf-8"))
    except Exception:
        return {"state":"UNKNOWN","ts":0}

def health_status(acc,default):
    h=read_login_health(acc); s=str(h.get("state") or "").upper()
    if s=="AUTH": return "AUTH"
    if s=="NETWORK": return "NETWORK"
    return default

CACHE={"ts":0,"accounts":[],"stale":False,"market":None,"market_eth":None}
LOCK=threading.Lock()
MARKET_CACHE={}

def market_snapshot(symbol="BTCUSDT"):
    now=int(time.time()*1000)
    _c=MARKET_CACHE.get(symbol) or {}
    if _c.get("data") and now-_c.get("ts",0)<8000:
        return _c["data"]
    try:
        url="https://data-api.binance.vision/api/v3/klines?symbol="+symbol+"&interval=5m&limit=8"
        with urllib.request.urlopen(url,timeout=5) as r:
            arr=json.loads(r.read().decode("utf-8"))
        bars=[{"ot":int(x[0]),"o":float(x[1]),"c":float(x[4]),"ct":int(x[6])} for x in arr]
        closed=[b for b in bars if b["ct"]<=now]
        if not closed:
            return _c.get("data")
        last=closed[-1]
        last=closed[-1]
        direction="UP" if last["c"]>last["o"] else ("DOWN" if last["c"]<last["o"] else None)
        streak=0
        if direction:
            for b in reversed(closed):
                d="UP" if b["c"]>b["o"] else ("DOWN" if b["c"]<b["o"] else None)
                if d==direction: streak+=1
                else: break
        nxt=(now//300000+1)*300000
        data={"price":last["c"],"last_ot":last["ot"],"streak":streak,"dir":direction,
              "next_ts":nxt,"ts":now,
              "signal_3":("DOWN" if direction=="UP" and streak>=3 else ("UP" if direction=="DOWN" and streak>=3 else None))}
        MARKET_CACHE[symbol]={"ts":now,"data":data}
        return data
    except Exception:
        return _c.get("data")

def login_page(error=""):
    try:
        t=open(os.path.join(WORK,"login.html"),encoding="utf-8").read()
    except Exception:
        t="<h1>Login</h1><form method=post action=/login><input name=password type=password><button>Login</button></form>"
    return t.replace("{{ERROR}}",error)

def login_allowed(ip):
    now=time.time()
    arr=[x for x in LOGIN_FAILS.get(ip,[]) if now-x<300]
    LOGIN_FAILS[ip]=arr
    return len(arr)<5

def add_login_fail(ip):
    LOGIN_FAILS.setdefault(ip,[]).append(time.time())

def baw(js,args,env=None,timeout=15):
    e=os.environ.copy()
    e["NODE_COMPILE_CACHE"]=os.path.join(WORK,"node_cache")
    if env: e.update(env)
    if _cfg.PROXY_URL: e["BTC5M_PROXY_URL"]=_cfg.PROXY_URL
    try:
        r=subprocess.run([NODE,"--import",BOOT,js]+args,capture_output=True,text=True,timeout=timeout,env=e,
                         creationflags=subprocess.CREATE_NO_WINDOW)
        t=(r.stdout or "").strip(); i=t.find("{")
        return json.loads(t[i:]) if i>=0 else None
    except Exception:
        return None

def read_state(path,base):
    try:
        with open(path,encoding="utf-8") as f: return json.load(f)
    except Exception:
        return {"step":0,"stake":base,"cum_pnl":0.0,"active":False,"direction":None,"pending":None}

def as_float(x):
    try: return float(x or 0)
    except Exception: return 0.0

def flag(acc,name):
    suffix="" if acc["id"]=="A" else "_"+acc["id"]
    return os.path.join(WORK,f"{name}{suffix}{mode_suffix(account_mode(acc))}")

def state_pending(acc):
    st=read_state(acc_state(acc),acc["base"])
    return bool(st.get("pending"))

MODE_CACHE={"ts":0,"data":None}
def detect_mode():
    now=time.time()
    if MODE_CACHE["data"] and now-MODE_CACHE["ts"]<15:
        return MODE_CACHE["data"]
    try:
        pl=_bc.procs() if _bc else []
    except Exception:
        pl=[]
    modes=sorted(set(x["mode"] for x in pl if x.get("mode") in ("kelly","martingale")))
    pids=[x.get("pid") for x in pl]
    if not modes:
        d={"mode":"none","pids":pids,"detail":"没有运行中的机器人"}
    elif len(modes)>1:
        d={"mode":"mixed","pids":pids,"detail":"两个模式同时在跑，必须停掉一个"}
    else:
        d={"mode":modes[0],"pids":pids,"detail":"PID "+",".join(str(p) for p in pids)}
    MODE_CACHE["ts"]=now; MODE_CACHE["data"]=d
    return d

BOT_PATH=os.path.join(WORK,"predict_live_bot.py")
SERVICE_NAMES={"A":"BTC5mBotA","B":"BTC5mBotB"}

def service_exists(name):
    try:
        return subprocess.run(["sc.exe","query",name],capture_output=True,text=True,timeout=10,
                              creationflags=subprocess.CREATE_NO_WINDOW).returncode==0
    except Exception:
        return False

def service_start(name):
    try:
        r=subprocess.run(["sc.exe","start",name],capture_output=True,text=True,timeout=20,
                         creationflags=subprocess.CREATE_NO_WINDOW)
        return r.returncode in (0,1056)
    except Exception:
        return False

def heartbeat_age(acc):
    try:
        d=json.load(open(os.path.join(WORK,f"heartbeat_{acc['id']}.json"),encoding="utf-8"))
        return (time.time()*1000-float(d.get("ts") or 0))/1000
    except Exception:
        return 99999

def stop_account_bot(acc):
    # 先按 heartbeat 里的 PID 停止，再兜底按 --account-id 查找
    try:
        hb=json.load(open(os.path.join(WORK,f"heartbeat_{acc['id']}.json"),encoding="utf-8"))
        pid=int(hb.get("pid") or 0)
        if pid:
            subprocess.run(["taskkill","/PID",str(pid),"/F"],capture_output=True,
                           creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    except Exception:
        pass
    try:
        cmd=("Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python' -and $_.CommandLine -match 'predict_live_bot' -and $_.CommandLine -match '--account-id %s' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" % acc["id"])
        subprocess.run(["powershell","-NoProfile","-Command",cmd],capture_output=True,
                       creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    except Exception:
        pass
    try:
        os.remove(os.path.join(WORK,f"heartbeat_{acc['id']}.json"))
    except Exception:
        pass

def start_bot(acc):
    mode=account_mode(acc)
    if mode=="none":
        return False,"账户策略已关闭"
    script="predict_live_bot_kelly.py" if mode=="kelly" else "predict_live_bot.py"
    stopped=os.path.exists(flag(acc,"STOP"))
    if not stopped and heartbeat_age(acc)<600:
        return False,"已在运行"
    for nm in ("STOP","PAUSE"):
        try:
            p=flag(acc,nm)
            if os.path.exists(p): os.remove(p)
        except Exception: pass
    env=os.environ.copy(); env.update(acc.get("env") or {})
    env["ACCOUNT_ID"]=acc["id"]; env["BASE_STAKE"]="1"
    flags=0
    if os.name=="nt":
        flags=getattr(subprocess,"CREATE_NO_WINDOW",0)|getattr(subprocess,"DETACHED_PROCESS",0)
    try:
        subprocess.Popen([sys.executable,"-u",os.path.join(WORK,script),"--account-id",acc["id"]],
                         cwd=WORK,env=env,creationflags=flags)
        return True,"已启动 "+mode
    except Exception as e:
        return False,str(e)[:120]

def set_account_strategy(acc,mode):
    cur=account_mode(acc)
    if mode not in ("none","martingale","kelly"):
        return False,"bad mode"
    if cur==mode:
        return True,"未变化"
    st=read_state(acc_state(acc),acc["base"])
    if st.get("active") or st.get("pending"):
        return False,"有持仓/挂单，不能切换"
    stop_account_bot(acc)
    time.sleep(1)
    modes=read_account_modes(); modes[acc["id"]]=mode; write_account_modes(modes)
    if mode=="none":
        command_log("account-mode %s=none"%acc["id"])
        return True,"已停止"
    ok,msg=start_bot(acc)
    command_log("account-mode %s=%s %s"%(acc["id"],mode,msg))
    return ok,msg

def notify_change(acc,status,detail):
    cfg=read_config(acc)
    try:
        notify_send(f"[#{cfg['number']} {cfg['name']}] 控制面板操作",
                    f"状态: {status}\n{detail}")
    except Exception:
        pass

def read_watchdog():
    try:
        return json.load(open(os.path.join(WORK,"watchdog_status.json"),encoding="utf-8"))
    except Exception:
        return {"ok":False,"time":"","checks":[],"issues":["守护程序尚无状态"]}

KELLY_START="2026-11-10 03:10:00"

def kelly_fill_stats():
    import csv
    orders=[];cycles=[];fills=[]
    for acct in ("A","B"):
        p=os.path.join(WORK,f"live_orders_{acct}.csv")
        if os.path.exists(p):
            try:
                for row in csv.DictReader(open(p,encoding="utf-8-sig")):
                    if (row.get("order_time") or "")<KELLY_START: continue
                    try: lim=float(row.get("limit_price") or 0)
                    except Exception: lim=0
                    if abs(lim-0.51)>1e-6: continue
                    orders.append(row)
            except Exception: pass
        p=os.path.join(WORK,f"live_cycles_{acct}.csv")
        if os.path.exists(p):
            try:
                for row in csv.DictReader(open(p,encoding="utf-8-sig")):
                    if (row.get("start_time") or "")<KELLY_START: continue
                    cycles.append(row)
            except Exception: pass
        p=os.path.join(WORK,f"live_events_{acct}.jsonl")
        if os.path.exists(p):
            try:
                for line in open(p,encoding="utf-8"):
                    try: d=json.loads(line)
                    except Exception: continue
                    if d.get("event")!="fill": continue
                    if (d.get("time") or "")<KELLY_START: continue
                    fills.append(d)
            except Exception: pass
    n_ord=len(orders); n_fill=len(fills)
    win=sum(1 for c in cycles if c.get("category")=="WIN")
    loss=sum(1 for c in cycles if c.get("category")=="LOSS")
    skip=sum(1 for c in cycles if c.get("category")=="SKIP")
    asks=[float(o["ask"]) for o in orders if o.get("ask") not in (None,"")]
    prices=[float(f["price"]) for f in fills if f.get("price") is not None]
    return {"orders":n_ord,"fills":n_fill,"win":win,"loss":loss,"skip":skip,
            "fill_rate":(round(100.0*n_fill/n_ord,1) if n_ord else None),
            "avg_ask":(round(sum(asks)/len(asks),4) if asks else None),
            "avg_fill":(round(sum(prices)/len(prices),4) if prices else None),
            "since":KELLY_START}

LEVELS_F=os.path.join(WORK,"kelly_levels.json")
FRACS_F=os.path.join(WORK,"kelly_fracs.json")

def read_levels():
    try: return json.load(open(LEVELS_F,encoding="utf-8"))
    except Exception: return {}

def write_levels(d):
    json.dump(d,open(LEVELS_F,"w",encoding="utf-8"),ensure_ascii=False,indent=1)

def read_fracs():
    try: return {str(k):float(v) for k,v in json.load(open(FRACS_F,encoding="utf-8")).items()}
    except Exception: return {}

def write_fracs(d):
    json.dump(d,open(FRACS_F,"w",encoding="utf-8"),ensure_ascii=False,indent=1)

TZ=timezone(timedelta(hours=8))
def p_update_meta():
    try:
        p=os.path.join(WORK,"kelly_p_table.json")
        d=json.load(open(p,encoding="utf-8"))
        raw=str(d.get("updated_at") or "")
        try:
            dt=datetime.fromisoformat(raw)
        except Exception:
            dt=datetime.fromtimestamp(os.path.getmtime(p),TZ)
        if dt.tzinfo is None: dt=dt.replace(tzinfo=TZ)
        dt=dt.astimezone(TZ)
        return {"updated_at":dt.strftime("%Y-%m-%d %H:%M:%S"),
                "fresh_today":dt.date()==datetime.now(TZ).date(),
                "age_hours":round((datetime.now(TZ)-dt).total_seconds()/3600,1)}
    except Exception as e:
        return {"updated_at":"","fresh_today":False,"age_hours":None,"error":str(e)[:80]}

def p_levels():
    try:
        d=json.load(open(os.path.join(WORK,"kelly_p_table.json"),encoding="utf-8"))
        lv=read_levels(); fr=read_fracs()
        return [{"L":int(k),"n":v.get("n"),"p":v.get("p"),"trade":bool(v.get("trade")),
                 "enabled":bool(lv.get(k,True)),"frac":float(fr.get(k,0.3)),
                 "n14":v.get("n14"),"p14":v.get("p14"),"n90":v.get("n90"),"p90":v.get("p90")}
                for k,v in sorted(d.get("levels",{}).items(),key=lambda x:int(x[0]))]
    except Exception:
        return []

def mode_counters():
    def rd(name):
        try: return json.load(open(os.path.join(WORK,name),encoding="utf-8"))
        except Exception: return {}
    mA=[rd("predict_live_state.json"),rd("predict_live_state_B.json")]
    mK=[rd("predict_live_state_kelly.json"),rd("predict_live_state_B_kelly.json")]
    mg={"cycles":sum(int(x.get("cycle_count") or 0) for x in mA),
        "wins":sum(int(x.get("win_cycles") or 0) for x in mA),
        "stops":sum(int(x.get("stop_cycles") or 0) for x in mA)}
    kl={"bets":sum(int(x.get("kelly_bets") or x.get("cycle_count") or 0) for x in mK),
        "wins":sum(int(x.get("kelly_wins") or 0) for x in mK),
        "losses":sum(int(x.get("kelly_losses") or 0) for x in mK)}
    kl["rate"]=round(100.0*kl["wins"]/kl["bets"],1) if kl["bets"] else 0.0
    return {"martingale":mg,"kelly":kl}

def mode_tag():
    try:
        return "_kelly" if detect_mode().get("mode")=="kelly" else ""
    except Exception:
        return ""

def acc_state(acc):
    t=mode_suffix(account_mode(acc))
    return acc["state"].replace(".json",t+".json") if t else acc["state"]

def config_path(acc):
    return os.path.join(WORK,f"account_config_{acc['id']}{mode_suffix(account_mode(acc))}.json")

def read_config(acc):
    default={"name":"账户"+acc["id"],"number":1 if acc["id"]=="A" else 2,"asset":"BTC","min_streak":3,"max_losses":5,"base_stake":1.0,"retry_count":1,
             "profit_boost_pct":50.0,"profit_boost_base":100.0,"profit_boost_add":1,"profit_boost_mode":"dynamic","price":0.4,"carry_price":0.45,
             "hard_stop_mode":"none","hard_stop_drawdown":0.0,"hard_stop_loss_cycles":0}
    try:
        d=json.load(open(config_path(acc),encoding="utf-8"))
        nm=str(d.get("name") or default["name"]).strip()[:20] or default["name"]
        return {
          "name":nm,
          "number":int(d.get("number") or default["number"]),
          "min_streak":max(2,min(8,int(d.get("min_streak",3)))),
          "max_losses":max(1,min(12,int(d.get("max_losses",5)))),
          "asset":("ETH" if str(d.get("asset") or "BTC").upper()=="ETH" else "BTC"),
          "base_stake":max(1.0,min(5.0,float(d.get("base_stake",1.0)))),
          "retry_count":max(0,min(10,int(d.get("retry_count",1)))),
          "profit_boost_pct":max(0.0,min(1000.0,float(d.get("profit_boost_pct",50.0)))),
          "profit_boost_base":max(0.0,float(d.get("profit_boost_base",100.0))),
          "profit_boost_add":max(0,min(3,int(d.get("profit_boost_add",1)))),
          "profit_boost_mode":("latched" if str(d.get("profit_boost_mode") or "dynamic").lower()=="latched" else "dynamic"),
          "price":round(float(d.get("price",0.4)),2),
          "carry_price":round(float(d.get("carry_price",0.45)),2),
          "hard_stop_mode":str(d.get("hard_stop_mode") or "none").lower() if str(d.get("hard_stop_mode") or "none").lower() in ("none","drawdown","loss_cycles") else "none",
          "hard_stop_drawdown":max(0.0,float(d.get("hard_stop_drawdown") or 0)),
          "sizing_mode":("kelly" if str(d.get("sizing_mode") or "martingale").lower()=="kelly" else "martingale"),
          "kelly_p":max(0.5,min(0.7,float(d.get("kelly_p",0.52)))),
          "kelly_frac":max(0.0,min(1.0,float(d.get("kelly_frac",0.25)))),
          "kelly_max_pct":max(0.001,min(0.2,float(d.get("kelly_max_pct",0.05)))),
          "kelly_min_stake":max(1.0,float(d.get("kelly_min_stake",1.0))),
          "hard_stop_loss_cycles":max(0,int(d.get("hard_stop_loss_cycles") or 0)),
        }
    except Exception:
        return default

def command_log(s):
    try:
        with open(os.path.join(WORK,"dashboard_commands.log"),"a",encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ")+s+"\n")
    except Exception: pass

def fetch(acc):
    env=acc["env"]
    bal=baw(acc["js"],["wallet","balance","--binanceChainId","56","--json"],env)
    if not bal or not bal.get("success"): return None
    pos=baw(acc["js"],["prediction","position","list","--limit","10","--json"],env)
    now=time.time()
    slow=SLOW_CACHE.get(acc["id"])
    if not slow or now-slow.get("ts",0)>=SLOW_REFRESH:
        claim=baw(acc["js"],["prediction","position","list","--tab","PENDING_CLAIM","--limit","10","--json"],env)
        orders=baw(acc["js"],["prediction","order","history","--limit","3","--json"],env)
        SLOW_CACHE[acc["id"]]={"ts":now,"claim":claim,"orders":orders}
    else:
        claim=slow.get("claim"); orders=slow.get("orders")
    usdt=bnb=0.0
    for x in bal.get("data") or []:
        if x.get("symbol")=="USDT": usdt=as_float(x.get("balance"))
        if x.get("symbol")=="BNB": bnb=as_float(x.get("balance"))
    d=(pos or {}).get("data") or {}
    sm=d.get("summary") or {}; cnt=d.get("counts") or {}
    positions=(d.get("positions") or []) + (((claim or {}).get("data") or {}).get("positions") or [])
    seen=set(); merged=[]
    for p in positions:
        key=p.get("positionId") or p.get("tokenId")
        if key in seen: continue
        seen.add(key); merged.append(p)
    olist=((orders or {}).get("data") or {}).get("orders") or []
    st=read_state(acc_state(acc),acc["base"])
    cfg=read_config(acc)
    return {
      "id":acc["id"],"mode":account_mode(acc),"login_notify":read_login_notify().get(acc["id"],False),
      "login_health":read_login_health(acc),
      "num":cfg.get("number") or (1 if acc["id"]=="A" else 2),"label":cfg.get("name") or acc["label"],"status":health_status(acc,"CONNECTED"),
      "usdt":usdt,"bnb":bnb,
      "positions":merged,
      "pending_claim":[p for p in merged if p.get("positionStatus")=="PENDING_CLAIM"],
      "today_pnl":as_float(sm.get("todayRealizedPnl")),
      "today_pct":as_float(sm.get("todayRealizedPnlPercent")),
      "position_value":as_float(sm.get("positionValue")),
      "claimable":as_float(sm.get("totalClaimableAmount")),
      "state":st,
      "config":cfg,
      "paused":os.path.exists(flag(acc,"PAUSE")),
      "stopped":os.path.exists(flag(acc,"STOP")),
      "last_order":olist[0] if olist else None,
      "error":None,
    }

REDEEM_LOCK=threading.Lock()

def _redeem_load(aid):
    try:
        d=json.load(open(os.path.join(WORK,f"redeem_state_{aid}.json"),encoding="utf-8"))
        return {str(k):float(v) for k,v in d.items()} if isinstance(d,dict) else {}
    except Exception:
        return {}

def _redeem_save(aid,d):
    try:
        json.dump(d,open(os.path.join(WORK,f"redeem_state_{aid}.json"),"w",encoding="utf-8"),ensure_ascii=False)
    except Exception: pass

def auto_redeem_loop():
    while True:
        try:
            phase=time.time()%300
            if phase<20 or phase>285:
                time.sleep(10); continue
            for acc in ACCOUNTS:
                r=baw(acc["js"],["prediction","position","list","--tab","PENDING_CLAIM","--limit","50","--json"],acc.get("env") or {})
                ps=((r or {}).get("data") or {}).get("positions") or []
                done=_redeem_load(acc["id"]); now=time.time()
                ids=[str(p.get("tokenId")) for p in ps if p.get("tokenId") and now-float(done.get(str(p.get("tokenId")),0))>600]
                if not ids: continue
                rr=baw(acc["js"],["prediction","trade","redeem","--tokenIds",",".join(ids),
                                  "--binanceChainId","56","--json"],acc.get("env") or {})
                if rr and rr.get("success"):
                    for x in ids: done[x]=time.time()
                    _redeem_save(acc["id"],done)
                    try: notify_send(f"[{acc['label']}] BTC 5m 自动赎回",f"已提交 {len(ids)} 笔待领取仓位赎回")
                    except Exception: pass
        except Exception:
            pass
        time.sleep(90)

def refresh_loop():
    global CACHE
    prev={a["id"]:None for a in ACCOUNTS}
    while True:
        phase=time.time()%300
        if phase<20 or phase>285:
            time.sleep(8); continue
        out=[]; anyerr=False
        for i,acc in enumerate(ACCOUNTS):
            try:
                d=fetch(acc)
            except Exception as e:
                d=None
            if i < len(ACCOUNTS)-1:
                time.sleep(ACCOUNT_STAGGER)
            if d:
                prev[acc["id"]]=d; out.append(d)
            else:
                anyerr=True
                old=prev.get(acc["id"])
                if old:
                    old=dict(old)
                    st=read_state(acc_state(acc),acc["base"])
                    old["state"]=st
                    old["mode"]=account_mode(acc)
                    old["login_notify"]=read_login_notify().get(acc["id"],False)
                    old["login_health"]=read_login_health(acc)
                    old["paused"]=os.path.exists(flag(acc,"PAUSE"))
                    old["stopped"]=os.path.exists(flag(acc,"STOP"))
                    try:
                        if st.get("bankroll") is not None:
                            old["usdt"]=float(st.get("bankroll") or 0)
                    except Exception:
                        pass
                    old["error"]="查询失败，显示上次数据/本地资金"; old["status"]=health_status(acc,"STALE")
                    out.append(old)
                else:
                    st=read_state(acc_state(acc),acc["base"])
                    _c=read_config(acc)
                    out.append({"id":acc["id"],"mode":account_mode(acc),"login_notify":read_login_notify().get(acc["id"],False),
                                "login_health":read_login_health(acc),
                                "num":_c.get("number"),"label":_c.get("name") or acc["label"],"status":"UNKNOWN","usdt":float(st.get("bankroll") or 0),"bnb":0,
                                "positions":[],"pending_claim":[],"today_pnl":0,"today_pct":0,
                                "position_value":0,"claimable":0,"state":st,"last_order":None,
                                "config":read_config(acc),
                                "paused":os.path.exists(flag(acc,"PAUSE")),
                                "stopped":os.path.exists(flag(acc,"STOP")),
                                "error":"查询失败，等待重试"})
        snap=market_snapshot()
        snap_eth=market_snapshot("ETHUSDT")
        with LOCK:
            CACHE={"ts":int(time.time()),"accounts":out,"stale":anyerr,"bot":detect_mode(),
                   "market":snap or CACHE.get("market"),"market_eth":snap_eth or CACHE.get("market_eth"),
                   "watchdog":read_watchdog()}
        time.sleep(REFRESH)

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def _send(self,code,body,ctype):
        self.send_response(code)
        self.send_header("Content-Type",ctype)
        self.send_header("Cache-Control","no-store, no-cache, must-revalidate")
        self.send_header("Vary","Cookie")
        self.end_headers()
        self.wfile.write(body)
    def _cookie(self,name):
        c=self.headers.get("Cookie","")
        for part in c.split(";"):
            k,_,v=part.strip().partition("=")
            if k==name: return v
        return ""
    def _session_role(self):
        tok=self._cookie(SESSION_COOKIE)
        with SESS_LOCK:
            return SESSIONS.get(tok) if tok else None
    def _new_session_cookie(self,role):
        tok=secrets.token_urlsafe(32)
        with SESS_LOCK: SESSIONS[tok]=role
        host=self.headers.get("Host","")
        secure=(self.headers.get("X-Forwarded-Proto","")=="https")
        c=f"{SESSION_COOKIE}={tok}; Path=/; HttpOnly; SameSite=Lax; Max-Age=86400"
        if secure: c+="; Secure"
        return c
    def _clear_session_cookie(self):
        tok=self._cookie(SESSION_COOKIE)
        with SESS_LOCK: SESSIONS.pop(tok,None)
        return f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
    def _auth_role(self):
        role=self._session_role()
        if role: return role
        if not AUTH_PASS: return None   # fail-closed: an unset password must never grant access
        h=self.headers.get("Authorization","")
        if not h.startswith("Basic "): return None
        try:
            u,p=base64.b64decode(h[6:]).decode("utf-8").split(":",1)
        except Exception:
            return None
        if hmac.compare_digest(u,AUTH_USER) and hmac.compare_digest(p,AUTH_PASS): return "master"
        return None
    def _serve_login(self,error="",code=200):
        self._send(code,login_page(error).encode("utf-8"),"text/html; charset=utf-8")
    def _redirect(self,loc):
        self.send_response(302)
        self.send_header("Location",loc)
        self.end_headers()
    def do_GET(self):
        parsed=urlparse(self.path)
        path=parsed.path.rstrip("/") or "/"
        if path=="/login":
            if self._session_role(): self._redirect("/")
            else: self._serve_login()
            return
        if path=="/logout":
            c=self._clear_session_cookie()
            self.send_response(302)
            self.send_header("Set-Cookie",c)
            self.send_header("Location","/login")
            self.end_headers()
            return
        if path=="/favicon.ico":
            self._send(204,b"","image/x-icon"); return
        role=self._auth_role()
        if not role:
            if path.startswith("/api/"):
                self._send(401,b'{"error":"auth required"}',"application/json; charset=utf-8")
            else:
                self._redirect("/login")
            return
        if path=="/api/status":
            with LOCK:
                payload=dict(CACHE)
            payload["role"]=role
            payload["bot"]=detect_mode()
            payload["counters"]=mode_counters()
            payload["levels"]=p_levels()
            payload["levels_meta"]=p_update_meta()
            payload["fill_stats"]=kelly_fill_stats()
            self._send(200,json.dumps(payload,ensure_ascii=False).encode("utf-8"),"application/json; charset=utf-8")
        elif path in ("/","/index.html"):
            try:
                html=open(os.path.join(WORK,"dashboard.html"),encoding="utf-8").read()
                badge=('<span id="roleBadge" class="badge" style="display:none">—</span>'
                       if role=="viewer" else '<span id="roleBadge" class="badge ok">主密码</span>')
                html=html.replace('<span id="roleBadge" class="badge">—</span>',badge)
                body=html.encode("utf-8")
            except Exception:
                body=b"dashboard.html missing"
            self._send(200,body,"text/html; charset=utf-8")
        elif path=="/favicon.ico":
            self._send(204,b"","image/x-icon")
        elif path.startswith("/api/"):
            self._send(404,b"not found","text/plain")
        else:
            self._redirect("/")
    def do_POST(self):
        parsed=urlparse(self.path)
        path=parsed.path
        if path=="/login":
            ip=self.client_address[0]
            if not login_allowed(ip):
                self._serve_login("尝试次数过多，请5分钟后再试",429); return
            try:
                n=int(self.headers.get("Content-Length","0") or 0)
                raw=self.rfile.read(min(n,4096)).decode("utf-8","ignore")
                form=parse_qs(raw)
                pw=(form.get("password") or [""])[0]
            except Exception:
                self._serve_login("请求格式错误",400); return
            role=None
            if AUTH_PASS and hmac.compare_digest(pw,AUTH_PASS):
                role="master"
            elif AUTH_VIEW_PASS and hmac.compare_digest(pw,AUTH_VIEW_PASS):
                role="viewer"
            if role:
                LOGIN_FAILS.pop(ip,None)
                c=self._new_session_cookie(role)
                self.send_response(302)
                self.send_header("Set-Cookie",c)
                self.send_header("Location","/")
                self.end_headers()
                return
            add_login_fail(ip)
            self._serve_login("密码错误",401); return
        if path not in ("/api/command","/api/config","/api/mode","/api/levels","/api/account_mode","/api/login_notify"):
            self._send(404,b"not found","text/plain"); return
        role=self._auth_role()
        if not role:
            self._send(401,b"auth required","text/plain"); return
        if role!="master":
            self._send(403,b"read only","text/plain"); return
        try:
            n=int(self.headers.get("Content-Length","0") or 0)
            raw=self.rfile.read(min(n,4096))
            data=json.loads(raw.decode("utf-8"))
        except Exception:
            self._send(400,b"bad json","text/plain"); return
        if path=="/api/mode":
            mode=str(data.get("mode") or "").lower()
            if mode not in ("kelly","martingale","none"):
                self._send(400,b'{"ok":false,"error":"bad mode"}',"application/json; charset=utf-8"); return
            if mode=="none":
                # 停止前必须有当前模式状态；有持仓/挂单则拒绝
                cm=detect_mode().get("mode")
                if cm in ("kelly","martingale"):
                    busy=[]
                    for acc in ACCOUNTS:
                        st=read_state(acc_state(acc),acc["base"])
                        if st.get("active") or st.get("pending"):
                            busy.append(acc["id"])
                    if busy:
                        msg="有持仓/挂单，先等结算: "+",".join(busy)
                        self._send(409,json.dumps({"ok":False,"error":msg},ensure_ascii=False).encode("utf-8"),"application/json; charset=utf-8")
                        return
                cmd=[sys.executable,"-u",os.path.join(WORK,"botctl.py"),"stop"]
            else:
                cmd=[sys.executable,"-u",os.path.join(WORK,"botctl.py"),"switch",mode]
            if data.get("wait"): cmd+=["--wait",str(int(data["wait"]))]
            if data.get("force"): cmd+=["--force"]
            try:
                r=subprocess.run(cmd,cwd=WORK,capture_output=True,text=True,timeout=180,
                                 creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
                out=(r.stdout or "")+(r.stderr or "")
                ok=(r.returncode==0) and ("拒绝" not in out) and ("失败" not in out)
            except Exception as e:
                out=str(e); ok=False
            try: command_log(("stop-mode" if mode=="none" else "switch-mode "+mode))
            except Exception: pass
            body=json.dumps({"ok":ok,"mode":mode,"output":out},ensure_ascii=False).encode("utf-8")
            self._send(200 if ok else 409,body,"application/json; charset=utf-8"); return
        if path=="/api/levels":
            try: lv=str(int(data.get("level") or 0))
            except Exception:
                self._send(400,b"bad level","text/plain"); return
            if not (2<=int(lv)<=10):
                self._send(400,b"bad level","text/plain"); return
            en=bool(data.get("enabled"))
            d=read_levels(); d[lv]=en; write_levels(d)
            fr=read_fracs()
            if data.get("frac_pct") is not None:
                try:
                    f=max(0.0,min(100.0,float(data.get("frac_pct"))))/100.0
                    fr[lv]=f; write_fracs(fr)
                except Exception:
                    self._send(400,b"bad frac","text/plain"); return
            try: command_log("level-toggle L%s=%s frac=%s"%(lv,en,fr.get(lv)))
            except Exception: pass
            body=json.dumps({"ok":True,"level":int(lv),"enabled":en,"frac":fr.get(lv)}).encode("utf-8")
            self._send(200,body,"application/json; charset=utf-8"); return
        if path=="/api/account_mode":
            aid=str(data.get("account") or "").upper()
            mode=str(data.get("mode") or "none").lower()
            acc=next((a for a in ACCOUNTS if a["id"]==aid),None)
            if not acc:
                self._send(400,b'{"ok":false,"error":"bad account"}',"application/json; charset=utf-8"); return
            ok,msg=set_account_strategy(acc,mode)
            body=json.dumps({"ok":ok,"account":aid,"mode":mode,"message":msg},ensure_ascii=False).encode("utf-8")
            self._send(200 if ok else 409,body,"application/json; charset=utf-8"); return
        if path=="/api/login_notify":
            aid=str(data.get("account") or "").upper()
            if aid not in ("A","B"):
                self._send(400,b'{"ok":false,"error":"bad account"}',"application/json; charset=utf-8"); return
            d=read_login_notify(); d[aid]=bool(data.get("enabled")); write_login_notify(d)
            try: command_log("login-notify %s=%s"%(aid,d[aid]))
            except Exception: pass
            body=json.dumps({"ok":True,"account":aid,"enabled":d[aid]},ensure_ascii=False).encode("utf-8")
            self._send(200,body,"application/json; charset=utf-8"); return
        if path=="/api/config":
            who=str(data.get("account") or "").upper()
            acc=next((a for a in ACCOUNTS if a["id"]==who),None)
            if not acc:
                self._send(400,b"bad account","text/plain"); return
            try:
                cur=read_config(acc)
                nm=str(data.get("name") or cur["name"]).strip()[:20]
                hmode=str(data.get("hard_stop_mode") or cur.get("hard_stop_mode") or "none").lower()
                if hmode not in ("none","drawdown","loss_cycles"): hmode="none"
                cfg={
                  "name":nm,
                  "number":int(cur.get("number") or (1 if acc["id"]=="A" else 2)),
                  "min_streak":int(data.get("min_streak")),
                  "max_losses":int(data.get("max_losses")),
                  "asset":("ETH" if str(data.get("asset") or "BTC").upper()=="ETH" else "BTC"),
                  "base_stake":round(float(data.get("base_stake", 1.0)),2),
                  "retry_count":int(data.get("retry_count", 1)),
                  "profit_boost_pct":round(float(data.get("profit_boost_pct") or 0),2),
                  "profit_boost_base":round(float(data.get("profit_boost_base") or 0),2),
                  "profit_boost_add":int(data.get("profit_boost_add") or 0),
                  "profit_boost_mode":str(data.get("profit_boost_mode") or "dynamic").lower(),
                  "price":round(float(data.get("price")),2),
                  "carry_price":round(float(data.get("carry_price")),2),
                  "hard_stop_mode":hmode,
                  "hard_stop_drawdown":round(float(data.get("hard_stop_drawdown") or 0),2),
                  "hard_stop_loss_cycles":int(data.get("hard_stop_loss_cycles") or 0),
                  "sizing_mode":("kelly" if str(cur.get("sizing_mode") or "martingale")=="kelly" else "martingale"),
                  "kelly_p":round(float(data.get("kelly_p_pct") or (cur.get("kelly_p",0.52)*100))/100,4),
                  "kelly_frac":round(float(data.get("kelly_frac") or cur.get("kelly_frac",0.25)),3),
                  "kelly_max_pct":round(float(data.get("kelly_max_pct_pct") or (cur.get("kelly_max_pct",0.05)*100))/100,4),
  "kelly_min_edge":round(float(data.get("kelly_min_edge_pct") or (cur.get("kelly_min_edge",0.006)*100))/100,4),
                  "kelly_min_stake":round(float(data.get("kelly_min_stake") or cur.get("kelly_min_stake",1.0)),2),
                }
            except Exception:
                self._send(400,b"bad values","text/plain"); return
            if not cfg["name"]:
                self._send(400,b"bad name","text/plain"); return
            if not (2<=cfg["min_streak"]<=8 and 1<=cfg["max_losses"]<=12 and cfg["asset"] in ("BTC","ETH") and 1<=cfg["base_stake"]<=5 and 0<=cfg["retry_count"]<=10
                       and 0<=cfg["profit_boost_pct"]<=1000 and 0<=cfg["profit_boost_base"]<=1000000000
                       and 0<=cfg["profit_boost_add"]<=3 and cfg["profit_boost_mode"] in ("dynamic","latched")
                       and 0.1<=cfg["price"]<=0.99 and 0.1<=cfg["carry_price"]<=0.99 and 0<=cfg["hard_stop_drawdown"]<=100000 and 0<=cfg["hard_stop_loss_cycles"]<=100):
                self._send(400,b"out of range","text/plain"); return
            try:
                with open(config_path(acc),"w",encoding="utf-8") as f:
                    json.dump(cfg,f,ensure_ascii=False,indent=2)
            except Exception:
                self._send(500,b"write failed","text/plain"); return
            with LOCK:
                for row in CACHE.get("accounts",[]):
                    if row.get("id")==acc["id"]:
                        row["config"]=cfg
                        row["label"]=cfg["name"]
            command_log("config "+who+" "+json.dumps(cfg,ensure_ascii=False))
            body=json.dumps({"ok":True,"account":who,"config":cfg},ensure_ascii=False).encode("utf-8")
            self._send(200,body,"application/json; charset=utf-8")
            return
        if str(data.get("action") or "").lower()=="close":
            who=str(data.get("account") or "").upper()
            acc=next((a for a in ACCOUNTS if a["id"]==who),None)
            if not acc:
                self._send(400,b"bad account","text/plain"); return
            pos=baw(acc["js"],["prediction","position","list","--limit","20","--json"],acc["env"])
            plist=((pos or {}).get("data") or {}).get("positions") or []
            closed=[]
            for p in plist:
                token=p.get("tokenId"); topic=p.get("marketTopicId")
                shares=as_float(p.get("shares"))
                status=str(p.get("positionStatus") or "").upper()
                if not token or shares<=0: continue
                if p.get("canClaim") or status=="PENDING_CLAIM":
                    rr=baw(acc["js"],["prediction","trade","redeem","--tokenIds",str(token),
                                      "--binanceChainId","56","--json"],acc["env"])
                    if rr and rr.get("success"):
                        closed.append({"tokenId":token,"mode":"redeem","shares":shares})
                    continue
                if status not in ("ONGOING","OPEN",""): continue
                q=baw(acc["js"],["prediction","trade","quote","--binanceChainId","56",
                                  "--tokenId",str(token),"--marketTopicId",str(topic),
                                  "--side","SELL","--amount",str(shares),
                                  "--orderType","MARKET","--json"],acc["env"])
                if not q or not q.get("success"): continue
                qd=q.get("data") or {}; qid=qd.get("quoteId")
                if not qid: continue
                po=baw(acc["js"],["prediction","trade","place-order","--quoteId",str(qid),
                                  "--orderType","MARKET","--slippageBps",
                                  str(qd.get("slippageBps",1000)),"--json"],acc["env"])
                if po and po.get("success"):
                    closed.append({"tokenId":token,"mode":"sell","shares":shares,
                                   "orderId":(po.get("data") or {}).get("orderId")})
            if not closed:
                body=json.dumps({"ok":False,"error":"没有可平仓持仓或平仓失败"},ensure_ascii=False).encode("utf-8")
                self._send(400,body,"application/json; charset=utf-8"); return
            command_log(f"close {who} {len(closed)}")
            notify_change(acc,"已平仓",f"已处理 {len(closed)} 个持仓")
            body=json.dumps({"ok":True,"action":"close","account":who,"closed":closed},ensure_ascii=False).encode("utf-8")
            self._send(200,body,"application/json; charset=utf-8"); return
        if str(data.get("action") or "").lower()=="watchdog":
            try:
                subprocess.run([sys.executable,"-u",os.path.join(WORK,"watchdog.py")],cwd=WORK,timeout=60,
                               stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                               creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            except Exception: pass
            body=json.dumps({"ok":True,"action":"watchdog","watchdog":read_watchdog()},ensure_ascii=False).encode("utf-8")
            self._send(200,body,"application/json; charset=utf-8"); return
        if str(data.get("action") or "").lower()=="cancel":
            who=str(data.get("account") or "").upper()
            acc=next((a for a in ACCOUNTS if a["id"]==who),None)
            oid=str(data.get("orderId") or "").strip()
            scope=str(data.get("scope") or "order").lower()
            if scope not in ("order","cycle"): scope="order"
            if not acc or not oid:
                self._send(400,b"bad cancel","text/plain"); return
            r=baw(acc["js"],["prediction","trade","cancel","--orderIds",oid,"--json"],acc["env"])
            if not r or not r.get("success"):
                body=json.dumps({"ok":False,"error":"撤单失败"},ensure_ascii=False).encode("utf-8")
                self._send(400,body,"application/json; charset=utf-8"); return
            if scope=="cycle":
                try:
                    open(flag(acc,"CYCLE_STOP"),"a").close()
                except Exception:
                    pass
                command_log(f"cancel-cycle {who} {oid}")
                notify_change(acc,"已终止本周期",f"订单 {oid} 已撤销\n本周期结束，等待新信号")
            else:
                command_log(f"cancel-order {who} {oid}")
                notify_change(acc,"已终止本次下单",f"订单 {oid} 已撤销\n本周期保留，下一轮按规则重试")
            body=json.dumps({"ok":True,"action":"cancel","account":who,"orderId":oid,"scope":scope},ensure_ascii=False).encode("utf-8")
            self._send(200,body,"application/json; charset=utf-8"); return
        action=str(data.get("action") or "").lower()
        who=str(data.get("account") or "all").upper()
        if action not in ("pause","resume","stop","start") or who not in ("A","B","ALL"):
            self._send(400,b"bad action","text/plain"); return
        targets=ACCOUNTS if who=="ALL" else [a for a in ACCOUNTS if a["id"]==who]
        if not targets:
            self._send(400,b"bad account","text/plain"); return
        changed=[]
        if action=="stop":
            busy=[a["id"] for a in targets if state_pending(a)]
            if busy:
                msg="有持仓/挂单，先暂停等结算: "+",".join(busy)
                self._send(409,json.dumps({"ok":False,"error":msg},ensure_ascii=False).encode("utf-8"),"application/json; charset=utf-8")
                return
            for a in targets:
                fp=flag(a,"STOP")
                if not os.path.exists(fp):
                    try:
                        open(fp,"a").close()
                        changed.append((a,"已停止","机器人进程退出，已有持仓会先结算"))
                    except Exception: pass
        elif action=="pause":
            for a in targets:
                fp=flag(a,"PAUSE")
                if not os.path.exists(fp):
                    try:
                        open(fp,"a").close()
                        changed.append((a,"已暂停","暂停开新单，已有持仓继续处理"))
                    except Exception: pass
        elif action=="start":
            for a in targets:
                started,msg=start_bot(a)
                command_log(f"start {a['id']}: {msg}")
                if started:
                    changed.append((a,"已启动","机器人进程已重新拉起"))
        elif action=="resume":
            for a in targets:
                try:
                    if os.path.exists(flag(a,"STOP")):
                        started,msg=start_bot(a)
                        command_log(f"resume-start {a['id']}: {msg}")
                        if started: changed.append((a,"已启动","机器人进程已重新拉起"))
                    else:
                        fp=flag(a,"PAUSE")
                        if os.path.exists(fp):
                            os.remove(fp)
                            changed.append((a,"已继续","恢复开新单"))
                except Exception: pass
        for a,status,detail in changed:
            notify_change(a,status,detail)
        command_log(f"{action} {who}")
        # 立即刷新缓存中的状态，避免等15秒
        with LOCK:
            for row in CACHE.get("accounts",[]):
                acct=next((a for a in ACCOUNTS if a["id"]==row.get("id")),None)
                if acct:
                    row["paused"]=os.path.exists(flag(acct,"PAUSE"))
                    row["stopped"]=os.path.exists(flag(acct,"STOP"))
        body=json.dumps({"ok":True,"action":action,"account":who,"changed":[a["id"] for a,_,_ in changed]},ensure_ascii=False).encode("utf-8")
        self._send(200,body,"application/json; charset=utf-8")

if __name__=="__main__":
    if not AUTH_PASS:
        print("[dashboard] DASH_PASS is empty; refusing to start (set it in .env).",flush=True)
        raise SystemExit(2)
    threading.Thread(target=refresh_loop,daemon=True).start()
    threading.Thread(target=auto_redeem_loop,daemon=True).start()
    bind=_cfg.DASH.get("bind","127.0.0.1")
    print(f"dashboard http://{bind}:{PORT}",flush=True)
    ThreadingHTTPServer((bind,PORT),Handler).serve_forever()
