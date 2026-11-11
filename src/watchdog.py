#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BTC 5m 守护程序：每分钟检查并拉起见板/机器人/隧道/辅助进程"""
import os, sys, json, time, socket, subprocess
from datetime import datetime, timezone, timedelta

import config as _cfg

SRC=os.path.dirname(os.path.abspath(__file__))
WORK=_cfg.HOME
sys.path.insert(0,SRC)
from notify import send
PY=sys.executable
if os.path.basename(PY).lower()=="pythonw.exe":
    _cand=os.path.join(os.path.dirname(PY),"python.exe")
    if os.path.exists(_cand): PY=_cand
BOT=os.path.join(SRC,"legacy","martingale_bot.py")
MODE_F=os.path.join(_cfg.STATE_DIR,"active_mode.json")
ACCOUNT_MODES_F=os.path.join(_cfg.STATE_DIR,"account_modes.json")
DASH=os.path.join(SRC,"dashboard_server.py")
CF=os.environ.get("CLOUDFLARED_EXE") or _cfg.RT.get("cloudflared","")
CF_CFG=os.environ.get("CLOUDFLARED_CONFIG") or _cfg.RT.get("cloudflared_config","")
TUNNEL=_cfg.RT.get("tunnel_name","")
TZ=_cfg.TZ
STATUS_F=os.path.join(_cfg.STATE_DIR,"watchdog_status.json")
STATE_F=os.path.join(_cfg.STATE_DIR,"watchdog_notify.json")
LOCK_F=os.path.join(_cfg.STATE_DIR,"watchdog.lock")
LOG_F=os.path.join(_cfg.LOG_DIR,"watchdog.log")
ACCOUNTS=[{"id":a["id"],
           "env":dict({"BINANCE_BAW_DIR":a["wallet_profile"]}, **({"BAW_JS":a["baw_js"]} if a["baw_js"] else {}))}
          for a in _cfg.ACCOUNTS]

def now_str(): return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
def log(s):
    line=f"[{now_str()}] {s}"
    try:
        if os.path.getsize(LOG_F)>2_000_000:
            os.replace(LOG_F,LOG_F+".old")
    except Exception: pass
    try:
        with open(LOG_F,"a",encoding="utf-8") as f: f.write(line+"\n")
    except Exception: pass
    print(line,flush=True)

def active_mode():
    try:
        d=json.load(open(MODE_F,encoding="utf-8"))
        m=str(d.get("mode") or "none").lower()
        return m if m in ("martingale","kelly") else "none"
    except Exception:
        return "none"

def start_bot_mode(mode):
    try:
        r=subprocess.run([PY,"-u",os.path.join(WORK,"botctl.py"),"start",mode],
                         capture_output=True,text=True,timeout=90,
                         creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        return r.returncode==0
    except Exception:
        return False

def account_modes():
    try:
        d=json.load(open(ACCOUNT_MODES_F,encoding="utf-8"))
    except Exception:
        d={}
    out={}
    for a in ACCOUNTS:
        m=str(d.get(a["id"]) or "none").lower()
        out[a["id"]]=m if m in ("martingale","kelly","none") else "none"
    return out

def start_account(acct,mode):
    script={"martingale":"legacy/martingale_bot.py","kelly":"predict_live_bot_kelly.py"}.get(mode)
    if not script: return False
    a=_cfg.account(acct)
    env=os.environ.copy(); env["ACCOUNT_ID"]=acct
    env["BINANCE_BAW_DIR"]=a["wallet_profile"]
    env["BASE_STAKE"]=str(a["base_stake"])
    if a["baw_js"]: env["BAW_JS"]=a["baw_js"]
    try:
        subprocess.Popen([PY,"-u",os.path.join(SRC,script),"--account-id",acct],
                         cwd=WORK,env=env,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0)|getattr(subprocess,"DETACHED_PROCESS",0))
        return True
    except Exception:
        return False

def sh(cmd,timeout=20):
    try:
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=timeout)
        return r.stdout or ""
    except Exception: return ""

def processes():
    cmd=('Get-CimInstance Win32_Process | Where-Object { $_.Name -in @("python.exe","cloudflared.exe") } '
         '| Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress')
    out=sh(["powershell","-NoProfile","-Command",cmd],25)
    try:
        d=json.loads(out)
        return d if isinstance(d,list) else [d]
    except Exception: return []

def pid_alive(pid,ps):
    try: return any(int(p.get("ProcessId") or 0)==int(pid) for p in ps)
    except Exception: return False

def start_bot(acc):
    try:
        hp=os.path.join(WORK,f"heartbeat_{acc['id']}.json")
        d=json.load(open(hp,encoding="utf-8"))
        pid=d.get("pid")
        if pid and pid_alive(pid,processes()): return False,"pid alive"
    except Exception: pass
    if os.path.exists(os.path.join(_cfg.STATE_DIR,"STOP_"+acc["id"] if acc["id"]!="A" else "STOP")):
        return False,"stopped by flag"
    env=os.environ.copy(); env.update(acc["env"])
    env["ACCOUNT_ID"]=acc["id"]; env["BASE_STAKE"]="1"
    flags=getattr(subprocess,"CREATE_NO_WINDOW",0)|getattr(subprocess,"DETACHED_PROCESS",0)
    try:
        subprocess.Popen([PY,"-u",BOT],cwd=WORK,env=env,creationflags=flags,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        return True,"started"
    except Exception as e:
        return False,str(e)[:100]

def start_py(name):
    return start_cmd(PY,[os.path.join(SRC,name)])


def start_cmd(path,args=None):
    flags=getattr(subprocess,"CREATE_NO_WINDOW",0)|getattr(subprocess,"DETACHED_PROCESS",0)
    try:
        subprocess.Popen([path]+(args or []),cwd=WORK,creationflags=flags,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False

def start_bat(name):
    flags=getattr(subprocess,"CREATE_NO_WINDOW",0)|getattr(subprocess,"DETACHED_PROCESS",0)
    try:
        subprocess.Popen(["cmd.exe","/c",os.path.join(WORK,name)],cwd=WORK,creationflags=flags,
                         stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        return True
    except Exception: return False

def dashboard_up():
    s=socket.socket(); s.settimeout(2)
    try: return s.connect_ex(("127.0.0.1",8600))==0
    finally: s.close()

def main():
    try:
        if os.path.exists(LOCK_F) and time.time()-os.path.getmtime(LOCK_F)<55:
            return
        open(LOCK_F,"w").write(str(os.getpid()))
    except Exception: pass
    checks=[]; issues=[]; actions=[]
    try:
        ps=processes()
        pids={int(p.get("ProcessId") or 0) for p in ps}
        # dashboard
        dash_proc=[p for p in ps if "dashboard_server.py" in (p.get("CommandLine") or "")]
        if dash_proc:
            ok=dashboard_up(); checks.append({"name":"看板","ok":ok,"detail":("端口正常" if ok else "进程在但端口未响应")})
            if not ok: issues.append("看板端口无响应")
        else:
            checks.append({"name":"看板","ok":False,"detail":"进程未运行"})
            if start_py("dashboard_server.py"): actions.append("重启看板"); time.sleep(2)
            issues.append("看板未运行")
        # bots —— 每个账户按 account_modes 独立检查
        amodes=account_modes()
        all_bots=[p for p in ps if "predict_live_bot" in (p.get("CommandLine") or "")]
        for acct in ("A","B"):
            expected=amodes.get(acct,"none")
            procs=[p for p in all_bots if ("--account-id "+acct) in (p.get("CommandLine") or "")]
            if expected=="none":
                if procs:
                    checks.append({"name":"机器人"+acct,"ok":False,"detail":"策略关闭但进程仍在"})
                    issues.append("账户"+acct+"策略关闭但机器人仍运行")
                else:
                    checks.append({"name":"机器人"+acct,"ok":True,"detail":"策略关闭"})
                continue
            if not procs:
                ok=start_account(acct,expected)
                checks.append({"name":"机器人"+acct,"ok":ok,"detail":("已启动"+expected if ok else "启动失败")})
                if ok: actions.append("启动账户"+acct+expected)
                else: issues.append("账户"+acct+"机器人未运行")
                continue
            modes=set("kelly" if "predict_live_bot_kelly" in (p.get("CommandLine") or "") else "martingale" for p in procs)
            if modes=={expected}:
                checks.append({"name":"机器人"+acct,"ok":True,"detail":"运行中 "+expected})
            else:
                checks.append({"name":"机器人"+acct,"ok":False,"detail":"模式="+"/".join(sorted(modes))+" 期望="+expected})
                issues.append("账户"+acct+"机器人模式不一致")
        # cloudflared
        cf=[p for p in ps if p.get("Name","").lower()=="cloudflared.exe" and TUNNEL and TUNNEL in (p.get("CommandLine") or "")]
        if cf: checks.append({"name":"隧道","ok":True,"detail":"运行中"})
        else:
            ok=bool(CF and CF_CFG and TUNNEL) and start_cmd(CF,["tunnel","--config",CF_CFG,"run",TUNNEL])
            checks.append({"name":"隧道","ok":ok,"detail":"已尝试启动" if ok else "启动失败"})
            if ok: actions.append("重启隧道")
            else: issues.append("隧道未运行")
        # helper processes
        for nm,cmdline in [("登录守护","login_watchdog.py"),
                               ("VPN守护","vpn_watchdog.py"),
                               ("8连监控","streak8_monitor.py")]:
            hit=[p for p in ps if cmdline in (p.get("CommandLine") or "")]
            if hit: checks.append({"name":nm,"ok":True,"detail":"运行中"})
            else:
                ok=start_py(cmdline)
                checks.append({"name":nm,"ok":ok,"detail":"已尝试启动" if ok else "启动失败"})
                if ok: actions.append("启动"+nm)
                else: issues.append(nm+"未运行")
        body={"ts":int(time.time()),"time":now_str(),"ok":not issues,
              "checks":checks,"issues":issues,"actions":actions}
        json.dump(body,open(STATUS_F,"w",encoding="utf-8"),ensure_ascii=False,indent=1)
        # 只有存在未解决问题时才通知；成功自动拉起/恢复正常不发消息
        prev={}
        try: prev=json.load(open(STATE_F,encoding="utf-8"))
        except Exception: pass
        key="|".join(issues)
        last=float(prev.get("ts") or 0)
        oldkey=prev.get("key","")
        if issues:
            if key!=oldkey:
                body_txt="时间: "+now_str()+"\n"
                for c in checks: body_txt+=("✅ " if c["ok"] else "❌ ")+c["name"]+": "+c.get("detail","")+"\n"
                if actions: body_txt+="\n已处理:\n"+"\n".join("- "+x for x in actions)
                body_txt+="\n待处理:\n"+"\n".join("- "+x for x in issues)
                try: send("BTC 5m 守护程序",body_txt)
                except Exception: pass
                json.dump({"ts":time.time(),"key":key},open(STATE_F,"w",encoding="utf-8"),ensure_ascii=False)
        elif oldkey:
            json.dump({"ts":0,"key":""},open(STATE_F,"w",encoding="utf-8"),ensure_ascii=False)
        log(("OK " if not issues else "ISSUE "+",".join(issues))+" actions="+",".join(actions))
    finally:
        try: os.remove(LOCK_F)
        except Exception: pass

if __name__=="__main__":
    main()
