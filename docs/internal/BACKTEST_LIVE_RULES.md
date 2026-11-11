# 当前实盘规则回测脚本说明

脚本：backtest_live_rules.py

## 重要限制

历史5分钟K线不能还原 Predict 订单簿的限价成交。
脚本必须选择 fill_model，不同模型结果不能混为一谈：

- always：假设每次限价单都成交，结果偏乐观
- adverse：下注方向正确时不成交，错误或平盘时才成交，模拟逆向选择，结果偏保守
- random：按固定概率成交
- hybrid：方向正确时按 win_fill_prob 成交，错误/平盘时成交

不要把 always 的结果当成真实可实现的收益。

## 已纳入的当前规则

- 3连反向信号
- 首单未成交直接跳过，不进入第二单
- 后续档位顺延，默认1次
- 马丁翻倍，最大亏损次数可配置
- 止损后冷却一根
- 6连风控
- 累计收益门槛达标后信号加一根
- 动态/锁定模式
- 硬止损
- 手续费可配置
- BTC/ETH 数据文件可切换

## 运行示例

    & $python backtest_live_rules.py --data <PROJECT_ROOT>\BTCUSDT_5m_6years_clean.csv --max-losses 5 --fill-model always

保守模型：

    & $python backtest_live_rules.py --data <PROJECT_ROOT>\BTCUSDT_5m_6years_clean.csv --fill-model adverse

随机成交模型：

    & $python backtest_live_rules.py --data <PROJECT_ROOT>\BTCUSDT_5m_6years_clean.csv --fill-model random --fill-prob 0.5

关闭硬止损：

    --hard-dd 0

## 本次样例结果

数据：BTCUSDT 5m，2020-11-02 至 2026-11-02，630725根
参数：首单1U，最大亏损5次，限价0.46/0.48，硬止损100U

| 成交模型 | 周期数 | 盈利 | 跳过 | 止损 | 盈亏 | 最大回撤 |
|---|---:|---:|---:|---:|---:|---:|
| always | 10738 | 10563 | 0 | 161 | +9687.13 | -107.91 |
| adverse | 20 | 0 | 7 | 13 | -115.00 | -115.00 |

always 在回撤超过100U后触发硬止损，属于极端乐观模型下的结果。
adverse 是极端逆向选择模型，首单盈利信号基本不成交，结果只用于压力测试。

## 结论

- 该脚本能复现实盘规则组合，但不能替代订单簿级回测。
- 真实结果应落在 always 和 adverse 之间，需用 random/hybrid 或真实成交数据进一步校准。
- 下一步建议采集实盘每笔挂单的ask、成交/未成交结果，估计真实 fill 概率，再回填到回测模型。