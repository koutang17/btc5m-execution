#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
币安 Predict 5m — 分数凯利 自动下单机器人
================================================
- 数据: data-api.binance.vision (国内直连)
- 下单: baw CLI (经 proxy-bootstrap)
- 信号: 连续 N 根阳/阴 -> 反向 (默认 8 连)
- 仓位: 分数凯利 stake = 余额 × (p-a)/(1-a) × 分数, 1U 起, 单注上限 5%
- 限价: 0.51u/份; 卖价<0.1不下单
- 挂单未成交: 每个档位最多挂两次，两次都未成交结束周期
- 成交: 等轮次结束, 用 order.realizedPnl 结算
- 通知: notify.py (企业微信), 标题/正文带「凯利」标识
- 配置: account_config_{A|B}_kelly.json; 状态: predict_live_state[_B]_kelly.json
"""
import json, os, sys, time, subprocess, urllib.request, threading
import concurrent.futures
from datetime import datetime, timezone, timedelta
from notify import send as _notify_send
import trade_recorder as rec

import config as _cfg

NODE=os.environ.get("NODE_EXE") or _cfg.NODE
ACCOUNT_ID=os.environ.get("ACCOUNT_ID","A")
for _i,_a in enumerate(sys.argv):
    if _a=="--account-id" and _i+1<len(sys.argv):
        ACCOUNT_ID=sys.argv[_i+1]
_ACCT=_cfg.account(ACCOUNT_ID)
BAW=os.environ.get("BAW_JS") or _ACCT["baw_js"] or _cfg.BAW_JS
BOOT=_cfg.BOOT
WORKDIR=_cfg.HOME
ACCOUNT_NAME=_ACCT["name"]
ACCOUNT_NUM=_ACCT["number"]
SUFFIX="" if ACCOUNT_ID=="A" else "_"+ACCOUNT_ID
RUN_TAG=os.environ.get("RUN_TAG","kelly")
SUFFIX=f"{SUFFIX}_{RUN_TAG}"
STATE_F=os.path.join(_cfg.STATE_DIR,f"predict_live_state{SUFFIX}.json")
HEARTBEAT_F=os.path.join(_cfg.STATE_DIR,f"heartbeat_{ACCOUNT_ID}.json")
CONFIG_F=os.path.join(_cfg.STATE_DIR,f"account_config_{ACCOUNT_ID}_{RUN_TAG}.json")
LOG_F=os.path.join(_cfg.LOG_DIR,f"predict_live_log{SUFFIX}.csv")
STOP_F=os.path.join(_cfg.STATE_DIR,f"STOP{SUFFIX}")
PAUSE_F=os.path.join(_cfg.STATE_DIR,f"PAUSE{SUFFIX}")
CYCLE_STOP_F=os.path.join(_cfg.STATE_DIR,f"CYCLE_STOP{SUFFIX}")
REDEEM_F=os.path.join(_cfg.STATE_DIR,f"redeem_state{SUFFIX}.json")
REDEEM_LOCK=threading.Lock()
REDEEM_DONE={}
TZ=_cfg.TZ
CFG=dict(_cfg.CFG)
CFG["asset"]=_ACCT["asset"]
CFG["symbol"]=CFG["asset"]+_cfg.EXCHANGE["symbol_suffix"]
DRY_RUN=_cfg.DRY_RUN
BASE_STAKE=float(os.environ.get("BASE_STAKE") or _ACCT["base_stake"])   # 仅作最小注位兜底，不再生成加倍档位

def next_stake(st):
    """凯利定注: 余额 x (p-a)/(1-a) x 当前档位分数, 夹在 [最小注, 余额 x 单注上限]"""
    p=float(st.get("kelly_p") or CFG["kelly_p"])
    a=float(st.get("limit") or CFG["price"]) or 0.51
    bank=float(st.get("bankroll") or 0) or 100.0
    L=int(st.get("level") or CFG["min_streak"])
    f=max(0.0,(p-a)/(1-a))*pick_frac(L)
    stake=bank*f
    stake=max(float(CFG["kelly_min_stake"]),min(stake,bank*float(CFG["kelly_max_pct"])))
    return round(stake,2)

def load_cfg():
    global ACCOUNT_NAME, ACCOUNT_NUM
    try:
        d=json.load(open(CONFIG_F,encoding="utf-8"))
    except Exception:
        return
    try:
        ACCOUNT_NUM=int(d.get("number") or ACCOUNT_NUM)
    except Exception:
        pass
    try:
        nm=str(d.get("name") or ACCOUNT_NAME).strip()
        if 1<=len(nm)<=20: ACCOUNT_NAME=nm
    except Exception:
        pass
    try:
        CFG["min_streak"]=max(2,min(8,int(d.get("min_streak",CFG["min_streak"]))))
        CFG["max_losses"]=max(1,min(12,int(d.get("max_losses",CFG["max_losses"]))))
        CFG["base_stake"]=max(1.0,min(5.0,float(d.get("base_stake",CFG["base_stake"]))))
        a=str(d.get("asset") or CFG["asset"]).upper()
        CFG["asset"]=a if a in ("BTC","ETH") else "BTC"
        CFG["symbol"]=CFG["asset"]+"USDT"
        CFG["retry_count"]=max(0,min(10,int(d.get("retry_count",CFG["retry_count"]))))
        CFG["profit_boost_pct"]=max(0.0,min(1000.0,float(d.get("profit_boost_pct",CFG["profit_boost_pct"]))))
        CFG["profit_boost_base"]=max(0.0,float(d.get("profit_boost_base",CFG["profit_boost_base"])))
        CFG["profit_boost_add"]=max(0,min(3,int(d.get("profit_boost_add",CFG["profit_boost_add"]))))
        m=str(d.get("profit_boost_mode") or "dynamic").lower()
        CFG["profit_boost_mode"]=m if m in ("dynamic","latched") else "dynamic"
        CFG["price"]=round(float(d.get("price",CFG["price"])),2)
        CFG["carry_price"]=round(float(d.get("carry_price",CFG["carry_price"])),2)
        m=str(d.get("sizing_mode") or CFG["sizing_mode"]).lower()
        CFG["sizing_mode"]=m if m in ("kelly","martingale") else "kelly"
        CFG["kelly_p"]=max(0.5,min(0.7,float(d.get("kelly_p",CFG["kelly_p"]))))
        CFG["kelly_frac"]=max(0.0,min(1.0,float(d.get("kelly_frac",CFG["kelly_frac"]))))
        CFG["kelly_max_pct"]=max(0.001,min(0.2,float(d.get("kelly_max_pct",CFG["kelly_max_pct"]))))
        CFG["kelly_min_stake"]=max(1.0,float(d.get("kelly_min_stake",CFG["kelly_min_stake"])))
        CFG["kelly_min_edge"]=max(0.0,min(0.1,float(d.get("kelly_min_edge",CFG.get("kelly_min_edge",0.005)))))
        mode=str(d.get("hard_stop_mode") or "none").lower()
        CFG["hard_stop_mode"]=mode if mode in ("none","drawdown","loss_cycles") else "none"
        CFG["hard_stop_drawdown"]=max(0.0,float(d.get("hard_stop_drawdown") or 0))
        CFG["hard_stop_loss_cycles"]=max(0,int(d.get("hard_stop_loss_cycles") or 0))
    except Exception:
        return
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

def notify_send(title, body):
    title=title.replace("BTC 5m", f"{CFG['asset']} 5m")
    full=f"[#{ACCOUNT_NUM} {ACCOUNT_NAME}] {title}"
    ok=False
    try:
        ok=bool(_notify_send(full, body))
    except Exception as e:
        ok=False
        log("通知异常 "+str(e)[:120])
    try:
        with open(os.path.join(WORKDIR,f"notify_log{SUFFIX}.jsonl"),"a",encoding="utf-8") as f:
            f.write(json.dumps({"ts":now_ms(),"title":full,"ok":ok,"len":len(body)},ensure_ascii=False)+"\n")
    except Exception:
        pass
    return ok

# ── 凯利专用：文案与统计（本文件不含马丁逻辑）──────────────────────
def mode_name():
    return "凯利"

def cycle_label(cyc, n):
    return f"本次 1 单 · 累计第{cyc}单"

def trade_stats(st):
    b=int(st.get("kelly_bets") or 0); w=int(st.get("kelly_wins") or 0); l=int(st.get("kelly_losses") or 0)
    r=(100.0*w/b) if b else 0.0
    return f"累计: {b} 单 · 胜 {w} · 负 {l} · 胜率 {r:.1f}%"

def sizing_note(st):
    try:
        p=float(st.get("kelly_p") or CFG["kelly_p"])
        a=float(st.get("limit") or CFG["price"]) or 0.51
        f=max(0.0,(p-a)/(1-a))*float(CFG["kelly_frac"]) if a<1 else 0.0
        bank=float(st.get("bankroll") or 0)
        L=int(st.get("level") or CFG["min_streak"])
        return f"凯利: L{L} · p={p*100:.1f}% · {pick_frac(L)*100:.1f}%分数 · 余额{bank:.2f}u · 不加倍"
    except Exception:
        return "凯利: 分数仓位"

def beat(st,note=""):
    try:
        json.dump({"ts":now_ms(),"account":ACCOUNT_ID,"pid":os.getpid(),"note":note,"state":st},
                  open(HEARTBEAT_F,"w",encoding="utf-8"),ensure_ascii=False)
    except Exception: pass

def now_ms(): return int(time.time()*1000)
def fmt(ms): return datetime.fromtimestamp(ms/1000,TZ).strftime("%Y-%m-%d %H:%M:%S")
def log(s): print("["+datetime.now(TZ).strftime("%H:%M:%S")+"] "+s,flush=True)

def baw(*args, timeout=30):
    try:
        env=os.environ.copy()
        env["NODE_COMPILE_CACHE"]=os.path.join(WORKDIR,"node_cache")
        env["BINANCE_BAW_DIR"]=_ACCT["wallet_profile"]
        if _cfg.PROXY_URL: env["BTC5M_PROXY_URL"]=_cfg.PROXY_URL
        r=subprocess.run([NODE,"--import",BOOT,BAW]+[str(a) for a in args],
                         capture_output=True,text=True,timeout=timeout,env=env,
                         creationflags=subprocess.CREATE_NO_WINDOW)
        t=(r.stdout or "").strip(); i=t.find("{")
        return json.loads(t[i:]) if i>=0 else None
    except Exception as e:
        log("baw异常 "+str(e)[:120]); return None

def klines(limit=12):
    last=None
    for base in ["https://data-api.binance.vision","https://api.binance.com"]:
        url=base+"/api/v3/klines?symbol="+CFG["symbol"]+"&interval=5m&limit="+str(limit)
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
            d=json.loads(urllib.request.urlopen(req,timeout=20).read())
            return [{"ot":int(k[0]),"o":float(k[1]),"c":float(k[4]),"ct":int(k[6])} for k in d]
        except Exception as e:
            last=e
            try:
                r=subprocess.run(["curl.exe","-sS","--noproxy","*","--max-time","15",url],capture_output=True,text=True,timeout=25,creationflags=subprocess.CREATE_NO_WINDOW)
                if r.returncode==0 and r.stdout.strip():
                    d=json.loads(r.stdout)
                    return [{"ot":int(k[0]),"o":float(k[1]),"c":float(k[4]),"ct":int(k[6])} for k in d]
            except Exception as e2:
                last=e2
    log("K线失败 "+str(last)[:80]); return None

def streak(closed):
    k=int(CFG.get("effective_min_streak",CFG["min_streak"]))
    if len(closed)<k: return None
    lk=closed[-k:]
    if all(b["c"]>b["o"] for b in lk): return "DOWN"
    if all(b["c"]<b["o"] for b in lk): return "UP"
    return None

def streak_run(closed):
    """返回 (方向, 当前连段长度)；长度按严格同向计算，平局打断"""
    k=int(CFG.get("min_streak") or 4)
    if len(closed)<k: return None,0
    def sgn(b):
        return 1 if b["c"]>b["o"] else (-1 if b["c"]<b["o"] else 0)
    d=sgn(closed[-1])
    if d==0: return None,0
    L=0
    for b in reversed(closed):
        if sgn(b)!=d: break
        L+=1
    if L<k: return None,0
    return ("DOWN" if d>0 else "UP"),L

P_TABLE={}
P_TABLE_MTIME=0.0
def load_p_table():
    global P_TABLE,P_TABLE_MTIME
    try:
        p=os.path.join(WORKDIR,"kelly_p_table.json")
        m=os.path.getmtime(p)
        if m!=P_TABLE_MTIME:
            P_TABLE=json.load(open(p,encoding="utf-8")).get("levels") or {}
            P_TABLE_MTIME=m
            log("分档表加载: "+", ".join("L%s=%.1f%%"%(k,v["p"]*100) for k,v in sorted(P_TABLE.items(),key=lambda x:int(x[0]))))
    except Exception as e:
        log("分档表加载失败: "+str(e)[:80])

def pick_p(L):
    v=P_TABLE.get(str(min(int(L),10)))
    if v: return float(v["p"])
    return float(CFG["kelly_p"])

LEVELS_ENABLED={}
LEVELS_MTIME=0.0
def load_levels():
    global LEVELS_ENABLED,LEVELS_MTIME
    try:
        p=os.path.join(WORKDIR,"kelly_levels.json")
        m=os.path.getmtime(p)
        if m!=LEVELS_MTIME:
            LEVELS_ENABLED=json.load(open(p,encoding="utf-8"))
            LEVELS_MTIME=m
            log("分档开关: "+", ".join("L%s=%s"%(k,"开" if v else "关") for k,v in sorted(LEVELS_ENABLED.items(),key=lambda x:int(x[0]))))
    except Exception:
        pass

def level_enabled(L):
    return bool(LEVELS_ENABLED.get(str(int(L)),True))

KELLY_FRACS={}
FRACS_MTIME=0.0
def load_fracs():
    global KELLY_FRACS
    try:
        raw=json.load(open(os.path.join(WORKDIR,"kelly_fracs.json"),encoding="utf-8"))
        KELLY_FRACS={str(k):max(0.0,min(1.0,float(v))) for k,v in raw.items()}
        log("分档分数: "+", ".join("L%s=%.1f%%"%(k,v*100) for k,v in sorted(KELLY_FRACS.items(),key=lambda x:int(x[0]))))
    except Exception:
        pass

def pick_frac(L):
    return float(KELLY_FRACS.get(str(min(int(L),10)),CFG["kelly_frac"]))

def sync_bankroll(st):
    try:
        r=baw("wallet","balance","--binanceChainId","56","--json")
        if r and r.get("success"):
            for x in r.get("data") or []:
                if x.get("symbol")=="USDT":
                    st["bankroll"]=float(x.get("balance") or 0)
                    return True
    except Exception:
        pass
    return False

def resolve_outcome(result, is_winner, pnl, can_claim):
    """Strict priority: WIN/LOSS -> isWinner -> pnl sign -> canClaim.
    Never defaults to LOSS when the payload is ambiguous."""
    r=str(result or "").upper()
    if r in ("WIN","LOSS"):
        return r=="WIN"
    if isinstance(is_winner,bool):
        return is_winner
    try:
        if pnl is not None and abs(float(pnl))>1e-9:
            return float(pnl)>0
    except Exception:
        pass
    return bool(can_claim)


def six_streak(closed):
    if len(closed)<6: return False,None
    l6=closed[-6:]
    up=all(b["c"]>b["o"] for b in l6)
    down=all(b["c"]<b["o"] for b in l6)
    if not up and not down: return False,None
    try: pct=abs((l6[-1]["c"]/l6[0]["o"]-1)*100)
    except Exception: return False,None
    info={"dir":"涨" if up else "跌","pct":pct,"start":l6[0]["ot"],"end":l6[-1]["ot"]}
    return (pct>1.4),info

def update_six_block(st,active,info):
    return   # 凯利版：关闭 6 连风控
    was=bool(st.get("six_block"))
    if active:
        st["six_block"]=True
        st["six_info"]=info
        if not was:
            st["six_skip_notified"]=False
            save_state(st)
            notify_send("BTC 5m 6连风控触发",
                        f"方向: 6连{info['dir']}\n"
                        f"区间: {fmt(info['start'])} → {fmt(info['end'])}\n"
                        f"累计涨跌幅: {info['pct']:.2f}% (>1.4%)\n"
                        f"规则: 本次挂单保留，下一单禁止并重置周期")
        return
    if was:
        st["six_block"]=False
        st["six_skip_notified"]=False
        st["six_info"]=None
        save_state(st)
        log("6连风控解除")

def _market_tf(t):
    tf=str(t.get("timeframe") or "").lower().replace(" ", "")
    if tf in ("5min","5m"): return "5min"
    text=(str(t.get("title") or "")+" "+str(t.get("slug") or "")).lower()
    if "5 min" in text or "5-min" in text or __import__("re").search(r"(?<!\d)5m(?!\d)",text): return "5min"
    return tf

def _market_topics(r):
    d=(r or {}).get("data")
    if isinstance(d,dict): return d.get("marketTopics") or []
    if isinstance(d,list): return d
    return []

def market(target_ot=None):
    # 先 search，再 list：近期 BAW 的 up-down 列表过滤会返回空数组
    sources=[
      ["prediction","market","search","--query",CFG["asset"],"--limit","50","--json"],
      ["prediction","market","list","--limit","100","--json"],
      ["prediction","market","list","--l1Category","crypto","--l2Category","up-down","--limit","100","--json"],
    ]
    for args in sources:
        r=baw(*args)
        if not r or not r.get("success"): continue
        for t in _market_topics(r):
            if t.get("symbol")!=CFG["symbol"]: continue
            if _market_tf(t)!="5min": continue
            ms=t.get("markets") or []
            if not ms or ms[0].get("tradingStatus")!="OPEN": continue
            m=ms[0]
            if target_ot:
                try:
                    st=int(m.get("startDate") or t.get("startDate") or 0)
                    if st and st!=int(target_ot): continue
                except Exception:
                    pass
            out={str(o.get("name","")).upper():o.get("tokenId") for o in m.get("outcomes") or []}
            return {"topicId":t.get("marketTopicId"),"marketId":m.get("marketId"),
                    "up":out.get("UP"),"down":out.get("DOWN"),"question":m.get("question")}
    return None

def best_ask(mid,tid):
    r=baw("prediction","market","order-book","--marketId",mid,"--tokenId",tid,"--json")
    if not r or not r.get("success"): return None
    asks=(r.get("data") or {}).get("asks") or []
    try: return float(sorted(asks,key=lambda x:float(x.get("price",9)))[0]["price"])
    except: return None

def order_by_id(oid):
    r=baw("prediction","order","history","--limit","20","--json")
    if not r or not r.get("success"): return None
    for o in (r.get("data") or {}).get("orders") or []:
        if str(o.get("orderId"))==str(oid): return o
    return None

def load_state():
    if os.path.exists(STATE_F):
        try:
            st=json.load(open(STATE_F,encoding="utf-8"))
            for k,v in {"step":0,"stake":BASE_STAKE,"cum_pnl":0.0,"active":False,"direction":None,
                        "pending":None,"cycle_orders":[],"limit":CFG["price"],
                        "cycle_count":0,"cycle_no":None,"cycle_counted":False,
                        "cycle_start":None,"cycle_pnl":0.0,"cycle_total_stake":0.0,
                        "cycle_order_count":0,"cycle_max_stake":0.0,"last_latency":None,
                        "eq_peak":0.0,"loss_streak":0,"hard_stop_active":False,
                        "hard_stop_reason":"","hard_stop_time":0,"first_attempts":0,
                        "win_cycles":0,"skip_cycles":0,"stop_cycles":0,"stats_init":False,
                        "stop_cooldown_until":0,"profit_boost_active":False,"profit_boost_ratio":0,
                        "asset":CFG["asset"],"base_stake":BASE_STAKE}.items():
                st.setdefault(k,v)
            return st
        except: pass
    return {"step":0,"stake":BASE_STAKE,"cum_pnl":0.0,"active":False,"direction":None,
            "pending":None,"cycle_orders":[],"limit":CFG["price"],
            "cycle_count":0,"cycle_no":None,"cycle_counted":False,
            "cycle_start":None,"cycle_pnl":0.0,"cycle_total_stake":0.0,
            "cycle_order_count":0,"cycle_max_stake":0.0,
            "eq_peak":0.0,"loss_streak":0,"hard_stop_active":False,
            "hard_stop_reason":"","hard_stop_time":0,"first_attempts":0,
            "win_cycles":0,"skip_cycles":0,"stop_cycles":0,"stats_init":False,
            "stop_cooldown_until":0,"profit_boost_active":False,"profit_boost_ratio":0,
            "asset":CFG["asset"],"base_stake":BASE_STAKE}

def save_state(st): json.dump(st,open(STATE_F,"w",encoding="utf-8"),ensure_ascii=False,indent=2)

def init_cycle_stats(st):
    if st.get("stats_init"): return
    win=skip=stop=0
    try:
        import csv as _csv
        p=os.path.join(WORKDIR,f"live_cycles_{ACCOUNT_ID}.csv")
        for r in _csv.DictReader(open(p,encoding="utf-8-sig")):
            cat=str(r.get("category") or "").upper()
            res=str(r.get("result") or "").upper()
            if cat=="WIN": win+=1
            elif cat in ("ABORT","SKIP") or "ABORT" in res or "跳过" in r.get("result",""): skip+=1
            else: stop+=1
    except Exception: pass
    st["win_cycles"]=win; st["skip_cycles"]=skip; st["stop_cycles"]=stop
    st["stats_init"]=True
    save_state(st)

def _load_redeem():
    global REDEEM_DONE
    try:
        d=json.load(open(REDEEM_F,encoding="utf-8"))
        if isinstance(d,dict): REDEEM_DONE={str(k):float(v) for k,v in d.items()}
    except Exception: pass

def _save_redeem():
    with REDEEM_LOCK:
        now=time.time()
        items={k:v for k,v in REDEEM_DONE.items() if now-float(v)<86400}
        REDEEM_DONE.clear(); REDEEM_DONE.update(items)
        try: json.dump(REDEEM_DONE,open(REDEEM_F,"w",encoding="utf-8"),ensure_ascii=False)
        except Exception: pass

def try_redeem(token, tries=3):
    """结算即时赎回：最多重试 3 次，全部响应落盘 redeem_log"""
    last=None
    for i in range(tries):
        rr=baw("prediction","trade","redeem","--tokenIds",token,"--binanceChainId","56","--json")
        last=rr
        try:
            with open(os.path.join(WORKDIR,f"redeem_log{SUFFIX}.jsonl"),"a",encoding="utf-8") as f:
                f.write(json.dumps({"ts":now_ms(),"token":token,"attempt":i+1,"resp":rr},ensure_ascii=False)+"\n")
        except Exception:
            pass
        if rr and rr.get("success"):
            return True, rr
        time.sleep(5*(i+1))
    return False, last

def remember_redeem(ids):
    with REDEEM_LOCK:
        for x in ids: REDEEM_DONE[str(x)]=time.time()
    _save_redeem()

def redeem_worker():
    _load_redeem()
    while True:
        try:
            phase=time.time()%300
            if phase<20 or phase>285:
                time.sleep(10); continue
            r=baw("prediction","position","list","--tab","PENDING_CLAIM","--limit","50","--json")
            ps=((r or {}).get("data") or {}).get("positions") or []
            now=time.time(); ids=[]
            for p in ps:
                tok=str(p.get("tokenId") or "")
                if tok and now-float(REDEEM_DONE.get(tok,0))>600: ids.append(tok)
            if ids:
                rr=baw("prediction","trade","redeem","--tokenIds",",".join(ids),
                       "--binanceChainId","56","--json")
                if rr and rr.get("success"):
                    remember_redeem(ids)
                    log(f"自动赎回 {len(ids)} 笔")
                    notify_send("BTC 5m 自动赎回",f"已提交 {len(ids)} 笔待领取仓位赎回")
                else:
                    log("自动赎回失败 "+json.dumps(rr,ensure_ascii=False)[:200])
        except Exception as e:
            log("自动赎回异常 "+str(e)[:160])
        time.sleep(90)

def notify_order(st, side, stake, last, ask, order_id, price, mode):
    shares=stake/price
    fee_txt="挂单0费" if mode=="maker" else "吃单2%费"
    ns=int(st.get("level") or CFG["min_streak"])
    sig_txt=f"{ns}连阳" if side=="DOWN" else f"{ns}连阴"
    cyc=st.get("cycle_no") or (int(st.get("cycle_count") or 0)+1)
    step_no=int(st.get("step") or 0)+1
    body=(f"信号K线: {fmt(last['ot'])} ({sig_txt})\n"
          f"方向: 反向{'买跌' if side=='DOWN' else '买涨'}\n"
          f"{cycle_label(cyc,step_no)}\n"
          f"限价: {price}u/份 ({fee_txt}) | 注额: {stake}u | 份数: {shares:.2f}\n"
          f"下单前卖价: {ask}\n"
          f"订单号: {order_id}\n"
          f"状态: {'已挂单，等成交' if mode=='maker' else '立即吃单成交'}\n"
          f"累计: {st.get('cum_pnl',0):+.2f}u")
    notify_send(f"BTC 5m 买单情况说明 · {mode_name()}", body)

def poll_order_report(oid, phase, checks=4, interval=2):
    last=None
    for i in range(checks):
        o=order_by_id(oid) or {}
        status=str(o.get("status") or "UNKNOWN")
        fq=float(o.get("filledShareQty") or 0)
        fu=float(o.get("filledUsdtAmount") or 0)
        avg=(fu/fq) if fq else 0.0
        key=(status,round(fq,2))
        if i==0 or key!=last or i==checks-1:
            notify_send(f"BTC 5m {phase}轮询",
                        f"第{i+1}/{checks}次查询\n状态: {status}\n成交: {fq:.2f}份 @ {avg:.4f}\n订单: {oid}")
        last=key
        if status=="FILLED" and fq>0: break
        time.sleep(interval)

def mark_cycle_filled(st,p):
    if not st.get("cycle_counted"):
        no=int(st.get("cycle_no") or (int(st.get("cycle_count") or 0)+1))
        st["cycle_no"]=no
        st["cycle_count"]=no
        st["cycle_counted"]=True
        st["first_attempts"]=0
        save_state(st)

def sync_profit_boost(st):
    pct=float(CFG.get("profit_boost_pct") or 0)
    base=float(CFG.get("profit_boost_base") or 0)
    add=int(CFG.get("profit_boost_add") or 0)
    cum=float(st.get("cum_pnl") or 0)
    ratio=(cum/base*100.0) if base>0 else 0.0
    active=(pct>0 and base>0 and ratio>=pct)
    if CFG.get("profit_boost_mode")=="latched":
        if active: st["profit_boost_active"]=True
        active=bool(st.get("profit_boost_active"))
    else:
        st["profit_boost_active"]=active
    st["profit_boost_ratio"]=round(ratio,2)
    CFG["effective_min_streak"]=int(CFG["min_streak"])+(add if active else 0)

def sync_asset(st):
    cur=str(st.get("asset") or CFG["asset"]).upper()
    if not (st.get("active") or st.get("pending")):
        cur=CFG["asset"]
    if cur not in ("BTC","ETH"): cur="BTC"
    old=st.get("asset")
    CFG["asset"]=cur; CFG["symbol"]=cur+"USDT"; st["asset"]=cur
    if old!=cur:
        try: save_state(st)
        except Exception: pass

def sync_base(st):
    """凯利版: base_stake 只记录最小注位, 不再生成加倍档位"""
    st["base_stake"]=float(CFG.get("kelly_min_stake") or 1.0)
    if not st.get("active") and not st.get("pending"):
        st["stake"]=next_stake(st)

def hard_stop_check(st):
    mode=CFG.get("hard_stop_mode","none")
    cum=float(st.get("cum_pnl") or 0)
    peak=max(float(st.get("eq_peak") or 0),cum)
    st["eq_peak"]=peak
    reason=""
    if mode=="drawdown":
        lim=float(CFG.get("hard_stop_drawdown") or 0)
        dd=max(0.0,peak-cum)
        if lim>0 and dd>=lim:
            reason=f"最大回撤 {dd:.2f}u ≥ {lim:.2f}u"
    elif mode=="loss_cycles":
        lim=int(CFG.get("hard_stop_loss_cycles") or 0)
        ls=int(st.get("loss_streak") or 0)
        if lim>0 and ls>=lim:
            reason=f"连续亏损 {ls} 个周期 ≥ {lim} 个"
    if reason:
        if not st.get("hard_stop_active"):
            st["hard_stop_active"]=True
            st["hard_stop_reason"]=reason
            st["hard_stop_time"]=now_ms()
            save_state(st)
            try: open(PAUSE_F,"a").close()
            except Exception: pass
            notify_send("BTC 5m 硬止损触发",
                        f"原因: {reason}\n机器人已暂停，等待人工处理")
        return True
    if st.get("hard_stop_active"):
        st["hard_stop_active"]=False
        st["hard_stop_reason"]=""
        save_state(st)
    return False

def reset_cycle(st, reason=""):
    st["step"]=0
    st["stake"]=next_stake(st)
    st["active"]=False
    st["direction"]=None
    st["pending"]=None
    st["cycle_no"]=None
    st["cycle_counted"]=False
    st["cycle_start"]=None
    st["cycle_pnl"]=0.0
    st["cycle_total_stake"]=0.0
    st["cycle_order_count"]=0
    st["cycle_max_stake"]=0.0
    st["first_attempts"]=0
    st["limit"]=CFG["price"]
    save_state(st)
    if reason: log("周期重置: "+reason)

def record_reset(st, reason, category="ABORT"):
    cyc=st.get("cycle_no")
    if not cyc: return
    try:
        rec.cycle(ACCOUNT_ID,ACCOUNT_NUM,ACCOUNT_NAME,{
            "cycle_no":cyc,"start_time":rec.ts(st.get("cycle_start")),
            "end_time":rec.ts(),"direction":st.get("direction"),
            "result":reason,"category":category,
            "orders":st.get("cycle_order_count") or 0,
            "max_stake":st.get("cycle_max_stake") or 0,
            "total_stake":st.get("cycle_total_stake") or 0,
            "pnl":float(st.get("cycle_pnl") or 0),"counted":False})
        if category=="SKIP":
            st["skip_cycles"]=int(st.get("skip_cycles") or 0)+1
    except Exception:
        pass

def cancel_order(oid):
    r=baw("prediction","trade","cancel","--orderIds",oid,"--json")
    log(f"撤单 {oid}: {json.dumps(r,ensure_ascii=False)[:200]}")
    return r

def six_block_skip(st):
    return False   # 凯利版：不做 6 连风控
    if not st.get("six_block"): return False
    if not st.get("six_skip_notified"):
        info=st.get("six_info") or {}
        notify_send("BTC 5m 6连风控生效",
                    f"6连{info.get('dir','')} 累计 {float(info.get('pct') or 0):.2f}%\n"
                    f"本次已有挂单不受影响；下一次挂单已跳过\n"
                    f"{mode_name()}周期重置，等待新信号")
        st["six_skip_notified"]=True
    record_reset(st,"6连风控阻止下一单","SKIP")
    reset_cycle(st,"6连风控阻止下一单")
    return True

def recent_placed_order(mid, side, price, seconds=120):
    r=baw("prediction","order","history","--limit","10","--json")
    if not r or not r.get("success"): return None
    for o in (r.get("data") or {}).get("orders") or []:
        try:
            if int(o.get("marketId") or 0)!=int(mid): continue
            if str(o.get("outcome") or "").upper()!=side.upper(): continue
            if abs(float(o.get("price") or 0)-float(price))>0.0001: continue
            if int(o.get("createTime") or 0) < now_ms()-seconds*1000: continue
            if str(o.get("status") or "").upper() in ("CANCELLED","FAILED","EXPIRED"): continue
            return o
        except Exception: continue
    return None

def place(st, last, side, stake, prefetched=None):
    if DRY_RUN:
        log(f"[DRY-RUN] would place {side} {stake}u @ {CFG['price']} (signal {fmt(last['ot'])})")
        try: rec.event(ACCOUNT_ID,ACCOUNT_NUM,ACCOUNT_NAME,"dry_run",side=side,stake=stake,price=CFG["price"])
        except Exception: pass
        return False
    # 每次下单前按最新 bankroll 重算，避免结算后仍用旧注额
    if CFG.get("sizing_mode","kelly")=="kelly":
        stake=next_stake(st)
    t_place0=time.time()
    target=last["ot"]+300000
    before_place=round(t_place0-target/1000,2)
    m=prefetched
    deadline=time.time()+120
    last_log=0
    while time.time()<deadline:
        if m: break
        m=market(target)
        if m: break
        if time.time()-last_log>15:
            log("新市场未出现, 继续重试(最多2分钟)...")
            last_log=time.time()
        time.sleep(0.2)
    if not m:
        log("2分钟内找不到BTC 5m市场")
        notify_send("BTC 5m 信号", f"出现{CFG['min_streak']}连{'阳' if side=='DOWN' else '阴'}信号，但2分钟内找不到对应市场，未下单")
        return False
    t_market=time.time()
    token=m["up"] if side=="UP" else m["down"]
    base_limit=float(st.get("limit") or CFG["price"])
    # 快路径：先quote，大多数情况不再单独查盘口
    t_quote=time.time()
    q=baw("prediction","trade","quote","--binanceChainId",CFG["chain"],"--tokenId",token,
          "--marketTopicId",m["topicId"],"--side","BUY","--amount",f"{stake:.2f}",
          "--orderType","LIMIT","--priceLimit",str(base_limit),"--json")
    if not q or not q.get("success"):
        log("报价失败 "+json.dumps(q,ensure_ascii=False)[:200])
        notify_send("BTC 5m 信号", f"出现{CFG['min_streak']}连{'阳' if side=='DOWN' else '阴'}信号，但报价失败，未下单")
        return False
    qd=q["data"]; qid=qd["quoteId"]; slip=qd.get("slippageBps",1000)
    lastp=float(qd.get("lastPrice") or 0)
    ask=lastp
    # 只有价格接近0.1时才额外查盘口，避免每次多一次CLI启动
    if lastp<=0.15:
        a=best_ask(m["marketId"],token)
        if a is not None: ask=a
    if ask<=CFG["min_ask"]:
        log(f"卖价{ask}<=0.1, 不下单")
        notify_send("BTC 5m 信号", f"出现{CFG['min_streak']}连{'阳' if side=='DOWN' else '阴'}信号，但卖价{ask}<=0.1，未下单")
        return False
    limit_price=base_limit
    mode="maker" if ask>base_limit else "taker"
    quote_sec=time.time()-t_quote
    log(f"quote耗时 {quote_sec:.1f}s 参考卖价 {ask:.2f} 模式 {mode}")
    t_before_place=time.time()
    p=baw("prediction","trade","place-order","--quoteId",qid,"--orderType","LIMIT",
          "--priceLimit",str(limit_price),"--slippageBps",slip,"--json")
    if not p or not p.get("success"):
        log("下单失败 "+json.dumps(p,ensure_ascii=False)[:200])
        time.sleep(2)
        o=recent_placed_order(m["marketId"],side,limit_price)
        if o and o.get("orderId"):
            oid=str(o.get("orderId"))
            st["pending"]={"orderId":oid,"side":side,"stake":stake,"signal_ot":last["ot"],
                           "target_ot":last["ot"]+300000,"price":limit_price,"ask_at_order":ask,
                           "step":int(st.get("step") or 0),"tokenId":token,"mode":mode}
            save_state(st)
            notify_send("BTC 5m 已挂单", f"下单返回网络错误，但已在订单历史找到订单\n订单: {oid}\n方向: {side}\n注额: {stake}u")
            return True
        notify_send("BTC 5m 信号", f"出现{CFG['min_streak']}连{'阳' if side=='DOWN' else '阴'}信号，但下单失败，下一轮自动重试")
        return False
    oid=(p.get("data") or {}).get("orderId")
    st["cycle_order_count"]=int(st.get("cycle_order_count") or 0)+1
    st["cycle_total_stake"]=float(st.get("cycle_total_stake") or 0)+float(stake)
    st["cycle_max_stake"]=max(float(st.get("cycle_max_stake") or 0),float(stake))
    st["pending"]={"orderId":oid,"side":side,"stake":stake,"signal_ot":last["ot"],
                   "target_ot":last["ot"]+300000,"price":limit_price,"ask_at_order":ask,
                   "step":int(st.get("step") or 0),"tokenId":token,"mode":mode}
    save_state(st)
    rec.order(ACCOUNT_ID,ACCOUNT_NUM,ACCOUNT_NAME,{
        "cycle_no":st.get("cycle_no"),"order_no":int(st.get("step") or 0)+1,
        "signal_time":fmt(last["ot"]),"order_time":fmt(now_ms()),"order_id":oid,
        "side":side,"stake":stake,"limit_price":limit_price,"mode":mode,
        "ask":ask,"status":"SUBMITTED"})
    elapsed=(now_ms()-int(target))/1000
    st["last_latency"]={"ts":now_ms(),"side":side,"stake":stake,
                        "before":before_place,
                        "market":round(t_market-t_place0,2),
                        "quote":round(quote_sec,2),
                        "place":round(time.time()-t_before_place,2),
                        "total":round(elapsed,2),"price":limit_price,"mode":mode}
    save_state(st)
    log(f"已下单 {side} {stake}u @{limit_price} ({mode}) orderId={oid} 卖价={ask} 延迟{elapsed:.1f}s")
    notify_order(st, side, stake, last, ask, oid, limit_price, mode)
    return True

def find_position(token_id):
    def one(tab):
        r=baw("prediction","position","list","--tab",tab,"--limit","50","--json")
        if not r or not r.get("success"): return None
        for pos in (r.get("data") or {}).get("positions") or []:
            if str(pos.get("tokenId"))==str(token_id): return pos
        return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        fs=[ex.submit(one,t) for t in ("PENDING_CLAIM","ENDED","ONGOING")]
        for f in concurrent.futures.as_completed(fs):
            try:
                pos=f.result()
                if pos: return pos
            except Exception:
                pass
    return None

def settle(st):
    p=st.get("pending")
    if not p: return
    oid=p["orderId"]; end=p["target_ot"]+300000; token=p.get("tokenId")
    filled=False; last_status=None
    while now_ms() < end + 15000:
        o=order_by_id(oid)
        if o:
            last_status=str(o.get("status","")).upper()
            if float(o.get("filledShareQty") or 0)>0:
                filled=True; break
            if last_status in ("CANCELLED","FAILED","EXPIRED"):
                break
        time.sleep(2)
    if not filled:
        for _ in range(5):
            o=order_by_id(oid)
            if o and float(o.get("filledShareQty") or 0)>0:
                filled=True; break
            time.sleep(3)
    if not filled:
        o=order_by_id(oid)
        fq=float((o or {}).get("filledShareQty") or 0)
        if fq>0:
            filled=True
        else:
            if o and str(o.get("status","")).upper() not in ("CANCELLED","FAILED","EXPIRED"):
                cancel_order(oid)
            # 首单未成交：不做顺延，直接取消本周期，等待新信号
            if int(st.get("step") or 0)==0:
                cyc_pnl=float(st.get("cycle_pnl") or 0)
                rec.cycle(ACCOUNT_ID,ACCOUNT_NUM,ACCOUNT_NAME,{
                    "cycle_no":st.get("cycle_no"),"start_time":rec.ts(st.get("cycle_start")),
                    "end_time":rec.ts(),"direction":p.get("side"),"result":"跳过",
                    "category":"SKIP","orders":st.get("cycle_order_count") or 0,
                    "max_stake":st.get("cycle_max_stake") or 0,
                    "total_stake":st.get("cycle_total_stake") or 0,
                    "pnl":0,"counted":False})
                st["skip_cycles"]=int(st.get("skip_cycles") or 0)+1
                st["cycle_count"]=int(st.get("cycle_count") or 0)+1
                notify_send("BTC 5m 首单跳过",
                            f"首单未成交/已撤单\n订单: {oid}\n挂单价: {p.get('price')}\n"
                            f"按规则：首单不设顺延，本周期取消，等待新信号\n"
                            f"累计: {st.get('cum_pnl',0):+.2f}u")
                reset_cycle(st,"首单未成交，周期跳过")
                return
            attempts=int(st.get("first_attempts") or 0)+1
            st["first_attempts"]=attempts
            max_attempts=1+int(CFG.get("retry_count",1))
            if attempts>=max_attempts:
                counted=bool(st.get("cycle_counted"))
                cyc_pnl=float(st.get("cycle_pnl") or 0)
                if counted: st["stop_cycles"]=int(st.get("stop_cycles") or 0)+1
                else: st["skip_cycles"]=int(st.get("skip_cycles") or 0)+1
                log(f"本档第{attempts}次未成交，周期结束")
                rec.cycle(ACCOUNT_ID,ACCOUNT_NUM,ACCOUNT_NAME,{
                    "cycle_no":st.get("cycle_no"),"start_time":rec.ts(st.get("cycle_start")),
                    "end_time":rec.ts(),"direction":p.get("side"),
                    "result":"LOSS" if counted else "ABORT",
                    "category":"LOSS" if counted else "ABORT",
                    "orders":st.get("cycle_order_count") or 0,
                    "max_stake":st.get("cycle_max_stake") or 0,
                    "total_stake":st.get("cycle_total_stake") or 0,
                    "pnl":cyc_pnl if counted else 0,"counted":counted})
                notify_send("BTC 5m 未成交",
                            f"本档第{attempts}次未成交/已撤单\n订单: {oid}\n挂单价: {p.get('price')}\n"
                            f"按规则：同一档位最多挂{max_attempts}次（顺延{max_attempts-1}次），本周期结束，等待新信号\n"
                            f"周期盈亏: {cyc_pnl:+.2f}u\n累计: {st.get('cum_pnl',0):+.2f}u")
                if counted:
                    st["loss_streak"]=int(st.get("loss_streak") or 0)+1
                reset_cycle(st,f"同一档位{max_attempts}次未成交，周期结束")
                hard_stop_check(st)
                return
            log(f"本档第{attempts}/{max_attempts}次未成交，保留本周期，下一轮再试")
            notify_send("BTC 5m 未成交",
                        f"本档第{attempts}/{max_attempts}次未成交/已撤单\n订单: {oid}\n挂单价: {p.get('price')}\n"
                        f"保留本周期；还可顺延{max_attempts-attempts}次\n"
                        f"本周期剩余订单限价改为 {CFG.get('carry_price')}\n"
                        f"累计: {st.get('cum_pnl',0):+.2f}u")
            st["pending"]=None
            st["limit"]=CFG["carry_price"]
            st["active"]=True
            save_state(st)
            return
    if filled and not p.get("fill_notified"):
        o=order_by_id(oid) or {}
        fq=float(o.get("filledShareQty") or 0)
        fu=float(o.get("filledUsdtAmount") or 0)
        avg=(fu/fq) if fq else float(p.get("price") or 0)
        mark_cycle_filled(st,p)
        cyc=int(st.get("cycle_no") or 0)
        step_no=int(p.get("step") or st.get("step") or 0)+1
        rec.event(ACCOUNT_ID,ACCOUNT_NUM,ACCOUNT_NAME,"fill",cycle_no=cyc,order_no=step_no,
                  order_id=oid,price=avg,shares=fq,cost=fu)
        # 成交不单独发；等结算后合并成一条结果通知
        st["first_attempts"]=0
        p["fill_notified"]=True; save_state(st)
    # 等轮次结束+结算（尽量快轮询，避免拖到下一轮）
    wait_ms=end+2000-now_ms()
    if wait_ms>0: time.sleep(wait_ms/1000)
    pos=None
    for i in range(20):
        pos=find_position(token)
        if pos: break
        time.sleep(1)
    if not pos:
        log("成交后找不到持仓结算数据, 暂停")
        notify_send("BTC 5m 需要人工检查", f"订单{oid}已成交但找不到position, 机器人暂停")
        st["paused"]=True; save_state(st); return
    result=str(pos.get("result") or "").upper()
    pnl=pos.get("unrealizedPnl")
    if pnl is None:
        try: pnl=float(pos.get("value") or 0)-float(pos.get("totalCost") or 0)
        except Exception: pnl=0.0
    pnl=float(pnl)
    # BAW 的部分结算返回 result/isWinner 为空；此时以实际盈亏方向判定，避免盈利单被判成输单
    win=resolve_outcome(result,pos.get("isWinner"),pnl,pos.get("canClaim"))
    cyc=int(st.get("cycle_no") or 0)
    order_no=int(p.get("step") or 0)+1
    st["cum_pnl"]+=pnl
    # 结算后先按已实现盈亏更新本地资金，BAW余额查询失败时也不会继续用旧仓位
    try:
        _bank=float(st.get("bankroll") or 0)
        if _bank>0: st["bankroll"]=round(_bank+pnl,8)
    except Exception:
        pass
    st["kelly_bets"]=int(st.get("kelly_bets") or 0)+1
    st["kelly_wins"]=int(st.get("kelly_wins") or 0)+1 if win else int(st.get("kelly_wins") or 0)
    st["kelly_losses"]=int(st.get("kelly_losses") or 0)+(0 if win else 1)
    st["eq_peak"]=max(float(st.get("eq_peak") or 0),float(st["cum_pnl"]))
    st["cycle_pnl"]=float(st.get("cycle_pnl") or 0)+pnl
    rec.event(ACCOUNT_ID,ACCOUNT_NUM,ACCOUNT_NAME,"settle",cycle_no=cyc,order_no=order_no,
              order_id=oid,result=("WIN" if win else "LOSS"),pnl=pnl,shares=float(pos.get("shares") or 0),
              avg_price=float(pos.get("avgPrice") or 0))
    if win:
        st["stake"]=next_stake(st); st["active"]=False; st["direction"]=None; st["limit"]=CFG["price"]; res="赢"
        st["loss_streak"]=0
        st["win_cycles"]=int(st.get("win_cycles") or 0)+1
    else:
        st["stake"]=next_stake(st); st["active"]=False; st["direction"]=None; st["limit"]=CFG["price"]; res="输"
        st["loss_streak"]=int(st.get("loss_streak") or 0)+1
    st["first_attempts"]=0
    redeemed=""
    if pos.get("canClaim"):
        ok,rr=try_redeem(token)
        redeemed="已赎回" if ok else "赎回失败（已转后台重试）"
        if ok: remember_redeem([token])
        log(f"redeem {redeemed} {json.dumps(rr,ensure_ascii=False)[:300]}")
    st["pending"]=None; save_state(st)
    hard_stop_check(st)
    time.sleep(2)
    # 真实账户数据
    usdt=None; today_pnl=None; today_pct=None
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            fb=ex.submit(baw,"wallet","balance","--binanceChainId","56","--json")
            fp=ex.submit(baw,"prediction","position","list","--limit","5","--json")
            bal=fb.result(timeout=6)
            psr=fp.result(timeout=6)
        if bal and bal.get("success"):
            for x in bal.get("data") or []:
                if x.get("symbol")=="USDT": usdt=float(x.get("balance") or 0)
        if psr and psr.get("success"):
            sm=(psr.get("data") or {}).get("summary") or {}
            today_pnl=sm.get("todayRealizedPnl"); today_pct=sm.get("todayRealizedPnlPercent")
    except Exception:
        pass
    avg=float(pos.get("avgPrice") or 0) or (float(pos.get("totalCost") or 0)/float(pos.get("shares") or 1))
    fq=float(pos.get("shares") or 0)
    body=(f"{res} · {mode_name()}\n{cycle_label(cyc,order_no)}\n订单: {oid}\n"
          f"成交价: {avg:.4f}\n成交份数: {fq:.2f}\n"
          f"本轮盈亏: {pnl:+.2f}u\n策略累计: {st['cum_pnl']:+.2f}u\n")
    if usdt is not None:
        st["bankroll"]=float(usdt)
        st["stake"]=next_stake(st)
        save_state(st)
        body+=f"钱包余额: {usdt:.2f} USDT\n"
    if today_pnl is not None: body+=f"今日已实现: {float(today_pnl):+.2f}u ({float(today_pct or 0):+.1f}%)\n"
    body+=f"{redeemed}\n{sizing_note(st)}\n{trade_stats(st)}\n下一单: {st['stake']}u"
    log(f"{res} pnl={pnl:.2f} 策略累计={st['cum_pnl']:.2f} 钱包={usdt} {redeemed}")
    notify_send(f"BTC 5m 成交结果 · {mode_name()} · {res}", body)
    if res in ("赢","止损","输"):
        rec.cycle(ACCOUNT_ID,ACCOUNT_NUM,ACCOUNT_NAME,{
            "cycle_no":cyc,"start_time":rec.ts(st.get("cycle_start")),"end_time":rec.ts(),
            "direction":p.get("side"),"result":res,
            "category":"WIN" if float(st.get("cycle_pnl") or 0)>0 else "LOSS",
            "orders":st.get("cycle_order_count") or 0,
            "max_stake":st.get("cycle_max_stake") or 0,
            "total_stake":st.get("cycle_total_stake") or 0,
            "pnl":st.get("cycle_pnl") or 0,"counted":True})
        st["cycle_no"]=None
        st["cycle_counted"]=False
        st["cycle_start"]=None
        st["cycle_pnl"]=0.0
        st["cycle_total_stake"]=0.0
        st["cycle_order_count"]=0
        st["cycle_max_stake"]=0.0
        save_state(st)

def main():
    if "--once" in sys.argv:
        bars=klines(12); closed=[b for b in bars if b["ct"]<=now_ms()]
        last=closed[-1]; d=streak(closed)
        log(f"最近K线 {fmt(last['ot'])} 信号={d or '无'} 状态={load_state()}")
        return
    load_cfg()
    load_fracs()
    st=load_state()
    init_cycle_stats(st)
    sync_base(st)
    sync_asset(st)
    sync_profit_boost(st)
    sync_bankroll(st)
    if CFG.get("sizing_mode","kelly")=="kelly":
        st["stake"]=next_stake(st)
    save_state(st)
    beat(st,"start")
    log(f"live bot启动 mode=凯利 stake={st['stake']} 累计={st['cum_pnl']} 信号{CFG['min_streak']}连 凯利p={CFG['kelly_p']} 分数={CFG['kelly_frac']} 限价{CFG['price']}/{CFG['carry_price']}")
    while True:
        if os.path.exists(STOP_F):
            st=load_state()
            if st.get("pending"):
                log("STOP: 先结算当前持仓")
                settle(st)
            log("STOP文件存在, 退出"); return
        load_cfg()
        st=load_state()
        sync_base(st)
        sync_asset(st)
        sync_profit_boost(st)
        hard_stop_check(st)
        if os.path.exists(CYCLE_STOP_F):
            st=load_state()
            log("收到控制面板：终止本周期")
            try:
                rec.event(ACCOUNT_ID,ACCOUNT_NUM,ACCOUNT_NAME,"cycle_stop",
                          cycle_no=st.get("cycle_no"),cycle_pnl=st.get("cycle_pnl"),
                          cum_pnl=st.get("cum_pnl"))
            except Exception:
                pass
            record_reset(st,"控制面板终止本周期","ABORT")
            reset_cycle(st,"控制面板终止本周期")
            try: os.remove(CYCLE_STOP_F)
            except Exception: pass
            notify_send("BTC 5m 周期终止",
                        f"已按控制面板指令终止当前周期\n累计: {st.get('cum_pnl',0):+.2f}u\n等待新信号")
            continue
        if os.path.exists(PAUSE_F):
            st=load_state()
            if st.get("pending"):
                log("PAUSED: 继续处理当前持仓")
                settle(st)
            else:
                log("PAUSED: 暂停开新单")
            beat(st,"paused")
            time.sleep(10)
            continue
        nxt=(now_ms()//300000+1)*300000+500
        warm_done=False
        while now_ms()<nxt:
            if os.path.exists(STOP_F) or os.path.exists(PAUSE_F) or os.path.exists(CYCLE_STOP_F): break
            if (not warm_done) and now_ms()>=nxt-9000:
                warm_done=True
                def _warm():
                    try:
                        baw("prediction","market","list","--l1Category","crypto",
                            "--l2Category","up-down","--limit","100","--json")
                    except Exception:
                        pass
                threading.Thread(target=_warm,daemon=True).start()
            time.sleep(0.2)
        if os.path.exists(STOP_F) or os.path.exists(PAUSE_F) or os.path.exists(CYCLE_STOP_F):
            continue
        target=(now_ms()//300000)*300000
        m_pre=None
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fm=ex.submit(market,target)
            bars=klines(12)
            try:
                m_pre=fm.result(timeout=6)
            except Exception:
                m_pre=None
        if not bars: continue
        closed=[b for b in bars if b["ct"]<=now_ms()]
        if len(closed)<5: continue
        last=closed[-1]; load_cfg(); load_p_table(); load_levels(); load_fracs(); st=load_state(); sync_base(st); sync_asset(st)
        if not st.get("active"):
            st["limit"]=CFG["price"]
        want=next_stake(st)
        if abs(float(st.get("stake") or 0)-want)>1e-9:
            st["stake"]=want; save_state(st)
        beat(st)
        six_active,six_info=six_streak(closed)
        update_six_block(st,six_active,six_info)
        st=load_state()
        if st.get("paused"):
            log("paused, 停止交易"); continue
        if st.get("pending"):
            settle(st)
            st=load_state()
            if st.get("pending") or st.get("paused"):
                continue
        cu=int(st.get("stop_cooldown_until") or 0)
        if cu and not st.get("active") and not st.get("pending"):
            if now_ms()<cu:
                log("止损冷却中，跳过本次信号")
                continue
            st["stop_cooldown_until"]=0
            save_state(st)
        sync_profit_boost(st)
        d,L_cur=streak_run(closed)
        st.setdefault("active",False)
        if st.get("active"):
            if not st.get("cycle_no"):
                st["cycle_no"]=int(st.get("cycle_count") or 0)+1
                st["cycle_counted"]=False
                st["cycle_start"]=now_ms()
                st["cycle_pnl"]=0.0; st["cycle_total_stake"]=0.0
                st["cycle_order_count"]=0; st["cycle_max_stake"]=0.0
                st["first_attempts"]=0
            if not st.get("cycle_start"):
                st["cycle_start"]=now_ms()
            save_state(st)
            if not place(st,last,st["direction"],st["stake"],m_pre):
                if int(st.get("step") or 0)==0:
                    st=load_state(); record_reset(st,"首单下单失败，周期结束","SKIP"); reset_cycle(st,"首单下单失败，周期结束")
                    notify_send("BTC 5m 未成交","首单下单失败，按规则本周期结束，等待新信号")
                else:
                    log("下单失败，保留当前档位，下一轮顺延重试")
        elif d:
            st["level"]=int(L_cur)
            st["kelly_p"]=pick_p(L_cur)
            st["kelly_frac"]=pick_frac(L_cur)
            if not level_enabled(L_cur):
                log(f"L={L_cur} 已手动关闭，跳过")
                continue
            _p=float(st["kelly_p"]); _edge=_p-float(CFG["price"])
            if _edge < float(CFG.get("kelly_min_edge") or 0.005):
                log(f"L={L_cur} p={_p:.4f} edge={_edge:+.4f} 低于阈值，跳过")
                continue
            st["active"]=True; st["direction"]=d; st["step"]=0; st["stake"]=next_stake(st); st["base_stake"]=BASE_STAKE; st["asset"]=CFG["asset"]; st["limit"]=CFG["price"]
            st["cycle_no"]=int(st.get("cycle_count") or 0)+1
            st["cycle_counted"]=False
            st["cycle_start"]=now_ms()
            st["cycle_pnl"]=0.0; st["cycle_total_stake"]=0.0
            st["cycle_order_count"]=0; st["cycle_max_stake"]=0.0
            st["first_attempts"]=0
            save_state(st)
            rec.event(ACCOUNT_ID,ACCOUNT_NUM,ACCOUNT_NAME,"cycle_start",cycle_no=st["cycle_no"],direction=d,signal_time=fmt(last["ot"]))
            if not place(st,last,d,st["stake"],m_pre):
                if int(st.get("step") or 0)==0:
                    st=load_state(); record_reset(st,"首单下单失败，周期结束","SKIP"); reset_cycle(st,"首单下单失败，周期结束")
                    notify_send("BTC 5m 未成交","首单下单失败，按规则本周期结束，等待新信号")
                else:
                    log("下单失败，保留当前档位，下一轮顺延重试")
        else:
            log(f"{fmt(last['ot'])} 无信号")

if __name__=="__main__": main()
