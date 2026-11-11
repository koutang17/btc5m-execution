#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local proxy supervisor.

Everything it acts on is configured under network.proxy in config.json. If nothing is
configured it idles instead of guessing: an unconfigured watchdog must never kill a process.

    network.proxy.host / port      proxy endpoint the wallet CLI uses
    network.proxy.clash_api        optional controller, e.g. http://127.0.0.1:9090
    network.proxy.selector         optional proxy-group name
    network.proxy.nodes            optional preferred node names, tried in order
    network.proxy.app_exe          optional proxy app to restart
    network.proxy.kill_names       optional process names to stop before restart
    network.proxy.helper_exe       optional helper to start if the app does not come back
"""
import json, os, socket, subprocess, sys, time, urllib.parse, urllib.request
from datetime import datetime

import config as _cfg

P = _cfg.NET
HOST = P.get("host") or "127.0.0.1"
PORT = int(P.get("port") or 7897)
CLASH_API = P.get("clash_api") or ""
SELECTOR = P.get("selector") or ""
NODES = [str(x) for x in (P.get("nodes") or [])]
APP_EXE = P.get("app_exe") or ""
HELPER_EXE = P.get("helper_exe") or ""
KILL_NAMES = [str(x) for x in (P.get("kill_names") or [])]

TEST_URLS = ["https://api.binance.com", "https://www.binance.com", "https://www.google.com"]
CHECK_SEC = 10
FAIL_THRESHOLD = 2
WAIT_SEC = 60
MAX_RESTARTS = 3
COOLDOWN_SEC = 120
LOG_F = os.path.join(_cfg.LOG_DIR, "vpn_watchdog.log")


def log(s):
    line = f"[{datetime.now(_cfg.TZ).strftime('%Y-%m-%d %H:%M:%S')}] {s}"
    print(line, flush=True)
    try:
        with open(LOG_F, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def port_open():
    s = socket.socket(); s.settimeout(1)
    try:
        return s.connect_ex((HOST, PORT)) == 0
    finally:
        s.close()


def proxy_ok():
    for url in TEST_URLS:
        try:
            r = subprocess.run(["curl.exe", "-sS", "-x", f"http://{HOST}:{PORT}",
                                "-o", "NUL", "-w", "%{http_code}", "--max-time", "10", url],
                               capture_output=True, text=True, timeout=15,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if (r.stdout or "").strip() not in ("", "000"):
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def controller_ok():
    if not CLASH_API:
        return False
    try:
        urllib.request.urlopen(CLASH_API + "/version", timeout=3).read()
        return True
    except Exception:
        return False


def switch_node():
    if not (controller_ok() and SELECTOR and NODES):
        return None
    sel = urllib.parse.quote(SELECTOR, safe="")
    for name in NODES:
        try:
            req = urllib.request.Request(CLASH_API + "/proxies/" + sel,
                                         data=json.dumps({"name": name}, ensure_ascii=False).encode("utf-8"),
                                         method="PUT", headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5).read()
            log("switched group to: " + name)
            time.sleep(3)
            if proxy_ok():
                return name
        except Exception as e:
            log("switch failed " + name + ": " + str(e)[:80])
    return None


def restart_app():
    if not APP_EXE:
        return False
    for name in KILL_NAMES:
        subprocess.run(["taskkill", "/F", "/IM", name], capture_output=True, text=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    time.sleep(2)
    if os.path.exists(APP_EXE):
        try:
            subprocess.Popen([APP_EXE], cwd=os.path.dirname(APP_EXE))
        except Exception as e:
            log("start failed: " + str(e)[:120])
    if HELPER_EXE and os.path.exists(HELPER_EXE):
        try:
            subprocess.Popen([HELPER_EXE], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
    for _ in range(WAIT_SEC // 2):
        time.sleep(2)
        if port_open() and proxy_ok():
            return True
    return False


def notify(msg):
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from notify import send
        send("VPN 守护", msg)
    except Exception:
        pass


def main():
    dry = "--dry-run" in sys.argv
    if "--once" in sys.argv:
        p = port_open(); q = proxy_ok(); ok = p and q
        log(f"port={p} proxy={q} => {'OK' if ok else 'DOWN'}")
        return 0 if ok else 1
    if not (APP_EXE or HELPER_EXE or NODES):
        log("network.proxy not configured; idling (no action will be taken)")
        while True:
            time.sleep(3600)
    log("VPN 守护启动  dry=" + str(dry))
    fails = 0; last_restart = 0
    while True:
        ok = port_open() and proxy_ok()
        if ok:
            if fails > 0:
                log("代理已恢复")
            fails = 0
        else:
            fails += 1
            log(f"检测失败 {fails}/{FAIL_THRESHOLD}")
            if fails >= FAIL_THRESHOLD:
                if dry:
                    log("dry-run: 本应切节点/重启")
                elif time.time() - last_restart < COOLDOWN_SEC:
                    log("冷却中，暂不处理")
                else:
                    picked = switch_node()
                    if picked:
                        fails = 0
                        time.sleep(CHECK_SEC)
                        continue
                    notify("节点切换无效，正在重启代理程序")
                    ok2 = False
                    for i in range(1, MAX_RESTARTS + 1):
                        log(f"重启尝试 {i}/{MAX_RESTARTS}")
                        if restart_app():
                            ok2 = True
                            break
                        time.sleep(5)
                    last_restart = time.time()
                    fails = 0 if ok2 else fails
                    if not ok2:
                        notify("代理自动恢复失败，请人工检查")
        time.sleep(CHECK_SEC)


if __name__ == "__main__":
    sys.exit(main())
