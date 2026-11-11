# Verification

This document is deliberately conservative. It records what the code can support and what it
cannot, so that no reader (including a future me) mistakes a backtest for evidence.

## 1. Backtest

`src/backtest_live_rules.py` replays the live rules over historical 5-minute candles and makes
the fill assumption **explicit**, because a limit order on a binary token is not filled at the
candle price:

| `--fill-model` | Assumption | Bias |
|---|---|---|
| `always` | every limit order fills | optimistic |
| `adverse` | fills only when the bet direction is wrong | conservative; models adverse selection |
| `random` | fills with a fixed probability | neutral, seedable |
| `hybrid` | correct bets fill with `win_fill_prob` | tunable |

The presence of `adverse` is the most important design decision in the backtester: a strategy
that "always fills at 0.46" is really a strategy that only fills when the market has already
moved against it. Any result produced with `always` must be read next to `adverse`.

## 2. Probability table

`src/update_kelly_p.py` computes, for each run length L, the historical probability that the
next candle reverses. It blends a 14-day and a 90-day window (50/50) and writes
`kelly_p_table.json`. The bot then requires `p - limit_price >= min_edge` before trading.

Limitations, stated plainly:

- **In-sample.** The table is estimated on the same 90 days the strategy is evaluated on.
  There is no walk-forward split and no held-out period.
- **No multiple-testing correction.** Nine levels are screened; the best-looking one is
  expected to look good by chance.
- **Thin margin.** At a 0.51 limit and a 2 % taker fee, break-even is roughly 0.52. A sampled
  reversal rate of ~0.539 over ~1.5 k observations has a standard error near 1.3 pp, so the
  lower confidence bound sits at or below break-even.
- **Selection drift.** An operator can disable levels from the dashboard
  (`kelly_levels.json`), which introduces a second, undocumented selection layer.

## 3. Live reconciliation

Three independent records exist for every round: the exchange order history, the event stream
(`live_events_*.jsonl`), and the cycle ledger (`live_cycles_*.csv`). The audit procedure is:

1. sum settlement events per account,
2. sum cycle rows per account,
3. compare both against the state file's cumulative PnL,
4. for every mismatch, map the missing cycle id back to a code path that resets a cycle
   **without** writing a cycle row.

Step 4 is what turns "the numbers don't match" into "this code path is wrong". In the
2026-11-09 audit it identified the six-streak guard and the manual stop path.

Known gap: the cycle ledger currently mixes two strategies (R-06) and reuses cycle numbers
(R-07), so step 2 is only indicative until the ledger is split.

## 4. What this does not prove

- That the strategy has positive expectancy.
- That the measured win rate generalises out of sample.
- That fill prices in live trading match any backtest assumption. They are not compared,
  because the backtest does not model the order book.

The honest summary is: **the execution system is verified; the edge is not.**
