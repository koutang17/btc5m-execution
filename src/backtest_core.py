#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
4连反向 + 马丁格尔 回测引擎（无UI）
=====================================
数据: BTCUSDT 5m CSV, 列: 开盘时间_北京, 开盘价_USDT, 收盘价_USDT
策略: 连续N根同色 -> 反向开单; 马丁 1,2,4,...; 连亏M次止损; 赢后重置
成交: 历史无盘口, 用 fill_model 假设:
  - always: 100%按 order_price 成交
  - prob:   以 fill_prob 概率成交, 未成交则下一根同注额重试
费用: 每笔成交按 stake*fee_rate 扣费
"""
from dataclasses import dataclass, field
from datetime import datetime
import os
import config as _cfg
import math, random
import pandas as pd

@dataclass
class Config:
    min_streak: int = 4
    flat_up: bool = False             # 平局中性，不算涨
    direction_mode: str = "reverse"   # reverse / trend
    delay_bars: int = 1               # 信号后第几根K线下单
    base_stake: float = 1.0
    multiplier: float = 2.0
    max_losses: int = 6               # 连亏几次止损; 0=不止损
    order_price: float = 0.4          # 每份限价
    payout_per_share: float = 1.0     # 每份到期返还
    fee_rate: float = 0.0002          # 吃单费率(按注额); 挂单0则填0
    fill_model: str = "always"        # always / prob
    fill_prob: float = 1.0
    six_streak: int = 6               # 6连风控
    six_block_pct: float = 1.4         # 6连累计涨跌幅阈值(%)
    six_block_enabled: bool = True
    seed: int = 42
    initial_capital: float = 0.0
    start: str | None = None
    end: str | None = None

@dataclass
class Result:
    cfg: Config
    total_pnl: float = 0.0
    final_equity: float = 0.0
    max_drawdown: float = 0.0
    max_stake: float = 0.0
    max_capital_need: float = 0.0
    total_staked: float = 0.0
    total_fees: float = 0.0
    cycles: int = 0
    wins: int = 0
    losses: int = 0
    stops: int = 0
    filled: int = 0
    unfilled: int = 0
    max_steps: int = 0
    max_consec_loss: int = 0
    six_blocks: int = 0
    equity_times: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)
    cycle_rows: list = field(default_factory=list)
    yearly: pd.DataFrame | None = None
    monthly: pd.DataFrame | None = None
    hourly: pd.DataFrame | None = None

def load_data(path: str, start: str | None = None, end: str | None = None) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8-sig")
    df["dt"] = pd.to_datetime(df["开盘时间_北京"])
    df = df.rename(columns={"开盘价_USDT": "open", "收盘价_USDT": "close"})
    df = df.sort_values("dt").drop_duplicates("dt").reset_index(drop=True)
    if start: df = df[df["dt"] >= pd.Timestamp(start)]
    if end:   df = df[df["dt"] <= pd.Timestamp(end)]
    return df[["dt", "open", "close"]].reset_index(drop=True)

def run_backtest(df: pd.DataFrame, cfg: Config | None = None) -> Result:
    cfg = cfg or Config()
    rng = random.Random(cfg.seed)
    o = df["open"].to_numpy(); c = df["close"].to_numpy(); dt = df["dt"].to_numpy()
    n = len(df)
    if cfg.flat_up:
        up = c >= o
        dn = c < o
    else:
        up = c > o
        dn = c < o

    ups = [0]*n; dns = [0]*n
    cu = cd = 0
    for i in range(n):
        cu = cu+1 if up[i] else 0
        cd = cd+1 if dn[i] else 0
        ups[i] = cu; dns[i] = cd

    def six_block_at(i):
        if not cfg.six_block_enabled: return False
        k=cfg.six_streak
        if i<k-1: return False
        s=i-k+1
        up6=all(c[x]>o[x] for x in range(s,i+1))
        dn6=all(c[x]<o[x] for x in range(s,i+1))
        if not up6 and not dn6: return False
        try: pct=abs((c[i]/o[s]-1)*100)
        except Exception: return False
        return pct>cfg.six_block_pct

    res = Result(cfg=cfg)
    pnl = 0.0; peak = 0.0; maxdd = 0.0
    stake = cfg.base_stake
    step = 0                 # 本周期已连亏
    in_cycle = False
    direction = None
    cycle_loss = 0.0
    cycle_id = 0
    cycle_start = None
    cycle_fills = 0
    cycle_pnl = 0.0
    i = 0
    while i < n - 1:
        if not in_cycle:
            sig = None
            if ups[i] >= cfg.min_streak: sig = "DOWN"
            elif dns[i] >= cfg.min_streak: sig = "UP"
            if sig:
                if six_block_at(i):
                    res.six_blocks += 1
                    i += 1
                    continue
                if cfg.direction_mode == "trend":
                    sig = "UP" if sig == "DOWN" else "DOWN"
                in_cycle = True; direction = sig; step = 0
                stake = cfg.base_stake; cycle_loss = 0.0
                cycle_id += 1; cycle_fills = 0
                cycle_pnl = 0.0
                cycle_start = dt[i]
                # 信号后 delay_bars 下单; 目标K线 = i + delay
                j = i + cfg.delay_bars
                if j >= n - 1:
                    break
                i = j
            else:
                i += 1; continue
        # ---- 在一个周期内: 从当前 i 下注 ----
        if i >= n - 1: break
        # 下单时只能看到 i-1 及之前的收盘K线
        if six_block_at(i-1):
            res.six_blocks += 1
            in_cycle = False; direction = None; step = 0
            stake = cfg.base_stake; cycle_loss = 0.0
            i += 1
            continue
        fill = (cfg.fill_model == "always") or (rng.random() < cfg.fill_prob)
        if not fill:
            res.unfilled += 1
            i += 1
            continue
        # 成交
        res.filled += 1; cycle_fills += 1
        shares = stake / cfg.order_price
        fee = stake * cfg.fee_rate
        res.total_fees += fee
        res.total_staked += stake
        res.max_stake = max(res.max_stake, stake)
        res.max_capital_need = max(res.max_capital_need, stake + cycle_loss)
        win = (c[i] > o[i]) if direction == "UP" else (c[i] < o[i])
        if win:
            gross = shares * cfg.payout_per_share
            trade_pnl = gross - stake - fee
            pnl += trade_pnl
            cycle_pnl += trade_pnl
            res.wins += 1
            res.cycle_rows.append(dict(cycle=cycle_id, start=cycle_start, end=dt[i], direction=direction,
                                       steps=step, stake=stake, outcome="赢", pnl=cycle_pnl, fills=cycle_fills))
            in_cycle = False; direction = None; step = 0
            stake = cfg.base_stake; cycle_loss = 0.0
            i += 1
        else:
            trade_pnl = -stake - fee
            pnl += trade_pnl
            cycle_pnl += trade_pnl
            res.losses += 1
            cycle_loss += stake
            step += 1
            res.max_steps = max(res.max_steps, step)
            if cfg.max_losses and step >= cfg.max_losses:
                res.stops += 1
                res.cycle_rows.append(dict(cycle=cycle_id, start=cycle_start, end=dt[i], direction=direction,
                                           steps=step, stake=stake, outcome="止损", pnl=cycle_pnl, fills=cycle_fills))
                in_cycle = False; direction = None; step = 0
                stake = cfg.base_stake; cycle_loss = 0.0
            else:
                stake *= cfg.multiplier
            i += 1
        peak = max(peak, pnl)
        maxdd = min(maxdd, pnl - peak)
        res.equity_curve.append(pnl)
        res.equity_times.append(dt[i-1])

    res.total_pnl = pnl
    res.final_equity = cfg.initial_capital + pnl
    res.max_drawdown = maxdd
    res.cycles = len(res.cycle_rows)
    hourly = pd.DataFrame({"hour": range(24), "cycles": 0, "stops": 0, "pnl": 0.0})
    if res.cycle_rows:
        cdf = pd.DataFrame(res.cycle_rows)
        cdf["year"] = pd.to_datetime(cdf["end"]).dt.year
        cdf["month"] = pd.to_datetime(cdf["end"]).dt.to_period("M").astype(str)
        cdf["hour"] = pd.to_datetime(cdf["end"]).dt.hour
        res.yearly = cdf.groupby("year")["pnl"].agg(["count", "sum"]).reset_index()
        res.monthly = cdf.groupby("month")["pnl"].agg(["count", "sum"]).reset_index()
        g = cdf.groupby("hour")
        h = pd.DataFrame({
            "cycles": g.size(),
            "stops": g["outcome"].apply(lambda s: int((s == "止损").sum())),
            "pnl": g["pnl"].sum(),
        })
        h = h.reindex(range(24), fill_value=0)
        h["stop_rate"] = [(s / c * 100 if c else 0.0) for s, c in zip(h["stops"], h["cycles"])]
        hourly = h.reset_index().rename(columns={"index": "hour"})
    else:
        hourly["stop_rate"] = 0.0
    res.hourly = hourly
    return res

def summary_text(r: Result) -> str:
    return (f"盈亏 {r.total_pnl:,.2f}u | 周期 {r.cycles} | 赢 {r.wins} 输 {r.losses} 止损 {r.stops} | "
            f"最大单注 {r.max_stake:,.2f}u | 最大回撤 {r.max_drawdown:,.2f}u | "
            f"最低资金需求 {r.max_capital_need:,.2f}u | 成交 {r.filled} 未成交 {r.unfilled} | "
            f"6连拦截 {r.six_blocks} | 手续费 {r.total_fees:,.2f}u")

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(_cfg.DATA_DIR, "BTCUSDT_5m.csv"))
    ap.add_argument("--streak", type=int, default=4)
    ap.add_argument("--max-losses", type=int, default=6)
    ap.add_argument("--base", type=float, default=1.0)
    ap.add_argument("--price", type=float, default=0.4)
    ap.add_argument("--fee", type=float, default=0.0002)
    ap.add_argument("--six-pct", type=float, default=1.4)
    ap.add_argument("--no-six", action="store_true")
    ap.add_argument("--fill-prob", type=float, default=1.0)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    a = ap.parse_args()
    df = load_data(a.data, a.start, a.end)
    cfg = Config(min_streak=a.streak, max_losses=a.max_losses, base_stake=a.base,
                 order_price=a.price, fee_rate=a.fee, fill_prob=a.fill_prob,
                 six_block_enabled=not a.no_six, six_block_pct=a.six_pct,
                 fill_model="always" if a.fill_prob >= 1 else "prob")
    r = run_backtest(df, cfg)
    print(f"bars={len(df)}")
    print(summary_text(r))
    if r.yearly is not None: print(r.yearly.to_string(index=False))
