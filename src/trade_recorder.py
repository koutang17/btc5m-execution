#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实盘记录：事件JSONL + 周期/订单CSV，按账户分开，盈亏周期分开"""
import os, json, csv
from datetime import datetime, timezone, timedelta
TZ=timezone(timedelta(hours=8))
import config as _cfg

BASE=_cfg.STATE_DIR

def ts(ms=None):
    if ms is None: ms=int(datetime.now(TZ).timestamp()*1000)
    return datetime.fromtimestamp(ms/1000,TZ).strftime("%Y-%m-%d %H:%M:%S")

def event(account,number,name,ev,**data):
    row={"time":ts(),"account":account,"number":number,"name":name,"event":ev}
    row.update(data)
    try:
        with open(os.path.join(BASE,f"live_events_{account}.jsonl"),"a",encoding="utf-8") as f:
            f.write(json.dumps(row,ensure_ascii=False)+"\n")
    except Exception: pass

def _append(path,header,row):
    new=not os.path.exists(path)
    try:
        with open(path,"a",newline="",encoding="utf-8-sig") as f:
            w=csv.writer(f)
            if new: w.writerow(header)
            w.writerow(row)
    except Exception: pass

def cycle(account,number,name,row):
    header=["cycle_no","start_time","end_time","direction","result","category","orders","max_stake","total_stake","pnl","counted"]
    r=[row.get(k,"") for k in header]
    _append(os.path.join(BASE,f"live_cycles_{account}.csv"),header,r)
    cat=row.get("category","")
    if cat=="WIN":
        _append(os.path.join(BASE,f"live_cycles_win_{account}.csv"),header,r)
    elif cat=="LOSS":
        _append(os.path.join(BASE,f"live_cycles_loss_{account}.csv"),header,r)

def order(account,number,name,row):
    header=["cycle_no","order_no","signal_time","order_time","order_id","side","stake","limit_price","mode","ask","status"]
    _append(os.path.join(BASE,f"live_orders_{account}.csv"),header,[row.get(k,"") for k in header])
