#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single source of truth for paths, accounts and strategy parameters.

Resolution order for every value:  CLI flag > environment variable > config.json > built-in default.
Secrets (dashboard password, webhook) are NEVER read from config.json - use .env or the environment.

    from config import CFG, NODE, BAW_JS, HOME, STATE_DIR, TZ, ACCOUNTS, secret
"""
import json, os, sys
from datetime import timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("BTC5M_CONFIG") or (ROOT / "config.json"))
EXAMPLE_PATH = ROOT / "config.example.json"

DEFAULTS = {
    "home": ".",
    "runtime": {
        "python": "python", "node": "node", "baw_js": "", "baw_profile": "~/.baw",
        "proxy_url": "http://127.0.0.1:7897", "timezone_offset": 8,
        "cloudflared": "", "cloudflared_config": "", "tunnel_name": "",
    },
    "paths": {"state": "state", "logs": "logs", "data": "data"},
    "accounts": [{"id": "A", "name": "main", "asset": "BTC", "base_stake": 1.0,
                  "wallet_profile": "~/.baw", "baw_js": "", "enabled": True}],
    "exchange": {
        "chain_id": 56, "symbol_suffix": "USDT", "category": ["crypto", "up-down"],
        "timeframe": "5 min",
        "api_base": "https://data-api.binance.vision",
        "api_fallback": "https://api.binance.com",
    },
    "strategy": {
        "min_streak": 3, "max_losses": 5, "retry_count": 1, "price": 0.51,
        "carry_price": 0.51, "min_ask": 0.1, "profit_boost_pct": 0.0,
        "profit_boost_base": 0.0, "profit_boost_add": 0, "profit_boost_mode": "dynamic",
    },
    "sizing": {"mode": "kelly", "p": 0.52, "frac": 0.25, "max_pct": 0.05,
               "min_edge": 0.006, "min_stake": 1.0},
    "risk": {"dry_run": True, "hard_stop_mode": "drawdown",
             "hard_stop_drawdown": 20.0, "hard_stop_loss_cycles": 0},
    "dashboard": {"bind": "127.0.0.1", "port": 8600, "user": "admin",
                  "refresh_sec": 30, "slow_refresh_sec": 60},
    "notify": {"enabled": False, "provider": "wecom"},
    "network": {"proxy": {"host": "127.0.0.1", "port": 7897,
                          "clash_api": "http://127.0.0.1:39797", "selector": "",
                          "nodes": [], "app_exe": "", "helper_exe": ""}},
    "backtest": {"data": "data/BTCUSDT_5m.csv", "fill_model": "adverse", "fee_bps": 200},
}


def _merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _read_env_file(p):
    d = {}
    try:
        for line in Path(p).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            d[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return d


ENV = _read_env_file(ROOT / ".env")
WARNINGS = []


def secret(name, default=""):
    """Secret lookup: environment first, then .env. Never config.json."""
    return os.environ.get(name) or ENV.get(name) or default


def _load():
    if not CONFIG_PATH.exists():
        try:
            CONFIG_PATH.write_text(EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            WARNINGS.append(f"config.json was missing; created from config.example.json at {CONFIG_PATH}")
        except Exception as e:
            WARNINGS.append(f"config.json missing and could not be created: {e}")
            return _merge(DEFAULTS, {})
    try:
        user = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        sys.stderr.write(f"[config] cannot parse {CONFIG_PATH}: {e}\n")
        raise SystemExit(2)
    return _merge(DEFAULTS, user)


CFG_RAW = _load()
if CFG_RAW.get("home") in (".", "", None):
    HOME = str(ROOT)
else:
    HOME = str(Path(os.path.expandvars(os.path.expanduser(str(CFG_RAW["home"])))).resolve())


def _dir(key):
    p = Path(str(CFG_RAW["paths"].get(key, key)))
    p = p if p.is_absolute() else Path(HOME) / p
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


STATE_DIR = _dir("state")
LOG_DIR = _dir("logs")
DATA_DIR = _dir("data")

RT = CFG_RAW["runtime"]
TZ = timezone(timedelta(hours=float(RT.get("timezone_offset", 8))))
NODE = RT.get("node") or "node"
PYTHON = RT.get("python") or sys.executable
PROXY_URL = RT.get("proxy_url") or ""
BOOT = "file:///" + str(ROOT / "proxy" / "proxy-bootstrap.mjs").replace("\\", "/")


def _expand(p):
    return str(Path(os.path.expandvars(os.path.expanduser(str(p)))).resolve()) if p else ""


BAW_JS = _expand(RT.get("baw_js") or "")
ACCOUNTS = []
for a in CFG_RAW["accounts"]:
    wjs = a.get("baw_js") or BAW_JS
    ACCOUNTS.append({
        "id": str(a.get("id") or "A").upper(),
        "name": str(a.get("name") or "main"),
        "number": int(a.get("number") or len(ACCOUNTS) + 1),
        "asset": str(a.get("asset") or "BTC").upper(),
        "base_stake": float(a.get("base_stake") or 1.0),
        "wallet_profile": _expand(a.get("wallet_profile") or RT.get("baw_profile") or "~/.baw"),
        "baw_js": _expand(wjs),
        "enabled": bool(a.get("enabled", True)),
    })

EX = CFG_RAW["exchange"]
ST = CFG_RAW["strategy"]
SZ = CFG_RAW["sizing"]
RK = CFG_RAW["risk"]

# Strategy defaults in the shape the bot's existing code expects.
CFG = {
    "min_streak": int(ST["min_streak"]), "max_losses": int(ST["max_losses"]),
    "base_stake": float(ACCOUNTS[0]["base_stake"] if ACCOUNTS else 1.0),
    "retry_count": int(ST["retry_count"]),
    "price": float(ST["price"]), "carry_price": float(ST["carry_price"]),
    "min_ask": float(ST["min_ask"]),
    "profit_boost_pct": float(ST["profit_boost_pct"]),
    "profit_boost_base": float(ST["profit_boost_base"]),
    "profit_boost_add": int(ST["profit_boost_add"]),
    "profit_boost_mode": ST["profit_boost_mode"],
    "hard_stop_mode": RK["hard_stop_mode"],
    "hard_stop_drawdown": float(RK["hard_stop_drawdown"]),
    "hard_stop_loss_cycles": int(RK["hard_stop_loss_cycles"]),
    "chain": str(EX["chain_id"]),
    "asset": ACCOUNTS[0]["asset"] if ACCOUNTS else "BTC",
    "symbol": (ACCOUNTS[0]["asset"] if ACCOUNTS else "BTC") + str(EX["symbol_suffix"]),
    "timeframe": EX["timeframe"],
    "mode": "dry_run" if RK.get("dry_run") else "live",
    "sizing_mode": SZ["mode"],
    "kelly_p": float(SZ["p"]), "kelly_frac": float(SZ["frac"]),
    "kelly_max_pct": float(SZ["max_pct"]), "kelly_min_edge": float(SZ["min_edge"]),
    "kelly_min_stake": float(SZ["min_stake"]),
}

DASH = CFG_RAW["dashboard"]
NOTIFY = CFG_RAW["notify"]
NET = CFG_RAW["network"]["proxy"]
BACKTEST = CFG_RAW["backtest"]
EXCHANGE = EX
DRY_RUN = bool(RK.get("dry_run", True))


def account(account_id=None):
    aid = str(account_id or os.environ.get("ACCOUNT_ID") or "A").upper()
    for a in ACCOUNTS:
        if a["id"] == aid:
            return a
    return ACCOUNTS[0]


def problems():
    """Configuration problems that will stop the bot from trading. Empty list = ready."""
    out = []
    if not BAW_JS:
        out.append("runtime.baw_js is empty - point it at @binance/agentic-wallet/dist/index.js")
    elif not Path(BAW_JS).exists():
        out.append(f"runtime.baw_js does not exist: {BAW_JS}")
    if not ACCOUNTS:
        out.append("accounts is empty")
    for a in ACCOUNTS:
        if not a["wallet_profile"]:
            out.append(f"account {a['id']}: wallet_profile is empty")
    if DRY_RUN is False and not secret("DASH_PASS"):
        out.append("live mode with no dashboard password is refused (set DASH_PASS in .env)")
    if not (0.0 < float(EX["chain_id"]) and int(EX["chain_id"]) > 0):
        out.append("exchange.chain_id must be a positive integer")
    return out


def _mask(value):
    s = str(value)
    return (s[:2] + "*" * max(0, len(s) - 2)) if len(s) > 4 else "***"


def check(verbose=True):
    probs = problems()
    if verbose:
        red = dict(CFG_RAW)
        red["dashboard"] = dict(DASH, user="***")
        print(json.dumps({"home": HOME, "state": STATE_DIR, "logs": LOG_DIR,
                          "data": DATA_DIR, "dry_run": DRY_RUN,
                          "accounts": [{k: v for k, v in a.items() if k != "baw_js"} for a in ACCOUNTS],
                          "node": NODE, "baw_js": _mask(BAW_JS) if BAW_JS else "",
                          "config": str(CONFIG_PATH),
                          "warnings": WARNINGS, "problems": probs}, ensure_ascii=False, indent=2))
    return probs


if __name__ == "__main__":
    p = check()
    sys.exit(1 if p else 0)
