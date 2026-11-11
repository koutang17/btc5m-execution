#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""无窗口启动器：启动全部组件后退出。所有路径与凭据来自 config.json / .env。"""
import os, subprocess, time, json
from datetime import datetime
import config as _cfg

SRC = os.path.dirname(os.path.abspath(__file__))
WORK = _cfg.HOME
PY = _cfg.PYTHON
LOG = os.path.join(_cfg.LOG_DIR, "launch_all.log")
FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)


def log(s):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now(_cfg.TZ).strftime('%Y-%m-%d %H:%M:%S')}] {s}\n")
    except Exception:
        pass


def start(name, args, extra=None):
    env = os.environ.copy()
    env["NODE_COMPILE_CACHE"] = os.path.join(WORK, "node_cache")
    if _cfg.PROXY_URL:
        env["BTC5M_PROXY_URL"] = _cfg.PROXY_URL
    if extra:
        env.update(extra)
    try:
        p = subprocess.Popen(args, cwd=WORK, env=env, creationflags=FLAGS,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log(f"{name} pid={p.pid}")
    except Exception as e:
        log(f"{name} failed: {e}")


def account_modes():
    try:
        return json.load(open(os.path.join(_cfg.STATE_DIR, "account_modes.json"), encoding="utf-8"))
    except Exception:
        return {}


def main():
    log("launcher start")
    if _cfg.WARNINGS:
        log("config warnings: " + "; ".join(_cfg.WARNINGS))
    modes = account_modes()
    for acct in _cfg.ACCOUNTS:
        aid = acct["id"]
        mode = str(modes.get(aid) or "none").lower()
        script = {"martingale": "legacy/martingale_bot.py", "kelly": "predict_live_bot_kelly.py"}.get(mode)
        if not script:
            log(f"account {aid} mode=none, skip")
            continue
        extra = {"ACCOUNT_ID": aid, "BASE_STAKE": str(acct["base_stake"]),
                 "BINANCE_BAW_DIR": acct["wallet_profile"]}
        if acct["baw_js"]:
            extra["BAW_JS"] = acct["baw_js"]
        start("bot" + aid, [PY, "-u", os.path.join(SRC, script), "--account-id", aid], extra)

    cf = _cfg.RT.get("cloudflared") or ""
    cf_cfg = _cfg.RT.get("cloudflared_config") or ""
    tunnel = _cfg.RT.get("tunnel_name") or ""
    if cf and cf_cfg and tunnel:
        start("tunnel", [cf, "tunnel", "--config", cf_cfg, "run", tunnel])
    else:
        log("tunnel not configured, skip")

    for name, script, optional in [("login", "login_watchdog.py", False),
                                   ("vpn", "vpn_watchdog.py", True),
                                   ("streak8", "streak8_monitor.py", True)]:
        p = os.path.join(SRC, script)
        if optional and not os.path.exists(p):
            continue
        start(name, [PY, "-u", p])

    time.sleep(3)
    start("dashboard", [PY, "-u", os.path.join(SRC, "dashboard_server.py")],
          {"DASH_USER": os.environ.get("DASH_USER") or _cfg.DASH.get("user", "admin"),
           "DASH_PASS": _cfg.secret("DASH_PASS", ""),
           "DASH_VIEW_PASS": _cfg.secret("DASH_VIEW_PASS", "")})
    time.sleep(1)
    log("launcher done")


if __name__ == "__main__":
    main()
