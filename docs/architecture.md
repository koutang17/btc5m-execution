# Architecture

## 1. Runtime topology

```
                      +---------------------------+
                      |  Windows Task Scheduler   |
                      |  every 5 min: watchdog.py |
                      +-------------+-------------+
                                    | checks / restarts
        +---------------------------+---------------------------+
        |                           |                           |
   bot (acct A)               dashboard :8600             helpers
   bot (acct B)                    |                    login_watchdog
        |                    cloudflared (opt-in)      vpn_watchdog
        |                           |                    streak probe
        v                           v
  wallet CLI (node)          browser / phone
        |
        v
  Binance Predict API
```

- **One process per account.** Each bot owns exactly one state file, one config file and one
  ledger triplet. There is no shared in-process state between accounts.
- **The wallet CLI is the only write path to the exchange.** Every market lookup, quote, order,
  cancel, position query and redemption spawns a short-lived `node` process. This keeps the
  Python side simple but makes each call cost 0.2-3 s.
- **The dashboard is a read-mostly sidecar** that shells out to the same CLI for balances and
  positions, caches the result, and serves a single-page UI. Its write endpoints only flip flag
  files or invoke the strategy switch tool.

## 2. Components

| Component | File | Responsibility | Failure domain |
|---|---|---|---|
| Strategy loop | `src/predict_live_bot_kelly.py` | signal -> size -> order -> settle -> record | per account |
| Legacy martingale bot | `src/predict_live_bot.py` | previous doubling strategy, kept for comparison | per account |
| Ledger | `src/trade_recorder.py` | append events / orders / cycles | shared, account-scoped |
| Notifier | `src/notify.py` | WeCom / DingTalk / PushPlus / Bark / ServerChan | shared |
| Dashboard | `src/dashboard_server.py`, `src/dashboard.html` | state, config, pause/resume/stop, manual close | host |
| Supervisor | `src/watchdog.py` | process/port/tunnel liveness, restart | host |
| Login supervisor | `src/login_watchdog.py` | classify auth vs network, issue QR link | per account |
| Network supervisor | `src/vpn_watchdog.py` | local proxy health, node switching | host |
| Strategy switch | `src/botctl.py` | stop one strategy, start another, verify flat | host |
| Probability table | `src/update_kelly_p.py` | daily 14/90-day blended reversal rate | offline |

## 3. Per-round data flow

```
00:00.0  sleep until boundary + 0.5 s
00:00.5  market(target_round) and klines(12) fetched in parallel
00:00.9  closed = [b for b in bars if closeTime <= now]   # never the live candle
00:01.0  streak_run(closed) -> (direction, run_length)
00:01.1  pick_p(run_length) + pick_frac(run_length) -> next_stake()
00:01.2  quote(token, LIMIT, 0.51)
00:02.0  place-order(quoteId)
00:02.1  state.pending = {orderId, tokenId, target_ot, ...}; save
...
05:00.0  round closes
05:02.0  position lookup -> WIN / LOSS
05:02.2  redeem if canClaim; update cum_pnl, bankroll, counters; save
05:02.3  ledger cycle row; notification; next loop
```

Measured on the live host: order submission lands **~6 s (p50), ~30 s (p90)** after the round
opens, with a worst case near 96 s when the market lookup has to fall back across three CLI
query shapes.

## 4. Why a CLI instead of the REST API

The prediction market is not exposed through the standard spot/futures REST surface used by
this account; the supported path is a wallet CLI that carries its own session. Consequences:

- **Authentication is a session, not a key.** It expires and must be re-established by QR.
- **Latency is dominated by process startup**, not by the exchange.
- **Errors arrive as JSON on stdout**, sometimes with a human-readable string, sometimes empty.
  Every call site therefore needs its own "did this succeed?" policy.

That last point is the origin of most of the defensive code in `baw()`, `market()` and
`order_by_id()`.

## 5. Shared-nothing between accounts

```
acct A: ~/.baw          + predict_live_state_kelly.json      + live_*_A.*
acct B: ~/.baw-b        + predict_live_state_B_kelly.json    + live_*_B.*
```

The isolation is real but incomplete: the *ledger* files are keyed by account only, not by
strategy, so switching a strategy reuses the same CSV. This is finding **R-06** in the
[risk register](risk-register.md).

## 6. Latency budget

| Segment | Typical | Notes |
|---|---|---|
| Wake-up alignment | +0.5 s | deliberate, to let the round open |
| Candle fetch | 0.2-0.5 s | public REST, direct |
| Market lookup | 0.3-6 s | up to three CLI fallbacks |
| Quote | 0.5-2.5 s | one CLI call |
| Place | 0.5-1.6 s | one CLI call |
| **Total to order** | **~2-11 s** | p90 ~30 s |

For a 300 s round this is 1-10 % of the horizon. It is the single largest architectural
weakness for anything latency-sensitive, and the reason the roadmap replaces
process-per-call with a persistent client.
