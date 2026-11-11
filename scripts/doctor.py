#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pre-flight check. Read-only: never places an order and never mutates state.

    python scripts/doctor.py
"""
import socket, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import config as _cfg  # noqa: E402

OK, WARN, FAIL = "OK", "WARN", "FAIL"
rows = []


def check(name, fn):
    try:
        state, detail = fn()
    except Exception as e:
        state, detail = FAIL, str(e)[:120]
    rows.append((state, name, detail))


def _python():
    v = "%d.%d.%d" % sys.version_info[:3]
    return (OK if sys.version_info >= (3, 10) else FAIL), v


def _config():
    if not Path(_cfg.CONFIG_PATH).exists():
        return FAIL, "missing: " + str(_cfg.CONFIG_PATH)
    probs = _cfg.problems()
    return (OK if not probs else FAIL), (str(_cfg.CONFIG_PATH) if not probs else "; ".join(probs))


def _node():
    try:
        out = subprocess.run([_cfg.NODE, "--version"], capture_output=True, text=True, timeout=10)
        return (OK if out.returncode == 0 else WARN), (out.stdout or out.stderr).strip()[:40]
    except Exception as e:
        return WARN, "node not callable (" + str(e) + ") - only needed for the wallet CLI"


def _baw():
    if not _cfg.BAW_JS:
        return FAIL, "runtime.baw_js is empty"
    p = Path(_cfg.BAW_JS)
    return (OK if p.exists() else FAIL), str(p)


def _wallet_profile():
    missing = [a["id"] for a in _cfg.ACCOUNTS if not Path(a["wallet_profile"]).exists()]
    if missing:
        return WARN, "not initialised yet: " + ",".join(missing)
    return OK, ", ".join(a["wallet_profile"] for a in _cfg.ACCOUNTS)


def _proxy():
    host = _cfg.NET.get("host", "127.0.0.1")
    port = int(_cfg.NET.get("port", 7897))
    s = socket.socket(); s.settimeout(2)
    try:
        return (OK if s.connect_ex((host, port)) == 0 else WARN), host + ":" + str(port)
    finally:
        s.close()


def _dirs():
    for d in (_cfg.STATE_DIR, _cfg.LOG_DIR, _cfg.DATA_DIR):
        Path(d).mkdir(parents=True, exist_ok=True)
    return OK, "state / logs / data writable"


def _port():
    port = int(_cfg.DASH.get("port", 8600))
    s = socket.socket(); s.settimeout(1)
    try:
        free = s.connect_ex(("127.0.0.1", port)) != 0
        return (OK if free else WARN), "port " + str(port) + (" free" if free else " already in use")
    finally:
        s.close()


def _dry_run():
    if _cfg.DRY_RUN:
        return OK, "dry_run = true (no orders will be sent)"
    return WARN, "dry_run = false - real orders will be placed"


def _secrets():
    if _cfg.secret("DASH_PASS"):
        return OK, "dashboard password set"
    return FAIL, "DASH_PASS missing - the dashboard will refuse to start"


for name, fn in [("python", _python), ("config", _config), ("node", _node), ("wallet cli", _baw),
                 ("wallet profile", _wallet_profile), ("proxy", _proxy), ("paths", _dirs),
                 ("dashboard port", _port), ("mode", _dry_run), ("secrets", _secrets)]:
    check(name, fn)

width = max(len(n) for _, n, _ in rows) + 2
for state, name, detail in rows:
    print("[%-4s] %-*s %s" % (state, width, name, detail))

fails = sum(1 for s, _, _ in rows if s == FAIL)
print()
print("ready" if fails == 0 else str(fails) + " problem(s) to fix before running")
sys.exit(0 if fails == 0 else 1)
