#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Download closed 5-minute klines from the public Binance REST API into data/.

    python scripts/fetch_klines.py --days 180 --out data/BTCUSDT_5m.csv

Read-only against the exchange: no key, no order, no account.
"""
import argparse, csv, json, sys, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import config as _cfg  # noqa: E402

COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time"]


def fetch(url, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            if i == retries - 1:
                raise
            print(f"  retry {i + 1}/{retries}: {e}")
            time.sleep(1.5 * (i + 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default=(_cfg.ACCOUNTS[0]["asset"] if _cfg.ACCOUNTS else "BTC") + _cfg.EXCHANGE["symbol_suffix"])
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--out", default=str(Path(_cfg.DATA_DIR) / "klines_5m.csv"))
    ap.add_argument("--base", default=_cfg.EXCHANGE["api_base"])
    a = ap.parse_args()

    now = int(time.time() * 1000)
    start = (now - a.days * 86400000) // 300000 * 300000
    rows, cur = [], start
    while cur < now:
        url = (f"{a.base}/api/v3/klines?symbol={a.symbol}&interval=5m"
               f"&startTime={cur}&endTime={cur + 999 * 300000}&limit=1000")
        batch = fetch(url)
        if not batch:
            break
        rows += batch
        cur = batch[-1][0] + 300000
        print(f"  {len(rows)} bars ...", end="\r")
        time.sleep(0.1)

    closed = [b for b in rows if int(b[6]) <= now]
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(COLS)
        for b in closed:
            wr.writerow([int(b[0]), b[1], b[2], b[3], b[4], b[5], int(b[6])])
    print(f"\nwrote {len(closed)} closed bars -> {a.out}")


if __name__ == "__main__":
    main()
