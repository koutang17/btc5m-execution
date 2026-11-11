
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""botctl — 在马丁机器人与凯利机器人之间安全切换

用法:
  python botctl.py status                 查看当前在跑哪个、状态是否空仓
  python botctl.py switch kelly           切到凯利（会先确认空仓）
  python botctl.py switch martingale      切回马丁
  python botctl.py switch kelly --wait 300  最多等 300 秒直到空仓再切
  python botctl.py switch kelly --force   不等空仓，强制切（危险）
  python botctl.py stop                   停掉所有机器人
  python botctl.py start kelly            只启动，不检查
  python botctl.py switch kelly --dry-run 只打印将要做什么
"""
import argparse, json, os, subprocess, sys, time

import config as _cfg

SRC = os.path.dirname(os.path.abspath(__file__))
WORKDIR = _cfg.HOME
SCRIPTS = {"martingale": "legacy/martingale_bot.py", "kelly": "predict_live_bot_kelly.py"}
STATE = {"martingale": [os.path.join(_cfg.STATE_DIR, "predict_live_state.json"),
                        os.path.join(_cfg.STATE_DIR, "predict_live_state_B.json")],
         "kelly": [os.path.join(_cfg.STATE_DIR, "predict_live_state_kelly.json"),
                   os.path.join(_cfg.STATE_DIR, "predict_live_state_B_kelly.json")]}
PY = sys.executable
MODE_F = os.path.join(_cfg.STATE_DIR, "active_mode.json")

def set_mode(mode):
    try:
        json.dump({"mode": mode}, open(MODE_F, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass

def ps(cmd):
    r = subprocess.run(["powershell","-NoProfile","-Command",cmd], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout.strip()

def procs():
    out = ps("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object { $_.CommandLine -match 'predict_live_bot' } | "
             "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress")
    if not out:
        return []
    try:
        d = json.loads(out)
    except Exception:
        return []
    if isinstance(d, dict):
        d = [d]
    res = []
    for x in d:
        cl = x.get("CommandLine") or ""
        mode = "kelly" if "predict_live_bot_kelly" in cl else ("martingale" if "predict_live_bot.py" in cl else "?")
        res.append({"pid": x.get("ProcessId"), "mode": mode, "cmd": cl})
    return res

def read_state(mode):
    out = []
    for f in STATE[mode]:
        p = os.path.join(WORKDIR, f)
        if not os.path.exists(p):
            continue
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        out.append({"file": f, "cum_pnl": d.get("cum_pnl"), "active": d.get("active"),
                    "pending": bool(d.get("pending")), "stake": d.get("stake"),
                    "bankroll": d.get("bankroll"), "cycle": d.get("cycle_no"),
                    "step": d.get("step"), "limit": d.get("limit")})
    return out

def is_flat(mode):
    st = read_state(mode)
    if not st:
        return True, "无状态文件（视为空仓）"
    busy = [s for s in st if s["active"] or s["pending"]]
    if busy:
        return False, "有持仓/挂单: " + ", ".join("%s(active=%s,pending=%s)" % (s["file"], s["active"], s["pending"]) for s in busy)
    return True, "空仓"

def kill_all(dry):
    ps_list = procs()
    if not ps_list:
        print("  没有运行中的机器人")
        return
    for x in ps_list:
        print("  停止 PID %s (%s)" % (x["pid"], x["mode"]))
        if not dry:
            subprocess.run(["powershell","-NoProfile","-Command",
                            "Stop-Process -Id %s -Force" % x["pid"]], capture_output=True)
    if not dry:
        time.sleep(2)

def start(mode, dry):
    script = os.path.join(SRC, SCRIPTS[mode])
    if not os.path.exists(script):
        print("  [错误] 找不到脚本: " + script); return False
    ok = True
    for acct in ("A","B"):
        env_prefix=""
        if acct=="B":
            env_prefix=("$env:BAW_JS='%s'; $env:BINANCE_BAW_DIR='%s'; " % (B_JS, B_DIR))
            if not b_logged_in():
                print("  跳过账户 B: baw-b 未登录 (NOT_LOGGED_IN)")
                continue
        cmd = (env_prefix + "$env:ACCOUNT_ID='%s'; Start-Process -FilePath '%s' -ArgumentList '-u','%s' "
               "-WorkingDirectory '%s' -WindowStyle Hidden -PassThru | Select-Object -ExpandProperty Id") % (acct, PY, script, WORKDIR)
        print("  启动账户 %s: %s" % (acct, SCRIPTS[mode]))
        if dry:
            continue
        out = ps(cmd)
        print("    PID = " + out.strip())
        if not out.strip().isdigit():
            ok = False
    if ok and not dry:
        set_mode(mode)
    return ok

_B = _cfg.account("B")
B_JS = _B["baw_js"]
B_DIR = _B["wallet_profile"]
BOOT = _cfg.BOOT

def b_logged_in():
    """检查 B 账户的钱包 CLI 是否已登录"""
    try:
        cmd=("$env:NODE_COMPILE_CACHE='%s'; $env:BINANCE_BAW_DIR='%s'; "
             "& '%s' --import '%s' '%s' "
             "wallet balance --binanceChainId %s --json") % (
                 os.path.join(_cfg.HOME,"node_cache"), B_DIR, _cfg.NODE, BOOT, B_JS, _cfg.CFG["chain"])
        out=ps(cmd)
        return ('"success": true' in out or '"success":true' in out) and ("NOT_LOGGED_IN" not in out)
    except Exception:
        return False

def status():
    print("== 进程 ==")
    pl = procs()
    if not pl:
        print("  （无）")
    for x in pl:
        print("  PID %s  mode=%s" % (x["pid"], x["mode"]))
    modes = sorted(set(x["mode"] for x in pl))
    if len(modes) > 1:
        print("  !! 警告: 两个模式同时在跑，必须停掉一个")
    print("\n== 状态 ==")
    for m in ("martingale","kelly"):
        flat, why = is_flat(m)
        st = read_state(m)
        print("  %-10s 空仓=%s  %s" % (m, flat, why if not flat else ""))
        for s in st:
            print("    %-38s cum_pnl=%s active=%s pending=%s stake=%s bankroll=%s" % (
                s["file"], s["cum_pnl"], s["active"], s["pending"], s["stake"], s["bankroll"]))

def wait_flat(mode, timeout):
    t0 = time.time()
    while True:
        flat, why = is_flat(mode)
        if flat:
            return True
        if time.time() - t0 > timeout:
            return False
        print("  等待空仓... (%s)" % why)
        time.sleep(5)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["status","switch","stop","start"])
    ap.add_argument("mode", nargs="?", choices=["martingale","kelly"])
    ap.add_argument("--wait", type=int, default=0)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if a.action == "status":
        status(); return
    if a.action == "stop":
        kill_all(a.dry_run)
        if not a.dry_run: set_mode("none")
        return
    if not a.mode:
        print("需要指定模式: martingale | kelly"); sys.exit(1)
    if a.action == "start":
        start(a.mode, a.dry_run); return

    # switch
    print("== 切换到 %s ==" % a.mode)
    pl = procs()
    cur = sorted(set(x["mode"] for x in pl))
    print("  当前运行: %s" % (", ".join(cur) if cur else "无"))
    if len(cur) == 1 and cur[0] == a.mode:
        set_mode(a.mode)
        print("  已经是该模式，无需切换"); return
    for m in cur:
        flat, why = is_flat(m)
        if not flat:
            if a.force:
                print("  [强制] 忽略持仓: " + why)
            elif a.wait > 0:
                if not wait_flat(m, a.wait):
                    print("  [超时] 仍未空仓: " + why); return
            else:
                print("  [拒绝] 当前有持仓，等结算后再切（或加 --wait 300 / --force）")
                print("  详情: " + why); return
    kill_all(a.dry_run)
    if start(a.mode, a.dry_run):
        print("  切换完成")
        if not a.dry_run:
            time.sleep(2); status()
    else:
        print("  启动失败，请检查")

if __name__ == "__main__":
    main()
