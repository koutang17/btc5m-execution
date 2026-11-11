#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
按当前实盘规则回测 BTC/ETH 5m 马丁策略。

重要：真实 Predict 市场是订单簿，历史K线无法精确还原限价单成交。
本脚本提供 fill_model，必须明确选择假设：
  always  : 假设每次限价单都成交（乐观）
  adverse : 只有下注方向错误/平盘时才成交（保守，模拟逆向选择）
  random  : 按固定概率成交（可设置 seed）
  hybrid  : 下注正确时按 win_fill_prob 成交，错误/平盘时成交

规则包含：
- N连反向信号
- 首单未成交直接跳过
- 后续档位顺延
- 马丁与最大亏损
- 止损后冷却一根
- 6连风控
- 累计收益达标后信号+1
- 动态/锁定模式
- 硬止损
"""
import os
import config as _cfg
import argparse, csv, json, math, os, sys
from datetime import datetime

import numpy as np
import pandas as pd


def parse_args():
    p=argparse.ArgumentParser()
    p.add_argument("--data",default=os.path.join(_cfg.DATA_DIR, "BTCUSDT_5m.csv"))
    p.add_argument("--asset",default="BTC")
    p.add_argument("--min-streak",type=int,default=3)
    p.add_argument("--max-losses",type=int,default=5)
    p.add_argument("--base-stake",type=float,default=1.0)
    p.add_argument("--retry-count",type=int,default=1)
    p.add_argument("--price",type=float,default=0.46)
    p.add_argument("--carry-price",type=float,default=0.48)
    p.add_argument("--fee-bps",type=float,default=0.0)
    p.add_argument("--fill-model",choices=["always","adverse","random","hybrid"],default="always")
    p.add_argument("--fill-prob",type=float,default=1.0)
    p.add_argument("--win-fill-prob",type=float,default=0.0)
    p.add_argument("--seed",type=int,default=7)
    p.add_argument("--flat-payout",type=float,default=0.5)
    p.add_argument("--cooldown-bars",type=int,default=1)
    p.add_argument("--boost-pct",type=float,default=50.0)
    p.add_argument("--boost-base",type=float,default=100.0)
    p.add_argument("--boost-add",type=int,default=1)
    p.add_argument("--boost-mode",choices=["dynamic","latched"],default="dynamic")
    p.add_argument("--six-pct",type=float,default=1.4)
    p.add_argument("--hard-dd",type=float,default=100.0)
    p.add_argument("--hard-loss-cycles",type=int,default=0)
    p.add_argument("--out-prefix",default="")
    return p.parse_args()


def load_data(path):
    df=pd.read_csv(path,encoding="utf-8-sig")
    dt_col="dt" if "dt" in df.columns else ("开盘时间_北京" if "开盘时间_北京" in df.columns else None)
    if not dt_col: raise SystemExit("找不到时间列: dt 或 开盘时间_北京")
    op_col="open" if "open" in df.columns else ("开盘价" if "开盘价" in df.columns else None)
    cl_col="close" if "close" in df.columns else ("收盘价" if "收盘价" in df.columns else None)
    if not op_col or not cl_col: raise SystemExit("找不到开/收盘列")
    df["dt"]=pd.to_datetime(df[dt_col],errors="coerce")
    df["o"]=pd.to_numeric(df[op_col],errors="coerce")
    df["c"]=pd.to_numeric(df[cl_col],errors="coerce")
    df=df.dropna(subset=["dt","o","c"]).sort_values("dt").drop_duplicates("dt").reset_index(drop=True)
    return df


def dir_of(o,c):
    if c>o: return "UP"
    if c<o: return "DOWN"
    return "FLAT"


def signal_at(o,c,i,k):
    if i+1<k: return None
    seg=range(i-k+1,i+1)
    if all(c[j]>o[j] for j in seg): return "DOWN"
    if all(c[j]<o[j] for j in seg): return "UP"
    return None


def six_block_at(o,c,i,pct_lim):
    if i<5: return False,None
    seg=range(i-5,i+1)
    up=all(c[j]>o[j] for j in seg)
    dn=all(c[j]<o[j] for j in seg)
    if not up and not dn: return False,None
    try:
        pct=abs((c[i]/o[i-5]-1.0)*100.0)
    except Exception:
        return False,None
    return (pct>pct_lim, {"dir":"涨" if up else "跌","pct":round(pct,3)})


def fill_decision(model,outcome,bet,rng,fill_prob,win_fill_prob):
    if model=="always": return True
    if model=="adverse": return outcome!=bet
    if model=="random": return bool(rng.random()<fill_prob)
    if model=="hybrid":
        if outcome!=bet: return True
        return bool(rng.random()<win_fill_prob)
    return True


def settle(stake,price,outcome,bet,flat_payout,fee_bps):
    if outcome==bet:
        gross=stake*(1.0/price-1.0)
    elif outcome=="FLAT":
        gross=stake*(flat_payout/price-1.0)
    else:
        gross=-stake
    fee=stake*float(fee_bps)/10000.0
    return gross-fee, (outcome==bet)


def main():
    a=parse_args()
    df=load_data(a.data)
    o=df["o"].to_numpy(float); c=df["c"].to_numpy(float); n=len(df)
    times=df["dt"].dt.strftime("%Y-%m-%d %H:%M:%S").to_numpy()
    rng=np.random.default_rng(a.seed)
    equity=0.0; peak=0.0; maxdd=0.0
    cycles=[]; orders=0; wins=skips=stops=risk_skips=0
    max_step=0; max_stake=0.0
    loss_streak=0; total_stake=0.0
    cooldown_until=-1
    boost_latched=False
    hard_stop=None
    i=0
    while i<n-1:
        # 止损后冷却：结束后至少跳过指定根数
        if i<cooldown_until:
            i+=1; continue
        # 当前累计收益与信号门槛
        ratio=(equity/a.boost_base*100.0) if a.boost_base>0 else 0.0
        boost=False
        if a.boost_pct>0 and a.boost_base>0 and ratio>=a.boost_pct:
            boost=True
        if a.boost_mode=="latched":
            if boost: boost_latched=True
            boost=boost_latched
        eff_k=a.min_streak + (a.boost_add if boost else 0)
        sig=signal_at(o,c,i,eff_k)
        if not sig:
            i+=1; continue
        blocked,info=six_block_at(o,c,i,a.six_pct)
        if blocked:
            risk_skips+=1
            cycles.append({"start":times[i],"end":times[i+1] if i+1<n else times[i],
                           "direction":sig,"result":"RISK_SKIP","pnl":0.0,
                           "orders":0,"max_step":0,"max_stake":0.0,
                           "reason":"6连"+info["dir"]+" "+str(info["pct"])+"%"})
            i+=1; continue

        # 开始一个周期
        step=0; attempts=0; j=i+1
        cyc_pnl=0.0; cyc_orders=0; cyc_max=0.0; result=None; end_j=j
        while j<n:
            stake=a.base_stake*(2.0**step)
            px=a.price if attempts==0 else a.carry_price
            outcome=dir_of(o[j],c[j])
            fill=fill_decision(a.fill_model,outcome,sig,rng,a.fill_prob,a.win_fill_prob)
            if not fill:
                if step==0:
                    # 首单未成交：本周期直接跳过
                    result="SKIP"
                    end_j=j
                    break
                attempts+=1
                if attempts>a.retry_count:
                    result="STOP_NOFILL"
                    end_j=j
                    break
                j+=1
                continue
            # 成交
            pnl,win=settle(stake,px,outcome,sig,a.flat_payout,a.fee_bps)
            cyc_pnl+=pnl; equity+=pnl
            cyc_orders+=1; orders+=1
            cyc_max=max(cyc_max,stake); max_stake=max(max_stake,stake)
            max_step=max(max_step,step+1)
            end_j=j
            if win:
                result="WIN"; break
            step+=1; attempts=0
            if step>=a.max_losses:
                result="STOP_MAX"; break
            j+=1
        if result is None:
            result="END_OF_DATA"
        if cyc_orders==0 and result=="SKIP":
            skips+=1
        elif result=="WIN":
            wins+=1
            loss_streak=0
        elif result=="STOP_MAX":
            stops+=1
            loss_streak+=1
        elif result=="STOP_NOFILL":
            stops+=1
            loss_streak+=1
        # STOP_NOFILL 的 cyc_pnl 已经包含之前成交的亏损
        peak=max(peak,equity); maxdd=min(maxdd,equity-peak)
        cycles.append({"start":times[i],"end":times[end_j] if end_j<n else times[-1],
                       "direction":sig,"result":result,"pnl":round(cyc_pnl,6),
                       "orders":cyc_orders,"max_step":step,
                       "max_stake":round(cyc_max,4),
                       "equity":round(equity,6),
                       "boost":bool(boost),"eff_k":eff_k,
                       "reason":""})
        if result=="STOP_MAX":
            cooldown_until=end_j+1+a.cooldown_bars
        # 硬止损
        if a.hard_dd>0 and (peak-equity)>=a.hard_dd:
            hard_stop=f"回撤{peak-equity:.2f}U >= {a.hard_dd}U"
            break
        if a.hard_loss_cycles>0 and loss_streak>=a.hard_loss_cycles:
            hard_stop=f"连续亏损{loss_streak}周期"
            break
        i=max(i+1,end_j+1)

    summary={
        "data":os.path.basename(a.data),"asset":a.asset,
        "rows":int(n),"start":str(df["dt"].iloc[0]),"end":str(df["dt"].iloc[-1]),
        "fill_model":a.fill_model,"fill_prob":a.fill_prob,
        "price":a.price,"carry_price":a.carry_price,
        "min_streak":a.min_streak,"max_losses":a.max_losses,
        "base_stake":a.base_stake,"retry_count":a.retry_count,
        "boost_pct":a.boost_pct,"boost_base":a.boost_base,
        "boost_add":a.boost_add,"boost_mode":a.boost_mode,
        "cycles":len(cycles),"wins":wins,"skips":skips,"stops":stops,
        "risk_skips":risk_skips,"orders":orders,
        "pnl":round(equity,6),"max_drawdown":round(maxdd,6),
        "max_step":max_step,"max_stake":round(max_stake,4),
        "hard_stop":hard_stop,
        "final_equity":round(equity,6),
    }
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    prefix=a.out_prefix or os.path.join(os.path.dirname(a.data),"backtest_live_rules")
    cdf=pd.DataFrame(cycles)
    cdf.to_csv(prefix+"_cycles.csv",index=False,encoding="utf-8-sig")
    with open(prefix+"_summary.json","w",encoding="utf-8") as f:
        json.dump(summary,f,ensure_ascii=False,indent=2)
    print("cycles ->",prefix+"_cycles.csv")
    print("summary ->",prefix+"_summary.json")


if __name__=="__main__":
    main()
