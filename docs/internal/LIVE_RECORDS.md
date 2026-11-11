# 实盘交易记录说明

记录从 2026-11-06 01:56（北京时间）重新开始。

## 文件

每个账户独立：

### 事件流
- `live_events_A.jsonl`
- `live_events_B.jsonl`

逐行记录：
- signal / cycle_start
- order 下单
- fill 成交
- settle 结算
- 其他关键事件

字段包含：
`time, account, number, name, event, cycle_no, order_no, order_id, side, stake, price, shares, result, pnl`

### 订单表
- `live_orders_A.csv`
- `live_orders_B.csv`

字段：
`cycle_no, order_no, signal_time, order_time, order_id, side, stake, limit_price, mode, ask, status`

### 全部周期
- `live_cycles_A.csv`
- `live_cycles_B.csv`

字段：
`cycle_no, start_time, end_time, direction, result, category, orders, max_stake, total_stake, pnl, counted`

### 盈利周期
- `live_cycles_win_A.csv`
- `live_cycles_win_B.csv`

### 亏损周期
- `live_cycles_loss_A.csv`
- `live_cycles_loss_B.csv`

## 周期规则
- 每个账户独立编号
- 从下一轮开始算第1个周期
- 首单未成交 → 记录为 ABORT，不计入已完成周期
- 首单成交后周期计数生效
- 5次亏损止损 → 算一个亏损周期
- 盈利 → 算一个盈利周期
- 看板和通知显示：第N个马丁周期、第M单

## 盈亏分类
- `WIN`：周期最终盈利
- `LOSS`：周期最终亏损，包括5次止损
- `ABORT`：首单未成交，不计入周期数

所有时间均为北京时间。

## 历史回填文件

从币安订单/持仓历史回填，周期号使用 H001、H002...，不计入当前周期计数。

每个账户：
- `live_orders_backfill_A.csv` / `B`
- `live_cycles_backfill_A.csv` / `B`
- `live_cycles_win_backfill_A.csv` / `B`
- `live_cycles_loss_backfill_A.csv` / `B`
- `live_settlements_backfill_A.csv` / `B`

回填周期是按实际结算持仓归并的：
- 同方向
- 注额约为上一单的2倍
- 相邻结算间隔不超过60分钟
- 盈利或无法继续翻倍时结束周期

历史数据里存在6次止损周期，因为当时止损参数是6；新规则从当前记录开始生效。
